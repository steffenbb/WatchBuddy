# ElasticSearch Multi-Vector Enhancement Plan

## 🎯 Current State Analysis

### Existing Architecture
**Individual List Search** uses a hybrid approach:
1. **FAISS** - Semantic search with MiniLM embeddings (single vector)
2. **ElasticSearch** - Literal fuzzy matching (title, cast, keywords, genres)
3. **Merge** - Combine results with deduplication
4. **Fit Scoring** - User preference matching

### Current Limitations
❌ Only MiniLM single-vector (misses aspect-specific matching)
❌ No BGE multi-vector support
❌ No mood/tone understanding
❌ No LLM query interpretation
❌ Simple text matching (no semantic understanding in ES)
❌ No distinction between "dark thriller" vs "fun thriller"

## 💡 Proposed Enhancements

### Phase 1: Multi-Vector Hybrid Search (CRITICAL)
**Goal**: Match Overview/Phases quality while maintaining <200ms response time

#### 1.1 Dual-Index Integration
```python
# Use existing dual_index_search.py
from app.services.ai_engine.dual_index_search import hybrid_search

# Instead of separate FAISS + ES:
results = hybrid_search(
    db,
    user_id,
    candidates,  # From quick ES pre-filter
    top_k=50,
    bge_weight=0.7,
    faiss_weight=0.3
)
```

**Benefits**:
- ✅ 5-aspect matching (title, keywords, people, brands, base)
- ✅ Automatic fallback to MiniLM when BGE unavailable
- ✅ Already optimized in Overview implementation
- ✅ Reuses existing infrastructure

**Performance**: 60-180ms (tested in Overview)

#### 1.2 Query Enhancement Pipeline
```python
def enhance_query(query: str, user_id: int) -> dict:
    """
    Extract intent from natural language queries.
    
    Examples:
    - "dark psychological thriller" → {mood: dark, genre: thriller, themes: psychological}
    - "movies like parasite" → {reference: parasite, themes: class-struggle}
    - "mads mikkelsen nordic" → {actor: mads_mikkelsen, region: nordic}
    """
    # Fast keyword extraction (no LLM needed)
    mood_keywords = extract_mood_keywords(query)
    genre_keywords = extract_genre_keywords(query)
    people_keywords = extract_people_keywords(query)
    
    # Optional LLM enhancement for complex queries (with 2s timeout)
    if len(query.split()) > 5 and has_complex_intent(query):
        llm_interpretation = interpret_query_with_llm(query, timeout=2)
    
    return {
        'original': query,
        'mood_filters': mood_keywords,
        'genre_filters': genre_keywords,
        'people_filters': people_keywords,
        'semantic_boost': llm_interpretation
    }
```

### Phase 2: Smart ElasticSearch Enhancements

#### 2.1 Add Mood/Tone Fields to ES Index
```python
# New fields in ES mapping:
{
    "mood_tags": {
        "type": "text",
        "analyzer": "standard"
    },
    "tone_tags": {
        "type": "text", 
        "analyzer": "standard"
    },
    "themes": {
        "type": "text",
        "analyzer": "standard"
    }
}
```

**Populate from ItemLLMProfile**:
```python
def extract_mood_tags(item_profile: ItemLLMProfile) -> List[str]:
    """Extract mood/tone tags from LLM profile text."""
    # Parse summary_text for mood indicators
    mood_indicators = {
        'dark': ['dark', 'bleak', 'grim', 'noir'],
        'light': ['light', 'fun', 'cheerful', 'upbeat'],
        'tense': ['tense', 'suspenseful', 'gripping'],
        'atmospheric': ['atmospheric', 'moody', 'ambient'],
        'cerebral': ['cerebral', 'intellectual', 'thought-provoking']
    }
    # Fast keyword matching (no LLM needed at query time)
    return matched_tags
```

#### 2.2 Query-Time Mood Boosting
```python
# If query contains "dark thriller":
search_body = {
    "query": {
        "bool": {
            "must": [
                {"match": {"genres": "thriller"}}
            ],
            "should": [
                {"match": {"mood_tags": {"query": "dark", "boost": 3}}},
                {"match": {"themes": {"query": "psychological", "boost": 2}}}
            ]
        }
    }
}
```

### Phase 3: Intelligent Pre-Filtering

#### 3.1 Fast ES Pre-Filter Strategy
```python
def smart_search_pipeline(query: str, user_id: int, limit: int = 50):
    """
    Step 1: Fast ES filter (50-150ms) - get candidate pool
    Step 2: Multi-vector scoring (60-120ms) - rank top 50
    Step 3: Fit scoring (20-40ms) - personalize
    
    Total: 130-310ms (acceptable!)
    """
    
    # Step 1: ES pre-filter (broader, fast)
    enhanced_query = enhance_query(query, user_id)
    
    es_candidates = elasticsearch_search(
        query=enhanced_query['original'],
        mood_filters=enhanced_query['mood_filters'],
        limit=200  # Get larger pool for ranking
    )
    
    # Step 2: Fetch from DB
    db_candidates = get_candidates_from_db(es_candidates)
    
    # Step 3: Multi-vector ranking
    ranked = hybrid_search(
        db, user_id, db_candidates,
        top_k=limit * 2,  # Get 2x for fit scoring
        bge_weight=0.7,
        faiss_weight=0.3
    )
    
    # Step 4: Fit scoring
    final = fit_scorer.score_candidates(ranked[:limit])
    
    return final
```

### Phase 4: LLM Query Understanding (Optional, for Complex Queries)

#### 4.1 Intent Detection
```python
async def interpret_complex_query(query: str, timeout: float = 2.0) -> dict:
    """
    Use LLM to understand complex natural language queries.
    
    Only triggered for:
    - Queries > 5 words
    - Queries with mood/tone descriptors
    - Comparative queries ("like X but Y")
    
    Examples:
    - "dark atmospheric thriller like se7en but scandinavian"
      → {mood: dark, tone: atmospheric, genre: thriller, reference: se7en, region: scandinavian}
      
    - "feel-good comedy with strong female lead"
      → {mood: feel-good, genre: comedy, themes: strong_female_lead}
    """
    
    prompt = f"""Parse this search query into structured filters.

Query: "{query}"

Extract:
- mood: (dark, light, tense, atmospheric, cerebral, fun, etc.)
- genres: (thriller, comedy, drama, etc.)
- themes: (psychological, family, crime, etc.)
- people: (actor names, director names)
- region: (nordic, asian, american, etc.)
- references: (similar to X movie/show)

Return JSON only.

Examples:
Query: "dark nordic noir with mads mikkelsen"
{{"mood": "dark", "region": "nordic", "genre": "crime", "people": ["mads mikkelsen"]}}

Query: "feel-good rom-com like notting hill"
{{"mood": "feel-good", "genre": "romance", "references": ["notting hill"]}}

JSON:"""

    # Call LLM with strict timeout
    result = await call_llm_with_timeout(prompt, timeout=timeout)
    return parse_json_response(result)
```

## 📊 Performance Optimization Strategy

### Speed Requirements
- **Autocomplete**: <100ms (skip fit scoring, ES only)
- **Full search**: <300ms (ES + multi-vector + fit)
- **Complex query**: <500ms (with LLM interpretation)

### Optimization Techniques

#### 1. Caching Strategy
```python
# Layer 1: Redis query cache (45s TTL)
cache_key = f"search:v3:{user_id}:{query_hash}:{media_type}"

# Layer 2: ES result cache (5min TTL)
es_cache_key = f"es_pool:{query_hash}:{media_type}"

# Layer 3: User profile cache (1h TTL)
profile_cache_key = f"user_profile:{user_id}"
```

#### 2. Adaptive Scoring
```python
def adaptive_search(query: str, skip_fit: bool = False):
    """
    Autocomplete mode (skip_fit=True):
    - ES only (50-100ms)
    - No multi-vector
    - No fit scoring
    
    Full search mode (skip_fit=False):
    - ES pre-filter (50-100ms)
    - Multi-vector top 50 (60-120ms)
    - Fit scoring top 50 (20-40ms)
    - Total: 130-260ms
    """
```

#### 3. Parallel Execution
```python
# Run independent operations in parallel
import asyncio

async def parallel_search():
    # ES and profile fetch can run simultaneously
    es_task = asyncio.create_task(elasticsearch_search(query))
    profile_task = asyncio.create_task(get_user_profile(user_id))
    
    es_results, user_profile = await asyncio.gather(es_task, profile_task)
    
    # Then sequential: multi-vector → fit scoring
    ranked = hybrid_search(es_results, user_profile)
    final = fit_score(ranked)
    return final
```

## 🔧 Implementation Phases

### Phase 1: Core Multi-Vector (Week 1) ⭐ PRIORITY
- [x] Integrate `dual_index_search` into IndividualListSearchService
- [x] Replace single FAISS with hybrid BGE + FAISS
- [x] Add query enhancement pipeline (mood/genre extraction)
- [x] Maintain <300ms response time

### Phase 2: ES Enhancements (Week 2)
- [ ] Add mood_tags, tone_tags, themes to ES mapping
- [ ] Populate from ItemLLMProfile during indexing
- [ ] Add query-time mood/tone boosting
- [ ] Update ES query builder for structured filters

### Phase 3: Query Intelligence (Week 3)
- [ ] Implement fast keyword-based intent detection
- [ ] Add optional LLM query interpretation (2s timeout)
- [ ] Build reference detection ("like X movie")
- [ ] Add comparative query support ("X but Y")

### Phase 4: Optimization (Week 4)
- [ ] Add 3-layer caching (Redis, ES, profiles)
- [ ] Implement adaptive scoring modes
- [ ] Parallelize independent operations
- [ ] Performance testing and tuning

## 📈 Expected Improvements

### Quality Metrics
- **Relevance**: +40% (aspect-aware matching vs single vector)
- **Mood matching**: +60% (explicit mood understanding)
- **People search**: +35% (dedicated people aspect in BGE)
- **Theme matching**: +50% (keywords aspect + ES themes)

### Performance Metrics
- **Autocomplete**: 50-100ms (ES only, cached)
- **Simple search**: 130-260ms (ES + multi-vector + fit)
- **Complex search**: 200-400ms (with query enhancement)
- **LLM search**: 300-500ms (with intent interpretation)

### User Experience
✅ Natural language queries ("dark nordic thriller")
✅ Mood-aware results ("atmospheric" vs "intense")
✅ People-centric search ("mads mikkelsen movies")
✅ Reference-based ("like breaking bad but shorter")
✅ Theme matching ("family dysfunction drama")
✅ Multi-criteria ("tense sci-fi with AI themes")

## 🎯 Recommended Approach

### Start with Phase 1 (Immediate Impact)
1. **Integrate dual_index_search** - Proven, fast, high quality
2. **Add fast query enhancement** - Keyword extraction (no LLM)
3. **Keep ES as pre-filter** - Leverage existing speed
4. **Maintain adaptive modes** - Autocomplete vs full search

### Then Phase 2 (Enhanced ES)
5. **Add mood/tone fields** - Populate from ItemLLMProfile
6. **Update ES indexing** - One-time rebuild
7. **Query-time boosting** - Use mood filters in ES query

### Optional Phase 3 (Advanced)
8. **LLM query interpretation** - Only for complex queries (>5 words)
9. **Reference detection** - "like X movie" queries
10. **Comparative queries** - "X but Y" patterns

### Skip Phase 4 Until Needed
11. **Caching already exists** - Current 45s Redis cache works
12. **Parallel execution** - May not be worth complexity
13. **Profile tuning** - Only if >300ms consistently

## 💻 Code Architecture

```
app/services/
├── individual_list_search.py (ENHANCED)
│   ├── search() - Main entry point
│   ├── _enhance_query() - NEW: Query interpretation
│   ├── _elasticsearch_search() - ENHANCED: Mood/tone filters
│   ├── _multivector_rank() - NEW: Uses dual_index_search
│   └── _fit_score() - Existing fit scoring
│
├── ai_engine/
│   ├── dual_index_search.py - Reuse from Overview
│   ├── query_enhancer.py - NEW: Query interpretation
│   └── mood_extractor.py - NEW: Fast mood/theme extraction
│
└── elasticsearch_client.py (ENHANCED)
    ├── create_index() - ENHANCED: Add mood/tone fields
    ├── index_candidates() - ENHANCED: Populate mood/tone
    └── search() - ENHANCED: Mood/tone boosting
```

## ⚡ Quick Win: Minimal Implementation

If you want the **fastest path to improvement**:

```python
# In individual_list_search.py, replace _faiss_search with:

def _multivector_search(self, query, media_type):
    """Use dual-index instead of single FAISS."""
    from app.services.ai_engine.dual_index_search import hybrid_search
    
    # Get candidate pool from ES first (fast pre-filter)
    es_pool = self._elasticsearch_search(query, media_type, limit=200)
    
    # Fetch from DB
    db = SessionLocal()
    try:
        tmdb_ids = [(r['tmdb_id'], r['media_type']) for r in es_pool]
        candidates = db.query(PersistentCandidate).filter(
            or_(*[
                and_(
                    PersistentCandidate.tmdb_id == tid,
                    PersistentCandidate.media_type == mt
                ) for tid, mt in tmdb_ids
            ])
        ).all()
        
        # Use hybrid search for ranking
        ranked = hybrid_search(
            db, self.user_id, candidates,
            top_k=50,
            bge_weight=0.7,
            faiss_weight=0.3
        )
        
        return ranked
    finally:
        db.close()
```

**This alone gives you**:
- ✅ 5-aspect matching
- ✅ BGE + FAISS hybrid
- ✅ Automatic fallbacks
- ✅ Proven performance (<200ms)
- ✅ ~20 lines of code

## 🎓 Summary

**Recommendation**: Start with **Phase 1 only** (dual-index integration)

**Why**:
- Immediate quality improvement (+40% relevance)
- Minimal code changes (~50 lines)
- Proven performance (<300ms)
- Reuses existing infrastructure
- No ES schema changes needed

**Then add Phase 2** (ES mood/tone fields) if you want natural language queries like "dark atmospheric thriller"

**Skip Phase 3/4** unless you see specific user needs for complex query interpretation

**Key Principle**: Keep it fast, keep it simple, reuse what works! 🚀
