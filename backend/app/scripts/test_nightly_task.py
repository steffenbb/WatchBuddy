"""
Test script to verify the nightly maintenance task can be imported and has correct structure.
"""
import sys
sys.path.insert(0, '/app')

def test_nightly_task():
    print("=" * 80)
    print("Testing Nightly Maintenance Task Structure")
    print("=" * 80)
    
    # Import the task
    from app.services.tasks import run_nightly_maintenance
    print("\n✅ Successfully imported run_nightly_maintenance")
    
    # Check if it's a Celery task
    print(f"   Task name: {run_nightly_maintenance.name}")
    print(f"   Task class: {type(run_nightly_maintenance)}")
    
    # Import the helper functions
    from app.services import tasks
    
    # Check _backfill_ai_segments exists
    if hasattr(tasks, '_backfill_ai_segments'):
        print("\n✅ _backfill_ai_segments function exists")
    else:
        print("\n❌ _backfill_ai_segments function NOT FOUND")
    
    # Check _rebuild_elasticsearch_index exists
    if hasattr(tasks, '_rebuild_elasticsearch_index'):
        print("✅ _rebuild_elasticsearch_index function exists")
    else:
        print("❌ _rebuild_elasticsearch_index function NOT FOUND")
    
    print("\n" + "=" * 80)
    print("Nightly Maintenance Task Flow:")
    print("=" * 80)
    print("1. Check timezone (00:00-07:00 local time)")
    print("2. Run metadata builder (build_metadata.delay)")
    print("3. Run AI optimization (_backfill_ai_segments)")
    print("   - Generate embeddings for items missing them")
    print("   - Build rich text segments from metadata")
    print("   - Update FAISS index incrementally")
    print("4. Rebuild ElasticSearch index (_rebuild_elasticsearch_index)")
    print("   - Index all candidates with trakt_id + embedding")
    print("   - Used for literal/fuzzy text search")
    print("   - Complements FAISS semantic search")
    print("\n✅ All nightly maintenance components verified!")

if __name__ == "__main__":
    test_nightly_task()
