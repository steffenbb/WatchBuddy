# Secondary BGE Embedding Index (No-Migration Design)

## Goals & Constraints
- No schema changes to existing persistent DB tables.
- No full re-embed; only incremental/nightly for top-N items.
- Keep first-render fast; heavy work runs in background with caching.
- Mix results with current FAISS (MiniLM) + BM25 + personalization; feature-flagged.

## Overview
Add a separate FAISS HNSW index powered by `BAAI/bge-small-en-v1.5` (via sentence-transformers). Build/update it nightly for a selected top-N item set (e.g., 5k–50k). At query-time, compute a BGE query embedding (optionally contextualized with mood/season cues), retrieve top-K from the BGE index, then RRF-fuse with existing BM25 + MiniLM FAISS results and apply the current scoring/personalization.

## Components
- Model: `BAAI/bge-small-en-v1.5` (small, strong for English). Stored on disk; loaded only when flag is on.
- Storage (no DB migrations):
  - `backend/app/data/bge_index/faiss_bge.index` – FAISS HNSW index (dim matches BGE).
  - `backend/app/data/bge_index/id_map.json` – item_id → vector position mapping + content hash for change detection.
  - `backend/app/data/bge_index/faiss_bge.lock` – cross-process lock for reads/writes.
- Index type: `IndexHNSWFlat` with `M` ≈ 32–64; `efConstruction` ≈ 200–400.
- Query-time `efSearch`: adaptive (e.g., base 400, up to 1000 for complex queries).

## Item Text for Embedding
Stable fields only (don’t encode ephemeral user state):
- Title (original/title), overview/plot, keywords/tags, genre names, key people (top-billed actors/director/creator), studio/network.
- Concatenate with separators and normalize (lowercase, punctuation spacing, diacritics stripped).

## Contextual Query Embedding (mood/season)
- Item vectors remain stable. Inject `mood`, `tone`, `season/holiday`, and key facets into the QUERY text before embedding (e.g., “mood: cozy; season: christmas; brand: a24; actor: tom hanks”).
- This keeps the index compact and avoids churn.

## Nightly Embedding Job
- Selection (top-N): combine recent popularity, active lists’ candidates, and items surfaced in last 24–48h. Cap to configurable N (e.g., 5k–50k).
- Incremental: compute a content hash from the concatenated text; only re-embed when hash changed or model version bumped.
- Concurrency: use dedicated lock file for FAISS writes; batch add vectors.
- Outputs: updated index + id_map.json; write-then-rename pattern for atomic swap.

## Query-Time Retrieval & Fusion
1. Existing: BM25, MiniLM FAISS.
2. New: BGE FAISS (top-K ≈ 300–800; adaptive by query complexity).
3. Fuse with RRF: weight BGE slightly higher for abstract queries; BM25 higher for exact phrases/brands.
4. Apply existing personalization (Trakt history, novelty/recency, tone/genre/penalties) in final score blend.

## Feature Flags & Config
- `AI_BGE_INDEX_ENABLED=false`
- `AI_BGE_TOPN_NIGHTLY=5000`
- `AI_BGE_TOPK_QUERY=600`
- `AI_BGE_WEIGHT_IN_RRF=1.1` (tunable)
- `AI_BGE_QUERY_CONTEXT_ENABLED=true` (adds mood/season to query text)
- `AI_BGE_MODEL_NAME=BAAI/bge-small-en-v1.5`

## Expected Gains
- Retrieval quality: +3–8% nDCG@10 over current hybrid for nuanced/abstract prompts (more robust semantics).
- With cross-encoder reranker (optional Phase B): +8–15% total uplift typical (BGE improves recall, reranker fixes order).
- Latency impact: negligible at first render (flag off or cached); BGE search costs are comparable to MiniLM FAISS for top-K.
- Storage: ~0.03–0.3 GB depending on N and HNSW params (e.g., 5k items ≈ <20 MB index incl. overhead).

## Failure Modes & Safeguards
- Index missing/cold: proceed without BGE; log at debug.
- Disk/corruption: rebuild on next nightly; keep last-known-good index for rollback.
- Model load failure: disable BGE at runtime and continue with existing pipeline.

## Implementation Steps
1. Create `ai_engine/bge_index.py` with lock-protected load/search/add operations; model lazy-load; file paths in `data/bge_index/`.
2. Add nightly Celery-beat task `build_bge_index_topN` (feature-flagged).
3. Wire query-time fusion: if enabled and index present, retrieve from BGE and RRF-fuse.
4. Telemetry: log component contributions and hit ratios to tune weights.
5. Optional: cache query embeddings and search results (short TTL) to trim p95 latency.

## Rollout
- Phase 1 (off by default): Ship code, verify nightly builder on small N (e.g., 1000). Observe telemetry.
- Phase 2: Enable on staging with N=5000, adjust RRF weights per prompt buckets.
- Phase 3: Enable in production; keep reranker behind a separate flag.
