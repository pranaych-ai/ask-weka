"""Admin portal APIs. Every route is gated by require_admin (server-side RBAC)."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from fastapi import Request

from .audit import log_event_standalone
from .auth import require_admin
from .db import get_db
from .models import ActivityLog, Conversation, Feedback, Message


def admin_audited(request: Request) -> dict:
    """RBAC gate + centralized audit for EVERY admin route.

    New endpoints added to this router are automatically both protected and
    audited — routes cannot omit the audit record.
    """
    user = require_admin(request)
    section = request.url.path.removeprefix("/api/admin").strip("/") or "root"
    log_event_standalone(user["username"], f"admin.{section}", str(request.url.query)[:500])
    return user


router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_audited)])

PAGE_SIZE_MAX = 100


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    """Counts for the admin dashboard landing page."""
    return {
        "conversations": db.query(func.count(Conversation.id)).scalar(),
        "messages": db.query(func.count(Message.id)).scalar(),
        "feedback": db.query(func.count(Feedback.id)).scalar(),
        "thumbs_up": db.query(func.count(Feedback.id)).filter(Feedback.thumbs == "up").scalar(),
        "thumbs_down": db.query(func.count(Feedback.id)).filter(Feedback.thumbs == "down").scalar(),
        "audit_events": db.query(func.count(ActivityLog.id)).scalar(),
    }


@router.get("/audit")
def audit_log(
    db: Session = Depends(get_db),
    username: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    page_size: int = 50,
):
    """Read-only, paginated, filterable audit trail."""
    if page < 1:
        page = 1
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))

    q = db.query(ActivityLog)
    if username:
        q = q.filter(ActivityLog.username.ilike(f"%{username}%"))
    if action:
        q = q.filter(ActivityLog.action.ilike(f"%{action}%"))
    for name, value, op in (("from", date_from, "ge"), ("to", date_to, "le")):
        if value:
            try:
                dt = datetime.fromisoformat(value)
            except ValueError:
                raise HTTPException(400, f"Invalid '{name}' date (use YYYY-MM-DD)")
            if op == "ge":
                q = q.filter(ActivityLog.created_at >= dt)
            else:
                q = q.filter(ActivityLog.created_at <= dt.replace(hour=23, minute=59, second=59))

    total = q.count()
    rows = (
        q.order_by(ActivityLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "events": [
            {
                "id": r.id,
                "username": r.username,
                "action": r.action,
                "detail": r.detail,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/actions")
def distinct_actions(db: Session = Depends(get_db)):
    """Distinct action names for the audit filter dropdown."""
    rows = db.query(ActivityLog.action).distinct().order_by(ActivityLog.action).all()
    return [r[0] for r in rows]
