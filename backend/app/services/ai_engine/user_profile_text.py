import json
from typing import Dict, Any
from collections import Counter
from app.core.database import SessionLocal
from app.models import TraktWatchHistory, UserTextProfile
from app.utils.timezone import utc_now

class UserTextProfileService:
    """Synthesizes a short textual user profile from watch history & ratings.
    Lazy-fills DB and also returns a compact dict for prompts.
    """

    @staticmethod
    def _summarize(genres: list[str], keywords: list[str], languages: list[str]) -> str:
        top_gen = ", ".join([g for g, _ in Counter(genres).most_common(3)])
        top_kw = ", ".join([k for k, _ in Counter(keywords).most_common(5)])
        langs = ", ".join([l for l, _ in Counter(languages).most_common(2)])
        parts = []
        if top_gen:
            parts.append(f"tends toward {top_gen}")
        if top_kw:
            parts.append(f"often enjoys {top_kw}")
        if langs:
            parts.append(f"frequently watches {langs}-language content")
        if not parts:
            return "general taste across genres, open to variety"
        return "; ".join(parts)

    @classmethod
    def get_or_build(cls, user_id: int) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            row = db.query(UserTextProfile).filter(UserTextProfile.user_id == user_id).first()
            if row and row.summary_text:
                try:
                    tags = json.loads(row.tags_json) if row.tags_json else []
                except Exception:
                    tags = []
                return {"summary_text": row.summary_text, "tags": tags}

            # Aggregate simple signals from watch history
            q = db.query(TraktWatchHistory).filter(TraktWatchHistory.user_id == user_id).order_by(TraktWatchHistory.watched_at.desc()).limit(200)
            genres: list[str] = []
            keywords: list[str] = []
            languages: list[str] = []
            for ev in q.all():
                try:
                    if ev.genres:
                        genres.extend(json.loads(ev.genres) if ev.genres.startswith("[") else [g.strip() for g in ev.genres.split(",") if g.strip()])
                except Exception:
                    pass
                try:
                    if ev.keywords:
                        keywords.extend(json.loads(ev.keywords) if ev.keywords.startswith("[") else [k.strip() for k in ev.keywords.split(",") if k.strip()])
                except Exception:
                    pass
                if ev.language:
                    languages.append(ev.language)
            # Build compact narrative and tags
            summary = cls._summarize([g.lower() for g in genres], [k.lower() for k in keywords], [l.lower() for l in languages])
            tags = list({*(g.lower() for g, _ in Counter(genres).most_common(6)), *(k.lower() for k, _ in Counter(keywords).most_common(8))})
            row = UserTextProfile(user_id=user_id, summary_text=summary, tags_json=json.dumps(tags), created_at=utc_now(), updated_at=utc_now())
            db.add(row)
            db.commit()
            return {"summary_text": summary, "tags": tags}
        finally:
            db.close()
