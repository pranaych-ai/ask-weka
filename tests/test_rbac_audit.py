"""RBAC, ownership-isolation, and audit-coverage tests.

Run: python -m pytest tests/ -q
(Uses a throwaway SQLite DB and dev mode — Okta env vars are cleared before
backend import.)
"""

import os

# Must run before backend imports: force dev mode + isolated DB.
for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import auth
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog, Conversation, Message

client = TestClient(app)


def _audit_actions():
    db = SessionLocal()
    try:
        return [(r.action, r.detail) for r in db.query(ActivityLog).all()]
    finally:
        db.close()


def _clear_audit():
    db = SessionLocal()
    try:
        db.query(ActivityLog).delete()
        db.commit()
    finally:
        db.close()


class FakeRequest:
    def __init__(self, session):
        self.session = session


def test_dev_mode_grants_admin():
    me = client.get("/api/me").json()
    assert me["is_admin"] is True
    assert me["auth_enabled"] is False


def test_non_admin_gets_403(monkeypatch):
    import time
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    now = time.time()
    session = {"user": {"username": "user1", "is_admin": False,
                        "logged_in_at": now, "last_seen": now}}
    with pytest.raises(HTTPException) as e:
        auth.require_admin(FakeRequest(session))
    assert e.value.status_code == 403


def test_group_admin_allowed(monkeypatch):
    import time
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    now = time.time()
    session = {"user": {"username": "admin1", "is_admin": True,
                        "logged_in_at": now, "last_seen": now}}
    user = auth.require_admin(FakeRequest(session))
    assert user["username"] == "admin1"


def test_unauthenticated_gets_401(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    with pytest.raises(HTTPException) as e:
        auth.require_admin(FakeRequest({}))
    assert e.value.status_code == 401


def test_ownership_isolation():
    # A conversation owned by someone else must be invisible to the caller
    # (dev-mode caller is "anonymous").
    db = SessionLocal()
    conv = Conversation(title="other user's", username="someone.else")
    conv.messages.append(Message(role="user", content="private question"))
    conv.messages.append(Message(role="assistant", content="private answer"))
    db.add(conv)
    db.commit()
    other_id = conv.id
    other_msg_id = conv.messages[-1].id
    db.close()

    ids = [c["id"] for c in client.get("/api/conversations").json()]
    assert other_id not in ids
    assert client.get(f"/api/conversations/{other_id}/messages").status_code == 404
    assert client.delete(f"/api/conversations/{other_id}").status_code == 404
    # Feedback on another user's message is also rejected.
    r = client.post("/api/feedback", json={"message_id": other_msg_id, "thumbs": "up"})
    assert r.status_code == 404


def test_admin_routes_are_audited():
    _clear_audit()
    client.get("/api/admin/summary")
    client.get("/api/admin/audit")
    client.get("/api/admin/actions")
    actions = [a for a, _ in _audit_actions()]
    assert "admin.summary" in actions
    assert "admin.audit" in actions
    assert "admin.actions" in actions


def test_chat_mutations_audited_without_content():
    _clear_audit()
    secret = "my SSN is 123-45-6789"
    client.post("/api/chat", json={"message": secret})
    events = _audit_actions()
    actions = [a for a, _ in events]
    assert "conversation.create" in actions
    assert "chat.message" in actions
    # No raw user content may ever reach the audit trail.
    assert not any(secret[:12] in (d or "") for _, d in events)


def test_conversation_delete_audited():
    conv_id = [c["id"] for c in client.get("/api/conversations").json()][0]
    _clear_audit()
    assert client.delete(f"/api/conversations/{conv_id}").json()["ok"] is True
    actions = [a for a, _ in _audit_actions()]
    assert "conversation.delete" in actions
