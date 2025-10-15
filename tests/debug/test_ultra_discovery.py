#!/usr/bin/env python3
import asyncio
from app.services.bulk_candidate_provider import BulkCandidateProvider

async def test():
    provider = BulkCandidateProvider(1)
    print("Testing ultra_discovery mode...")
    candidates = await provider.get_candidates(
        media_type="movies", 
        limit=1000, 
        discovery="ultra_discovery", 
        enrich_with_tmdb=False
    )
    print(f"Found {len(candidates)} candidates")
    if len(candidates) >= 5000:
        print("🎯 SUCCESS: 5000+ candidate goal achieved!")
    else:
        print(f"Current: {len(candidates)}, Target: 5000+")

asyncio.run(test())