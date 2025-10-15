#!/usr/bin/env python3
"""
Test script to manually trigger content ingestion.
"""
import asyncio
import sys
sys.path.insert(0, '/app')

from app.services.candidate_ingestion import ingest_new_content

async def main():
    print("Testing ingestion with 2 pages of 10 movies...")
    result = await ingest_new_content('movies', pages=2, per_page=10)
    print(f"Ingestion result: {result}")
    print("✓ Test complete!")

if __name__ == "__main__":
    asyncio.run(main())
