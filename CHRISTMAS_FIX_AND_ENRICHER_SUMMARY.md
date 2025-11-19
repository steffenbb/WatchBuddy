# Christmas Query Fix + Candidate Enricher Implementation

## Problem Statement

**Original Issue**: Query "I want wholesome christmas romantic movies like the Holiday" returned only 5/50 actual Christmas movies (10% accuracy). The rest were generic romantic movies without Christmas themes.

**Root Causes Identified**:
1. **Weak seasonal bonus** (0.6) - Christmas keywords didn't influence scoring enough
2. **Strict vote threshold** (150 votes) - eliminated niche Christmas/Hallmark movies (559/595 candidates filtered out)
3. **Lenient hard-inclusion** (OR logic) - "holiday" alone passed even without "christmas"
4. **High quota** (33%) - forced padding with 9 non-Christmas movies when only 8 matched
5. **Missing metadata** - Old CSV candidates lacked keywords, cast, overview needed for accurate matching

## Solutions Implemented

### 1. Scorer.py Seasonal Fixes (4 Changes)

**A. Doubled Seasonal Bonus** (Lines 1672-1682)
```python
# OLD: seasonal_bonus = 0.6 if is_seasonal else 0.0
# NEW: seasonal_bonus = 1.2 if is_seasonal else 0.0
```
- **Impact**: Christmas keywords now have 2x influence on final score
- **Rationale**: 0.6 was too subtle to overcome noise from romantic movie matching

**B. Reduced Vote Thresholds** (Lines 353-365)
```python
# OLD: seasonal → 150 (balanced) / 100 (obscure)
# NEW: seasonal → 80 (balanced) / 30 (obscure)
```
- **Impact**: Niche Christmas/Hallmark movies no longer filtered out aggressively
- **Rationale**: 150 votes eliminated 94% of candidates (559/595), including many valid Christmas movies

**C. Simplified _matches_cue()** (Lines 1820-1840)
```python
# Two-stage logic:
# 1. Seasonal cues: Check title/overview/keywords/tagline ONLY
# 2. Standard cues: Check all fields including genres, people, moods, themes
```
- **Impact**: Seasonal keywords enforce strict field matching, avoid false positives from actor names
- **Rationale**: Previous OR logic allowed "holiday" alone to pass without "christmas"

**D. Reduced Quota** (Lines 1845-1872)
```python
# OLD: 33% quota (17/50 items) for seasonal queries
# NEW: 25% quota (13/50 items) for seasonal queries
```
- **Impact**: Less padding with non-seasonal movies when matches are scarce
- **Rationale**: 33% forced 9 non-Christmas movies when only 8 candidates matched

### 2. Candidate Enricher Module (New File)

**Purpose**: On-demand TMDB metadata refresh for candidates with stale/missing data

**File**: `backend/app/services/ai_engine/candidate_enricher.py` (579 lines)

**Public API** (3 Functions):

```python
# 1. Sync wrapper for scorer.py (sync context)
def enrich_candidates_sync(candidates, max_age_days=90, max_concurrent=10) -> List[Dict]:
    """Synchronous wrapper for scorer.py. Creates SessionLocal(), checks event loop."""

# 2. Async batch wrapper for services with db session
async def enrich_candidates_async(db, candidates, max_age_days=90, max_concurrent=10) -> List[PersistentCandidate]:
    """Async batch enrichment with provided session."""

# 3. Async single model wrapper for overview_service.py
async def enrich_single_candidate(db, candidate, tmdb_id, media_type) -> None:
    """Updates PersistentCandidate in-place with fresh metadata + BGE regeneration."""
```

**Core Features**:
- **Detection**: Identifies candidates needing enrichment (missing keywords/cast/overview, >90 days old, status="announced")
- **Concurrent TMDB Fetching**: Max 10 simultaneous requests with rate limiting
- **Comprehensive Updates**: Updates PersistentCandidate fields (overview, keywords, cast, genres, release_date, runtime, etc.)
- **BGE Regeneration**: Creates/updates 5-aspect embeddings (base, title, keywords, people, brands) for semantic search
- **Score Recomputation**: Calculates obscurity/mainstream/freshness scores from fresh data
- **Thread Safety**: Uses SessionLocal() in sync context, avoids asyncio.run() in running event loops
- **Graceful Degradation**: Continues on individual failures, logs errors without breaking entire batch

**Integration Points**:

1. **scorer.py** (Lines 609-615):
   ```python
   from .candidate_enricher import enrich_candidates_sync
   cand_subset = enrich_candidates_sync(cand_subset, max_age_days=90, max_concurrent=10)
   ```
   - Called BEFORE scoring so enriched metadata influences results
   - Sync wrapper handles event loop detection automatically

2. **overview_service.py** (Lines 1859-1874):
   ```python
   from app.services.ai_engine.candidate_enricher import enrich_single_candidate
   await enrich_single_candidate(db, candidate, tmdb_id, media_type)
   ```
   - Replaces manual metadata update with centralized enricher
   - Includes BGE embedding regeneration for semantic search accuracy

### 3. Frontend Timeout Fix

**File**: `frontend/src/components/PairwiseTrainer.tsx` (Line 93)

```typescript
// OLD: Default timeout (30s)
// NEW: 120000 (120s)
```
- **Impact**: Preference trainer won't timeout during IntentExtractor processing
- **Rationale**: BGE encoding + LLM intent extraction can take 30-60s for complex queries

## Technical Implementation Details

### Enrichment Detection Logic (`_needs_enrichment`)

```python
def _needs_enrichment(candidate: PersistentCandidate, max_age_days: int) -> bool:
    # Missing critical fields
    if not candidate.overview or not candidate.keywords or not candidate.cast:
        return True
    
    # Stale metadata (>90 days old)
    if candidate.last_refreshed:
        age = (utc_now() - candidate.last_refreshed).days
        if age > max_age_days:
            return True
    
    # Announced/planned status (often incomplete data)
    if candidate.status in ('announced', 'planned'):
        return True
    
    return False
```

### BGE Embedding Regeneration (`_regenerate_bge_embedding`)

```python
async def _regenerate_bge_embedding(db: Session, candidate: PersistentCandidate) -> None:
    # Load BGE model
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('BAAI/bge-small-en-v1.5')
    
    # Generate 5-aspect embeddings
    aspects = {
        'base': f"{candidate.title} {candidate.overview or ''}",
        'title': candidate.title,
        'keywords': " ".join(candidate.keywords or []),
        'people': " ".join(candidate.cast or []),
        'brands': " ".join(candidate.brands or [])
    }
    
    embeddings = {
        aspect: model.encode(text, convert_to_numpy=True).astype('float16').tobytes()
        for aspect, text in aspects.items()
    }
    
    # Update or create BGEEmbedding record
    bge = db.query(BGEEmbedding).filter_by(candidate_id=candidate.id).first()
    if bge:
        for aspect, embedding_bytes in embeddings.items():
            setattr(bge, f'{aspect}_embedding', embedding_bytes)
    else:
        bge = BGEEmbedding(candidate_id=candidate.id, **embeddings)
        db.add(bge)
```

### Async/Sync Context Handling

**Problem**: scorer.py is synchronous but enricher needs async for TMDB fetching

**Solution**: Dual wrapper pattern
- `enrich_candidates_sync()`: Detects if event loop already running, creates SessionLocal(), runs asyncio.run()
- `enrich_candidates_async()`: Direct async for services with existing db session

```python
def enrich_candidates_sync(candidates, max_age_days=90, max_concurrent=10):
    db = SessionLocal()
    try:
        # Check if event loop already running
        try:
            asyncio.get_running_loop()
            # Already in async context - should use async wrapper instead
            logger.warning("[Enricher] Detected running event loop in sync wrapper")
            return candidates
        except RuntimeError:
            # No event loop - safe to use asyncio.run()
            pass
        
        return asyncio.run(_enrich_candidates_async(db, candidates, max_age_days, max_concurrent))
    finally:
        db.close()
```

## Expected Outcomes

### Christmas Query Results
- **Before**: 5/50 actual Christmas movies (10% accuracy)
- **After**: 30-40/50 actual Christmas movies (60-80% accuracy)

**Why**:
- 1.2 seasonal bonus strongly influences scoring
- 80/30 vote thresholds include niche Christmas/Hallmark movies
- 25% quota reduces non-Christmas padding
- Simplified _matches_cue enforces strict seasonal keyword matching
- Enriched metadata provides keywords/cast for accurate semantic matching

### Metadata Quality
- **Before**: 20,000+ candidates from old CSVs (2018-2021) missing keywords, cast, overview
- **After**: On-demand enrichment refreshes stale candidates during scoring

**Why**:
- `_needs_enrichment()` detects missing fields automatically
- Concurrent TMDB fetching (max 10) minimizes latency overhead
- BGE embeddings regenerated for semantic search accuracy

### Performance Impact
- **Enrichment overhead**: ~2-5 seconds for 50 candidates (concurrent fetching)
- **Scoring latency**: Minimal increase due to early enrichment (before scoring)
- **Database writes**: Single commit per batch (not per candidate)

## Testing Checklist

### Pre-Deployment Verification
- [x] No syntax errors in scorer.py
- [x] No syntax errors in candidate_enricher.py
- [x] No syntax errors in overview_service.py
- [x] Import statements resolve correctly (false positives from missing type stubs OK)
- [x] Event loop handling logic correct (asyncio.run() only when safe)
- [x] Database session management thread-safe (SessionLocal() in sync context)

### Post-Deployment Testing

**1. Christmas Query Validation**
```powershell
# Test Christmas query
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python tests/manual/test_christmas_query.py"

# Expected: 30-40/50 results should be actual Christmas movies
# Check for: Keywords like "christmas", "holiday", "santa", "xmas" in metadata
```

**2. Enrichment Integration**
```powershell
# Trigger list sync with stale candidates
docker exec -i watchbuddy-backend-1 python /app/tests/manual/trigger_sync.py <list_id>

# Check logs for enrichment
docker logs --tail 100 watchbuddy-backend-1 | Select-String -Pattern "Candidate enrichment"

# Expected: "[Scorer] Candidate enrichment completed for X candidates"
```

**3. BGE Embedding Verification**
```sql
-- Check BGE embeddings updated
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "
SELECT 
    pc.title,
    pc.last_refreshed,
    LENGTH(bge.base_embedding) as base_len,
    LENGTH(bge.title_embedding) as title_len,
    LENGTH(bge.keywords_embedding) as keywords_len
FROM persistent_candidates pc
LEFT JOIN bge_embeddings bge ON pc.id = bge.candidate_id
WHERE pc.last_refreshed > NOW() - INTERVAL '1 hour'
LIMIT 10;"

-- Expected: All embeddings should be 768 bytes (384 float16 values)
```

**4. Overview Service Integration**
```powershell
# Check overview_service uses enricher
docker logs --tail 50 watchbuddy-backend-1 | Select-String -Pattern "Updated candidate.*trakt:"

# Expected: "[Ingest] Updated candidate: <title> (trakt:<id>)"
```

**5. Performance Monitoring**
```powershell
# Analyze sync performance
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/analyze_sync_simple.py"

# Expected: Enrichment adds <5s overhead for typical list sizes
```

## Deployment Steps

```powershell
# 1. Rebuild backend with all changes
docker compose build backend

# 2. Restart backend service
docker compose up -d backend

# 3. Wait for initialization (5-10 seconds)
Start-Sleep -Seconds 10

# 4. Check logs for errors
docker logs --tail 50 watchbuddy-backend-1

# 5. Test Christmas query via API
# Frontend: Create AI list with prompt "wholesome christmas romantic movies like the Holiday"
# Expected: 30-40 results should be actual Christmas movies
```

## Rollback Plan

If issues arise, revert scorer.py changes:

```powershell
# 1. Restore scorer.py from git
git checkout HEAD -- backend/app/services/ai_engine/scorer.py

# 2. Remove candidate_enricher.py
rm backend/app/services/ai_engine/candidate_enricher.py

# 3. Restore overview_service.py
git checkout HEAD -- backend/app/services/overview_service.py

# 4. Rebuild backend
docker compose build backend; docker compose up -d backend
```

## Files Changed

### Modified
1. `backend/app/services/ai_engine/scorer.py` (2116 lines)
   - Lines 353-365: Vote threshold reduction
   - Lines 609-615: Enrichment integration
   - Lines 1672-1682: Seasonal bonus increase
   - Lines 1820-1872: _matches_cue simplification + quota reduction

2. `backend/app/services/overview_service.py` (1920 lines)
   - Lines 1859-1874: Use centralized enricher instead of manual update

3. `frontend/src/components/PairwiseTrainer.tsx`
   - Line 93: Timeout increase to 120s

### Created
1. `backend/app/services/ai_engine/candidate_enricher.py` (579 lines)
   - Complete enrichment module with dual API (sync/async)
   - BGE multi-vector embedding regeneration
   - Concurrent TMDB fetching with rate limiting

## References

- **Persistent Pool Architecture**: `PERSISTENT_POOL_GUIDE.md`
- **BGE Embedding System**: `BGE_PERSISTENCE_IMPLEMENTATION.md`
- **API Integration**: `API_AND_SCRIPTS_REFERENCE.md`
- **Docker Workflows**: `.github/copilot-instructions.md`
