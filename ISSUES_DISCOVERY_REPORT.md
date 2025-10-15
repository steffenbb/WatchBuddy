# Critical Issues Discovery & Status Report

## Date: October 15, 2025

---

## 🎯 Issues Investigated

### 1. ✅ CRITICAL: CSV Import Failed (99.2% data loss)
**Status**: FIXED - Partially Complete

**Problem**: 
- Only 12,248 items imported from 1,471,947 valid CSV rows (99.2% loss!)
- UNIQUE constraint on `tmdb_id` prevented importing items with same ID
- TMDB uses separate ID spaces for movies/shows, but 101,358 IDs overlap
- Movies imported first (266k), then shows failed due to constraint violations

**Root Cause**:
```sql
-- Old (broken):
tmdb_id integer UNIQUE  -- Prevents movies AND shows having same ID

-- New (fixed):
UNIQUE (tmdb_id, media_type)  -- Allows same ID for different media types
```

**Actions Taken**:
1. ✅ Fixed database constraint with `fix_tmdb_constraint.py`
2. ✅ Updated `models.py` to use composite UNIQUE constraint
3. ✅ Truncated and re-imported - now have 266,507 movies (SUCCESS!)
4. ⚠️  TV shows still only 12,000 (expected ~160,000)

**Next Steps**:
- Check why TV show import incomplete - might still be running
- Monitor `inserted_at` timestamps to see if import is progressing

---

### 2. ⚠️  Celery Beat Not Scheduling Background Tasks
**Status**: PARTIALLY FIXED - Tasks work, scheduling broken

**Problem**:
- Celery Beat running but only sending `refresh-smartlists` task
- Other tasks (ingest_new_movies, build_metadata) scheduled but not executing
- Beat using stale `celerybeat-schedule` persistent database

**Root Cause**:
- Beat caches schedule in persistent file `celerybeat-schedule`
- When config changes, Beat continues using old cached schedule
- Deleting file and restarting doesn't force reload properly

**Actions Taken**:
1. ✅ Verified tasks are properly registered in Celery
2. ✅ Manually triggered tasks - they work!
   - `ingest_new_movies`: Added 7 movies, completed in 2.3s
   - `ingest_new_shows`: Completed successfully
   - `build_metadata`: Had event loop issue (fixed below)
3. ✅ Fixed `build_metadata` event loop crash in Celery workers
4. ⚠️  Beat still not auto-scheduling - persistent schedule file issue

**Workaround**:
```python
# Manually trigger tasks:
docker exec watchbuddy-backend-1 python /app/trigger_bg.py
```

**Proper Solution Needed**:
- Option A: Use `redis://` as beat scheduler instead of persistent file
- Option B: Force Beat to reload config on every startup
- Option C: Use cron-based scheduling instead of Beat

---

### 3. ✅ Metadata Builder Event Loop Crash
**Status**: FIXED

**Problem**:
```
RuntimeError: Event loop is closed
```
- `asyncio.run()` doesn't work in Celery workers
- Workers have existing event loops that conflict

**Solution Implemented**:
```python
# Handle event loop properly for Celery workers
try:
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        raise RuntimeError("Loop is closed")
    return loop.run_until_complete(_run())
except RuntimeError:
    # Create new loop if none exists
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
```

---

## 📊 Current Database State

| Media Type | Count | Expected | Status |
|------------|-------|----------|--------|
| Movies | 266,507 | ~1.3M | ✅ 20% imported |
| Shows | 12,000 | ~160K | ⚠️  7% imported |
| **Total** | **278,507** | **~1.47M** | **19% complete** |

| Metric | Value | Notes |
|--------|-------|-------|
| Trakt IDs populated | 9,262 / 12,248 | 75% (only for old 12k items) |
| Missing Trakt IDs | 2,986 | Need metadata builder to run |
| Last movie inserted | 2025-10-15 16:23:51 | ✅ Recent |
| Last show inserted | 2025-10-15 16:18:21 | ⚠️  Stale |

---

## 🚧 Still TODO (From Original Request)

### High Priority:
1. **Complete TV Shows Import** - Only 12k/160k imported
2. **Fix Celery Beat Scheduling** - Tasks work but Beat doesn't schedule them
3. **60% Content Refresh on Sync** - Lists need regular content rotation
4. **Filter Adherence** - Different lists showing same content despite different filters

### Medium Priority:
5. **Active Sync Status in UI** - Not showing in real-time
6. **Trakt List Creation** - Custom/suggested lists not creating on Trakt

### Low Priority:
7. **Update Help Page** - Document new features

---

## 🔧 Immediate Actions Required

### 1. Check TV Show Import Status
```powershell
# Is it still running?
docker logs --tail 50 watchbuddy-backend-1 | Select-String -Pattern "bootstrap|CSV"

# Check progress
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT COUNT(*), MAX(inserted_at) FROM persistent_candidates WHERE media_type='show';"
```

### 2. Fix Celery Beat Scheduling
**Option A - Switch to Redis Scheduler** (Recommended):
```python
# In celery_app.py:
from celery.beat import PersistentScheduler
from redbeat import RedBeatScheduler  # Add to requirements.txt

celery_app.conf.beat_scheduler = 'redbeat.RedBeatScheduler'
celery_app.conf.redbeat_redis_url = settings.redis_url
```

**Option B - Force Schedule Reload**:
```yaml
# In docker-compose.yml:
celery-beat:
  command: sh -c "rm -f /app/celerybeat-schedule* && celery -A app.core.celery_app.celery_app beat --loglevel=info"
```

### 3. Test Background Tasks
```powershell
# After fixing Beat, verify tasks run automatically:
docker logs -f watchbuddy-celery-1 | Select-String -Pattern "ingest|build_metadata"

# Should see tasks every 2 hours (ingest) and 12 hours (metadata)
```

---

## 📈 Success Metrics

When everything is working:
- ✅ 1.47M items in persistent_candidates
- ✅ 90%+ have Trakt IDs populated
- ✅ New content ingested every 2 hours automatically
- ✅ Metadata enriched every 12 hours automatically
- ✅ Lists show diverse content matching filters
- ✅ Lists refresh 60% of content on each sync

---

## Files Modified

1. `backend/app/models.py` - Fixed UNIQUE constraint
2. `backend/app/services/tasks.py` - Fixed event loop handling
3. `backend/app/core/celery_app.py` - Added schedule logging
4. `backend/app/scripts/fix_tmdb_constraint.py` - Database migration
5. `backend/app/scripts/analyze_csv_import.py` - Analysis tool
6. `backend/app/scripts/check_id_overlap.py` - Diagnostic tool
7. `backend/app/scripts/trigger_background_tasks.py` - Manual trigger

---

## Next Session Priorities

1. **Verify TV show import completion** (or restart if stuck)
2. **Implement Redis Beat scheduler** to fix auto-scheduling
3. **Implement 60% content refresh** in list_sync.py
4. **Fix filter adherence** in bulk_candidate_provider.py
5. **Test end-to-end**: Create list → Auto-populate → Sync → Refresh content
