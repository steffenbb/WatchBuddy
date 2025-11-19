#!/usr/bin/env python3
"""
Warm up Ollama by loading the model with a simple test request.
This prevents the first user request from timing out during model loading.
"""
import requests
import time
import sys
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

OLLAMA_URL = "http://ollama:11434"
MODEL_NAME = "phi3.5:3.8b-mini-instruct-q4_K_M"
MAX_RETRIES = 10
RETRY_DELAY = 5


def check_ollama_health():
    """Check if Ollama service is ready."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def warm_model():
    """Send a simple request to load the model into memory."""
    try:
        logger.info(f"Sending warmup request to load model: {MODEL_NAME}")
        start_time = time.time()
        
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": MODEL_NAME,
                "prompt": "Hello",
                "options": {"num_predict": 5},
                "stream": False,
            },
            timeout=120,  # Model loading can take up to 30s
        )
        
        elapsed = time.time() - start_time
        
        if resp.status_code == 200:
            logger.info(f"✓ Model warmed up successfully in {elapsed:.1f}s")
            return True
        else:
            logger.warning(f"Warmup request failed with status {resp.status_code}")
            return False
            
    except requests.Timeout:
        elapsed = time.time() - start_time
        logger.error(f"✗ Warmup timeout after {elapsed:.1f}s")
        return False
    except Exception as e:
        logger.error(f"✗ Warmup failed: {e}")
        return False


def main():
    logger.info("Starting Ollama warmup...")
    
    # Wait for Ollama to be ready
    logger.info(f"Waiting for Ollama at {OLLAMA_URL}...")
    for attempt in range(MAX_RETRIES):
        if check_ollama_health():
            logger.info("✓ Ollama is ready")
            break
        logger.info(f"Ollama not ready (attempt {attempt + 1}/{MAX_RETRIES}), waiting {RETRY_DELAY}s...")
        time.sleep(RETRY_DELAY)
    else:
        logger.error("✗ Ollama not ready after max retries")
        sys.exit(1)
    
    # Warm up the model
    if warm_model():
        logger.info("✓ Ollama warmup complete - model ready for requests")
        sys.exit(0)
    else:
        logger.warning("⚠ Ollama warmup incomplete - first request may be slow")
        sys.exit(0)  # Don't fail container startup


if __name__ == "__main__":
    main()
