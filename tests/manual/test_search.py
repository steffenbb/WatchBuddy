import asyncio
from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.services.trakt_client import TraktClient

async def test_global_search():
    provider = BulkCandidateProvider(1)
    try:
        # Test if global search finds Danish content
        results = await provider._fetch_global_content("movies", 10, ["comedy"])
        print(f"Global search: {len(results)} items")
        for item in results[:3]:
            print(f" - {item.get('title', 'No Title')} ({item.get('year')}) [{item.get('ids', {}).get('trakt')}]")
        
        # Test basic Trakt search
        tc = TraktClient(1)
        trakt_results = await tc.search("danish", "movie", 5)
        print(f"\nBasic Trakt search for 'danish': {len(trakt_results)} results")
        for item in trakt_results[:3]:
            movie = item.get('movie', {})
            print(f" - {movie.get('title', 'No Title')} ({movie.get('year')}) [{movie.get('ids', {}).get('trakt')}]")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        provider.db.close()

if __name__ == '__main__':
    asyncio.run(test_global_search())