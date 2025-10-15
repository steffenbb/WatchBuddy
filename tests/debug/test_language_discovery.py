import asyncio
import asyncio
import sys, os

# Ensure /app on sys.path before importing application modules
ROOT = '/app'
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.bulk_candidate_provider import BulkCandidateProvider

async def run():
    provider = BulkCandidateProvider(user_id=1)
    candidates = await provider.get_candidates(
        media_type='movies',
        limit=40,
        discovery='popular',
        genres=['thriller','mystery'],
        languages=['da'],
        enrich_with_tmdb=True,
        fusion_mode=True,
        list_title='Danske Thriller Anbefalinger'
    )
    print(f"Total returned (trimmed to limit): {len(candidates)}")
    from collections import Counter
    langs = []
    for c in candidates:
        inner = c.get('movie') or c.get('show') or c
        lang = inner.get('language') or (inner.get('tmdb_data') or {}).get('original_language')
        if lang: langs.append(lang)
    print('Language distribution:', Counter(langs))
    print('\nSample:')
    for inner in [ (c.get('movie') or c.get('show') or c) for c in candidates[:12] ]:
        print('-', inner.get('title'), '| lang:', inner.get('language') or (inner.get('tmdb_data') or {}).get('original_language'), '| genres:', inner.get('genres'))

if __name__ == '__main__':
    asyncio.run(run())
