"""
suggested.py

API endpoints for personalized list suggestions.
"""

from fastapi import APIRouter, HTTPException, Body, Query
from typing import Optional, List
from app.services.suggested_lists import SuggestedListsService
import logging

def extract_error_message(e: Exception) -> str:
    import traceback, json
    if hasattr(e, 'detail') and e.detail:
        detail = e.detail
        if isinstance(detail, (dict, list)):
            try:
                return json.dumps(detail)
            except Exception:
                return str(detail)
        return str(detail)
    elif hasattr(e, 'args') and e.args:
        arg = e.args[0]
        if isinstance(arg, (dict, list)):
            try:
                return json.dumps(arg)
            except Exception:
                return str(arg)
        return str(arg)
    else:
        return f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/")
async def get_suggested_lists(
    user_id: Optional[int] = Query(1, description="User ID to use for Trakt (default 1)"),
    limit: int = Query(12, ge=1, le=50)
):
    """Get personalized list suggestions for a user."""
    try:
        service = SuggestedListsService(user_id)
        suggestions = await service.generate_suggestions(limit=limit)
        
        return {
            "status": "success",
            "total": len(suggestions),
            "suggestions": suggestions
        }
        
    except Exception as e:
        msg = extract_error_message(e)
        logger.error(f"Error getting suggested lists: {msg}")
        raise HTTPException(status_code=500, detail=f"Failed to get suggestions: {msg}")

@router.post("/create")
async def create_suggested_list(
    suggestion: dict = Body(...),
    user_id: Optional[int] = Body(1, description="User ID to use for Trakt (default 1)")
):
    """Create a UserList from a suggestion."""
    try:
        logger.info(f"Creating suggested list with payload: {suggestion}")
        service = SuggestedListsService(user_id)
        result = await service.create_suggested_list(suggestion)
        logger.info(f"Successfully created suggested list: {result}")
        
        return {
            "status": "success",
            "message": f"Created list '{result['title']}'",
            "list": result
        }
        
    except Exception as e:
        msg = extract_error_message(e)
        logger.error(f"Error creating suggested list: {msg}")
        raise HTTPException(status_code=500, detail=f"Failed to create list: {msg}")

@router.get("/fallback")
async def get_fallback_suggestions():
    """Get fallback suggestions when user analysis isn't possible."""
    try:
        service = SuggestedListsService()
        suggestions = service._get_fallback_suggestions()
        
        return {
            "status": "success",
            "total": len(suggestions),
            "suggestions": suggestions
        }
        
    except Exception as e:
        msg = extract_error_message(e)
        logger.error(f"Error getting fallback suggestions: {msg}")
        raise HTTPException(status_code=500, detail=f"Failed to get fallback suggestions: {msg}")