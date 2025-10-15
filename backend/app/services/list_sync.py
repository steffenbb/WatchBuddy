"""
list_sync.py

Smart list synchronization service that handles watched status tracking,
incremental vs full syncs, and respects user preferences.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from app.core.database import SessionLocal
from app.models import UserList, ListItem, MediaMetadata
from app.services.trakt_client import TraktClient
from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.services.scoring_engine import ScoringEngine
from app.services.mood import ensure_user_mood
from app.services.tmdb_client import fetch_tmdb_metadata, get_tmdb_api_key
from app.services.dynamic_titles import DynamicTitleGenerator
import json


logger = logging.getLogger(__name__)


class ListSyncService:
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.trakt_client = TraktClient(user_id)
        self.candidate_provider = BulkCandidateProvider(self.user_id or 0)
        self.scoring_engine = ScoringEngine()
        # Readiness flags to bypass external calls when not configured
        self._trakt_ready = False
        self._tmdb_ready = False

    async def _update_readiness(self) -> None:
        """Compute and cache readiness flags for Trakt/TMDB to avoid timeouts when not configured."""
        try:
            from app.core.redis_client import get_redis
            r = get_redis()
            client_id = await r.get("settings:global:trakt_client_id")
            # Try both possible keys for user access token
            access = await r.get(f"settings:user:{self.user_id}:trakt_access_token") if self.user_id else None
            alt_access = await r.get(f"trakt_tokens:{self.user_id}") if self.user_id else None
            logger.warning(f"[DEBUG] Trakt readiness: client_id={client_id}, access={access}, alt_access={alt_access}, user_id={self.user_id}")
            self._trakt_ready = bool(client_id and (access or alt_access))
        except Exception as e:
            logger.warning(f"[DEBUG] Exception in Trakt readiness: {e}")
            self._trakt_ready = False
        try:
            tmdb_key = await get_tmdb_api_key()
            logger.warning(f"[DEBUG] TMDB readiness: tmdb_key={'set' if tmdb_key else 'missing'}")
            self._tmdb_ready = bool(tmdb_key)
        except Exception as e:
            logger.warning(f"[DEBUG] Exception in TMDB readiness: {e}")
            self._tmdb_ready = False

    async def sync_all_lists(self, force_full: bool = False) -> Dict[str, Any]:
        """Sync all user lists based on their sync settings."""
        db = SessionLocal()
        results = {
            "synced": 0,
            "errors": 0,
            "lists": []
        }
        
        try:
            # Warm up mood cache daily for this user
            try:
                if self.user_id:
                    await ensure_user_mood(self.user_id)
            except Exception:
                pass

            user_lists = db.query(UserList).filter(
                UserList.user_id == self.user_id if self.user_id else True
            ).all()
            
            for user_list in user_lists:
                try:
                    sync_result = await self._sync_single_list(user_list, force_full=force_full)
                    results["lists"].append(sync_result)
                    if sync_result["status"] == "success":
                        results["synced"] += 1
                    else:
                        results["errors"] += 1
                except Exception as e:
                    logger.error(f"Error syncing list {user_list.id}: {e}")
                    results["errors"] += 1
                    results["lists"].append({
                        "list_id": user_list.id,
                        "status": "error",
                        "error": str(e)
                    })
        finally:
            db.close()
        
        return results

    async def _sync_single_list(self, user_list: UserList, force_full: bool = False) -> Dict[str, Any]:
        """Sync a single list with smart incremental/full sync logic."""
        db = SessionLocal()
        
        try:
            # Ensure we operate on an instance bound to this session
            try:
                list_id = user_list.id
            except Exception:
                raise ValueError("Invalid user_list passed to sync")
            user_list = db.query(UserList).filter(UserList.id == list_id).first()
            if not user_list:
                raise ValueError(f"List {list_id} not found")
            logger.info(f"[SYNC] Preparing sync for list {user_list.id} ({user_list.title})")
            # Mark active sync in Redis for UI visibility
            try:
                from app.core.redis_client import get_redis
                r = get_redis()
                await r.set(f"sync_lock:{user_list.id}", json.dumps({
                    "list_id": user_list.id,
                    "started_at": datetime.utcnow().timestamp(),
                    "type": "full" if force_full else "auto"
                }), ex=3600)
            except Exception:
                pass
            # Update sync status
            user_list.sync_status = "syncing"
            db.commit()
            
            # Determine sync type
            sync_type = self._determine_sync_type(user_list, force_full)
            logger.info(f"Starting {sync_type} sync for list {user_list.id}: {user_list.title}")

            # Update external service readiness
            await self._update_readiness()
            
            # Send sync start notification
            from app.api.notifications import send_notification
            try:
                await send_notification(
                    user_list.user_id,
                    f"Syncing '{user_list.title}'...",
                    "info"
                )
            except Exception:
                pass

            if sync_type == "skip":
                # Avoid unnecessary API usage
                now = datetime.utcnow()
                user_list.last_sync_at = now
                user_list.sync_status = "skipped"
                db.commit()
                return {
                    "list_id": user_list.id,
                    "status": "skipped",
                    "sync_type": sync_type,
                    "items_updated": 0,
                    "total_items": 0
                }
            
            # Get previous items and validate they still match current filters
            prev_items = db.query(ListItem).filter(ListItem.smartlist_id == user_list.id).all()
            
            # Check which previous items still match current filters
            valid_prev_ids = set()
            invalid_prev_ids = set()
            filters = json.loads(user_list.filters) if user_list.filters else {}
            filter_languages = filters.get("languages", [])
            filter_genres = filters.get("genres", [])
            
            for item in prev_items:
                item_id = item.trakt_id or item.item_id
                if not item_id:
                    continue
                    
                # Fetch metadata to validate against current filters
                from app.models import PersistentCandidate
                candidate = None
                if item.trakt_id:
                    candidate = db.query(PersistentCandidate).filter(
                        PersistentCandidate.trakt_id == item.trakt_id
                    ).first()
                
                # If we have candidate metadata, validate filters
                if candidate:
                    is_valid = True
                    # Check language filter
                    if filter_languages and candidate.language:
                        if candidate.language.lower() not in [l.lower() for l in filter_languages]:
                            is_valid = False
                            logger.debug(f"[SYNC] Item {item.title} no longer matches language filter (has: {candidate.language}, need: {filter_languages})")
                    
                    # Check genre filter (basic check)
                    if is_valid and filter_genres and candidate.genres:
                        try:
                            item_genres = json.loads(candidate.genres) if isinstance(candidate.genres, str) else candidate.genres
                            if item_genres:
                                genre_set = {g.lower() for g in item_genres if isinstance(g, str)}
                                filter_set = {g.lower() for g in filter_genres}
                                if not genre_set.intersection(filter_set):
                                    is_valid = False
                                    logger.debug(f"[SYNC] Item {item.title} no longer matches genre filter")
                        except Exception as e:
                            logger.debug(f"[SYNC] Could not parse genres for {item.title}: {e}")
                    
                    if is_valid:
                        valid_prev_ids.add(item_id)
                    else:
                        invalid_prev_ids.add(item_id)
                else:
                    # No metadata found - if filters are strict (e.g., language), consider invalid
                    # to ensure proper refresh. External content without metadata should be re-sourced.
                    if filter_languages or filter_genres:
                        invalid_prev_ids.add(item_id)
                        logger.debug(f"[SYNC] Item {item.title} has no metadata and filters are active - marking for removal")
                    else:
                        # No filters, keep item
                        valid_prev_ids.add(item_id)
            
            logger.info(f"[SYNC] List {user_list.id}: {len(valid_prev_ids)} items still match filters, {len(invalid_prev_ids)} will be removed")
            
            # Generate new candidates based on list filters
            # Note: _get_list_candidates already handles exclusion of recently shown items internally
            candidates = await self._get_list_candidates(user_list)
            logger.debug(f"[SYNC] List {user_list.id} generated {len(candidates)} raw candidates")
            
            # Apply watched status filtering
            if user_list.sync_watched_status:
                if self._trakt_ready:
                    candidates = await self._apply_watched_filtering(candidates, user_list)
                else:
                    logger.info(f"[SYNC] Skipping watched-status filtering for list {user_list.id}: Trakt not configured")
            


            # --- All Lists: Ensure 60% new recommendations and deduplication ---

            if not candidates:
                logger.warning(f"[SYNC] No valid candidates found for list {user_list.id} ({user_list.title}) after filtering. Skipping update.")
                from app.api.notifications import send_notification
                await send_notification(
                    user_list.user_id,
                    f"No valid recommendations could be found for '{user_list.title}'. Try adjusting your filters or check your candidate pool.",
                    "warning"
                )
                now = datetime.utcnow()
                user_list.last_sync_at = now
                user_list.sync_status = "no_candidates"
                db.commit()
                return {
                    "list_id": user_list.id,
                    "status": "no_candidates",
                    "sync_type": sync_type,
                    "items_updated": 0,
                    "total_items": 0
                }

            scored_candidates = await self._score_candidates(candidates, user_list)
            
            # Remove duplicates from candidates (based on IDs)
            seen_ids = set()
            deduped_candidates = []
            skipped_no_id = 0
            skipped_duplicate = 0
            for c in scored_candidates:
                # Try multiple ID extraction methods
                trakt_id = c.get("trakt_id")
                tmdb_id = c.get("tmdb_id")
                item_id = c.get("item_id")
                
                # Also check nested ids dict
                ids_dict = c.get("ids", {})
                if not trakt_id and isinstance(ids_dict, dict):
                    trakt_id = ids_dict.get("trakt")
                if not tmdb_id and isinstance(ids_dict, dict):
                    tmdb_id = ids_dict.get("tmdb")
                
                cid = trakt_id or tmdb_id or item_id
                
                if not cid:
                    skipped_no_id += 1
                    logger.warning(f"[DEDUP] Skipping candidate '{c.get('title', 'UNKNOWN')}' - no ID found (trakt={trakt_id}, tmdb={tmdb_id}, item={item_id}, ids={ids_dict})")
                    continue
                if cid in seen_ids:
                    skipped_duplicate += 1
                    logger.debug(f"[DEDUP] Skipping candidate '{c.get('title', 'UNKNOWN')}' - duplicate ID {cid}")
                    continue
                deduped_candidates.append(c)
                seen_ids.add(cid)
            
            logger.warning(f"[DEDUP] List {user_list.id}: {len(scored_candidates)} scored → {len(deduped_candidates)} deduped (skipped: {skipped_no_id} no ID, {skipped_duplicate} duplicates)")

            # Get IDs that are already on the list (to avoid duplicates)
            existing_list_ids = set()
            for item in prev_items:
                item_id = item.trakt_id or item.item_id
                if item_id:
                    existing_list_ids.add(item_id)
            
            # Filter out candidates that are already on the list
            # Check both direct fields and nested ids dict
            fresh_candidates = []
            already_on_list = 0
            for c in deduped_candidates:
                ids_dict = c.get("ids", {})
                cid = (
                    c.get("trakt_id") or 
                    c.get("tmdb_id") or 
                    c.get("item_id") or
                    ids_dict.get("trakt") or 
                    ids_dict.get("tmdb")
                )
                if cid not in existing_list_ids:
                    fresh_candidates.append(c)
                else:
                    already_on_list += 1
            
            logger.warning(f"[FRESH_FILTER] List {user_list.id}: {len(deduped_candidates)} deduped → {len(fresh_candidates)} fresh (filtered {already_on_list} already on list, {len(existing_list_ids)} items on list)")
            if fresh_candidates:
                logger.warning(f"[FRESH_FILTER] First 10 fresh candidates: {[c.get('title') for c in fresh_candidates[:10]]}")
            
            # Take top N candidates by score up to item_limit
            item_limit = user_list.item_limit or 50
            
            # For incremental sync: Keep some existing items if they still match filters (valid_prev_ids)
            # For full sync: Replace everything with fresh candidates
            if sync_type == "incremental" and valid_prev_ids:
                # Keep up to 40% of existing valid items, fill rest with fresh candidates
                num_fresh = int(item_limit * 0.6)
                num_keep = item_limit - num_fresh
                
                # Get existing items that are still valid - check both direct fields and ids dict
                keep_candidates = []
                for c in deduped_candidates:
                    ids_dict = c.get("ids", {})
                    cid = (
                        c.get("trakt_id") or 
                        c.get("tmdb_id") or 
                        c.get("item_id") or
                        ids_dict.get("trakt") or 
                        ids_dict.get("tmdb")
                    )
                    if cid in valid_prev_ids:
                        keep_candidates.append(c)
                        if len(keep_candidates) >= num_keep:
                            break
                
                # Fill with fresh candidates
                limited_candidates = fresh_candidates[:num_fresh] + keep_candidates
                logger.debug(f"[SYNC] Incremental sync: {len(fresh_candidates[:num_fresh])} fresh + {len(keep_candidates)} kept = {len(limited_candidates)} total")
            else:
                # Full sync or no existing items: Just take top N fresh candidates
                limited_candidates = fresh_candidates[:item_limit]
                logger.debug(f"[SYNC] Full sync: taking top {len(limited_candidates)} fresh candidates")
            
            # Final deduplication (in case of overlap between fresh and keep)
            final_ids = set()
            final_candidates = []
            for c in limited_candidates:
                ids_dict = c.get("ids", {})
                cid = (
                    c.get("trakt_id") or 
                    c.get("tmdb_id") or 
                    c.get("item_id") or
                    ids_dict.get("trakt") or 
                    ids_dict.get("tmdb")
                )
                if cid and cid not in final_ids:
                    final_candidates.append(c)
                    final_ids.add(cid)
            limited_candidates = final_candidates[:item_limit]
            logger.debug(f"[SYNC][ALL] List {user_list.id} will persist {len(limited_candidates)} candidates (limit={item_limit})")

            # Get existing item count for calculating removed count
            existing_count = 0
            if sync_type == "incremental":
                existing_count = db.query(ListItem).filter(
                    ListItem.smartlist_id == user_list.id
                ).count()

            # Debug: Log before update
            logger.warning(f"[DEBUG] About to call _update_list_items - user_list.id={user_list.id}, num_candidates={len(limited_candidates)}, is_full_sync={sync_type == 'full'}")
            logger.warning(f"[DEBUG] First few candidate titles: {[c.get('title', 'NO_TITLE') for c in limited_candidates[:5]]}")

            updated_count = await self._update_list_items(
                user_list.id, limited_candidates, sync_type == "full"
            )

            # Calculate removed count for incremental sync
            removed_count = 0
            if sync_type != "full":
                removed_count = max(0, existing_count - len(limited_candidates))

            # If full sync, eagerly enrich TMDB posters/backdrops for all items
            if sync_type == "full":
                if self._tmdb_ready:
                    try:
                        await self._enrich_posters_for_candidates(limited_candidates)
                    except Exception as e:
                        logger.warning(f"[SYNC] Poster enrichment skipped due to error: {e}")
                else:
                    logger.info(f"[SYNC] Skipping poster enrichment: TMDB not configured")
            
            # Update dynamic title if needed (for SmartLists only)
            new_title = None  # Initialize to None
            if user_list.list_type == "smartlist" and user_list.user_id:
                try:
                    title_generator = DynamicTitleGenerator(user_list.user_id)
                    should_update = await title_generator.should_update_title(user_list)
                    
                    if should_update:
                        # Parse existing filters to get the parameters
                        filters = {}
                        if user_list.filters:
                            try:
                                filters = json.loads(user_list.filters)
                            except:
                                pass
                        
                        new_title = await title_generator.generate_title(
                            list_type=user_list.list_type,
                            discovery=filters.get("discovery", "balanced"),
                            media_types=filters.get("media_types", ["movies", "shows"]),
                            fusion_mode=filters.get("fusion_mode", False)
                        )
                        
                        if new_title != user_list.title:
                            logger.info(f"[SYNC] Updating list title from '{user_list.title}' to '{new_title}'")
                            user_list.title = new_title
                        
                except Exception as e:
                    logger.warning(f"[SYNC] Dynamic title update failed: {e}")
            
            # Update sync metadata
            now = datetime.utcnow()
            user_list.last_sync_at = now
            user_list.last_updated = now
            user_list.sync_status = "complete"
            user_list.last_error = None
            
            if sync_type == "full":
                user_list.last_full_sync_at = now
            
            db.commit()
            
            # Sync to Trakt list if it exists
            trakt_sync_success = False
            if user_list.trakt_list_id and self._trakt_ready:
                try:
                    # Get current list items for Trakt sync
                    current_items = db.query(ListItem).filter(
                        ListItem.smartlist_id == user_list.id
                    ).all()
                    
                    # Format items for Trakt
                    trakt_items = []
                    for item in current_items:
                        if item.trakt_id and isinstance(item.trakt_id, int):
                            trakt_items.append({
                                "trakt_id": item.trakt_id,
                                "media_type": item.media_type or "movie"
                            })
                    
                    if trakt_items:
                        trakt_client = TraktClient(user_id=user_list.user_id)
                        
                        # Update list title on Trakt if it changed
                        if new_title and new_title != user_list.title:
                            try:
                                await trakt_client.update_list(
                                    user_list.trakt_list_id,
                                    name=new_title,
                                    description="Created and managed by WatchBuddy"
                                )
                            except Exception as e:
                                logger.warning(f"[SYNC] Failed to update Trakt list title: {e}")
                        
                        # Sync items to Trakt
                        sync_stats = await trakt_client.sync_list_items(
                            user_list.trakt_list_id,
                            trakt_items
                        )
                        logger.info(f"[SYNC] Trakt sync: {sync_stats}")
                        trakt_sync_success = True
                        
                        # Send notification about Trakt sync
                        from app.api.notifications import send_notification
                        if sync_stats.get("added", 0) > 0 or sync_stats.get("removed", 0) > 0:
                            await send_notification(
                                user_list.user_id,
                                f"Synced to Trakt: +{sync_stats.get('added', 0)} -{sync_stats.get('removed', 0)}",
                                "info"
                            )
                except Exception as e:
                    logger.warning(f"[SYNC] Failed to sync to Trakt list {user_list.trakt_list_id}: {e}")
                    from app.api.notifications import send_notification
                    try:
                        await send_notification(
                            user_list.user_id,
                            f"List synced locally, but Trakt sync failed",
                            "warning"
                        )
                    except Exception:
                        pass
            elif user_list.trakt_list_id and not self._trakt_ready:
                logger.info(f"[SYNC] Skipping Trakt sync for list {user_list.id}: Trakt not configured")
            
            # Send notification with sync details
            from app.services.tasks import format_sync_notification, send_toast_notification
            trigger = "manual" if force_full else "auto"
            msg = format_sync_notification(user_list.title, trigger, updated=updated_count, removed=removed_count, total=len(limited_candidates))
            await send_toast_notification(user_list.user_id, msg, "success")

            return {
                "list_id": user_list.id,
                "status": "success",
                "sync_type": sync_type,
                "items_updated": updated_count,
                "items_removed": removed_count,
                "total_items": len(limited_candidates)
            }
            
        except Exception as e:
            user_list.sync_status = "error"
            user_list.last_error = str(e)
            db.commit()
            raise
        finally:
            # Clear active sync lock
            try:
                from app.core.redis_client import get_redis
                r = get_redis()
                # Small delay to allow UI poller to catch active syncs
                try:
                    await asyncio.sleep(2)
                except Exception:
                    pass
                await r.delete(f"sync_lock:{user_list.id}")
            except Exception:
                pass
            db.close()

    def _determine_sync_type(self, user_list: UserList, force_full: bool) -> str:
        """Determine if we should do full or incremental sync."""
        if force_full:
            return "full"
        
        # First sync is always full
        if not user_list.last_sync_at:
            return "full"
        
        # Check list-specific settings from filters
        full_sync_days = 1  # default daily full sync unless overridden
        try:
            if user_list.filters:
                filters = json.loads(user_list.filters)
                if isinstance(filters, dict):
                    full_sync_days = int(filters.get("full_sync_days", full_sync_days))
        except Exception:
            pass

        # Check if it's time for a full sync based on last_full_sync_at and setting
        if user_list.last_full_sync_at:
            days_since_full = (datetime.utcnow() - user_list.last_full_sync_at).days
            if days_since_full >= max(1, full_sync_days):
                return "full"
        
        # Check sync interval preference (default to 0.5 hours = 30 minutes if not set)
        sync_interval_hours = user_list.sync_interval if user_list.sync_interval is not None else 0.5
        hours_since_sync = (datetime.utcnow() - user_list.last_sync_at).total_seconds() / 3600
        if hours_since_sync >= sync_interval_hours:
            return "incremental"

        return "skip"

    async def _get_list_candidates(self, user_list: UserList) -> List[Dict[str, Any]]:
        filters = {}
        if user_list.filters:
            try:
                filters = json.loads(user_list.filters)
            except:
                filters = {}
        logger.warning(f"[DEBUG][CANDIDATE] List {user_list.id} filters: {filters}")
        print(f"[DEBUG][CANDIDATE] List {user_list.id} filters: {filters}")
        
        # Default to mixed content if no specific type
        media_types = filters.get("media_types", ["movies", "shows"])
        if not isinstance(media_types, list):
            media_types = ["movies", "shows"]
        
        # Get list of recently shown items (last 3 syncs) for rotation/freshness
        db = SessionLocal()
        try:
            recent_trakt_ids = set()
            existing_items = db.query(ListItem).filter(
                ListItem.smartlist_id == user_list.id
            ).order_by(ListItem.added_at.desc()).limit(user_list.item_limit * 3 if user_list.item_limit else 100).all()
            
            for item in existing_items:
                if item.trakt_id:
                    recent_trakt_ids.add(item.trakt_id)
            
            logger.info(f"Excluding {len(recent_trakt_ids)} recently shown items for freshness")
        finally:
            db.close()
        
        # Get candidates from bulk provider
        all_candidates = []
        for media_type in media_types:
            # Extract filter parameters (must be before logger.warning)
            genres = filters.get("genres", [])
            languages = filters.get("languages", [])
            min_year = filters.get("year_from")
            max_year = filters.get("year_to")
            min_rating = filters.get("min_rating")
            # Compute limits before logging
            base_limit = int(filters.get("candidate_limit", 200))
            enhanced_limit = max(base_limit, 1600)
            enhanced_limit = min(enhanced_limit, 4000)
            # Discovery/search inputs
            discovery = filters.get("discovery") or filters.get("mood") or "balanced"
            search_keywords = filters.get("search_query")
            search_keywords_list = search_keywords.split() if search_keywords else None
            logger.warning(f"[DEBUG][CANDIDATE] Fetching candidates for media_type={media_type}, genres={genres}, languages={languages}, min_year={min_year}, max_year={max_year}, limit={enhanced_limit}")
            
            # Enhanced candidate fetching for better recommendation quality (computed above)
            # Keep discovery balanced to rely on PersistentCandidate ordering heuristics
            enhanced_discovery = discovery or "balanced"
            
            candidates = await self.candidate_provider.get_candidates(
                search_keywords=search_keywords_list,
                discovery=enhanced_discovery,
                media_type=media_type,
                genres=genres,
                languages=languages,
                min_year=min_year,
                max_year=max_year,
                min_rating=min_rating,
                limit=enhanced_limit,
                list_title=user_list.title,
                fusion_mode=filters.get("fusion_mode", False),
                persistent_only=True  # Only use persistent DB for syncs, no TMDB fallback
            )
            
            logger.warning(f"[DEBUG] Candidates from provider for {media_type} - sample titles: {[c.get('title', 'NO_TITLE') for c in candidates[:5]]}")
            logger.warning(f"[DEBUG][CANDIDATE] Provider returned {len(candidates)} candidates for media_type={media_type}")
            if candidates:
                logger.warning(f"[DEBUG][CANDIDATE] Sample candidate: {candidates[0]}")
            
            # Filter out recently shown items for freshness
            fresh_candidates = []
            for candidate in candidates:
                trakt_id = candidate.get("trakt_id") or (candidate.get("ids", {}).get("trakt") if isinstance(candidate.get("ids"), dict) else None)
                if trakt_id not in recent_trakt_ids:
                    fresh_candidates.append(candidate)
            
            logger.info(f"Filtered {len(candidates) - len(fresh_candidates)} recently shown items from {media_type}")
            all_candidates.extend(fresh_candidates)
            
        logger.info(f"Total fresh candidates gathered: {len(all_candidates)} from {len(media_types)} media types")
        if not all_candidates:
            logger.warning(f"[DEBUG][CANDIDATE] No candidates found after all filters for list {user_list.id}")
        
        # Add small random shuffle for variety (shuffle top 30% of results)
        if all_candidates and len(all_candidates) > 10:
            import random
            top_portion = int(len(all_candidates) * 0.3)
            if top_portion > 0:
                top_items = all_candidates[:top_portion]
                random.shuffle(top_items)
                all_candidates[:top_portion] = top_items
        
        return all_candidates

    async def _apply_watched_filtering(self, candidates: List[Dict[str, Any]], user_list: UserList) -> List[Dict[str, Any]]:
        """Filter candidates based on watched status preferences."""
        if not user_list.exclude_watched:
            # Just update watched status but don't filter
            return await self._enrich_with_watched_status(candidates)
        
        # Get watched status for filtering
        enriched_candidates = await self._enrich_with_watched_status(candidates)
        
        # Filter out watched items
        filtered = []
        for candidate in enriched_candidates:
            if not candidate.get("is_watched", False):
                filtered.append(candidate)
        
        logger.info(f"Filtered {len(candidates) - len(filtered)} watched items from list {user_list.id}")
        return filtered

    async def _enrich_with_watched_status(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add watched status information to candidates."""
        # Check if any candidates have Trakt IDs - no point fetching if none do
        has_trakt_ids = any(candidate.get("trakt_id") for candidate in candidates)
        
        if not has_trakt_ids:
            logger.debug("Skipping watched status enrichment - no candidates have Trakt IDs")
            # Mark all as not watched and return
            for candidate in candidates:
                candidate["is_watched"] = False
                candidate["watched_at"] = None
            return candidates
        
        # Get all watched status in bulk for efficiency
        watched_movies = await self.trakt_client.get_watched_status("movies")
        watched_shows = await self.trakt_client.get_watched_status("shows")
        
        enriched = []
        for candidate in candidates:
            trakt_id = candidate.get("trakt_id")
            media_type = candidate.get("media_type", "movie")
            
            if trakt_id:
                watched_dict = watched_movies if media_type == "movie" else watched_shows
                watched_info = watched_dict.get(trakt_id)
                
                candidate["is_watched"] = bool(watched_info)
                candidate["watched_at"] = watched_info.get("watched_at") if watched_info else None
            else:
                candidate["is_watched"] = False
                candidate["watched_at"] = None
            
            enriched.append(candidate)
        
        return enriched

    async def _score_candidates(self, candidates: List[Dict[str, Any]], user_list: UserList) -> List[Dict[str, Any]]:
        logger.warning(f"[DEBUG][SCORING] Scoring {len(candidates)} candidates for list {user_list.id}")
        print(f"[DEBUG][SCORING] Scoring {len(candidates)} candidates for list {user_list.id}")
        """Score and sort candidates using the scoring engine."""
        scored_candidates = []
        
        # Get user filters for scoring context
        filters = {}
        if user_list.filters:
            try:
                filters = json.loads(user_list.filters)
            except:
                filters = {}
        
        logger.warning(f"[DEBUG] Candidates before scoring for list {user_list.id}: {[c.get('title') for c in candidates]}")
        
        for candidate in candidates:
            try:
                # Use scoring engine to get comprehensive score
                score = await self.scoring_engine.score_candidate(
                    candidate, 
                    user_profile={}, 
                    filters=filters
                )
                candidate["score"] = score
                logger.warning(f"[DEBUG][SCORING] Candidate '{candidate.get('title')}' scored {score}")
                
                # Generate explanation for the item using existing explanation service
                try:
                    from app.services.explain import generate_explanation
                    # Create basic explanation metadata
                    explanation_meta = {
                        'title': candidate.get('title', 'Unknown'),
                        'year': candidate.get('year'),
                        'genres': candidate.get('genres', []),
                        'rating': candidate.get('rating'),
                        'score': score,
                        'reason': 'Based on genre preferences and popularity'
                    }
                    candidate["explanation"] = generate_explanation(explanation_meta)
                except Exception as ex:
                    logger.warning(f"Error generating explanation for {candidate.get('title', 'Unknown')}: {ex}")
                    candidate["explanation"] = f"Recommended based on your preferences (Score: {score:.2f})"
                
                scored_candidates.append(candidate)
            except Exception as e:
                logger.warning(f"Error scoring candidate {candidate.get('title', 'Unknown')}: {e}")
                candidate["score"] = 0.5
                candidate["explanation"] = "Recommended for you"
                scored_candidates.append(candidate)
        
        # Sort by score descending
        scored_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        logger.warning(f"[DEBUG] Candidates after scoring for list {user_list.id}: {[c.get('title') for c in scored_candidates]}")
        if not scored_candidates:
            logger.warning(f"[DEBUG][SCORING] No candidates survived scoring for list {user_list.id}")
        return scored_candidates

    async def _resolve_trakt_id(self, candidate: Dict[str, Any]) -> Optional[int]:
        """Resolve Trakt ID from TMDB/IMDB IDs if missing."""
        # First check if trakt_id is already present in ids dict
        ids = candidate.get("ids", {})
        raw_trakt = ids.get("trakt") or candidate.get("trakt_id")
        # Normalize to integer if possible; ignore non-numeric placeholders
        if isinstance(raw_trakt, int):
            return raw_trakt
        if isinstance(raw_trakt, str) and raw_trakt.isdigit():
            return int(raw_trakt)
        
        # Try to look up via TMDB ID (check both locations)
        tmdb_id = ids.get("tmdb") or candidate.get("tmdb_id")
        media_type = candidate.get("media_type")
        
        if tmdb_id and media_type:
            # Don't attempt network lookup if Trakt isn't configured
            if not getattr(self, "_trakt_ready", False):
                logger.debug("Trakt not configured; skipping trakt_id lookup via TMDB")
                return None
            try:
                logger.debug(f"Looking up Trakt ID for TMDB {tmdb_id} ({media_type})")
                results = await self.trakt_client.search_by_tmdb_id(tmdb_id, media_type)
                if results:
                    # Extract trakt_id from first result
                    first_result = results[0]
                    item_data = first_result.get(media_type, {})
                    trakt_id = item_data.get("ids", {}).get("trakt")
                    if trakt_id:
                        logger.debug(f"Found Trakt ID {trakt_id} for TMDB {tmdb_id}")
                        return trakt_id
            except Exception as e:
                logger.debug(f"Error looking up Trakt ID for TMDB {tmdb_id}: {e}")
        
        # Could also try IMDB lookup here if needed
        # For now, return None if we couldn't resolve
        logger.warning(f"Could not resolve Trakt ID for candidate: {candidate.get('title')} (TMDB: {tmdb_id})")
        return None

    async def _update_list_items(
        self, 
        user_list_id: int, 
        candidates: List[Dict[str, Any]], 
        is_full_sync: bool
    ) -> int:
        """Update database with new list items."""
        db = SessionLocal()
        updated_count = 0
        
        try:
            # Get the list's media_type filter to enforce strict filtering
            user_list = db.query(UserList).filter(UserList.id == user_list_id).first()
            allowed_media_types = set()
            if user_list and user_list.filters:
                filters = json.loads(user_list.filters)
                # Check both "media_types" (plural) and "media_type" (singular) keys for compatibility
                media_types = filters.get("media_types") or filters.get("media_type", ["movies", "shows"])
                # Normalize to singular form: "movies" -> "movie", "shows" -> "show"
                for mt in media_types:
                    if mt == "movies":
                        allowed_media_types.add("movie")
                    elif mt == "shows":
                        allowed_media_types.add("show")
                    else:
                        allowed_media_types.add(mt)
            logger.warning(f"[MEDIA_TYPE_FILTER] List {user_list_id} allows media_types: {allowed_media_types}")
            
            # Get existing items in this session
            existing_items = {}
            items_to_refresh = set()  # Track items to be replaced with new content
            
            if not is_full_sync:
                current_items = db.query(ListItem).filter(
                    ListItem.smartlist_id == user_list_id
                ).all()
                existing_items = {item.trakt_id: item for item in current_items if item.trakt_id}
                
                # Implement 60% content refresh: randomly select 60% of items to replace
                if existing_items and len(existing_items) > 5:  # Only refresh if we have enough items
                    import random
                    total_items = len(existing_items)
                    num_to_refresh = int(total_items * 0.6)  # 60% refresh
                    trakt_ids_list = list(existing_items.keys())
                    items_to_refresh = set(random.sample(trakt_ids_list, min(num_to_refresh, len(trakt_ids_list))))
                    logger.info(f"[CONTENT_REFRESH] Will refresh {len(items_to_refresh)} of {total_items} items (60%)")
            
            # If full sync, remove all existing items first
            if is_full_sync:
                db.query(ListItem).filter(
                    ListItem.smartlist_id == user_list_id
                ).delete()
                db.commit()
                existing_items = {}
                items_to_refresh = set()
            
            # Track which items we're keeping
            processed_trakt_ids = set()
            any_candidates_applied = False
            
            logger.warning(f"[DEBUG] Starting loop - total candidates: {len(candidates)}")
            for idx, candidate in enumerate(candidates):
                if idx == 0:
                    logger.warning(f"[DEBUG] First candidate keys: {list(candidate.keys())}")
                    logger.warning(f"[DEBUG] First candidate sample: {candidate}")
                
                # STRICT MEDIA_TYPE FILTER: Skip candidates that don't match the list's allowed media types
                candidate_media_type = candidate.get("media_type", "movie")
                if allowed_media_types and candidate_media_type not in allowed_media_types:
                    logger.warning(f"[MEDIA_TYPE_FILTER] Skipping candidate '{candidate.get('title')}' - media_type '{candidate_media_type}' not in allowed {allowed_media_types}")
                    continue
                
                # Resolve Trakt ID (from candidate or via lookup)
                trakt_id = await self._resolve_trakt_id(candidate)
                logger.warning(f"[DEBUG] Candidate {idx}: trakt_id={trakt_id} (title: {candidate.get('title')})")
                
                # If we cannot resolve a numeric trakt_id, we skip setting trakt_id but may still be able to persist using item_id fallback
                tmdb_id = (candidate.get('ids') or {}).get('tmdb') if isinstance(candidate.get('ids'), dict) else candidate.get('tmdb_id')
                if not trakt_id and not tmdb_id and not candidate.get('item_id'):
                    logger.warning(f"[DEBUG] Skipping candidate {idx} - no usable identifier (no trakt_id/tmdb_id/item_id)")
                    continue
                
                if trakt_id:
                    processed_trakt_ids.add(trakt_id)
                
                # Check if item already exists
                existing_item = existing_items.get(trakt_id) if trakt_id else None
                
                # If item exists AND is NOT marked for refresh, update it
                # If item exists AND IS marked for refresh, treat as new (replace it)
                if existing_item and trakt_id not in items_to_refresh:
                    # Update existing item (keep it)
                    existing_item.score = candidate.get("score", 0)
                    existing_item.is_watched = candidate.get("is_watched", False)
                    existing_item.watched_at = candidate.get("watched_at")
                    existing_item.title = candidate.get("title", "")
                    updated_count += 1
                    any_candidates_applied = True
                else:
                    # Create new item (either new or replacing old item marked for refresh)
                    if existing_item and trakt_id in items_to_refresh:
                        # Remove old item to replace it
                        db.delete(existing_item)
                        logger.debug(f"[CONTENT_REFRESH] Replacing item trakt_id={trakt_id}")
                    
                    title_value = candidate.get("title", "")
                    logger.warning(f"[DEBUG] Creating ListItem - trakt_id={trakt_id}, title='{title_value}', media_type={candidate.get('media_type', 'movie')}")
                    
                    # Determine safe item_id and trakt_id values
                    # trakt_id MUST be int or None; item_id can be string surrogate (tmdb-<id>)
                    safe_trakt: Optional[int] = trakt_id if isinstance(trakt_id, int) else None
                    # Prefer trakt_id for item_id if available, else tmdb_id, else provided item_id
                    if safe_trakt is not None:
                        item_id_value = str(safe_trakt)
                    elif tmdb_id is not None:
                        item_id_value = f"tmdb-{tmdb_id}"
                    else:
                        item_id_value = str(candidate.get('item_id'))

                    # If we don't have a trakt_id, try to upsert on item_id to avoid duplicates
                    if safe_trakt is None and item_id_value:
                        existing_by_item = db.query(ListItem).filter(
                            ListItem.smartlist_id == user_list_id,
                            ListItem.item_id == item_id_value
                        ).first()
                        if existing_by_item:
                            # Update existing TMDB-only item
                            existing_by_item.media_type = candidate.get("media_type", "movie")
                            existing_by_item.title = title_value
                            existing_by_item.score = candidate.get("score", 0)
                            existing_by_item.is_watched = candidate.get("is_watched", False)
                            existing_by_item.watched_at = candidate.get("watched_at")
                            existing_by_item.explanation = candidate.get("explanation", "")
                            updated_count += 1
                            any_candidates_applied = True
                            continue

                    new_item = ListItem(
                        smartlist_id=user_list_id,
                        item_id=item_id_value,
                        trakt_id=safe_trakt,
                        media_type=candidate.get("media_type", "movie"),
                        title=title_value,
                        score=candidate.get("score", 0),
                        is_watched=candidate.get("is_watched", False),
                        watched_at=candidate.get("watched_at"),
                        explanation=candidate.get("explanation", "")
                    )
                    db.add(new_item)
                    updated_count += 1
                    any_candidates_applied = True
            
            # Remove items that are no longer in the top candidates (for incremental sync)
            if not is_full_sync:
                # If we didn't process any candidates, avoid destructive removal
                if not any_candidates_applied:
                    logger.info("[INCREMENTAL] No candidates applied; skipping removal of existing items to avoid wiping the list")
                    db.commit()
                    return updated_count
                items_to_remove = [
                    item for trakt_id, item in existing_items.items() 
                    if trakt_id not in processed_trakt_ids
                ]
                for item in items_to_remove:
                    db.delete(item)
            
            db.commit()
            logger.info(f"Updated {updated_count} items for list {user_list_id}")
            return updated_count
            
        finally:
            db.close()

    async def sync_watched_status_only(self, list_id: int) -> Dict[str, Any]:
        """Sync only watched status for existing list items without changing the list."""
        db = SessionLocal()
        
        try:
            # Ensure readiness flags are current
            await self._update_readiness()
            user_list = db.query(UserList).filter(UserList.id == list_id).first()
            if not user_list:
                raise ValueError(f"List {list_id} not found")
            if not self._trakt_ready:
                logger.info(f"[SYNC] Skipping watched-only sync for list {list_id}: Trakt not configured")
                return {"updated": 0, "removed": 0, "total": 0}
            
            list_items = db.query(ListItem).filter(
                ListItem.smartlist_id == list_id
            ).all()
            
            if not list_items:
                return {"updated": 0, "total": 0}
            
            # Get watched status for all media types
            watched_movies = await self.trakt_client.get_watched_status("movies")
            watched_shows = await self.trakt_client.get_watched_status("shows")
            
            updated_count = 0
            removed_count = 0
            for item in list_items:
                if not item.trakt_id:
                    continue
                
                watched_dict = watched_movies if item.media_type == "movie" else watched_shows
                watched_info = watched_dict.get(item.trakt_id)
                
                old_watched = item.is_watched
                new_watched = bool(watched_info)
                new_watched_at = watched_info.get("watched_at") if watched_info else None
                
                if user_list.exclude_watched and new_watched:
                    # Remove watched items if list excludes them
                    db.delete(item)
                    removed_count += 1
                else:
                    if old_watched != new_watched or item.watched_at != new_watched_at:
                        item.is_watched = new_watched
                        item.watched_at = new_watched_at
                        updated_count += 1
            
            db.commit()
            
            # Send notification for watched-only sync
            from app.services.tasks import format_sync_notification, send_toast_notification
            user_id = user_list.user_id
            msg = format_sync_notification(user_list.title, "watched-only", updated=updated_count, removed=removed_count, total=len(list_items))
            await send_toast_notification(user_id, msg, "info")
            return {
                "updated": updated_count,
                "removed": removed_count,
                "total": len(list_items)
            }
            
        finally:
            db.close()

    async def get_sync_stats(self, list_id: Optional[int] = None) -> Dict[str, Any]:
        """Get sync statistics for lists."""
        db = SessionLocal()
        
        try:
            query = db.query(UserList)
            if list_id:
                query = query.filter(UserList.id == list_id)
            elif self.user_id:
                query = query.filter(UserList.user_id == self.user_id)
            
            lists = query.all()
            
            stats = {
                "total_lists": len(lists),
                "pending": 0,
                "syncing": 0,
                "complete": 0,
                "error": 0,
                "lists": []
            }
            
            for user_list in lists:
                stats[user_list.sync_status] = stats.get(user_list.sync_status, 0) + 1
                
                item_count = db.query(ListItem).filter(
                    ListItem.smartlist_id == user_list.id
                ).count()
                
                watched_count = db.query(ListItem).filter(
                    ListItem.smartlist_id == user_list.id,
                    ListItem.is_watched == True
                ).count()
                
                stats["lists"].append({
                    "id": user_list.id,
                    "title": user_list.title,
                    "status": user_list.sync_status,
                    "last_sync": user_list.last_sync_at.isoformat() if user_list.last_sync_at else None,
                    "last_full_sync": user_list.last_full_sync_at.isoformat() if user_list.last_full_sync_at else None,
                    "item_count": item_count,
                    "watched_count": watched_count,
                    "error": user_list.last_error
                })
            
            return stats
            
        finally:
            db.close()

    async def _enrich_posters_for_candidates(self, candidates: List[Dict[str, Any]]):
        """Fetch and cache poster/backdrop for all candidates using TMDB, bounded concurrency.
        This runs after items are persisted during a full sync so the UI has images immediately.
        """
        # Quick exit if TMDB not configured
        try:
            tmdb_key = await get_tmdb_api_key()
        except Exception:
            tmdb_key = None
        if not tmdb_key:
            return

        # Build work list of (tmdb_id, media_type, trakt_id)
        # tmdb_id may be None initially; we'll try to resolve it via Trakt details per-item
        work: List[tuple[Optional[int], str, int]] = []
        for c in candidates:
            try:
                tmdb_id = c.get('tmdb_id') or (c.get('ids') or {}).get('tmdb')
                trakt_id = c.get('trakt_id') or (c.get('ids') or {}).get('trakt')
                mt = c.get('media_type') or c.get('type') or 'movie'
                if trakt_id:
                    work.append((int(tmdb_id) if tmdb_id else None, 'movie' if mt == 'movie' else 'tv', int(trakt_id)))
            except Exception:
                continue
        if not work:
            return

        import asyncio as _asyncio
        sem = _asyncio.Semaphore(5)

        async def fetch_and_cache(tmdb_id: Optional[int], mt: str, trakt_id: int):
            try:
                async with sem:
                    # If tmdb_id missing, try to fetch from Trakt details
                    _tmdb_id = tmdb_id
                    if _tmdb_id is None:
                        if getattr(self, "_trakt_ready", False):
                            try:
                                details = await self.trakt_client.get_item_details('movie' if mt == 'movie' else 'show', trakt_id)
                                _tmdb_id = (details.get('ids') or {}).get('tmdb') if isinstance(details, dict) else None
                            except Exception:
                                _tmdb_id = None
                        else:
                            _tmdb_id = None
                    if not _tmdb_id:
                        return
                    tmdb = await fetch_tmdb_metadata(_tmdb_id, mt)
                    if not tmdb:
                        return
                    poster_url = None
                    backdrop_url = None
                    pp = tmdb.get('poster_path')
                    bp = tmdb.get('backdrop_path')
                    if pp:
                        poster_url = f"https://image.tmdb.org/t/p/w342{pp}"
                    if bp:
                        backdrop_url = f"https://image.tmdb.org/t/p/w780{bp}"
                    # Upsert into MediaMetadata
                    local_db = SessionLocal()
                    try:
                        meta = local_db.query(MediaMetadata).filter(MediaMetadata.trakt_id == trakt_id).first()
                        from datetime import datetime as _dt
                        if meta:
                            if poster_url:
                                meta.poster_path = poster_url
                            if backdrop_url:
                                meta.backdrop_path = backdrop_url
                            meta.last_updated = _dt.utcnow()
                        else:
                            meta = MediaMetadata(
                                trakt_id=trakt_id,
                                tmdb_id=_tmdb_id,
                                media_type='movie' if mt == 'movie' else 'show',
                                title='',
                                poster_path=poster_url,
                                backdrop_path=backdrop_url,
                            )
                            local_db.add(meta)
                        local_db.commit()
                    except Exception:
                        local_db.rollback()
                    finally:
                        local_db.close()
            except Exception:
                # ignore individual failures
                pass

        await _asyncio.gather(*[fetch_and_cache(tm, mt, tid) for (tm, mt, tid) in work])
