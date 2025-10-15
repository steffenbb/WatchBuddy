Note: This document has moved.

Please see docs/SYNC_ANALYSIS_REPORT.md for the latest version.

### 🚀 Key Achievements

- **Performance Gains**: Average sync time **320ms** (still vastly improved vs. pre-migration ~45s)
- **API Load Reduction**: **100% of candidates from persistent DB** (1,600/1,600 sourced, 0 API calls)
- **Query Speed**: Sourcing phase averages **319ms** per list
- **Scoring Efficiency**: Candidate scoring completes in **~1ms** (negligible overhead)
- **Zero TMDB Failures**: No metadata lookup failures (all items pre-enriched)

### 📊 Detailed Metrics

| List | Candidates | Final Items | Sourcing (ms) | Total (ms) | Accuracy | Status |
|------|-----------|------------|---------------|-----------|----------|--------|
| Action Movies for Weekend (42) | 200 | 30 | 790 | 790 | 100% | ✅ Perfect |
| Trending Now (45) | 200 | 20 | 330 | 330 | 100% | ✅ Perfect |
| Your Perfect Match (38) | 200 | 200 | 221 | 221 | 100% | ✅ Perfect |
| Top Thriller Recommendations (39) | 200 | 50 | 212 | 212 | 100% | ✅ Perfect |
| Discovery (40) | 200 | 50 | 327 | 327 | 100% | ✅ Perfect |
| Your Perfect Match (43) | 200 | 20 | 112 | 112 | 100% | ✅ Perfect |
| Thriller (37) | 200 | 200 | 281 | 281 | 100% | ✅ Perfect |
| This Year's Best (44) | 200 | 15 | 290 | 290 | 100% | ✅ Perfect |

**Aggregated:**
- Total lists analyzed: 8
- Successful: 8/8
- Average sync time: 320ms
- Average filter accuracy: 100%
- Total incorrect items: 0

## 🔍 Issue Analysis

The previous filter accuracy issues were resolved by:
- Enforcing `genre_mode` (any/all) at DB query and in-memory filters
- Adding genre aliasing and compound handling (e.g., romcom)
- Using validation fallbacks (title/overview/keywords) where appropriate

### Enhanced Genre Extraction

Successfully implemented compound genre detection in `BulkCandidateProvider._extract_genres_from_title`:

**New Patterns Supported:**
- Romcoms → Romance + Comedy
- Crime documentaries → Crime + Documentary
- Action thrillers → Action + Thriller
- Sci-fi horror → Science Fiction + Horror
- Psychological thrillers → Thriller + Mystery
- Dark comedies → Comedy + Drama
- Superhero movies → Action + Adventure + Science Fiction
- Space opera → Science Fiction + Adventure

**Additional Single Keywords:**
- Noir → Crime + Thriller
- Slasher/Zombie/Vampire → Horror (+ Fantasy for vampire)
- Suspense → Thriller + Mystery
- Animated → Animation
- Biopic → Drama + History

This enables fusion lists to correctly extract multi-genre hints from titles like "Nordic Crime Documentaries" or "Romantic Comedy Classics".

## 💡 Recommendations

### Immediate Fixes

1. **Update Filter Validation**:
   ```python
   # For genre_mode="all", check ALL genres present
   if genre_mode == "all":
       genre_match = all(g.lower() in [ig.lower() for ig in item_genres] for g in genres)
   else:  # "any" mode
       genre_match = any(g.lower() in [ig.lower() for ig in item_genres] for g in genres)
   ```

2. **Case-Insensitive Genre Matching**:
   - Normalize all genre comparisons to lowercase
   - Use fuzzy matching for near-misses (Sci-Fi vs Science Fiction)

3. **Genre Alias Map**:
   ```python
   GENRE_ALIASES = {
       'sci-fi': 'science fiction',
       'scifi': 'science fiction',
       'mystery': 'thriller',  # TMDB often lumps these
       'romance': 'romantic',
       ...
   }
   ```

### Performance Optimization

- Added PostgreSQL `pg_trgm` extension and a GIN trigram index on `persistent_candidates.genres` to accelerate ILIKE filters
- Reduced over-fetch and added early-exit in provider to avoid processing thousands of rows when not needed
- Capped per-media-type candidate pool in list sync to 2,500 for fast yet diverse scoring pools

### Monitoring

Add to production logging:
- Filter accuracy rate per list
- Genre match success rate
- Average candidates per list type
- DB query timing breakdown

## 🎯 Success Criteria Met

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Sync time | < 5s | 167ms | ✅ 30x better |
| API calls | < 50 | 0 | ✅ Perfect |
| TMDB failures | < 5% | 0% | ✅ Perfect |
| Filter accuracy | > 95% | 66.7% | ⚠️ Needs fix |
| DB sourcing | > 80% | 100% | ✅ Perfect |

**Overall Assessment**: Migration to persistent candidate pool is a **major success** for performance and API load reduction. Filter accuracy issues are validation bugs, not architecture problems.

## 📝 Next Steps

1. Document compound genre extraction and genre aliases
2. Add filter accuracy and sourcing performance metrics to production dashboard
3. Schedule periodic background ingestion refreshes to keep the pool fresh

## 📂 Full Data

Complete analysis results available in: `/app/sync_analysis_report.json`

**Test command to reproduce**:
```bash
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/analyze_sync_simple.py"
```

---
*Analysis performed: 2025-10-14 21:43:34*
*Total runtime: ~2.9 seconds for 8 lists*
*Database: ~165k persistent candidates*
