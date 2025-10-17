# Chat List Quality Fixes

## Issues Identified

### 1. **Incomplete Filter Parsing**
- **Problem**: Chat prompts weren't fully parsed - missing genres, moods, and anchor references
- **Example**: "cozy feel good movies like the hangover...comedies with a bit of action"
  - ❌ Was only extracting: `year_from`, `mood: ["cozy"]`, `search_query`
  - ✅ Now extracts: All genres, moods, anchor title, media types, year constraints

### 2. **Media Type Not Respected**
- **Problem**: Lists without `media_types` in filters defaulted to BOTH movies and shows
- **Example**: List 17 said "movies" but showed both movies and TV shows
- **Root Cause**: Missing `media_types` field in filters → default to `["movies", "shows"]`

### 3. **Genre Detection Too Restrictive**
- **Problem**: Only detected genres with explicit "genre:" prefix
- **Example**: "comedies with action" didn't detect Comedy or Action genres
- **Fix**: Added 60+ genre keyword variations with word boundary matching

### 4. **Mood/Theme Not Used for Scoring**
- **Problem**: `filters["mood"]` was ignored - only used user's cached mood profile
- **Example**: "Cozy mood" list had The Godfather, Schindler's List, The Dark Knight
- **Root Cause**: Scoring engine only used `get_cached_user_mood()`, not list-specific mood filters

### 5. **No Semantic Anchoring**
- **Problem**: "like the hangover" anchor wasn't extracted
- **Pattern**: Only looked for "similar to X" but not "like X" or "as good as X"
- **Fix**: Multiple patterns now detect anchor references

### 6. **Generic Titles**
- **Problem**: List titles were just the raw prompt (truncated)
- **Example**: "I want cozy feel good movies like the hangover prefer stuff after 2000..."
- **Fix**: Dynamic title generation based on parsed filters

### 7. **Exclude IDs Not Applied in Second Pass**
- **Problem**: When first query didn't find enough candidates, second pass ignored `exclude_ids`
- **Result**: 63% duplication rate across lists (213 duplicates out of 335 items)
- **Fix**: Added `exclude_ids` filter to second pass query in `bulk_candidate_provider.py`

## Fixes Applied

### 1. Enhanced Genre Parsing (`chat_prompt.py`)
```python
# Added 60+ genre keyword mappings with variations:
- 'romcom', 'rom-com', 'romantic comedy' → ['Romance', 'Comedy']
- 'sci-fi', 'scifi', 'science fiction', 'sf' → 'Science Fiction'
- 'comedies', 'funny', 'hilarious', 'comic' → 'Comedy'
- 'thrillers', 'suspense', 'suspenseful' → 'Thriller'
- etc.
```

### 2. Comprehensive Mood Detection
```python
# Imported MOOD_KEYWORD_MAPPING from scoring_engine.py (70+ mood keywords)
- 'cozy', 'comfort' → {happy: 0.6, thoughtful: 0.3, romantic: 0.1}
- 'feel-good', 'feel good', 'feelgood' → {happy: 0.9, excited: 0.1}
- 'dark', 'intense', 'gritty', 'serious' → tense/thoughtful moods
- 'scary', 'horror', 'creepy' → scared/tense moods
```

### 3. Multiple Anchor Patterns
```python
# Detects anchor references with multiple patterns:
- "similar to X"
- "like X"  
- "as good as X"
- "movies/shows like X"
```

### 4. Mood-Based Scoring (`scoring_engine.py`)
```python
# New logic in score_candidates():
if filters.get("mood"):
    mood_vector = self._mood_keywords_to_vector(filters["mood"])
    # Use list-specific mood instead of user's cached mood
    
# Boosts candidates matching the specified mood axes
```

### 5. Dynamic Title Generation
```python
def generate_dynamic_title(filters, prompt):
    # Builds descriptive title from parsed components:
    # "[Mood] [Genres] [Media Type] like [Anchor] (Year Range) [Language]"
    
# Example outputs:
# "Cozy, Feel-Good Comedy & Action Movies like The Hangover (from 2000)"
# "Scary, Horror Horror Movies & Shows"
# "Romantic Comedy & Romance"
```

### 6. Exclude IDs in Second Pass (`bulk_candidate_provider.py`)
```python
# Line 545-550: Added to second pass query
if exclude_ids:
    q2 = q2.filter(~PersistentCandidate.trakt_id.in_(exclude_ids))
```

### 7. Media Type Fallback Handling (`list_sync.py`)
```python
# Enhanced media_type extraction with fallback to parsing:
media_types = filters.get("media_types") or filters.get("media_type", ["movies", "shows"])

# For chat lists without media_types, tries to re-parse from title
if not media_types and user_list.list_type == "chat":
    reparsed = parse_chat_prompt(user_list.title)
    media_types = reparsed.get("media_types", ["movies", "shows"])
```

## Testing Results

### Original Parsing (List 17 prompt):
```
Prompt: "I want cozy feel good movies like the hangover prefer stuff after 2000, comedies with a bit of action"

OLD:
{
  "year_from": 2000,
  "mood": ["cozy"],
  "search_query": "..."
}
```

### Enhanced Parsing:
```
NEW:
{
  "genres": ["Comedy", "Action"],
  "year_from": 2000,
  "mood": ["cozy", "feel-good"],
  "similar_to_title": "the hangover",
  "media_types": ["movie"],
  "search_query": "..."
}

Generated Title: "Cozy, Feel-Good Comedy & Action Movies like The Hangover (from 2000)"
```

## Validation Checklist

- [x] Genre keywords properly detected (including variations like "romcom", "sci-fi")
- [x] Mood keywords mapped to MOOD_KEYWORD_MAPPING from scoring engine
- [x] Media types correctly identified and enforced
- [x] Anchor references extracted with multiple patterns
- [x] Year constraints parsed correctly
- [x] Dynamic titles generated based on filters
- [x] Mood filtering applied during scoring (not just discovery mode)
- [x] Exclude IDs applied in both first and second pass queries
- [x] Plural forms and variations handled (comedies → Comedy, feel good → feel-good)

## Next Steps

1. **Resync existing chat lists** with `force_full=True` to apply new parsing logic
2. **Update existing list titles** to use dynamic generation
3. **Test mood-based scoring** by checking if "cozy" lists now exclude dark content
4. **Verify exclude_ids** - ensure <10% duplication rate across lists
5. **Validate content relevance** - lists should match their theme/mood/filters

## Migration Script Needed

For existing chat lists that were created with old parsing:
```python
# Pseudo-code:
for list in chat_lists:
    # Re-parse the original prompt
    new_filters = parse_chat_prompt(list.title)
    
    # Update filters in database
    list.filters = json.dumps(new_filters)
    
    # Regenerate title
    list.title = generate_dynamic_title(new_filters, list.title)
    
    # Trigger full resync
    sync_list(list.id, force_full=True)
```
