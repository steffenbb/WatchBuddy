#!/usr/bin/env python
"""Check FAISS index sizes and statistics for both MiniLM and BGE indexes."""
import os
import sys
import json
from pathlib import Path

# Add app to path
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.models import BGEEmbedding, PersistentCandidate
from sqlalchemy import func, text


def check_minilm_index():
    """Check MiniLM FAISS index (standard embedding index)."""
    print("\n" + "="*60)
    print("📊 MiniLM FAISS Index (Standard Embeddings)")
    print("="*60)
    
    index_path = "/data/ai/faiss_index.bin"
    mapping_path = "/data/ai/faiss_map.json"
    
    print(f"Index file:   {index_path}")
    print(f"  Exists: {os.path.exists(index_path)}")
    if os.path.exists(index_path):
        size_mb = os.path.getsize(index_path) / (1024 * 1024)
        print(f"  Size: {size_mb:.2f} MB")
    
    print(f"\nMapping file: {mapping_path}")
    print(f"  Exists: {os.path.exists(mapping_path)}")
    if os.path.exists(mapping_path):
        size_kb = os.path.getsize(mapping_path) / 1024
        print(f"  Size: {size_kb:.2f} KB")
    
    if os.path.exists(index_path) and os.path.exists(mapping_path):
        try:
            from app.services.ai_engine.faiss_index import load_index
            index, mapping = load_index()
            print(f"\n✅ Index loaded successfully")
            print(f"  Vectors: {index.ntotal:,}")
            print(f"  Mapping entries: {len(mapping):,}")
            
            # Check for dimension
            if hasattr(index, 'd'):
                print(f"  Vector dimension: {index.d}")
            
            return {"status": "ok", "vectors": index.ntotal, "mapping": len(mapping)}
        except Exception as e:
            print(f"\n❌ Error loading index: {e}")
            return {"status": "error", "error": str(e)}
    else:
        print("\n⚠️  Index files not found")
        return {"status": "missing"}


def check_bge_index():
    """Check BGE FAISS index (multi-vector semantic index)."""
    print("\n" + "="*60)
    print("🔍 BGE FAISS Index (Multi-Vector Semantic)")
    print("="*60)
    
    base_dir = "/data/ai/bge_index"
    index_path = os.path.join(base_dir, "faiss_bge.index")
    mapping_path = os.path.join(base_dir, "id_map.json")
    
    print(f"Base directory: {base_dir}")
    print(f"  Exists: {os.path.exists(base_dir)}")
    
    print(f"\nIndex file: {index_path}")
    print(f"  Exists: {os.path.exists(index_path)}")
    if os.path.exists(index_path):
        size_mb = os.path.getsize(index_path) / (1024 * 1024)
        print(f"  Size: {size_mb:.2f} MB")
    
    print(f"\nMapping file: {mapping_path}")
    print(f"  Exists: {os.path.exists(mapping_path)}")
    if os.path.exists(mapping_path):
        size_kb = os.path.getsize(mapping_path) / 1024
        print(f"  Size: {size_kb:.2f} KB")
        
        # Parse mapping to count vectors
        try:
            with open(mapping_path, 'r') as f:
                mapping = json.load(f)
            
            total_vectors = 0
            items_count = len(mapping.get('items', {}))
            
            # Count vectors per label
            label_counts = {}
            for item_id, item_data in mapping.get('items', {}).items():
                entries = item_data.get('entries', [])
                for entry in entries:
                    label = entry.get('label', 'unknown')
                    label_counts[label] = label_counts.get(label, 0) + 1
                    total_vectors += 1
            
            print(f"\n✅ Mapping loaded successfully")
            print(f"  Total items: {items_count:,}")
            print(f"  Total vectors: {total_vectors:,}")
            print(f"\n  Vectors by label:")
            for label, count in sorted(label_counts.items()):
                print(f"    {label}: {count:,}")
            
            return {
                "status": "ok", 
                "items": items_count, 
                "vectors": total_vectors,
                "labels": label_counts
            }
        except Exception as e:
            print(f"\n❌ Error reading mapping: {e}")
            return {"status": "error", "error": str(e)}
    else:
        print("\n⚠️  Index files not found")
        return {"status": "missing"}


def check_bge_embeddings_db():
    """Check BGE embeddings in database."""
    print("\n" + "="*60)
    print("💾 BGE Embeddings Database")
    print("="*60)
    
    db = SessionLocal()
    try:
        # Count total embeddings
        total = db.query(BGEEmbedding).count()
        print(f"Total BGEEmbedding rows: {total:,}")
        
        if total == 0:
            print("⚠️  No embeddings found in database")
            return {"status": "empty", "count": 0}
        
        # Count by media type
        movie_count = db.query(BGEEmbedding).filter_by(media_type='movie').count()
        show_count = db.query(BGEEmbedding).filter_by(media_type='show').count()
        print(f"  Movies: {movie_count:,}")
        print(f"  Shows: {show_count:,}")
        
        # Count embeddings by type
        base_count = db.query(BGEEmbedding).filter(BGEEmbedding.embedding_base.isnot(None)).count()
        title_count = db.query(BGEEmbedding).filter(BGEEmbedding.embedding_title.isnot(None)).count()
        keywords_count = db.query(BGEEmbedding).filter(BGEEmbedding.embedding_keywords.isnot(None)).count()
        people_count = db.query(BGEEmbedding).filter(BGEEmbedding.embedding_people.isnot(None)).count()
        brands_count = db.query(BGEEmbedding).filter(BGEEmbedding.embedding_brands.isnot(None)).count()
        
        print(f"\nEmbedding coverage:")
        print(f"  Base (required): {base_count:,} ({base_count/total*100:.1f}%)")
        print(f"  Title: {title_count:,} ({title_count/total*100:.1f}%)")
        print(f"  Keywords: {keywords_count:,} ({keywords_count/total*100:.1f}%)")
        print(f"  People: {people_count:,} ({people_count/total*100:.1f}%)")
        print(f"  Brands: {brands_count:,} ({brands_count/total*100:.1f}%)")
        
        total_vectors = base_count + title_count + keywords_count + people_count + brands_count
        print(f"\nTotal stored vectors: {total_vectors:,}")
        print(f"Average vectors per item: {total_vectors/total:.2f}")
        
        # Check model version
        models = db.query(BGEEmbedding.model_name, func.count(BGEEmbedding.id)).group_by(BGEEmbedding.model_name).all()
        print(f"\nModel versions:")
        for model, count in models:
            print(f"  {model}: {count:,}")
        
        return {
            "status": "ok",
            "total": total,
            "movies": movie_count,
            "shows": show_count,
            "vectors": {
                "base": base_count,
                "title": title_count,
                "keywords": keywords_count,
                "people": people_count,
                "brands": brands_count,
                "total": total_vectors
            }
        }
    except Exception as e:
        print(f"❌ Database error: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def check_persistent_candidates():
    """Check persistent candidates with MiniLM embeddings."""
    print("\n" + "="*60)
    print("📦 Persistent Candidates (MiniLM Embeddings)")
    print("="*60)
    
    db = SessionLocal()
    try:
        total = db.query(PersistentCandidate).count()
        print(f"Total candidates: {total:,}")
        
        # Count with embeddings
        with_emb = db.query(PersistentCandidate).filter(
            PersistentCandidate.embedding.isnot(None)
        ).count()
        
        print(f"  With MiniLM embeddings: {with_emb:,} ({with_emb/total*100:.1f}%)")
        print(f"  Without embeddings: {total - with_emb:,}")
        
        # Count by media type
        movies = db.query(PersistentCandidate).filter_by(media_type='movie').count()
        shows = db.query(PersistentCandidate).filter_by(media_type='show').count()
        print(f"\nBy media type:")
        print(f"  Movies: {movies:,}")
        print(f"  Shows: {shows:,}")
        
        return {
            "status": "ok",
            "total": total,
            "with_embeddings": with_emb,
            "movies": movies,
            "shows": shows
        }
    except Exception as e:
        print(f"❌ Database error: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def check_redis_settings():
    """Check Redis BGE settings."""
    print("\n" + "="*60)
    print("⚙️  Redis Settings")
    print("="*60)
    
    try:
        from app.core.redis_client import get_redis_sync
        r = get_redis_sync()
        
        bge_enabled = r.get("settings:global:ai_bge_index_enabled")
        bge_size = r.get("settings:global:ai_bge_index_size")
        bge_last_build = r.get("settings:global:ai_bge_last_build")
        
        print(f"BGE Index Enabled: {bge_enabled}")
        print(f"BGE Index Size: {bge_size}")
        
        if bge_last_build:
            import time
            from datetime import datetime
            last_build_dt = datetime.fromtimestamp(int(bge_last_build))
            print(f"Last Build: {last_build_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        
        return {
            "status": "ok",
            "bge_enabled": bge_enabled,
            "bge_size": bge_size
        }
    except Exception as e:
        print(f"❌ Redis error: {e}")
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    print("\n🔍 WATCHBUDDY FAISS INDEX DIAGNOSTIC TOOL")
    print("="*60)
    
    results = {}
    
    # Check all systems
    results['minilm'] = check_minilm_index()
    results['bge'] = check_bge_index()
    results['bge_db'] = check_bge_embeddings_db()
    results['candidates'] = check_persistent_candidates()
    results['redis'] = check_redis_settings()
    
    # Summary
    print("\n" + "="*60)
    print("📋 SUMMARY")
    print("="*60)
    
    all_ok = all(r.get('status') == 'ok' for r in results.values())
    
    if all_ok:
        print("✅ All systems operational")
    else:
        print("⚠️  Issues detected:")
        for name, result in results.items():
            if result.get('status') != 'ok':
                print(f"  - {name}: {result.get('status', 'unknown')}")
    
    print("\n" + "="*60)
