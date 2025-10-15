#!/usr/bin/env python3
import sys
sys.path.append('/app')

import asyncio
from app.services.mood import ensure_user_mood, compute_mood_vector_for_tmdb, get_contextual_mood_adjustment

async def test_mood_integration():
    print("=== Enhanced Mood Analysis Test ===")
    
    # 1. Ensure user mood with fallback
    user_mood = await ensure_user_mood(1, fallback_strategy=True)
    print(f"1. User mood from SmartLists: {user_mood}")
    
    # 2. Get contextual adjustments
    contextual = get_contextual_mood_adjustment()
    print(f"2. Contextual adjustments: {contextual}")
    
    # 3. Enhanced mood
    enhanced_mood = user_mood.copy()
    for mood, adjustment in contextual.items():
        enhanced_mood[mood] = enhanced_mood.get(mood, 0) + adjustment
    print(f"3. Enhanced user mood: {enhanced_mood}")
    
    # 4. Test candidate mood computation
    print(f"\n4. Testing candidate mood computation:")
    
    action_movie = {
        'genres': [{'name': 'Action'}, {'name': 'Thriller'}],
        'keywords': [{'name': 'explosion'}, {'name': 'fight'}, {'name': 'chase'}],
        'runtime': 132, 'vote_average': 8.2, 'popularity': 85.0
    }
    
    romance_movie = {
        'genres': [{'name': 'Romance'}, {'name': 'Drama'}],
        'keywords': [{'name': 'love'}, {'name': 'wedding'}, {'name': 'relationship'}],
        'runtime': 123, 'vote_average': 7.8, 'popularity': 65.0
    }
    
    comedy_show = {
        'genres': [{'name': 'Comedy'}],
        'keywords': [{'name': 'friendship'}, {'name': 'fun'}],
        'runtime': 22, 'vote_average': 8.5, 'popularity': 95.0
    }
    
    candidates = [
        ("Action Movie (Die Hard style)", action_movie),
        ("Romance Movie (The Notebook style)", romance_movie),
        ("Comedy Show (The Office style)", comedy_show)
    ]
    
    def cosine_similarity(v1, v2):
        import math
        all_keys = set(v1.keys()).union(v2.keys())
        dot_product = sum(v1.get(k, 0) * v2.get(k, 0) for k in all_keys)
        norm1 = math.sqrt(sum(v1.get(k, 0)**2 for k in all_keys))
        norm2 = math.sqrt(sum(v2.get(k, 0)**2 for k in all_keys))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)
    
    for name, tmdb_data in candidates:
        cand_mood = compute_mood_vector_for_tmdb(tmdb_data)
        mood_score = cosine_similarity(enhanced_mood, cand_mood)
        
        print(f"\n  {name}:")
        print(f"    Candidate mood: {cand_mood}")
        print(f"    Mood score vs user: {mood_score:.3f}")
        
        # Show which moods align
        top_cand_moods = sorted(cand_mood.items(), key=lambda x: x[1], reverse=True)[:3]
        top_user_moods = sorted(enhanced_mood.items(), key=lambda x: x[1], reverse=True)[:3]
        print(f"    Top candidate moods: {[(m, round(v, 3)) for m, v in top_cand_moods if v > 0]}")
        print(f"    Top user moods: {[(m, round(v, 3)) for m, v in top_user_moods if v > 0]}")

if __name__ == "__main__":
    asyncio.run(test_mood_integration())