# BGE Index Building Progress Logging

## Overview

Enhanced logging for BGE index building to track vector generation progress in real-time.

## New Logging Features

### 1. Initial Summary
```
[BGE] 🚀 Starting base embedding generation for 15,432 items
[BGE] Total candidates: 20,000 | Need updates: 15,432 | Up-to-date: 4,568
```

### 2. Base Embedding Progress (Every Batch)
```
[BGE Base] 📊 Progress: 64/15,432 (0.4%) - Embedding batch 1
[BGE Base] 📊 Progress: 128/15,432 (0.8%) - Embedding batch 2
[BGE Base] 📊 Progress: 192/15,432 (1.2%) - Embedding batch 3
...
[BGE Base] 📊 Progress: 7,680/15,432 (49.8%) - Embedding batch 120
...
[BGE Base] 📊 Progress: 15,432/15,432 (100.0%) - Embedding batch 241
```

### 3. Database Persistence (Every Batch)
```
[BGE Persist] 💾 Batch 1: Saved 64/64 to DB (0 failed)
[BGE Persist] 💾 Batch 2: Saved 64/64 to DB (0 failed)
[BGE Persist] 💾 Batch 120: Saved 63/64 to DB (1 failed)
```

### 4. Base Completion Summary
```
[BGE] ✅ Base embeddings complete: 15,432 vectors generated and saved
```

### 5. Labeled Embedding Progress
```
[BGE] 🏷️  Starting labeled embeddings generation (title/keywords/people/brands)
[BGE] Processing 15,432 items with labeled variants...
[BGE Labeled] 📊 Progress: 1,000/15,432 (6.5%) - 3,842 vectors generated, 158 skipped
[BGE Labeled] 📊 Progress: 2,000/15,432 (13.0%) - 7,684 vectors generated, 316 skipped
...
[BGE Labeled] 📊 Progress: 15,000/15,432 (97.2%) - 57,630 vectors generated, 2,370 skipped
```

### 6. Labeled Batch Persistence
```
[BGE Persist] 💾 Labeled batch: 256 vectors saved to DB
[BGE Persist] 💾 Labeled batch: 256 vectors saved to DB
...
[BGE Persist] 💾 Final labeled batch: 178 vectors saved to DB
```

### 7. Final Summary
```
[BGE] ✅ Labeled embeddings complete: 59,328 vectors generated, 2,472 skipped (up-to-date)
[BGE] 🎉 Index build finished: 15,432 base vectors + 59,328 labeled vectors = 74,760 total
[BGE] Summary: 20,000 candidates | 15,432 updated | 4,568 unchanged
```

## Complete Example Output

```
[2025-11-16 15:30:22] [BGE] 🚀 Starting base embedding generation for 15,432 items
[2025-11-16 15:30:22] [BGE] Total candidates: 20,000 | Need updates: 15,432 | Up-to-date: 4,568
[2025-11-16 15:30:25] [BGE Base] 📊 Progress: 64/15,432 (0.4%) - Embedding batch 1
[2025-11-16 15:30:25] [BGE Persist] 💾 Batch 1: Saved 64/64 to DB (0 failed)
[2025-11-16 15:30:28] [BGE Base] 📊 Progress: 128/15,432 (0.8%) - Embedding batch 2
[2025-11-16 15:30:28] [BGE Persist] 💾 Batch 2: Saved 64/64 to DB (0 failed)
...
[2025-11-16 15:35:42] [BGE Base] 📊 Progress: 15,432/15,432 (100.0%) - Embedding batch 241
[2025-11-16 15:35:42] [BGE Persist] 💾 Batch 241: Saved 56/56 to DB (0 failed)
[2025-11-16 15:35:42] [BGE] ✅ Base embeddings complete: 15,432 vectors generated and saved

[2025-11-16 15:35:42] [BGE] 🏷️  Starting labeled embeddings generation (title/keywords/people/brands)
[2025-11-16 15:35:42] [BGE] Processing 15,432 items with labeled variants...
[2025-11-16 15:36:15] [BGE Labeled] 📊 Progress: 1,000/15,432 (6.5%) - 3,842 vectors generated, 158 skipped
[2025-11-16 15:36:45] [BGE Labeled] 📊 Progress: 2,000/15,432 (13.0%) - 7,684 vectors generated, 316 skipped
[2025-11-16 15:37:18] [BGE Labeled] 📊 Progress: 3,000/15,432 (19.4%) - 11,526 vectors generated, 474 skipped
...
[2025-11-16 15:45:12] [BGE Labeled] 📊 Progress: 15,000/15,432 (97.2%) - 57,630 vectors generated, 2,370 skipped
[2025-11-16 15:45:18] [BGE Persist] 💾 Final labeled batch: 178 vectors saved to DB

[2025-11-16 15:45:18] [BGE] ✅ Labeled embeddings complete: 59,328 vectors generated, 2,472 skipped (up-to-date)
[2025-11-16 15:45:18] [BGE] 🎉 Index build finished: 15,432 base vectors + 59,328 labeled vectors = 74,760 total
[2025-11-16 15:45:18] [BGE] Summary: 20,000 candidates | 15,432 updated | 4,568 unchanged
```

## Monitoring Commands

### Watch Logs During Build
```powershell
# Follow BGE build logs in real-time
docker logs -f watchbuddy-celery-1 | Select-String -Pattern "BGE"

# Filter only progress updates
docker logs -f watchbuddy-celery-1 | Select-String -Pattern "BGE.*Progress"

# Filter only completion messages
docker logs -f watchbuddy-celery-1 | Select-String -Pattern "BGE.*complete"
```

### Check Last Build Status
```powershell
# Get last 100 BGE-related log lines
docker logs --tail 100 watchbuddy-celery-1 | Select-String -Pattern "BGE"
```

## Progress Tracking Details

### Base Embeddings
- **Frequency**: Every batch (64 items)
- **Shows**: Current count, total count, percentage, batch number
- **Timing**: ~2-3 seconds per batch (depending on hardware)

### Labeled Embeddings
- **Frequency**: Every 1,000 items processed
- **Shows**: Items processed, vectors generated, vectors skipped
- **Details**: 
  - Generated = new embeddings computed
  - Skipped = already up-to-date (hash matches)

### Database Persistence
- **Base**: Every batch shows success/fail count
- **Labeled**: Every 256 vectors (batch commit)

## Time Estimates

For a typical full build of 20,000 candidates:

| Phase | Items | Time (approx) | Vectors |
|-------|-------|---------------|---------|
| Base Embeddings | 20,000 | ~10 minutes | 20,000 |
| Labeled Embeddings | 20,000 | ~15 minutes | ~76,000 |
| **Total** | **20,000** | **~25 minutes** | **~96,000** |

*Times vary based on:*
- CPU/GPU performance
- Network latency (model loading)
- Database I/O speed
- Number of up-to-date embeddings (skipped items are instant)

## Benefits

1. **Progress Visibility**: Know exactly how far along the build is
2. **Time Estimation**: Calculate remaining time based on progress percentage
3. **Error Detection**: Immediate visibility if batches start failing
4. **Performance Monitoring**: Track embeddings-per-second rate
5. **Debugging**: Detailed logs help identify bottlenecks

## Emoji Legend

- 🚀 Starting/initialization
- 📊 Progress updates
- 💾 Database operations
- ✅ Successful completion
- ❌ Errors/failures
- 🏷️ Labeled embeddings phase
- 🎉 Final completion
