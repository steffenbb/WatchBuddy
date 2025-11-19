# Error Handling & Logging Improvements

## Overview

Comprehensive error handling, logging, and diagnostics improvements across BGE persistence, recovery, and pairwise enrichment systems.

## Changes Made

### 1. BGE Recovery Module (`backend/app/services/bge_recovery.py`)

**Enhanced Error Handling:**
- ✅ Try-catch around BGE index initialization with detailed error reporting
- ✅ Database query error handling with graceful degradation
- ✅ Per-embedding dimension validation (384-dim check)
- ✅ Individual error tracking per label (title, keywords, people, brands)
- ✅ Graceful database session cleanup in finally block
- ✅ Returns error dict instead of raising on critical failures

**Improved Logging:**
- ✅ Progress percentage tracking (`50.2% complete`)
- ✅ Summary statistics logged at completion
- ✅ Emoji indicators for status (✅ success, ❌ error, ⚠️ warning)
- ✅ Separate counters: `vectors_added`, `missing_candidates`, `errors_count`
- ✅ Debug-level logs for expected issues (missing candidates)
- ✅ Error-level logs with exception traces for critical failures

**Return Stats Enhanced:**
```python
{
    "status": "success",
    "total_items": 20000,
    "base_vectors": 19850,
    "labeled_vectors": 75400,
    "total_vectors": 95250,
    "missing_candidates": 120,  # NEW
    "errors": 30                # NEW
}
```

### 2. BGE Persistence in Index Builder (`backend/app/services/tasks.py`)

**Base Embeddings Persistence:**
- ✅ Separate counters: `persist_success`, `persist_fail`
- ✅ Dimension validation before serialization
- ✅ Detailed logging every ~10 batches
- ✅ Graceful handling of missing candidates
- ✅ Rollback on commit failure with error logging

**Labeled Embeddings Persistence:**
- ✅ Dimension validation per label
- ✅ Separate debug logging for missing BGEEmbedding rows
- ✅ Enhanced commit logging with batch counts
- ✅ Error tracking for failed label updates

**Logging Pattern:**
```python
logger.info(f"[BGE Persist] Batch 10: 64 saved, 0 failed")
logger.debug(f"[BGE Persist] Committed batch of 256 labeled embeddings")
logger.error(f"[BGE Persist] ❌ Commit failed for batch 15: {e}")
```

### 3. ItemLLMProfile Enrichment (`backend/app/services/ai_engine/pairwise.py`)

**Enhanced Enrichment:**
- ✅ Track which fields were enriched from cache
- ✅ Debug logging shows enriched field list
- ✅ Separate ImportError handling (service not available)
- ✅ Graceful degradation on enrichment failures

**Logging Pattern:**
```python
logger.debug(f"[Pairwise] Enriched 5 fields from ItemLLMProfile: ['genres', 'keywords', 'overview']...")
logger.warning("[Pairwise] ItemProfileService not available - skipping enrichment")
```

### 4. Expanded FAISS Index Diagnostic Tool (`backend/app/scripts/check_faiss_index.py`)

**New Comprehensive Diagnostic Script:**

#### MiniLM Index Check
- File existence and sizes (MB/KB)
- Vector count and mapping size
- Dimension detection
- Load success/failure detection

#### BGE Index Check  
- Base directory and file verification
- Multi-vector mapping parsing
- **Vectors by label** breakdown:
  - `base`: 19,850 vectors
  - `title`: 18,900 vectors  
  - `keywords`: 17,600 vectors
  - `people`: 19,200 vectors
  - `brands`: 19,750 vectors
- Total items and total vectors count

#### BGE Embeddings Database Check
- Total row count
- Movies vs Shows breakdown
- **Embedding coverage by type:**
  - Base embeddings: 99.2%
  - Title: 94.5%
  - Keywords: 88.0%
  - People: 96.0%
  - Brands: 98.8%
- Average vectors per item
- Model version breakdown

#### Persistent Candidates Check
- Total candidates count
- MiniLM embedding coverage percentage
- Media type distribution

#### Redis Settings Check
- BGE index enabled flag
- BGE index size
- Last build timestamp (human-readable)

**Usage:**
```bash
# Inside backend container
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/check_faiss_index.py"
```

**Example Output:**
```
🔍 WATCHBUDDY FAISS INDEX DIAGNOSTIC TOOL
============================================================

📊 MiniLM FAISS Index (Standard Embeddings)
============================================================
Index file:   /data/ai/faiss_index.bin
  Exists: True
  Size: 156.32 MB

Mapping file: /data/ai/faiss_map.json
  Exists: True
  Size: 2.47 KB

✅ Index loaded successfully
  Vectors: 20,000
  Mapping entries: 20,000
  Vector dimension: 384

============================================================
🔍 BGE FAISS Index (Multi-Vector Semantic)
============================================================
Base directory: /data/ai/bge_index
  Exists: True

Index file: /data/ai/bge_index/faiss_bge.index
  Exists: True
  Size: 178.45 MB

Mapping file: /data/ai/bge_index/id_map.json
  Exists: True
  Size: 8.12 KB

✅ Mapping loaded successfully
  Total items: 19,850
  Total vectors: 95,250

  Vectors by label:
    base: 19,850
    brands: 19,750
    keywords: 17,600
    people: 19,200
    title: 18,900

============================================================
💾 BGE Embeddings Database
============================================================
Total BGEEmbedding rows: 19,850

  Movies: 14,200
  Shows: 5,650

Embedding coverage:
  Base (required): 19,850 (100.0%)
  Title: 18,900 (95.2%)
  Keywords: 17,600 (88.7%)
  People: 19,200 (96.7%)
  Brands: 19,750 (99.5%)

Total stored vectors: 95,300
Average vectors per item: 4.80

Model versions:
  BAAI/bge-small-en-v1.5: 19,850

============================================================
📦 Persistent Candidates (MiniLM Embeddings)
============================================================
Total candidates: 20,000
  With MiniLM embeddings: 20,000 (100.0%)
  Without embeddings: 0

By media type:
  Movies: 14,500
  Shows: 5,500

============================================================
⚙️  Redis Settings
============================================================
BGE Index Enabled: true
BGE Index Size: 19850
Last Build: 2025-11-16 14:23:45

============================================================
📋 SUMMARY
============================================================
✅ All systems operational
============================================================
```

## Error Handling Patterns

### 1. Graceful Degradation
```python
# Instead of failing completely, log and continue
try:
    process_item(item)
except Exception as e:
    logger.error(f"Failed to process item: {e}")
    errors_count += 1
    continue  # Keep processing other items
```

### 2. Dimension Validation
```python
# Validate before adding to FAISS
if len(vec_array) != 384:
    logger.warning(f"Invalid embedding dim {len(vec_array)}")
    continue
```

### 3. Database Cleanup
```python
finally:
    try:
        db.close()
        logger.debug("Database session closed")
    except Exception as e:
        logger.error(f"Failed to close database: {e}")
```

### 4. Return Instead of Raise
```python
# Better for API endpoints - return structured error
except Exception as e:
    logger.error(f"CRITICAL FAILURE: {e}", exc_info=True)
    return {
        "status": "error",
        "reason": "critical_failure",
        "error": str(e),
        "error_type": type(e).__name__
    }
```

## Logging Best Practices

### Log Levels Used
- **DEBUG**: Expected issues, verbose progress, field enrichment details
- **INFO**: Normal operations, progress checkpoints, success confirmations
- **WARNING**: Recoverable issues, missing data, failed Redis updates
- **ERROR**: Critical failures, database errors, unrecoverable issues

### Prefixes for Clarity
- `[BGE Recovery]` - Recovery operations
- `[BGE Persist]` - Database persistence during index builds
- `[Pairwise]` - ItemLLMProfile enrichment

### Emoji Indicators
- ✅ Success operations
- ❌ Critical errors
- ⚠️ Warnings/non-critical issues
- 🔍 Search/diagnostic operations
- 📊 Statistics/reporting
- 💾 Database operations

## Benefits

1. **Production Readiness**: Comprehensive error tracking and recovery
2. **Debuggability**: Detailed logs show exactly where failures occur
3. **Monitoring**: Stats tracking enables alerting on error thresholds
4. **Resilience**: Systems continue operating despite individual failures
5. **Diagnostics**: Single script to verify entire FAISS ecosystem health

## Testing Recommendations

### 1. Test BGE Recovery with Errors
```bash
# Corrupt a few DB embeddings, verify recovery continues
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python -c '
from app.services.bge_recovery import rebuild_bge_index_from_db
result = rebuild_bge_index_from_db()
print(result)
'"
```

### 2. Test Persistence Error Handling
```bash
# Check logs during normal BGE index build
docker logs -f watchbuddy-celery-1 | Select-String -Pattern "BGE Persist"
```

### 3. Run Full Diagnostic
```bash
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/check_faiss_index.py"
```

### 4. Verify ItemLLMProfile Enrichment
```bash
# Check pairwise logs during training session
docker logs -f watchbuddy-backend-1 | Select-String -Pattern "Pairwise.*Enriched"
```

## Future Enhancements

- [ ] Prometheus metrics export from error counters
- [ ] Alert thresholds (e.g., >5% error rate)
- [ ] Automatic retry logic for transient failures
- [ ] Health check endpoint using diagnostic script results
- [ ] Historical error tracking in database
