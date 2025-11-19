"""
explainability.py (AI Engine)
- LLM-backed explanations with deterministic fallback.
"""
from typing import Dict, Any, Optional, Any as _Any
import json
import os

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

import httpx

from app.core.config import settings


def build_explanation_meta(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "similarity_type": "semantic" if candidate.get("semantic_sim", 0) >= candidate.get("bm25_sim", 0) else "bm25",
        "genre_overlap": candidate.get("genre_overlap", 0.0),
        "mood_score": candidate.get("mood_score", 0.0),
        "novelty_score": candidate.get("novelty", 0.0),
    }


def _r() -> Optional[_Any]:
    if redis is None:
        return None
    try:
        return redis.from_url(settings.redis_url)
    except Exception:
        return None


def _llm_explain_prompt(candidate: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict:
    sys = "You explain why a movie/show fits a user request. One short sentence."
    payload = {
        "title": candidate.get("title"),
        "media_type": candidate.get("media_type"),
        "genres": candidate.get("genres"),
        "overview": candidate.get("overview"),
        "mood": (context or {}).get("mood"),
        "tone": (context or {}).get("tone"),
        "seasonal": (context or {}).get("seasonal"),
    }
    model_name = settings.ai_llm_judge_model or "phi3.5:3.8b-mini-instruct-q4_K_M"
    if not model_name or model_name.strip() == "":
        model_name = "phi3.5:3.8b-mini-instruct-q4_K_M"
    return {
        "model": model_name,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": 60,
        "stream": False,
    }


def generate_explanation(candidate: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    # LLM path (local) if enabled
    if getattr(settings, "ai_llm_explain_enabled", False):
        try:
            r = _r()
            # If judge reasons exist for the same query hash, reuse them
            qh = candidate.get("judge_query_hash") or (context or {}).get("judge_query_hash")
            if r is not None and qh:
                try:
                    rid = int(candidate.get("id") or 0)
                except Exception:
                    rid = 0
                if rid:
                    key = f"llmjudge:reasons:{qh}:{rid}"
                    rv = r.get(key)
                    if rv:
                        try:
                            arr = json.loads(rv)
                            if isinstance(arr, list) and arr:
                                return ", ".join([str(x) for x in arr[:2]]).strip().rstrip(".") + "."
                        except Exception:
                            pass
            # Gate on nightly readiness (same as judge)
            ready = False
            if r is not None:
                try:
                    bge_ready = r.get("settings:global:ai_bge_index_enabled")
                    prof_ready = r.get("settings:global:ai_profile_vectors_ready")
                    ready = (bge_ready == b"true" or bge_ready == "true") and (prof_ready == b"true" or prof_ready == "true")
                except Exception:
                    ready = False
            if not ready:
                raise RuntimeError("LLM explanation gated until nightly prerequisites ready")
            cache_key = None
            if r is not None:
                cache_key = f"llmexp:{settings.ai_llm_judge_model}:{int(candidate.get('id') or 0)}:{hash(json.dumps(context or {}, sort_keys=True))}"
                val = r.get(cache_key)
                if val:
                    return val.decode("utf-8")
            with httpx.Client(base_url=settings.ai_llm_api_base, timeout=int(settings.ai_llm_timeout_seconds or 8)) as client:
                body = _llm_explain_prompt(candidate, context)
                if settings.ai_llm_judge_provider == "ollama":
                    resp = client.post("/api/chat", json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    text = data.get("message", {}).get("content", "")
                else:
                    resp = client.post("/chat/completions", json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                text = (text or "").strip()
                if r is not None and cache_key and text:
                    r.setex(cache_key, 24 * 3600, text)
                if text:
                    return text
        except Exception:
            pass
    # Deterministic fallback
    bits = []
    if candidate.get("semantic_sim", 0) > 0.6:
        bits.append("high semantic match")
    if candidate.get("bm25_sim", 0) > 0.4:
        bits.append("strong keyword match")
    if candidate.get("novelty", 0) > 0.6:
        bits.append("novel pick")
    if not bits:
        bits.append("relevant to your prompt")
    return ", ".join(bits).capitalize() + "."
