from fastapi import APIRouter, HTTPException, Body
from typing import Optional, List, Dict, Any
from ..core.database import SessionLocal
from ..models import UserList, ListItem
from ..services.trakt_client import TraktClient
import json
import logging

router = APIRouter()

@router.post("/chat/create")
async def create_chat_list(
    title: str = Body(...),
    items: List[Dict[str, Any]] = Body(...),
    user_id: Optional[int] = Body(1, description="User ID to use for Trakt (default 1)")
) -> Dict[str, Any]:
    """
    Create a chat-based ad-hoc list with specified items. Items must have trakt_id and media_type.
    Syncs list to Trakt as well.
    """
    db = SessionLocal()
    try:
        # Create UserList of type 'chat'
        user_list = UserList(
            user_id=user_id,
            title=title,
            filters=json.dumps({"source": "chat"}),
            item_limit=len(items),
            list_type="chat",
            sync_status="queued"
        )
        db.add(user_list)
        db.commit()
        db.refresh(user_list)

        # Add items to ListItem
        for item in items:
            if not item.get("trakt_id") or not item.get("media_type"):
                continue
            list_item = ListItem(
                smartlist_id=user_list.id,
                trakt_id=item["trakt_id"],
                media_type=item["media_type"],
                title=item.get("title"),
                score=item.get("score", 1.0),
                explanation=item.get("explanation", "Added via chat"),
                is_watched=False
            )
            db.add(list_item)
        db.commit()

        # Create Trakt list and add items
        trakt_client = TraktClient(user_id=user_id)
        trakt_list = await trakt_client.create_list(
            name=title,
            description="Chat-based ad-hoc list created by WatchBuddy",
            privacy="private"
        )
        trakt_list_id = trakt_list.get("ids", {}).get("trakt")
        if trakt_list_id:
            user_list.trakt_list_id = str(trakt_list_id)
            db.commit()
            # Add items to Trakt list
            await trakt_client.add_items_to_list(trakt_list_id, [
                {"trakt_id": item["trakt_id"], "media_type": item["media_type"]}
                for item in items if item.get("trakt_id") and item.get("media_type")
            ])
        return {
            "id": user_list.id,
            "title": user_list.title,
            "status": "created",
            "trakt_list_id": user_list.trakt_list_id,
            "item_count": len(items)
        }
    except Exception as e:
        db.rollback()
        logging.error(f"Failed to create chat list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create chat list: {str(e)}")
    finally:
        db.close()
