"""Check for TMDB ID overlaps between movies and shows."""
import csv

movies = set()
shows = set()

with open("/app/data/TMDB_movie_dataset_v11.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r.get("id"):
            movies.add(r["id"])

with open("/app/data/TMDB_tv_dataset_v3.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r.get("id"):
            shows.add(r["id"])

overlap = movies & shows
print(f"Movies: {len(movies):,}")
print(f"Shows: {len(shows):,}")
print(f"Overlap: {len(overlap):,}")
if overlap:
    print(f"Sample overlaps: {list(overlap)[:20]}")
