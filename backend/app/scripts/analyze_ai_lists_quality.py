#!/usr/bin/env python3
"""
Analyze AI lists quality: vote_count coverage and topic coherence.
Outputs per-list summary: title, type, prompt, item_count, % with vote_count >= thresholds, median votes,
median popularity, and topic coherence score percentiles.
Run inside backend container with PYTHONPATH=/app.
"""
import json
import statistics
from math import floor
from typing import List, Dict

from app.core.database import SessionLocal
from app.models_ai import AiList, AiListItem
from app.models import PersistentCandidate
from app.services.ai_engine.metadata_processing import compose_text_for_embedding


def topic_scores(prompt: str, texts: List[str]) -> List[float]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except Exception:
        return [0.0 for _ in texts]
    if not texts:
        return []
    vec = TfidfVectorizer(max_features=5000)
    mat = vec.fit_transform([prompt] + texts)
    sims = cosine_similarity(mat[0:1], mat[1:]).flatten().tolist()
    return sims


def _percentile(values: List[float], q: float) -> float:
    """Robust percentile for small samples without numpy.
    Uses linear interpolation between closest ranks.
    Handles len 0/1 gracefully.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    vals = sorted(values)
    # position in [0, n-1]
    k = (len(vals) - 1) * q
    f = floor(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return float(vals[f])
    d = k - f
    return float(vals[f] + (vals[c] - vals[f]) * d)


def main():
    db = SessionLocal()
    try:
        lists: List[AiList] = db.query(AiList).order_by(AiList.created_at.desc()).all()
        print(f"Found {len(lists)} AI lists\n")
        for lst in lists:
            items: List[AiListItem] = db.query(AiListItem).filter_by(ai_list_id=lst.id).order_by(AiListItem.rank.asc()).all()
            if not items:
                continue
            # Fetch candidates from persistent store for vote/popularity
            trakt_ids = [i.trakt_id for i in items if i.trakt_id]
            q = db.query(PersistentCandidate).filter(PersistentCandidate.trakt_id.in_(trakt_ids)) if trakt_ids else []
            by_tid: Dict[int, PersistentCandidate] = {c.trakt_id: c for c in (q or [])}
            votes = []
            pops = []
            texts: List[str] = []
            for it in items:
                c = by_tid.get(it.trakt_id)
                if c:
                    if c.vote_count is not None:
                        votes.append(int(c.vote_count))
                    if c.popularity is not None:
                        pops.append(float(c.popularity))
                    texts.append(compose_text_for_embedding({
                        "title": c.title,
                        "overview": c.overview,
                        "genres": c.genres,
                        "keywords": c.keywords,
                    }))
                else:
                    texts.append("")
            sims = topic_scores(lst.normalized_prompt or lst.prompt_text or "", texts)
            # Stats
            total = len(items)
            v_ge_400 = sum(1 for v in votes if v >= 400)
            v_ge_800 = sum(1 for v in votes if v >= 800)
            v_zero = sum(1 for v in votes if v == 0)
            med_votes = statistics.median(votes) if votes else 0
            med_pop = statistics.median(pops) if pops else 0.0
            p25 = _percentile(sims, 0.25)
            p50 = _percentile(sims, 0.50)
            p90 = _percentile(sims, 0.90)
            print(f"- {lst.generated_title or (lst.prompt_text or '')[:40]} [{lst.type}] ({total} items)")
            print(f"  Prompt: {(lst.normalized_prompt or lst.prompt_text or '')[:80]}")
            print(f"  Votes: >=400 {v_ge_400}/{total} ({(v_ge_400/total*100):.0f}%), >=800 {v_ge_800}/{total} ({(v_ge_800/total*100):.0f}%), zeros {v_zero}")
            print(f"  Med votes: {med_votes}, Med popularity: {med_pop:.2f}")
            print(f"  Topic sim p25/p50/p90: {p25:.2f}/{p50:.2f}/{p90:.2f}\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
