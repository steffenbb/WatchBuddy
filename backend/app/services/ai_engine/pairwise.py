from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import math
import json
import re
import logging
import random

logger = logging.getLogger(__name__)


def format_item_summary(item: Dict[str, Any], max_overview_len: int = 200, use_llm_profile: bool = True) -> str:
    """Format item with all 24 TMDB fields into compact summary for LLM prompts.
    
    Includes: title, year, media_type, genres, keywords, overview, tagline, people (actors/directors),
    studio, network, rating, votes, popularity, language, runtime, certification, status,
    original_title, aliases, season_count, episode_count, first_air_date, obscurity_score,
    mainstream_score, freshness_score.
    
    Args:
        item: Candidate dictionary
        max_overview_len: Maximum overview length
        use_llm_profile: If True, enrich from ItemLLMProfile cache when available
    """
    # Enrich item from ItemLLMProfile if available (adds missing fields from DB)
    if use_llm_profile:
        candidate_id = item.get('_candidate_id') or item.get('candidate_id') or item.get('id')
        if candidate_id:
            try:
                from .profile_prep import ItemProfileService
                profile = ItemProfileService.get_or_build(int(candidate_id))
                if profile and profile.get('profile'):
                    # Merge missing fields from LLM profile
                    prof_data = profile['profile']
                    enriched_fields = []
                    for key in ['genres', 'keywords', 'overview', 'tagline', 'popularity', 
                               'vote_average', 'vote_count', 'original_language', 'runtime',
                               'cast', 'production_companies', 'certification', 'status']:
                        if (item.get(key) in (None, [], "")) and (key in prof_data):
                            item[key] = prof_data[key]
                            enriched_fields.append(key)
                    if enriched_fields:
                        logger.debug(f"[Pairwise] Enriched {len(enriched_fields)} fields from ItemLLMProfile: {enriched_fields[:3]}...")
            except ImportError:
                logger.warning("[Pairwise] ItemProfileService not available - skipping enrichment")
            except Exception as e:
                logger.debug(f"[Pairwise] Could not enrich from ItemLLMProfile: {e}")
    
    parts = []
    
    # Title + Year + Type
    title = item.get('title', 'Unknown')
    year = item.get('year', '')
    media_type = item.get('media_type', 'movie')
    parts.append(f"{title} ({year})" if year else title)
    parts.append(f"[{media_type}]")
    
    # Genres (max 6)
    genres = item.get('genres', [])
    if genres:
        genre_str = '/'.join(genres[:6]) if isinstance(genres, list) else str(genres)[:50]
        parts.append(f"Genres: {genre_str}")
    
    # Keywords (max 8)
    keywords = item.get('keywords', [])
    if keywords:
        kw_list = keywords[:8] if isinstance(keywords, list) else []
        if kw_list:
            parts.append(f"Keywords: {', '.join(kw_list)}")
    
    # Overview (truncated)
    overview = item.get('overview', '')
    if overview:
        overview_short = overview[:max_overview_len] + '...' if len(overview) > max_overview_len else overview
        parts.append(f"Plot: {overview_short}")
    
    # Tagline
    tagline = item.get('tagline', '')
    if tagline:
        tagline_short = tagline[:120]
        parts.append(f"Tagline: {tagline_short}")
    
    # Cast/Directors (max 4 people)
    cast = item.get('cast', [])
    if cast:
        cast_list = cast[:4] if isinstance(cast, list) else []
        if cast_list:
            parts.append(f"Cast: {', '.join(cast_list)}")
    
    # Studios/Network
    studios = item.get('production_companies', [])
    if studios:
        studio_str = studios[0] if isinstance(studios, list) and studios else str(studios)[:50]
        parts.append(f"Studio: {studio_str}")
    
    network = item.get('network', '')
    if network:
        parts.append(f"Network: {network}")
    
    # Rating + Votes + Popularity
    rating = item.get('vote_average') or item.get('rating')
    votes = item.get('vote_count') or item.get('votes')
    if rating:
        parts.append(f"Rating: {float(rating):.1f}/10")
    if votes:
        parts.append(f"Votes: {int(votes)}")
    
    popularity = item.get('popularity')
    if popularity:
        parts.append(f"Pop: {float(popularity):.1f}")
    
    # Language
    language = item.get('language') or item.get('original_language')
    if language:
        parts.append(f"Lang: {language}")
    
    # Runtime
    runtime = item.get('runtime')
    if runtime:
        parts.append(f"Runtime: {int(runtime)}min")
    
    # Certification
    cert = item.get('certification')
    if cert:
        parts.append(f"Cert: {cert}")
    
    # Status
    status = item.get('status')
    if status and status not in ['Released', 'Ended']:
        parts.append(f"Status: {status}")
    
    # TV-specific
    if media_type == 'show':
        seasons = item.get('season_count')
        episodes = item.get('episode_count')
        if seasons:
            parts.append(f"Seasons: {seasons}")
        if episodes:
            parts.append(f"Episodes: {episodes}")
    
    # Computed scores
    obscurity = item.get('obscurity_score')
    if obscurity is not None:
        parts.append(f"Obscurity: {float(obscurity):.2f}")
    
    return ' | '.join(parts)


class PairwiseRanker:
    """
    LLM-based pairwise tournament ranking with phi3:mini.
    - Batches pairs into single LLM calls for efficiency
    - Uses tournament scheduling with weighted sampling (favors high CE scores)
    - Aggregates wins to produce final ranking
    """
    def __init__(self, model_url: str = "http://ollama:11434/api/generate", model_name: str = "phi3.5:3.8b-mini-instruct-q4_K_M") -> None:
        self.model_url = model_url
        self.model_name = model_name

    def rank(
        self,
        items: List[Dict[str, Any]],
        user_context: Dict[str, Any],
        intent: str,
        persona: str = "",
        history: str = "",
        max_pairs: int = 120,
        batch_size: int = 12
    ) -> Tuple[List[int], int]:
        """Run pairwise tournament with LLM judge to produce final ranking.
        
        Args:
            items: List of candidate dicts with scores
            user_context: User context dict
            intent: Compact one-line intent
            persona: User persona text (3-line)
            history: History summary (one-line)
            max_pairs: Maximum number of pairwise comparisons
            batch_size: Pairs per LLM batch call
            
        Returns:
            (ordered_indices, pairs_used)
        """
        if not items:
            return [], 0
            
        N = len(items)
        if N <= 1:
            return list(range(N)), 0
        
        # Limit to top K candidates based on budget
        K = min(self._max_n_for_pairs(max_pairs), N, 60)  # Hard cap at 60
        
        # Weighted sampling: higher CE-scored items compared more often
        ce_scores = [item.get('final_score', 0.5) for item in items]
        top_k_indices = sorted(range(N), key=lambda i: ce_scores[i], reverse=True)[:K]
        
        # Generate pairs with probabilistic weighting
        pairs = self._sample_pairs_weighted(top_k_indices, ce_scores, max_pairs)
        
        # Batch pairs and call LLM
        wins = {i: 0 for i in top_k_indices}
        matches_played = {i: 0 for i in top_k_indices}
        pairs_used = 0
        
        for batch_start in range(0, len(pairs), batch_size):
            batch_pairs = pairs[batch_start:batch_start + batch_size]
            if pairs_used >= max_pairs:
                break
                
            # Call LLM with batch
            try:
                results = self._call_llm_batch(batch_pairs, items, intent, persona, history)
                
                for result in results:
                    left_idx = result['left_id']
                    right_idx = result['right_id']
                    winner = result['winner']
                    
                    matches_played[left_idx] = matches_played.get(left_idx, 0) + 1
                    matches_played[right_idx] = matches_played.get(right_idx, 0) + 1
                    
                    if winner == 'left':
                        wins[left_idx] = wins.get(left_idx, 0) + 1
                    elif winner == 'right':
                        wins[right_idx] = wins.get(right_idx, 0) + 1
                    elif winner == 'tie':
                        wins[left_idx] = wins.get(left_idx, 0) + 0.5
                        wins[right_idx] = wins.get(right_idx, 0) + 0.5
                    
                    pairs_used += 1
                    if pairs_used >= max_pairs:
                        break
            except Exception as e:
                logger.warning(f"[PairwiseRanker] LLM batch failed: {e}")
                continue
        
        # Compute win rates and sort
        win_rates = {}
        for idx in top_k_indices:
            played = matches_played.get(idx, 1)
            win_rates[idx] = wins.get(idx, 0) / played if played > 0 else 0.5
        
        # Final ranking: top K by win rate, rest by CE score
        top_k_sorted = sorted(top_k_indices, key=lambda i: win_rates[i], reverse=True)
        remaining = [i for i in range(N) if i not in top_k_indices]
        
        return top_k_sorted + remaining, pairs_used
    
    @staticmethod
    def _max_n_for_pairs(budget_pairs: int, hard_cap: int = 60) -> int:
        """Find largest N such that N*(N-1)/2 <= budget_pairs, limited by hard_cap."""
        if budget_pairs <= 1:
            return 2
        n = int((1.0 + math.sqrt(1.0 + 8.0 * float(budget_pairs))) // 2)
        return max(2, min(n, hard_cap))
    
    def _sample_pairs_weighted(
        self,
        indices: List[int],
        scores: List[float],
        max_pairs: int
    ) -> List[Tuple[int, int]]:
        """Sample pairs with probabilistic weighting favoring high-scoring items."""
        K = len(indices)
        if K <= 1:
            return []
        
        # Create weight distribution (favor high scores)
        weights = [scores[i] + 0.1 for i in indices]  # +0.1 to avoid zero weights
        total_weight = sum(weights)
        probs = [w / total_weight for w in weights]
        
        pairs = []
        attempts = 0
        seen = set()
        
        # Target: ~6-12 comparisons per candidate
        target_pairs = min(max_pairs, K * 8)
        
        while len(pairs) < target_pairs and attempts < target_pairs * 3:
            # Weighted random sampling
            idx_a = random.choices(indices, weights=probs)[0]
            idx_b = random.choices(indices, weights=probs)[0]
            
            if idx_a != idx_b:
                pair = tuple(sorted([idx_a, idx_b]))
                if pair not in seen:
                    pairs.append((pair[0], pair[1]))
                    seen.add(pair)
            
            attempts += 1
        
        return pairs[:max_pairs]
    
    def _call_llm_batch(
        self,
        pairs: List[Tuple[int, int]],
        items: List[Dict[str, Any]],
        intent: str,
        persona: str,
        history: str
    ) -> List[Dict[str, Any]]:
        """Call phi3:mini with batched pairwise comparisons."""
        import requests
        from app.services.ai_engine.intent_extractor import validate_json_response
        
        # Build prompt
        pairs_text = []
        for i, (left_idx, right_idx) in enumerate(pairs, 1):
            left_item = items[left_idx]
            right_item = items[right_idx]
            
            left_summary = format_item_summary(left_item, max_overview_len=160)
            right_summary = format_item_summary(right_item, max_overview_len=160)
            
            pairs_text.append(f"{i}) left: {{id:{left_idx}, {left_summary}}}")
            pairs_text.append(f"   right: {{id:{right_idx}, {right_summary}}}")
        
        prompt = f"""SYSTEM:
You are WatchBuddy's strict comparator. Given a user intent & taste persona, compare each LEFT vs RIGHT pair and decide which is a better recommendation for the user.

**CRITICAL: You MUST return ONLY a valid JSON array. No explanations, no markdown, no extra text.**

Expected format: [{{"left_id":<int>,"right_id":<int>,"winner":"left"|"right"|"tie","reason":"≤10 words"}}]

USER:
Intent: {intent[:200]}
Persona: {persona[:300]}
History: {history[:150]}

Pairs:
{chr(10).join(pairs_text)}

Constraints:
- Use absolute judgments based on user intent and persona
- Prefer concision
- winner must be exactly "left", "right", or "tie"
- Return ONLY the JSON array, ensure all brackets are closed

EXAMPLE OUTPUT:
[{{"left_id":0,"right_id":1,"winner":"left","reason":"better genre match"}},{{"left_id":2,"right_id":3,"winner":"tie","reason":"both equally relevant"}}]

**Output the JSON array now:**
"""
        
        try:
            resp = requests.post(
                self.model_url,
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "options": {"temperature": 0.0, "num_predict": 512, "num_ctx": 4096},
                    "stream": False,
                    "keep_alive": "24h"
                },
                timeout=60
            )
            data = resp.json()
            output = data.get("response", "")
            
            # Use validation wrapper
            results = validate_json_response(output, expected_structure="array")
            if results and isinstance(results, list):
                return results
            
            logger.warning(f"[PairwiseRanker] LLM returned invalid JSON. Raw output: {output[:400]}")
            return []
            
        except Exception as e:
            logger.error(f"[PairwiseRanker] LLM call failed: {e}")
            return []
