def extract_error_message(e: Exception) -> str:
    import traceback
    if hasattr(e, 'detail') and e.detail:
        return str(e.detail)
    elif hasattr(e, 'args') and e.args:
        return str(e.args[0])
    else:
        return f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"

"""
notifications.py

API endpoints for notifications with SSE streaming and persistent logging.
"""
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import asyncio
import logging
from datetime import datetime
from app.core.redis_client import get_redis
from app.utils.timezone import utc_now, get_user_local_time

async def get_user_timezone(user_id: int) -> str:
    """Get user's configured timezone from Redis, defaulting to UTC."""
    try:
        redis = get_redis()
        timezone_data = await redis.get(f"settings:timezone:{user_id}")
        if timezone_data:
            data = json.loads(timezone_data)
            return data.get("timezone", "UTC")
    except Exception:
        pass
    return "UTC"

router = APIRouter()
logger = logging.getLogger(__name__)

class NotificationCreate(BaseModel):
    message: str
    type: str = "info"  # info, success, warning, error
    link: Optional[str] = None
    source: Optional[str] = None

class MarkReadRequest(BaseModel):
    user_id: int = 1

class NotificationResponse(BaseModel):
    id: int
    user_id: int
    message: str
    type: str
    link: Optional[str]
    source: Optional[str]
    read: bool
    created_at: datetime
    created_at_utc: Optional[datetime] = None  # For backward compatibility

class Notification:
    """In-memory notification model for Redis storage."""
    def __init__(self, user_id: int, message: str, notification_type: str = "info", 
                 link: str = None, source: str = None, user_timezone: str = "UTC"):
        self.id = int(utc_now().timestamp() * 1000)  # Use timestamp as ID
        self.user_id = user_id
        self.message = message
        self.type = notification_type
        self.link = link
        self.source = source
        self.read = False
        # Store timestamp in user's timezone for display purposes
        self.created_at = get_user_local_time(user_timezone)
        self.created_at_utc = utc_now()  # Keep UTC for sorting/processing
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "message": self.message,
            "type": self.type,
            "link": self.link,
            "source": self.source,
            "read": self.read,
            "created_at": self.created_at.isoformat(),
            "created_at_utc": self.created_at_utc.isoformat()
        }


@router.get("/", response_model=List[NotificationResponse])
async def get_notifications(user_id: int = 1, limit: int = 50, offset: int = 0):
    """Get paginated notifications for user."""
    redis = get_redis()
    key = f"notifications:{user_id}"
    
    # Get notifications from Redis (stored as JSON)
    notifications_data = await redis.lrange(key, offset, offset + limit - 1)
    notifications = []
    
    for data in notifications_data:
        try:
            notification_dict = json.loads(data)
            notifications.append(NotificationResponse(**notification_dict))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse notification: {e}")
    
    return notifications

@router.post("/{notification_id}/read")
async def mark_notification_read(notification_id: int, payload: MarkReadRequest):
    """Mark notification as read."""
    redis = get_redis()
    key = f"notifications:{payload.user_id}"
    
    # Get all notifications
    notifications_data = await redis.lrange(key, 0, -1)
    updated = False
    
    for i, data in enumerate(notifications_data):
        try:
            notification_dict = json.loads(data)
            if notification_dict["id"] == notification_id:
                notification_dict["read"] = True
                # Update in Redis
                await redis.lset(key, i, json.dumps(notification_dict))
                updated = True
                break
        except (json.JSONDecodeError, ValueError):
            continue
    
    if not updated:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True}

@router.get("/stream")
async def stream_notifications(user_id: int):
    """Server-Sent Events endpoint for real-time notifications."""
    
    async def event_stream():
        redis = get_redis()
        pubsub = redis.pubsub()
        channel = f"notifications:{user_id}"
        try:
            await pubsub.subscribe(channel)
            yield f"data: {json.dumps({'type': 'connected', 'message': 'Notification stream connected'})}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(pubsub.get_message(), timeout=30.0)
                    if message and message["type"] == "message":
                        data = message['data']
                        # If data is bytes, decode; if str, use as is
                        if isinstance(data, bytes):
                            data = data.decode()
                        yield f"data: {data}\n\n"
                    elif message is None:
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            logger.info(f"Notification stream cancelled for user {user_id}")
        except Exception as e:
            logger.error(f"Notification stream error for user {user_id}: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Stream error'})}\n\n"
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception as e:
                logger.warning(f"Error closing pubsub for user {user_id}: {e}")
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )

@router.post("/send")
async def send_notification_api(user_id: int, notification: NotificationCreate):
    """Send notification to user (for testing or admin use)."""
    await send_notification(user_id, notification.message, notification.type, 
                          notification.link, notification.source)
    return {"success": True, "message": "Notification sent"}

async def send_notification(user_id: int, message: str, notification_type: str = "info",
                          link: str = None, source: str = None):
    """Send notification to user and store persistently."""
    redis = get_redis()
    
    # Get user's timezone for proper timestamp display
    user_timezone = await get_user_timezone(user_id)
    
    # Create notification with timezone-aware timestamp
    notification = Notification(user_id, message, notification_type, link, source, user_timezone)
    notification_json = json.dumps(notification.to_dict())
    
    # Store in Redis list (persistent log)
    key = f"notifications:{user_id}"
    await redis.lpush(key, notification_json)
    # Keep only last 1000 notifications per user
    await redis.ltrim(key, 0, 999)
    await redis.expire(key, 86400 * 30)  # 30 days retention
    
    # Publish to real-time stream
    stream_key = f"notifications:{user_id}"
    await redis.publish(stream_key, notification_json)
    
    logger.info(f"Sent notification to user {user_id}: {message}")

@router.delete("/clear")
async def clear_notifications(user_id: int):
    """Clear all notifications for user."""
    redis = get_redis()
    key = f"notifications:{user_id}"
    await redis.delete(key)
    return {"success": True, "message": "Notifications cleared"}