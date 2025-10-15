#!/usr/bin/env python3
"""Analyze CSV files to understand filtering impact."""
import csv
from pathlib import Path

def analyze_csv(filepath, media_type):
    """Analyze a CSV file for required fields."""
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    total = len(rows)
    has_id = sum(1 for r in rows if r.get('id'))
    has_title = sum(1 for r in rows if r.get('title') or r.get('name'))
    has_lang = sum(1 for r in rows if r.get('original_language'))
    
    valid_all = sum(1 for r in rows if 
                   r.get('id') and 
                   (r.get('title') or r.get('name')) and 
                   r.get('original_language'))
    
    missing_lang = sum(1 for r in rows if 
                      r.get('id') and 
                      (r.get('title') or r.get('name')) and 
                      not r.get('original_language'))
    
    print(f"\n{media_type.upper()} CSV Analysis ({Path(filepath).name}):")
    print(f"  Total rows: {total:,}")
    print(f"  Has ID: {has_id:,} ({has_id/total*100:.1f}%)")
    print(f"  Has title/name: {has_title:,} ({has_title/total*100:.1f}%)")
    print(f"  Has language: {has_lang:,} ({has_lang/total*100:.1f}%)")
    print(f"  Valid (id+title+lang): {valid_all:,} ({valid_all/total*100:.1f}%)")
    print(f"  Missing only language: {missing_lang:,}")
    
    return valid_all

if __name__ == "__main__":
    data_dir = Path('/app/data')
    
    movie_csv = data_dir / 'TMDB_movie_dataset_v11.csv'
    tv_csv = data_dir / 'TMDB_tv_dataset_v3.csv'
    
    total_valid = 0
    
    if movie_csv.exists():
        total_valid += analyze_csv(movie_csv, 'movie')
    
    if tv_csv.exists():
        total_valid += analyze_csv(tv_csv, 'tv')
    
    print(f"\n{'='*60}")
    print(f"EXPECTED IMPORT TOTAL: {total_valid:,} items")
    print(f"{'='*60}")
