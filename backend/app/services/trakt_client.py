class TraktAPIError(Exception):
    """Base exception for Trakt API errors."""
    pass

class TraktAuthError(TraktAPIError):
    """Raised when Trakt authentication fails or token is missing/expired."""
    pass

class TraktNetworkError(TraktAPIError):
    """Raised when network or connection to Trakt fails."""
    pass

class TraktUnavailableError(TraktAPIError):
    """Raised when Trakt API is offline or unavailable."""
    pass

"""
trakt_client.py

Async Trakt API client with Redis caching, rate limiting, and exponential backoff.
Secrets (client id/secret, access tokens) are stored in Redis-based settings for zero-config.
"""

import asyncio
import httpx
import json
from typing import Any, Dict, Optional, List
from app.core.redis_client import get_redis

TRAKT_API_URL = "https://api.trakt.tv"
REDIS_GLOBAL_PREFIX = "settings:global:"
REDIS_USER_PREFIX = "settings:user:"

class TraktClient:
    async def _get_refresh_token(self) -> Optional[str]:
        # Try to get refresh token from user-specific storage
        if self.user_id:
            token_json = await self._redis.get(f"trakt_tokens:{self.user_id}")
            if token_json:
                try:
                    data = json.loads(token_json)
                    return data.get("refresh_token")
                except Exception:
                    return None
        return None

    async def _store_tokens(self, access_token: str, refresh_token: Optional[str] = None, expires_in: Optional[int] = None):
        # Store new tokens in both new and legacy locations for compatibility
        if self.user_id:
            await self._redis.set(f"{REDIS_USER_PREFIX}{self.user_id}:trakt_access_token", access_token)
            token_data = {"access_token": access_token}
            if refresh_token:
                token_data["refresh_token"] = refresh_token
            if expires_in:
                token_data["expires_in"] = expires_in
            await self._redis.set(f"trakt_tokens:{self.user_id}", json.dumps(token_data))

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self._client_id = None
        self._client_secret = None
        self._access_token = None
        self._redis = get_redis()

    async def _load_secrets(self):
        # Load secrets from Redis-based settings
        self._client_id = await self._redis.get(REDIS_GLOBAL_PREFIX + "trakt_client_id")
        self._client_secret = await self._redis.get(REDIS_GLOBAL_PREFIX + "trakt_client_secret")
        # Prefer user-specific token if available
        if self.user_id:
            # New storage location
            token = await self._redis.get(f"{REDIS_USER_PREFIX}{self.user_id}:trakt_access_token")
            if token:
                self._access_token = token
            else:
                # Back-compat: token stored as JSON under trakt_tokens:{user_id}
                token_json = await self._redis.get(f"trakt_tokens:{self.user_id}")
                if token_json:
                    try:
                        data = json.loads(token_json)
                        self._access_token = data.get("access_token") or data.get("token")
                    except Exception:
                        self._access_token = None

    async def _get_headers(self) -> Dict[str, str]:
        import logging
        logger = logging.getLogger(__name__)
        if not self._client_id or not self._access_token:
            await self._load_secrets()
        if not self._client_id:
            logger.error("Trakt client_id is missing (not configured in Redis)")
            raise TraktAuthError("Trakt integration is not configured. Please contact your administrator.")
        if not self._access_token:
            logger.error(f"Trakt access_token is missing for user_id={self.user_id}")
            raise TraktAuthError("Trakt account is not authorized. Please reauthorize your Trakt account.")
        return {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self._client_id,
            "Authorization": f"Bearer {self._access_token}",
        }

    async def _request(self, method: str, endpoint: str, params: Optional[dict] = None, data: Optional[dict] = None, max_retries: int = 5) -> Any:
        import logging
        logger = logging.getLogger(__name__)
        url = f"{TRAKT_API_URL}{endpoint}"
        headers = await self._get_headers()
        cache_key = f"trakt:{method}:{endpoint}:{json.dumps(params, sort_keys=True) if params else ''}:{json.dumps(data, sort_keys=True) if data else ''}"

        # Check Redis cache
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)

        # No fallback to global access token: always require user-specific token
        if not self._access_token:
            logger.error(f"Trakt access_token is missing for user_id={self.user_id}")
            raise TraktAuthError("Trakt account is not authorized. Please reauthorize your Trakt account.")

        from app.services.rate_limit import with_backoff

        async def make_request(headers_override=None):
            use_headers = headers_override if headers_override else headers
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.request(method, url, headers=use_headers, params=params, json=data)
                    if resp.status_code == 401:
                        # Try to refresh token if possible
                        refresh_token = await self._get_refresh_token()
                        if refresh_token and self._client_id and self._client_secret:
                            refresh_payload = {
                                "refresh_token": refresh_token,
                                "client_id": self._client_id,
                                "client_secret": self._client_secret,
                                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                                "grant_type": "refresh_token"
                            }
                            refresh_url = f"{TRAKT_API_URL}/oauth/token"
                            refresh_resp = await client.post(refresh_url, json=refresh_payload, headers={
                                "Content-Type": "application/json",
                                "trakt-api-version": "2",
                                "trakt-api-key": self._client_id
                            })
                            if refresh_resp.is_success:
                                tokens = refresh_resp.json()
                                new_access = tokens.get("access_token")
                                new_refresh = tokens.get("refresh_token")
                                expires_in = tokens.get("expires_in")
                                if new_access:
                                    await self._store_tokens(new_access, new_refresh, expires_in)
                                    self._access_token = new_access
                                    # Retry original request with new token
                                    new_headers = dict(use_headers)
                                    new_headers["Authorization"] = f"Bearer {new_access}"
                                    resp = await client.request(method, url, headers=new_headers, params=params, json=data)
                                    resp.raise_for_status()
                                    result = resp.json()
                                    await self._redis.set(cache_key, json.dumps(result), ex=300)
                                    return result
                            # If refresh fails, raise error
                            logger.error("Trakt access token expired and refresh failed.")
                            raise TraktAuthError("Trakt access token expired and refresh failed. Please reauthorize your Trakt account.")
                        else:
                            logger.error("Trakt access token expired and no refresh token available.")
                            raise TraktAuthError("Trakt access token expired and no refresh token available. Please reauthorize your Trakt account.")
                    resp.raise_for_status()
                    result = resp.json()
                    await self._redis.set(cache_key, json.dumps(result), ex=300)
                    return result
            except httpx.ConnectTimeout:
                logger.error("Network timeout connecting to Trakt API.")
                raise TraktNetworkError("Network timeout connecting to Trakt API. Please check your connection or try again later.")
            except httpx.ConnectError:
                logger.error("Network error connecting to Trakt API.")
                raise TraktNetworkError("Network error connecting to Trakt API. Please check your connection or try again later.")
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (502, 503, 504):
                    logger.error(f"Trakt API is currently unavailable (status {status}).")
                    raise TraktUnavailableError("Trakt API is currently offline or unavailable. Please try again later.")
                elif status == 429:
                    logger.error("Trakt API rate limit exceeded.")
                    raise TraktUnavailableError("Trakt API rate limit exceeded. Please wait and try again.")
                else:
                    logger.error(f"Trakt API returned HTTP error {status}: {e}")
                    raise TraktAPIError(f"Trakt API error: {e}")
            except httpx.RequestError as e:
                logger.error(f"Network error connecting to Trakt API: {e}")
                raise TraktNetworkError("Network error connecting to Trakt API. Please check your connection or try again later.")
            except Exception as e:
                logger.error(f"Unexpected error in Trakt API request: {e}")
                raise TraktAPIError(f"Unexpected error in Trakt API request: {e}")

        return await with_backoff(
            make_request,
            max_retries=max_retries,
            service="trakt_api",
            user_id=str(self.user_id) if self.user_id else "anon"
        )

    async def get_user_profile(self) -> Dict[str, Any]:
        """Return the authenticated user's profile (basic public fields)."""
        # Trakt requires /users/me for authenticated user info
        endpoint = "/users/me"
        return await self._request("GET", endpoint)

    async def get_user_settings(self) -> Dict[str, Any]:
        """Return the authenticated user's account settings (includes VIP)."""
        endpoint = "/users/settings"
        return await self._request("GET", endpoint)

    async def get_user_history(self, username: str, media_type: str = "movies", limit: int = 100) -> Any:
        endpoint = f"/users/{username}/history/{media_type}"
        params = {"limit": limit}
        return await self._request("GET", endpoint, params=params)

    async def get_my_history(self, media_type: str = "movies", limit: int = 1000) -> List[Dict]:
        """Fetch authenticated user's watch history in bulk (first N)."""
        # Trakt supports pagination; do a couple of pages to get up to limit
        collected: List[Dict] = []
        page = 1
        per_page = min(100, limit)
        while len(collected) < limit:
            endpoint = f"/users/me/history/{media_type}"
            params = {"limit": per_page, "page": page}
            batch = await self._request("GET", endpoint, params=params)
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return collected[:limit]

    async def search(self, query: str, media_type: str = "movie", limit: int = 10) -> Any:
        endpoint = f"/search/{media_type}"
        params = {"query": query, "limit": limit}
        return await self._request("GET", endpoint, params=params)

    async def get_recommendations(self, media_type: str = "movies", limit: int = 10) -> Any:
        endpoint = f"/recommendations/{media_type}"
        params = {"limit": limit}
        return await self._request("GET", endpoint, params=params)

    async def get_trending(self, media_type: str = "movies", limit: int = 50) -> Any:
        endpoint = f"/movies/trending" if media_type == "movies" else "/shows/trending"
        params = {"limit": min(100, limit)}
        return await self._request("GET", endpoint, params=params)

    async def get_popular(self, media_type: str = "movies", limit: int = 50) -> Any:
        endpoint = f"/movies/popular" if media_type == "movies" else "/shows/popular"
        params = {"limit": min(100, limit)}
        return await self._request("GET", endpoint, params=params)

    async def get_item_details(self, media_type: str, trakt_id: int) -> Dict[str, Any]:
        """Fetch a single item by Trakt ID for fallback metadata (e.g., title).
        media_type: 'movie' or 'show'
        """
        item_type = "movie" if media_type == "movie" else "show"
        endpoint = f"/{'movies' if item_type=='movie' else 'shows'}/{trakt_id}"
        params = {"extended": "full"}
        return await self._request("GET", endpoint, params=params)

    async def search_by_tmdb_id(self, tmdb_id: int, media_type: Optional[str] = None) -> List[Dict]:
        """Search Trakt by TMDB ID to retrieve corresponding Trakt item(s).
        If media_type is provided ("movie" or "show"), it will be used to disambiguate.
        """
        endpoint = f"/search/tmdb/{tmdb_id}"
        params = {}
        if media_type in ("movie", "show"):
            params["type"] = media_type
        try:
            results = await self._request("GET", endpoint, params=params)
            return results or []
        except Exception:
            return []

    async def get_watched_movies(self) -> List[Dict]:
        """Get all movies the user has watched."""
        endpoint = "/users/me/watched/movies"
        return await self._request("GET", endpoint)

    async def get_watched_shows(self) -> List[Dict]:
        """Get all shows the user has watched."""
        endpoint = "/users/me/watched/shows"
        return await self._request("GET", endpoint)

    async def get_watched_status(self, media_type: str = "movies") -> Dict[int, Dict]:
        """Get watched status for all items of a media type, indexed by Trakt ID."""
        if media_type == "movies":
            watched_items = await self.get_watched_movies()
        else:
            watched_items = await self.get_watched_shows()
        
        # Convert to dict indexed by Trakt ID for quick lookup
        watched_dict = {}
        for item in watched_items:
            if media_type == "movies":
                trakt_id = item.get("movie", {}).get("ids", {}).get("trakt")
                if trakt_id:
                    watched_dict[trakt_id] = {
                        "watched_at": item.get("last_watched_at"),
                        "plays": item.get("plays", 1)
                    }
            else:
                show = item.get("show", {})
                trakt_id = show.get("ids", {}).get("trakt")
                if trakt_id:
                    watched_dict[trakt_id] = {
                        "watched_at": item.get("last_watched_at"),
                        "seasons": item.get("seasons", [])
                    }
        return watched_dict

    async def check_item_watched(self, trakt_id: int, media_type: str = "movies") -> tuple[bool, Optional[str]]:
        """Check if a specific item is watched. Returns (is_watched, watched_at)."""
        watched_status = await self.get_watched_status(media_type)
        item_status = watched_status.get(trakt_id)
        if item_status:
            return True, item_status.get("watched_at")
        return False, None

    async def create_list(self, name: str, description: str = "Created and managed by WatchBuddy", 
                         privacy: str = "private") -> Dict[str, Any]:
        """Create a new list on Trakt.
        
        Args:
            name: List name
            description: List description (default: "Created and managed by WatchBuddy")
            privacy: List privacy setting ("private", "friends", or "public")
            
        Returns:
            Dict containing list details including 'ids' with 'trakt' ID
        """
        endpoint = "/users/me/lists"
        data = {
            "name": name,
            "description": description,
            "privacy": privacy,
            "display_numbers": True,
            "allow_comments": False
        }
        return await self._request("POST", endpoint, data=data)
    
    async def update_list(self, trakt_list_id: str, name: Optional[str] = None, 
                         description: Optional[str] = None) -> Dict[str, Any]:
        """Update an existing list on Trakt.
        
        Args:
            trakt_list_id: Trakt list ID (slug or numeric ID)
            name: New list name (optional)
            description: New list description (optional)
            
        Returns:
            Dict containing updated list details
        """
        endpoint = f"/users/me/lists/{trakt_list_id}"
        data = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        
        return await self._request("PUT", endpoint, data=data)
    
    async def delete_list(self, trakt_list_id: str) -> bool:
        """Delete a list from Trakt.
        
        Args:
            trakt_list_id: Trakt list ID (slug or numeric ID)
            
        Returns:
            True if successful
        """
        endpoint = f"/users/me/lists/{trakt_list_id}"
        try:
            await self._request("DELETE", endpoint)
            return True
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to delete Trakt list {trakt_list_id}: {e}")
            return False
    
    async def add_items_to_list(self, trakt_list_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Add items to a Trakt list.
        
        Args:
            trakt_list_id: Trakt list ID (slug or numeric ID)
            items: List of items in format: [{"movies": [...], "shows": [...]}]
                   Each movie/show should have at minimum {"ids": {"trakt": 123}}
                   
        Returns:
            Dict with added/existing/not_found counts
        """
        endpoint = f"/users/me/lists/{trakt_list_id}/items"
        
        # Organize items by type
        payload = {"movies": [], "shows": []}
        for item in items:
            media_type = item.get("media_type", "movie")
            trakt_id = item.get("trakt_id")
            
            if not trakt_id:
                continue
            
            item_data = {"ids": {"trakt": int(trakt_id)}}
            
            if media_type == "movie":
                payload["movies"].append(item_data)
            elif media_type == "show":
                payload["shows"].append(item_data)
        
        return await self._request("POST", endpoint, data=payload)
    
    async def remove_items_from_list(self, trakt_list_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Remove items from a Trakt list.
        
        Args:
            trakt_list_id: Trakt list ID (slug or numeric ID)
            items: List of items to remove
            
        Returns:
            Dict with deleted/not_found counts
        """
        endpoint = f"/users/me/lists/{trakt_list_id}/items/remove"
        
        # Organize items by type
        payload = {"movies": [], "shows": []}
        for item in items:
            media_type = item.get("media_type", "movie")
            trakt_id = item.get("trakt_id")
            
            if not trakt_id:
                continue
            
            item_data = {"ids": {"trakt": int(trakt_id)}}
            
            if media_type == "movie":
                payload["movies"].append(item_data)
            elif media_type == "show":
                payload["shows"].append(item_data)
        
        return await self._request("POST", endpoint, data=payload)
    
    async def get_list_items(self, trakt_list_id: str) -> List[Dict[str, Any]]:
        """Get all items from a Trakt list.
        
        Args:
            trakt_list_id: Trakt list ID (slug or numeric ID)
            
        Returns:
            List of items with their details
        """
        endpoint = f"/users/me/lists/{trakt_list_id}/items"
        return await self._request("GET", endpoint)
    
    async def sync_list_items(self, trakt_list_id: str, desired_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Synchronize a Trakt list to match desired items.
        
        This will add missing items and remove items that shouldn't be there.
        
        Args:
            trakt_list_id: Trakt list ID
            desired_items: List of items that should be in the list
            
        Returns:
            Dict with sync statistics
        """
        # Get current list items
        current_items = await self.get_list_items(trakt_list_id)
        
        # Build set of current trakt IDs
        current_ids = set()
        for item in current_items:
            movie = item.get("movie")
            show = item.get("show")
            if movie:
                trakt_id = movie.get("ids", {}).get("trakt")
                if trakt_id:
                    current_ids.add(trakt_id)
            elif show:
                trakt_id = show.get("ids", {}).get("trakt")
                if trakt_id:
                    current_ids.add(trakt_id)
        
        # Build set of desired trakt IDs
        desired_ids = set()
        for item in desired_items:
            trakt_id = item.get("trakt_id")
            if trakt_id and isinstance(trakt_id, int):
                desired_ids.add(trakt_id)
        
        # Calculate what to add and remove
        to_add = desired_ids - current_ids
        to_remove = current_ids - desired_ids
        
        stats = {
            "added": 0,
            "removed": 0,
            "unchanged": len(current_ids & desired_ids)
        }
        
        # Add missing items
        if to_add:
            items_to_add = [item for item in desired_items if item.get("trakt_id") in to_add]
            if items_to_add:
                add_result = await self.add_items_to_list(trakt_list_id, items_to_add)
                stats["added"] = add_result.get("added", {}).get("movies", 0) + add_result.get("added", {}).get("shows", 0)
        
        # Remove extra items
        if to_remove:
            items_to_remove = []
            for trakt_id in to_remove:
                # Find the item type from current_items
                for item in current_items:
                    movie = item.get("movie")
                    show = item.get("show")
                    if movie and movie.get("ids", {}).get("trakt") == trakt_id:
                        items_to_remove.append({"media_type": "movie", "trakt_id": trakt_id})
                        break
                    elif show and show.get("ids", {}).get("trakt") == trakt_id:
                        items_to_remove.append({"media_type": "show", "trakt_id": trakt_id})
                        break
            
            if items_to_remove:
                remove_result = await self.remove_items_from_list(trakt_list_id, items_to_remove)
                stats["removed"] = remove_result.get("deleted", {}).get("movies", 0) + remove_result.get("deleted", {}).get("shows", 0)
        
        return stats

    async def close(self):
        # Explicit resource cleanup
        if self._redis:
            await self._redis.close()
        del self._client_id
        del self._client_secret
        del self._access_token
        del self._redis
        import gc
        gc.collect()
