#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models_ai import AiList
from app.tasks_ai import generate_chat_list


def main(user_id: int = 1):
    db = SessionLocal()
    try:
        lists = db.query(AiList).filter(AiList.user_id == user_id).all()
        print(f"Enqueuing {len(lists)} AI lists for regeneration...")
        for lst in lists:
            try:
                generate_chat_list.delay(lst.id, user_id)
                print(f"  queued: {lst.id} ({lst.type}) -> {lst.generated_title or (lst.prompt_text or '')[:50]}")
            except Exception as e:
                print(f"  failed to queue {lst.id}: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
