"""Alerting layer: dedup, thresholds, and coverage of non-5xx failure paths
(MCP JSON-RPC errors return HTTP 200; Slack DM failures are background)."""

import asyncio
import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import backend.alerts as alerts
import backend.mcp_server as mcp_module
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_alert_state(monkeypatch):
    """Fresh dedup/counter state and a captured Slack channel for each test."""
    alerts._last_sent.clear()
    alerts._suppressed.clear()
    alerts._auth_failures.clear()
    alerts._server_errors.clear()
    sent: list[str] = []

    async def fake_notify_channel(feature, text, blocks=None):
        assert feature == "security_alerts"
        sent.append(text)
        return True

    import backend.slack_service as svc

    monkeypatch.setattr(svc, "notify_channel", fake_notify_channel)
    yield sent


def _run(coro):
    return asyncio.run(coro)


def _audit(action):
    db = SessionLocal()
    try:
        return [
            r.detail
            for r in db.query(ActivityLog).filter(ActivityLog.action == action).all()
        ]
    finally:
        db.close()


def test_dedup_suppresses_and_reports_count(_reset_alert_state):
    sent = _reset_alert_state

    async def go():
        assert await alerts.send_alert("k", "warning", "T", "d") is True
        assert await alerts.send_alert("k", "warning", "T", "d") is False
        assert await alerts.send_alert("k", "warning", "T", "d") is False
        # different key is not deduped
        assert await alerts.send_alert("k2", "critical", "T2") is True
        # expire the cooldown -> next post reports the 2 suppressed repeats
        alerts._last_sent["k"] = 0.0
        assert await alerts.send_alert("k", "warning", "T", "d") is True

    _run(go())
    assert len(sent) == 3
    assert "2 similar alert(s) suppressed" in sent[-1]
    assert "[CRITICAL]" in sent[1]


def test_auth_failure_threshold_fires_once(_reset_alert_state):
    sent = _reset_alert_state

    async def go():
        for _ in range(alerts.AUTH_FAILURE_THRESHOLD):
            alerts.record_auth_failure("access_denied")
        await asyncio.sleep(0.05)

    _run(go())
    assert len(sent) == 1
    assert "Repeated sign-in failures" in sent[0]
    # no credentials / usernames in the alert text
    assert "password" not in sent[0].lower()


def test_server_error_threshold(_reset_alert_state):
    sent = _reset_alert_state

    async def go():
        for _ in range(alerts.SERVER_ERROR_THRESHOLD):
            alerts.record_server_error("/api/chat")
        await asyncio.sleep(0.05)

    _run(go())
    assert len(sent) == 1
    assert "Application errors spiking" in sent[0]


def _make_mcp_key():
    r = client.post("/api/admin/keys", json={"name": "alerts-mcp", "kind": "mcp"})
    assert r.status_code == 200, r.text
    return r.json()["key"]


def test_mcp_provider_failure_alerts_despite_http_200(_reset_alert_state, monkeypatch):
    sent = _reset_alert_state

    def broken_provider():
        raise RuntimeError("model retired")

    monkeypatch.setattr(mcp_module, "get_provider", broken_provider)
    tok = _make_mcp_key()
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ask_weka", "arguments": {"question": "hi"}},
    })
    # JSON-RPC error travels over HTTP 200 — middleware can't see it...
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    # ...but the owner alert still fired, and repeats are deduplicated.
    assert len(sent) == 1
    assert "AI provider failing" in sent[0]
    client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "ask_weka", "arguments": {"question": "hi again"}},
    })
    assert len(sent) == 1  # deduped within cooldown
    assert any("app.ai_provider_failure" in d for d in _audit("alert.raised"))
    assert _audit("alert.deduplicated")


def test_slack_dm_failure_alerts(_reset_alert_state, monkeypatch):
    sent = _reset_alert_state
    import backend.slack_app as slack_app

    async def fake_resolve(slack_user_id):
        return "u@weka.io"

    def broken_provider():
        raise RuntimeError("model retired")

    import backend.providers as providers

    monkeypatch.setattr(slack_app, "_resolve_email", fake_resolve)
    monkeypatch.setattr(providers, "get_provider", broken_provider)

    async def go():
        # _answer_dm_inner swallows exceptions; provider failure must still alert.
        await slack_app._answer_dm_inner("D1", "U123", "hello")
        await asyncio.sleep(0.05)

    _run(go())
    assert len(sent) == 1
    assert "AI provider failing" in sent[0]
    assert "Slack DM" in sent[0]


def test_admin_change_alert_content(_reset_alert_state):
    sent = _reset_alert_state

    async def go():
        alerts.record_admin_change("someone@weka.io", True, "Okta group")
        await asyncio.sleep(0.05)

    _run(go())
    assert len(sent) == 1
    assert "[CRITICAL]" in sent[0]
    assert "someone@weka.io" in sent[0]
    assert "Okta group" in sent[0]
