#!/usr/bin/env python3
"""
Update existing chat lists with enhanced parsing.
Re-parses filters and regenerates titles for all chat-type lists.
"""

import sys
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.models import UserList
from app.api.chat_prompt import parse_chat_prompt, generate_dynamic_title
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def update_chat_lists(dry_run=False):
    """Update all chat lists with enhanced parsing."""
    db = SessionLocal()
    try:
        # Get all chat lists
        chat_lists = db.query(UserList).filter(UserList.list_type == "chat").all()
        logger.info(f"Found {len(chat_lists)} chat lists to update")
        
        for ulist in chat_lists:
            logger.info(f"\n{'='*60}")
            logger.info(f"List ID {ulist.id}: {ulist.title}")
            
            # Parse current filters
            old_filters = json.loads(ulist.filters) if ulist.filters else {}
            logger.info(f"  Old filters: {old_filters}")
            
            # Re-parse from the original prompt (stored in search_query or title)
            original_prompt = old_filters.get('search_query') or ulist.title
            new_filters = parse_chat_prompt(original_prompt)
            logger.info(f"  New filters: {new_filters}")
            
            # Generate dynamic title
            new_title = generate_dynamic_title(new_filters, original_prompt)
            logger.info(f"  Old title: {ulist.title}")
            logger.info(f"  New title: {new_title}")
            
            # Show what changed
            changes = []
            if old_filters.get('genres') != new_filters.get('genres'):
                changes.append(f"genres: {old_filters.get('genres')} → {new_filters.get('genres')}")
            if old_filters.get('mood') != new_filters.get('mood'):
                changes.append(f"mood: {old_filters.get('mood')} → {new_filters.get('mood')}")
            if old_filters.get('media_types') != new_filters.get('media_types'):
                changes.append(f"media_types: {old_filters.get('media_types')} → {new_filters.get('media_types')}")
            if old_filters.get('similar_to_title') != new_filters.get('similar_to_title'):
                changes.append(f"anchor: {old_filters.get('similar_to_title')} → {new_filters.get('similar_to_title')}")
            
            if changes:
                logger.info(f"  Changes detected:")
                for change in changes:
                    logger.info(f"    - {change}")
            else:
                logger.info(f"  No changes needed")
            
            # Apply updates
            if not dry_run:
                ulist.filters = json.dumps(new_filters)
                ulist.title = new_title
                db.commit()
                logger.info(f"  ✓ Updated")
            else:
                logger.info(f"  [DRY RUN] Would update")
        
        if not dry_run:
            logger.info(f"\n{'='*60}")
            logger.info(f"✓ Successfully updated {len(chat_lists)} chat lists")
            logger.info(f"Next step: Resync lists to apply new filters")
            logger.info(f"  Run: docker exec -i watchbuddy-backend-1 python /app/tests/manual/trigger_sync.py <list_id>")
        else:
            logger.info(f"\n{'='*60}")
            logger.info(f"DRY RUN complete. Run with dry_run=False to apply changes.")
            
    except Exception as e:
        logger.error(f"Failed to update chat lists: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    # Default to dry run for safety
    dry_run = "--apply" not in sys.argv
    
    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE - No changes will be made")
        print("To apply changes, run with: --apply")
        print("=" * 60)
    
    update_chat_lists(dry_run=dry_run)
