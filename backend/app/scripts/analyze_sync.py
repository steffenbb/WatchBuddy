#!/usr/bin/env python3
"""
Comprehensive list sync analysis tool.

Measures for each list:
- Candidate count from sourcing
- Final item count after scoring
- Correct items (match list filters)
- Incorrect items (wrong genre/language/year)
- Filtered correct candidates (matched but scored lower)
- TMDB lookup failures
- Sync duration
"""
import asyncio
import time
import json
from typing import Dict, List, Any
from datetime import datetime

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models import UserList, ListItem, PersistentCandidate
from app.services.list_sync import ListSyncService
from app.services.bulk_candidate_provider import BulkCandidateProvider
from app.services.scoring_engine import ScoringEngine


class SyncAnalyzer:
    """Analyzes list syncs with detailed metrics."""
    
    def __init__(self):
        self.db: Session = SessionLocal()
        self.results: List[Dict[str, Any]] = []
    
    def __del__(self):
        self.db.close()
    
    async def analyze_list(self, user_list: UserList) -> Dict[str, Any]:
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
            min_year = filters.get('min_year')
            max_year = filters.get('max_year')
            discovery = filters.get('discovery', 'balanced')
            
            # Get candidates
            candidates = await provider.get_candidates(
                media_type=media_type,
                genres=genres,
                languages=languages,
                min_year=min_year,
                max_year=max_year,
                discovery=discovery,
                limit=200  # Large enough to test filtering
            )
            
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
            
            # Track TMDB lookup failures (missing critical metadata)
            # For persistent candidates, title and tmdb_id are always present
            # Real failures are missing genres, poster_path, or other enrichment data
            tmdb_failures = sum(1 for c in candidates 
                               if not c.get('_from_persistent_store') and 
                               (not c.get('title') or not c.get('tmdb_id')))
            result['tmdb_failures'] = tmdb_failures
            if tmdb_failures > 0:
                print(f"    ⚠ TMDB lookup failures: {tmdb_failures}")
            
            # Phase 2: Scoring and filtering
            print(f"\nPhase 2: Scoring candidates...")
            start_time = time.time()
            
                # ScoringEngine takes optional trakt_client
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
            
            # Phase 3: Validation - check if items match filters
            print(f"\nPhase 3: Validating filter accuracy...")
            
            correct_items = []
            incorrect_items = []
            
            for item in final_items:
                violations = []
                
                # Check genre match
                item_genres = item.get('genres', [])
                if isinstance(item_genres, str):
                    try:
                        item_genres = json.loads(item_genres)
                    except:
                        item_genres = []
                
                if genres:
                    genre_match = any(
                        g.lower() in [ig.lower() for ig in item_genres]
                        for g in genres
                    )
                    if not genre_match:
                        violations.append(f"genre mismatch (wanted {genres}, got {item_genres})")
                
                # Check language
                item_language = item.get('language') or item.get('original_language', '')
                if languages and item_language not in languages:
                    violations.append(f"language mismatch (wanted {languages}, got {item_language})")
                
                # Check year range
                item_year = item.get('year') or item.get('release_date', '')[:4]
                if item_year:
                    try:
                        year_num = int(item_year)
                        if min_year and year_num < min_year:
                            violations.append(f"year too old (wanted >={min_year}, got {year_num})")
                        if max_year and year_num > max_year:
                            violations.append(f"year too new (wanted <={max_year}, got {year_num})")
                    except ValueError:
                        pass
                
                if violations:
                    incorrect_items.append({
                        'title': item.get('title', 'Unknown'),
                        'tmdb_id': item.get('tmdb_id'),
                        'score': item.get('score', 0),
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
                print(f"\n  ⚠ Incorrect items details:")
                for item in incorrect_items[:5]:  # Show first 5
                    print(f"    - {item['title']} (score={item['score']:.2f})")
                    for v in item['violations']:
                        print(f"      • {v}")
                if len(incorrect_items) > 5:
                    print(f"    ... and {len(incorrect_items)-5} more")
            
            # Phase 4: Check filtered correct candidates
            print(f"\nPhase 4: Analyzing filtered-out candidates...")
            
            filtered_correct = []
            for candidate in scored[target_count:]:  # Items that didn't make the cut
                # Quick validation (simplified)
                item_genres = candidate.get('genres', [])
                if isinstance(item_genres, str):
                    try:
                        item_genres = json.loads(item_genres)
                    except:
                        item_genres = []
                
                genre_ok = not genres or any(g.lower() in [ig.lower() for ig in item_genres] for g in genres)
                
                item_language = candidate.get('language') or candidate.get('original_language', '')
                language_ok = not languages or item_language in languages
                
                if genre_ok and language_ok:
                    filtered_correct.append(candidate)
            
            result['filtered_correct_count'] = len(filtered_correct)
            print(f"  ✓ Found {len(filtered_correct)} correct candidates that were filtered out by scoring")
            
            # Total duration
            result['total_duration_ms'] = result['sourcing_duration_ms'] + result['scoring_duration_ms']
            result['success'] = True
            
            print(f"\n{'='*80}")
            print(f"✓ Analysis complete: {result['total_duration_ms']}ms total")
            print(f"{'='*80}")
            
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)
            print(f"\n✗ Error analyzing list: {e}")
            import traceback
            traceback.print_exc()
        
        return result
    
    async def analyze_all_lists(self, user_id: int = 1) -> Dict[str, Any]:
        """Analyze all lists for a user."""
        print(f"\n{'#'*80}")
        print(f"# Comprehensive Sync Analysis - User {user_id}")
        print(f"# Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*80}")
        
        # Get all lists
        lists = self.db.query(UserList).filter(UserList.user_id == user_id).all()
        
        if not lists:
            print(f"\n✗ No lists found for user {user_id}")
            return {'error': 'No lists found'}
        
        print(f"\nFound {len(lists)} lists to analyze\n")
        
        # Analyze each list
        for user_list in lists:
            result = await self.analyze_list(user_list)
            self.results.append(result)
        
        # Generate summary report
        print(f"\n\n{'#'*80}")
        print(f"# SUMMARY REPORT")
        print(f"{'#'*80}\n")
        
        summary = self._generate_summary()
        
        # Print summary
        print(f"Total lists analyzed: {summary['total_lists']}")
        print(f"Successful: {summary['successful_lists']}")
        print(f"Failed: {summary['failed_lists']}")
        print(f"\nPerformance:")
        print(f"  Average sync time: {summary['avg_sync_time_ms']:.0f}ms")
        print(f"  Average sourcing time: {summary['avg_sourcing_time_ms']:.0f}ms")
        print(f"  Average scoring time: {summary['avg_scoring_time_ms']:.0f}ms")
        print(f"\nCandidate sourcing:")
        print(f"  Average candidates sourced: {summary['avg_candidates_sourced']:.0f}")
        print(f"  From persistent DB: {summary['total_from_db']} ({summary['pct_from_db']:.1f}%)")
        print(f"  From live API: {summary['total_from_api']} ({summary['pct_from_api']:.1f}%)")
        print(f"\nFiltering:")
        print(f"  Average filter accuracy: {summary['avg_filter_accuracy']:.1%}")
        print(f"  Total incorrect items: {summary['total_incorrect_items']}")
        print(f"  Total filtered correct: {summary['total_filtered_correct']}")
        print(f"\nTMDB lookups:")
        print(f"  Total failures: {summary['total_tmdb_failures']}")
        
        # Per-list details
        print(f"\n{'='*80}")
        print(f"Per-List Details:")
        print(f"{'='*80}\n")
        
        for result in self.results:
            if not result.get('success'):
                print(f"✗ {result['list_name']}: {result.get('error', 'Unknown error')}")
                continue
            
            print(f"\n{result['list_name']} ({result['list_type']}):")
            print(f"  Candidates: {result['candidates_sourced']} "
                  f"(DB: {result['candidates_from_db']}, API: {result['candidates_from_api']})")
            print(f"  Final items: {result['final_item_count']}")
            print(f"  Accuracy: {result['filter_accuracy']:.1%} "
                  f"(correct: {result['correct_items']}, incorrect: {result['incorrect_items']})")
            print(f"  Timing: {result['total_duration_ms']}ms "
                  f"(sourcing: {result['sourcing_duration_ms']}ms, scoring: {result['scoring_duration_ms']}ms)")
            if result['tmdb_failures'] > 0:
                print(f"  ⚠ TMDB failures: {result['tmdb_failures']}")
        
        print(f"\n{'#'*80}")
        print(f"# Analysis complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*80}\n")
        
        return {
            'summary': summary,
            'results': self.results
        }
    
    def _generate_summary(self) -> Dict[str, Any]:
        """Generate aggregate summary statistics."""
        successful = [r for r in self.results if r.get('success')]
        
        if not successful:
            return {
                'total_lists': len(self.results),
                'successful_lists': 0,
                'failed_lists': len(self.results),
                'avg_sync_time_ms': 0,
                'avg_sourcing_time_ms': 0,
                'avg_scoring_time_ms': 0,
                'avg_candidates_sourced': 0,
                'total_from_db': 0,
                'total_from_api': 0,
                'pct_from_db': 0,
                'pct_from_api': 0,
                'avg_filter_accuracy': 0,
                'total_incorrect_items': 0,
                'total_filtered_correct': 0,
                'total_tmdb_failures': 0,
            }
        
        total_candidates = sum(r.get('candidates_sourced', 0) for r in successful)
        total_from_db = sum(r.get('candidates_from_db', 0) for r in successful)
        total_from_api = sum(r.get('candidates_from_api', 0) for r in successful)
        
        return {
            'total_lists': len(self.results),
            'successful_lists': len(successful),
            'failed_lists': len(self.results) - len(successful),
            'avg_sync_time_ms': sum(r.get('total_duration_ms', 0) for r in successful) / len(successful),
            'avg_sourcing_time_ms': sum(r.get('sourcing_duration_ms', 0) for r in successful) / len(successful),
            'avg_scoring_time_ms': sum(r.get('scoring_duration_ms', 0) for r in successful) / len(successful),
            'avg_candidates_sourced': total_candidates / len(successful),
            'total_from_db': total_from_db,
            'total_from_api': total_from_api,
            'pct_from_db': (total_from_db / total_candidates * 100) if total_candidates > 0 else 0,
            'pct_from_api': (total_from_api / total_candidates * 100) if total_candidates > 0 else 0,
            'avg_filter_accuracy': sum(r.get('filter_accuracy', 0) for r in successful) / len(successful),
            'total_incorrect_items': sum(r.get('incorrect_items', 0) for r in successful),
            'total_filtered_correct': sum(r.get('filtered_correct_count', 0) for r in successful),
            'total_tmdb_failures': sum(r.get('tmdb_failures', 0) for r in successful),
        }


async def main():
    """Run comprehensive sync analysis."""
    analyzer = SyncAnalyzer()
    
    try:
        report = await analyzer.analyze_all_lists(user_id=1)
        
        # Save to file
        output_path = '/app/sync_analysis_report.json'
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n✓ Full report saved to: {output_path}")
        
    except Exception as e:
        print(f"\n✗ Analysis failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
