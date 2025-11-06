"""
maintenance.py

API endpoints for system maintenance tasks.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class MaintenanceResponse(BaseModel):
    status: str
    message: str
    task_id: str | None = None


@router.post("/rebuild-faiss", response_model=MaintenanceResponse)
async def rebuild_faiss_index():
    """Trigger FAISS index rebuild from persistent candidates with embeddings."""
    try:
        from app.tasks_ai import rebuild_faiss_index
        
        # Queue the task
        task = rebuild_faiss_index.delay()
        
        return MaintenanceResponse(
            status="queued",
            message="FAISS index rebuild has been queued. This may take several minutes.",
            task_id=task.id
        )
    except Exception as e:
        logger.exception(f"Failed to queue FAISS rebuild: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rebuild-elasticsearch", response_model=MaintenanceResponse)
async def rebuild_elasticsearch_index():
    """Trigger Elasticsearch index rebuild from persistent candidates with embeddings."""
    try:
        from app.services.tasks import rebuild_elasticsearch_task
        
        # Queue the task
        task = rebuild_elasticsearch_task.delay()
        
        return MaintenanceResponse(
            status="queued",
            message="Elasticsearch index rebuild has been queued. This may take several minutes.",
            task_id=task.id
        )
    except Exception as e:
        logger.exception(f"Failed to queue Elasticsearch rebuild: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/faiss-status")
async def get_faiss_status():
    """Get FAISS index status."""
    try:
        from app.services.ai_engine.faiss_index import load_index
        
        try:
            idx, mapping = load_index()
            total_vectors = idx.ntotal if idx else 0
            
            return {
                "loaded": idx is not None,
                "total_vectors": total_vectors,
                "status": "ready" if idx else "not_built"
            }
        except FileNotFoundError:
            return {
                "loaded": False,
                "total_vectors": 0,
                "status": "not_built"
            }
    except Exception as e:
        logger.exception(f"Failed to get FAISS status: {e}")
        return {
            "loaded": False,
            "total_vectors": 0,
            "status": "error",
            "error": str(e)
        }


@router.get("/elasticsearch-status")
async def get_elasticsearch_status():
    """Get Elasticsearch index status."""
    try:
        from app.services.elasticsearch_client import get_elasticsearch_client
        
        es = get_elasticsearch_client()
        if not es or not es.es:
            return {
                "available": False,
                "total_documents": 0,
                "status": "not_configured"
            }
        
        # Get index stats
        stats = es.get_index_stats()
        
        if "error" in stats:
            return {
                "available": False,
                "total_documents": 0,
                "status": "error",
                "error": stats["error"]
            }
        
        doc_count = stats.get("doc_count", 0)
        
        if doc_count == 0:
            return {
                "available": True,
                "total_documents": 0,
                "status": "not_built"
            }
        
        return {
            "available": True,
            "total_documents": doc_count,
            "status": "ready",
            "size_bytes": stats.get("size_bytes")
        }
    except Exception as e:
        logger.exception(f"Failed to get Elasticsearch status: {e}")
        return {
            "available": False,
            "total_documents": 0,
            "status": "error",
            "error": str(e)
        }
