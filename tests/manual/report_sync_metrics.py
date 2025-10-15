#!/usr/bin/env python3
"""
Sync all lists and report candidate pool + quality metrics.
- For each existing UserList: fetch enhanced candidates (per list filters), sync the list, and compute:
  - candidates_found: number of filter-matching candidates available for ranking (limit capped)
  - metadata_coverage: % with TMDB or cached metadata; % with any genres; % with language info
  - saved_items: count, avg score, median score
  - filter_match_rate on saved items (genres, languages, year/rating when present)
  - tmdb_rating_avg on saved items (if available)
Totals are summarized at the end.

Run inside container:
  docker exec -i watchbuddy-backend-1 python /app/tests/manual/report_sync_metrics.py
"""

import asyncio
import json
import statistics
from typing import Any, Dict, List, Optional

import sys, os
sys.path.append('/app')  # ensure imports work in container

from app.core.database import SessionLocal
from app.models import UserList, ListItem
from app.services.list_sync import ListSyncService
from app.services.bulk_candidate_provider import BulkCandidateProvider


def _get_filters(user_list: UserList) -> Dict[str, Any]:
    try:
        return json.loads(user_list.filters or '{}')
    except Exception:
        return {}


def _extract_media_types(filters: Dict[str, Any]) -> List[str]:
    media_types = filters.get("media_types", ["movies", "shows"])
    if not isinstance(media_types, list) or not media_types:
        media_types = ["movies", "shows"]
    return media_types


def _compute_enhanced_params(user_list: UserList, filters: Dict[str, Any]):
    discovery = filters.get("discovery") or filters.get("mood") or "balanced"
    items_per_list = max(1, int(filters.get("item_limit") or (user_list.item_limit or 25)))
    base_limit = max(int(filters.get("candidate_limit") or 200), items_per_list * 3)
    enhanced_limit = max(base_limit, 1000)
    enhanced_discovery = discovery
    if (user_list.list_type == "smartlist" or (user_list.item_limit or 0) >= 50 or enhanced_limit >= 800):
        enhanced_discovery = "ultra_discovery"
        enhanced_limit = min(enhanced_limit * 3, 5000)
    return enhanced_discovery, enhanced_limit


def _languages_from_filters(filters: Dict[str, Any]) -> List[str]:
    langs = filters.get("languages", [])
    return langs if isinstance(langs, list) else []


def _genres_from_filters(filters: Dict[str, Any]) -> List[str]:
    gens = filters.get("genres", [])
    return gens if isinstance(gens, list) else []


def _safe_num(x):
    try:
        return float(x)
    except Exception:
        return None


def _has_genres(item: Dict[str, Any]) -> bool:
    if item.get('genres'):  # Trakt genres list
        return True
    # tmdb_data or cached_metadata genres
    if item.get('tmdb_data') and item['tmdb_data'].get('genres'):
        return True
    if item.get('cached_metadata') and item['cached_metadata'].get('genres'):
        return True
    return False


def _has_language(item: Dict[str, Any]) -> bool:
    if item.get('language'):
        return True
    if item.get('tmdb_data') and item['tmdb_data'].get('original_language'):
        return True
    if item.get('cached_metadata') and item['cached_metadata'].get('language'):
        return True
    return False


def _matches_filters(item: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    # Genres
    gens = _genres_from_filters(filters)
    if gens:
        all_g = set([g.lower() for g in (item.get('genres') or [])])
        if item.get('tmdb_data') and item['tmdb_data'].get('genres'):
            all_g.update([g.lower() for g in item['tmdb_data']['genres'] if isinstance(g, str)])
        if item.get('cached_metadata') and item['cached_metadata'].get('genres'):
            cg = item['cached_metadata']['genres']
            if isinstance(cg, list):
                all_g.update([g.lower() for g in cg])
        if all_g and not (all_g & set([g.lower() for g in gens])):
            return False
    # Languages
    langs = _languages_from_filters(filters)
    if langs:
        ok = False
        il = item.get('language')
        if il and il in langs: ok = True
        tl = item.get('tmdb_data', {}).get('original_language') if item.get('tmdb_data') else None
        if tl and tl in langs: ok = True
        cl = item.get('cached_metadata', {}).get('language') if item.get('cached_metadata') else None
        if cl and cl in langs: ok = True
        if not ok:
            return False
    # Years
    y = item.get('year')
    yf = filters.get('year_from') or filters.get('min_year')
    yt = filters.get('year_to') or filters.get('max_year')
    if yf and (not y or y < int(yf)): return False
    if yt and (not y or y > int(yt)): return False
    # Ratings (Trakt rating)
    mr = filters.get('min_rating')
    if mr is not None:
        rt = _safe_num(item.get('rating'))
        if rt is None or rt < float(mr):
            return False
    return True


async def main():
    db = SessionLocal()
    try:
        lists = db.query(UserList).all()
        if not lists:
            print("No lists found.")
            return
        print(f"Found {len(lists)} lists. Starting sync + metrics...\n")

        totals = {
            'lists': 0,
            'candidates': 0,
            'saved': 0,
            'avg_score_sum': 0.0,
            'avg_score_count': 0,
        }

        for ul in lists:
            filters = _get_filters(ul)
            media_types = _extract_media_types(filters)
            discovery, enhanced_limit = _compute_enhanced_params(ul, filters)
            genres = _genres_from_filters(filters)
            languages = _languages_from_filters(filters)
            min_year = filters.get('year_from') or filters.get('min_year')
            max_year = filters.get('year_to') or filters.get('max_year')
            min_rating = filters.get('min_rating')
            search_keywords = filters.get('search_query')
            search_keywords_list = search_keywords.split() if isinstance(search_keywords, str) else None

            provider = BulkCandidateProvider(user_id=ul.user_id or 1)

            # Gather candidates across media types
            all_candidates: List[Dict[str, Any]] = []
            for mt in media_types:
                batch = await provider.get_candidates(
                    media_type=mt,
                    limit=enhanced_limit,
                    discovery=discovery,
                    genres=genres or None,
                    languages=languages or None,
                    min_year=min_year,
                    max_year=max_year,
                    min_rating=min_rating,
                    search_keywords=search_keywords_list,
                    enrich_with_tmdb=True,
                )
                all_candidates.extend(batch)

            # Candidate metrics
            cand_count = len(all_candidates)
            with_tmdb = sum(1 for c in all_candidates if (c.get('tmdb_data') or c.get('cached_metadata'))) 
            with_genres = sum(1 for c in all_candidates if _has_genres(c))
            with_lang = sum(1 for c in all_candidates if _has_language(c))
            match_rate_est = sum(1 for c in all_candidates if _matches_filters(c, filters)) / cand_count if cand_count else 0.0

            # Sync list
            svc = ListSyncService(user_id=ul.user_id or 1)
            result = await svc._sync_single_list(ul, force_full=True)

            # Saved items metrics
            q = db.query(ListItem).filter(ListItem.smartlist_id == ul.id)
            saved = q.count()
            scores = [x.score for x in q if x.score is not None]
            avg_score = statistics.mean(scores) if scores else 0.0
            med_score = statistics.median(scores) if scores else 0.0

            totals['lists'] += 1
            totals['candidates'] += cand_count
            totals['saved'] += saved
            if scores:
                totals['avg_score_sum'] += avg_score
                totals['avg_score_count'] += 1

            print(f"List {ul.id} - {ul.title}")
            print(f"  Discovery={discovery} limit={enhanced_limit} types={media_types}")
            print(f"  Candidates: {cand_count} | metadata: TMDB {with_tmdb/cand_count:.0%}, genres {with_genres/cand_count:.0%}, lang {with_lang/cand_count:.0%}")
            print(f"  Filter-match (est.): {match_rate_est:.0%}")
            print(f"  Saved items: {saved} | avg score={avg_score:.2f}, median={med_score:.2f}")
            print("")

        print("== Summary ==")
        print(f"Lists synced: {totals['lists']}")
        if totals['lists']:
            print(f"Avg candidates per list: {totals['candidates']//totals['lists']}")
            print(f"Avg saved per list: {totals['saved']//totals['lists']}")
            if totals['avg_score_count']:
                print(f"Avg score (mean of means): {totals['avg_score_sum']/totals['avg_score_count']:.2f}")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
