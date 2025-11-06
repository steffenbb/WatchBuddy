"""
One-off helper to force a full Trakt watch history sync and immediate phase detection.
Usage (inside container):
  cd /app && PYTHONPATH=/app python app/scripts/compute_phases_now.py
"""
import asyncio
import json
from datetime import datetime


def main(user_id: int = 1, full_sync: bool = True):
    from app.services.watch_history_sync import sync_user_watch_history
    from app.services.phase_detector import PhaseDetector

    # Full sync of watch history
    stats = asyncio.run(sync_user_watch_history(user_id, full_sync=full_sync))
    print(f"[ComputePhasesNow] Watch history sync stats: {stats}")

    # Compute phases
    detector = PhaseDetector(user_id)
    phases = detector.detect_all_phases()
    print(f"[ComputePhasesNow] Detected {len(phases)} phases at {datetime.utcnow().isoformat()}Z")
    if phases:
        latest = phases[0]
        # latest may be newest; print basic info
        try:
            label = getattr(latest, 'label', None)
            start_at = getattr(latest, 'start_at', None)
            end_at = getattr(latest, 'end_at', None)
            print(f"[ComputePhasesNow] Latest phase: {label} ({start_at} -> {end_at})")
        except Exception:
            pass


if __name__ == "__main__":
    main()
