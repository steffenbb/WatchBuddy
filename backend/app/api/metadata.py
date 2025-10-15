"""
metadata.py

API endpoints for metadata building and status checking.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging

from app.core.database import get_db
from app.core.celery_app import celery_app
from app.services.metadata_builder import MetadataBuilder

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/status")
async def get_metadata_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get metadata build status and readiness.
    
    Returns:
        status: Build status (not_started, running, complete, error)
        ready: Whether metadata is ready for use
        build_progress: Current build progress if running
    """
    builder = MetadataBuilder()
    
    # Get build status from Redis
    build_status = await builder.get_build_status()
    
    # Check if metadata is ready
    is_ready = await builder.check_metadata_ready(db)
    
    return {
        "ready": is_ready,
        "build_status": build_status
    }

@router.post("/build/start")
async def start_metadata_build(
    force: bool = False,
    user_id: int = 1,
    db: Session = Depends(get_db)
) -> Dict[str, str]:
    """
    Start metadata building process using Celery task.
    
    Args:
        force: Force rebuild even if already complete
        user_id: User ID for Trakt authentication
        
    Returns:
        message: Status message
    """
    builder = MetadataBuilder()
    
    # Check current status
    current_status = await builder.get_build_status()
    
    if current_status["status"] == "running" and not force:
        return {
            "message": "Metadata build already in progress",
            "progress_percent": current_status.get("progress_percent", 0)
        }
    
    # Start build via Celery using the configured app (Redis broker)
    celery_app.send_task(
        "app.services.tasks.build_metadata",
        kwargs={"user_id": user_id, "force": force}
    )
    
    return {
        "message": "Metadata build started",
        "status": "running"
    }

@router.get("/build/status")
async def get_build_status() -> Dict[str, Any]:
    """
    Get current metadata build progress.
    
    Returns detailed progress information including:
    - status: Current status (not_started, running, complete, error)
    - total: Total items to process
    - processed: Items processed so far
    - progress_percent: Percentage complete
    - errors: Number of errors encountered
    """
    builder = MetadataBuilder()
    return await builder.get_build_status()
