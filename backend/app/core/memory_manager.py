"""
memory_manager.py

Memory optimization utilities for WatchBuddy.
Provides context managers and utilities to manage memory usage,
especially for large query results and batch processing.
"""
import logging
import gc
from contextlib import contextmanager
from typing import Iterator, List, Any, Callable
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@contextmanager
def managed_memory(operation_name: str = "operation"):
    """
    Context manager that ensures garbage collection and logs memory cleanup.
    Use around large operations to ensure proper cleanup.
    
    Usage:
        with managed_memory("phase detection"):
            # heavy operation
            pass
    """
    try:
        yield
    finally:
        # Force garbage collection
        collected = gc.collect()
        logger.debug(f"[MemoryManager] {operation_name}: collected {collected} objects")


def batch_query_iterator(
    session: Session,
    query,
    batch_size: int = 500,
    expunge: bool = True
) -> Iterator[List[Any]]:
    """
    Iterate over query results in batches to avoid loading entire result set into memory.
    Automatically expunges objects from session to free memory if expunge=True.
    
    Args:
        session: SQLAlchemy session
        query: SQLAlchemy query object (not yet executed)
        batch_size: Number of rows per batch
        expunge: If True, expunge objects from session after yielding to free memory
    
    Yields:
        Lists of batch_size rows
        
    Example:
        query = db.query(PersistentCandidate).filter(...)
        for batch in batch_query_iterator(db, query, batch_size=1000):
            process_batch(batch)
    """
    offset = 0
    while True:
        batch = query.limit(batch_size).offset(offset).all()
        if not batch:
            break
            
        yield batch
        
        if expunge:
            # Expunge all objects from session to free memory
            for obj in batch:
                session.expunge(obj)
        
        offset += batch_size
        
        # Hint to garbage collector
        if offset % (batch_size * 10) == 0:
            gc.collect(generation=0)  # Quick generation 0 collection


def chunked_list_processor(
    items: List[Any],
    chunk_size: int,
    processor: Callable[[List[Any]], None],
    auto_gc: bool = True
) -> None:
    """
    Process a list in chunks with automatic garbage collection.
    Useful for processing large in-memory lists.
    
    Args:
        items: List to process
        chunk_size: Size of each chunk
        processor: Function that takes a chunk and processes it
        auto_gc: If True, run gc.collect() after each chunk
        
    Example:
        def process_chunk(chunk):
            for item in chunk:
                # process item
                pass
                
        chunked_list_processor(large_list, 500, process_chunk)
    """
    total = len(items)
    for i in range(0, total, chunk_size):
        chunk = items[i:i + chunk_size]
        processor(chunk)
        
        # Clear chunk reference
        del chunk
        
        if auto_gc and (i + chunk_size) % (chunk_size * 5) == 0:
            gc.collect(generation=0)
    
    # Final cleanup
    if auto_gc:
        gc.collect()


def optimize_query_result(rows: List[Any], keep_fields: List[str] = None) -> List[dict]:
    """
    Convert SQLAlchemy row objects to lightweight dicts with only needed fields.
    Reduces memory by dropping SQLAlchemy tracking overhead.
    
    Args:
        rows: List of SQLAlchemy row objects
        keep_fields: List of field names to keep (if None, keeps all)
        
    Returns:
        List of dicts with minimal data
        
    Example:
        rows = db.query(PersistentCandidate).filter(...).all()
        lightweight = optimize_query_result(rows, keep_fields=['tmdb_id', 'title', 'year'])
    """
    if not rows:
        return []
    
    result = []
    for row in rows:
        if keep_fields:
            item = {field: getattr(row, field, None) for field in keep_fields}
        else:
            # Use row's dict representation
            item = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        result.append(item)
    
    # Clear original rows
    rows.clear()
    gc.collect(generation=0)
    
    return result


class SessionMemoryGuard:
    """
    Context manager that ensures session cleanup and memory release.
    
    Usage:
        with SessionMemoryGuard() as db:
            results = db.query(...).all()
            # process results
        # Session is automatically closed and memory freed
    """
    
    def __init__(self):
        self.db = None
    
    def __enter__(self) -> Session:
        from app.core.database import SessionLocal
        self.db = SessionLocal()
        return self.db
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            try:
                # Expunge all objects from session
                self.db.expunge_all()
                self.db.close()
            except Exception as e:
                logger.error(f"[SessionMemoryGuard] Error closing session: {e}")
            finally:
                self.db = None
                gc.collect()
        return False


def reduce_query_footprint(query, batch_size: int = 500):
    """
    Decorator to automatically batch large queries.
    Converts query.all() calls into batched iteration.
    
    This is a yield-based approach - use with for loops.
    
    Example:
        @reduce_query_footprint(batch_size=1000)
        def get_candidates(db):
            return db.query(PersistentCandidate).filter(...)
            
        for batch in get_candidates(db):
            process_batch(batch)
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            query_obj = func(*args, **kwargs)
            # Extract session from query
            session = query_obj.session
            offset = 0
            while True:
                batch = query_obj.limit(batch_size).offset(offset).all()
                if not batch:
                    break
                yield batch
                # Expunge to free memory
                for obj in batch:
                    session.expunge(obj)
                offset += batch_size
                if offset % (batch_size * 10) == 0:
                    gc.collect(generation=0)
        return wrapper
    return decorator


# Memory optimization constants
OPTIMAL_BATCH_SIZES = {
    'persistent_candidates': 1000,  # Large table, use bigger batches
    'watch_history': 500,           # Medium table
    'list_items': 1000,             # Can be large
    'embeddings': 100,              # Heavy objects, use small batches
    'metadata': 500,                # Medium weight
    'default': 500                  # Safe default
}


def get_optimal_batch_size(table_name: str) -> int:
    """
    Get recommended batch size for a specific table.
    
    Args:
        table_name: Name of the table (snake_case)
        
    Returns:
        Recommended batch size
    """
    return OPTIMAL_BATCH_SIZES.get(table_name, OPTIMAL_BATCH_SIZES['default'])
