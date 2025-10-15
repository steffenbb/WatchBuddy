"""
Trakt OAuth endpoints for authentication flow.
"""
import json
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query
from app.core.redis_client import get_redis
from app.services.trakt_client import TraktClient
import httpx

router = APIRouter()
logger = logging.getLogger(__name__)

# Trakt OAuth configuration - now loaded dynamically from Redis
TRAKT_REDIRECT_URI = "http://localhost:5173/auth/trakt/callback"

async def get_trakt_credentials():
    """Get Trakt credentials from Redis settings."""
    redis = get_redis()
    client_id = await redis.get("settings:global:trakt_client_id")
    client_secret = await redis.get("settings:global:trakt_client_secret")
    
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400, 
            detail="Trakt API credentials not configured. Please set them up first."
        )
    
    return client_id, client_secret

@router.get("/oauth/url")
async def get_oauth_url():
    """Get Trakt OAuth authorization URL."""
    # Get dynamic credentials
    client_id, client_secret = await get_trakt_credentials()
    
    # Generate state parameter for security
    import uuid
    state = str(uuid.uuid4())
    
    # Store state in Redis for validation
    redis = get_redis()
    await redis.setex(f"trakt_oauth_state:{state}", 600, "pending")  # 10 min expiry
    
    auth_url = (
        f"https://trakt.tv/oauth/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={TRAKT_REDIRECT_URI}"
        f"&state={state}"
    )
    
    return {"auth_url": auth_url, "state": state}

@router.post("/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...)
):
    """Handle OAuth callback and exchange code for tokens."""
    redis = get_redis()
    
    # Validate state parameter
    state_key = f"trakt_oauth_state:{state}"
    stored_state = await redis.get(state_key)
    if not stored_state:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    
    # Clean up state
    await redis.delete(state_key)
    
    # Get dynamic credentials
    client_id, client_secret = await get_trakt_credentials()
    
    try:
        # Exchange code for access token
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://api.trakt.tv/oauth/token",
                json={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": TRAKT_REDIRECT_URI,
                    "grant_type": "authorization_code"
                },
                headers={
                    "Content-Type": "application/json",
                    "trakt-api-version": "2",
                    "trakt-api-key": client_id
                }
            )
        
        if not token_response.is_success:
            logger.error(f"Token exchange failed: {token_response.text}")
            raise HTTPException(status_code=400, detail="Failed to exchange code for token")
        
        token_data = token_response.json()
        
        # Store tokens in Redis (for user_id=1 since we don't have proper auth yet)
        user_id = 1
        token_key = f"trakt_tokens:{user_id}"
        await redis.setex(
            token_key,
            token_data.get("expires_in", 86400),  # Use token expiry or 24h default
            json.dumps({
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "expires_at": token_data.get("expires_in"),
                "token_type": token_data.get("token_type", "Bearer")
            })
        )
        
        # Fetch user info to confirm authentication
        try:
            trakt_client = TraktClient(user_id=user_id)
            user_info = await trakt_client.get_user_profile()
            
            # Store user info
            user_key = f"trakt_user:{user_id}"
            await redis.setex(
                user_key,
                86400,  # 24h
                json.dumps({
                    "username": user_info.get("username"),
                    "name": user_info.get("name"),
                    "joined_at": user_info.get("joined_at"),
                    "location": user_info.get("location"),
                    "about": user_info.get("about")
                })
            )
            
            return {
                "success": True,
                "user": {
                    "username": user_info.get("username"),
                    "name": user_info.get("name")
                }
            }
            
        except Exception as e:
            logger.warning(f"Failed to fetch user info after auth: {e}")
            return {"success": True, "user": None}
    
    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.get("/status")
async def get_auth_status():
    """Get current Trakt authentication status, including VIP when available."""
    redis = get_redis()
    user_id = 1  # Default user for demo

    authenticated = False
    user_info = None
    vip = None
    vip_ep = None
    error = None

    try:
        # Check token in either user or global storage
        has_user_token = await redis.get(f"settings:user:{user_id}:trakt_access_token")
        has_legacy = await redis.get(f"trakt_tokens:{user_id}")
        has_global = await redis.get("settings:global:trakt_access_token")
        authenticated = bool(has_user_token or has_legacy or has_global)

        if authenticated:
            try:
                client = TraktClient(user_id=user_id)
                # Get profile and settings (VIP present in settings)
                profile = await client.get_user_profile()
                settings = await client.get_user_settings()
                account = settings.get("account") if isinstance(settings, dict) else None
                vip = bool(account.get("vip")) if isinstance(account, dict) else None
                vip_ep = bool(account.get("vip_ep")) if isinstance(account, dict) else None
                user_info = {
                    "username": profile.get("username") if isinstance(profile, dict) else None,
                    "name": profile.get("name") if isinstance(profile, dict) else None,
                    "vip": vip,
                    "vip_ep": vip_ep,
                }
            except Exception as inner:
                # Still report authenticated but with limited info
                error = f"profile/settings fetch failed: {inner}"

        return {
            "authenticated": authenticated,
            "user": user_info,
            "vip": vip,
            "vip_ep": vip_ep,
            "error": error
        }
    except Exception as e:
        logger.error(f"Failed to check auth status: {e}")
        return {"authenticated": False, "user": None, "error": str(e)}

@router.delete("/disconnect")
async def disconnect():
    """Disconnect Trakt account."""
    redis = get_redis()
    user_id = 1  # Default user for demo
    
    try:
        # Remove tokens and user data
        await redis.delete(f"trakt_tokens:{user_id}")
        await redis.delete(f"trakt_user:{user_id}")
        
        return {"success": True, "message": "Trakt account disconnected"}
    
    except Exception as e:
        logger.error(f"Failed to disconnect: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect account")

from fastapi import Query

@router.get("/test")
async def test_connection(user_id: int = Query(1, description="User ID to test Trakt connection for")):
    """Test Trakt API connection for a specific user, with clear error if token or secret is missing."""
    try:
        trakt_client = TraktClient(user_id=user_id)
        # Try to fetch trending movies as a simple test
        trending = await trakt_client.get_trending(media_type="movies", limit=5)
        return {
            "success": True,
            "message": f"Trakt API connection successful for user_id={user_id}",
            "sample_data": len(trending) if trending else 0
        }
    except RuntimeError as e:
        logger.error(f"Trakt connection test failed for user_id={user_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    except Exception as e:
        logger.error(f"Trakt connection test failed for user_id={user_id}: {e}")
        return {
            "success": False,
            "error": f"Unexpected error: {e}"
        }