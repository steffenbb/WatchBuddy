"""
ai_export_import.py

Export/import AI artifacts so users don't need to rebuild embeddings on first run.

Artifacts:
- /data/ai/faiss_index.bin (FAISS IVF+PQ index)
- /data/ai/faiss_map.json (rowId -> tmdb_id mapping)
- Optional: ai_embeddings.jsonl.gz (tmdb_id + base64 float16 embedding from DB)

Usage (inside backend container, always set PYTHONPATH=/app):
  python -m app.scripts.ai_export_import export --out /app/data/ai_bundle.tar.gz [--with-embeddings]
  python -m app.scripts.ai_export_import import --src /app/data/ai_bundle.tar.gz [--apply-embeddings]

Notes:
- Import writes FAISS artifacts into /data/ai, which is what ai_engine.faiss_index.load_index() reads.
- Embedding import updates persistent_candidates.embedding only when --apply-embeddings is passed.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import logging
import os
import sys
import tarfile
from pathlib import Path
from typing import Iterable, Tuple

from sqlalchemy import text

from app.core.database import SessionLocal


logger = logging.getLogger(__name__)


DATA_AI_DIR = Path("/data/ai")
INDEX_FILE = DATA_AI_DIR / "faiss_index.bin"
MAP_FILE = DATA_AI_DIR / "faiss_map.json"


def _ensure_dirs() -> None:
    DATA_AI_DIR.mkdir(parents=True, exist_ok=True)


def _export_faiss() -> Tuple[Path, Path]:
    if not INDEX_FILE.exists() or not MAP_FILE.exists():
        raise FileNotFoundError("FAISS artifacts not found at /data/ai; build index first.")
    return INDEX_FILE, MAP_FILE


def _iter_db_embeddings(batch_size: int = 5000) -> Iterable[Tuple[int, bytes]]:
    """Yield (tmdb_id, embedding_bytes) for rows that have embeddings."""
    db = SessionLocal()
    try:
        # Stream in batches to avoid memory spikes
        offset = 0
        while True:
            rows = db.execute(
                text(
                    """
                    SELECT tmdb_id, embedding
                    FROM persistent_candidates
                    WHERE embedding IS NOT NULL
                    ORDER BY tmdb_id
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"lim": batch_size, "off": offset},
            ).fetchall()
            if not rows:
                break
            for tmdb_id, blob in rows:
                if blob:
                    yield int(tmdb_id), bytes(blob)
            offset += len(rows)
    finally:
        db.close()


def _write_embeddings_jsonl_gz(out_path: Path) -> int:
    """Write embeddings to gzipped JSONL: {"tmdb_id": int, "emb": base64}.
    Returns count of rows written.
    """
    count = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for tmdb_id, blob in _iter_db_embeddings():
            # store compact base64; numpy dtype/shape not included (fixed model size)
            f.write(json.dumps({"tmdb_id": tmdb_id, "emb": base64.b64encode(blob).decode("ascii")}) + "\n")
            count += 1
    return count


def cmd_export(args: argparse.Namespace) -> None:
    _ensure_dirs()
    idx, mapping = _export_faiss()

    out_tar = Path(args.out).resolve()
    out_tar.parent.mkdir(parents=True, exist_ok=True)

    tmp_emb_path: Path | None = None
    try:
        if args.with_embeddings:
            tmp_emb_path = Path("/tmp/ai_embeddings.jsonl.gz")
            logger.info("Exporting DB embeddings to %s ...", tmp_emb_path)
            wrote = _write_embeddings_jsonl_gz(tmp_emb_path)
            logger.info("Exported %d embedding rows", wrote)

        with tarfile.open(out_tar, "w:gz") as tar:
            tar.add(idx, arcname="faiss_index.bin")
            tar.add(mapping, arcname="faiss_map.json")
            if tmp_emb_path and tmp_emb_path.exists():
                tar.add(tmp_emb_path, arcname="ai_embeddings.jsonl.gz")
    finally:
        try:
            if tmp_emb_path and tmp_emb_path.exists():
                tmp_emb_path.unlink()
        except Exception:
            pass

    print(f"✅ Exported AI bundle to {out_tar}")


def _apply_embeddings_from_jsonl_gz(jsonl_gz_bytes: bytes) -> int:
    """Apply embeddings into DB. Returns count updated."""
    db = SessionLocal()
    updated = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(jsonl_gz_bytes), mode="rb") as gz:
            for line in gz:
                try:
                    rec = json.loads(line)
                    tmdb_id = int(rec["tmdb_id"])
                    blob = base64.b64decode(rec["emb"])
                except Exception:
                    continue
                try:
                    db.execute(
                        text(
                            """
                            UPDATE persistent_candidates
                            SET embedding = :blob
                            WHERE tmdb_id = :tmdb
                            """
                        ),
                        {"blob": blob, "tmdb": tmdb_id},
                    )
                    updated += 1
                    if updated % 2000 == 0:
                        db.commit()
                except Exception:
                    db.rollback()
                    continue
        db.commit()
        return updated
    finally:
        db.close()


def cmd_import(args: argparse.Namespace) -> None:
    src = Path(args.src).resolve()
    if not src.exists():
        raise FileNotFoundError(src)

    _ensure_dirs()
    # Extract into temp dir and then move artifacts
    with tarfile.open(src, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        if "faiss_index.bin" not in members or "faiss_map.json" not in members:
            raise RuntimeError("Bundle missing faiss_index.bin or faiss_map.json")

        tar.extract(members["faiss_index.bin"], path="/tmp")
        tar.extract(members["faiss_map.json"], path="/tmp")

        # Move to /data/ai
        Path("/tmp/faiss_index.bin").replace(INDEX_FILE)
        Path("/tmp/faiss_map.json").replace(MAP_FILE)
        print(f"✅ Imported FAISS artifacts to {DATA_AI_DIR}")

        if args.apply_embeddings and "ai_embeddings.jsonl.gz" in members:
            fobj = tar.extractfile(members["ai_embeddings.jsonl.gz"])  # type: ignore[arg-type]
            if fobj:
                payload = fobj.read()
                count = _apply_embeddings_from_jsonl_gz(payload)
                print(f"✅ Applied {count} DB embeddings from bundle")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Export/import AI artifacts (FAISS + optional DB embeddings)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="Export FAISS and optional DB embeddings")
    pe.add_argument("--out", required=True, help="Path for output tar.gz bundle")
    pe.add_argument("--with-embeddings", action="store_true", help="Include DB embeddings JSONL.gz")
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="Import FAISS and optionally apply DB embeddings")
    pi.add_argument("--src", required=True, help="Path to input tar.gz bundle")
    pi.add_argument("--apply-embeddings", action="store_true", help="Apply DB embeddings to persistent_candidates")
    pi.set_defaults(func=cmd_import)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
