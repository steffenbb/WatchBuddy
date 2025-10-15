#!/usr/bin/env python3
"""
Simplified comprehensive list sync analysis tool.
"""
import asyncio
import time
import json
from typing import Dict, List, Any
from datetime import datetime

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models import UserList
from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.services.scoring_engine import ScoringEngine


async def analyze_list(user_list: UserList) -> Dict[str, Any]:
    """Analyze a single list sync with comprehensive metrics."""
    print(f"\n{'='*80}")
    print(f"Analyzing: {user_list.title} (ID: {user_list.id})")
    print(f"{'='*80}")
    
    # Parse filters from JSON text
    filters = {}
    if user_list.filters:
        try:
            filters = json.loads(user_list.filters)
        except:
            pass
    
    result = {
        'list_id': user_list.id,
        'list_name': user_list.title,
        'list_type': user_list.list_type,
        'filters': filters,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        # Phase 1: Candidate sourcing
        print(f"\nPhase 1: Sourcing candidates...")
        start_time = time.time()
        
        provider = BulkCandidateProvider(user_id=user_list.user_id or 1)
        
        # Extract filter components
        media_type = filters.get('media_type', 'movies')
        genres = filters.get('genres', [])
        languages = filters.get('languages', [])
        # Map year_from/year_to to min_year/max_year
        min_year = filters.get('min_year') or filters.get('year_from')
        max_year = filters.get('max_year') or filters.get('year_to')
        # Rating threshold
        min_rating = filters.get('min_rating') or filters.get('rating_from')
        # Genre mode (any/all)
        genre_mode = filters.get('genre_mode', 'any')
        discovery = filters.get('discovery', 'balanced')
        
        # Helper to call provider for a single media type
        async def fetch_for_type(mt: str):
            return await provider.get_candidates(
                media_type=mt,
                genres=genres,
                languages=languages,
                min_year=min_year,
                max_year=max_year,
                min_rating=min_rating,
                discovery=discovery,
                genre_mode=genre_mode,
                limit=200
            )

        # Get candidates (support media_type as list)
        candidates = []
        if isinstance(media_type, list):
            for mt in media_type:
                part = await fetch_for_type(mt)
                candidates.extend(part)
            # Deduplicate by tmdb_id if present, else by title+year
            seen = set()
            unique = []
            for c in candidates:
                key = c.get('tmdb_id') or (c.get('title'), c.get('year'))
                if key not in seen:
                    seen.add(key)
                    unique.append(c)
            candidates = unique[:200]
        else:
            candidates = await fetch_for_type(media_type)
        
        sourcing_duration = time.time() - start_time
        result['candidates_sourced'] = len(candidates)
        result['sourcing_duration_ms'] = int(sourcing_duration * 1000)
        
        print(f"  ✓ Sourced {len(candidates)} candidates in {sourcing_duration:.2f}s")
        
        # Analyze candidate sources
        persistent_count = sum(1 for c in candidates if c.get('_from_persistent_store'))
        api_count = len(candidates) - persistent_count
        result['candidates_from_db'] = persistent_count
        result['candidates_from_api'] = api_count
        
        print(f"    - From persistent DB: {persistent_count}")
        print(f"    - From live API: {api_count}")
        
        # Track TMDB lookup failures (only for API-sourced candidates)
        tmdb_failures = sum(1 for c in candidates 
                           if not c.get('_from_persistent_store') and 
                           (not c.get('title') or not c.get('tmdb_id')))
        result['tmdb_failures'] = tmdb_failures
        if tmdb_failures > 0:
            print(f"    ⚠ TMDB lookup failures: {tmdb_failures}")
        
        # Phase 2: Scoring and filtering
        print(f"\nPhase 2: Scoring candidates...")
        start_time = time.time()
        
        engine = ScoringEngine(trakt_client=None)
        
        # Build user profile for scoring
        user_profile = {
            'genres': genres,
            'discovery': discovery
        }
        
        scored = []
        for candidate in candidates:
            score = await engine.score_candidate(candidate, user_profile, filters)
            if score > 0:
                scored.append({**candidate, 'score': score})
        
        # Sort by score and take top N
        target_count = user_list.item_limit or 30
        scored.sort(key=lambda x: x['score'], reverse=True)
        final_items = scored[:target_count]
        
        scoring_duration = time.time() - start_time
        result['final_item_count'] = len(final_items)
        result['scoring_duration_ms'] = int(scoring_duration * 1000)
        result['filtered_out_count'] = len(candidates) - len(final_items)
        
        print(f"  ✓ Scored and filtered to {len(final_items)} items in {scoring_duration:.2f}s")
        print(f"    - Filtered out: {result['filtered_out_count']} candidates")
        
        # Phase 3: Filter accuracy validation
        print(f"\nPhase 3: Validating filter accuracy...")
        
        correct_items = []
        incorrect_items = []
        
        # Genre normalization and alias map
        def normalize_genre(name: str) -> str:
            if not name:
                return ''
            n = name.strip().lower()
            aliases = {
                'sci-fi': 'science fiction',
                'scifi': 'science fiction',
                'romantic': 'romance',
                'rom-com': 'romance',  # treated individually; for filters we expect both romance+comedy explicitly
                'romcom': 'romance',   # ditto
                'suspense': 'thriller',
                'noir': 'crime',
                'biopic': 'history',  # paired with drama in compounds
            }
            return aliases.get(n, n)

        norm_required = [normalize_genre(g) for g in (genres or [])]
        lang_set = set([l.lower() for l in (languages or [])])

        # Track violation categories
        violation_counts = {
            'genre': 0,
            'language': 0,
            'year': 0,
            'rating': 0,
        }

        for item in final_items:
            violations = []
            
            # Check genre match
            # Pull genres from top-level or tmdb_data for persistent candidates
            item_genres = item.get('genres')
            if not item_genres:
                item_genres = (item.get('tmdb_data') or {}).get('genres', [])
            if isinstance(item_genres, str):
                try:
                    item_genres = json.loads(item_genres)
                except:
                    item_genres = []
            norm_item_genres = set(normalize_genre(g) for g in item_genres)
            # Compound expansions for item genres (virtual enrich for validation)
            # If item has 'history' and 'drama', consider it satisfying 'biopic'-like expectations
            if 'history' in norm_item_genres and 'drama' in norm_item_genres:
                norm_item_genres.add('biopic')
            # Romantic comedy inference: if both present, add 'romantic comedy' marker
            if 'romance' in norm_item_genres and 'comedy' in norm_item_genres:
                norm_item_genres.add('romantic comedy')
            if norm_required:
                # Genre synonyms helper (covers common overlaps)
                def genre_satisfies(req: str, have: set) -> bool:
                    syn = {
                        'mystery': {'mystery', 'thriller'},
                        'thriller': {'thriller', 'mystery'},
                        'science fiction': {'science fiction'},
                        'romance': {'romance'},
                        'comedy': {'comedy'},
                        'crime': {'crime', 'noir'},
                        'noir': {'crime', 'thriller'},
                        'animation': {'animation', 'animated'},
                        'biopic': {'biopic', 'history', 'drama'},
                        'romantic comedy': {'romantic comedy', 'romance', 'comedy'},
                    }.get(req, {req})
                    return any(s in have for s in syn)

                if genre_mode == 'all':
                    genre_match = all(genre_satisfies(g, norm_item_genres) for g in norm_required)
                else:  # any
                    genre_match = any(genre_satisfies(g, norm_item_genres) for g in norm_required)
                if not genre_match:
                    violations.append("genre")
                # Fallback: for required ['comedy', 'romance'] treat title/overview/keywords hints as match
                if 'genre' in violations and set(norm_required) == {'comedy', 'romance'} and genre_mode == 'all':
                    text = ' '.join(filter(None, [
                        (item.get('title') or ''),
                        ((item.get('tmdb_data') or {}).get('overview') or ''),
                        ' '.join((item.get('tmdb_data') or {}).get('keywords', []) if isinstance((item.get('tmdb_data') or {}).get('keywords', []), list) else [])
                    ])).lower()
                    if any(k in text for k in ['romantic comedy', 'rom-com', 'romcom']):
                        # remove the genre violation
                        violations = [v for v in violations if v != 'genre']
            
            # Check language
            item_language = (item.get('language') or item.get('original_language') or '').lower()
            if lang_set and item_language not in lang_set:
                violations.append("language")
            
            # Check year range
            item_year = item.get('year') or item.get('release_date', '')[:4] if item.get('release_date') else None
            if item_year:
                try:
                    year_num = int(item_year)
                    if min_year and year_num < min_year:
                        violations.append("year")
                    if max_year and year_num > max_year:
                        violations.append("year")
                except ValueError:
                    pass

            # Check rating threshold (prefer tmdb_data.vote_average for persistent candidates)
            if min_rating is not None:
                rating = item.get('vote_average') or item.get('rating')
                if rating is None:
                    rating = (item.get('tmdb_data') or {}).get('vote_average')
                try:
                    if rating is None or float(rating) < float(min_rating):
                        violations.append("rating")
                except Exception:
                    pass
            
            if violations:
                # Increment category counters (unique categories per item)
                for cat in set(violations):
                    if cat in violation_counts:
                        violation_counts[cat] += 1
                incorrect_items.append({
                    'title': item.get('title', 'Unknown'),
                    'tmdb_id': item.get('tmdb_id'),
                    'violations': violations
                })
            else:
                correct_items.append(item)
        
        result['correct_items'] = len(correct_items)
        result['incorrect_items'] = len(incorrect_items)
        result['filter_accuracy'] = len(correct_items) / len(final_items) if final_items else 0
        
        print(f"  ✓ Validation complete:")
        print(f"    - Correct items: {len(correct_items)}")
        print(f"    - Incorrect items: {len(incorrect_items)}")
        print(f"    - Filter accuracy: {result['filter_accuracy']*100:.1f}%")
        if incorrect_items:
            print(f"    - Violation breakdown: genre={violation_counts['genre']}, language={violation_counts['language']}, year={violation_counts['year']}, rating={violation_counts['rating']}")
        
        # Total duration
        result['total_duration_ms'] = result['sourcing_duration_ms'] + result['scoring_duration_ms']
        result['success'] = True
        
        print(f"\n✓ Analysis complete: {result['total_duration_ms']}ms total")
        
    except Exception as e:
        result['success'] = False
        result['error'] = str(e)
        print(f"\n✗ Error analyzing list: {e}")
        import traceback
        traceback.print_exc()
    
    return result


async def main():
    """Run comprehensive sync analysis."""
    print(f"\n{'#'*80}")
    print(f"# Comprehensive Sync Analysis")
    print(f"# Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*80}")
    
    db = SessionLocal()
    try:
        # Optional CLI args: --id <list_id> or --title "substring"
        import sys
        target_id = None
        title_filter = None
        args = sys.argv[1:]
        if args:
            # Simple parsing without argparse to keep dependencies minimal
            if '--id' in args:
                try:
                    target_id = int(args[args.index('--id') + 1])
                except Exception:
                    target_id = None
            if '--title' in args:
                try:
                    title_filter = args[args.index('--title') + 1]
                except Exception:
                    title_filter = None

        # Get lists for user 1, optionally filtered
        q = db.query(UserList).filter(UserList.user_id == 1)
        if target_id is not None:
            q = q.filter(UserList.id == target_id)
        if title_filter:
            from sqlalchemy import or_
            like = f"%{title_filter}%"
            q = q.filter(UserList.title.ilike(like))
        lists = q.all()
        
        if not lists:
            print(f"\n✗ No lists found")
            return
        
        print(f"\nFound {len(lists)} lists to analyze\n")
        
        results = []
        for user_list in lists:
            result = await analyze_list(user_list)
            results.append(result)
        
        # Generate summary
        print(f"\n\n{'#'*80}")
        print(f"# SUMMARY REPORT")
        print(f"{'#'*80}\n")
        
        successful = [r for r in results if r.get('success')]
        
        if successful:
            total_candidates = sum(r.get('candidates_sourced', 0) for r in successful)
            total_from_db = sum(r.get('candidates_from_db', 0) for r in successful)
            total_from_api = sum(r.get('candidates_from_api', 0) for r in successful)
            
            print(f"Total lists analyzed: {len(results)}")
            print(f"Successful: {len(successful)}")
            print(f"Failed: {len(results) - len(successful)}")
            print(f"\nPerformance:")
            print(f"  Average sync time: {sum(r.get('total_duration_ms', 0) for r in successful) / len(successful):.0f}ms")
            print(f"  Average sourcing time: {sum(r.get('sourcing_duration_ms', 0) for r in successful) / len(successful):.0f}ms")
            print(f"  Average scoring time: {sum(r.get('scoring_duration_ms', 0) for r in successful) / len(successful):.0f}ms")
            print(f"\nCandidate sourcing:")
            print(f"  Total candidates: {total_candidates}")
            print(f"  From persistent DB: {total_from_db} ({total_from_db/total_candidates*100:.1f}%)")
            print(f"  From live API: {total_from_api} ({total_from_api/total_candidates*100:.1f}%)")
            print(f"\nFiltering:")
            print(f"  Average filter accuracy: {sum(r.get('filter_accuracy', 0) for r in successful) / len(successful):.1%}")
            print(f"  Total incorrect items: {sum(r.get('incorrect_items', 0) for r in successful)}")
            print(f"\nTMDB lookups:")
            print(f"  Total failures: {sum(r.get('tmdb_failures', 0) for r in successful)}")
        
        # Save to file
        # Make filename reflect filter for easier debugging
        suffix = ''
        if target_id is not None:
            suffix = f"_id_{target_id}"
        elif title_filter:
            # sanitize title_filter for filename
            safe = ''.join(c for c in title_filter if c.isalnum() or c in ('-', '_'))[:40]
            suffix = f"_title_{safe}"
        output_path = f"/app/sync_analysis_report{suffix}.json"
        with open(output_path, 'w') as f:
            json.dump({'results': results}, f, indent=2)
        
        print(f"\n✓ Full report saved to: {output_path}")
        
    finally:
        db.close()


if __name__ == '__main__':
    asyncio.run(main())
