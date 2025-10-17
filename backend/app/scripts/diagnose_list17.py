"""
Diagnostic script for List 17 - Why aren't we getting buddy cop comedies?
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.database import SessionLocal
from app.models import UserList, ListItem, PersistentCandidate
from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.services.scoring_engine import ScoringEngine
import json
import asyncio

async def diagnose():
    db = SessionLocal()
    try:
        # Get list 17
        user_list = db.query(UserList).filter(UserList.id == 17).first()
        if not user_list:
            print("List 17 not found!")
            return
        
        print(f"List Title: {user_list.title}")
        print(f"Filters: {json.dumps(json.loads(user_list.filters), indent=2)}")
        print("\n" + "="*80 + "\n")
        
        # Parse filters
        filters = json.loads(user_list.filters)
        
        # Check if the expected movies match the filters
        expected_titles = [
            "Ted",
            "21 Jump Street", 
            "This Is the End",
            "Project X",
            "We're the Millers",
            "The Hangover"
        ]
        
        print("CHECKING EXPECTED MOVIES IN DATABASE:\n")
        for title in expected_titles:
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.title == title,
                PersistentCandidate.media_type == 'movie'
            ).all()
            
            for cand in candidates:
                if cand.vote_count and cand.vote_count > 1000:  # Focus on the real ones
                    print(f"\n{cand.title}:")
                    print(f"  - Genres: {cand.genres}")
                    print(f"  - Year: {cand.release_year}")
                    print(f"  - Rating: {cand.vote_average} ({cand.vote_count} votes)")
                    print(f"  - Language: {cand.language}")
                    print(f"  - Obscurity Score: {cand.obscurity_score}")
                    print(f"  - Mainstream Score: {cand.mainstream_score}")
                    
                    # Check filter matching
                    genres_parsed = json.loads(cand.genres) if cand.genres else []
                    has_comedy = "Comedy" in genres_parsed
                    has_action = "Action" in genres_parsed
                    year_ok = cand.release_year and cand.release_year >= filters.get('year_from', 2000)
                    
                    print(f"  - Has Comedy: {has_comedy}")
                    print(f"  - Has Action: {has_action}")
                    print(f"  - Year >= 2000: {year_ok}")
        
        print("\n" + "="*80 + "\n")
        print("RUNNING BULK CANDIDATE PROVIDER:\n")
        
        # Get candidates using the provider
        provider = BulkCandidateProvider(db)
        candidates = await provider.get_candidates(
            user_id=1,
            filters=filters,
            limit=50  # Get more to see what we're getting
        )
        
        print(f"Got {len(candidates)} candidates from provider\n")
        print("Top 30 candidates by title:")
        for i, cand in enumerate(candidates[:30], 1):
            print(f"{i}. {cand.get('title')} - Obscurity: {cand.get('obscurity_score', 0):.3f}")
        
        print("\n" + "="*80 + "\n")
        print("ANALYZING FILTERS:\n")
        
        # Check what the filters are doing
        print(f"Genres: {filters.get('genres')}")
        print(f"Year from: {filters.get('year_from')}")
        print(f"Media types: {filters.get('media_types')}")
        print(f"Mood: {filters.get('mood')}")
        print(f"Similar to: {filters.get('similar_to_title')}")
        
        # Let's query what we SHOULD be getting
        print("\n" + "="*80 + "\n")
        print("DIRECT DATABASE QUERY (what we SHOULD get):\n")
        
        query = db.query(PersistentCandidate).filter(
            PersistentCandidate.media_type == 'movie',
            PersistentCandidate.release_year >= 2000,
            PersistentCandidate.vote_count > 1000  # Popular movies only
        )
        
        # Check for Comedy AND Action
        all_candidates = query.all()
        matching = []
        for cand in all_candidates:
            if cand.genres:
                genres_list = json.loads(cand.genres)
                if "Comedy" in genres_list and "Action" in genres_list:
                    matching.append(cand)
        
        # Sort by popularity
        matching.sort(key=lambda x: x.vote_count or 0, reverse=True)
        
        print(f"Found {len(matching)} Comedy+Action movies from 2000+ with 1000+ votes\n")
        print("Top 30 by popularity:")
        for i, cand in enumerate(matching[:30], 1):
            print(f"{i}. {cand.title} ({cand.release_year}) - Rating: {cand.vote_average}, Votes: {cand.vote_count}")
        
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(diagnose())
