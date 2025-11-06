"""
trakt_id_resolver.py

Service for resolving TMDB IDs to Trakt IDs with Redis caching.
Eliminates the need for pre-populating Trakt IDs in persistent_candidates table.

Cache Strategy:
- Cache key: trakt_lookup:tmdb:{tmdb_id}:{media_type}
- Cache TTL: 30 days (Trakt IDs rarely change)
- Batch lookup support for efficiency
"""

import logging
import json
from typing import Optional, Dict, List, Tuple
from app.core.redis_client import get_redis
from app.services.trakt_client import TraktClient

logger = logging.getLogger(__name__)

CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
CACHE_PREFIX = "trakt_lookup:tmdb:"


class TraktIdResolver:
    """
    Resolves TMDB IDs to Trakt IDs with caching.
    
    Usage:
        resolver = TraktIdResolver(user_id=1)
        trakt_id = await resolver.get_trakt_id(tmdb_id=550, media_type='movie')
        
        # Batch lookup
        mappings = await resolver.get_trakt_ids_batch([
            (550, 'movie'),
            (1396, 'show')
        ])
    """
    
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.trakt_client = TraktClient(user_id=user_id)
        self.redis = get_redis()
    
    async def get_trakt_id(self, tmdb_id: int, media_type: str) -> Optional[int]:
        """
        Get Trakt ID for a TMDB ID with caching.
        
        Args:
            tmdb_id: TMDB ID
            media_type: 'movie' or 'show'
            
        Returns:
            Trakt ID if found, None otherwise
        """
        if not tmdb_id:
            return None
        
        # Check cache first
        cache_key = f"{CACHE_PREFIX}{tmdb_id}:{media_type}"
        cached = await self.redis.get(cache_key)
        
        if cached:
            try:
                cached_data = json.loads(cached)
                trakt_id = cached_data.get('trakt_id')
                if trakt_id:
                    logger.debug(f"Cache hit for TMDB {tmdb_id} ({media_type}): Trakt ID {trakt_id}")
                    return int(trakt_id)
                else:
                    # Cached as not found
                    logger.debug(f"Cache hit for TMDB {tmdb_id} ({media_type}): Not found")
                    return None
            except Exception as e:
                logger.warning(f"Failed to parse cached Trakt ID for TMDB {tmdb_id}: {e}")
        
        # Cache miss - fetch from Trakt API
        logger.debug(f"Cache miss for TMDB {tmdb_id} ({media_type}), fetching from Trakt API")
        trakt_id = await self._fetch_trakt_id(tmdb_id, media_type)
        
        # Cache the result (even if None to avoid repeated API calls)
        await self._cache_result(tmdb_id, media_type, trakt_id)
        
        return trakt_id
    
    async def _fetch_trakt_id(self, tmdb_id: int, media_type: str) -> Optional[int]:
        """Fetch Trakt ID from API."""
        try:
            results = await self.trakt_client.search_by_tmdb_id(tmdb_id, media_type)
            
            if not results:
                logger.debug(f"No Trakt results for TMDB {tmdb_id} ({media_type})")
                return None
            
            # Get first result matching media type
            for result in results:
                if media_type in result:
                    ids = result[media_type].get('ids', {})
                    trakt_id = ids.get('trakt')
                    if trakt_id:
                        logger.debug(f"Found Trakt ID {trakt_id} for TMDB {tmdb_id} ({media_type})")
                        return int(trakt_id)
            
            logger.debug(f"No matching Trakt ID found for TMDB {tmdb_id} ({media_type})")
            return None
            
        except Exception as e:
            logger.warning(f"Failed to fetch Trakt ID for TMDB {tmdb_id} ({media_type}): {e}")
            return None
    
    async def _cache_result(self, tmdb_id: int, media_type: str, trakt_id: Optional[int]):
        """Cache the lookup result."""
        cache_key = f"{CACHE_PREFIX}{tmdb_id}:{media_type}"
        cache_data = {
            'tmdb_id': tmdb_id,
            'media_type': media_type,
            'trakt_id': trakt_id,
            'cached_at': str(datetime.now())
        }
        
        try:
            await self.redis.set(cache_key, json.dumps(cache_data), ex=CACHE_TTL)
            logger.debug(f"Cached Trakt lookup for TMDB {tmdb_id} ({media_type}): {trakt_id}")
        except Exception as e:
            logger.warning(f"Failed to cache Trakt ID for TMDB {tmdb_id}: {e}")
    
    async def get_trakt_ids_batch(self, items: List[Tuple[int, str]]) -> Dict[Tuple[int, str], Optional[int]]:
        """
        Batch lookup of Trakt IDs for multiple TMDB IDs.
        
        Args:
            items: List of (tmdb_id, media_type) tuples
            
        Returns:
            Dict mapping (tmdb_id, media_type) to Trakt ID
        """
        results = {}
        
        # Check cache for all items
        cache_hits = {}
        cache_misses = []
        
        for tmdb_id, media_type in items:
            cache_key = f"{CACHE_PREFIX}{tmdb_id}:{media_type}"
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    cached_data = json.loads(cached)
                    cache_hits[(tmdb_id, media_type)] = cached_data.get('trakt_id')
                else:
                    cache_misses.append((tmdb_id, media_type))
            except Exception as e:
                logger.warning(f"Cache read error for TMDB {tmdb_id}: {e}")
                cache_misses.append((tmdb_id, media_type))
        
        results.update(cache_hits)
        logger.debug(f"Batch lookup: {len(cache_hits)} cache hits, {len(cache_misses)} misses")
        
        # Fetch cache misses from API
        for tmdb_id, media_type in cache_misses:
            trakt_id = await self._fetch_trakt_id(tmdb_id, media_type)
            results[(tmdb_id, media_type)] = trakt_id
            await self._cache_result(tmdb_id, media_type, trakt_id)
        
        return results
    
    async def resolve_item(self, item: Dict) -> Dict:
        """
        Resolve Trakt ID for an item dict and add it to the dict.
        
        Args:
            item: Dict with 'tmdb_id' and 'media_type' keys
            
        Returns:
            Same dict with 'trakt_id' added if found
        """
        tmdb_id = item.get('tmdb_id')
        media_type = item.get('media_type')
        
        if tmdb_id and media_type:
            trakt_id = await self.get_trakt_id(tmdb_id, media_type)
            if trakt_id:
                item['trakt_id'] = trakt_id
        
        return item
    
    async def resolve_items_batch(self, items: List[Dict]) -> List[Dict]:
        """
        Resolve Trakt IDs for a batch of items.
        
        Args:
            items: List of dicts with 'tmdb_id' and 'media_type' keys
            
        Returns:
            Same list with 'trakt_id' added to each item if found
        """
        # Extract unique (tmdb_id, media_type) pairs
        lookup_pairs = []
        for item in items:
            tmdb_id = item.get('tmdb_id')
            media_type = item.get('media_type')
            if tmdb_id and media_type:
                lookup_pairs.append((tmdb_id, media_type))
        
        if not lookup_pairs:
            return items
        
        # Batch lookup
        trakt_mappings = await self.get_trakt_ids_batch(lookup_pairs)
        
        # Apply results to items
        for item in items:
            tmdb_id = item.get('tmdb_id')
            media_type = item.get('media_type')
            if tmdb_id and media_type:
                key = (tmdb_id, media_type)
                trakt_id = trakt_mappings.get(key)
                if trakt_id:
                    item['trakt_id'] = trakt_id
        
        return items
    
    async def clear_cache(self, tmdb_id: Optional[int] = None, media_type: Optional[str] = None):
        """
        Clear cached Trakt ID lookups.
        
        Args:
            tmdb_id: If provided, clear only this TMDB ID
            media_type: If provided with tmdb_id, clear only this combination
        """
        if tmdb_id and media_type:
            cache_key = f"{CACHE_PREFIX}{tmdb_id}:{media_type}"
            await self.redis.delete(cache_key)
            logger.info(f"Cleared cache for TMDB {tmdb_id} ({media_type})")
        elif tmdb_id:
            # Clear both movie and show
            await self.redis.delete(f"{CACHE_PREFIX}{tmdb_id}:movie")
            await self.redis.delete(f"{CACHE_PREFIX}{tmdb_id}:show")
            logger.info(f"Cleared cache for TMDB {tmdb_id} (both types)")
        else:
            # Clear all (use with caution)
            pattern = f"{CACHE_PREFIX}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Cleared all Trakt ID caches ({len(keys)} keys)")


# Import here to avoid circular dependency
from datetime import datetime
