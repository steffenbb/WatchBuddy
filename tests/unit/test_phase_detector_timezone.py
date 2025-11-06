from datetime import datetime, timezone

from app.services.phase_detector import PhaseDetector


def test_generate_explanation_handles_naive_and_aware_datetimes():
    pd = PhaseDetector(user_id=1)
    # Mix of naive UTC, aware UTC, and None
    cluster_watches = [
        {"watched_at": datetime(2024, 1, 1), "poster_path": "/p1.jpg", "tmdb_id": 1},  # naive (assumed UTC)
        {"watched_at": datetime(2024, 1, 5, tzinfo=timezone.utc), "poster_path": "/p2.jpg", "tmdb_id": 2},  # aware UTC
        {"watched_at": datetime(2024, 1, 3), "poster_path": "/p3.jpg", "tmdb_id": 3},  # naive
    ]
    metrics = {
        "item_count": len(cluster_watches),
        "dominant_genres": ["thriller", "drama"],
        "dominant_keywords": ["spy", "heist", "conspiracy"],
    }

    # Should not raise
    explanation = pd._generate_explanation(cluster_watches, metrics, label="Test Phase")
    assert isinstance(explanation, str) and len(explanation) > 0


def test_select_representative_posters_handles_missing_and_mixed_times():
    pd = PhaseDetector(user_id=1)
    cluster_watches = [
        {"watched_at": None, "poster_path": "/p0.jpg", "tmdb_id": 0},
        {"watched_at": datetime(2024, 1, 1), "poster_path": "/p1.jpg", "tmdb_id": 1},
        {"watched_at": datetime(2024, 1, 5, tzinfo=timezone.utc), "poster_path": "/p2.jpg", "tmdb_id": 2},
    ]

    posters = pd._select_representative_posters(cluster_watches, count=2)
    assert posters and all(isinstance(p, str) for p in posters)
