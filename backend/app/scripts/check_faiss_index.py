#!/usr/bin/env python
"""Check FAISS index size and mapping."""
import os
from app.services.ai_engine.faiss_index import load_index

index_path = "/data/ai/faiss_index.bin"
mapping_path = "/data/ai/faiss_map.json"

print(f"FAISS index exists: {os.path.exists(index_path)}")
print(f"FAISS mapping exists: {os.path.exists(mapping_path)}")

if os.path.exists(index_path) and os.path.exists(mapping_path):
    try:
        index, mapping = load_index()
        print(f"FAISS index size: {index.ntotal} vectors")
        print(f"Mapping size: {len(mapping)} entries")
    except Exception as e:
        print(f"Error loading index: {e}")
else:
    print("FAISS index files not found")
