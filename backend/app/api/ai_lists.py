"""
api/ai_lists.py
- FastAPI endpoints for AI-powered lists: create, list, refresh, delete, and prompt cache inspection.
- Integrates with tasks_ai and models_ai.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from app.models_ai import AiList, AiListItem, PromptCache
from app.core.database import SessionLocal
from app.tasks_ai import generate_chat_list, generate_dynamic_lists, refresh_ai_list
from typing import List

router = APIRouter(prefix="", tags=["ai_lists"])


class CreateAiListRequest(BaseModel):
    prompt: str
    user_id: int = 1


class UserRequest(BaseModel):
    user_id: int = 1


class UserScopedRequest(BaseModel):
    user_id: int = 1

@router.post("/create", response_model=dict)
def create_ai_list(payload: CreateAiListRequest, background_tasks: BackgroundTasks = None):
    db = SessionLocal()
    try:
        ai_list = AiList(prompt_text=payload.prompt, user_id=payload.user_id, type="chat", status="pending")
        db.add(ai_list)
        db.commit()
        db.refresh(ai_list)
        # Trigger async generation
        if background_tasks is not None:
            background_tasks.add_task(generate_chat_list.delay, ai_list.id, payload.user_id)
        else:
            generate_chat_list.delay(ai_list.id, payload.user_id)
        return {"id": ai_list.id, "status": ai_list.status}
    finally:
        db.close()

@router.get("/list", response_model=List[dict])
def list_ai_lists(user_id: int = 1):
    db = SessionLocal()
    try:
        lists = db.query(AiList).filter_by(user_id=user_id).all()
        return [{
            "id": l.id,
            "prompt": l.prompt_text,
            "status": l.status,
            "type": l.type,
            "generated_title": getattr(l, "generated_title", None),
            "last_synced_at": getattr(l, "last_synced_at", None).isoformat() if getattr(l, "last_synced_at", None) else None,
        } for l in lists]
    finally:
        db.close()

@router.post("/list", response_model=List[dict])
def list_ai_lists_post(payload: UserRequest):
    return list_ai_lists(user_id=payload.user_id)

@router.post("/refresh/{ai_list_id}", response_model=dict)
def refresh_ai_list_route(ai_list_id: str, payload: UserScopedRequest):
    db = SessionLocal()
    try:
        ai_list = db.query(AiList).filter_by(id=ai_list_id, user_id=payload.user_id).first()
        if not ai_list:
            raise HTTPException(status_code=404, detail="AI list not found")
        ai_list.status = "pending"
        db.commit()
        refresh_ai_list.delay(ai_list.id, payload.user_id)
        return {"id": ai_list.id, "status": ai_list.status}
    finally:
        db.close()

@router.delete("/delete/{ai_list_id}", response_model=dict)
def delete_ai_list(ai_list_id: str, user_id: int = 1):
    db = SessionLocal()
    try:
        ai_list = db.query(AiList).filter_by(id=ai_list_id, user_id=user_id).first()
        if not ai_list:
            raise HTTPException(status_code=404, detail="AI list not found")
        db.query(AiListItem).filter_by(ai_list_id=ai_list_id).delete()
        db.delete(ai_list)
        db.commit()
        return {"deleted": True}
    finally:
        db.close()

@router.post("/delete/{ai_list_id}", response_model=dict)
def delete_ai_list_post(ai_list_id: str, payload: UserScopedRequest):
    return delete_ai_list(ai_list_id=ai_list_id, user_id=payload.user_id)

@router.post("/generate-7", response_model=dict)
def generate_7_lists(user_id: int = 1):
    generate_dynamic_lists.delay(user_id)
    return {"queued": True}

@router.get("/prompt-cache/{hash}", response_model=dict)
def get_prompt_cache_by_hash(hash: str):
    from app.core.redis_client import get_redis_sync
    r = get_redis_sync()
    val = r.get(f"ai:prompt_cache:{hash}")
    if not val:
        return {"cached": False}
    import json
    return {"cached": True, "data": json.loads(val)}

@router.get("/cooldown/{ai_list_id}", response_model=dict)
def get_cooldown(ai_list_id: str):
    """Return remaining cooldown seconds for an AI list refresh/create."""
    from app.core.redis_client import get_redis_sync
    r = get_redis_sync()
    ttl = r.ttl(f"ai:cooldown:{ai_list_id}")
    ttl = int(ttl) if ttl and ttl > 0 else 0
    return {"ttl": ttl}

@router.get("/{ai_list_id}/items", response_model=List[dict])
def list_ai_list_items(ai_list_id: str, user_id: int = 1):
    """Return AI list items enriched with title/poster when available.

    Fields:
    - tmdb_id, trakt_id, rank, score
    - title (from MediaMetadata or persistent_candidates fallback)
    - media_type (if available)
    - poster_url (absolute URL when possible)
    - explanation_text, explanation_meta
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(AiListItem)
            .filter_by(ai_list_id=ai_list_id)
            .order_by(AiListItem.rank.asc())
            .all()
        )

        # Bulk-enrich by tmdb_id using MediaMetadata (preferred)
        tmdb_ids = [r.tmdb_id for r in rows if r.tmdb_id]
        meta_by_tmdb = {}
        if tmdb_ids:
            try:
                from app.models import MediaMetadata
                metas = (
                    db.query(MediaMetadata)
                    .filter(MediaMetadata.tmdb_id.in_(tmdb_ids))
                    .all()
                )
                for m in metas:
                    meta_by_tmdb[m.tmdb_id] = m
            except Exception:
                meta_by_tmdb = {}

        # For any missing, fallback to persistent_candidates for title/media_type/poster_path
        missing_tmdb = [tid for tid in tmdb_ids if tid not in meta_by_tmdb]
        pc_by_tmdb = {}
        if missing_tmdb:
            try:
                from app.models import PersistentCandidate
                pcs = (
                    db.query(PersistentCandidate)
                    .filter(PersistentCandidate.tmdb_id.in_(missing_tmdb))
                    .with_entities(
                        PersistentCandidate.tmdb_id,
                        PersistentCandidate.media_type,
                        PersistentCandidate.title,
                        PersistentCandidate.poster_path,
                    )
                    .all()
                )
                for tmdb_id, media_type, title_pc, poster_pc in pcs:
                    pc_by_tmdb[tmdb_id] = {
                        "tmdb_id": tmdb_id,
                        "media_type": media_type,
                        "title": title_pc,
                        "poster_path": poster_pc,
                    }
            except Exception:
                pc_by_tmdb = {}

        def _poster_to_url(p: str | None) -> str | None:
            if not p:
                return None
            # If already a full URL, return as-is
            if isinstance(p, str) and (p.startswith("http://") or p.startswith("https://")):
                return p
            return f"https://image.tmdb.org/t/p/w342{p}" if p else None

        enriched = []
        for r in rows:
            meta = meta_by_tmdb.get(r.tmdb_id)
            pc = pc_by_tmdb.get(r.tmdb_id)
            title = None
            media_type = None
            poster_url = None
            if meta:
                title = getattr(meta, "title", None)
                media_type = getattr(meta, "media_type", None)
                poster_url = _poster_to_url(getattr(meta, "poster_path", None))
            if (not title or not poster_url or not media_type) and pc:
                # Fill missing fields from persistent_candidates
                title = title or pc.get("title")
                media_type = media_type or pc.get("media_type")
                poster_url = poster_url or _poster_to_url(pc.get("poster_path"))

            enriched.append(
                {
                    "tmdb_id": r.tmdb_id,
                    "trakt_id": r.trakt_id,
                    "rank": r.rank,
                    "score": r.score,
                    "title": title,
                    "media_type": media_type,
                    "poster_url": poster_url,
                    "explanation_text": r.explanation_text,
                    "explanation_meta": r.explanation_meta,
                }
            )

        return enriched
    finally:
        db.close()
