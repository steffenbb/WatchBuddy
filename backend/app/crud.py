from .core.database import SessionLocal
from . import models
from sqlalchemy.orm import Session
import json
import logging

logger = logging.getLogger(__name__)

def create_list(payload):
    db: Session = SessionLocal()
    try:
        logger.info(f"Creating list: {payload.title}")
        l = models.UserList(
            user_id=1,  # single-user mode
            title=payload.title,
            filters=json.dumps(payload.filters) if isinstance(payload.filters, (dict, list)) else (payload.filters or "{}"),
            sort_order=payload.sort_order,
            item_limit=payload.item_limit,
            sync_interval=payload.sync_interval,
            list_type=payload.list_type
        )
        db.add(l)
        db.commit()
        db.refresh(l)
        logger.info(f"Successfully created list with ID: {l.id}")
        return l
    except Exception as e:
        logger.error(f"Failed to create list: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def list_all():
    db = SessionLocal()
    try:
        logger.info("Fetching all lists from database")
        lists = db.query(models.UserList).order_by(models.UserList.created_at.desc()).all()
        logger.info(f"Found {len(lists)} lists in database")
        return lists
    except Exception as e:
        logger.error(f"Failed to fetch lists: {e}")
        raise
    finally:
        db.close()

def get_list(list_id: int):
    db = SessionLocal()
    try:
        return db.query(models.UserList).filter(models.UserList.id == list_id).first()
    finally:
        db.close()

def delete_list(list_id: int):
    db = SessionLocal()
    try:
        l = db.query(models.UserList).filter(models.UserList.id == list_id).first()
        if not l: return False
        db.delete(l)
        db.commit()
        return True
    finally:
        db.close()
