#!/usr/bin/env python3
"""Debug enrichment data structures"""
import asyncio
import sys
import os
sys.path.append(os.path.abspath('.'))

from app.core.database import SessionLocal
from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.services.trakt_client import TraktClient
from app.models import UserList

async def debug_enrichment():
    print("Debugging enrichment data structures...")
    
    db = SessionLocal()
    try:
        # Get the Danish list
        user_list = db.query(UserList).filter(UserList.id == 41).first()
        if not user_list:
            print("List 41 not found")
            return
            
        print(f"List: {user_list.title}")
        print(f"Filters: {user_list.filters}")
        
        # Create provider
        provider = BulkCandidateProvider(user_id=1)  # Use a default user ID
        
        # Get some basic search results
        trakt_client = TraktClient()
        results = await trakt_client.search('danish movie', media_type='movie', limit=5)
        print(f"\nRaw search results count: {len(results)}")
        
        if results:
            print(f"First result structure: {list(results[0].keys())}")
            
            # Try enriching just one item
            enriched = await provider._enrich_with_tmdb_metadata([results[0]], 'movie')
            print(f"\nEnriched count: {len(enriched)}")
            
            if enriched:
                item = enriched[0]
                print(f"Enriched item keys: {list(item.keys())}")
                
                if 'tmdb_data' in item:
                    print(f"tmdb_data type: {type(item['tmdb_data'])}")
                    print(f"tmdb_data keys: {list(item['tmdb_data'].keys()) if isinstance(item['tmdb_data'], dict) else 'NOT A DICT'}")
                    
                    if 'genres' in item['tmdb_data']:
                        genres = item['tmdb_data']['genres']
                        print(f"Genres type: {type(genres)}")
                        print(f"Genres content: {genres}")
                        if isinstance(genres, list) and genres:
                            print(f"First genre type: {type(genres[0])}")
                            print(f"First genre content: {genres[0]}")
                else:
                    print("No tmdb_data in enriched item")
                    
                if 'cached_metadata' in item:
                    print(f"cached_metadata keys: {list(item['cached_metadata'].keys())}")
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(debug_enrichment())