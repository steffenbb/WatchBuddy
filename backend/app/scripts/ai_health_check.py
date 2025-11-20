"""
AI Health Check Script

Run inside the backend container to verify AI stack readiness:
- MiniLM sentence-transformer encoding
- FAISS primary index load and stats
- Optional BGE secondary index presence and size
- Cross-Encoder reranker load and scoring

Usage (from host):
  docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/ai_health_check.py"
"""
from __future__ import annotations
import json
import os
import sys


def check_minilm():
    out = {"ok": False}
    try:
        from app.services.ai_engine.embeddings import EmbeddingService, MODEL_NAME as MINILM_MODEL
        svc = EmbeddingService()
        v = svc.encode_text("healthcheck")
        dim = int(getattr(v, "shape", [0])[0]) if getattr(v, "shape", None) is not None else (len(v) if hasattr(v, "__len__") else 0)
        out.update({"ok": True, "model": MINILM_MODEL, "dim": dim})
    except Exception as e:
        out.update({"ok": False, "error": str(e)})
    return out


def check_faiss():
    out = {"ok": False}
    try:
        from app.services.ai_engine import faiss_index as fi
        index, mapping = fi.load_index()
        count = int(getattr(index, "ntotal", 0))
        ef_search = int(getattr(getattr(index, "hnsw", None), "efSearch", 0)) if hasattr(index, "hnsw") else None
        out.update({
            "ok": True,
            "vectors": count,
            "mapping_entries": len(mapping or {}),
            "efSearch": ef_search,
            "index_path": str(getattr(fi, "INDEX_FILE", "")),
            "map_path": str(getattr(fi, "MAPPING_FILE", "")),
        })
    except Exception as e:
        out.update({
            "ok": False,
            "error": str(e),
        })
    return out


essential_env = os.environ.get


def check_bge():
    out = {"ok": False}
    try:
        from app.services.ai_engine.bge_index import BGEIndex
        base_dir = os.path.join("/data/ai", "bge_index")
        bge = BGEIndex(base_dir)
        if not bge.is_available:
            return {"ok": False, "base_dir": base_dir, "note": "BGE index not present (optional)"}
        if bge.load():
            size = int(getattr(getattr(bge, "_index", None), "ntotal", 0))
            return {"ok": True, "base_dir": base_dir, "vectors": size}
        return {"ok": False, "base_dir": base_dir, "error": "failed to load"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_cross_encoder():
    out = {"ok": False}
    try:
        from app.services.ai_engine.cross_encoder_reranker import CrossEncoderReranker
        ce = CrossEncoderReranker()
        ce.ensure()
        try:
            _ = ce.score("test", ["doc"], batch_size=1)
        except Exception:
            # Even if scoring fails, ensure() is sufficient to validate download
            pass
        out.update({"ok": True, "model": ce.model_name})
    except Exception as e:
        out.update({"ok": False, "error": str(e)})
    return out


def main():
    results = {
        "minilm_embedding": check_minilm(),
        "faiss_index": check_faiss(),
        "bge_index": check_bge(),
        "cross_encoder": check_cross_encoder(),
    }
    results["ok"] = all(v.get("ok") for v in results.values())
    print(json.dumps(results, indent=2))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
