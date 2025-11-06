"""
Repair PostgreSQL sequences after bootstrap import.
This script sets the persistent_candidates.id sequence to MAX(id)
so subsequent inserts don't violate the primary key.

Run inside container with:
  PYTHONPATH=/app python app/scripts/repair_sequences.py
"""
import logging
from sqlalchemy import text
from app.core.database import SessionLocal

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SEQUENCE_FIXES = [
    ("persistent_candidates", "id"),
]

def reset_sequence(table: str, column: str) -> None:
    db = SessionLocal()
    try:
        logger.info(f"Resetting sequence for {table}.{column} -> MAX({column})")
        db.execute(text(
            "SELECT setval("
            "  pg_get_serial_sequence(:table, :column),"
            "  GREATEST((SELECT COALESCE(MAX(id), 1) FROM persistent_candidates), 1),"
            "  true)"
        ), {"table": table, "column": column})
        db.commit()
        logger.info(f"Done resetting sequence for {table}.{column}")
    except Exception as e:
        logger.error(f"Failed to reset sequence for {table}.{column}: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def verify_counts() -> None:
    db = SessionLocal()
    try:
        count = db.execute(text("SELECT COUNT(*) FROM persistent_candidates")).scalar()
        max_id = db.execute(text("SELECT COALESCE(MAX(id),0) FROM persistent_candidates")).scalar()
        logger.info(f"persistent_candidates: count={count:,}, max(id)={max_id}")
        curr = db.execute(text(
            "SELECT last_value FROM pg_sequences WHERE schemaname = 'public' AND sequencename = 'persistent_candidates_id_seq'"
        )).scalar()
        if curr is not None:
            logger.info(f"persistent_candidates_id_seq.last_value={curr}")
    except Exception as e:
        logger.warning(f"Could not verify sequence current value: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    for table, column in SEQUENCE_FIXES:
        reset_sequence(table, column)
    verify_counts()
