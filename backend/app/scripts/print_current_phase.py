"""
print_current_phase.py

Print the current phase object as seen by PhaseDetector without HTTP.
"""
import json
from app.services.phase_detector import PhaseDetector


def main(user_id: int = 1):
    detector = PhaseDetector(user_id)
    phase = detector.get_current_phase()
    if not phase:
        print("Current phase: None")
        return
    # Convert to dict similar to API serializer
    data = {
        "id": phase.id,
        "label": phase.label,
        "start_at": phase.start_at.isoformat() if phase.start_at else None,
        "end_at": phase.end_at.isoformat() if phase.end_at else None,
        "item_count": phase.item_count,
        "movie_count": phase.movie_count,
        "show_count": phase.show_count,
        "phase_type": phase.phase_type,
        "phase_score": phase.phase_score,
    }
    print(json.dumps(data))


if __name__ == "__main__":
    main()
