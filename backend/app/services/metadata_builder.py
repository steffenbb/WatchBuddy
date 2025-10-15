"""
metadata_builder.py

Service for bulk enrichment of persistent candidate pool with Trakt IDs.
Runs during initial setup to populate missing metadata with progress tracking.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.redis_client import get_redis
from app.services.trakt_client import TraktClient

logger = logging.getLogger(__name__)

class MetadataBuilder:
    """Builds metadata for persistent candidates with progress tracking."""
    
    def __init__(self):
        # Do not cache an asyncio Redis connection across event loop lifecycles.
        # Always grab a fresh client within each method to avoid 'Event loop is closed' errors
        # after worker restarts or Windows host reboots.
        pass
        
    async def get_build_status(self) -> Dict[str, Any]:
        """Get current metadata build status from Redis."""
        r = get_redis()
        status_json = await r.get("metadata_build:status")
        if status_json:
            try:
                return json.loads(status_json)
            except Exception:
                pass
        
        return {
            "status": "not_started",
            "total": 0,
            "processed": 0,
            "progress_percent": 0,
            "started_at": None,
            "updated_at": None,
            "errors": 0
        }
    
    async def set_build_status(self, status: Dict[str, Any]):
        """Update metadata build status in Redis."""
        status["updated_at"] = datetime.utcnow().isoformat()
        try:
            r = get_redis()
            await r.setex(
                "metadata_build:status",
                86400,  # 24 hour expiry
                json.dumps(status)
            )
        except Exception as e:
            logger.error(f"Failed to update build status: {e}")
    
    async def check_metadata_ready(self, db: Session) -> bool:
        """
        Check if metadata has been built (Trakt IDs populated) or if the build has completed.
        
        Returns True if:
        1. At least 80% of candidates have Trakt IDs, OR
        2. A metadata scan has been marked as completed (even if below 80%)
        """
        from app.models import PersistentCandidate
        from sqlalchemy import func
        
        # First check if we have a completion flag set
        r = get_redis()
        completed_flag = await r.get("metadata_build:scan_completed")
        if completed_flag:
            return True
        
        # Check if we have candidates with Trakt IDs
        total = db.query(func.count(PersistentCandidate.id)).scalar() or 0
        with_trakt = db.query(func.count(PersistentCandidate.id)).filter(
            PersistentCandidate.trakt_id.isnot(None)
        ).scalar() or 0
        
        # Consider ready if at least 80% have Trakt IDs
        if total == 0:
            return False
        
        percent = (with_trakt / total) * 100
        if percent >= 80.0:
            # Set completion flag so we don't show the screen again
            await r.set("metadata_build:scan_completed", "true")
            return True
        
        return False
    
    async def build_trakt_ids(self, db: Session, user_id: int = 1, force: bool = False, retry_limit: int = 3):
        """
        Bulk lookup and populate Trakt IDs for persistent candidates.
        Retries candidates with missing trakt_id up to retry_limit times.
        
        Args:
            db: Database session
            user_id: User ID for Trakt authentication
            force: Force rebuild even if already complete
            retry_limit: Max number of attempts for each candidate
        """
        from app.models import PersistentCandidate
        from sqlalchemy import func
        
        # Check if already in progress and detect stale runs
        current_status = await self.get_build_status()
        if current_status["status"] == "running" and not force:
            # Consider a job stale if status hasn't been updated in the last 120 seconds
            updated_at = current_status.get("updated_at")
            is_stale = False
            if updated_at:
                try:
                    last = datetime.fromisoformat(updated_at)
                    age = (datetime.utcnow() - last).total_seconds()
                    if age > 120:
                        is_stale = True
                except Exception:
                    # If parsing fails, treat as stale to be safe
                    is_stale = True
            if not is_stale:
                logger.info("Metadata build already in progress")
                return
            logger.warning("Detected stale metadata build status (no heartbeat >120s). Resuming work.")
        
        # Get total count and candidates without Trakt IDs
        total_count = db.query(func.count(PersistentCandidate.id)).scalar() or 0
        logger.info(f"Total candidates in database: {total_count}")
        
        # Use Redis to track retry counts for each candidate (fresh client per call)
        retry_key_prefix = "metadata_build:retry:"
        redis = get_redis()
        
        # Count candidates without Trakt IDs (don't load all into memory)
        if not force:
            candidates_count = db.query(func.count(PersistentCandidate.id)).filter(
                PersistentCandidate.trakt_id.is_(None)
            ).scalar() or 0
        else:
            candidates_count = total_count
        
        logger.info(f"Candidates without Trakt IDs: {candidates_count}")
        
        if candidates_count == 0:
            logger.info("All candidates already have Trakt IDs or reached retry limit")
            await self.set_build_status({
                "status": "complete",
                "total": total_count,
                "processed": total_count,
                "progress_percent": 100,
                "started_at": datetime.utcnow().isoformat(),
                "errors": 0
            })
            return
        
        logger.info(f"Starting Trakt ID lookup for {candidates_count} candidates (out of {total_count} total)")
        
        # Initialize status
        await self.set_build_status({
            "status": "running",
            "total": candidates_count,
            "processed": 0,
            "progress_percent": 0,
            "started_at": datetime.utcnow().isoformat(),
            "errors": 0
        })
        
        try:
            # Initialize Trakt client
            trakt_client = TraktClient(user_id=user_id)
            
            # Process using pagination to avoid loading all into memory
            # Trakt API limits: ~1000 requests per 5 minutes = ~3 requests/second max
            batch_size = 100  # Fetch and process in batches of 100
            processed = 0
            errors = 0
            unmapped_ids = []
            offset = 0
            
            # Use pagination instead of loading all candidates
            while offset < candidates_count:
                # Fetch batch from database
                if not force:
                    batch = db.query(PersistentCandidate).filter(
                        PersistentCandidate.trakt_id.is_(None)
                    ).limit(batch_size).offset(offset).all()
                else:
                    batch = db.query(PersistentCandidate).limit(batch_size).offset(offset).all()
                
                if not batch:
                    break
                
                logger.info(f"Processing batch at offset {offset}, got {len(batch)} candidates")
                
                for candidate in batch:
                    try:
                        # Use TMDB ID to search for Trakt ID
                        if candidate.tmdb_id and candidate.media_type:
                            trakt_id = await self._lookup_trakt_id(
                                tmdb_id=candidate.tmdb_id,
                                media_type=candidate.media_type,
                                trakt_client=trakt_client
                            )
                            
                            if trakt_id:
                                candidate.trakt_id = trakt_id
                                db.add(candidate)
                                # Reset retry count on success
                                await redis.delete(f"{retry_key_prefix}{candidate.id}")
                            else:
                                # Increment retry count for failed lookup
                                current_retry = 0
                                try:
                                    val = await redis.get(f"{retry_key_prefix}{candidate.id}")
                                    if val is not None:
                                        current_retry = int(val)
                                except Exception:
                                    pass
                                await redis.set(f"{retry_key_prefix}{candidate.id}", str(current_retry + 1), ex=86400)  # 24h TTL
                                unmapped_ids.append(candidate.id)
                            
                    except Exception as e:
                        logger.warning(f"Failed to lookup Trakt ID for candidate {candidate.id} (TMDB: {candidate.tmdb_id}): {e}")
                        errors += 1
                        # Increment retry count for errors
                        current_retry = 0
                        try:
                            val = await redis.get(f"{retry_key_prefix}{candidate.id}")
                            if val is not None:
                                current_retry = int(val)
                        except Exception:
                            pass
                        await redis.set(f"{retry_key_prefix}{candidate.id}", str(current_retry + 1), ex=86400)
                        unmapped_ids.append(candidate.id)
                    
                    processed += 1
                    
                    # Update progress every 20 items
                    if processed % 20 == 0:
                        progress_percent = (processed / candidates_count) * 100
                        await self.set_build_status({
                            "status": "running",
                            "total": candidates_count,
                            "processed": processed,
                            "progress_percent": round(progress_percent, 2),
                            "started_at": current_status.get("started_at") or datetime.utcnow().isoformat(),
                            "errors": errors
                        })
                
                # Commit after each batch
                try:
                    db.commit()
                    logger.info(f"Committed batch at offset {offset}")
                except Exception as e:
                    logger.error(f"Failed to commit batch: {e}")
                    db.rollback()
                
                # Move to next batch
                offset += batch_size
                
                # Clear session to free memory
                db.expunge_all()
            
            # Final commit
            try:
                db.commit()
            except Exception as e:
                logger.error(f"Failed to commit final batch: {e}")
                db.rollback()
            
            # Determine completion status
            if unmapped_ids and len(unmapped_ids) > 0:
                # Mark as partial if there are still unmapped items
                logger.info(f"Metadata build partial: {len(unmapped_ids)} items still unmapped (will retry later)")
                await self.set_build_status({
                    "status": "partial",
                    "total": candidates_count,
                    "processed": processed,
                    "progress_percent": 100,
                    "started_at": current_status.get("started_at"),
                    "errors": errors,
                    "unmapped_ids": unmapped_ids[:100]  # Store first 100 for debugging
                })
                # Set completion flag so UI doesn't show metadata screen on reload
                # Periodic retry task will continue trying to map remaining items
                r_done = get_redis()
                await r_done.set("metadata_build:scan_completed", "true")
            else:
                # Mark as complete if all mapped
                logger.info(f"Metadata build complete: all {processed} candidates processed")
                await self.set_build_status({
                    "status": "complete",
                    "total": candidates_count,
                    "processed": processed,
                    "progress_percent": 100,
                    "started_at": current_status.get("started_at"),
                    "errors": errors
                })
                # Set completion flag
                r_done = get_redis()
                await r_done.set("metadata_build:scan_completed", "true")
            
            logger.info(f"Metadata build complete: {processed} processed, {errors} errors")
            
        except Exception as e:
            logger.error(f"Metadata build failed: {e}", exc_info=True)
            await self.set_build_status({
                "status": "error",
                "total": candidates_count,
                "processed": processed,
                "progress_percent": round((processed / candidates_count) * 100, 2) if candidates_count > 0 else 0,
                "started_at": current_status.get("started_at"),
                "errors": errors,
                "error_message": str(e)
            })
            raise
    
    async def _lookup_trakt_id(
        self,
        tmdb_id: int,
        media_type: str,
        trakt_client: TraktClient
    ) -> Optional[int]:
        """
        Lookup Trakt ID from TMDB ID using Trakt search API.
        
        Args:
            tmdb_id: TMDB ID
            media_type: 'movie' or 'tv'
            trakt_client: Authenticated Trakt client
            
        Returns:
            Trakt ID if found, None otherwise
        """
        try:
            # Use Trakt's ID lookup endpoint
            # GET /search/tmdb/:id?type=movie|show
            endpoint = f"/search/tmdb/{tmdb_id}"
            params = {"type": "movie" if media_type == "movie" else "show"}
            
            results = await trakt_client._request("GET", endpoint, params=params)
            
            if results and len(results) > 0:
                result = results[0]
                item = result.get("movie") or result.get("show")
                if item:
                    return item.get("ids", {}).get("trakt")
            
            return None
            
        except Exception as e:
            logger.debug(f"Trakt lookup failed for TMDB {tmdb_id}: {e}")
            return None
