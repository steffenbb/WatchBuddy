"""
elasticsearch_client.py

ElasticSearch client for Individual Lists hybrid search.
Provides literal fuzzy search across title, cast, crew, keywords, genres.
Enhanced with mood/tone/theme extraction for intelligent matching.
"""
import logging
import time
from typing import List, Dict, Any, Optional
from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import NotFoundError, ConnectionError as ESConnectionError
from app.services.ai_engine.mood_extractor import get_mood_extractor
from app.core.database import SessionLocal
from app.models import ItemLLMProfile, PersistentCandidate

logger = logging.getLogger(__name__)

# ElasticSearch connection
ES_HOST = "elasticsearch"
ES_PORT = 9200
INDEX_NAME = "watchbuddy_candidates"


class ElasticSearchClient:
    """
    ElasticSearch client for fuzzy search on persistent_candidates.
    
    Features:
    - Multi-field search (title, cast, crew, keywords, genres)
    - Fuzzy matching for typo tolerance
    - Boost title matches higher than other fields
    - Returns TMDB IDs with relevance scores
    """
    
    def __init__(self):
        self.es = None
        # Cache ping results to avoid frequent slow pings under load
        self._last_ping_ok: bool = False
        self._last_ping_at: float | None = None
        self._connect()
    
    def _connect(self):
        """Connect to ElasticSearch."""
        try:
            self.es = Elasticsearch(
                [f"http://{ES_HOST}:{ES_PORT}"],
                request_timeout=10,  # Reduced from 30s for faster failures
                max_retries=2,  # Reduced from 3 for faster failures
                retry_on_timeout=True,
                sniff_on_start=False,  # Disable sniffing for Docker environments
                sniff_on_connection_fail=False
            )
            
            # Test connection
            if self._ping(timeout=2):
                logger.info(f"Connected to ElasticSearch at {ES_HOST}:{ES_PORT}")
            else:
                logger.error("Failed to ping ElasticSearch")
                self.es = None
                
        except Exception as e:
            logger.error(f"Failed to connect to ElasticSearch: {e}")
            self.es = None
    
    def _ping(self, timeout: int = 2) -> bool:
        """Perform a quick ping with a short timeout and cache the result."""
        if not self.es:
            self._last_ping_ok = False
            self._last_ping_at = time.time()
            return False
        try:
            ok = self.es.ping(request_timeout=timeout)
            self._last_ping_ok = bool(ok)
            self._last_ping_at = time.time()
            return self._last_ping_ok
        except Exception:
            self._last_ping_ok = False
            self._last_ping_at = time.time()
            return False

    def is_connected(self) -> bool:
        """Check if connected to ElasticSearch with cached ping (valid for ~60s)."""
        if not self.es:
            return False
        now = time.time()
        if self._last_ping_at and (now - self._last_ping_at) < 60:
            return self._last_ping_ok
        # Refresh ping result with short timeout
        return self._ping(timeout=2)
    
    def create_index(self):
        """
        Create index with mapping for persistent_candidates.
        
        Fields indexed for search:
        - tmdb_id, media_type (identifiers)
        - title, original_title (text with autocomplete)
        - overview, tagline (text)
        - genres, keywords (text)
        - cast, created_by, networks (text - people/companies)
        - production_companies, production_countries (text)
        - spoken_languages (text)
        - year, popularity, vote_average (numeric for filtering/boosting)
        """
        if not self.es:
            logger.error("Not connected to ElasticSearch")
            return False
        
        mapping = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "refresh_interval": "30s",  # Reduce refresh frequency for better indexing performance
                "index": {
                    "max_result_window": 10000  # Allow deeper pagination if needed
                },
                "analysis": {
                    "analyzer": {
                        "autocomplete": {
                            "tokenizer": "autocomplete",
                            "filter": ["lowercase", "asciifolding"]  # Handle accents
                        },
                        "autocomplete_search": {
                            "tokenizer": "lowercase"
                        }
                    },
                    "tokenizer": {
                        "autocomplete": {
                            "type": "edge_ngram",
                            "min_gram": 2,
                            "max_gram": 10,
                            "token_chars": ["letter", "digit"]
                        }
                    }
                }
            },
            "mappings": {
                "properties": {
                    "tmdb_id": {"type": "integer"},
                    "media_type": {"type": "keyword"},
                    "title": {
                        "type": "text",
                        "analyzer": "autocomplete",
                        "search_analyzer": "autocomplete_search",
                        "fields": {
                            "keyword": {"type": "keyword"}
                        }
                    },
                    "original_title": {"type": "text"},
                    "overview": {"type": "text"},
                    "tagline": {"type": "text"},
                    "genres": {"type": "text"},
                    "keywords": {"type": "text"},
                    "cast": {"type": "text"},
                    "created_by": {"type": "text"},  # TV shows
                    "networks": {"type": "text"},  # TV shows
                    "production_companies": {"type": "text"},
                    "production_countries": {"type": "text"},
                    "spoken_languages": {"type": "text"},
                    "year": {"type": "integer"},
                    "popularity": {"type": "float"},
                    "vote_average": {"type": "float"},
                    "vote_count": {"type": "integer"},
                    "mood_tags": {"type": "keyword"},  # Extracted from ItemLLMProfile
                    "tone_tags": {"type": "keyword"},  # Extracted from ItemLLMProfile
                    "themes": {"type": "keyword"}      # Extracted from ItemLLMProfile
                }
            }
        }
        
        try:
            # Delete existing index if exists
            if self.es.indices.exists(index=INDEX_NAME):
                logger.info(f"Deleting existing index: {INDEX_NAME}")
                self.es.indices.delete(index=INDEX_NAME)
            
            # Create new index
            self.es.indices.create(index=INDEX_NAME, body=mapping)
            logger.info(f"Created ElasticSearch index: {INDEX_NAME}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create index: {e}")
            return False
    
    def index_candidates(self, candidates: List[Dict[str, Any]]) -> int:
        """
        Bulk index candidates into ElasticSearch using a streaming generator
        to minimize memory usage. Enriches with mood/tone/themes from ItemLLMProfile.
        """
        if not self.es:
            logger.error("Not connected to ElasticSearch")
            return 0

        if not candidates:
            return 0
        
        # Fetch ItemLLMProfiles for candidates (batch lookup)
        db = SessionLocal()
        try:
            tmdb_ids = [c['tmdb_id'] for c in candidates]
            # Join with persistent_candidates to get tmdb_id
            profiles = db.query(ItemLLMProfile, PersistentCandidate.tmdb_id).join(
                PersistentCandidate, ItemLLMProfile.candidate_id == PersistentCandidate.id
            ).filter(
                PersistentCandidate.tmdb_id.in_(tmdb_ids)
            ).all()
            profile_map = {tmdb_id: profile for profile, tmdb_id in profiles}
        finally:
            db.close()
        
        # Extract mood/tone/themes
        extractor = get_mood_extractor()

        def action_generator():
            for c in candidates:
                # Get mood/tone/themes from profile
                profile = profile_map.get(c['tmdb_id'])
                tags = {'mood_tags': [], 'tone_tags': [], 'themes': []}
                if profile:
                    tags = extractor.extract_from_profile(profile)
                else:
                    # Fallback: extract from overview + genres
                    text = f"{c.get('overview', '')} {c.get('genres', '')}"
                    tags = extractor.extract_from_text(text)
                
                yield {
                    "_index": INDEX_NAME,
                    "_id": f"{c['tmdb_id']}_{c['media_type']}",
                    "_source": {
                        "tmdb_id": c['tmdb_id'],
                        "media_type": c['media_type'],
                        "title": c.get('title', ''),
                        "original_title": c.get('original_title', ''),
                        "overview": c.get('overview', ''),
                        "tagline": c.get('tagline', ''),
                        "genres": c.get('genres', ''),
                        "keywords": c.get('keywords', ''),
                        "cast": c.get('cast', ''),
                        "created_by": c.get('created_by', ''),
                        "networks": c.get('networks', ''),
                        "production_companies": c.get('production_companies', ''),
                        "production_countries": c.get('production_countries', ''),
                        "spoken_languages": c.get('spoken_languages', ''),
                        "year": c.get('year'),
                        "popularity": c.get('popularity'),
                        "vote_average": c.get('vote_average'),
                        "vote_count": c.get('vote_count'),
                        "mood_tags": tags.get('mood_tags', []),
                        "tone_tags": tags.get('tone_tags', []),
                        "themes": tags.get('themes', [])
                    }
                }

        try:
            success, failed = helpers.bulk(
                self.es,
                action_generator(),
                chunk_size=100,
                request_timeout=30,
                raise_on_error=False
            )
            logger.debug(f"Indexed {success} documents, {len(failed)} failures")
            return success
        except Exception as e:
            logger.error(f"Failed to bulk index: {e}")
            return 0
    
    def search(
        self,
        query: str,
        media_type: Optional[str] = None,
        limit: int = 50,
        strict_titles_only: bool = False,
        enhanced_filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fuzzy search across multiple fields with optional query enhancement.
        
        Args:
            query: Search query
            media_type: Filter by 'movie' or 'show' (optional)
            limit: Max results to return
            strict_titles_only: Use tight title-only matching
            enhanced_filters: Optional mood/tone/theme/people boost filters
                             from QueryEnhancer.build_es_filters()
        Returns:
            List of results with tmdb_id, media_type, title, score
        """
        if not self.es:
            logger.warning("Not connected to ElasticSearch, returning empty results")
            return []
        
        # Build query with field boosts
        # If strict_titles_only, keep search tight (titles + people/org), avoid expensive fields and fuzziness
        if strict_titles_only:
            # Tight, title-first matching for multi-word queries; ensure phrase + AND tokens on title fields
            max_exp = 50
            should_clauses = [
                # Strong phrase match on titles
                {"match_phrase": {"title": {"query": query, "boost": 10}}},
                {"match_phrase": {"original_title": {"query": query, "boost": 8}}},
                # AND all tokens must appear in the same field (best_fields)
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^8", "original_title^6"],
                        "type": "best_fields",
                        "operator": "and",
                        "fuzziness": 0
                    }
                },
                # Prefix helpers for partial typing
                {"match_bool_prefix": {"title": {"query": query, "boost": 5}}},
                {"match_bool_prefix": {"original_title": {"query": query, "boost": 4}}},
                {"match_phrase_prefix": {"title": {"query": query, "slop": 1, "boost": 4, "max_expansions": max_exp}}},
                {"match_phrase_prefix": {"original_title": {"query": query, "slop": 1, "boost": 3, "max_expansions": max_exp}}}
            ]
        else:
            # Use bool query with should clauses for broader matches
            # Reduce expensive field set; prefer titles + people/org + genres. Exclude overview/tagline/keywords.
            should_clauses = [
                # Exact matches (highest priority)
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^10", "original_title^8"],
                        "type": "phrase",
                        "boost": 3
                    }
                },
                # Prefix matches (fast autocomplete) using bool_prefix
                {"match_bool_prefix": {"title": {"query": query, "boost": 3}}},
                {"match_bool_prefix": {"original_title": {"query": query, "boost": 2}}},
                # Phrase prefix with capped expansions as a fallback
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^5", "original_title^4"],
                        "type": "phrase_prefix",
                        "boost": 2,
                        "max_expansions": 50
                    }
                },
                # Fuzzy matches (typo tolerance, limited fuzziness)
                {
                    "multi_match": {
                        "query": query,
                        "fields": [
                            "title^5",
                            "original_title^4",
                            "cast^3",
                            "created_by^2",
                            "production_companies^2",
                            "networks^2",
                            "genres^2",
                            "production_countries",
                            "spoken_languages"
                        ],
                        "fuzziness": 1 if len(query.strip()) >= 5 else 0,
                        "prefix_length": 2,
                        "type": "best_fields"
                    }
                }
            ]
            
            # Add enhanced filter boosts if provided (mood/tone/theme)
            if enhanced_filters and isinstance(enhanced_filters, list):
                should_clauses.extend(enhanced_filters)
        
        must_clauses = [
            {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1
                }
            }
        ]
        
        # Add media type filter if specified
        if media_type:
            must_clauses.append({
                "term": {"media_type": media_type}
            })
        
        search_body = {
            "query": {
                "bool": {
                    "must": must_clauses
                }
            },
            "size": limit,
            "timeout": "5s",  # Lower initial timeout; rely on a single retry for spikes
            "_source": ["tmdb_id", "media_type", "title", "year", "popularity"],
            "track_total_hits": False,
            "terminate_after": 5000,
            "request_cache": True
        }
        # Slight min_score to drop near-zero matches on broader queries
        if not strict_titles_only:
            search_body["min_score"] = 0.01
        
        try:
            response = self.es.search(
                index=INDEX_NAME,
                body=search_body,
                request_timeout=8,
                params={"filter_path": "hits.hits._source,hits.hits._score"}
            )
            
            results = []
            for hit in response['hits']['hits']:
                results.append({
                    "tmdb_id": hit['_source']['tmdb_id'],
                    "media_type": hit['_source']['media_type'],
                    "title": hit['_source']['title'],
                    "year": hit['_source'].get('year'),
                    "popularity": hit['_source'].get('popularity'),
                    "es_score": hit['_score']
                })
            
            logger.debug(f"ElasticSearch found {len(results)} results for: {query}")
            return results
            
        except NotFoundError:
            logger.error(f"Index {INDEX_NAME} not found")
            return []
        except Exception as e:
            logger.warning(f"Search failed (first attempt): {e}. Retrying once with longer timeout…")
            try:
                # Retry once with a higher timeout and without per-query timeout constraint
                retry_body = dict(search_body)
                retry_body.pop("timeout", None)
                response = self.es.search(
                    index=INDEX_NAME,
                    body=retry_body,
                    request_timeout=20,
                    params={"filter_path": "hits.hits._source,hits.hits._score"}
                )
                results = []
                for hit in response['hits']['hits']:
                    results.append({
                        "tmdb_id": hit['_source']['tmdb_id'],
                        "media_type": hit['_source']['media_type'],
                        "title": hit['_source']['title'],
                        "year": hit['_source'].get('year'),
                        "popularity": hit['_source'].get('popularity'),
                        "es_score": hit['_score']
                    })
                logger.debug(f"ElasticSearch retry found {len(results)} results for: {query}")
                return results
            except Exception as e2:
                logger.error(f"Search failed after retry: {e2}")
                return []
    
    def get_index_stats(self) -> Dict[str, Any]:
        """Get lightweight index statistics (docs and store size)."""
        if not self.es:
            return {"error": "Not connected"}
        try:
            # Prefer cat indices for a cheap summary
            cat = self.es.cat.indices(index=INDEX_NAME, format='json', bytes='b', request_timeout=5)
            if isinstance(cat, list) and cat:
                row = cat[0]
                # Field names vary slightly across ES versions
                docs = int(row.get('docs.count') or row.get('docsCount') or 0)
                size = int(row.get('store.size') or row.get('storeSize') or 0)
                return {"doc_count": docs, "size_bytes": size, "status": "healthy"}
        except Exception:
            # Fallback to narrow metrics via indices.stats and count
            pass
        try:
            stats = self.es.indices.stats(index=INDEX_NAME, metric=['docs', 'store'], request_timeout=8)
            count = self.es.count(index=INDEX_NAME, request_timeout=5)
            # Try multiple shapes for stats response
            size_bytes = None
            try:
                size_bytes = stats['_all']['total']['store']['size_in_bytes']
            except Exception:
                try:
                    size_bytes = list(stats.get('indices', {}).values())[0]['total']['store']['size_in_bytes']
                except Exception:
                    size_bytes = None
            return {
                "doc_count": count.get('count', 0),
                "size_bytes": size_bytes,
                "status": "healthy"
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"error": str(e)}
    
    def health_check(self) -> bool:
        """Check if ElasticSearch is healthy and index exists."""
        if not self.es:
            return False
        
        try:
            return self.es.indices.exists(index=INDEX_NAME)
        except:
            return False


# Singleton instance
_es_client = None


def get_elasticsearch_client() -> ElasticSearchClient:
    """Get or create ElasticSearch client singleton."""
    global _es_client
    if _es_client is None:
        _es_client = ElasticSearchClient()
    return _es_client
