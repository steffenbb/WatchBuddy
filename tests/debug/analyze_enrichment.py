#!/usr/bin/env python3
"""Analyze TMDB enrichment success rate"""
from app.core.database import SessionLocal
from app.models import MediaMetadata, ListItem

db = SessionLocal()
try:
    # Check total metadata cache
    total_metadata = db.query(MediaMetadata).count()
    print(f"Total items with TMDB metadata cached: {total_metadata}")
    
    # Check items in list 37 with/without metadata
    list_items = db.query(ListItem).filter(ListItem.smartlist_id == 37).all()
    print(f"Total items in list 37: {len(list_items)}")
    
    enriched_count = 0
    for item in list_items:
        metadata = db.query(MediaMetadata).filter(MediaMetadata.trakt_id == item.trakt_id).first()
        if metadata:
            enriched_count += 1
    
    print(f"Items with TMDB metadata: {enriched_count}")
    print(f"Items without TMDB metadata: {len(list_items) - enriched_count}")
    if len(list_items) > 0:
        enrichment_rate = (enriched_count / len(list_items)) * 100
        print(f"Enrichment success rate: {enrichment_rate:.1f}%")
    
finally:
    db.close()