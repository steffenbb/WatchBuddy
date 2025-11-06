"""Verify bootstrap bundle contents"""
import tarfile, json
from pathlib import Path

bundle = Path("/app/data/watchbuddy_bootstrap.tar.gz")
t = tarfile.open(bundle, 'r:gz')
members = t.getmembers()

print(f"Bundle: {bundle}")
print(f"Size: {bundle.stat().st_size / (1024*1024*1024):.2f} GB")
print(f"\nContents ({len(members)} files):")

for m in members:
    size_mb = m.size / (1024*1024)
    print(f"  {m.name}: {size_mb:.2f} MB")

# Read metadata
try:
    meta_member = next(m for m in members if m.name.endswith("metadata.json"))
    meta = json.load(t.extractfile(meta_member))
    print(f"\nMetadata:")
    print(f"  Export time: {meta.get('export_timestamp')}")
    print(f"  Total candidates: {meta.get('counts', {}).get('total_candidates', 0):,}")
    print(f"  With embeddings: {meta.get('counts', {}).get('with_embeddings', 0):,}")
    print(f"  Movies: {meta.get('counts', {}).get('movies', 0):,}")
    print(f"  TV shows: {meta.get('counts', {}).get('tv_shows', 0):,}")
except Exception as e:
    print(f"Could not read metadata: {e}")

t.close()
print("\n✅ Bundle verification complete")
