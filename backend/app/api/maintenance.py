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
async def rebuild_faiss_index(use_db_recovery: bool = True):
    """Trigger FAISS index rebuild from persistent candidates with embeddings.
    
    Rebuilds both:
    - MiniLM FAISS HNSW index (main semantic search) - always from DB
    - BGE multi-vector index (mood-aware secondary search)
    
    Args:
        use_db_recovery: If True, rebuild BGE index from persisted embeddings (fast, default).
                        If False, re-compute embeddings (slow but thorough).
    """
    try:
        from app.tasks_ai import rebuild_faiss_index
        from app.services.tasks import build_bge_index_topN
        
        # Queue the MiniLM FAISS rebuild (always from DB)
        task = rebuild_faiss_index.delay()
        
        # BGE rebuild: recovery from DB or full re-computation
        if use_db_recovery:
            # Fast path: rebuild from existing embeddings in bge_embeddings table
            from app.services.bge_recovery import rebuild_bge_index_from_db
            try:
                result = rebuild_bge_index_from_db()
                bge_msg = f"BGE index recovered from DB: {result.get('total_vectors', 0)} vectors"
            except Exception as e:
                logger.error(f"BGE recovery failed: {e}, falling back to full rebuild")
                build_bge_index_topN.delay(topN=50000)
                bge_msg = "BGE recovery failed, queued full rebuild"
        else:
            # Full rebuild: re-compute all embeddings
            build_bge_index_topN.delay(topN=50000)
            bge_msg = "BGE index queued for full rebuild (computing embeddings)"
        
        return MaintenanceResponse(
            status="queued",
            message=f"MiniLM FAISS rebuild queued. {bge_msg}. This may take several minutes.",
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


@router.post("/generate-user-profile", response_model=MaintenanceResponse)
async def generate_user_profile(user_id: int = 1, force: bool = False):
    """Trigger UserTextProfile generation using LLM analysis of watch history.
    
    Generates a 2-5 sentence narrative summary of user preferences that can be used
    in LLM prompts for phase labeling, module reranking, and predictions.
    
    Args:
        user_id: User ID to generate profile for (default: 1)
        force: If True, regenerate even if profile exists and is recent
    """
    try:
        from app.services.tasks import generate_user_text_profile
        from app.models import UserTextProfile
        from app.core.database import SessionLocal
        from app.utils.timezone import utc_now
        
        # Check if profile exists and is recent (unless force=True)
        if not force:
            db = SessionLocal()
            try:
                existing = db.query(UserTextProfile).filter_by(user_id=user_id).first()
                if existing:
                    age_days = (utc_now() - existing.updated_at).days
                    if age_days < 7:
                        return MaintenanceResponse(
                            status="skipped",
                            message=f"User profile already exists and is {age_days} days old (< 7 days). Use force=true to regenerate.",
                            task_id=None
                        )
            finally:
                db.close()
        
        # Queue the task
        task = generate_user_text_profile.delay(user_id=user_id)
        
        return MaintenanceResponse(
            status="queued",
            message=f"User profile generation queued for user {user_id}. This may take 10-30 seconds.",
            task_id=task.id
        )
    except Exception as e:
        logger.exception(f"Failed to queue user profile generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user-profile-status")
async def get_user_profile_status(user_id: int = 1):
    """Get UserTextProfile status for a user."""
    try:
        from app.models import UserTextProfile
        from app.core.database import SessionLocal
        from app.utils.timezone import utc_now
        
        db = SessionLocal()
        try:
            profile = db.query(UserTextProfile).filter_by(user_id=user_id).first()
            
            if not profile:
                return {
                    "exists": False,
                    "status": "not_generated"
                }
            
            age_days = (utc_now() - profile.updated_at).days
            
            return {
                "exists": True,
                "status": "ready",
                "summary_length": len(profile.summary_text),
                "tags_count": len(profile.tags_json) if profile.tags_json else 0,
                "age_days": age_days,
                "created_at": profile.created_at.isoformat(),
                "updated_at": profile.updated_at.isoformat()
            }
        finally:
            db.close()
    except Exception as e:
        logger.exception(f"Failed to get user profile status: {e}")
        return {
            "exists": False,
            "status": "error",
            "error": str(e)
        }
