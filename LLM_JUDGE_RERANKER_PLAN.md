# LLM Judge Reranker (Additive, Feature-Flagged)

## Goal
Use an LLM as a constrained judge to slightly adjust ordering within the top-100–300 candidates, improving alignment to complex prompts (nuanced tones, multi-faceted constraints) without harming latency or determinism for first render.

## Constraints
- Additive: Do not replace existing retrieval or personalization; only blend a small weight (≈0.15–0.25) into final score.
- Zero-friction startup: Disabled by default; no calls on first render when off.
- Cost control: Tight token budgets, batch processing, caching by `(query_hash,item_id)`.
- Safety: Deterministic settings (temperature=0), JSON-only output with strict schema validation and fallbacks.

## Where It Helps
- Hard verbal constraints: “thrillers without horror,” “no gore,” “family-friendly heist”.
- Fine-grained tone matching: “wholesome cozy christmas romance,” “bittersweet coming-of-age”.
- Entity disambiguation: brand/actor/director compliance verification when metadata is noisy.

## Candidate Scope
- Online: Top-100–150 after hybrid retrieval (BM25 + FAISS + BGE if enabled). Timeout-guarded; results cached.
- Nightly: Batch-judge top candidates for active lists to pre-warm cache.

## Prompt Schema (system + user)
- System: “You are a strict list curator. Score each item 0.0–1.0 based on how well it satisfies the criteria. Penalize explicit contradictions. Be conservative. Output JSON only.”
- User content includes:
  - Query summary: media type, facets (genres, people, studios), negatives, mood, season.
  - Scoring rubric with weights: relevance (0.5), constraint compliance (0.3), tone/mood fit (0.2). Tunable.
  - Items: array of concise item cards (title, year, media_type, genres[], keywords[], brief overview ≤ 280 chars, people/studio/network).

## JSON Output Schema
```json
{
  "scores": [
    {"id": 123, "score": 0.78, "reasons": ["meets cozy christmas romance", "no horror elements"]},
    {"id": 456, "score": 0.22, "reasons": ["slasher horror conflicts with 'no horror'"]}
  ]
}
```
- Strict: No other fields. Max 3 short reasons per item. Validate and drop items that fail schema or exceed limits.

## Ranking Blend
- FinalScore = BaseScore (existing) * 0.85 + LLMScore * 0.15 (tunable, capped to avoid large swings).
- Only applied to the top-100–150 to bound cost and variance.

## Caching & Telemetry
- Redis key: `llmjudge:{query_hash}:{item_id}` → float score, TTL 7–14 days.
- Log sampling: prompt tokens, output tokens, invalid JSON rate, correlation with click/watch if available.

## Providers
- Pluggable: OpenAI/Anthropic/Azure or local serving. Keys stored in Redis settings, disabled by default.
- Timeouts: 3–6s per batch; batch 25–50 items per call as needed.

## Failure & Fallback
- Any API error/timeout/invalid JSON → skip LLM scores for this batch and proceed with the base ranking.
- Guard against prompt injection by stripping item text and limiting to metadata + truncated overview.

## Rollout Plan
1. Implement stub module with schema + validator + caching; wrap behind `AI_LLM_JUDGE_ENABLED=false`.
2. Wire optional online rerank for top-100 with small weight; add telemetry.
3. Add nightly batch to warm cache for active lists.
4. Compare vs cross-encoder reranker on a test set; choose defaults and keep both as flags.
