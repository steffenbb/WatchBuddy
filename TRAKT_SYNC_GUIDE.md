# Trakt List Synchronization - Implementation Guide

## Overview

WatchBuddy now fully synchronizes SmartLists with Trakt.tv, creating, updating, and deleting lists automatically with the description "Created and managed by WatchBuddy".

---

## ✅ Features Implemented

### 1. **Trakt List Creation on SmartList Creation**
When a user creates a new SmartList in WatchBuddy:
- ✅ Automatically creates a corresponding private list on Trakt
- ✅ Stores the Trakt list ID in `UserList.trakt_list_id`
- ✅ Sets description to "Created and managed by WatchBuddy"
- ✅ Privacy set to "private" by default
- ✅ Adds initial items to the Trakt list
- ✅ Sends notifications about Trakt list creation status

### 2. **Trakt List Updates on Sync**
When a SmartList is synced:
- ✅ Updates the Trakt list with current items
- ✅ Adds new items that aren't on Trakt
- ✅ Removes items that were deleted locally
- ✅ Updates the list title if it changed (via dynamic title generation)
- ✅ Maintains description as "Created and managed by WatchBuddy"
- ✅ Sends notifications about sync statistics (+X -Y items)

### 3. **Trakt List Deletion on UI Deletion**
When a user deletes a SmartList:
- ✅ Automatically deletes the corresponding Trakt list
- ✅ Handles cases where Trakt deletion fails gracefully
- ✅ Sends notifications about deletion status

---

## Architecture

### Database Schema

**UserList Model** (new field):
```python
trakt_list_id = Column(String, nullable=True, index=True)  # Stores Trakt list ID
```

**Migration** (auto-applied on startup):
```sql
ALTER TABLE user_lists ADD COLUMN IF NOT EXISTS trakt_list_id varchar(255) NULL;
CREATE INDEX IF NOT EXISTS idx_user_lists_trakt_list_id ON user_lists (trakt_list_id);
```

### TraktClient Methods

**List Management:**
```python
async def create_list(name, description, privacy="private") -> Dict
async def update_list(trakt_list_id, name=None, description=None) -> Dict
async def delete_list(trakt_list_id) -> bool
```

**Item Management:**
```python
async def add_items_to_list(trakt_list_id, items) -> Dict
async def remove_items_from_list(trakt_list_id, items) -> Dict
async def get_list_items(trakt_list_id) -> List[Dict]
async def sync_list_items(trakt_list_id, desired_items) -> Dict
```

---

## Workflows

### SmartList Creation Flow

```
User Creates SmartList
    ↓
1. Create UserList in database
    ↓
2. Call TraktClient.create_list()
    ↓
3. Store trakt_list_id in UserList
    ↓
4. Add items to database
    ↓
5. Call TraktClient.add_items_to_list()
    ↓
6. Send notifications to user
```

**Code Location:** `backend/app/api/smartlists.py` (lines ~240-280)

**Error Handling:**
- If Trakt list creation fails, the local list is still created
- User receives warning notification: "List created locally, but Trakt sync failed"
- List can be manually synced later

---

### List Sync Flow

```
User Triggers Sync (or Auto-sync)
    ↓
1. Fetch candidates from persistent DB
    ↓
2. Score and rank items
    ↓
3. Update database with new items
    ↓
4. If trakt_list_id exists:
    ├── Update title on Trakt (if changed)
    ├── Call sync_list_items()
    │   ├── Get current Trakt items
    │   ├── Calculate diff (add/remove)
    │   ├── Add missing items
    │   └── Remove extra items
    └── Send sync statistics notification
```

**Code Location:** `backend/app/services/list_sync.py` (lines ~220-270)

**Sync Statistics:**
- `added`: Number of items added to Trakt
- `removed`: Number of items removed from Trakt
- `unchanged`: Number of items already in sync

---

### List Deletion Flow

```
User Deletes SmartList
    ↓
1. Get UserList to check trakt_list_id
    ↓
2. If trakt_list_id exists:
    └── Call TraktClient.delete_list()
    ↓
3. Delete from local database
    ↓
4. Send deletion notifications
```

**Code Location:** `backend/app/api/lists.py` (lines ~201-245)

**Error Handling:**
- If Trakt deletion fails, local list is still deleted
- User receives warning: "List deleted locally, but Trakt deletion failed"

---

## API Reference

### TraktClient.create_list()

**Purpose:** Create a new list on Trakt

**Parameters:**
- `name` (str): List name
- `description` (str): List description (default: "Created and managed by WatchBuddy")
- `privacy` (str): "private", "friends", or "public" (default: "private")

**Returns:**
```python
{
    "name": "My List",
    "description": "Created and managed by WatchBuddy",
    "privacy": "private",
    "ids": {
        "trakt": 12345678,
        "slug": "my-list"
    },
    # ... other Trakt metadata
}
```

**Example:**
```python
trakt_client = TraktClient(user_id=1)
result = await trakt_client.create_list(
    name="Weekend Action Movies",
    description="Created and managed by WatchBuddy",
    privacy="private"
)
trakt_list_id = result["ids"]["trakt"]
```

---

### TraktClient.sync_list_items()

**Purpose:** Synchronize Trakt list to match desired items

**Parameters:**
- `trakt_list_id` (str): Trakt list ID
- `desired_items` (List[Dict]): Items that should be in the list
  - Each item: `{"trakt_id": int, "media_type": "movie"|"show"}`

**Returns:**
```python
{
    "added": 5,      # Items added to Trakt
    "removed": 3,    # Items removed from Trakt
    "unchanged": 12  # Items already in sync
}
```

**Example:**
```python
items = [
    {"trakt_id": 123, "media_type": "movie"},
    {"trakt_id": 456, "media_type": "show"}
]
stats = await trakt_client.sync_list_items("12345678", items)
# Returns: {"added": 2, "removed": 0, "unchanged": 0}
```

---

## Configuration

### Trakt API Requirements

**Required Settings (in Redis):**
- `settings:global:trakt_client_id` - Trakt app client ID
- `settings:global:trakt_client_secret` - Trakt app client secret
- `settings:user:{user_id}:trakt_access_token` - User access token

### List Settings

**Default Values:**
- **Privacy:** `private` (not visible to other Trakt users)
- **Description:** `"Created and managed by WatchBuddy"` (unchangeable)
- **Display Numbers:** `true` (shows ranking numbers)
- **Allow Comments:** `false` (comments disabled)

---

## Notifications

### Creation
- ✅ Success: "Created list '{title}' on Trakt"
- ⚠️ Warning: "List created locally, but Trakt sync failed"

### Sync
- ℹ️ Info: "Syncing '{title}'..."
- ✅ Success: "Sync (auto/manual) for '{title}': X updated, Y removed, Z total"
- ℹ️ Info: "Synced to Trakt: +X -Y"
- ⚠️ Warning: "List synced locally, but Trakt sync failed"

### Deletion
- ℹ️ Info: "Deleted '{title}' from Trakt"
- ℹ️ Info: "List {id} deleted."
- ⚠️ Warning: "List deleted locally, but Trakt deletion failed"

---

## Error Handling

### Graceful Degradation

All Trakt operations use graceful degradation:
1. **Primary operation** (local DB) always completes
2. **Trakt sync** is attempted as secondary operation
3. **Failures are logged** and user is notified
4. **Local data is never lost** due to Trakt failures

### Common Failure Scenarios

**1. Authentication Errors**
- User's Trakt token expired
- Notification: "Trakt access token expired. Please reauthorize."
- List operations continue locally

**2. Network Errors**
- Trakt API unreachable
- Notification: "Trakt sync failed" (warning)
- Can retry on next sync

**3. Rate Limiting**
- Trakt rate limit exceeded
- Automatic exponential backoff with retries
- Notification: "Trakt API rate limit exceeded. Please wait."

**4. Missing Trakt Items**
- Some items don't exist on Trakt (TMDB-only content)
- Items are skipped silently
- Logs show: "Item {id} not found on Trakt"

---

## Testing

### Manual Testing

**1. Test List Creation:**
```powershell
# Create a list via UI and verify on Trakt
# Check browser console for "Created Trakt list with ID: X"
docker logs watchbuddy-backend-1 | grep "Created Trakt list"
```

**2. Test List Sync:**
```powershell
# Sync a list and check Trakt for updates
docker logs watchbuddy-backend-1 | grep "Trakt sync:"
```

**3. Test List Deletion:**
```powershell
# Delete a list in UI and verify it's gone from Trakt
docker logs watchbuddy-backend-1 | grep "Deleted Trakt list"
```

### Database Inspection

```powershell
# Check which lists have Trakt IDs
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT id, title, trakt_list_id FROM user_lists;"

# Check list items
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT smartlist_id, trakt_id, media_type FROM list_items LIMIT 10;"
```

---

## Performance

### Caching
- Trakt API responses cached in Redis (5 minutes TTL)
- User ratings cached per scoring session
- List items fetched once per sync

### Rate Limiting
- Built-in exponential backoff for Trakt API
- Automatic retry on 429 (rate limit) errors
- Max 5 retries before giving up

### Batch Operations
- Items added/removed in bulk (not one-by-one)
- Single API call to sync entire list
- Efficient diff calculation (O(n) complexity)

---

## Migration Notes

### Existing Lists

Lists created before this feature will NOT have `trakt_list_id`:
- They continue to work normally in WatchBuddy
- On next sync, they can optionally be pushed to Trakt (future enhancement)
- No data loss or errors for existing lists

### Manual Migration (Optional)

To create Trakt lists for existing WatchBuddy lists:

```python
# Run this script in backend container:
from app.core.database import SessionLocal
from app.models import UserList, ListItem
from app.services.trakt_client import TraktClient
import asyncio

async def migrate_list(list_id: int):
    db = SessionLocal()
    user_list = db.query(UserList).filter(UserList.id == list_id).first()
    
    if not user_list or user_list.trakt_list_id:
        return
    
    trakt_client = TraktClient(user_id=user_list.user_id)
    
    # Create Trakt list
    result = await trakt_client.create_list(
        name=user_list.title,
        description="Created and managed by WatchBuddy"
    )
    
    trakt_list_id = result["ids"]["trakt"]
    user_list.trakt_list_id = str(trakt_list_id)
    db.commit()
    
    # Add items
    items = db.query(ListItem).filter(ListItem.smartlist_id == list_id).all()
    trakt_items = [{"trakt_id": item.trakt_id, "media_type": item.media_type} for item in items if item.trakt_id]
    
    if trakt_items:
        await trakt_client.add_items_to_list(str(trakt_list_id), trakt_items)
    
    db.close()
    print(f"Migrated list {list_id} to Trakt list {trakt_list_id}")

# Run for specific list
asyncio.run(migrate_list(1))
```

---

## Troubleshooting

### "Trakt sync failed" Warnings

**Causes:**
1. User not authenticated with Trakt
2. Network connectivity issues
3. Trakt API temporarily unavailable
4. Invalid Trakt list ID

**Solutions:**
1. Check Trakt authentication in Settings
2. Retry sync operation
3. Check backend logs for detailed error
4. Verify Trakt list still exists on Trakt.tv

### Items Missing on Trakt

**Causes:**
1. Items only exist in TMDB (no Trakt mapping)
2. Items were filtered by Trakt's validation

**Solutions:**
- Items will appear in WatchBuddy but not on Trakt
- This is expected behavior (graceful degradation)
- Check logs: "Item {id} not found on Trakt"

### Duplicate Lists on Trakt

**Causes:**
- List was manually created on Trakt with same name
- Database lost trakt_list_id (rare)

**Solutions:**
1. Delete duplicate from Trakt manually
2. Or delete WatchBuddy list and recreate

---

## Future Enhancements

### Potential Features:
1. **Bi-directional Sync:** Import lists from Trakt to WatchBuddy
2. **Public Lists:** Option to make lists public on Trakt
3. **List Comments:** Enable Trakt comments for sharing
4. **Collaborative Lists:** Share lists with friends
5. **List Rankings:** Sync item order/ranking to Trakt
6. **Custom Descriptions:** Allow users to customize list descriptions
7. **Automatic Migration:** Auto-create Trakt lists for existing lists on first sync

---

## Security & Privacy

### Privacy by Default
- All lists created as **private** on Trakt
- Only the authenticated user can see their lists
- No sharing without explicit user action

### Data Protection
- Trakt credentials stored securely in Redis
- OAuth tokens refreshed automatically
- No passwords stored (OAuth flow only)

### User Control
- Users can delete lists from both platforms
- Manual deletion from Trakt.tv also supported
- WatchBuddy respects user's Trakt privacy settings

---

## Summary

WatchBuddy now provides **full bidirectional synchronization** with Trakt:
- ✅ Lists automatically created on Trakt
- ✅ Items synced on every list update
- ✅ Titles kept in sync with dynamic updates
- ✅ Lists deleted from Trakt when deleted locally
- ✅ Graceful error handling with user notifications
- ✅ All lists marked "Created and managed by WatchBuddy"

Users can now enjoy seamless integration between WatchBuddy's intelligent recommendations and Trakt's social features!
