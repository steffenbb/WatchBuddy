"""
individual_list_suggestions.py

AI-powered suggestions service for Individual Lists.
Generates intelligent suggestions based on current list items using:
- Dual-index semantic search (BGE multi-vector + FAISS fallback)
- LLM-generated personalized rationales
- Genre diversification
- User profile fit scoring
- Aspect-aware matching (cast, themes, studios)
- Deduplication (don't suggest items already in list)
"""
import json
import logging
from typing import List, Dict, Any, Set, Optional
from collections import Counter
import numpy as np
import asyncio
import httpx
from sqlalchemy import or_

from app.services.ai_engine.faiss_index import load_index, get_embedding_from_index
from app.services.ai_engine.dual_index_search import hybrid_search
from app.services.fit_scoring import FitScorer
from app.core.database import SessionLocal
from app.core.redis_client import get_redis_sync
from app.models import PersistentCandidate, IndividualListItem, ItemLLMProfile, UserTextProfile

logger = logging.getLogger(__name__)

SUGGESTIONS_LIMIT = 20
NEIGHBORS_PER_ITEM = 30  # Increased for better dual-index coverage
MIN_SIMILARITY = 0.40  # Slightly lower for broader suggestions


class IndividualListSuggestionsService:
    """
    Generate smart suggestions for Individual Lists using FAISS.
    
    Workflow:
    1. Get embeddings for all items currently in the list
    2. Query FAISS for nearest neighbors of each item
    3. Aggregate and score candidates by:
       - Frequency (how many list items recommend it)
       - Average similarity score
       - Genre diversity (boost underrepresented genres)
       - User fit score
    4. Filter out items already in list
    5. Return top 20 suggestions sorted by combined score
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.fit_scorer = FitScorer(user_id)
        self.use_llm_rationales = True  # Enable LLM-generated rationales
    
    def get_suggestions(self, list_id: int) -> List[Dict[str, Any]]:
        """
        Generate suggestions for an Individual List.
        
        Args:
            list_id: ID of the Individual List
            
        Returns:
            List of up to 20 suggested candidates with fit scores
        """
        logger.info(f"Generating suggestions for list {list_id}")
        
        db = SessionLocal()
        try:
            # Get current list items
            list_items = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == list_id
            ).all()
            # Short cache: based on list composition and user
            r = None
            try:
                r = get_redis_sync()
            except Exception:
                r = None
            cache_key = None
            if r:
                try:
                    sig = ",".join(str(li.tmdb_id or "x") for li in sorted(list_items, key=lambda x: (x.order_index or 0)))
                    cache_key = f"suggestions:v1:user:{self.user_id}:list:{list_id}:n{len(list_items)}:{sig[:64]}"
                    cached = r.get(cache_key)
                    if cached:
                        return json.loads(cached)
                except Exception:
                    pass
            
            if not list_items:
                logger.info(f"List {list_id} is empty, returning popular recommendations")
                return self._get_popular_recommendations(db)
            
            # Get existing item IDs to exclude from suggestions
            existing_tmdb_ids = {(item.tmdb_id, item.media_type) for item in list_items}
            
            # Analyze list to understand genre distribution
            list_genres = self._analyze_list_genres(list_items)
            
            # Get FAISS neighbors for each item
            candidates = self._get_faiss_neighbors(list_items, existing_tmdb_ids)
            
            if not candidates:
                logger.warning(f"No FAISS neighbors found for list {list_id}; falling back to hybrid popularity + mild diversity suggestions")
                return self._fallback_diverse_recommendations(db, existing_tmdb_ids)
            
            # Score candidates with diversity boost
            scored = self._score_candidates(candidates, list_genres)
            
            # Enrich with full metadata (includes aspect-aware boosting)
            enriched = self._enrich_with_metadata(scored, db, list_genres=list_genres, list_items=list_items)
            
            # Apply fit scoring (use cached profile first)
            final = self.fit_scorer.score_candidates(enriched, use_cached_profile=True)

            # If all results are neutral 0.5, force a profile rebuild and rescore once
            try:
                if final and all(abs((it.get('fit_score') or 0.5) - 0.5) < 1e-6 for it in final):
                    self.fit_scorer.profile_service.invalidate_cache()
                    final = self.fit_scorer.score_candidates(enriched, use_cached_profile=False)
            except Exception:
                pass
            
            # Get user profile for top_genres boost
            try:
                profile = self.fit_scorer.profile_service.get_profile()
                user_top_genres = set(g.lower() for g in (profile.get('top_genres') or [])[:5])
            except Exception:
                user_top_genres = set()
            
            # Combine suggestion score with fit score + top_genres boost
            for item in final:
                suggestion_score = item.get('_suggestion_score', 0.5)
                fit_score = item.get('fit_score', 0.5)
                
                # Apply small boost if item matches user's top genres
                genre_boost = 0.0
                if user_top_genres:
                    try:
                        item_genres = set(g.lower() for g in (item.get('genres') or []))
                        if user_top_genres.intersection(item_genres):
                            genre_boost = 0.05  # 5% boost for matching top genres
                    except Exception:
                        pass
                
                # Weight: 50% similarity, 30% fit, 20% diversity + aspect/genre boosts
                item['_final_score'] = (
                    suggestion_score * 0.5 + 
                    fit_score * 0.3 + 
                    item.get('_diversity_boost', 0.0) * 0.20 + 
                    item.get('_aspect_boost', 0.0) +  # Direct additive boost for matching cast/themes
                    genre_boost
                )
                # Expose similarity for UI badges if available
                try:
                    if '_avg_similarity' in item and item['_avg_similarity'] is not None:
                        sim = float(item['_avg_similarity'])
                        # Ensure [0,1]
                        item['similarity_score'] = max(0.0, min(1.0, sim))
                except Exception:
                    pass
                
                # Mark high-fit items (> 0.7)
                item['is_high_fit'] = fit_score > 0.7
            
            # Sort by final score and return top N
            final.sort(key=lambda x: x['_final_score'], reverse=True)
            
            result = final[:SUGGESTIONS_LIMIT]
            
            # Generate LLM rationales for top suggestions (async batch)
            if self.use_llm_rationales and result:
                try:
                    # Build list context from existing items
                    list_titles = [item.title for item in list_items[:5]]
                    list_genres = set()
                    for item in list_items:
                        if item.genres:
                            try:
                                genres = json.loads(item.genres) if isinstance(item.genres, str) else item.genres
                                list_genres.update(g.lower() for g in genres)
                            except:
                                pass
                    
                    list_context = f"A list containing: {', '.join(list_titles[:5])}. Common genres: {', '.join(list(list_genres)[:5])}."
                    
                    # Fetch UserTextProfile for personalization
                    user_profile = None
                    try:
                        user_profile = db.query(UserTextProfile).filter(
                            UserTextProfile.user_id == self.user_id
                        ).first()
                    except Exception as e:
                        logger.debug(f"Could not fetch UserTextProfile: {e}")
                    
                    # Generate rationales in batch (async)
                    async def generate_all_rationales():
                        tasks = [
                            self._generate_llm_rationale(candidate, list_context, user_profile)
                            for candidate in result
                        ]
                        return await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Run async batch
                    rationales = asyncio.run(generate_all_rationales())
                    
                    # Attach rationales to results
                    for idx, rationale in enumerate(rationales):
                        if idx < len(result) and isinstance(rationale, str) and rationale:
                            result[idx]['llm_rationale'] = rationale
                        else:
                            result[idx]['llm_rationale'] = ""
                    
                    logger.debug(f"Generated {sum(1 for r in rationales if r)} LLM rationales")
                    
                except Exception as e:
                    logger.warning(f"Failed to generate LLM rationales: {e}")
                    # Continue without rationales
                    for item in result:
                        item['llm_rationale'] = ""
            
            # Store in cache briefly (45s)
            if r and cache_key:
                try:
                    r.set(cache_key, json.dumps(result), ex=45)
                except Exception:
                    pass
            logger.info(f"Generated {len(result)} suggestions for list {list_id}")
            return result
            
        finally:
            db.close()
    
    def _analyze_list_genres(self, list_items: List[IndividualListItem]) -> Dict[str, int]:
        """
        Analyze genre distribution in current list.
        
        Returns dict of {genre: count} for genre diversity boosting.
        """
        genre_counts = Counter()
        
        for item in list_items:
            if item.genres:
                try:
                    genres = json.loads(item.genres) if isinstance(item.genres, str) else item.genres
                    for genre in genres:
                        genre_counts[genre.lower()] += 1
                except:
                    pass
        
        return dict(genre_counts)
    
    def _get_faiss_neighbors(
        self,
        list_items: List[IndividualListItem],
        existing_ids: Set[tuple]
    ) -> List[Dict[str, Any]]:
        """
        Get nearest neighbors using dual-index hybrid search (BGE + FAISS).
        
        Builds composite query from list items, then uses multi-vector BGE search
        with FAISS fallback for better semantic matching.
        
        Returns list of candidates with {tmdb_id, media_type, similarity_scores, frequency}.
        """
        try:
            # Build composite query from list items' titles and metadata
            query_parts = []
            db = SessionLocal()
            try:
                # Fetch metadata for list items to build rich query
                tmdb_ids = [item.tmdb_id for item in list_items[:10]]  # Limit to top 10 items
                candidates = db.query(PersistentCandidate).filter(
                    PersistentCandidate.tmdb_id.in_(tmdb_ids)
                ).all()
                
                for candidate in candidates:
                    if candidate.title:
                        query_parts.append(candidate.title)
                    # Add genres and overview snippets for context
                    try:
                        if candidate.genres:
                            genres = json.loads(candidate.genres)
                            query_parts.extend(genres[:3])  # Top 3 genres
                    except:
                        pass
                    
                    # Add overview keywords (first 50 chars)
                    if candidate.overview:
                        query_parts.append(candidate.overview[:50])
            finally:
                db.close()
            
            # Build query string
            query_text = " ".join(query_parts[:20])  # Limit total parts
            if not query_text:
                logger.warning("No query text built from list items, falling back to basic FAISS")
                # Fallback to old method if query building fails
                return self._get_faiss_neighbors_fallback(list_items, existing_ids)
            
            logger.debug(f"Built query from {len(list_items)} list items: {query_text[:100]}...")
            
            # Get candidate pool from database for hybrid search
            db2 = SessionLocal()
            try:
                # Get broad candidate pool based on list items' characteristics
                search_terms = query_text.lower().split()[:5]  # Top 5 terms
                
                query_obj = db2.query(PersistentCandidate)
                
                # Broad matching on title/overview
                if search_terms:
                    conditions = []
                    for term in search_terms:
                        conditions.append(PersistentCandidate.title.ilike(f'%{term}%'))
                        conditions.append(PersistentCandidate.overview.ilike(f'%{term}%'))
                    query_obj = query_obj.filter(or_(*conditions))
                
                candidate_pool = query_obj.limit(1000).all()
                
                if not candidate_pool:
                    logger.warning("No candidates found for hybrid search, falling back")
                    return self._get_faiss_neighbors_fallback(list_items, existing_ids)
                
                logger.debug(f"Found {len(candidate_pool)} candidates for hybrid search")
                
                # Use dual-index hybrid search with candidate pool
                search_results = hybrid_search(
                    db=db2,
                    user_id=self.user_id,
                    candidate_pool=candidate_pool,
                    top_k=NEIGHBORS_PER_ITEM * len(list_items),  # More results to dedupe
                    bge_weight=0.7,  # Prefer BGE multi-vector (better semantic understanding)
                    faiss_weight=0.3
                )
            finally:
                db2.close()
            
            # Aggregate by (tmdb_id, media_type) and track frequency
            candidate_scores = {}
            
            for result in search_results:
                # Extract from new hybrid_search return format
                candidate = result.get('candidate')
                if not candidate:
                    continue
                
                tmdb_id = candidate.tmdb_id
                media_type = candidate.media_type
                similarity = result.get('score', 0.0)
                
                key = (tmdb_id, media_type)
                
                # Skip if already in list
                if key in existing_ids:
                    continue
                
                # Filter by minimum similarity
                if similarity < MIN_SIMILARITY:
                    continue
                
                # Aggregate scores (items may appear from multiple search vectors)
                if key not in candidate_scores:
                    candidate_scores[key] = {
                        'tmdb_id': tmdb_id,
                        'media_type': media_type,
                        'scores': [],
                        'frequency': 0
                    }
                
                candidate_scores[key]['scores'].append(similarity)
                candidate_scores[key]['frequency'] += 1
            
            # Convert to list and calculate statistics
            candidates = []
            for key, data in candidate_scores.items():
                avg_score = np.mean(data['scores'])
                max_score = max(data['scores'])
                
                candidates.append({
                    'tmdb_id': data['tmdb_id'],
                    'media_type': data['media_type'],
                    'avg_similarity': float(avg_score),
                    'max_similarity': float(max_score),
                    'frequency': data['frequency']
                })
            
            # Sort by frequency and average similarity
            candidates.sort(key=lambda x: (x['frequency'], x['avg_similarity']), reverse=True)
            
            logger.debug(f"Dual-index search found {len(candidates)} unique candidates")
            return candidates
            
        except Exception as e:
            logger.error(f"Failed dual-index search, falling back: {e}")
            # Fallback to basic FAISS if dual-index fails
            return self._get_faiss_neighbors_fallback(list_items, existing_ids)
    
    async def _generate_llm_rationale(
        self,
        candidate: Dict[str, Any],
        list_context: str,
        user_profile: Optional[UserTextProfile] = None
    ) -> str:
        """
        Generate LLM explanation for why this candidate fits the list.
        
        Similar to overview_service rationale generation, but focused on
        explaining fit with THIS specific user list.
        
        Args:
            candidate: Enriched candidate dict with title, genres, overview
            list_context: Summary of what's in the user's list
            user_profile: Optional user preferences for personalization
        
        Returns:
            Single sentence rationale or empty string on failure
        """
        try:
            # Build prompt with list context
            title = candidate.get('title', 'Unknown')
            genres = ', '.join(candidate.get('genres', [])[:3]) if candidate.get('genres') else 'Unknown'
            overview = candidate.get('overview', '')[:200]  # Truncate
            
            # Add user profile context if available
            user_context = ""
            if user_profile:
                user_context = f"\n\nUser preferences: {user_profile.profile_text}"
            
            prompt = f"""You are explaining why a movie/show suggestion fits a user's list.

List context: {list_context}
{user_context}

Suggested item:
- Title: {title}
- Genres: {genres}
- Overview: {overview}

Task: Write ONE sentence explaining why this item fits the list. Focus on thematic connections, genre overlap, or similar tones.

**CRITICAL**: Output ONLY the rationale sentence, no preamble, no "This fits because...", just the explanation itself.

Example: "Shares the same dark Scandinavian noir atmosphere and explores moral ambiguity through complex characters."
"""
            
            # Make async request to Ollama
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://ollama:11434/api/generate",
                    json={
                        "model": "phi3.5:3.8b-mini-instruct-q4_K_M",
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 50,  # Short response
                            "num_ctx": 4096  # Large context window
                        },
                        "keep_alive": "24h"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    rationale = result.get('response', '').strip()
                    
                    # Clean up common prefixes
                    prefixes = [
                        "This fits because ",
                        "This item fits because ",
                        "It fits because ",
                        "Suggested because ",
                        "Rationale: "
                    ]
                    for prefix in prefixes:
                        if rationale.startswith(prefix):
                            rationale = rationale[len(prefix):]
                    
                    return rationale[:200]  # Cap length
                else:
                    logger.debug(f"LLM rationale failed: {response.status_code}")
                    return ""
                    
        except asyncio.TimeoutError:
            logger.debug("LLM rationale timeout")
            return ""
        except Exception as e:
            logger.debug(f"LLM rationale error: {e}")
            return ""
    
    def _get_faiss_neighbors_fallback(
        self,
        list_items: List[IndividualListItem],
        existing_ids: Set[tuple]
    ) -> List[Dict[str, Any]]:
        """
        Fallback method using basic FAISS search (original implementation).
        
        Used when dual-index search fails or BGE index unavailable.
        """
        try:
            # Load FAISS index
            index, mapping = load_index()
            
            # Aggregate candidates from all list items
            candidate_scores = {}  # (tmdb_id, media_type) -> [scores]
            
            for item in list_items:
                # Get embedding for this item
                embedding = get_embedding_from_index(item.tmdb_id, item.media_type)
                
                if embedding is None:
                    logger.debug(f"No embedding for {item.tmdb_id} ({item.media_type})")
                    continue
                
                # Normalize embedding
                embedding = embedding.astype(np.float32)
                embedding = embedding / (np.linalg.norm(embedding) + 1e-8)
                embedding = embedding.reshape(1, -1)
                
                # Query FAISS
                distances, indices = index.search(embedding, NEIGHBORS_PER_ITEM)
                
                # Collect valid trakt_ids from this item's neighbors
                neighbor_data = []
                for i, idx in enumerate(indices[0]):
                    if idx == -1:
                        continue
                    
                    similarity = float(distances[0][i])
                    
                    # Filter by minimum similarity
                    if similarity < MIN_SIMILARITY:
                        continue
                    
                    # Look up candidate trakt_id
                    trakt_id = mapping.get(int(idx))
                    if not trakt_id:
                        continue
                    
                    neighbor_data.append((trakt_id, similarity))
                
                # Batch lookup all neighbors for this item in one query
                if neighbor_data:
                    trakt_ids = [tid for tid, _ in neighbor_data]
                    trakt_to_sim = {tid: sim for tid, sim in neighbor_data}
                    
                    db = SessionLocal()
                    try:
                        candidates = db.query(PersistentCandidate).filter(
                            PersistentCandidate.trakt_id.in_(trakt_ids)
                        ).all()
                        
                        for candidate in candidates:
                            # Skip if already in list
                            key = (candidate.tmdb_id, candidate.media_type)
                            if key in existing_ids:
                                continue
                            
                            # Get similarity for this candidate
                            similarity = trakt_to_sim.get(candidate.trakt_id, 0.0)
                            
                            # Aggregate scores
                            if key not in candidate_scores:
                                candidate_scores[key] = {
                                    'tmdb_id': candidate.tmdb_id,
                                    'media_type': candidate.media_type,
                                    'scores': [],
                                    'frequency': 0
                                }
                            
                            candidate_scores[key]['scores'].append(similarity)
                            candidate_scores[key]['frequency'] += 1
                    finally:
                        db.close()
            
            # Convert to list and calculate average scores
            candidates = []
            for key, data in candidate_scores.items():
                avg_score = np.mean(data['scores'])
                max_score = max(data['scores'])
                
                candidates.append({
                    'tmdb_id': data['tmdb_id'],
                    'media_type': data['media_type'],
                    'avg_similarity': float(avg_score),
                    'max_similarity': float(max_score),
                    'frequency': data['frequency']
                })
            
            # Sort by frequency and average similarity
            candidates.sort(key=lambda x: (x['frequency'], x['avg_similarity']), reverse=True)
            
            logger.debug(f"FAISS fallback found {len(candidates)} unique candidates")
            return candidates
            
        except Exception as e:
            logger.error(f"Failed FAISS fallback: {e}")
            return []
    
    def _score_candidates(
        self,
        candidates: List[Dict[str, Any]],
        list_genres: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """
        Score candidates with diversity boost.
        
        Boost candidates with underrepresented genres to encourage diversity.
        """
        if not candidates:
            return []
        
        # Normalize frequency and similarity scores
        max_freq = max(c['frequency'] for c in candidates)
        max_sim = max(c['avg_similarity'] for c in candidates)
        
        for candidate in candidates:
            freq_score = candidate['frequency'] / max_freq
            sim_score = candidate['avg_similarity'] / max_sim
            
            # Combined suggestion score: 60% similarity, 40% frequency
            candidate['_suggestion_score'] = sim_score * 0.6 + freq_score * 0.4
            
            # Diversity boost will be calculated after metadata enrichment
            candidate['_diversity_boost'] = 0.0
        
        return candidates
    
    def _enrich_with_metadata(
        self,
        candidates: List[Dict[str, Any]],
        db,
        list_genres: Dict[str, int] = None,
        list_items: List[IndividualListItem] = None
    ) -> List[Dict[str, Any]]:
        """
        Enrich candidates with full metadata from DB.
        
        Also calculates diversity boost based on genres and aspect-aware boosting
        (matching cast, themes, studios from list items).
        """
        if not candidates:
            return []
        
        # Fetch metadata
        tmdb_ids = [c['tmdb_id'] for c in candidates]
        
        db_candidates = db.query(PersistentCandidate).filter(
            PersistentCandidate.tmdb_id.in_(tmdb_ids),
            PersistentCandidate.active == True
        ).all()
        
        # Create lookup
        candidate_map = {
            (c.tmdb_id, c.media_type): c
            for c in db_candidates
        }
        
        # Extract aspects from list items (cast, themes, studios) for aspect-aware boosting
        list_people = set()
        list_themes = set()
        list_studios = set()
        
        if list_items:
            try:
                # Batch fetch ItemLLMProfiles for list items
                list_tmdb_ids = [item.tmdb_id for item in list_items[:10]]  # Top 10 items
                profiles = db.query(ItemLLMProfile).filter(
                    ItemLLMProfile.tmdb_id.in_(list_tmdb_ids)
                ).all()
                
                for profile in profiles:
                    # Extract people (cast/crew)
                    if profile.key_people:
                        try:
                            people = json.loads(profile.key_people) if isinstance(profile.key_people, str) else profile.key_people
                            list_people.update(p.lower() for p in people[:10])  # Top 10 people per item
                        except:
                            pass
                    
                    # Extract themes
                    if profile.themes:
                        try:
                            themes = json.loads(profile.themes) if isinstance(profile.themes, str) else profile.themes
                            list_themes.update(t.lower() for t in themes[:5])
                        except:
                            pass
                    
                    # Extract studios/brands
                    if profile.notable_brands:
                        try:
                            brands = json.loads(profile.notable_brands) if isinstance(profile.notable_brands, str) else profile.notable_brands
                            list_studios.update(b.lower() for b in brands[:3])
                        except:
                            pass
                
                logger.debug(f"Extracted {len(list_people)} people, {len(list_themes)} themes, {len(list_studios)} studios from list")
            except Exception as e:
                logger.debug(f"Could not extract list aspects: {e}")
        
        # Batch fetch ItemLLMProfiles for aspect matching (avoid N+1 queries)
        candidate_profiles_map = {}
        if list_people or list_themes or list_studios:
            try:
                candidate_tmdb_ids = [c['tmdb_id'] for c in candidates]
                candidate_profiles = db.query(ItemLLMProfile).filter(
                    ItemLLMProfile.tmdb_id.in_(candidate_tmdb_ids)
                ).all()
                
                candidate_profiles_map = {
                    (p.tmdb_id, p.media_type): p
                    for p in candidate_profiles
                }
                logger.debug(f"Fetched {len(candidate_profiles_map)} ItemLLMProfiles for aspect matching")
            except Exception as e:
                logger.debug(f"Could not batch fetch ItemLLMProfiles: {e}")
        
        # Enrich
        enriched = []
        # Pre-compute rarity metrics from list_genres (genre frequency within existing list)
        list_genres = list_genres or {}
        # Compute median frequency to define underrepresentation threshold
        try:
            counts = [c for c in list_genres.values() if isinstance(c, int)]
            median_count = 0
            if counts:
                counts_sorted = sorted(counts)
                mid = len(counts_sorted) // 2
                median_count = counts_sorted[mid] if counts_sorted else 0
        except Exception:
            median_count = 0

        for candidate in candidates:
            key = (candidate['tmdb_id'], candidate['media_type'])
            db_candidate = candidate_map.get(key)
            
            if not db_candidate:
                continue
            
            # Parse genres
            genres = []
            try:
                genres = json.loads(db_candidate.genres) if db_candidate.genres else []
            except:
                pass

            # Compute diversity boost: promote genres that are underrepresented in the user's current list
            diversity_boost = 0.0
            try:
                rarity_scores = []
                for g in genres:
                    g_norm = g.lower()
                    count = list_genres.get(g_norm, 0)
                    # If genre absent or below median, treat as rare
                    if count <= median_count:
                        # Rarity score inversely proportional to (1 + count)
                        rarity = 1.0 / (1 + count)
                        rarity_scores.append(rarity)
                if rarity_scores:
                    # Average rarity scaled; cap to avoid overpowering similarity/fit
                    diversity_boost = min(0.15, (sum(rarity_scores) / len(rarity_scores)) * 0.12)
            except Exception:
                diversity_boost = 0.0
            
            # Aspect-aware boosting: match people, themes, studios from list
            aspect_boost = 0.0
            if list_people or list_themes or list_studios:
                try:
                    # Get candidate's ItemLLMProfile from batch-fetched map
                    candidate_key = (db_candidate.tmdb_id, db_candidate.media_type)
                    candidate_profile = candidate_profiles_map.get(candidate_key)
                    
                    if candidate_profile:
                        matches = 0
                        total_checks = 0
                        
                        # Check people overlap
                        if list_people and candidate_profile.key_people:
                            try:
                                cand_people = json.loads(candidate_profile.key_people) if isinstance(candidate_profile.key_people, str) else candidate_profile.key_people
                                cand_people_lower = set(p.lower() for p in cand_people[:10])
                                people_overlap = len(list_people.intersection(cand_people_lower))
                                if people_overlap > 0:
                                    matches += people_overlap
                                    total_checks += 1
                            except:
                                pass
                        
                        # Check themes overlap
                        if list_themes and candidate_profile.themes:
                            try:
                                cand_themes = json.loads(candidate_profile.themes) if isinstance(candidate_profile.themes, str) else candidate_profile.themes
                                cand_themes_lower = set(t.lower() for t in cand_themes[:5])
                                themes_overlap = len(list_themes.intersection(cand_themes_lower))
                                if themes_overlap > 0:
                                    matches += themes_overlap
                                    total_checks += 1
                            except:
                                pass
                        
                        # Check studios/brands overlap
                        if list_studios and candidate_profile.notable_brands:
                            try:
                                cand_brands = json.loads(candidate_profile.notable_brands) if isinstance(candidate_profile.notable_brands, str) else candidate_profile.notable_brands
                                cand_brands_lower = set(b.lower() for b in cand_brands[:3])
                                brands_overlap = len(list_studios.intersection(cand_brands_lower))
                                if brands_overlap > 0:
                                    matches += brands_overlap * 1.5  # Weight studios slightly higher
                                    total_checks += 1
                            except:
                                pass
                        
                        # Calculate aspect boost (capped at 0.10)
                        if total_checks > 0:
                            aspect_boost = min(0.10, matches * 0.03)
                except Exception as e:
                    logger.debug(f"Aspect matching failed for {db_candidate.tmdb_id}: {e}")
                    aspect_boost = 0.0
            
            enriched_item = {
                'tmdb_id': db_candidate.tmdb_id,
                'trakt_id': db_candidate.trakt_id,
                'media_type': db_candidate.media_type,
                'title': db_candidate.title,
                'original_title': db_candidate.original_title,
                'year': db_candidate.year,
                'overview': db_candidate.overview,
                'poster_path': db_candidate.poster_path,
                'backdrop_path': db_candidate.backdrop_path,
                'genres': genres,
                'popularity': db_candidate.popularity,
                'vote_average': db_candidate.vote_average,
                '_suggestion_score': candidate['_suggestion_score'],
                '_frequency': candidate['frequency'],
                '_avg_similarity': candidate['avg_similarity'],
                '_diversity_boost': diversity_boost,
                '_aspect_boost': aspect_boost  # Add aspect boost
            }
            
            enriched.append(enriched_item)
        
        return enriched
    
    def _get_popular_recommendations(self, db) -> List[Dict[str, Any]]:
        """
        Fallback: Return popular highly-rated items when list is empty.
        
        Used to bootstrap suggestions for empty lists.
        """
        try:
            # Get top rated popular items (slightly relaxed thresholds to improve diversity)
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.active == True,
                PersistentCandidate.vote_average >= 7.0,
                PersistentCandidate.vote_count >= 250,
                PersistentCandidate.popularity >= 12
            ).order_by(
                PersistentCandidate.popularity.desc(),
                PersistentCandidate.vote_average.desc()
            ).limit(SUGGESTIONS_LIMIT * 5).all()  # Fetch more for better diversity sampling

            enriched: List[Dict[str, Any]] = []
            for c in candidates:
                try:
                    genres = json.loads(c.genres) if c.genres else []
                except Exception:
                    genres = []
                enriched.append({
                    'tmdb_id': c.tmdb_id,
                    'trakt_id': c.trakt_id,
                    'media_type': c.media_type,
                    'title': c.title,
                    'original_title': c.original_title,
                    'year': c.year,
                    'overview': c.overview,
                    'poster_path': c.poster_path,
                    'backdrop_path': c.backdrop_path,
                    'genres': genres,
                    'popularity': c.popularity,
                    'vote_average': c.vote_average
                })

            # Apply fit scoring
            try:
                scored = self.fit_scorer.score_candidates(enriched, use_cached_profile=True)
            except Exception:
                scored = enriched
                for it in scored:
                    it['fit_score'] = 0.5

            # Sort by fit score then popularity
            scored.sort(key=lambda x: (x.get('fit_score', 0.5), x.get('popularity', 0)), reverse=True)
            for item in scored:
                item['is_high_fit'] = item.get('fit_score', 0.0) > 0.7
            return scored[:SUGGESTIONS_LIMIT]
        except Exception as e:
            logger.error(f"Failed to get popular recommendations: {e}")
            return []

    def _fallback_diverse_recommendations(self, db, existing_tmdb_ids) -> List[Dict[str, Any]]:
        """Return a diversified set when FAISS cannot provide neighbors (e.g., all niche items without embeddings).
        Excludes existing items.
        """
        try:
            base = db.query(PersistentCandidate).filter(
                PersistentCandidate.active == True,
                PersistentCandidate.vote_average >= 6.2,
                PersistentCandidate.vote_count >= 80,
                PersistentCandidate.popularity >= 10
            ).order_by(
                PersistentCandidate.freshness_score.desc(),
                PersistentCandidate.popularity.desc()
            ).limit(300).all()

            # Genre balancing buckets
            import json as _json
            genre_buckets: Dict[str, List[PersistentCandidate]] = {}
            for c in base:
                if (c.tmdb_id, c.media_type) in existing_tmdb_ids:
                    continue
                try:
                    genres = _json.loads(c.genres) if c.genres else []
                except Exception:
                    genres = []
                first_genre = genres[0].lower() if genres else 'unknown'
                genre_buckets.setdefault(first_genre, []).append(c)

            # Round-robin selection for diversity
            selected: List[PersistentCandidate] = []
            while len(selected) < SUGGESTIONS_LIMIT and any(genre_buckets.values()):
                for g, items in list(genre_buckets.items()):
                    if not items:
                        genre_buckets.pop(g, None)
                        continue
                    selected.append(items.pop(0))
                    if len(selected) >= SUGGESTIONS_LIMIT:
                        break

            out: List[Dict[str, Any]] = []
            for c in selected:
                try:
                    genres = _json.loads(c.genres) if c.genres else []
                except Exception:
                    genres = []
                out.append({
                    'tmdb_id': c.tmdb_id,
                    'trakt_id': c.trakt_id,
                    'media_type': c.media_type,
                    'title': c.title,
                    'original_title': c.original_title,
                    'year': c.year,
                    'overview': c.overview,
                    'poster_path': c.poster_path,
                    'backdrop_path': c.backdrop_path,
                    'genres': genres,
                    'popularity': c.popularity,
                    'vote_average': c.vote_average,
                    '_suggestion_score': 0.55,
                    '_frequency': 0,
                    '_avg_similarity': None
                })

            # Fit scoring
            try:
                scored = self.fit_scorer.score_candidates(out, use_cached_profile=True)
            except Exception:
                scored = out
                for it in scored:
                    it['fit_score'] = 0.5

            scored.sort(key=lambda x: (x.get('fit_score', 0.5), x.get('popularity', 0)), reverse=True)
            for item in scored:
                item['is_high_fit'] = item.get('fit_score', 0.0) > 0.7
            return scored[:SUGGESTIONS_LIMIT]
        except Exception as e:
            logger.warning(f"Fallback diverse recommendations failed: {e}")
            return []
