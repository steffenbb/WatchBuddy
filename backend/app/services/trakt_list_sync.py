"""
trakt_list_sync.py

Service for automatically syncing WatchBuddy lists to Trakt.

Sync Rules:
- Custom/Manual lists (UserList): Auto-sync on creation/sync, delete on UI delete
- AI lists mood/theme/fusion: Auto-sync on creation, DELETE+RECREATE on rotation, delete on UI delete
- Chat lists: Auto-sync on creation/sync, delete on UI delete
- Individual lists: Manual sync only (via UI button)
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from app.services.trakt_client import TraktClient, TraktAuthError
from app.services.trakt_id_resolver import TraktIdResolver
from app.models import UserList, ListItem, IndividualList, IndividualListItem, PersistentCandidate
from app.models_ai import AiList, AiListItem

logger = logging.getLogger(__name__)


async def sync_user_list_to_trakt(user_list: UserList, db: Session, user_id: int = 1, 
                                   force_recreate: bool = False) -> Optional[str]:
    """
    Sync a UserList (custom/manual SmartList) to Trakt.
    
    Args:
        user_list: The UserList object to sync
        db: Database session
        user_id: User ID for Trakt client
        force_recreate: If True, delete existing Trakt list and create new one
        
    Returns:
        Trakt list ID if successful, None otherwise
    """
    try:
        trakt = TraktClient(user_id=user_id)
        
        # If force_recreate and list exists on Trakt, delete it first
        if force_recreate and user_list.trakt_list_id:
            try:
                await trakt.delete_list(user_list.trakt_list_id)
                logger.info(f"Deleted existing Trakt list {user_list.trakt_list_id} for UserList {user_list.id}")
                user_list.trakt_list_id = None
            except Exception as e:
                logger.warning(f"Failed to delete Trakt list {user_list.trakt_list_id}: {e}")
        
        # Create or update Trakt list
        if not user_list.trakt_list_id:
            # Create new list
            list_data = await trakt.create_list(
                name=user_list.title,
                description=f"WatchBuddy SmartList: {user_list.list_type}",
                privacy="private"
            )
            user_list.trakt_list_id = str(list_data.get("ids", {}).get("trakt"))
            logger.info(f"Created Trakt list {user_list.trakt_list_id} for UserList {user_list.id}")
        else:
            # Update existing list
            await trakt.update_list(user_list.trakt_list_id, name=user_list.title)
            logger.info(f"Updated Trakt list {user_list.trakt_list_id} for UserList {user_list.id}")
        
        # Get list items
        list_items = db.query(ListItem).filter_by(user_list_id=user_list.id).all()
        
        # Resolve Trakt IDs for items using TMDB IDs directly
        resolver = TraktIdResolver(user_id=user_id)
        items_to_resolve = []
        for item in list_items:
            if getattr(item, 'tmdb_id', None) and getattr(item, 'media_type', None):
                items_to_resolve.append({
                    'tmdb_id': item.tmdb_id,
                    'media_type': item.media_type,
                    'list_item': item
                })
        
        # Batch resolve Trakt IDs
        items_with_trakt = await resolver.resolve_items_batch(items_to_resolve)
        
        # Prepare Trakt items with correct format for add_items_to_list
        items_payload = []
        for item_data in items_with_trakt:
            trakt_id = item_data.get('trakt_id')
            media_type = item_data.get('media_type')
            if trakt_id and media_type:
                # Format expected by TraktClient.add_items_to_list
                items_payload.append({
                    "trakt_id": trakt_id,
                    "media_type": media_type
                })
        
        # Add items to Trakt list
        if items_payload and user_list.trakt_list_id:
            await trakt.add_items_to_list(user_list.trakt_list_id, items_payload)
            logger.info(f"Added {len(items_payload)} items to Trakt list {user_list.trakt_list_id}")
        
        # Update sync timestamp
        user_list.last_sync_at = datetime.utcnow()
        db.commit()
        
        return user_list.trakt_list_id
        
    except TraktAuthError:
        logger.warning(f"User {user_id} not authenticated with Trakt, skipping sync")
        return None
    except Exception as e:
        logger.error(f"Failed to sync UserList {user_list.id} to Trakt: {e}")
        return None


async def sync_ai_list_to_trakt(ai_list: AiList, db: Session, user_id: int = 1,
                                 force_recreate: bool = False) -> Optional[str]:
    """
    Sync an AI list to Trakt.
    
    For mood/theme/fusion lists with force_recreate=True, will delete old list and create new.
    This is used when lists are rotated to ensure clean slate.
    
    Args:
        ai_list: The AiList object to sync
        db: Database session
        user_id: User ID for Trakt client
        force_recreate: If True, delete existing Trakt list and create new one
        
    Returns:
        Trakt list ID if successful, None otherwise
    """
    try:
        trakt = TraktClient(user_id=user_id)
        
        # For mood/theme/fusion lists that are being rotated, delete old list first
        if force_recreate and ai_list.trakt_list_id:
            try:
                await trakt.delete_list(ai_list.trakt_list_id)
                logger.info(f"Deleted old Trakt list {ai_list.trakt_list_id} for AI list {ai_list.id} (rotation)")
                ai_list.trakt_list_id = None
            except Exception as e:
                logger.warning(f"Failed to delete Trakt list {ai_list.trakt_list_id}: {e}")
        
        # Create or update Trakt list
        if not ai_list.trakt_list_id:
            # Create new list
            list_name = ai_list.generated_title or ai_list.prompt_text[:50] or "AI Recommendations"
            list_data = await trakt.create_list(
                name=list_name,
                description=f"WatchBuddy AI List ({ai_list.type})",
                privacy="private"
            )
            ai_list.trakt_list_id = str(list_data.get("ids", {}).get("trakt"))
            logger.info(f"Created Trakt list {ai_list.trakt_list_id} for AI list {ai_list.id}")
        else:
            # Update existing list name
            list_name = ai_list.generated_title or ai_list.prompt_text[:50] or "AI Recommendations"
            await trakt.update_list(ai_list.trakt_list_id, name=list_name)
            logger.info(f"Updated Trakt list {ai_list.trakt_list_id} for AI list {ai_list.id}")
        
        # Get list items
        list_items = db.query(AiListItem).filter_by(ai_list_id=ai_list.id).all()
        
        # Resolve Trakt IDs for AI list items.
        # AiListItem lacks media_type; infer via PersistentCandidate using tmdb_id.
        resolver = TraktIdResolver(user_id=user_id)
        items_to_resolve = []
        for item in list_items:
            if getattr(item, 'tmdb_id', None):
                # Prefer movie first, then show as fallback
                pc_movie = db.query(PersistentCandidate).filter_by(tmdb_id=item.tmdb_id, media_type='movie').first()
                if pc_movie:
                    items_to_resolve.append({'tmdb_id': item.tmdb_id, 'media_type': 'movie', 'list_item': item})
                    continue
                pc_show = db.query(PersistentCandidate).filter_by(tmdb_id=item.tmdb_id, media_type='show').first()
                if pc_show:
                    items_to_resolve.append({'tmdb_id': item.tmdb_id, 'media_type': 'show', 'list_item': item})
        
        # Batch resolve Trakt IDs
        items_with_trakt = await resolver.resolve_items_batch(items_to_resolve)
        
        # Prepare Trakt items with correct format for add_items_to_list
        items_payload = []
        for item_data in items_with_trakt:
            trakt_id = item_data.get('trakt_id')
            media_type = item_data.get('media_type')
            if trakt_id and media_type:
                # Format expected by TraktClient.add_items_to_list
                items_payload.append({
                    "trakt_id": trakt_id,
                    "media_type": media_type
                })
        
        # Add items to Trakt list
        if items_payload and ai_list.trakt_list_id:
            await trakt.add_items_to_list(ai_list.trakt_list_id, items_payload)
            logger.info(f"Added {len(items_payload)} items to Trakt list {ai_list.trakt_list_id}")
        
        # Update sync timestamp
        ai_list.last_synced_at = datetime.utcnow()
        db.commit()
        
        return ai_list.trakt_list_id
        
    except TraktAuthError:
        logger.warning(f"User {user_id} not authenticated with Trakt, skipping sync")
        return None
    except Exception as e:
        logger.error(f"Failed to sync AI list {ai_list.id} to Trakt: {e}")
        return None


async def delete_trakt_list_for_user_list(user_list: UserList, user_id: int = 1):
    """Delete a UserList's corresponding Trakt list."""
    if not user_list.trakt_list_id:
        return True
    
    try:
        trakt = TraktClient(user_id=user_id)
        await trakt.delete_list(user_list.trakt_list_id)
        logger.info(f"Deleted Trakt list {user_list.trakt_list_id} for UserList {user_list.id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete Trakt list {user_list.trakt_list_id}: {e}")
        return False


async def delete_trakt_list_for_ai_list(ai_list: AiList, user_id: int = 1):
    """Delete an AI list's corresponding Trakt list."""
    if not ai_list.trakt_list_id:
        return True
    
    try:
        trakt = TraktClient(user_id=user_id)
        await trakt.delete_list(ai_list.trakt_list_id)
        logger.info(f"Deleted Trakt list {ai_list.trakt_list_id} for AI list {ai_list.id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete Trakt list {ai_list.trakt_list_id}: {e}")
        return False


async def delete_trakt_list_for_individual_list(individual_list: IndividualList, user_id: int = 1):
    """Delete an Individual list's corresponding Trakt list."""
    if not individual_list.trakt_list_id:
        return True
    
    try:
        trakt = TraktClient(user_id=user_id)
        await trakt.delete_list(individual_list.trakt_list_id)
        logger.info(f"Deleted Trakt list {individual_list.trakt_list_id} for Individual list {individual_list.id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete Trakt list {individual_list.trakt_list_id}: {e}")
        return False


async def sync_individual_list_to_trakt(individual_list: IndividualList, db: Session, user_id: int = 1) -> Optional[str]:
    """
    Sync an Individual list to Trakt (manual sync only, triggered by user).
    
    Args:
        individual_list: The IndividualList object to sync
        db: Database session
        user_id: User ID for Trakt client
        
    Returns:
        Trakt list ID if successful, None otherwise
    """
    try:
        trakt = TraktClient(user_id=user_id)
        
        # Create or update Trakt list
        if not individual_list.trakt_list_id:
            # Create new list
            list_data = await trakt.create_list(
                name=individual_list.name,
                description=individual_list.description or "WatchBuddy Individual List",
                privacy="private" if not individual_list.is_public else "public"
            )
            individual_list.trakt_list_id = str(list_data.get("ids", {}).get("trakt"))
            logger.info(f"Created Trakt list {individual_list.trakt_list_id} for Individual list {individual_list.id}")
        else:
            # Update existing list
            await trakt.update_list(
                individual_list.trakt_list_id,
                name=individual_list.name,
                description=individual_list.description or "WatchBuddy Individual List"
            )
            logger.info(f"Updated Trakt list {individual_list.trakt_list_id} for Individual list {individual_list.id}")
        
        # Get list items
        list_items = db.query(IndividualListItem).filter_by(list_id=individual_list.id).all()
        
        # Resolve Trakt IDs for items missing them
        resolver = TraktIdResolver(user_id=user_id)
        items_to_resolve = []
        items_with_trakt = []
        
        for item in list_items:
            if item.trakt_id:
                # Already has Trakt ID
                items_with_trakt.append({
                    'trakt_id': item.trakt_id,
                    'media_type': item.media_type,
                    'list_item': item
                })
            elif item.tmdb_id:
                # Need to resolve Trakt ID from TMDB ID
                items_to_resolve.append({
                    'tmdb_id': item.tmdb_id,
                    'media_type': item.media_type,
                    'list_item': item
                })
        
        # Batch resolve missing Trakt IDs
        if items_to_resolve:
            resolved = await resolver.resolve_items_batch(items_to_resolve)
            items_with_trakt.extend(resolved)
        
        # Prepare Trakt items with correct format for add_items_to_list
        items_payload = []
        for item_data in items_with_trakt:
            trakt_id = item_data.get('trakt_id')
            media_type = item_data.get('media_type')
            if trakt_id and media_type:
                # Format expected by TraktClient.add_items_to_list
                items_payload.append({
                    "trakt_id": trakt_id,
                    "media_type": media_type
                })
        
        # Add items to Trakt list
        if items_payload and individual_list.trakt_list_id:
            await trakt.add_items_to_list(individual_list.trakt_list_id, items_payload)
            logger.info(f"Added {len(items_payload)} items to Trakt list {individual_list.trakt_list_id}")
        
        # Update sync timestamp
        individual_list.trakt_synced_at = datetime.utcnow()
        db.commit()
        
        return individual_list.trakt_list_id
        
    except TraktAuthError:
        logger.warning(f"User {user_id} not authenticated with Trakt, skipping sync")
        return None
    except Exception as e:
        logger.error(f"Failed to sync Individual list {individual_list.id} to Trakt: {e}")
        return None
