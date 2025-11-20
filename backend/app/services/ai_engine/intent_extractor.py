import hashlib
import json
import re
import logging
from typing import Dict, Any, Optional
from app.core.redis_client import get_redis_sync

logger = logging.getLogger(__name__)

def validate_json_response(raw_output: str, expected_structure: str = "object") -> Optional[Any]:
    """
    Validate and parse JSON from LLM output.
    
    Args:
        raw_output: Raw LLM response text
        expected_structure: "object" for {}, "array" for []
    
    Returns:
        Parsed JSON object/array, or None if invalid
    """
    try:
        # Try to find JSON in output
        if expected_structure == "array":
            match = re.search(r'\[.*\]', raw_output, re.DOTALL)
        else:
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        
        if match:
            parsed = json.loads(match.group(0))
            return parsed
        else:
            logger.warning(f"[JSON_VALIDATION] No JSON structure found in output (expected {expected_structure}): {raw_output[:300]}")
            return None
    except json.JSONDecodeError as e:
        logger.error(f"[JSON_VALIDATION] JSONDecodeError: {e}. Raw output: {raw_output[:500]}")
        return None
    except Exception as e:
        logger.error(f"[JSON_VALIDATION] Unexpected error: {e}. Raw output: {raw_output[:500]}")
        return None

class IntentExtractor:
    """
    Converts NL query + persona/history into structured intent JSON for filters/query variants.
    Uses LLM (phi3:mini) with constrained JSON prompt, caches by query+persona+history hash.
    """
    @staticmethod
    def _cache_key(query: str, persona: str, history: str) -> str:
        # Truncate persona/history to prevent cache key collisions and reduce size
        # Keep query full for uniqueness
        # v2: genres merge fix - changed prompt and merge logic
        p_trunc = persona[:500] if persona else ""
        h_trunc = history[:500] if history else ""
        raw = json.dumps({"v": "2", "q": query, "p": p_trunc, "h": h_trunc}, separators=(",", ":"), sort_keys=True)
        return "intent:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def extract_intent(cls, query: str, persona: str, history: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        rds = get_redis_sync()
        cache_key = cls._cache_key(query, persona, history)
        if not force_refresh:
            cached = rds.get(cache_key)
            if cached:
                try:
                    result = json.loads(cached if isinstance(cached, str) else cached.decode("utf-8", errors="ignore"))
                    logger.debug(f"[IntentExtractor] Cache hit for query: {query[:50]}, cache_key: {cache_key}")
                    return result
                except Exception as e:
                    logger.debug(f"[IntentExtractor] Cache parse error: {e}")
        # Compose prompt for LLM with complete field specifications
        prompt = f"""SYSTEM:
You are a filter extractor. Given a user's natural request, persona, and history, output a JSON object with these fields.

**CRITICAL: You MUST return ONLY valid JSON. No explanations, no markdown, no extra text. Just the JSON object.**

REQUIRED FIELDS:
- required_genres: [str] (ONLY when user says "MUST be" or "ONLY" genres - leave empty otherwise!)
- optional_genres: [str] (suggested/nice-to-have genres - USE THIS BY DEFAULT)
- exclude_genres: [str] (genres to avoid)
- moods: [str] (emotional tone: "dark", "uplifting", "contemplative", "intense", etc.)
- tones: [str] (narrative style: "gritty", "whimsical", "realistic", "surreal", etc.)
- actors: [str] (ONLY actors explicitly mentioned by name - don't infer from movie titles!)
- directors: [str] (ONLY directors explicitly mentioned - don't infer from movie titles!)
- studios: [str] (production companies: "Pixar", "A24", "Warner Bros", etc.)
- runtime_min: int|null (minimum runtime in minutes)
- runtime_max: int|null (maximum runtime in minutes)
- era: str|null (decade/period: "1980s", "2010s", "golden age", "modern", etc.)
- popularity_pref: str (preference: "mainstream", "obscure", "indie", "blockbuster", "mixed")
- complexity: str (narrative complexity: "simple", "moderate", "complex", "mindbending")
- pacing: str (rhythm: "slow-burn", "fast-paced", "moderate", "contemplative")
- target_size: int (desired result count, default 30)
- negative_cues: [str] (things to avoid: "no musicals", "avoid slow pacing", etc.)
- query_variants: [str] (3-5 alternative phrasings of the query for semantic search)

USER:
Request: \"{query}\"
Persona: \"{persona}\"
History: \"{history}\"

EXAMPLE OUTPUT FORMAT (copy this structure):
{{"required_genres":[],"optional_genres":["Science Fiction","Drama"],"exclude_genres":["Horror"],"moods":["contemplative","hopeful"],"tones":["realistic"],"actors":[],"directors":[],"studios":[],"runtime_min":null,"runtime_max":null,"era":"2010s","popularity_pref":"mainstream","complexity":"complex","pacing":"slow-burn","target_size":30,"negative_cues":["no jump scares"],"query_variants":["space exploration drama","realistic sci-fi","thoughtful space movies"]}}

**CRITICAL RULES:**
1. Use optional_genres for all genre suggestions - required_genres should be EMPTY unless user explicitly says "MUST be" or "ONLY"
2. Do NOT extract actors/directors from movie titles (e.g., "Twilight" should NOT add Kristen Stewart)
3. Only add actors/directors if user explicitly mentions them by name

**IMPORTANT: Return ONLY the JSON object. Ensure all brackets are closed. Double-check JSON syntax before responding.**
"""
        # Call LLM (phi3:mini) via local API
        try:
            import requests
            # Truncate inputs to prevent prompt overflow (8K context limit)
            # Increased limits: we have 8192 context window, prompt template is ~1000 chars
            query_safe = query[:3000] if query else ""
            persona_safe = persona[:2000] if persona else ""
            history_safe = history[:2000] if history else ""
            
            # Rebuild prompt with truncated inputs
            prompt_safe = prompt.replace(f'"{query}"', f'"{query_safe}"').replace(f'"{persona}"', f'"{persona_safe}"').replace(f'"{history}"', f'"{history_safe}"')
            
            logger.info(f"[IntentExtractor] Calling Ollama with query (len={len(query)}, truncated to {len(query_safe)})")
            logger.debug(f"[IntentExtractor] Full prompt length: {len(prompt_safe)} chars")
            logger.debug(f"[IntentExtractor] Query preview: {query[:200]}..." if len(query) > 200 else f"[IntentExtractor] Query: {query}")
            
            # Retry logic for model loading (first request can take 20-30s)
            max_retries = 2
            retry_delay = 5
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    timeout_val = 60 if attempt == 0 else 30  # First attempt: 60s for model loading
                    logger.debug(f"[IntentExtractor] Attempt {attempt + 1}/{max_retries}, timeout={timeout_val}s")
                    
                    resp = requests.post(
                        "http://ollama:11434/api/generate",
                        json={
                            "model": "phi3.5:3.8b-mini-instruct-q4_K_M",
                            "prompt": prompt_safe,
                            "options": {"temperature": 0.0, "num_predict": 1024, "num_ctx": 8192},
                            "stream": False,
                            "keep_alive": "24h",
                        },
                        timeout=timeout_val,
                    )
                    break  # Success, exit retry loop
                except requests.Timeout as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(f"[IntentExtractor] Timeout on attempt {attempt + 1}, retrying in {retry_delay}s...")
                        import time
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    else:
                        raise  # Final attempt failed, re-raise
                except requests.RequestException as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(f"[IntentExtractor] Network error on attempt {attempt + 1}: {e}, retrying in {retry_delay}s...")
                        import time
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        raise
            
            if resp.status_code != 200:
                logger.warning(f"[IntentExtractor] LLM request failed with status {resp.status_code}")
                return None
            
            data = resp.json()
            output = data.get("response", "")
            
            # Use validation wrapper
            intent = validate_json_response(output, expected_structure="object")
            if intent:
                # Merge required_genres and optional_genres into single genres list
                # This ensures compatibility with the rest of the pipeline
                required = intent.get("required_genres", []) or []
                optional = intent.get("optional_genres", []) or []
                # Combine with required first (higher priority in scoring)
                all_genres = required + [g for g in optional if g not in required]
                intent["genres"] = all_genres[:8]  # Limit to 8 genres max
                # Remove the separate fields to avoid confusion
                intent.pop("required_genres", None)
                intent.pop("optional_genres", None)
                
                rds.setex(cache_key, 21600, json.dumps(intent))
                logger.info(f"[IntentExtractor] LLM extraction successful for query: {query[:50]}")
                return intent
            else:
                logger.warning(f"[IntentExtractor] LLM returned invalid JSON for query: {query[:50]}. Raw output: {output[:300]}")
        except requests.Timeout:
            logger.warning(f"[IntentExtractor] LLM timeout (60s+retries) - likely model still loading. Query len: {len(query)}")
        except requests.RequestException as e:
            logger.warning(f"[IntentExtractor] LLM network error: {e}")
        except Exception as e:
            logger.warning(f"[IntentExtractor] LLM call failed: {e}")
        # Return None so scorer fallback handles it
        return None
