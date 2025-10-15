from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List, Dict, Any

from app.services.fusion import FusionEngine
from app.services.trakt_client import TraktClient

router = APIRouter()

@router.get("/fusion")
async def fusion_recommendations(
    user_id: Optional[int] = Query(1, description="User ID to use for Trakt (default 1)"),
    media_type: str = Query("movies"),
    limit: int = Query(30, ge=1, le=100)
) -> List[Dict[str, Any]]:
    """
    Return fusion-mode recommendations combining core components, trending, and history affinity.
    """
    try:
        # Check if fusion is enabled for this user
        engine = FusionEngine(user_id=user_id)
        settings = await engine._load_user_settings()
        
        if not settings["enabled"]:
            raise HTTPException(status_code=400, detail="Fusion mode is disabled for this user")
        
        # Fetch candidate pool from Trakt recommendations as a base (could be widened by search/trending)
        trakt = TraktClient(user_id=user_id)
        base = await trakt.get_recommendations(media_type="movies" if media_type=="movies" else "shows", limit=200)
        # Flatten Trakt response into simple candidate dicts
        candidates = []
        for item in base or []:
            entity = item.get("movie") or item.get("show") or item
            if not isinstance(entity, dict):
                continue
            entity["type"] = "movie" if item.get("movie") else ("show" if item.get("show") else entity.get("type"))
            candidates.append(entity)

        fused = await engine.fuse(user={"id": user_id or 0}, candidates=candidates, list_type="smartlist", media_type=media_type, limit=limit)
        return fused
    except RuntimeError as e:
        # Surface Trakt token errors clearly
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fusion recommendations failed: {str(e)}")

@router.get("/fusion/status")
async def fusion_status(user_id: Optional[int] = Query(1, description="User ID to use for Trakt (default 1)")) -> Dict[str, Any]:
    """
    Get fusion mode status and current weights for a user.
    """
    try:
        engine = FusionEngine(user_id=user_id)
        settings = await engine._load_user_settings()
        return {
            "enabled": settings["enabled"],
            "weights": settings["weights"],
            "available_components": [
                "components.genre",
                "components.semantic", 
                "components.mood",
                "components.rating",
                "components.novelty",
                "trending",
                "history"
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get fusion status: {str(e)}")
