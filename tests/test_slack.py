"""Slack events endpoint: signature verification, handshake, DM answer flow."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import asyncio
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

import backend.slack_app as slack_module
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog, Conversation, SlackChannel, SlackIntegration

client = TestClient(app)

SECRET = "test-signing-secret"


def _signed_post(payload: dict, secret: str = SECRET, ts: str = None):
    body = json.dumps(payload).encode()
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return client.post(
        "/api/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _enable(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    db = SessionLocal()
    try:
        row = db.get(SlackIntegration, 1) or SlackIntegration(id=1)
        row.enabled = True
        row.verified = True
        row.features = json.dumps({"dm_chat": True})
        db.merge(row)
        db.commit()
    finally:
        db.close()


def test_disabled_without_secrets(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    assert client.post("/api/slack/events", content=b"{}").status_code == 404


def test_signature_required(monkeypatch):
    _enable(monkeypatch)
    # No headers
    assert client.post("/api/slack/events", content=b"{}").status_code == 401
    # Wrong secret
    assert _signed_post({"type": "url_verification"}, secret="wrong").status_code == 401
    # Stale timestamp (replay)
    assert (
        _signed_post({"type": "url_verification"}, ts=str(int(time.time()) - 3600)).status_code
        == 401
    )


def test_url_verification_challenge(monkeypatch):
    _enable(monkeypatch)
    r = _signed_post({"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json()["challenge"] == "abc123"


class FakeProvider:
    async def stream_chat(self, system, messages):
        yield "Restart your VPN client. Sources:\n"
        yield "- [VPN Guide](https://portal.weka.io/it/vpn)"


def test_dm_answered_and_logged(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(slack_module, "get_provider", None, raising=False)
    import backend.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda: FakeProvider())

    calls = []

    async def fake_slack_call(method, payload):
        calls.append((method, payload))
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"email": "jane@weka.io"}}}
        return {"ok": True}

    monkeypatch.setattr(slack_module, "_slack_call", fake_slack_call)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake_slack_call)

    event = {
        "type": "event_callback",
        "event_id": "Ev001",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "user": "U123",
            "text": "My VPN keeps dropping",
        },
    }
    r = _signed_post(event)
    assert r.status_code == 200

    # Background task runs on the test client's loop; give it a beat.
    for _ in range(50):
        if any(m == "chat.postMessage" for m, _ in calls):
            break
        time.sleep(0.1)

    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert posts and "VPN" in posts[0]["text"] and "portal.weka.io" in posts[0]["text"]

    db = SessionLocal()
    try:
        m = db.get(SlackChannel, "D123")
        assert m is not None
        conv = db.get(Conversation, m.conversation_id)
        assert conv.username == "jane@weka.io"
        assert [x.role for x in conv.messages] == ["user", "assistant"]
        via_slack = (
            db.query(ActivityLog)
            .filter(ActivityLog.username == "jane@weka.io")
            .all()
        )
        assert any("via=slack" in log.detail for log in via_slack)
    finally:
        db.close()

    # Duplicate delivery (Slack retry) is ignored
    before = len(calls)
    assert _signed_post(event).status_code == 200
    time.sleep(0.3)
    assert len(calls) == before


def test_unverified_user_refused(monkeypatch):
    _enable(monkeypatch)

    calls = []

    async def fake_slack_call(method, payload):
        calls.append((method, payload))
        if method == "users.info":
            return {"ok": False, "error": "user_not_found"}
        return {"ok": True}

    monkeypatch.setattr(slack_module, "_slack_call", fake_slack_call)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake_slack_call)

    event = {
        "type": "event_callback",
        "event_id": "Ev002",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D999",
            "user": "U999",
            "text": "hello",
        },
    }
    assert _signed_post(event).status_code == 200
    for _ in range(50):
        if any(m == "chat.postMessage" for m, _ in calls):
            break
        time.sleep(0.1)
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert posts and "verified WEKA employees" in posts[0]["text"]
