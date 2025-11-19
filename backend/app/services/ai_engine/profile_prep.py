import json
from typing import Any, Dict, Optional
from app.core.database import SessionLocal
from app.models import PersistentCandidate, ItemLLMProfile
from app.utils.timezone import utc_now

class ItemProfileService:
    """Builds compact, unified item profiles for LLM use.
    Lazy-fills a DB row and returns a dict + short text.
    """

    PROFILE_VERSION = 1

    @staticmethod
    def _compact_profile(c: PersistentCandidate) -> Dict[str, Any]:
        def _loads(val: Optional[str], max_items: Optional[int] = None) -> list:
            if not val:
                return []
            try:
                items = json.loads(val) if val.startswith("[") else [s.strip() for s in val.split(",") if s.strip()]
                if max_items is not None:
                    items = items[:max_items]
                return items
            except Exception:
                return []
        # 24 TMDB fields, length caps, omit missing
        fields = {
            "candidate_id": c.id,
            "tmdb_id": c.tmdb_id,
            "trakt_id": c.trakt_id,
            "media_type": c.media_type,
            "title": (c.title or "")[:120],
            "original_title": (c.original_title or "")[:120],
            "year": c.year,
            "genres": _loads(c.genres, 6),
            "keywords": _loads(c.keywords, 8),
            "overview": (c.overview or "")[:200],
            "tagline": (c.tagline or "")[:120],
            "people": _loads(c.cast, 4),
            "studio": (c.production_companies or "")[:60],
            "network": (c.networks or "")[:60],
            "rating": float(c.vote_average or 0.0),
            "votes": int(c.vote_count or 0),
            "popularity": float(c.popularity or 0.0),
            "language": (c.language or "")[:8],
            "runtime": int(c.runtime or 0),
            "certification": (c.certification or "")[:16],
            "status": (c.status or "")[:24],
            "aliases": _loads(c.aliases, 4),
            "season_count": int(getattr(c, "number_of_seasons", 0) or 0),
            "episode_count": int(getattr(c, "number_of_episodes", 0) or 0),
            "first_air_date": (getattr(c, "first_air_date", "") or "")[:12],
            "original_language": (c.language or "").lower() if c.language else None,
        }
        # Omit missing/empty fields
        return {k: v for k, v in fields.items() if v not in (None, "", [], {})}

    @staticmethod
    def _compact_text(p: Dict[str, Any]) -> str:
        # Compact one-line summary using key fields
        parts = []
        if p.get("title"):
            parts.append(f"{p['title']} ({p.get('year','')})")
        if p.get("media_type"):
            parts.append(p["media_type"])
        if p.get("genres"):
            parts.append("/".join(p["genres"]))
        if p.get("overview"):
            parts.append(f"Overview: {p['overview']}")
        if p.get("keywords"):
            parts.append(f"Keywords: {', '.join(p['keywords'][:3])}")
        if p.get("people"):
            parts.append(f"People: {', '.join(p['people'])}")
        if p.get("studio"):
            parts.append(f"Studio: {p['studio']}")
        if p.get("rating"):
            parts.append(f"Rating: {p['rating']}/10")
        return ". ".join(parts)[:220]

    @classmethod
    def get_or_build(cls, candidate_id: int) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            row = db.query(ItemLLMProfile).filter(ItemLLMProfile.candidate_id == candidate_id).first()
            if row and (row.version == cls.PROFILE_VERSION) and row.profile_json:
                try:
                    prof = json.loads(row.profile_json)
                    text = row.profile_text or cls._compact_text(prof)
                    return {"profile": prof, "text": text}
                except Exception:
                    pass
            c = db.query(PersistentCandidate).filter(PersistentCandidate.id == candidate_id).first()
            if not c:
                return {"profile": {}, "text": ""}
            prof = cls._compact_profile(c)
            text = cls._compact_text(prof)
            data = json.dumps(prof, ensure_ascii=False)
            if row is None:
                row = ItemLLMProfile(candidate_id=candidate_id, profile_json=data, profile_text=text, version=cls.PROFILE_VERSION, created_at=utc_now(), updated_at=utc_now())
                db.add(row)
            else:
                row.profile_json = data
                row.profile_text = text
                row.version = cls.PROFILE_VERSION
                row.updated_at = utc_now()
            db.commit()
            return {"profile": prof, "text": text}
        finally:
            db.close()
