"""
Debug script to check user profile generation and fit scoring.
"""
import sys
sys.path.insert(0, '/app')

from app.services.user_profile import UserProfileService
from app.services.fit_scoring import FitScorer
import json

def main():
    user_id = 1
    
    print("=" * 80)
    print(f"Checking user profile for user_id={user_id}")
    print("=" * 80)
    
    # Check profile
    profile_service = UserProfileService(user_id)
    profile = profile_service.get_profile(force_refresh=True)
    
    print("\n📊 User Profile:")
    print(json.dumps(profile, indent=2))
    
    # Check if profile has data
    total_watched = profile.get('total_watched', 0)
    print(f"\n✅ Total watched items: {total_watched}")
    
    if total_watched == 0:
        print("\n⚠️  WARNING: No watch history found!")
        print("   This explains why fit_score is stuck at 0.5 (neutral)")
        print("\n   To fix this:")
        print("   1. Ensure Trakt is connected and authorized")
        print("   2. Check that Trakt tokens are valid in Redis")
        print("   3. Verify Trakt history sync is working")
        return
    
    # Check genre weights
    genre_weights = profile.get('genre_weights', {})
    print(f"\n📈 Genre weights ({len(genre_weights)} genres):")
    for genre, weight in sorted(genre_weights.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"   {genre}: {weight:.3f}")
    
    # Check recent items
    recent_tmdb_ids = profile.get('recent_tmdb_ids', [])
    print(f"\n🕒 Recent TMDb IDs ({len(recent_tmdb_ids)} items): {recent_tmdb_ids[:10]}")
    
    # Test fit scorer with sample candidates
    print("\n" + "=" * 80)
    print("Testing FitScorer with sample candidates")
    print("=" * 80)
    
    fit_scorer = FitScorer(user_id)
    
    # Sample candidates
    sample_candidates = [
        {
            'tmdb_id': 550,
            'media_type': 'movie',
            'title': 'Fight Club',
            'genres': ['Drama', 'Thriller', 'Action'],
            'popularity': 45.2
        },
        {
            'tmdb_id': 13,
            'media_type': 'movie',
            'title': 'Forrest Gump',
            'genres': ['Drama', 'Romance'],
            'popularity': 60.8
        },
        {
            'tmdb_id': 603,
            'media_type': 'movie',
            'title': 'The Matrix',
            'genres': ['Action', 'Science Fiction'],
            'popularity': 55.1
        }
    ]
    
    scored = fit_scorer.score_candidates(sample_candidates.copy())
    
    print("\n📊 Scored candidates:")
    for item in scored:
        score = item.get('fit_score', 0.5)
        components = item.get('_score_components', {})
        print(f"\n   {item['title']}")
        print(f"      Fit Score: {score:.3f}")
        if components:
            print(f"      Components: genre={components.get('genre', 0):.3f}, "
                  f"similarity={components.get('similarity', 0):.3f}, "
                  f"popularity={components.get('popularity', 0):.3f}")
    
    # Check if all scores are 0.5
    all_neutral = all(abs(item.get('fit_score', 0.5) - 0.5) < 0.01 for item in scored)
    
    if all_neutral:
        print("\n❌ ISSUE DETECTED: All fit scores are ~0.5 (neutral)")
        print("   Possible causes:")
        print("   1. Profile has no usable data (no genres, no embeddings)")
        print("   2. FAISS index is missing embeddings for recent items")
        print("   3. Scoring weights are misconfigured")
    else:
        print("\n✅ Fit scores are varying correctly!")

if __name__ == "__main__":
    main()
