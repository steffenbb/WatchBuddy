import hashlib
import json
import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

import httpx
import time

logger = logging.getLogger(__name__)


@dataclass
class JudgeConfig:
    enabled: bool = False
    weight: float = 0.15
    batch_size: int = 5  # REDUCED from 20 to 5 - smaller batches = faster response, less timeout risk
    cache_ttl_seconds: int = 14 * 24 * 3600
    timeout_seconds: int = int(os.environ.get("AI_LLM_TIMEOUT_SECONDS", "90")) or 90  # Increased to 90 for remote Ollama server
    provider: str = os.environ.get("AI_LLM_JUDGE_PROVIDER", "ollama") or "ollama"
    api_base: str = os.environ.get("AI_LLM_API_BASE", os.environ.get("OPENAI_API_BASE", "http://ollama:11434")) or "http://ollama:11434"
    api_key_env: str = os.environ.get("AI_LLM_API_KEY_ENV", "") or ""
    model: str = os.environ.get("AI_LLM_JUDGE_MODEL", "phi3.5:3.8b-mini-instruct-q4_K_M") or "phi3.5:3.8b-mini-instruct-q4_K_M"


def _hash_query(query_summary: Dict) -> str:
    raw = json.dumps(query_summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _get_redis_client() -> Optional[Any]:
    if redis is None:
        return None
    try:
        return redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    except Exception:
        return None


def _schema_item(item: Dict) -> Dict:
    return {
        "id": int(item.get("id")),
        "title": str(item.get("title", ""))[:120],
        "year": int(item.get("year")) if item.get("year") else None,
        "media_type": item.get("media_type", "movie"),
        "genres": item.get("genres", [])[:6],
        "keywords": item.get("keywords", [])[:8],
        "overview": str(item.get("overview", ""))[:180],
        "people": item.get("people", [])[:4],  # actors/directors/creators
        "studio": item.get("studio"),
        "network": item.get("network"),
        "rating": float(item.get("vote_average", 0.0) or 0.0),
        "votes": int(item.get("vote_count", 0) or 0),
        "popularity": float(item.get("popularity", 0.0) or 0.0),
        "language": item.get("original_language") or item.get("language"),
        "runtime": int(item.get("runtime", 0) or 0),
    }


def _build_prompt(query_summary: Dict, rubric: Dict, items: List[Dict], persona: str = "", history: str = "") -> str:
    # Explicit, compact instruction + structured payload to encourage JSON-only output
    sys = (
        "You are a strict list curator. Score each item on an absolute 0.0–1.0 scale. "
        "Use an absolute scale across batches (do not rescale within this batch). "
        "Calibrate with target_size: roughly that many items overall should score ≥ 0.70. "
        "Penalize contradictions and negative cues. Prefer concise, factual reasons (≤8 words each, max 2). "
        "\n\n**CRITICAL: You MUST return ONLY valid JSON. No explanations, no markdown, no extra text.**\n"
        "Expected format: {\"scores\":[{\"id\":<int>,\"score\":<float>,\"reasons\":[<str>,<str>]}]}\n"
        "Double-check all brackets are closed and JSON syntax is correct before responding.\n"
        "If your output is not valid JSON, repair and output corrected JSON only."
    )
    # Weighted rubric for small local LLMs; keep concise
    target_size = int(query_summary.get("target_size") or 50)
    filters = query_summary.get("filters") or {}
    
    # Add persona/history context if available (trimmed to 200 chars each)
    user_context = {}
    if persona:
        user_context["persona"] = persona[:200]
    if history:
        user_context["history"] = history[:150]
    
    rubric_compact = {
        "dimensions": [
            {"name": "on_topic_fit", "weight": 0.45, "desc": "Match prompt intent and query_variants"},
            {"name": "mood_season_fit", "weight": 0.25, "desc": "Align with mood/tone/seasonal cues"},
            {"name": "genre_language_runtime", "weight": 0.10, "desc": "Genres/language/runtime tolerance"},
            {"name": "quality_signal", "weight": 0.10, "desc": "Rating/votes/popularity; adjust to qualifiers"},
            {"name": "constraints", "weight": 0.05, "desc": "Penalize negatives, duplicates, obvious mismatch"},
            {"name": "user_profile_fit", "weight": 0.05, "desc": "Align to user profile (persona/history)"},
        ],
        "calibration": {
            "target_size": target_size,
            "threshold_hint": 0.70,
            "neutral_default": 0.50,
        },
        "user_context": user_context,
        "output": {
            "schema": {"scores": [{"id": "int", "score": "float(0..1)", "reasons": ["str", "str"]}]},
            "rules": [
                "Only score provided item ids",
                "No prose outside JSON",
                "Reasons ≤8 words, max 2 per item",
                "Round score to 2 decimals if needed",
            ],
        },
        "notes": {
            "negative_cues": filters.get("negative_cues") or [],
            "query_variants": (query_summary.get("enrichment") or {}).get("query_variants") or [],
        },
    }
    payload = {
        "criteria": query_summary,
        "rubric": rubric_compact,
        "items": [_schema_item(it) for it in items],
    }
    return sys + "\n" + json.dumps(payload, separators=(",",":"), ensure_ascii=False)


def _parse_scores_and_reasons(output_text: str) -> Tuple[Dict[int, float], Dict[int, List[str]]]:
    def _try_parse(txt: str) -> Tuple[Dict[int, float], Dict[int, List[str]]]:
        try:
            data = json.loads(txt)
        except Exception:
            return {}, {}
        scores: Dict[int, float] = {}
        reasons: Dict[int, List[str]] = {}
        for entry in data.get("scores", [])[:500]:
            try:
                iid = int(entry.get("id"))
                sc = float(entry.get("score"))
                if sc < 0.0 or sc > 1.0:
                    continue
                scores[iid] = sc
                rs = entry.get("reasons") or []
                if isinstance(rs, list):
                    # Enforce max 2 concise reasons, trimmed length
                    reasons[iid] = [str(r)[:100] for r in rs[:2]]
            except Exception:
                continue
        return scores, reasons

    # First attempt: direct JSON
    scores, reasons = _try_parse(output_text)
    if scores:
        return scores, reasons

    # Fallback: extract JSON object containing a "scores" array
    try:
        import re as _re
        m = _re.search(r"\{.*?\"scores\"\s*:\s*\[.*?\]\s*\}", output_text, flags=_re.S)
        if m:
            return _try_parse(m.group(0))
    except Exception as e:
        logger.warning(f"[LLM_JUDGE] Failed to extract scores JSON from output: {e}. Raw output: {output_text[:400]}")

    # Telemetry: count JSON drift
    try:
        r = _get_redis_client()
        if r is not None:
            r.incrby("ai_telemetry:llmjudge:json_drift", 1)
    except Exception:
        pass

    logger.warning(f"[LLM_JUDGE] Could not parse any valid JSON from LLM output. Raw: {output_text[:500]}")
    return {}, {}


def judge_scores(
    query_summary: Dict,
    candidates: List[Dict],
    cfg: Optional[JudgeConfig] = None,
    provider_name: Optional[str] = None,
    persona: str = "",
    history: str = "",
) -> Dict[int, float]:
    """Score candidates with an LLM judge (cached, batched).
    
    Args:
        query_summary: Query intent and filters
        candidates: List of candidate items to score
        cfg: Judge configuration
        provider_name: LLM provider override
        persona: User persona text (trimmed to 200 chars)
        history: User history summary (trimmed to 150 chars)
    """
    cfg = cfg or JudgeConfig(enabled=False)
    if not cfg.enabled:
        return {}

    r = _get_redis_client()
    qh = _hash_query(query_summary)

    # DISABLED CACHING - LLM judge should run fresh every time for dynamic scoring
    # Cache was causing stale results and incorrect scores across different queries
    cached: Dict[int, float] = {}
    
    # Score ALL candidates (no cache lookup)
    to_score = candidates

    # Prepare HTTP client and request builders per provider
    api_key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _req_payload_openai(prompt: str) -> Dict:
        # OpenAI-compatible chat.completions (can be local vLLM/LM Studio/Ollama plugin)
        # CRITICAL FIX: Ensure model_name is never None/empty
        model_name = cfg.model
        if not model_name or not str(model_name).strip():
            logger.warning(f"[LLM_JUDGE] Model name is empty (cfg.model={cfg.model}), using default")
            model_name = "phi3.5:3.8b-mini-instruct-q4_K_M"
        else:
            model_name = str(model_name).strip()
        
        logger.info(f"[LLM_JUDGE] OpenAI-compatible request with model: {model_name}")
        return {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are a rigorous ranking judge. Respond with strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 300,
        }

    def _req_payload_ollama(prompt: str) -> Dict:
        # Native Ollama chat API
        # Hardcoded model name to match intent_extractor
        logger.info(f"[LLM_JUDGE] Ollama request with model: phi3.5:3.8b-mini-instruct-q4_K_M")
        return {
            "model": "phi3.5:3.8b-mini-instruct-q4_K_M",
            "messages": [
                {"role": "system", "content": "You are a rigorous ranking judge. Respond with strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 512, "num_ctx": 4096},  # Increased from 128 to 512 for complete JSON output
            "keep_alive": "24h",
        }

    # Score in batches
    scored: Dict[int, float] = {}
    batches = [to_score[i : i + cfg.batch_size] for i in range(0, len(to_score), cfg.batch_size)]
    for batch in batches:
        prompt = _build_prompt(query_summary, rubric={"scale": "0-1", "goal": "maximize relevance"}, items=batch, persona=persona, history=history)
        try:
            # Use explicit httpx.Timeout to set all timeout values (connect, read, write, pool)
            # Set all four timeout values explicitly to prevent defaults
            timeout = httpx.Timeout(
                connect=cfg.timeout_seconds,
                read=cfg.timeout_seconds,
                write=cfg.timeout_seconds,
                pool=cfg.timeout_seconds
            )
            with httpx.Client(base_url=cfg.api_base, timeout=timeout) as client:
                if (provider_name or cfg.provider) == "ollama":
                    # Expect cfg.api_base like http://host.docker.internal:11434
                    resp = client.post("/api/chat", headers=headers, json=_req_payload_ollama(prompt))
                    resp.raise_for_status()
                    data = resp.json()
                    content = data.get("message", {}).get("content", "")
                else:
                    # Default: OpenAI-compatible (local OK)
                    resp = client.post("/chat/completions", headers=headers, json=_req_payload_openai(prompt))
                    resp.raise_for_status()
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except httpx.TimeoutException as e:
            logger.warning(f"[LLM_JUDGE] Timeout calling LLM API: {e}")
            content = "{}"
        except httpx.HTTPStatusError as e:
            logger.warning(f"[LLM_JUDGE] HTTP error from LLM API: {e.response.status_code} - {e.response.text[:200]}")
            content = "{}"
        except Exception as e:
            logger.warning(f"[LLM_JUDGE] Failed to call LLM API: {e}")
            content = "{}"
        scores, reasons = _parse_scores_and_reasons(content)
        # NO CACHING - always compute fresh scores
        scored.update(scores)
        # Gentle pacing guard
        time.sleep(0.05)

    # Return fresh scores (no cache merge)
    return scored
