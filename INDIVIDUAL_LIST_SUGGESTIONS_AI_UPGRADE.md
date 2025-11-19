# Individual List Suggestions AI Upgrade

## Overview
Enhanced `individual_list_suggestions.py` with same AI capabilities as Overview and Individual List search modules.

## Changes Implemented

### 1. Dual-Index Hybrid Search
**Before**: Basic FAISS MiniLM search only
**After**: BGE multi-vector (70%) + FAISS fallback (30%)

**Benefits**:
- Better semantic understanding (5 aspect vectors: base, title, keywords, people, brands)
- Automatic fallback to FAISS if BGE index unavailable
- Composite query building from list items (titles, genres, overview snippets)
- Higher quality neighbor discovery

**Implementation**:
- New method: `_get_faiss_neighbors()` uses `hybrid_search()`
- Builds rich query from top 10 list items
- Renamed old method to `_get_faiss_neighbors_fallback()`
- Graceful degradation on errors

### 2. LLM-Generated Rationales
**Before**: No explanations for suggestions
**After**: One-sentence rationales via phi3:mini

**Benefits**:
- Explains WHY each suggestion fits THIS specific list
- Personalized with UserTextProfile context
- Examples:
  - "Shares the same dark Scandinavian noir atmosphere"
  - "Features similar cast from your collection"
  - "Explores related themes of moral ambiguity"

**Implementation**:
- New async method: `_generate_llm_rationale()`
- Batch generation via `asyncio.gather()`
- 5-second timeout per rationale
- List context + user profile passed to LLM
- Graceful failure (empty string if LLM unavailable)

### 3. Aspect-Aware Matching
**Before**: Only genre-based diversity boost
**After**: Match cast, themes, and studios from ItemLLMProfile

**Benefits**:
- Boost suggestions with shared cast/crew
- Match thematic elements
- Recognize production companies/studios
- More nuanced than genre matching alone

**Implementation**:
- Extract aspects from top 10 list items (people, themes, brands)
- Match candidate's ItemLLMProfile against list aspects
- People overlap: +0.03 per match
- Themes overlap: +0.03 per match  
- Studios overlap: +0.045 per match (weighted higher)
- Max aspect boost: +0.10
- New field: `_aspect_boost` in enriched items

### 4. UserTextProfile Integration
**Before**: Only fit scoring from watch history
**After**: Use 2-5 sentence user preference summary

**Benefits**:
- LLM rationales consider user's stated preferences
- More personalized explanations
- Context: "User likes character-driven dramas with complex narratives"

**Implementation**:
- Fetch UserTextProfile in `get_suggestions()`
- Pass to `_generate_llm_rationale()` for context
- Optional (continues without if unavailable)

### 5. Enhanced Scoring Formula
**Before**: 
```python
final_score = similarity * 0.6 + frequency * 0.4
combined = suggestion_score * 0.5 + fit * 0.3 + diversity * 0.25
```

**After**:
```python
# Stage 1: Suggestion score
suggestion_score = similarity * 0.6 + frequency * 0.4

# Stage 2: Final combined score
final_score = (
    suggestion_score * 0.50 +  # Semantic similarity
    fit_score * 0.30 +          # User fit
    diversity_boost * 0.20 +    # Genre rarity
    aspect_boost +              # Cast/theme matches (additive)
    genre_boost                 # User top genres (additive)
)
```

**Boosts**:
- Diversity: up to +0.15 (rare genres)
- Aspect: up to +0.10 (matching cast/themes/studios)
- Genre: +0.05 (user's top 5 genres)

## Performance Considerations

### Response Time Target: <300ms
- Dual-index search: ~150ms (vectorized operations)
- LLM rationales: Async batch, don't block response
- Aspect matching: Batch DB queries (1 query per aspect type)
- Redis cache: 45-second TTL for repeat requests

### Fallback Strategy
1. Try dual-index hybrid search
2. Fall back to basic FAISS if BGE unavailable
3. Continue without LLM rationales if timeout
4. Continue without aspects if ItemLLMProfile missing
5. Always return results (graceful degradation)

## Constants Updated
```python
NEIGHBORS_PER_ITEM = 30  # Was 25 (more candidates for deduping)
MIN_SIMILARITY = 0.40    # Was 0.45 (broader discovery)
```

## New Dependencies
- `httpx`: Already in requirements.txt (0.24.1)
- `asyncio`: Python stdlib
- Models: `ItemLLMProfile`, `UserTextProfile` (existing)

## API Response Format (New Fields)
```json
{
  "tmdb_id": 12345,
  "title": "Example Movie",
  "similarity_score": 0.85,
  "fit_score": 0.72,
  "is_high_fit": true,
  "llm_rationale": "Shares the same dark tone and explores similar themes of redemption.",
  "_aspect_boost": 0.06,
  "_diversity_boost": 0.08,
  "_final_score": 0.91
}
```

## Testing Recommendations

### 1. Test Dual-Index Search
```powershell
# Ensure BGE index exists
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT COUNT(*) FROM bge_embeddings;"
# Should show 50,000+

# Trigger rebuild if needed
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/rebuild_bge_faiss.py"
```

### 2. Test LLM Rationales
```powershell
# Check Ollama is running
docker exec -i watchbuddy-backend-1 curl http://ollama:11434/api/tags

# Test individual list suggestions
curl http://localhost:8000/api/individual-lists/<list_id>/suggestions
```

### 3. Monitor Performance
```powershell
# Check backend logs for timing
docker logs -f watchbuddy-backend-1 | Select-String -Pattern "suggestions|dual-index|rationale"

# Expected log entries:
# "Built query from 10 list items: ..."
# "Dual-index search found 150 unique candidates"
# "Generated 12 LLM rationales"
# "Generated 20 suggestions for list 123"
```

### 4. Verify Aspect Matching
```powershell
# Check ItemLLMProfile coverage
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "
SELECT COUNT(*) as total, 
       COUNT(key_people) as with_people,
       COUNT(themes) as with_themes,
       COUNT(notable_brands) as with_brands
FROM item_llm_profiles;
"
```

## Deployment Checklist
- [x] Updated imports (dual_index_search, ItemLLMProfile, UserTextProfile, asyncio, httpx)
- [x] Implemented dual-index hybrid search with fallback
- [x] Added async LLM rationale generation
- [x] Added aspect-aware matching (people, themes, studios)
- [x] Integrated UserTextProfile for personalization
- [x] Updated scoring formula with new boosts
- [x] Added graceful error handling throughout
- [x] Maintained backward compatibility (fallback to FAISS)
- [ ] Rebuild backend container: `docker compose build backend; docker compose up -d backend`
- [ ] Verify BGE index exists (run rebuild script if needed)
- [ ] Test with existing Individual Lists
- [ ] Monitor logs for errors

## Compatibility Notes

### Backward Compatibility
✅ All enhancements degrade gracefully:
- No BGE index? Falls back to FAISS
- No ItemLLMProfile? Skips aspect matching
- No UserTextProfile? Skips user context
- LLM timeout? Empty rationale
- Still returns suggestions even if all AI features fail

### Database Requirements
- `bge_embeddings` table with 50,000+ rows
- `item_llm_profiles` table (optional, improves quality)
- `user_text_profiles` table (optional, personalizes)

### Service Dependencies
- Ollama (phi3:mini): Optional for rationales
- BGE FAISS index: Optional (falls back to MiniLM)
- Redis: Required for caching

## Future Enhancements

### Potential Improvements
1. **Multi-List Context**: Learn from user's other lists
2. **Temporal Filtering**: "Show me recent releases only"
3. **Negative Filtering**: "No horror suggestions"
4. **Cross-List Discovery**: "Items similar to List A but not in List B"
5. **Rationale Caching**: Cache rationales per (candidate, list_context) pair
6. **A/B Testing**: Compare BGE vs FAISS quality metrics

### Performance Optimizations
- Pre-compute list aspects on list update
- Cache ItemLLMProfile aspects per list
- Batch LLM calls across multiple users
- Use sentence-transformers for rationale embedding similarity

## Related Files
- `backend/app/services/individual_list_suggestions.py` (932 lines)
- `backend/app/services/ai_engine/dual_index_search.py`
- `backend/app/services/overview_service.py` (reference for LLM patterns)
- `backend/app/models.py` (ItemLLMProfile, UserTextProfile)
- `backend/app/scripts/rebuild_bge_faiss.py` (BGE index rebuild)

## Success Metrics
- **Quality**: Suggestions feel more relevant to list context
- **Transparency**: Users understand WHY items are suggested
- **Performance**: <300ms response time (cached: <50ms)
- **Coverage**: 90%+ of suggestions have LLM rationales
- **Aspect Matching**: 30%+ of suggestions have aspect boosts >0
