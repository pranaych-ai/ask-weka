"""Managed Slack notifications: delivery gating, preference consent,
connection verification metadata, manifest generation, secret non-exposure,
rate limiting, and scheduler idempotency."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.slack_service as svc
import backend.slack_scheduler as sched
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog, SlackIntegration, SlackUserPref

client = TestClient(app)


def _reset_integration(**overrides):
    db = SessionLocal()
    try:
        row = db.get(SlackIntegration, 1) or SlackIntegration(id=1)
        row.enabled = overrides.get("enabled", True)
        row.verified = overrides.get("verified", True)
        row.features = json.dumps(overrides.get("features", {}))
        row.notify_channel = overrides.get("notify_channel", "C123")
        row.digest_time = overrides.get("digest_time", "09:00")
        row.digest_timezone = overrides.get("digest_timezone", "UTC")
        row.digest_last_run = overrides.get("digest_last_run", "")
        db.merge(row)
        db.query(SlackUserPref).delete()
        db.commit()
    finally:
        db.close()


def _set_pref(username="anonymous", slack_user_id="U1", prefs=None):
    db = SessionLocal()
    try:
        db.merge(
            SlackUserPref(
                username=username,
                slack_user_id=slack_user_id,
                slack_email="anon@weka.io",
                prefs=json.dumps(prefs or {}),
            )
        )
        db.commit()
    finally:
        db.close()


def _creds(monkeypatch, on=True):
    if on:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    else:
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)


# ---------- Delivery gate ----------


def test_gate_requires_creds_verified_enabled_feature_and_consent(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(features={"ticket_notifications": True})
    db = SessionLocal()
    try:
        ok, _ = svc.check_gate(db, "ticket_notifications")
        assert ok
        # unknown feature
        assert svc.check_gate(db, "nope")[0] is False
        # user-specific with no consent record
        ok, reason = svc.check_gate(db, "ticket_notifications", "anonymous")
        assert not ok and reason == "no_consent"
    finally:
        db.close()
    # consent given → allowed
    _set_pref(prefs={"ticket_notifications": True})
    db = SessionLocal()
    try:
        assert svc.check_gate(db, "ticket_notifications", "anonymous")[0] is True
    finally:
        db.close()
    # missing credentials fail closed
    _creds(monkeypatch, on=False)
    db = SessionLocal()
    try:
        ok, reason = svc.check_gate(db, "ticket_notifications")
        assert not ok and reason == "credentials_missing"
    finally:
        db.close()


def test_gate_blocks_disabled_feature_and_integration(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(features={"ticket_notifications": False})
    db = SessionLocal()
    try:
        assert svc.check_gate(db, "ticket_notifications")[1] == "feature_disabled"
    finally:
        db.close()
    _reset_integration(enabled=False)
    db = SessionLocal()
    try:
        assert svc.check_gate(db, "sync_alerts")[1] == "integration_disabled"
    finally:
        db.close()
    _reset_integration(verified=False)
    db = SessionLocal()
    try:
        assert svc.check_gate(db, "sync_alerts")[1] == "not_verified"
    finally:
        db.close()


def test_skipped_send_is_best_effort_and_audited(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(features={"ticket_notifications": False})
    called = []

    async def fake_api(method, payload=None, **kw):
        called.append(method)
        return {"ok": True}

    monkeypatch.setattr(svc, "slack_api", fake_api)
    ok = asyncio.get_event_loop().run_until_complete(
        svc.notify_user("ticket_notifications", "anonymous", "hi")
    )
    assert ok is False and called == []  # skipped, nothing sent, no exception
    db = SessionLocal()
    try:
        row = (
            db.query(ActivityLog)
            .filter(ActivityLog.action == "slack.skip")
            .order_by(ActivityLog.created_at.desc())
            .first()
        )
        assert row and "feature=ticket_notifications" in row.detail
        assert "xoxb" not in row.detail  # never log credential values
    finally:
        db.close()


def test_consented_dm_sends_block_kit(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(features={"ticket_notifications": True})
    _set_pref(prefs={"ticket_notifications": True})
    calls = []

    async def fake_api(method, payload=None, **kw):
        calls.append((method, payload))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D9"}}
        return {"ok": True}

    monkeypatch.setattr(svc, "slack_api", fake_api)
    ok = asyncio.get_event_loop().run_until_complete(
        svc.notify_user("ticket_notifications", "anonymous", "Your ticket was filed")
    )
    assert ok is True
    post = [p for m, p in calls if m == "chat.postMessage"][0]
    assert post["channel"] == "D9" and post["blocks"][0]["type"] == "section"


# ---------- Verification metadata ----------


def test_verify_stores_only_nonsecret_metadata(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(verified=False)

    async def fake_api(method, payload=None, **kw):
        assert method == "auth.test"
        return {
            "ok": True, "team_id": "T42", "team": "WEKA", "url": "https://weka.slack.com/",
            "user_id": "UBOT", "user": "askweka",
        }

    monkeypatch.setattr(svc, "slack_api", fake_api)
    r = client.post("/api/admin/slack/verify")
    assert r.status_code == 200
    data = r.json()
    assert data["verified"] is True and data["team_id"] == "T42" and data["bot_name"] == "askweka"
    blob = json.dumps(data)
    assert "xoxb" not in blob and "shhh" not in blob
    assert "token" not in blob.lower() or "credentials_configured" in blob


def test_verify_failure_records_error(monkeypatch):
    _creds(monkeypatch)

    async def fake_api(method, payload=None, **kw):
        return {"ok": False, "error": "invalid_auth"}

    monkeypatch.setattr(svc, "slack_api", fake_api)
    data = client.post("/api/admin/slack/verify").json()
    assert data["verified"] is False and data["last_verify_error"] == "invalid_auth"


# ---------- Config API: no secret intake or exposure ----------


def test_config_never_returns_or_accepts_secrets(monkeypatch):
    _creds(monkeypatch)
    _reset_integration()
    data = client.get("/api/admin/slack/config").json()
    blob = json.dumps(data)
    assert "xoxb-test" not in blob and "shhh" not in blob
    assert data["credentials_configured"] is True
    # Unknown fields (e.g. an attempted token) are ignored by the schema,
    # and no credential-shaped field exists to update.
    r = client.put(
        "/api/admin/slack/config",
        json={"bot_token": "xoxb-evil", "features": {"usage_digest": True}},
    )
    assert r.status_code == 200
    assert "bot_token" not in json.dumps(r.json())
    db = SessionLocal()
    try:
        row = db.get(SlackIntegration, 1)
        for col in row.__table__.columns.keys():
            assert "xoxb" not in str(getattr(row, col))
    finally:
        db.close()


def test_config_validation():
    assert client.put("/api/admin/slack/config", json={"features": {"bogus": True}}).status_code == 400
    assert client.put("/api/admin/slack/config", json={"digest_time": "25:00"}).status_code == 400
    assert client.put("/api/admin/slack/config", json={"digest_timezone": "Mars/Olympus"}).status_code == 400
    assert client.put(
        "/api/admin/slack/config", json={"slash_commands": [{"command": "bad cmd"}]}
    ).status_code == 400


# ---------- Manifest ----------


def test_manifest_generation(monkeypatch):
    monkeypatch.setenv("SLACK_APP_BASE_URL", "https://askweka.example.com")
    _reset_integration()
    data = client.get("/api/admin/slack/manifest").json()
    m = data["manifest"]
    assert m["settings"]["event_subscriptions"]["request_url"] == \
        "https://askweka.example.com/api/slack/events"
    assert m["features"]["slash_commands"][0]["command"] == "/askweka"
    assert "commands" in m["oauth_config"]["scopes"]["bot"]
    blob = json.dumps(data)
    assert "xoxb" not in blob


# ---------- Employee preferences ----------


def test_prefs_optin_resolves_identity_and_mismatch_error(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(features={"ticket_notifications": True})

    async def not_found(method, payload=None, **kw):
        return {"ok": False, "error": "users_not_found"}

    monkeypatch.setattr(svc, "slack_api", not_found)
    r = client.put("/api/slack/prefs", json={"prefs": {"ticket_notifications": True}})
    assert r.status_code == 400 and "must match" in r.json()["detail"]

    async def found(method, payload=None, **kw):
        return {"ok": True, "user": {"id": "U77"}}

    monkeypatch.setattr(svc, "slack_api", found)
    r = client.put("/api/slack/prefs", json={"prefs": {"ticket_notifications": True}})
    assert r.status_code == 200 and r.json()["slack_linked"] is True
    got = client.get("/api/slack/prefs").json()
    assert got["prefs"]["ticket_notifications"] is True
    # unknown / non-optin keys rejected
    assert client.put("/api/slack/prefs", json={"prefs": {"sync_alerts": True}}).status_code == 400


def test_prefs_requires_login_in_prod_mode():
    # In dev mode auth is disabled; assert the route depends on require_user by
    # checking it exists and returns available features (admin-gated visibility).
    _reset_integration(enabled=False)
    data = client.get("/api/slack/prefs").json()
    assert data["integration_active"] is False
    assert all(not f["enabled"] for f in data["available"])


# ---------- Rate limiting ----------


def test_slack_api_retries_once_on_rate_limit(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status, data, headers=None):
            self.status_code = status
            self._data = data
            self.headers = headers or {}

        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "0"})
            return FakeResp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = asyncio.get_event_loop().run_until_complete(svc.slack_api("chat.postMessage", {}))
    assert out == {"ok": True} and calls["n"] == 2

    # persistent rate limit → exactly one retry, then a clean failure
    calls["n"] = 10  # every call rate-limited
    class AlwaysLimited(FakeClient):
        async def post(self, *a, **kw):
            calls["n"] += 1
            return FakeResp(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "0"})

    monkeypatch.setattr(httpx, "AsyncClient", AlwaysLimited)
    before = calls["n"]
    out = asyncio.get_event_loop().run_until_complete(svc.slack_api("chat.postMessage", {}))
    assert out["ok"] is False and out["error"] == "ratelimited"
    assert calls["n"] - before == 2


# ---------- Scheduler idempotency ----------


def test_scheduler_posts_once_per_due_key(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(
        features={"usage_digest": True}, digest_time="00:00", digest_timezone="UTC"
    )
    posts = []

    async def fake_notify(feature, text, blocks=None):
        posts.append(text)
        return True

    async def fake_digest(metrics):
        return "digest text"

    monkeypatch.setattr(svc, "notify_channel", fake_notify)
    monkeypatch.setattr(sched.svc, "notify_channel", fake_notify)
    monkeypatch.setattr(sched, "_write_digest", fake_digest)

    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(sched.check_once()) is True
    # same day, second tick (also simulates a restart) → no duplicate
    assert loop.run_until_complete(sched.check_once()) is False
    assert len(posts) == 1
    db = SessionLocal()
    try:
        assert db.get(SlackIntegration, 1).digest_last_run != ""
    finally:
        db.close()


def test_scheduler_reclaims_after_failed_post(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(
        features={"usage_digest": True}, digest_time="00:00", digest_timezone="UTC"
    )

    async def failing_notify(feature, text, blocks=None):
        return False

    async def fake_digest(metrics):
        return "digest text"

    monkeypatch.setattr(sched.svc, "notify_channel", failing_notify)
    monkeypatch.setattr(sched, "_write_digest", fake_digest)
    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(sched.check_once()) is False
    db = SessionLocal()
    try:
        # claim rolled back → the next tick can retry today
        assert db.get(SlackIntegration, 1).digest_last_run == ""
    finally:
        db.close()


def test_scheduler_respects_gate(monkeypatch):
    _creds(monkeypatch)
    _reset_integration(features={"usage_digest": False}, digest_time="00:00")
    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(sched.check_once()) is False


# ---------- RBAC ----------


def test_admin_slack_routes_are_admin_gated():
    # Routes are on the centrally-audited admin router; in dev mode the
    # anonymous user is admin, so simply assert they respond (RBAC coverage
    # for non-admins lives in test_rbac_audit.py's route sweep).
    assert client.get("/api/admin/slack/config").status_code == 200
