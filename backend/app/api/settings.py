def extract_error_message(e: Exception) -> str:
    import traceback
    if hasattr(e, 'detail') and e.detail:
        return str(e.detail)
    elif hasattr(e, 'args') and e.args:
        return str(e.args[0])
    else:
        return f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
"""
settings.py

API endpoints for user and app settings using Redis-based storage (no DB secrets).
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Optional
import httpx
import json

from app.core.redis_client import get_redis

router = APIRouter()

class TMDBKeyRequest(BaseModel):
    api_key: str

class FusionSettingsRequest(BaseModel):
    enabled: bool
    weights: Optional[Dict[str, float]] = None
    aggressiveness: Optional[int] = None  # 0=Low, 1=Med (default), 2=High

class TimezoneSettingsRequest(BaseModel):
    timezone: str  # IANA timezone identifier (e.g., "America/New_York", "Europe/Copenhagen")

class TraktClientConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str

REDIS_SETTINGS_PREFIX = "settings:global:"

async def _settings_get(key: str) -> Optional[str]:
    r = get_redis()
    return await r.get(REDIS_SETTINGS_PREFIX + key)

async def _settings_set(key: str, value: str):
    r = get_redis()
    await r.set(REDIS_SETTINGS_PREFIX + key, value)

@router.get("")
async def get_settings():
    fusion_enabled = await _settings_get("fusion_enabled")
    fusion_weights = await _settings_get("fusion_weights")
    user_timezone = await _settings_get("user_timezone")
    return {
        "theme": "dark",
        "notifications": True,
        "timezone": user_timezone or "UTC",  # Default to UTC if not set
        "fusion": {
            "enabled": fusion_enabled == "true" if fusion_enabled else False,
            "weights": json.loads(fusion_weights) if fusion_weights else {
                "components.genre": 0.30,
                "components.semantic": 0.25,
                "components.mood": 0.20,
                "components.rating": 0.10,
                "components.novelty": 0.05,
                "trending": 0.07,
                "history": 0.03,
            }
        }
    }

@router.post("/trakt-config")
async def set_trakt_config(cfg: TraktClientConfig):
    r = get_redis()
    await r.set(REDIS_SETTINGS_PREFIX + "trakt_client_id", cfg.client_id)
    await r.set(REDIS_SETTINGS_PREFIX + "trakt_client_secret", cfg.client_secret)
    await r.set(REDIS_SETTINGS_PREFIX + "trakt_redirect_uri", cfg.redirect_uri)
    return {"success": True}

@router.get("/trakt-oauth-url")
async def get_trakt_oauth_url():
    r = get_redis()
    client_id = await r.get(REDIS_SETTINGS_PREFIX + "trakt_client_id")
    redirect_uri = await r.get(REDIS_SETTINGS_PREFIX + "trakt_redirect_uri")
    if not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail="Trakt not configured")
    url = (
        "https://api.trakt.tv/oauth/authorize"
        f"?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"
    )
    return {"url": url}

@router.post("/trakt-oauth-callback")
async def trakt_oauth_callback(payload: Dict[str, str]):
    code = payload.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    r = get_redis()
    client_id = await r.get(REDIS_SETTINGS_PREFIX + "trakt_client_id")
    client_secret = await r.get(REDIS_SETTINGS_PREFIX + "trakt_client_secret")
    redirect_uri = await r.get(REDIS_SETTINGS_PREFIX + "trakt_redirect_uri")
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(status_code=400, detail="Trakt not configured")
    # Exchange code for token
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.trakt.tv/oauth/token",
            json={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/json"}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="OAuth exchange failed")
        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="No access token returned")
        # For now store a single global access token
        await r.set(REDIS_SETTINGS_PREFIX + "trakt_access_token", access_token)
    return {"success": True}

@router.get("/status")
async def get_settings_status():
    """Minimal status used by the app to decide if setup is complete."""
    r = get_redis()
    
    # Check if Trakt credentials exist
    client_id = await r.get(REDIS_SETTINGS_PREFIX + "trakt_client_id")
    client_secret = await r.get(REDIS_SETTINGS_PREFIX + "trakt_client_secret")
    
    # Check if user is authenticated (has tokens)
    user_id = 1  # Default user for demo
    token_data = await r.get(f"trakt_tokens:{user_id}")
    # Also check new/global token locations
    if not token_data:
        token_data = await r.get(f"settings:user:{user_id}:trakt_access_token") or await r.get(REDIS_SETTINGS_PREFIX + "trakt_access_token")
    
    # Trakt is "configured" if we have both credentials AND authentication
    trakt_configured = bool(client_id and client_secret and token_data)
    
    tmdb_key = await r.get(REDIS_SETTINGS_PREFIX + "tmdb_api_key")
    return {
        "subscription": True,
        "trakt_configured": trakt_configured,
        "tmdb_configured": bool(tmdb_key),
    }

@router.post("/tmdb-key")
async def set_tmdb_key(request: TMDBKeyRequest):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.themoviedb.org/3/configuration",
                params={"api_key": request.api_key}
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Invalid TMDB API key")
        await _settings_set("tmdb_api_key", request.api_key)
        
        # No metadata builder needed - we use TMDB IDs directly
        # Bootstrap import sets completion flag to prevent auto-trigger
        
        return {"success": True}
    except httpx.RequestError as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to validate TMDB API key: {msg}")

@router.get("/tmdb-key/status")
async def get_tmdb_key_status():
    key = await _settings_get("tmdb_api_key")
    if not key:
        return {"configured": False}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.themoviedb.org/3/configuration",
                params={"api_key": key}
            )
            return {"configured": True, "valid": resp.status_code == 200}
    except Exception as e:
        # Optionally log error: logger.error(f"TMDB key status error: {extract_error_message(e)}")
        return {"configured": True, "valid": False}

@router.post("/fusion")
async def set_fusion_settings(request: FusionSettingsRequest):
    if request.weights:
        total = sum(request.weights.values())
        if abs(total - 1.0) > 0.1:
            raise HTTPException(status_code=400, detail=f"Fusion weights should sum to ~1.0, got {total}")
        await _settings_set("fusion_weights", json.dumps(request.weights))
    # Persist aggressiveness when provided
    if request.aggressiveness is not None:
        try:
            val = int(request.aggressiveness)
            if val not in (0, 1, 2):
                raise ValueError()
            await _settings_set("fusion_aggressiveness", str(val))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid aggressiveness (expected 0, 1, or 2)")
    await _settings_set("fusion_enabled", "true" if request.enabled else "false")
    return {"success": True}

@router.get("/timezone")
async def get_timezone_settings():
    """Get user's timezone preference."""
    user_timezone = await _settings_get("user_timezone")
    return {
        "timezone": user_timezone or "UTC",
        "available_timezones": [
            # Common timezones grouped by region
            {"group": "UTC", "zones": [{"id": "UTC", "label": "UTC (Coordinated Universal Time)"}]},
            {"group": "Americas", "zones": [
                {"id": "America/New_York", "label": "Eastern Time (New York)"},
                {"id": "America/Chicago", "label": "Central Time (Chicago)"},
                {"id": "America/Denver", "label": "Mountain Time (Denver)"},
                {"id": "America/Los_Angeles", "label": "Pacific Time (Los Angeles)"},
                {"id": "America/Toronto", "label": "Toronto"},
                {"id": "America/Vancouver", "label": "Vancouver"},
                {"id": "America/Mexico_City", "label": "Mexico City"},
                {"id": "America/Sao_Paulo", "label": "São Paulo"},
                {"id": "America/Argentina/Buenos_Aires", "label": "Buenos Aires"}
            ]},
            {"group": "Europe", "zones": [
                {"id": "Europe/London", "label": "London (GMT/BST)"},
                {"id": "Europe/Paris", "label": "Paris (CET/CEST)"},
                {"id": "Europe/Berlin", "label": "Berlin (CET/CEST)"},
                {"id": "Europe/Rome", "label": "Rome (CET/CEST)"},
                {"id": "Europe/Madrid", "label": "Madrid (CET/CEST)"},
                {"id": "Europe/Amsterdam", "label": "Amsterdam (CET/CEST)"},
                {"id": "Europe/Copenhagen", "label": "Copenhagen (CET/CEST)"},
                {"id": "Europe/Stockholm", "label": "Stockholm (CET/CEST)"},
                {"id": "Europe/Oslo", "label": "Oslo (CET/CEST)"},
                {"id": "Europe/Helsinki", "label": "Helsinki (EET/EEST)"},
                {"id": "Europe/Warsaw", "label": "Warsaw (CET/CEST)"},
                {"id": "Europe/Moscow", "label": "Moscow (MSK)"}
            ]},
            {"group": "Asia & Pacific", "zones": [
                {"id": "Asia/Tokyo", "label": "Tokyo (JST)"},
                {"id": "Asia/Seoul", "label": "Seoul (KST)"},
                {"id": "Asia/Shanghai", "label": "Shanghai (CST)"},
                {"id": "Asia/Hong_Kong", "label": "Hong Kong (HKT)"},
                {"id": "Asia/Singapore", "label": "Singapore (SGT)"},
                {"id": "Asia/Bangkok", "label": "Bangkok (ICT)"},
                {"id": "Asia/Dubai", "label": "Dubai (GST)"},
                {"id": "Asia/Kolkata", "label": "India (IST)"},
                {"id": "Australia/Sydney", "label": "Sydney (AEST/AEDT)"},
                {"id": "Australia/Melbourne", "label": "Melbourne (AEST/AEDT)"},
                {"id": "Australia/Perth", "label": "Perth (AWST)"},
                {"id": "Pacific/Auckland", "label": "Auckland (NZST/NZDT)"}
            ]}
        ]
    }

@router.post("/timezone")
async def set_timezone_settings(request: TimezoneSettingsRequest):
    """Set user's timezone preference."""
    # Validate timezone
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(request.timezone)
    except Exception:
        # Fallback validation for systems without zoneinfo
        valid_timezones = [
            "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
            "America/Toronto", "America/Vancouver", "Europe/London", "Europe/Paris", "Europe/Berlin",
            "Europe/Copenhagen", "Europe/Stockholm", "Europe/Oslo", "Asia/Tokyo", "Asia/Seoul",
            "Asia/Shanghai", "Asia/Singapore", "Australia/Sydney", "Pacific/Auckland"
        ]
        if request.timezone not in valid_timezones:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {request.timezone}")
    
    await _settings_set("user_timezone", request.timezone)
    return {"success": True, "timezone": request.timezone}

@router.post("/reauthorize-trakt")
async def reauthorize_trakt():
    """Clear existing Trakt tokens and credentials to restart authorization."""
    redis = get_redis()
    try:
        # Clear all Trakt-related data
        await redis.delete("settings:global:trakt_client_id")
        await redis.delete("settings:global:trakt_client_secret")
        await redis.delete("settings:global:trakt_access_token")
        
        # Clear user tokens (user_id=1 for demo)
        user_id = 1
        await redis.delete(f"trakt_tokens:{user_id}")
        await redis.delete(f"trakt_user:{user_id}")
        
        return {"success": True, "message": "Trakt authorization cleared. Please set up again."}
    except Exception as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to clear authorization: {msg}")

@router.get("/tmdb-key")
async def get_tmdb_key():
    """Get TMDB API key status."""
    try:
        key = await _settings_get("tmdb_api_key")
        return {
            "configured": bool(key),
            "key_preview": f"...{key[-4:]}" if key and len(key) > 4 else None
        }
    except Exception as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to get TMDB key: {msg}")

@router.post("/tmdb-key") 
async def set_tmdb_key(request: TMDBKeyRequest):
    """Set and validate TMDB API key."""
    try:
        # Validate the key by making a test request
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.themoviedb.org/3/configuration",
                params={"api_key": request.api_key}
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Invalid TMDB API key")
        
        # Save the key
        await _settings_set("tmdb_api_key", request.api_key)
        return {"success": True, "message": "TMDB API key saved and validated"}
    except httpx.RequestError as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to validate TMDB API key: {msg}")

@router.delete("/tmdb-key")
async def delete_tmdb_key():
    """Delete TMDB API key."""
    try:
        redis = get_redis()
        await redis.delete("settings:global:tmdb_api_key")
        return {"success": True, "message": "TMDB API key deleted"}
    except Exception as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to delete TMDB key: {msg}")

@router.get("/trakt-credentials")
async def get_trakt_credentials():
    """Check if Trakt credentials are configured."""
    redis = get_redis()
    try:
        client_id = await redis.get("settings:global:trakt_client_id")
        client_secret = await redis.get("settings:global:trakt_client_secret")
        
        return {
            "configured": bool(client_id and client_secret),
            "has_client_id": bool(client_id),
            "has_client_secret": bool(client_secret)
        }
    except Exception as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to check credentials: {msg}")

@router.post("/trakt-credentials")
async def save_trakt_credentials(payload: dict):
    """Save Trakt API credentials."""
    client_id = payload.get("client_id", "").strip()
    client_secret = payload.get("client_secret", "").strip()
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Both client_id and client_secret are required")
    
    redis = get_redis()
    try:
        await redis.set("settings:global:trakt_client_id", client_id)
        await redis.set("settings:global:trakt_client_secret", client_secret)
        
        return {"success": True, "message": "Trakt credentials saved successfully"}
    except Exception as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to save credentials: {msg}")

@router.delete("/trakt-credentials")
async def delete_trakt_credentials():
    """Delete Trakt API credentials."""
    redis = get_redis()
    try:
        await redis.delete("settings:global:trakt_client_id")
        await redis.delete("settings:global:trakt_client_secret")
        
        return {"success": True, "message": "Trakt credentials deleted"}
    except Exception as e:
        msg = extract_error_message(e)
        raise HTTPException(status_code=500, detail=f"Failed to delete credentials: {msg}")

@router.get("/validate-setup")
async def validate_setup():
    """Validate both Trakt and TMDB configurations are set up and working."""
    redis = get_redis()
    errors = []
    warnings = []
    
    # Check Trakt credentials
    client_id = await redis.get("settings:global:trakt_client_id")
    client_secret = await redis.get("settings:global:trakt_client_secret")
    
    if not client_id or not client_secret:
        errors.append("Trakt API credentials not configured")
    
    # Check Trakt authentication
    user_id = 1
    token_data = await redis.get(f"trakt_tokens:{user_id}")
    if not token_data:
        token_data = await redis.get(f"settings:user:{user_id}:trakt_access_token") or await redis.get("settings:global:trakt_access_token")
    
    if not token_data:
        errors.append("Trakt not authenticated. Please complete OAuth flow")
    
    # Check TMDB API key
    tmdb_key = await redis.get("settings:global:tmdb_api_key")
    if not tmdb_key:
        errors.append("TMDB API key not configured")
    else:
        # Validate TMDB key works
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    "https://api.themoviedb.org/3/configuration",
                    params={"api_key": tmdb_key}
                )
                if resp.status_code != 200:
                    errors.append("TMDB API key is invalid or expired")
        except Exception as e:
            warnings.append(f"Could not validate TMDB key: {str(e)}")
    
    is_valid = len(errors) == 0
    
    return {
        "valid": is_valid,
        "trakt_configured": bool(client_id and client_secret),
        "trakt_authenticated": bool(token_data),
        "tmdb_configured": bool(tmdb_key),
        "errors": errors,
        "warnings": warnings,
        "message": "Setup complete" if is_valid else "Setup incomplete"
    }

@router.get("/fusion")
async def get_fusion_settings():
    fusion_enabled = await _settings_get("fusion_enabled")
    fusion_weights = await _settings_get("fusion_weights")
    fusion_aggr = await _settings_get("fusion_aggressiveness")
    return {
        "enabled": fusion_enabled == "true" if fusion_enabled else False,
        "weights": json.loads(fusion_weights) if fusion_weights else {
            "components.genre": 0.30,
            "components.semantic": 0.25,
            "components.mood": 0.20,
            "components.rating": 0.10,
            "components.novelty": 0.05,
            "trending": 0.07,
            "history": 0.03,
        },
        "aggressiveness": int(fusion_aggr) if isinstance(fusion_aggr, str) and fusion_aggr.isdigit() else 1
    }
