"""Solve-first ticketing + anonymous-feedback tests."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import backend.providers as providers_module
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog, Conversation, Feedback, Message, Ticket

client = TestClient(app)


class FakeDraftProvider:
    async def stream_chat(self, system, messages):
        yield json.dumps(
            {"title": "VPN keeps dropping", "body": "Issue: VPN drops.\nAlready tried:\n- restart"}
        )


def _make_conversation(username="anonymous", with_answer=True):
    db = SessionLocal()
    try:
        conv = Conversation(title="VPN problem", username=username, domain="IT")
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="My VPN keeps dropping"))
        if with_answer:
            db.add(
                Message(conversation_id=conv.id, role="assistant", content="Try restarting it.")
            )
        db.commit()
        return conv.id
    finally:
        db.close()


def test_solved_marks_conversation_and_audits():
    cid = _make_conversation()
    r = client.post("/api/tickets/solved", json={"conversation_id": cid})
    assert r.status_code == 200 and r.json()["resolution"] == "solved"
    db = SessionLocal()
    try:
        assert db.get(Conversation, cid).resolution == "solved"
        assert db.query(ActivityLog).filter_by(action="chat.solved").count() >= 1
    finally:
        db.close()
    # Solved conversations show up in the deflection metric
    stats = client.get("/api/admin/tickets/stats").json()
    assert stats["solved_without_ticket"] >= 1


def test_draft_then_approve_creates_ticket_once(monkeypatch):
    monkeypatch.setattr(providers_module, "get_provider", lambda: FakeDraftProvider())
    cid = _make_conversation()

    # Draft is AI-generated from the transcript, nothing stored yet
    r = client.post("/api/tickets/draft", json={"conversation_id": cid})
    assert r.status_code == 200
    draft = r.json()
    assert draft["title"] == "VPN keeps dropping"
    db = SessionLocal()
    try:
        assert db.query(Ticket).filter_by(conversation_id=cid).count() == 0
    finally:
        db.close()

    # Employee approves -> ticket filed, conversation marked, audited
    r = client.post(
        "/api/tickets",
        json={"conversation_id": cid, "title": draft["title"], "body": draft["body"]},
    )
    assert r.status_code == 200
    tid = r.json()["id"]
    db = SessionLocal()
    try:
        assert db.get(Conversation, cid).resolution == "ticket"
        assert db.query(ActivityLog).filter_by(action="ticket.create").count() >= 1
    finally:
        db.close()

    # No duplicates; can't also mark solved afterwards
    assert (
        client.post(
            "/api/tickets", json={"conversation_id": cid, "title": "x", "body": ""}
        ).status_code
        == 400
    )
    assert client.post("/api/tickets/solved", json={"conversation_id": cid}).status_code == 400

    # Shows in my tickets and admin list; admin can progress status
    assert any(t["id"] == tid for t in client.get("/api/tickets").json())
    assert any(t["id"] == tid for t in client.get("/api/admin/tickets").json())
    r = client.patch(f"/api/admin/tickets/{tid}", json={"status": "resolved"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert client.patch(f"/api/admin/tickets/{tid}", json={"status": "bogus"}).status_code == 400


def test_ticket_requires_owned_conversation():
    cid = _make_conversation(username="someone-else")
    assert client.post("/api/tickets/solved", json={"conversation_id": cid}).status_code == 404
    assert (
        client.post(
            "/api/tickets", json={"conversation_id": cid, "title": "t", "body": ""}
        ).status_code
        == 404
    )


def test_feedback_is_anonymous():
    cid = _make_conversation()
    db = SessionLocal()
    try:
        msg_id = (
            db.query(Message).filter_by(conversation_id=cid, role="assistant").first().id
        )
    finally:
        db.close()

    r = client.post("/api/feedback", json={"message_id": msg_id, "thumbs": "down"})
    assert r.status_code == 200

    # Stored rater is a one-way hash, never the raw username
    db = SessionLocal()
    try:
        fb = db.query(Feedback).filter_by(message_id=msg_id).one()
        key = (os.environ.get("SESSION_SECRET", "") or "dev-only-insecure").encode()
        expected = hmac.new(key, b"anonymous", hashlib.sha256).hexdigest()
        assert fb.username == expected  # keyed pseudonym, never the raw name
        # Audit entry must not link the person to the question
        fb_logs = db.query(ActivityLog).filter_by(action="feedback.submit").all()
        assert all("message_id" not in log.detail for log in fb_logs)
    finally:
        db.close()

    # Admin QA APIs never expose a username
    for item in client.get("/api/admin/qa/feedback").json()["items"]:
        assert "username" not in item
    for item in client.get("/api/admin/qa/stats").json()["recent_negative"]:
        assert "username" not in item
