#!/usr/bin/env python3
"""
Rebuild BGE FAISS index from database embeddings.
Run with: PYTHONPATH=/app python app/scripts/rebuild_bge_faiss.py
"""
import sys
import logging
from app.services.bge_recovery import rebuild_bge_index_from_db

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🔨 BGE FAISS INDEX REBUILD")
    logger.info("=" * 60)
    
    try:
        result = rebuild_bge_index_from_db()
        
        logger.info("\n" + "=" * 60)
        logger.info("📊 REBUILD RESULTS")
        logger.info("=" * 60)
        logger.info(f"Status: {result.get('status')}")
        logger.info(f"Total items in DB: {result.get('total_items', 0)}")
        logger.info(f"Base vectors added: {result.get('base_vectors', 0)}")
        logger.info(f"Labeled vectors added: {result.get('labeled_vectors', 0)}")
        logger.info(f"Total vectors: {result.get('total_vectors', 0)}")
        logger.info(f"Missing candidates: {result.get('missing_candidates', 0)}")
        logger.info(f"Errors: {result.get('errors', 0)}")
        
        if result.get('status') == 'success':
            logger.info("\n✅ BGE index rebuilt successfully!")
            sys.exit(0)
        else:
            logger.error(f"\n❌ Rebuild failed: {result.get('reason', 'unknown')}")
            if 'error' in result:
                logger.error(f"Error: {result['error']}")
            sys.exit(1)
    
    except Exception as e:
        logger.error(f"\n❌ CRITICAL ERROR: {e}", exc_info=True)
        sys.exit(1)
