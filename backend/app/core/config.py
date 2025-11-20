
import os
from pydantic import BaseSettings



class Settings(BaseSettings):
    db_user: str = os.getenv("POSTGRES_USER", "watchbuddy")
    db_password: str = os.getenv("POSTGRES_PASSWORD", "watchbuddy")
    db_name: str = os.getenv("POSTGRES_DB", "watchbuddy")
    database_url: str = f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'watchbuddy')}:{os.getenv('POSTGRES_PASSWORD', 'watchbuddy')}@db:5432/{os.getenv('POSTGRES_DB', 'watchbuddy')}"
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    trakt_redirect_uri: str = "http://localhost:5173/auth/callback"

    # Secondary BGE index (additive; disabled by default)
    ai_bge_index_enabled: bool = os.getenv("AI_BGE_INDEX_ENABLED", "false").lower() == "true"
    ai_bge_topn_nightly: int = int(os.getenv("AI_BGE_TOPN_NIGHTLY", "100000"))
    ai_bge_topk_query: int = int(os.getenv("AI_BGE_TOPK_QUERY", "600"))
    ai_bge_weight_in_rrf: float = float(os.getenv("AI_BGE_WEIGHT_IN_RRF", "1.1"))
    ai_bge_model_name: str = os.getenv("AI_BGE_MODEL_NAME", "BAAI/bge-small-en-v1.5")
    ai_bge_query_context_enabled: bool = os.getenv("AI_BGE_QUERY_CONTEXT_ENABLED", "true").lower() == "true"
    ai_bge_index_dir: str = os.getenv("AI_BGE_INDEX_DIR", "/data/ai/bge_index")

    # Multi-query & retrieval tuning
    ai_multiquery_enabled: bool = os.getenv("AI_MULTIQUERY_ENABLED", "true").lower() == "true"
    ai_multiquery_variants: int = int(os.getenv("AI_MULTIQUERY_VARIANTS", "4"))

    # Cross-encoder reranker (optional)
    ai_reranker_enabled: bool = os.getenv("AI_RERANKER_ENABLED", "false").lower() == "true"
    ai_reranker_model: str = os.getenv("AI_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    ai_reranker_topk: int = int(os.getenv("AI_RERANKER_TOPK", "300"))
    ai_reranker_weight: float = float(os.getenv("AI_RERANKER_WEIGHT", "0.3"))

    # LLM judge reranker (optional, LOCAL by default)
    # Provider options: "openai_compatible" (can be local), "ollama"
    ai_llm_judge_enabled: bool = os.getenv("AI_LLM_JUDGE_ENABLED", "true").lower() == "true"
    ai_llm_judge_topk: int = int(os.getenv("AI_LLM_JUDGE_TOPK", "100"))
    ai_llm_judge_weight: float = float(os.getenv("AI_LLM_JUDGE_WEIGHT", "0.15"))
    # Small, CPU-friendly local defaults
    ai_llm_judge_model: str = os.getenv("AI_LLM_JUDGE_MODEL", "phi3.5:3.8b-mini-instruct-q4_K_M")
    ai_llm_judge_provider: str = os.getenv("AI_LLM_JUDGE_PROVIDER", "ollama")
    # Default to local Ollama; for OpenAI-compatible local servers, set provider=openai_compatible and /v1 base
    ai_llm_api_base: str = os.getenv("AI_LLM_API_BASE", os.getenv("OPENAI_API_BASE", "http://ollama:11434"))
    # API key optional; for local providers typically not needed
    ai_llm_api_key_env: str = os.getenv("AI_LLM_API_KEY_ENV", "")
    ai_llm_timeout_seconds: int = int(os.getenv("AI_LLM_TIMEOUT_SECONDS", "10"))

    # LLM explanations (optional)
    ai_llm_explain_enabled: bool = os.getenv("AI_LLM_EXPLAIN_ENABLED", "false").lower() == "true"
    ai_llm_explain_topk: int = int(os.getenv("AI_LLM_EXPLAIN_TOPK", "50"))

    # Ranker strategy (AI lists only): classic | llm_only | hybrid
    ai_ranker_strategy: str = os.getenv("AI_RANKER_STRATEGY", "classic")
    # Final order cache (seconds)
    ai_rank_order_cache_ttl: int = int(os.getenv("AI_RANK_ORDER_CACHE_TTL", "21600"))  # 6h

    # Optional pairwise LLM ranking (AI lists only)
    ai_llm_pairwise_enabled: bool = os.getenv("AI_LLM_PAIRWISE_ENABLED", "false").lower() == "true"
    ai_llm_pairwise_max_pairs: int = int(os.getenv("AI_LLM_PAIRWISE_MAX_PAIRS", "60"))

settings = Settings()
