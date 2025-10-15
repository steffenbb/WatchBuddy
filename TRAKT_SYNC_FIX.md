# Trakt Sync Fix for Custom Lists

## Issue
Custom lists were creating Trakt lists but not syncing items to them.

## Root Cause
List 67 "Romcom" was created before the Trakt list creation fix was deployed. It had:
- 200 items locally
- NO `trakt_list_id` in database
- Empty list on Trakt

## Flow Analysis

### Correct Flow (After Fix):
1. User creates custom list via `/api/lists/` POST
2. Backend creates `UserList` with `list_type='custom'`
3. Backend immediately calls `TraktClient.create_list()`
4. Backend stores `trakt_list_id` in database
5. Frontend calls `/api/lists/{id}/sync?force_full=true`
6. Sync service:
   - Fetches candidates from bulk provider
   - Creates `ListItem` records in database  
   - Detects `trakt_list_id` exists
   - Calls `trakt_client.sync_list_items()` to push items to Trakt ✓

### What Happened to List 67:
1. Created when Trakt list creation code was missing
2. Got 200 items locally via sync
3. But no `trakt_list_id` so items never pushed to Trakt
4. Trakt list was created later but items weren't synced

## Fix Applied

### 1. Code Changes (`backend/app/api/lists.py`)
```python
# After creating list, immediately create Trakt list
if l.list_type in ("custom", "manual"):
    logger.info(f"Attempting to create Trakt list for custom list {l.id}...")
    try:
        trakt = TraktClient(user_id=user_id)
        trakt_result = await trakt.create_list(
            name=l.title,
            description=f"Custom list managed by WatchBuddy (ID: {l.id})",
            privacy="private"
        )
        trakt_list_id = str(trakt_result.get("ids", {}).get("trakt"))
        # Store in database
        db.query(UserList).filter(UserList.id == l.id).update({"trakt_list_id": trakt_list_id})
        db.commit()
    except Exception as trakt_err:
        logger.warning(f"Failed to create Trakt list: {trakt_err}")
```

### 2. Backfill Script (`backend/app/scripts/backfill_trakt_lists.py`)
- Finds all custom lists without `trakt_list_id`
- Creates Trakt lists for them
- Stores `trakt_list_id` in database

### 3. Enhanced Logging
Added debug logging to track:
- List type detection
- Trakt list creation attempts
- Success/failure of Trakt API calls

## Manual Fix for List 67

```bash
# 1. Run backfill script (DONE)
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/backfill_trakt_lists.py"

# 2. Verify trakt_list_id was set (DONE)
# List 67 now has trakt_list_id = 32682156

# 3. Trigger sync to push items to Trakt
# User needs to: Click "Sync" button on list 67 in frontend
# OR manually trigger via API:
# POST /api/lists/67/sync?user_id=1&force_full=true
```

## Verification

### For Existing Lists:
```sql
-- Check all custom lists have Trakt IDs
SELECT id, title, list_type, trakt_list_id,
       (SELECT COUNT(*) FROM list_items WHERE smartlist_id = user_lists.id) as item_count
FROM user_lists
WHERE list_type IN ('custom', 'manual')
ORDER BY id DESC;
```

### For New Lists (Test):
1. Create new custom list via frontend
2. Check logs for "Attempting to create Trakt list"
3. Verify `trakt_list_id` is set in database
4. Check Trakt website - list should appear with items

## Prevention
- ✅ Auto-create Trakt lists for all custom/manual lists
- ✅ Sync items to Trakt immediately after local sync
- ✅ Enhanced logging for debugging
- ✅ Backfill script for fixing existing lists

## Status
- **List 66 "Comedy"**: ✅ Fixed (32682070, 200 items synced)
- **List 67 "Romcom"**: ⚠️ Trakt list created (32682156), needs item sync
- **Future lists**: ✅ Will work automatically
