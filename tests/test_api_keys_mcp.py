"""API key lifecycle, public /api/v1 API, rate limiting, and MCP endpoint tests."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import backend.mcp_server as mcp_module
import backend.public_api as public_module
from backend.api_keys import _rate
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog, ApiKey, ApiKeyUsage

client = TestClient(app)


class FakeProvider:
    async def stream_chat(self, system, messages):
        yield "Answer text. Sources:\n"
        yield "- [Benefits Policy](https://portal.weka.io/hr/benefits)"


def _fake_get_provider():
    return FakeProvider()


def _make_key(kind="api", **kwargs):
    body = {"name": f"test-{kind}", "kind": kind, **kwargs}
    r = client.post("/api/admin/keys", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _audit_actions():
    db = SessionLocal()
    try:
        return [r.action for r in db.query(ActivityLog).all()]
    finally:
        db.close()


def test_key_lifecycle_hashed_shown_once_and_audited():
    out = _make_key(owner="platform team", rate_limit_per_min=5)
    assert out["key"].startswith("aw_")
    # Stored hashed — plaintext never in DB
    db = SessionLocal()
    try:
        row = db.get(ApiKey, out["id"])
        assert row.key_hash != out["key"] and out["key"] not in row.key_hash
    finally:
        db.close()
    # List never returns the secret
    listed = client.get("/api/admin/keys?kind=api").json()
    me = next(k for k in listed if k["id"] == out["id"])
    assert "key" not in me and me["prefix"].startswith("aw_")

    # Revoke works and is audited
    r = client.post(f"/api/admin/keys/{out['id']}/revoke")
    assert r.json()["revoked"] is True
    actions = _audit_actions()
    assert "apikey.create" in actions and "apikey.revoke" in actions


def test_v1_ask_with_key_auth_rate_limit_and_usage(monkeypatch):
    monkeypatch.setattr(public_module, "get_provider", _fake_get_provider)
    out = _make_key(rate_limit_per_min=3)
    key = out["key"]
    h = {"Authorization": f"Bearer {key}"}

    # No/bad credential rejected
    assert client.post("/api/v1/ask", json={"question": "hi"}).status_code == 401
    assert client.get("/api/v1/health", headers={"X-API-Key": "aw_wrong"}).status_code == 401

    assert client.get("/api/v1/health", headers=h).json()["ok"] is True
    r = client.post("/api/v1/ask", json={"question": "What benefits exist?"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "Answer text" in body["answer"] and body["sources"]

    # Rate limit: limit 3/min, 2 used -> 1 more ok, then 429
    assert client.get("/api/v1/health", headers=h).status_code == 200
    assert client.get("/api/v1/health", headers=h).status_code == 429

    # Usage recorded
    listed = client.get("/api/admin/keys").json()
    me = next(k for k in listed if k["id"] == out["id"])
    assert me["usage_count"] == 3 and me["last_used_at"]
    db = SessionLocal()
    try:
        assert db.query(ApiKeyUsage).filter(ApiKeyUsage.key_id == out["id"]).count() == 3
    finally:
        db.close()

    # Revoked key stops working
    client.post(f"/api/admin/keys/{out['id']}/revoke")
    _rate.clear()
    assert client.get("/api/v1/health", headers=h).status_code == 401


def test_mcp_endpoint_tools_and_auth(monkeypatch):
    monkeypatch.setattr(mcp_module, "get_provider", _fake_get_provider)
    tok = _make_key(kind="mcp", expires_days=7)
    h = {"Authorization": f"Bearer {tok['key']}"}
    assert tok["key"].startswith("aw_mcp_")

    # API keys cannot access MCP (kind separation)
    api_key = _make_key()
    assert (
        client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {api_key['key']}"}).status_code == 401
    )
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401

    init = client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
    }).json()
    assert init["result"]["serverInfo"]["name"] == "ask-weka"

    # Notification -> 202, no body
    assert client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }).status_code == 202

    tools = client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list"
    }).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["ask_weka"]

    call = client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "ask_weka", "arguments": {"question": "How do I get a laptop?"}},
    }).json()["result"]
    assert call["isError"] is False
    assert "Answer text" in call["content"][0]["text"]
    assert "Cited sources" in call["content"][0]["text"]

    # Unknown tool / method errors
    bad = client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}
    }).json()
    assert "error" in bad
    assert "error" in client.post("/mcp", headers=h, json={
        "jsonrpc": "2.0", "id": 5, "method": "bogus"
    }).json()


def test_mcp_token_expiry_enforced():
    tok = _make_key(kind="mcp", expires_days=7)
    db = SessionLocal()
    try:
        row = db.get(ApiKey, tok["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    finally:
        db.close()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {tok['key']}"})
    assert r.status_code == 401


def test_key_creation_validation():
    assert client.post("/api/admin/keys", json={"name": " "}).status_code == 400
    assert client.post("/api/admin/keys", json={"name": "x", "kind": "weird"}).status_code == 400
    assert client.post("/api/admin/keys", json={"name": "x", "scope": "write"}).status_code == 400
    assert client.post("/api/admin/keys", json={"name": "x", "rate_limit_per_min": 0}).status_code == 400
    assert client.post(
        "/api/admin/keys", json={"name": "x", "kind": "mcp", "expires_days": 365}
    ).status_code == 400
