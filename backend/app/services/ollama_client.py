"""
Simple Ollama client for LLM operations.

Provides async interface to Ollama's generate and chat APIs.
"""
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)

class OllamaClient:
    """Async client for Ollama API."""
    
    def __init__(self, base_url: str = "http://ollama:11434"):
        self.base_url = base_url.rstrip("/")
        
    async def generate(
        self, 
        model: str,
        prompt: str,
        options: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Call Ollama generate API.
        
        Args:
            model: Model name (e.g., "phi3:mini")
            prompt: Text prompt
            options: Model options (temperature, num_predict, etc.)
            timeout: Request timeout in seconds
            
        Returns:
            Response dict with "response" field containing generated text
        """
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "options": options or {},
                        "stream": False,
                        "keep_alive": "24h"
                    }
                )
                response.raise_for_status()
                return response.json()
                
            except httpx.TimeoutException as e:
                logger.error(f"Ollama request timeout for model {model}: {e}")
                raise
            except httpx.HTTPError as e:
                logger.error(f"Ollama HTTP error for model {model}: {e}")
                raise
            except Exception as e:
                logger.error(f"Ollama request failed for model {model}: {e}")
                raise
                
    async def chat(
        self,
        model: str,
        messages: list[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Call Ollama chat API.
        
        Args:
            model: Model name (e.g., "phi3:mini")
            messages: List of message dicts with "role" and "content"
            options: Model options (temperature, num_predict, etc.)
            timeout: Request timeout in seconds
            
        Returns:
            Response dict with "message" field containing assistant response
        """
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "options": options or {},
                        "stream": False,
                        "keep_alive": "24h"
                    }
                )
                response.raise_for_status()
                return response.json()
                
            except httpx.TimeoutException as e:
                logger.error(f"Ollama chat timeout for model {model}: {e}")
                raise
            except httpx.HTTPError as e:
                logger.error(f"Ollama chat HTTP error for model {model}: {e}")
                raise
            except Exception as e:
                logger.error(f"Ollama chat request failed for model {model}: {e}")
                raise


# Global client instance
_ollama_client: Optional[OllamaClient] = None


def get_ollama_client(base_url: str = "http://ollama:11434") -> OllamaClient:
    """Get or create Ollama client instance.
    
    Args:
        base_url: Ollama API base URL
        
    Returns:
        OllamaClient instance
    """
    global _ollama_client
    
    if _ollama_client is None:
        _ollama_client = OllamaClient(base_url=base_url)
        
    return _ollama_client
