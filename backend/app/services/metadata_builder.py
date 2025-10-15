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
        self.redis = get_redis()
        
    async def get_build_status(self) -> Dict[str, Any]:
        """Get current metadata build status from Redis."""
        status_json = await self.redis.get("metadata_build:status")
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
            await self.redis.setex(
                "metadata_build:status",
                86400,  # 24 hour expiry
                json.dumps(status)
            )
        except Exception as e:
            logger.error(f"Failed to update build status: {e}")
    
    async def check_metadata_ready(self, db: Session) -> bool:
        """Check if metadata has been built (Trakt IDs populated)."""
        from app.models import PersistentCandidate
        from sqlalchemy import func
        
        # Check if we have candidates with Trakt IDs
        total = db.query(func.count(PersistentCandidate.id)).scalar() or 0
        with_trakt = db.query(func.count(PersistentCandidate.id)).filter(
            PersistentCandidate.trakt_id.isnot(None)
        ).scalar() or 0
        
        # Consider ready if at least 80% have Trakt IDs
        if total == 0:
            return False
        
        percent = (with_trakt / total) * 100
        return percent >= 80.0
    
    async def build_trakt_ids(self, db: Session, user_id: int = 1, force: bool = False):
        """
        Bulk lookup and populate Trakt IDs for persistent candidates.
        
        Args:
            db: Database session
            user_id: User ID for Trakt authentication
            force: Force rebuild even if already complete
        """
        from app.models import PersistentCandidate
        from sqlalchemy import func
        
        # Check if already in progress
        current_status = await self.get_build_status()
        if current_status["status"] == "running" and not force:
            logger.info("Metadata build already in progress")
            return
        
        # Get total count and candidates without Trakt IDs
        total_count = db.query(func.count(PersistentCandidate.id)).scalar() or 0
        
        if not force:
            candidates_query = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id.is_(None)
            )
        else:
            candidates_query = db.query(PersistentCandidate)
        
        candidates_to_process = candidates_query.count()
        
        if candidates_to_process == 0:
            logger.info("All candidates already have Trakt IDs")
            await self.set_build_status({
                "status": "complete",
                "total": total_count,
                "processed": total_count,
                "progress_percent": 100,
                "started_at": datetime.utcnow().isoformat(),
                "errors": 0
            })
            return
        
        logger.info(f"Starting Trakt ID lookup for {candidates_to_process} candidates (out of {total_count} total)")
        
        # Initialize status
        await self.set_build_status({
            "status": "running",
            "total": candidates_to_process,
            "processed": 0,
            "progress_percent": 0,
            "started_at": datetime.utcnow().isoformat(),
            "errors": 0
        })
        
        try:
            # Initialize Trakt client
            trakt_client = TraktClient(user_id=user_id)
            
            # Process in batches to avoid memory issues
            batch_size = 100
            processed = 0
            errors = 0
            
            # Use pagination for large datasets
            offset = 0
            while True:
                batch = candidates_query.limit(batch_size).offset(offset).all()
                if not batch:
                    break
                
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
                            
                    except Exception as e:
                        logger.warning(f"Failed to lookup Trakt ID for candidate {candidate.id} (TMDB: {candidate.tmdb_id}): {e}")
                        errors += 1
                    
                    processed += 1
                    
                    # Update progress every 10 items
                    if processed % 10 == 0:
                        progress_percent = (processed / candidates_to_process) * 100
                        await self.set_build_status({
                            "status": "running",
                            "total": candidates_to_process,
                            "processed": processed,
                            "progress_percent": round(progress_percent, 2),
                            "started_at": current_status.get("started_at") or datetime.utcnow().isoformat(),
                            "errors": errors
                        })
                        
                        # Commit periodically
                        try:
                            db.commit()
                        except Exception as e:
                            logger.error(f"Failed to commit batch: {e}")
                            db.rollback()
                
                # Rate limiting: small delay between batches
                await asyncio.sleep(1)
                offset += batch_size
            
            # Final commit
            try:
                db.commit()
            except Exception as e:
                logger.error(f"Failed to commit final batch: {e}")
                db.rollback()
            
            # Mark as complete
            await self.set_build_status({
                "status": "complete",
                "total": candidates_to_process,
                "processed": processed,
                "progress_percent": 100,
                "started_at": current_status.get("started_at"),
                "errors": errors
            })
            
            logger.info(f"Metadata build complete: {processed} processed, {errors} errors")
            
        except Exception as e:
            logger.error(f"Metadata build failed: {e}")
            await self.set_build_status({
                "status": "error",
                "total": candidates_to_process,
                "processed": processed,
                "progress_percent": round((processed / candidates_to_process) * 100, 2) if candidates_to_process > 0 else 0,
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
