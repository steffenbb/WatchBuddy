#!/usr/bin/env python3
import sys
sys.path.append('/app')

from app.services.scoring_engine import ScoringEngine
from app.services.mood import ensure_user_mood
import asyncio

async def test_mood_scoring():
    # Ensure mood is computed with fallback
    mood = await ensure_user_mood(1, fallback_strategy=True)
    print(f"User mood: {mood}")
    
    # Test contextual adjustments
    from app.services.mood import get_contextual_mood_adjustment
    contextual = get_contextual_mood_adjustment()
    print(f"Contextual adjustments: {contextual}")
    
    # Enhanced user mood (combining both)
    enhanced_mood = mood.copy()
    for m, adj in contextual.items():
        enhanced_mood[m] = enhanced_mood.get(m, 0) + adj
    print(f"Enhanced user mood: {enhanced_mood}")
    # Test mood computation manually
    from app.services.mood import compute_mood_vector_for_tmdb
    
    print(f"\nTesting individual candidate moods:")
    for candidate in test_candidates:
        tmdb_data = candidate.get('tmdb_data', {})
        cand_mood = compute_mood_vector_for_tmdb(tmdb_data)
        print(f"- {candidate['title']}: {cand_mood}")
        
        # Manual cosine similarity
        def cosine_sim(v1, v2):
            import math
            dot_product = sum(v1.get(k, 0) * v2.get(k, 0) for k in set(v1.keys()).union(v2.keys()))
            norm1 = math.sqrt(sum(v1.get(k, 0)**2 for k in v1))
            norm2 = math.sqrt(sum(v2.get(k, 0)**2 for k in v2))
            if norm1 == 0 or norm2 == 0:
                return 0
            return dot_product / (norm1 * norm2)
        
        manual_mood_score = cosine_sim(enhanced_mood, cand_mood)
        print(f"  Manual mood score vs enhanced user mood: {manual_mood_score:.3f}")
        print()
    test_candidates = [
        {
            'trakt_id': 123456,
            'tmdb_id': 123456,
            'title': 'Die Hard',
            'media_type': 'movie',
            'votes': 50000,
            'rating': 8.2,
            'overview': 'Action-packed thriller about a man fighting terrorists in a building.',
            'genres': ['Action', 'Thriller'],
            'tmdb_data': {
                'genres': [{'name': 'Action'}, {'name': 'Thriller'}],
                'keywords': [{'name': 'explosion'}, {'name': 'fight'}],
                'runtime': 132,
                'vote_average': 8.2,
                'popularity': 85.0
            }
        },
        {
            'trakt_id': 234567,
            'tmdb_id': 234567,
            'title': 'The Notebook',
            'media_type': 'movie',
            'votes': 30000,
            'rating': 7.8,
            'overview': 'A romantic drama about true love spanning decades.',
            'genres': ['Romance', 'Drama'],
            'tmdb_data': {
                'genres': [{'name': 'Romance'}, {'name': 'Drama'}],
                'keywords': [{'name': 'love'}, {'name': 'relationship'}],
                'runtime': 123,
                'vote_average': 7.8,
                'popularity': 65.0
            }
        },
        {
            'trakt_id': 345678,
            'tmdb_id': 345678,
            'title': 'The Office',
            'media_type': 'show',
            'votes': 40000,
            'rating': 8.5,
            'overview': 'Comedy series about office workers and their daily antics.',
            'genres': ['Comedy'],
            'tmdb_data': {
                'genres': [{'name': 'Comedy'}],
                'keywords': [{'name': 'friendship'}, {'name': 'workplace'}],
                'runtime': 22,
                'vote_average': 8.5,
                'popularity': 95.0
            }
        }
    ]
    
    # Test scoring
    scoring_engine = ScoringEngine()
    user = {'id': 1}
    
    print(f"\nScoring {len(test_candidates)} candidates:")
    scored = scoring_engine.score_candidates(
        user=user,
        candidates=test_candidates.copy(),
        list_type='smartlist',
        explore_factor=0.15
    )
    
    print("\nResults:")
    for item in scored:
        mood_score = item.get('mood_score', 0)
        final_score = item.get('final_score', 0)
        title = item.get('title', f"Item {item.get('trakt_id', 'unknown')}")
        print(f"- {title}: final_score={final_score:.3f}, mood_score={mood_score:.3f}")
        print(f"  Mood breakdown: excited={item.get('excited', 0):.3f}, happy={item.get('happy', 0):.3f}, tense={item.get('tense', 0):.3f}")
        print(f"  Other scores: genre_overlap={item.get('genre_overlap', 0):.3f}, semantic_sim={item.get('semantic_sim', 0):.3f}")
        print()

if __name__ == "__main__":
    asyncio.run(test_mood_scoring())