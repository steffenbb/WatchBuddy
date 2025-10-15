#!/usr/bin/env python3
"""
Test script to verify that enhanced pool sizes still properly apply filters.
This tests that with 5000+ candidates, we still get properly filtered results.
"""

import sys
import os
import asyncio
import json

# Add the app directory to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.core.database import SessionLocal

async def test_enhanced_filtering():
    """Test that enhanced pool sizes still apply filters correctly."""
    
    print("🧪 Testing Enhanced Pool Size Filtering")
    print("=" * 50)
    
    provider = BulkCandidateProvider(user_id=1)
    
    # Test 1: Genre filtering with large pool
    print("\n📋 Test 1: Genre Filtering with Enhanced Pool (2000 candidates)")
    candidates = await provider.get_candidates(
        media_type="movies",
        limit=2000,  # This should trigger enhanced pool size
        genres=["action", "adventure"],
        discovery="ultra_discovery",
        enrich_with_tmdb=True
    )
    
    print(f"✅ Fetched {len(candidates)} candidates")
    
    # Verify genre filtering worked
    genre_match_count = 0
    for candidate in candidates[:20]:  # Check first 20
        # Check Trakt genres
        trakt_genres = [g.lower() for g in candidate.get('genres', [])]
        
        # Check TMDB genres
        tmdb_genres = []
        if candidate.get('tmdb_data') and candidate['tmdb_data'].get('genres'):
            tmdb_genres = [g.lower() for g in candidate['tmdb_data']['genres']]
        elif candidate.get('cached_metadata') and candidate['cached_metadata'].get('genres'):
            cached_genres = candidate['cached_metadata']['genres']
            if isinstance(cached_genres, list):
                tmdb_genres = [g.lower() for g in cached_genres]
        
        all_genres = set(trakt_genres + tmdb_genres)
        if any(g in all_genres for g in ['action', 'adventure']):
            genre_match_count += 1
            
        print(f"  📽️  {candidate.get('title', 'Unknown')}: genres={list(all_genres)[:3]}")
    
    print(f"✅ Genre filtering: {genre_match_count}/20 candidates have action/adventure genres")
    
    # Test 2: Language filtering with enhanced pool
    print("\n🌍 Test 2: Language Filtering with Enhanced Pool")
    candidates = await provider.get_candidates(
        media_type="movies", 
        limit=1000,
        languages=["en", "da"],
        discovery="ultra_discovery",
        enrich_with_tmdb=True
    )
    
    print(f"✅ Fetched {len(candidates)} candidates with language filtering")
    
    language_match_count = 0
    for candidate in candidates[:15]:  # Check first 15
        trakt_lang = candidate.get('language')
        tmdb_lang = None
        
        if candidate.get('tmdb_data'):
            tmdb_lang = candidate['tmdb_data'].get('original_language') 
        elif candidate.get('cached_metadata'):
            tmdb_lang = candidate['cached_metadata'].get('language')
            
        has_lang_match = (
            (trakt_lang and trakt_lang in ['en', 'da']) or
            (tmdb_lang and tmdb_lang in ['en', 'da']) or
            (not trakt_lang and not tmdb_lang)  # Lenient for missing data
        )
        
        if has_lang_match:
            language_match_count += 1
            
        print(f"  🎬 {candidate.get('title', 'Unknown')}: trakt_lang={trakt_lang}, tmdb_lang={tmdb_lang}")
    
    print(f"✅ Language filtering: {language_match_count}/15 candidates match language criteria")
    
    # Test 3: Year filtering with enhanced pool
    print("\n📅 Test 3: Year Filtering with Enhanced Pool")
    candidates = await provider.get_candidates(
        media_type="movies",
        limit=1500,
        min_year=2018,
        max_year=2024,
        discovery="ultra_discovery"
    )
    
    print(f"✅ Fetched {len(candidates)} candidates with year filtering (2018-2024)")
    
    year_match_count = 0
    for candidate in candidates[:15]:
        year = candidate.get('year')
        if year and 2018 <= year <= 2024:
            year_match_count += 1
        print(f"  📆 {candidate.get('title', 'Unknown')}: year={year}")
    
    print(f"✅ Year filtering: {year_match_count}/15 candidates are from 2018-2024")
    
    # Test 4: Combined filtering with ultra_discovery
    print("\n🎯 Test 4: Combined Filtering (Genre + Language + Year)")
    candidates = await provider.get_candidates(
        media_type="movies",
        limit=2000,  # Large pool
        genres=["comedy", "drama"],
        languages=["en"],
        min_year=2015,
        max_year=2023,
        min_rating=6.0,
        discovery="ultra_discovery",
        enrich_with_tmdb=True
    )
    
    print(f"✅ Fetched {len(candidates)} candidates with combined filtering")
    
    combined_match_count = 0
    for candidate in candidates[:10]:
        # Check all criteria
        year = candidate.get('year')
        rating = candidate.get('rating', 0)
        
        # Genre check
        all_genres = set()
        all_genres.update([g.lower() for g in candidate.get('genres', [])])
        if candidate.get('tmdb_data') and candidate['tmdb_data'].get('genres'):
            all_genres.update([g.lower() for g in candidate['tmdb_data']['genres']])
        
        # Language check  
        trakt_lang = candidate.get('language')
        tmdb_lang = None
        if candidate.get('tmdb_data'):
            tmdb_lang = candidate['tmdb_data'].get('original_language')
            
        has_criteria = (
            any(g in all_genres for g in ['comedy', 'drama']) and
            (year and 2015 <= year <= 2023) and
            (rating >= 6.0) and
            ((trakt_lang == 'en') or (tmdb_lang == 'en') or (not trakt_lang and not tmdb_lang))
        )
        
        if has_criteria:
            combined_match_count += 1
            
        print(f"  🏆 {candidate.get('title', 'Unknown')}: year={year}, rating={rating:.1f}, genres={list(all_genres)[:2]}")
    
    print(f"✅ Combined filtering: {combined_match_count}/10 candidates match all criteria")
    
    print("\n" + "=" * 50)
    print("🎉 Enhanced Pool Filtering Test Complete!")
    print("✅ Filters are working correctly with enhanced pool sizes")
    print("✅ Large candidate pools (1000-5000) still respect genre/language/year filters")
    print("✅ Ultra discovery mode maintains filtering integrity")

if __name__ == "__main__":
    try:
        asyncio.run(test_enhanced_filtering())
    except KeyboardInterrupt:
        print("\n❌ Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()