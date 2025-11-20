"""
Pairwise preference training endpoints.

Provides API for user feedback collection through pairwise comparisons.
"""
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.pairwise_trainer import PairwiseTrainer
from app.core import metrics as metrics

logger = logging.getLogger(__name__)
router = APIRouter()


# Request/Response Models
class CreateSessionRequest(BaseModel):
    user_id: int = Field(default=1, description="User ID (default 1 for single-user)")
    prompt: str = Field(..., description="User query/intent for this session")
    candidate_ids: list[int] = Field(..., description="List of persistent candidate IDs to compare")
    list_type: str = Field(default="chat", description="Type of list (chat, mood, theme, etc.)")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Optional filter dict for session context")


class SubmitJudgmentRequest(BaseModel):
    user_id: int = Field(default=1, description="User ID (default 1 for single-user)")
    session_id: int = Field(..., description="Session ID")
    candidate_a_id: int = Field(..., description="First candidate ID")
    candidate_b_id: int = Field(..., description="Second candidate ID")
    winner: str = Field(..., description="Winner: 'a', 'b', or 'skip'")
    confidence: Optional[float] = Field(default=None, description="Confidence score (0.0-1.0)")
    response_time_ms: Optional[int] = Field(default=None, description="Time taken to judge (milliseconds)")
    explanation: Optional[str] = Field(default=None, description="Optional user explanation")


class SessionStatusResponse(BaseModel):
    id: int
    user_id: int
    prompt: str
    list_type: str
    total_pairs: int
    completed_pairs: int
    progress: float
    status: str
    started_at: Optional[str]
    completed_at: Optional[str]


class NextPairResponse(BaseModel):
    session_id: int
    candidate_a: Dict[str, Any]
    candidate_b: Dict[str, Any]
    progress: float


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/session/create-auto", response_model=Dict[str, Any])
async def create_auto_training_session(
    user_id: int = Body(1, embed=True),
    db: Session = Depends(get_db)
):
    """Create a training session with automatically selected diverse candidates.
    
    Selects 20 diverse, popular candidates from the database for pairwise comparison.
    No prompt needed - candidates are chosen to represent variety across genres/types.
    """
    try:
        from app.models import PersistentCandidate
        
        # Get 20 diverse, popular candidates (randomized for variety across sessions)
        from sqlalchemy import func
        candidates = db.query(PersistentCandidate).filter(
            PersistentCandidate.active == True,
            PersistentCandidate.is_adult == False,
            PersistentCandidate.vote_count >= 100,
            PersistentCandidate.popularity >= 10  # Reasonably popular items
        ).order_by(
            func.random()  # Randomize selection for different candidates each session
        ).limit(20).all()
        
        if len(candidates) < 2:
            raise HTTPException(status_code=404, detail="Not enough candidates in database")
        
        candidate_ids = [c.id for c in candidates]
        
        trainer = PairwiseTrainer(db=db, user_id=user_id)
        session = trainer.create_session(
            prompt="Auto-generated diverse selection",
            candidate_ids=candidate_ids,
            list_type="auto",
            filters=None
        )
        
        try:
            await metrics.increment("trainer.sessions_created", 1)
        except Exception as e:
            logger.debug(f"Failed to record session metrics: {e}")
        
        return {
            "session_id": session.id,
            "total_pairs": session.total_pairs,
            "status": session.status,
            "message": f"Created training session with {len(candidate_ids)} auto-selected candidates"
        }
        
    except Exception as e:
        logger.error(f"Failed to create auto training session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/session/create", response_model=Dict[str, Any])
async def create_training_session(
    request: CreateSessionRequest,
    db: Session = Depends(get_db)
):
    """Create a new pairwise training session.
    
    Initializes a session with candidate pool and generates pair schedule.
    """
    try:
        trainer = PairwiseTrainer(db=db, user_id=request.user_id)
        session = trainer.create_session(
            prompt=request.prompt,
            candidate_ids=request.candidate_ids,
            list_type=request.list_type,
            filters=request.filters
        )
        try:
            await metrics.increment("trainer.sessions_created", 1)
        except Exception as e:
            logger.debug(f"Failed to record session metrics: {e}")
        
        return {
            "session_id": session.id,
            "total_pairs": session.total_pairs,
            "status": session.status,
            "message": f"Created training session with {len(request.candidate_ids)} candidates"
        }
        
    except Exception as e:
        logger.error(f"Failed to create training session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create session: {str(e)}")


@router.get("/session/{session_id}/next", response_model=Optional[NextPairResponse])
async def get_next_pair(
    session_id: int,
    user_id: int = 1,
    db: Session = Depends(get_db)
):
    """Get next pair of candidates to judge.
    
    Returns None if session is complete or no more pairs available.
    """
    try:
        trainer = PairwiseTrainer(db=db, user_id=user_id)
        pair = trainer.get_next_pair(session_id=session_id)
        
        if pair is None:
            return None
            
        candidate_a, candidate_b = pair
        status = trainer.get_session_status(session_id)
        
        try:
            await metrics.increment("trainer.pairs_served", 1)
        except Exception as e:
            logger.debug(f"Failed to record pair metrics: {e}")
        
        return NextPairResponse(
            session_id=session_id,
            candidate_a=candidate_a,
            candidate_b=candidate_b,
            progress=status["progress"] if status else 0.0
        )
        
    except Exception as e:
        logger.error(f"Failed to get next pair for session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get next pair: {str(e)}")


@router.post("/session/judgment", response_model=Dict[str, Any])
async def submit_judgment(
    request: SubmitJudgmentRequest,
    db: Session = Depends(get_db)
):
    """Submit a pairwise judgment.
    
    Records user preference and updates user vectors immediately.
    """
    try:
        trainer = PairwiseTrainer(db=db, user_id=request.user_id)
        judgment = trainer.submit_judgment(
            session_id=request.session_id,
            candidate_a_id=request.candidate_a_id,
            candidate_b_id=request.candidate_b_id,
            winner=request.winner,
            confidence=request.confidence,
            response_time_ms=request.response_time_ms,
            explanation=request.explanation
        )
        try:
            await metrics.increment("trainer.judgments", 1)
            if request.winner == "skip":
                await metrics.increment("trainer.skips", 1)
            if request.explanation:
                await metrics.increment("trainer.explanations", 1)
            if request.response_time_ms is not None:
                await metrics.timing("trainer.response_ms", float(request.response_time_ms))
        except Exception as e:
            logger.debug(f"Failed to record judgment metrics: {e}")
        
        # Get updated session status
        status = trainer.get_session_status(request.session_id)
        
        return {
            "judgment_id": judgment.id,
            "session_status": status,
            "message": "Judgment recorded successfully"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to submit judgment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to submit judgment: {str(e)}")


@router.get("/session/{session_id}/status", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: int,
    user_id: int = 1,
    db: Session = Depends(get_db)
):
    """Get session status and progress."""
    try:
        trainer = PairwiseTrainer(db=db, user_id=user_id)
        status = trainer.get_session_status(session_id)
        
        if status is None:
            raise HTTPException(status_code=404, detail="Session not found")
            
        return SessionStatusResponse(**status)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get session status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


@router.get("/profile", response_model=Dict[str, Any])
async def get_user_profile(
    user_id: int = 1,
    db: Session = Depends(get_db)
):
    """Get current user preference profile from pairwise training.
    
    Returns learned weights for genres, decades, languages, obscurity, and freshness.
    """
    try:
        trainer = PairwiseTrainer(db=db, user_id=user_id)
        profile = trainer.get_user_profile()
        
        return {
            "user_id": user_id,
            "profile": profile,
            "message": f"Retrieved profile with {profile.get('judgment_count', 0)} judgments"
        }
        
    except Exception as e:
        logger.error(f"Failed to get user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get profile: {str(e)}")
