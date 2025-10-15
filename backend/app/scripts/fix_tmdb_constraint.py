"""
Fix the UNIQUE constraint on persistent_candidates to allow same TMDB ID for different media types.

TMDB uses separate ID spaces for movies and TV shows, but some IDs overlap.
The current UNIQUE constraint on tmdb_id alone prevents importing items with duplicate IDs.

Solution: Change to composite UNIQUE constraint on (tmdb_id, media_type).
"""
from app.core.database import engine
from sqlalchemy import text

def fix_tmdb_unique_constraint():
    """Drop the single-column UNIQUE constraint and add composite constraint."""
    with engine.begin() as conn:
        # Drop existing UNIQUE constraints on tmdb_id
        try:
            conn.execute(text("ALTER TABLE persistent_candidates DROP CONSTRAINT IF EXISTS uq_persistent_candidates_tmdb"))
            print("✓ Dropped constraint: uq_persistent_candidates_tmdb")
        except Exception as e:
            print(f"  Warning dropping uq_persistent_candidates_tmdb: {e}")
        
        try:
            conn.execute(text("DROP INDEX IF EXISTS ix_persistent_candidates_tmdb_id"))
            print("✓ Dropped index: ix_persistent_candidates_tmdb_id")
        except Exception as e:
            print(f"  Warning dropping ix_persistent_candidates_tmdb_id: {e}")
        
        # Create composite UNIQUE constraint on (tmdb_id, media_type)
        try:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_persistent_candidates_tmdb_media 
                ON persistent_candidates (tmdb_id, media_type)
            """))
            print("✓ Created composite UNIQUE constraint: uq_persistent_candidates_tmdb_media")
        except Exception as e:
            print(f"  Error creating composite constraint: {e}")
        
        # Re-create non-unique index for tmdb_id lookups
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_persistent_candidates_tmdb 
                ON persistent_candidates (tmdb_id)
            """))
            print("✓ Created non-unique index: idx_persistent_candidates_tmdb")
        except Exception as e:
            print(f"  Error creating tmdb_id index: {e}")

if __name__ == "__main__":
    print("Fixing TMDB ID unique constraint...")
    fix_tmdb_unique_constraint()
    print("\nDone! Now you can re-import the CSV data.")
    print("\nTo clear and re-import:")
    print("  1. TRUNCATE TABLE persistent_candidates;")
    print("  2. Restart backend container to trigger bootstrap")
