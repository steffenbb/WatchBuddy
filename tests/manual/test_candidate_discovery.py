#!/usr/bin/env python3
"""
Test script for enhanced candidate discovery pool
Usage: docker exec -i watchbuddy-backend-1 python /app/tests/manual/test_candidate_discovery.py
"""
import sys
sys.path.append('/app')

import asyncio
import logging
from app.services.bulk_candidate_provider import BulkCandidateProvider

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_candidate_discovery():
    """Test different discovery modes and measure candidate pool sizes."""
    
    user_id = 1
    provider = BulkCandidateProvider(user_id)
    
    test_cases = [
        {"name": "Balanced (Default)", "discovery": "balanced", "limit": 500},
        {"name": "Deep Discovery", "discovery": "deep_discovery", "limit": 500},
        {"name": "Ultra Discovery", "discovery": "ultra_discovery", "limit": 1000},
        {"name": "Ultra Discovery Large", "discovery": "ultra_discovery", "limit": 2000},
    ]
    
    results = {}
    
    for test_case in test_cases:
        logger.info(f"\n=== Testing {test_case['name']} ===")
        try:
            candidates = await provider.get_candidates(
                media_type="movies",
                limit=test_case["limit"],
                discovery=test_case["discovery"],
                enrich_with_tmdb=False,  # Skip TMDB for faster testing
                include_watched=True  # Include watched for larger pool
            )
            
            candidate_count = len(candidates)
            results[test_case['name']] = candidate_count
            
            logger.info(f"✅ {test_case['name']}: {candidate_count} candidates (target: {test_case['limit']})")
            
            # Calculate coverage ratio
            coverage = (candidate_count / test_case['limit']) * 100
            logger.info(f"   Coverage: {coverage:.1f}% of requested limit")
            
            if candidate_count >= 5000:
                logger.info(f"   🎯 ACHIEVED 5000+ CANDIDATE GOAL!")
            
        except Exception as e:
            logger.error(f"❌ {test_case['name']} failed: {e}")
            results[test_case['name']] = 0
    
    # Summary
    logger.info(f"\n=== CANDIDATE DISCOVERY TEST RESULTS ===")
    for name, count in results.items():
        status = "✅" if count >= 1000 else "⚠️" if count >= 500 else "❌"
        logger.info(f"{status} {name}: {count} candidates")
    
    best_result = max(results.values()) if results else 0
    if best_result >= 5000:
        logger.info(f"🎯 SUCCESS: Achieved {best_result} candidates (5000+ goal met!)")
    else:
        logger.info(f"📊 Best result: {best_result} candidates (target: 5000+)")

if __name__ == "__main__":
    asyncio.run(test_candidate_discovery())