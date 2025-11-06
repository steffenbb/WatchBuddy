"""
Check database statistics and column coverage.

Includes:
- Persistent Candidates summary (existing metrics)
- Column coverage for key tables: persistent_candidates and media_metadata
    • For each column: how many rows have a value (non-null; for text, also non-empty)
    • For booleans: count TRUE values
"""
import sys
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from sqlalchemy import text


def _print_column_coverage(db, table_name: str):
    """Print per-column coverage for a table.

    Coverage rules:
    - All columns: report NOT NULL count and percentage
    - Text columns: also report NON-EMPTY (not '', not '[]') count and percentage
    - Boolean columns: also report TRUE count and percentage
    """
    # Fetch columns and data types
    cols = db.execute(text(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t
        ORDER BY ordinal_position
        """
    ), {"t": table_name}).fetchall()

    total = db.execute(text(f'SELECT COUNT(*) FROM {table_name}')).scalar() or 0
    if total == 0:
        print(f"No rows in {table_name}.")
        return

    # Pretty header
    print(f"Table: {table_name} (rows: {total:,})")
    print("-" * 80)
    print(f"{'Column':30} {'Type':14} {'Not Null':>10} {'%':>6}  {'Non-Empty':>10} {'%':>6}  {'TRUE':>8} {'%':>6}")
    print("-" * 80)

    # For each column compute coverage
    for col_name, data_type in cols:
        col_quoted = f'"{col_name}"'
        # Build base not-null query
        q_parts = [
            f"COUNT(*) FILTER (WHERE {col_quoted} IS NOT NULL) AS not_null"
        ]

        # Text-like fields: count non-empty (not '' and not '[]')
        text_like = data_type in ("text", "character varying", "character")
        if text_like:
            q_parts.append(
                f"COUNT(*) FILTER (WHERE {col_quoted} IS NOT NULL AND {col_quoted} <> '' AND {col_quoted} <> '[]') AS non_empty"
            )
        else:
            q_parts.append("0 AS non_empty")

        # Boolean fields: count TRUE
        if data_type == 'boolean':
            q_parts.append(f"COUNT(*) FILTER (WHERE {col_quoted} = TRUE) AS true_count")
        else:
            q_parts.append("0 AS true_count")

        query = f"SELECT {', '.join(q_parts)} FROM {table_name}"
        row = db.execute(text(query)).fetchone()
        not_null = int(row[0] or 0)
        non_empty = int(row[1] or 0)
        true_count = int(row[2] or 0)

        # Percentages
        pct_not_null = (not_null / total * 100.0) if total else 0.0
        pct_non_empty = (non_empty / total * 100.0) if total else 0.0
        pct_true = (true_count / total * 100.0) if total else 0.0

        print(f"{col_name:30} {data_type:14} {not_null:10,d} {pct_not_null:6.1f}  {non_empty:10,d} {pct_non_empty:6.1f}  {true_count:8,d} {pct_true:6.1f}")
    print("-" * 80)

def main():
    db = SessionLocal()
    try:
        print("=" * 80)
        print("PERSISTENT CANDIDATES DATABASE STATISTICS")
        print("=" * 80)
        
        # Total items
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates"
        )).scalar()
        print(f"\n📊 Total items: {total:,}")
        
        # Active items
        active = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active = true"
        )).scalar()
        print(f"   Active items: {active:,} ({active/total*100:.1f}%)")
        
        # Items with trakt_id
        with_trakt = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE trakt_id IS NOT NULL"
        )).scalar()
        without_trakt = total - with_trakt
        print(f"\n🔗 Trakt ID Coverage:")
        print(f"   With trakt_id: {with_trakt:,} ({with_trakt/total*100:.1f}%)")
        print(f"   Without trakt_id: {without_trakt:,} ({without_trakt/total*100:.1f}%)")
        
        # Active items with trakt_id
        active_with_trakt = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active = true AND trakt_id IS NOT NULL"
        )).scalar()
        print(f"   Active with trakt_id: {active_with_trakt:,} ({active_with_trakt/active*100:.1f}% of active)")
        
        # Items with embeddings
        with_embedding = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE embedding IS NOT NULL"
        )).scalar()
        without_embedding = total - with_embedding
        print(f"\n🧠 Embedding Coverage:")
        print(f"   With embedding: {with_embedding:,} ({with_embedding/total*100:.1f}%)")
        print(f"   Without embedding: {without_embedding:,} ({without_embedding/total*100:.1f}%)")
        
        # Active items with embeddings
        active_with_embedding = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active = true AND embedding IS NOT NULL"
        )).scalar()
        print(f"   Active with embedding: {active_with_embedding:,} ({active_with_embedding/active*100:.1f}% of active)")
        
        # Items with cast
        with_cast = db.execute(text(
            'SELECT COUNT(*) FROM persistent_candidates WHERE "cast" IS NOT NULL AND "cast" != \'[]\' AND "cast" != \'\''
        )).scalar()
        without_cast = total - with_cast
        print(f"\n🎭 Cast Data Coverage:")
        print(f"   With cast data: {with_cast:,} ({with_cast/total*100:.1f}%)")
        print(f"   Without cast data: {without_cast:,} ({without_cast/total*100:.1f}%)")
        
        # Active items with cast
        active_with_cast = db.execute(text(
            'SELECT COUNT(*) FROM persistent_candidates WHERE active = true AND "cast" IS NOT NULL AND "cast" != \'[]\' AND "cast" != \'\''
        )).scalar()
        print(f"   Active with cast: {active_with_cast:,} ({active_with_cast/active*100:.1f}% of active)")
        
        # Items with keywords
        with_keywords = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE keywords IS NOT NULL AND keywords != '[]' AND keywords != ''"
        )).scalar()
        print(f"\n🏷️  Keywords Coverage:")
        print(f"   With keywords: {with_keywords:,} ({with_keywords/total*100:.1f}%)")
        
        # Triple coverage: trakt_id + embedding + cast
        triple_coverage = db.execute(text(
            """SELECT COUNT(*) FROM persistent_candidates 
               WHERE trakt_id IS NOT NULL 
               AND embedding IS NOT NULL 
               AND "cast" IS NOT NULL AND "cast" != '[]' AND "cast" != ''"""
        )).scalar()
        print(f"\n✨ Complete Coverage (trakt_id + embedding + cast):")
        print(f"   {triple_coverage:,} items ({triple_coverage/total*100:.1f}%)")
        
        # Active items with complete coverage
        active_triple = db.execute(text(
            """SELECT COUNT(*) FROM persistent_candidates 
               WHERE active = true
               AND trakt_id IS NOT NULL 
               AND embedding IS NOT NULL 
               AND "cast" IS NOT NULL AND "cast" != '[]' AND "cast" != ''"""
        )).scalar()
        print(f"   Active complete: {active_triple:,} ({active_triple/active*100:.1f}% of active)")
        
        # Media type breakdown
        print(f"\n📺 Media Type Breakdown:")
        media_stats = db.execute(text(
            """SELECT media_type, COUNT(*) as count,
               COUNT(*) FILTER (WHERE trakt_id IS NOT NULL) as with_trakt,
               COUNT(*) FILTER (WHERE embedding IS NOT NULL) as with_embedding,
               COUNT(*) FILTER (WHERE "cast" IS NOT NULL AND "cast" != '[]') as with_cast
               FROM persistent_candidates
               GROUP BY media_type
               ORDER BY count DESC"""
        )).fetchall()
        
        for media_type, count, with_trakt, with_embedding, with_cast in media_stats:
            print(f"\n   {media_type.upper()}: {count:,} items")
            print(f"      Trakt ID: {with_trakt:,} ({with_trakt/count*100:.1f}%)")
            print(f"      Embedding: {with_embedding:,} ({with_embedding/count*100:.1f}%)")
            print(f"      Cast: {with_cast:,} ({with_cast/count*100:.1f}%)")
        
        # Items ready for search (trakt_id + embedding for hybrid search)
        search_ready = db.execute(text(
            """SELECT COUNT(*) FROM persistent_candidates 
               WHERE active = true
               AND trakt_id IS NOT NULL 
               AND embedding IS NOT NULL"""
        )).scalar()
        print(f"\n🔍 Search-Ready Items (active + trakt_id + embedding):")
        print(f"   {search_ready:,} items ({search_ready/active*100:.1f}% of active)")
        print(f"   These items are indexed in both FAISS and ElasticSearch")
        
        print("\n" + "=" * 80)
        print("COLUMN COVERAGE: persistent_candidates")
        print("=" * 80)
        _print_column_coverage(db, 'persistent_candidates')

        print("\n" + "=" * 80)
        print("COLUMN COVERAGE: media_metadata")
        print("=" * 80)
        _print_column_coverage(db, 'media_metadata')
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
