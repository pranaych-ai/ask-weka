"""Audit service: record who/what/when to the activity_log table.

Every mutation and admin action must be recorded. Never log secrets or raw
PII values — keep `detail` to identifiers and short descriptions.
"""

import logging

from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import ActivityLog

logger = logging.getLogger("askweka.audit")


def log_event(db: Session, username: str, action: str, detail: str = "") -> None:
    """Add an audit event to an existing session (committed by the caller)."""
    db.add(ActivityLog(username=username, action=action, detail=detail[:2000]))


def log_event_standalone(username: str, action: str, detail: str = "") -> None:
    """Record an audit event in its own session (for auth flows etc.)."""
    db = SessionLocal()
    try:
        log_event(db, username, action, detail)
        db.commit()
    except Exception:  # auditing must never break the request path
        logger.exception("Failed to write audit event %s", action)
        db.rollback()
    finally:
        db.close()
