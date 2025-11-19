import json
import os
import threading
from typing import Dict, Iterable, List, Optional, Tuple

# Lazy imports to avoid overhead when feature is disabled
try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None  # fallback when faiss not installed in current env

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # pragma: no cover
    SentenceTransformer = None  # type: ignore


class BGELock:
    """Cross-process lock via a lock file, plus in-process guard.
    Mirrors the pattern used in faiss_index.py without changing existing modules.
    """

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._local = threading.Lock()

    def acquire_shared(self):
        # For portability keep simple: use single lock file; shared behaves as exclusive here.
        # This is acceptable since BGE index is auxiliary and low-frequency writes.
        self._local.acquire()
        self._fh = open(self.lock_path, "a+")
        try:
            import fcntl  # type: ignore
            fcntl.flock(self._fh, fcntl.LOCK_EX)  # shared-as-exclusive
        except Exception:
            pass

    def acquire_exclusive(self):
        self.acquire_shared()

    def release(self):
        try:
            try:
                import fcntl  # type: ignore
                fcntl.flock(self._fh, 0)
            except Exception:
                pass
            self._fh.close()
        finally:
            self._local.release()


class BGEIndex:
    """Secondary FAISS index for BGE embeddings stored separately from the main index.

    Files under data/bge_index/:
      - faiss_bge.index: FAISS HNSW index
      - id_map.json: mapping {"model": str, "dim": int, "items": {str(item_id): {"pos": int, "hash": str}}}
      - faiss_bge.lock: lock file for cross-process coordination
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.index_path = os.path.join(base_dir, "faiss_bge.index")
        self.map_path = os.path.join(base_dir, "id_map.json")
        self.lock_path = os.path.join(base_dir, "faiss_bge.lock")
        os.makedirs(base_dir, exist_ok=True)
        self._index = None
        self._id_map: Dict[str, Dict] = {}
        self._lock = BGELock(self.lock_path)

    @property
    def is_available(self) -> bool:
        return os.path.exists(self.index_path) and os.path.exists(self.map_path) and faiss is not None

    def load(self) -> bool:
        if faiss is None:
            return False
        if not self.is_available:
            # id_map may exist without index; treat as unavailable
            return False
        self._lock.acquire_shared()
        try:
            self._index = faiss.read_index(self.index_path)
            with open(self.map_path, "r", encoding="utf-8") as f:
                self._id_map = json.load(f)
            # Back-compat normalization: if single-pos entries exist, wrap them into entries[] and build rev map
            items = self._id_map.get("items", {}) if isinstance(self._id_map, dict) else {}
            rev = self._id_map.get("rev")
            changed = False
            if isinstance(items, dict) and rev is None:
                self._id_map["rev"] = {}
                for sid, entry in list(items.items()):
                    if isinstance(entry, dict) and "pos" in entry:
                        pos = entry.get("pos")
                        h = entry.get("hash")
                        items[sid] = {"entries": [{"pos": pos, "hash": h, "label": "base"}]}
                        self._id_map["rev"][str(pos)] = {"item_id": int(sid), "label": "base"}
                        changed = True
                    elif isinstance(entry, dict) and "entries" in entry and isinstance(entry["entries"], list):
                        for e in entry["entries"]:
                            if isinstance(e, dict) and "pos" in e:
                                self._id_map["rev"][str(e["pos"])] = {"item_id": int(sid), "label": e.get("label", "base")}
                if changed:
                    # write normalized map atomically
                    tmp_map = self.map_path + ".tmp"
                    with open(tmp_map, "w", encoding="utf-8") as f2:
                        json.dump(self._id_map, f2)
                    os.replace(tmp_map, self.map_path)
            return True
        finally:
            self._lock.release()

    def search(self, vectors: List[List[float]], top_k: int) -> Tuple[List[List[int]], List[List[float]]]:
        if self._index is None:
            if not self.load():
                return [], []
        assert self._index is not None
        import numpy as np  # local import
        xq = np.array(vectors, dtype="float32")
        distances, indices = self._index.search(xq, top_k)
        return indices.tolist(), distances.tolist()

    def add_items(self, item_ids: List[int], vectors: List[List[float]], content_hashes: Optional[List[str]] = None,
                  hnsw_m: int = 32, ef_construction: int = 300, labels: Optional[List[str]] = None) -> None:
        if faiss is None:
            raise RuntimeError("FAISS not available")
        import numpy as np
        self._lock.acquire_exclusive()
        try:
            dim = len(vectors[0]) if vectors else None
            if self._index is None or not os.path.exists(self.index_path):
                if dim is None:
                    raise ValueError("Cannot initialize index without vectors")
                quantizer = None  # HNSWFlat doesn't need separate quantizer
                self._index = faiss.IndexHNSWFlat(dim, hnsw_m)
                self._index.hnsw.efConstruction = ef_construction
                # initialize id_map
                self._id_map = {"model": "BAAI/bge-small-en-v1.5", "dim": dim, "items": {}, "rev": {}}
            # append vectors
            xb = np.array(vectors, dtype="float32")
            start = self._index.ntotal
            self._index.add(xb)
            # update map
            for offset, item_id in enumerate(item_ids):
                pos = start + offset
                label = labels[offset] if labels and offset < len(labels) else "base"
                items = self._id_map.setdefault("items", {})
                existing = items.get(str(item_id))
                e = {"pos": pos}
                if content_hashes and offset < len(content_hashes):
                    e["hash"] = content_hashes[offset]
                e["label"] = label
                if not existing:
                    items[str(item_id)] = {"entries": [e]}
                else:
                    if "entries" in existing and isinstance(existing["entries"], list):
                        existing["entries"].append(e)
                    else:
                        # back-compat single entry -> convert
                        prev_pos = existing.get("pos")
                        prev_hash = existing.get("hash")
                        items[str(item_id)] = {"entries": [{"pos": prev_pos, "hash": prev_hash, "label": "base"}, e]}
                # reverse mapping
                rev = self._id_map.setdefault("rev", {})
                rev[str(pos)] = {"item_id": int(item_id), "label": label}
            # write atomically
            tmp_idx = self.index_path + ".tmp"
            tmp_map = self.map_path + ".tmp"
            faiss.write_index(self._index, tmp_idx)
            with open(tmp_map, "w", encoding="utf-8") as f:
                json.dump(self._id_map, f)
            os.replace(tmp_idx, self.index_path)
            os.replace(tmp_map, self.map_path)
        finally:
            self._lock.release()

    def get_missing_or_stale(self, candidates: Dict[int, str]) -> List[int]:
        """Return item_ids that are missing or whose stored content hash differs (base entry).

        For multi-vector per item, this checks only the 'base' label; callers can extend
        by passing different candidates dicts and labels to add_items.
        """
        missing = []
        items = (self._id_map or {}).get("items", {})
        for iid, h in candidates.items():
            entry = items.get(str(iid))
            if not entry:
                missing.append(iid)
                continue
            # prefer entries list
            if isinstance(entry, dict) and "entries" in entry:
                # find base label if present
                base_e = None
                for e in entry.get("entries", []):
                    if e.get("label", "base") == "base":
                        base_e = e
                        break
                if not base_e or base_e.get("hash") != h:
                    missing.append(iid)
            else:
                # back-compat single entry
                if entry.get("hash") != h:
                    missing.append(iid)
        return missing

    def positions_to_item_ids(self, positions: List[int]) -> List[Tuple[int, str]]:
        """Map FAISS positions back to (item_id, label)."""
        rev = (self._id_map or {}).get("rev", {})
        out: List[Tuple[int, str]] = []
        for p in positions:
            info = rev.get(str(p))
            if info and isinstance(info, dict):
                out.append((int(info.get("item_id")), str(info.get("label", "base"))))
            else:
                out.append((-1, "unknown"))
        return out


class BGEEmbedder:
    """Lazy-loading wrapper around sentence-transformers for BGE."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._model = None

    def ensure_model(self):
        if self._model is None:
            if SentenceTransformer is None:
                raise RuntimeError("sentence-transformers not available")
            self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: List[str], batch_size: int = 64) -> List[List[float]]:
        self.ensure_model()
        assert self._model is not None
        return self._model.encode(texts, batch_size=batch_size, normalize_embeddings=True).tolist()


def build_query_text(base_query: str, mood: Optional[str] = None, season: Optional[str] = None,
                     facets: Optional[Dict[str, Iterable[str]]] = None) -> str:
    parts = [base_query]
    if mood:
        parts.append(f"mood: {mood}")
    if season:
        parts.append(f"season: {season}")
    if facets:
        for k, vals in facets.items():
            vals_str = ", ".join([str(v) for v in vals])
            parts.append(f"{k}: {vals_str}")
    return " | ".join(parts)
