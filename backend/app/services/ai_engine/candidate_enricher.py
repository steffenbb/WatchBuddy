"""
On-demand candidate enrichment for stale/incomplete metadata.

When candidates from FAISS/BGE have missing metadata (keywords, cast, overview),
this module fetches fresh data from TMDB, updates the database, regenerates embeddings,
and returns enriched candidate dicts for scoring.
"""
import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models import PersistentCandidate, BGEEmbedding
from app.services.tmdb_client import fetch_tmdb_metadata, extract_enriched_fields
from app.core.database import utc_now

logger = logging.getLogger(__name__)


# ============================================================================
# PUBLIC API - Use these functions from external code
# ============================================================================

def enrich_candidates_sync(
    candidates: List[Dict[str, Any]],
    max_age_days: int = 90,
    max_concurrent: int = 10
) -> List[Dict[str, Any]]:
    """
    Synchronous wrapper for batch candidate enrichment.
    
    Safe to call from sync contexts (like scorer.py).
    Creates its own database session and runs async enrichment in new event loop.
    
    Args:
        candidates: List of candidate dicts
        max_age_days: Maximum age before refresh (default 90 days)
        max_concurrent: Maximum concurrent TMDB requests
    
    Returns:
        List of enriched candidate dicts (same order as input)
    """
    import asyncio
    from app.core.database import SessionLocal
    
    # Check if we're already in an event loop
    try:
        loop = asyncio.get_running_loop()
        # Already in event loop - cannot use asyncio.run()
        logger.info(f"[Enricher] Skipping sync enrichment - already in async context with {len(candidates)} candidates (use enrich_candidates_async instead)")
        return candidates
    except RuntimeError:
        # No event loop running - safe to create one
        pass
    
    db = SessionLocal()
    try:
        result = asyncio.run(_enrich_candidates_async(db, candidates, max_age_days, max_concurrent))
        return result
    finally:
        db.close()


async def enrich_candidates_async(
    db: Session,
    candidates: List[Dict[str, Any]],
    max_age_days: int = 90,
    max_concurrent: int = 10
) -> List[Dict[str, Any]]:
    """
    Async wrapper for batch candidate enrichment.
    
    Safe to call from async contexts. Uses provided database session.
    
    Args:
        db: Database session (must be from same thread/context)
        candidates: List of candidate dicts
        max_age_days: Maximum age before refresh (default 90 days)
        max_concurrent: Maximum concurrent TMDB requests
    
    Returns:
        List of enriched candidate dicts (same order as input)
    """
    return await _enrich_candidates_async(db, candidates, max_age_days, max_concurrent)


async def enrich_single_candidate(
    db: Session,
    candidate: PersistentCandidate,
    tmdb_id: int,
    media_type: str
) -> None:
    """
    Enrich a single PersistentCandidate model with fresh TMDB metadata.
    
    Designed for overview_service.py compatibility - updates the model in-place.
    Does NOT commit - caller is responsible for committing.
    
    Args:
        db: Database session
        candidate: PersistentCandidate model instance to update
        tmdb_id: TMDB ID for metadata fetch
        media_type: 'movie' or 'show'
    """
    try:
        # Fetch fresh metadata from TMDB
        tmdb_media_type = 'tv' if media_type == 'show' else 'movie'
        metadata = await fetch_tmdb_metadata(tmdb_id, tmdb_media_type)
        
        if not metadata:
            logger.debug(f"[Enricher] No TMDB metadata for {media_type}/{tmdb_id}")
            return
        
        # Extract enriched fields
        enriched = extract_enriched_fields(metadata, media_type)
        
        # Update all enriched fields
        candidate.title = metadata.get('title') or metadata.get('name') or candidate.title
        candidate.overview = metadata.get('overview') or candidate.overview
        candidate.poster_path = metadata.get('poster_path') or candidate.poster_path
        candidate.backdrop_path = metadata.get('backdrop_path') or candidate.backdrop_path
        candidate.vote_average = metadata.get('vote_average') or candidate.vote_average
        candidate.vote_count = metadata.get('vote_count') or candidate.vote_count
        candidate.popularity = metadata.get('popularity') or candidate.popularity
        candidate.status = enriched.get('status') or candidate.status
        candidate.tagline = enriched.get('tagline') or candidate.tagline
        candidate.homepage = enriched.get('homepage') or candidate.homepage
        candidate.runtime = enriched.get('runtime') or candidate.runtime
        
        # Update JSON fields
        candidate.keywords = enriched.get('keywords') or candidate.keywords
        candidate.cast = enriched.get('cast') or candidate.cast
        candidate.genres = enriched.get('genres') or candidate.genres
        candidate.production_companies = enriched.get('production_companies') or candidate.production_companies
        candidate.production_countries = enriched.get('production_countries') or candidate.production_countries
        candidate.spoken_languages = enriched.get('spoken_languages') or candidate.spoken_languages
        
        # TV-specific fields
        if media_type == 'show':
            candidate.networks = enriched.get('networks') or candidate.networks
            candidate.created_by = enriched.get('created_by') or candidate.created_by
            candidate.number_of_seasons = enriched.get('number_of_seasons') or candidate.number_of_seasons
            candidate.number_of_episodes = enriched.get('number_of_episodes') or candidate.number_of_episodes
            candidate.episode_run_time = enriched.get('episode_run_time') or candidate.episode_run_time
            candidate.first_air_date = enriched.get('first_air_date') or candidate.first_air_date
            candidate.last_air_date = enriched.get('last_air_date') or candidate.last_air_date
            candidate.in_production = enriched.get('in_production') or candidate.in_production
        
        # Recompute scores
        scores = _compute_scores(metadata)
        candidate.obscurity_score = scores['obscurity_score']
        candidate.mainstream_score = scores['mainstream_score']
        candidate.freshness_score = scores['freshness_score']
        
        candidate.last_refreshed = utc_now()
        
        db.add(candidate)
        
        # Regenerate BGE embedding
        await _regenerate_bge_embedding(db, candidate)
        
        logger.debug(f"[Enricher] Enriched: {candidate.title} ({media_type}/{tmdb_id})")
        
    except Exception as e:
        logger.error(f"[Enricher] Failed to enrich {media_type}/{tmdb_id}: {e}", exc_info=True)


# ============================================================================
# INTERNAL IMPLEMENTATION - Do not call directly
# ============================================================================

def _needs_enrichment(candidate: Dict[str, Any], max_age_days: int = 90) -> bool:
    """
    Determine if a candidate needs metadata refresh.
    
    Criteria:
    - Missing critical fields (overview, keywords, cast)
    - last_refreshed older than max_age_days
    - released field indicates unreleased/upcoming content that may now be released
    
    Args:
        candidate: Candidate dict from database
        max_age_days: Maximum age before refresh (default 90 days)
    
    Returns:
        True if candidate should be enriched
    """
    # Check for missing critical fields
    missing_overview = not candidate.get('overview') or candidate.get('overview', '').strip() == ''
    missing_keywords = not candidate.get('keywords') or candidate.get('keywords') == '[]' or candidate.get('keywords') == 'null'
    missing_cast = not candidate.get('cast') or candidate.get('cast') == '[]' or candidate.get('cast') == 'null'
    
    if missing_overview or missing_keywords or missing_cast:
        return True
    
    # Check last_refreshed timestamp
    last_refreshed = candidate.get('last_refreshed')
    if last_refreshed:
        if isinstance(last_refreshed, str):
            try:
                last_refreshed = datetime.fromisoformat(last_refreshed.replace('Z', '+00:00'))
            except Exception:
                last_refreshed = None
        
        if last_refreshed and isinstance(last_refreshed, datetime):
            age = datetime.utcnow() - last_refreshed.replace(tzinfo=None)
            if age > timedelta(days=max_age_days):
                return True
    
    # Check if content was previously unreleased but may now be available
    status = candidate.get('status', '').lower()
    if status in ('announced', 'planned', 'in production', 'post production'):
        # Could be released now - worth refreshing
        return True
    
    return False


def _compute_scores(metadata: Dict[str, Any]) -> Dict[str, float]:
    """Compute obscurity, mainstream, and freshness scores from TMDB metadata."""
    popularity = metadata.get('popularity', 0)
    vote_count = metadata.get('vote_count', 0)
    
    # Obscurity: lower popularity/votes = higher obscurity
    pop_score = max(0, 1 - (popularity / 1000))
    vote_score = max(0, 1 - (vote_count / 10000))
    obscurity_score = (pop_score + vote_score) / 2
    
    # Mainstream: opposite of obscurity
    mainstream_score = 1 - obscurity_score
    
    # Freshness: based on release date
    freshness_score = 0.5  # Default neutral
    release_date_str = metadata.get('release_date') or metadata.get('first_air_date', '')
    if release_date_str:
        try:
            release_date = datetime.strptime(release_date_str[:10], '%Y-%m-%d')
            age_days = (datetime.utcnow() - release_date).days
            # Decay over 2 years: 1.0 at release, 0.0 at 730 days
            freshness_score = max(0, min(1, 1 - (age_days / 730)))
        except Exception:
            pass
    
    return {
        'obscurity_score': obscurity_score,
        'mainstream_score': mainstream_score,
        'freshness_score': freshness_score
    }


async def _enrich_candidates_async(
    db: Session,
    candidates: List[Dict[str, Any]],
    max_age_days: int = 90,
    max_concurrent: int = 10
) -> List[Dict[str, Any]]:
    """
    Internal async implementation of candidate enrichment.
    
    Process:
    1. Identify candidates needing refresh
    2. Fetch fresh metadata from TMDB
    3. Update PersistentCandidate in database
    4. Regenerate BGE embeddings
    5. Return updated candidate dicts
    
    Args:
        db: Database session
        candidates: List of candidate dicts
        max_age_days: Maximum age before refresh (default 90 days)
        max_concurrent: Maximum concurrent TMDB requests
    
    Returns:
        List of enriched candidate dicts (same order as input)
    """
    import asyncio
    
    enrichment_tasks = []
    needs_enrichment_idx = []
    
    logger.debug(f"[Enricher] Checking {len(candidates)} candidates for enrichment needs (max_age={max_age_days} days)")
    
    # Identify candidates needing enrichment
    for idx, candidate in enumerate(candidates):
        if _needs_enrichment(candidate, max_age_days):
            needs_enrichment_idx.append(idx)
            enrichment_tasks.append({
                'idx': idx,
                'tmdb_id': candidate.get('tmdb_id'),
                'media_type': candidate.get('media_type'),
                'id': candidate.get('id')  # PersistentCandidate.id for DB update
            })
    
    if not enrichment_tasks:
        logger.debug(f"[Enricher] No candidates need enrichment (checked {len(candidates)} candidates)")
        return candidates
    
    logger.info(f"[Enricher] Enriching {len(enrichment_tasks)}/{len(candidates)} candidates with stale/missing metadata")
    
    # Semaphore to limit concurrent TMDB requests
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def enrich_one(task):
        async with semaphore:
            idx = task['idx']
            tmdb_id = task['tmdb_id']
            media_type = task['media_type']
            pc_id = task['id']
            
            if not tmdb_id or not media_type:
                return None
            
            try:
                # Fetch fresh metadata from TMDB
                tmdb_media_type = 'tv' if media_type == 'show' else 'movie'
                metadata = await fetch_tmdb_metadata(tmdb_id, tmdb_media_type)
                
                if not metadata:
                    logger.debug(f"[Enricher] No TMDB metadata for {media_type}/{tmdb_id}")
                    return None
                
                # Extract enriched fields
                enriched = extract_enriched_fields(metadata, media_type)
                
                # Update PersistentCandidate in database
                pc = db.query(PersistentCandidate).filter(PersistentCandidate.id == pc_id).first()
                if not pc:
                    logger.warning(f"[Enricher] PersistentCandidate {pc_id} not found")
                    return None
                
                # Update all enriched fields
                pc.title = metadata.get('title') or metadata.get('name') or pc.title
                pc.overview = metadata.get('overview') or pc.overview
                pc.poster_path = metadata.get('poster_path') or pc.poster_path
                pc.backdrop_path = metadata.get('backdrop_path') or pc.backdrop_path
                pc.vote_average = metadata.get('vote_average') or pc.vote_average
                pc.vote_count = metadata.get('vote_count') or pc.vote_count
                pc.popularity = metadata.get('popularity') or pc.popularity
                pc.status = enriched.get('status') or pc.status
                pc.tagline = enriched.get('tagline') or pc.tagline
                pc.homepage = enriched.get('homepage') or pc.homepage
                pc.runtime = enriched.get('runtime') or pc.runtime
                
                # Update JSON fields
                pc.keywords = enriched.get('keywords') or pc.keywords
                pc.cast = enriched.get('cast') or pc.cast
                pc.genres = enriched.get('genres') or pc.genres
                pc.production_companies = enriched.get('production_companies') or pc.production_companies
                pc.production_countries = enriched.get('production_countries') or pc.production_countries
                pc.spoken_languages = enriched.get('spoken_languages') or pc.spoken_languages
                
                # TV-specific fields
                if media_type == 'show':
                    pc.networks = enriched.get('networks') or pc.networks
                    pc.created_by = enriched.get('created_by') or pc.created_by
                    pc.number_of_seasons = enriched.get('number_of_seasons') or pc.number_of_seasons
                    pc.number_of_episodes = enriched.get('number_of_episodes') or pc.number_of_episodes
                    pc.episode_run_time = enriched.get('episode_run_time') or pc.episode_run_time
                    pc.first_air_date = enriched.get('first_air_date') or pc.first_air_date
                    pc.last_air_date = enriched.get('last_air_date') or pc.last_air_date
                    pc.in_production = enriched.get('in_production') or pc.in_production
                
                # Recompute scores
                scores = _compute_scores(metadata)
                pc.obscurity_score = scores['obscurity_score']
                pc.mainstream_score = scores['mainstream_score']
                pc.freshness_score = scores['freshness_score']
                
                pc.last_refreshed = utc_now()
                
                db.add(pc)
                
                # Regenerate BGE embedding
                await _regenerate_bge_embedding(db, pc)
                
                # Build updated candidate dict
                updated_candidate = {
                    **candidates[idx],  # Keep original fields
                    'title': pc.title,
                    'overview': pc.overview,
                    'keywords': pc.keywords,
                    'cast': pc.cast,
                    'genres': pc.genres,
                    'tagline': pc.tagline,
                    'poster_path': pc.poster_path,
                    'backdrop_path': pc.backdrop_path,
                    'vote_average': pc.vote_average,
                    'vote_count': pc.vote_count,
                    'popularity': pc.popularity,
                    'status': pc.status,
                    'obscurity_score': pc.obscurity_score,
                    'mainstream_score': pc.mainstream_score,
                    'freshness_score': pc.freshness_score,
                    'production_companies': pc.production_companies,
                    'last_refreshed': pc.last_refreshed
                }
                
                logger.debug(f"[Enricher] Enriched: {pc.title} ({media_type}/{tmdb_id})")
                return {'idx': idx, 'candidate': updated_candidate}
                
            except Exception as e:
                logger.error(f"[Enricher] Failed to enrich {media_type}/{tmdb_id}: {e}", exc_info=True)
                return None
    
    # Run enrichment tasks concurrently
    results = await asyncio.gather(*[enrich_one(task) for task in enrichment_tasks], return_exceptions=True)
    
    # Update candidates list with enriched data
    enriched_count = 0
    for result in results:
        if result and isinstance(result, dict) and 'idx' in result:
            candidates[result['idx']] = result['candidate']
            enriched_count += 1
    
    # Commit all database updates
    try:
        db.commit()
        logger.info(f"[Enricher] Successfully enriched {enriched_count}/{len(enrichment_tasks)} candidates (total pool: {len(candidates)})")
    except Exception as e:
        db.rollback()
        logger.error(f"[Enricher] Failed to commit enrichment updates: {e}", exc_info=True)
    
    return candidates


async def _regenerate_bge_embedding(db: Session, pc: PersistentCandidate) -> None:
    """
    Regenerate BGE multi-vector embeddings for enriched candidate.
    
    Creates/updates BGEEmbedding record with fresh embeddings for:
    - embedding_base (title + overview + genres + keywords)
    - embedding_title (title only)
    - embedding_keywords (keywords + tagline)
    - embedding_people (cast + created_by)
    - embedding_brands (production_companies + networks)
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        
        # Load BGE model
        model = SentenceTransformer('BAAI/bge-small-en-v1.5')
        
        # Build text components
        title = pc.title or ""
        overview = pc.overview or ""
        tagline = pc.tagline or ""
        
        # Parse JSON fields
        try:
            genres = json.loads(pc.genres or "[]")
            genres_text = " ".join(genres)
        except Exception:
            genres_text = ""
        
        try:
            keywords = json.loads(pc.keywords or "[]")
            keywords_text = " ".join(keywords)
        except Exception:
            keywords_text = ""
        
        try:
            cast = json.loads(pc.cast or "[]")
            cast_text = " ".join(cast[:10])  # Top 10 actors
        except Exception:
            cast_text = ""
        
        try:
            companies = json.loads(pc.production_companies or "[]")
            companies_text = " ".join(companies)
        except Exception:
            companies_text = ""
        
        try:
            networks = json.loads(pc.networks or "[]") if pc.networks else []
            networks_text = " ".join(networks)
        except Exception:
            networks_text = ""
        
        try:
            created_by = json.loads(pc.created_by or "[]") if pc.created_by else []
            created_by_text = " ".join(created_by)
        except Exception:
            created_by_text = ""
        
        # Generate embeddings
        base_text = f"{title} {overview} {genres_text} {keywords_text}".strip()
        title_text = title.strip()
        keywords_text_full = f"{keywords_text} {tagline}".strip()
        people_text = f"{cast_text} {created_by_text}".strip()
        brands_text = f"{companies_text} {networks_text}".strip()
        
        # Serialize embeddings (float16 for storage efficiency)
        def serialize_embedding(text: str) -> Optional[bytes]:
            if not text:
                return None
            emb = model.encode(text, normalize_embeddings=True)
            return np.array(emb, dtype=np.float16).tobytes()
        
        embedding_base = serialize_embedding(base_text)
        embedding_title = serialize_embedding(title_text) if title_text else None
        embedding_keywords = serialize_embedding(keywords_text_full) if keywords_text_full else None
        embedding_people = serialize_embedding(people_text) if people_text else None
        embedding_brands = serialize_embedding(brands_text) if brands_text else None
        
        # Update or create BGEEmbedding
        bge_emb = db.query(BGEEmbedding).filter(
            BGEEmbedding.tmdb_id == pc.tmdb_id,
            BGEEmbedding.media_type == pc.media_type
        ).first()
        
        if bge_emb:
            # Update existing
            bge_emb.embedding_base = embedding_base
            bge_emb.embedding_title = embedding_title
            bge_emb.embedding_keywords = embedding_keywords
            bge_emb.embedding_people = embedding_people
            bge_emb.embedding_brands = embedding_brands
            bge_emb.updated_at = utc_now()
        else:
            # Create new
            bge_emb = BGEEmbedding(
                tmdb_id=pc.tmdb_id,
                media_type=pc.media_type,
                embedding_base=embedding_base,
                embedding_title=embedding_title,
                embedding_keywords=embedding_keywords,
                embedding_people=embedding_people,
                embedding_brands=embedding_brands,
                created_at=utc_now(),
                updated_at=utc_now()
            )
        
        db.add(bge_emb)
        logger.debug(f"[Enricher] Regenerated BGE embeddings for {pc.title}")
        
    except Exception as e:
        logger.error(f"[Enricher] Failed to regenerate BGE embedding: {e}", exc_info=True)
