"""
individual_list_trakt_sync.py

Manual Trakt synchronization service for Individual Lists.
Handles creating/updating Trakt lists and syncing items.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from app.services.trakt_client import TraktClient
from app.services.trakt_id_resolver import TraktIdResolver
import asyncio
from app.core.database import SessionLocal
from app.models import IndividualList, IndividualListItem, PersistentCandidate
from app.utils.timezone import utc_now

logger = logging.getLogger(__name__)


class IndividualListTraktSync:
    """
    Manual Trakt synchronization for Individual Lists.
    
    Features:
    - Create new Trakt list if doesn't exist
    - Update existing Trakt list metadata (name, description)
    - Sync items (add new, remove deleted)
    - Map tmdb_id → trakt_id for all items
    - Return detailed status with error reporting
    - Only triggered manually by user
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
    
    def sync_list(self, list_id: int) -> Dict[str, Any]:
        """
        Synchronize an Individual List to Trakt.
        
        Args:
            list_id: ID of the Individual List to sync
            
        Returns:
            Status dict with:
            - success: bool
            - trakt_list_id: str (if successful)
            - items_added: int
            - items_removed: int
            - items_failed: int
            - errors: List[str]
            - message: str
        """
        logger.info(f"Starting Trakt sync for Individual List {list_id}")
        
        db = SessionLocal()
        try:
            # Get list
            individual_list = db.query(IndividualList).filter(
                IndividualList.id == list_id,
                IndividualList.user_id == self.user_id
            ).first()
            
            if not individual_list:
                return {
                    "success": False,
                    "message": f"List {list_id} not found or access denied",
                    "errors": ["List not found"]
                }
            
            # Get list items
            items = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == list_id
            ).order_by(IndividualListItem.order_index).all()
            
            if not items:
                return {
                    "success": False,
                    "message": "Cannot sync empty list to Trakt",
                    "errors": ["List is empty"]
                }
            
            # Ensure all items have trakt_ids
            items_mapped = self._ensure_trakt_ids(items, db)
            
            # Create or update Trakt list
            if individual_list.trakt_list_id:
                # Update existing list
                result = self._update_trakt_list(individual_list, items_mapped, db)
            else:
                # Create new list
                result = self._create_trakt_list(individual_list, items_mapped, db)
            
            # Update sync timestamp on success
            if result['success']:
                individual_list.trakt_synced_at = utc_now()
                if result.get('trakt_list_id'):
                    individual_list.trakt_list_id = result['trakt_list_id']
                db.commit()
            
            return result
            
        except Exception as e:
            logger.error(f"Trakt sync failed for list {list_id}: {e}")
            db.rollback()
            return {
                "success": False,
                "message": f"Sync failed: {str(e)}",
                "errors": [str(e)]
            }
        finally:
            db.close()
    
    def _ensure_trakt_ids(
        self,
        items: List[IndividualListItem],
        db
    ) -> List[IndividualListItem]:
            """
            Ensure all items have trakt_id by resolving from TMDB IDs with caching.
        
            Uses TraktIdResolver to look up Trakt IDs on-demand with Redis caching.
            Updates items in DB if trakt_id is found.
            """
            resolver = TraktIdResolver(user_id=self.user_id)
            updated_count = 0
        
            # Batch resolve Trakt IDs
            items_to_resolve = []
            for item in items:
                if not item.trakt_id and item.tmdb_id:
                    items_to_resolve.append({
                        'tmdb_id': item.tmdb_id,
                        'media_type': item.media_type,
                        'item': item
                    })
        
            if items_to_resolve:
                # Run async resolution in sync context with safe loop handling
                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        # Create a dedicated loop for this blocking operation
                        new_loop = asyncio.new_event_loop()
                        try:
                            resolved = new_loop.run_until_complete(resolver.resolve_items_batch(items_to_resolve))
                        finally:
                            new_loop.close()
                    else:
                        resolved = loop.run_until_complete(resolver.resolve_items_batch(items_to_resolve))
                except RuntimeError:
                    # No running loop
                    resolved = asyncio.run(resolver.resolve_items_batch(items_to_resolve))
            
                for item_data in resolved:
                    trakt_id = item_data.get('trakt_id')
                    if trakt_id:
                        item = item_data['item']
                        item.trakt_id = trakt_id
                        updated_count += 1
        
            if updated_count > 0:
                db.commit()
                logger.info(f"Resolved {updated_count} Trakt IDs for items")
        
            return items
    
    def _create_trakt_list(
        self,
        individual_list: IndividualList,
        items: List[IndividualListItem],
        db
    ) -> Dict[str, Any]:
        """
        Create a new Trakt list and add all items.
        
        Returns status dict.
        """
        logger.info(f"Creating new Trakt list for Individual List {individual_list.id}")
        
        try:
            # Create list on Trakt using asyncio.run(), instantiate client inside the coroutine
            async def _create():
                client = TraktClient(user_id=self.user_id)
                return await client.create_list(
                    name=individual_list.name,
                    description=individual_list.description or "Created from WatchBuddy Individual List",
                    privacy="public" if individual_list.is_public else "private"
                )
            
            # Check if we're already in an event loop
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, run in a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _create())
                    response = future.result()
            except RuntimeError:
                # No event loop running, safe to use asyncio.run
                response = asyncio.run(_create())
            
            if not response or 'ids' not in response:
                return {
                    "success": False,
                    "message": "Failed to create Trakt list",
                    "errors": ["Trakt API returned unexpected response"]
                }
            
            trakt_list_id = response['ids']['trakt']
            logger.info(f"Created Trakt list with ID: {trakt_list_id}")
            
            # Add items to list
            add_result = self._add_items_to_trakt_list(trakt_list_id, items)
            
            return {
                "success": True,
                "trakt_list_id": str(trakt_list_id),
                "items_added": add_result['added'],
                "items_failed": add_result['failed'],
                "items_removed": 0,
                "errors": add_result['errors'],
                "message": f"Created Trakt list and added {add_result['added']} items"
            }
            
        except Exception as e:
            logger.error(f"Failed to create Trakt list: {e}")
            return {
                "success": False,
                "message": f"Failed to create Trakt list: {str(e)}",
                "errors": [str(e)]
            }
    
    def _update_trakt_list(
        self,
        individual_list: IndividualList,
        items: List[IndividualListItem],
        db
    ) -> Dict[str, Any]:
        """
        Update existing Trakt list metadata and sync items.
        
        Returns status dict.
        """
        logger.info(f"Updating existing Trakt list {individual_list.trakt_list_id}")
        
        try:
            trakt_list_id = individual_list.trakt_list_id
            
            # Update list metadata using asyncio.run() with client created inside
            async def _update():
                client = TraktClient(user_id=self.user_id)
                return await client.update_list(
                    trakt_list_id=trakt_list_id,
                    name=individual_list.name,
                    description=individual_list.description or "Synced from WatchBuddy Individual List"
                )
            
            # Check if we're already in an event loop
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, run in a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _update())
                    future.result()
            except RuntimeError:
                # No event loop running, safe to use asyncio.run
                asyncio.run(_update())
            
            # Get current items
            trakt_items = self._get_trakt_list_items(trakt_list_id)
            trakt_ids_set = {(item['trakt_id'], item['media_type']) for item in trakt_items}
            
            # Determine items to add/remove
            current_ids_set = {
                (item.trakt_id, item.media_type) 
                for item in items 
                if item.trakt_id
            }
            
            to_add = [item for item in items if (item.trakt_id, item.media_type) not in trakt_ids_set and item.trakt_id]
            to_remove_ids = trakt_ids_set - current_ids_set
            
            # Add new items
            add_result = self._add_items_to_trakt_list(trakt_list_id, to_add) if to_add else {'added': 0, 'failed': 0, 'errors': []}
            
            # Remove deleted items
            remove_result = self._remove_items_from_trakt_list(trakt_list_id, to_remove_ids) if to_remove_ids else {'removed': 0, 'errors': []}
            
            return {
                "success": True,
                "trakt_list_id": str(trakt_list_id),
                "items_added": add_result['added'],
                "items_removed": remove_result['removed'],
                "items_failed": add_result['failed'],
                "errors": add_result['errors'] + remove_result['errors'],
                "message": f"Updated Trakt list: +{add_result['added']} items, -{remove_result['removed']} items"
            }
            
        except Exception as e:
            logger.error(f"Failed to update Trakt list: {e}")
            return {
                "success": False,
                "message": f"Failed to update Trakt list: {str(e)}",
                "errors": [str(e)]
            }
    
    def _get_trakt_list_items(self, trakt_list_id: str) -> List[Dict[str, Any]]:
        """Get all items currently in Trakt list."""
        try:
            # Use asyncio.run()
            async def _get_items():
                client = TraktClient(user_id=self.user_id)
                return await client.get_list_items(trakt_list_id)
            
            # Check if we're already in an event loop
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, run in a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _get_items())
                    response = future.result()
            except RuntimeError:
                # No event loop running, safe to use asyncio.run
                response = asyncio.run(_get_items())
            
            items = []
            if response and isinstance(response, list):
                for entry in response:
                    if entry.get('type') == 'movie' and entry.get('movie'):
                        items.append({
                            'trakt_id': entry['movie']['ids']['trakt'],
                            'media_type': 'movie'
                        })
                    elif entry.get('type') == 'show' and entry.get('show'):
                        items.append({
                            'trakt_id': entry['show']['ids']['trakt'],
                            'media_type': 'show'
                        })
            
            return items
            
        except Exception as e:
            logger.error(f"Failed to get Trakt list items: {e}")
            return []
    
    def _add_items_to_trakt_list(
        self,
        trakt_list_id: str,
        items: List[IndividualListItem]
    ) -> Dict[str, Any]:
        """Add items to Trakt list."""
        if not items:
            return {'added': 0, 'failed': 0, 'errors': []}
        
        # Build payload
        movies = []
        shows = []
        errors = []
        
        for item in items:
            if not item.trakt_id:
                errors.append(f"Missing trakt_id for {item.title}")
                continue
            
            entry = {"ids": {"trakt": item.trakt_id}}
            
            if item.media_type == 'movie':
                movies.append(entry)
            elif item.media_type == 'show':
                shows.append(entry)
        
        payload = {}
        if movies:
            payload['movies'] = movies
        if shows:
            payload['shows'] = shows
        
        if not payload:
            return {'added': 0, 'failed': len(items), 'errors': errors}
        
        try:
            # Prepare item dicts for async client
            item_dicts: List[Dict[str, Any]] = []
            for item in items:
                if item.trakt_id:
                    item_dicts.append({
                        'media_type': item.media_type,
                        'trakt_id': int(item.trakt_id)
                    })
            
            # Use asyncio.run()
            async def _add():
                client = TraktClient(user_id=self.user_id)
                return await client.add_items_to_list(trakt_list_id, item_dicts)
            
            # Check if we're already in an event loop
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, run in a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _add())
                    response = future.result()
            except RuntimeError:
                # No event loop running, safe to use asyncio.run
                response = asyncio.run(_add())

            if response and 'added' in response:
                # 'added' contains counts per type
                added_movies = response.get('added', {}).get('movies', 0) or 0
                added_shows = response.get('added', {}).get('shows', 0) or 0
                added_count = int(added_movies) + int(added_shows)

                # 'not_found' contains arrays of items per type – use lengths
                nf = response.get('not_found', {}) or {}
                nf_movies = nf.get('movies', []) or []
                nf_shows = nf.get('shows', []) or []
                # Defensive: if API shape differs, coerce to list length when possible
                try:
                    movies_missing = len(nf_movies)
                except TypeError:
                    movies_missing = int(nf_movies) if isinstance(nf_movies, (int, str)) else 0
                try:
                    shows_missing = len(nf_shows)
                except TypeError:
                    shows_missing = int(nf_shows) if isinstance(nf_shows, (int, str)) else 0
                failed_count = movies_missing + shows_missing
                return {
                    'added': added_count,
                    'failed': failed_count,
                    'errors': errors
                }
            return {'added': 0, 'failed': len(items), 'errors': errors + ["Unexpected API response"]}
        except Exception as e:
            logger.error(f"Failed to add items to Trakt list: {e}")
            errors.append(str(e))
            return {'added': 0, 'failed': len(items), 'errors': errors}
    
    def _remove_items_from_trakt_list(
        self,
        trakt_list_id: str,
        items_to_remove: set
    ) -> Dict[str, Any]:
        """Remove items from Trakt list."""
        if not items_to_remove:
            return {'removed': 0, 'errors': []}
        
        # Build payload
        movies = []
        shows = []
        
        for trakt_id, media_type in items_to_remove:
            entry = {"ids": {"trakt": trakt_id}}
            
            if media_type == 'movie':
                movies.append(entry)
            elif media_type == 'show':
                shows.append(entry)
        
        payload = {}
        if movies:
            payload['movies'] = movies
        if shows:
            payload['shows'] = shows
        
        try:
            # Prepare items for async client
            item_dicts: List[Dict[str, Any]] = []
            for trakt_id, media_type in items_to_remove:
                item_dicts.append({'media_type': media_type, 'trakt_id': int(trakt_id)})
            
            # Use asyncio.run()
            async def _remove():
                client = TraktClient(user_id=self.user_id)
                return await client.remove_items_from_list(trakt_list_id, item_dicts)
            
            # Check if we're already in an event loop
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, run in a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _remove())
                    response = future.result()
            except RuntimeError:
                # No event loop running, safe to use asyncio.run
                response = asyncio.run(_remove())
            
            if response and 'deleted' in response:
                removed_count = response['deleted'].get('movies', 0) + response['deleted'].get('shows', 0)
                return {'removed': removed_count, 'errors': []}
            return {'removed': 0, 'errors': ["Unexpected API response"]}
        except Exception as e:
            logger.error(f"Failed to remove items from Trakt list: {e}")
            return {'removed': 0, 'errors': [str(e)]}
