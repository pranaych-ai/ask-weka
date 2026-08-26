"""Per-user Jira filing: OAuth-connected employees file tickets in Jira as
themselves, routed to the project mapped for the conversation's team."""

import os

os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.db import SessionLocal
from backend.main import app
from backend.models import Conversation, JiraAccount, Message, Ticket

client = TestClient(app)


def _make_conversation(domain="IT", username="anonymous"):
    db = SessionLocal()
    try:
        conv = Conversation(username=username, title="VPN issue", domain=domain)
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="VPN drops"))
        db.add(Message(conversation_id=conv.id, role="assistant", content="Try X"))
        db.commit()
        return conv.id
    finally:
        db.close()


def _seed_account(username="anonymous"):
    db = SessionLocal()
    try:
        db.merge(
            JiraAccount(
                username=username,
                access_token="tok",
                refresh_token="ref",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                cloud_id="cloud-1",
                site_url="https://weka.atlassian.net",
                account_id="acc-1",
                email="anonymous@weka.io",
            )
        )
        db.commit()
    finally:
        db.close()


def _drop_account(username="anonymous"):
    db = SessionLocal()
    try:
        row = db.get(JiraAccount, username)
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def _set_mapping(mapping):
    r = client.put("/api/admin/jira/mapping", json={"mapping": mapping})
    assert r.status_code == 200, r.text
    return r.json()


def test_status_disconnected():
    _drop_account()
    r = client.get("/api/jira/status")
    assert r.status_code == 200
    assert r.json()["connected"] is False


def test_connect_requires_config(monkeypatch):
    monkeypatch.delenv("ATLASSIAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("ATLASSIAN_CLIENT_SECRET", raising=False)
    r = client.get("/api/jira/connect", follow_redirects=False)
    assert r.status_code == 503


def test_mapping_roundtrip_and_normalization():
    out = _set_mapping({"IT": "itsm", "HR": "HR", "": "itsm"})
    assert out["mapping"]["IT"] == "ITSM"
    r = client.get("/api/admin/jira/mapping")
    assert r.json()["mapping"]["IT"] == "ITSM"


def test_mapping_rejects_non_strings():
    r = client.put("/api/admin/jira/mapping", json={"mapping": {"IT": 5}})
    assert r.status_code == 400


def test_ticket_files_in_jira_when_connected(monkeypatch):
    _seed_account()
    _set_mapping({"IT": "ITSM", "": "ITSM"})
    calls = {}

    async def fake_create(db, acct, project, title, body):
        calls["project"] = project
        assert acct.username == "anonymous"  # filed AS the employee
        return "ITSM-42", "https://weka.atlassian.net/browse/ITSM-42"

    monkeypatch.setattr("backend.tickets.create_jira_issue", fake_create)
    cid = _make_conversation(domain="IT")
    r = client.post(
        "/api/tickets", json={"conversation_id": cid, "title": "VPN broken", "body": "Issue: x"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["jira_key"] == "ITSM-42"
    assert calls["project"] == "ITSM"


def test_ticket_blocked_without_mapping():
    _seed_account()
    _set_mapping({"IT": "", "": ""})
    cid = _make_conversation(domain="IT")
    r = client.post("/api/tickets", json={"conversation_id": cid, "title": "t", "body": "b"})
    assert r.status_code == 503


def test_jira_error_creates_no_local_ticket(monkeypatch):
    from fastapi import HTTPException

    _seed_account()
    _set_mapping({"IT": "ITSM", "": "ITSM"})

    async def fake_create(db, acct, project, title, body):
        raise HTTPException(502, "Jira rejected the ticket")

    monkeypatch.setattr("backend.tickets.create_jira_issue", fake_create)
    cid = _make_conversation(domain="IT")
    r = client.post("/api/tickets", json={"conversation_id": cid, "title": "t", "body": "b"})
    assert r.status_code == 502
    db = SessionLocal()
    try:
        assert db.query(Ticket).filter(Ticket.conversation_id == cid).count() == 0
        assert db.get(Conversation, cid).resolution == ""  # still unresolved
    finally:
        db.close()


def test_ticket_local_when_not_connected():
    _drop_account()
    cid = _make_conversation(domain="IT")
    r = client.post("/api/tickets", json={"conversation_id": cid, "title": "local", "body": "b"})
    assert r.status_code == 200
    assert r.json()["jira_key"] == ""


def test_disconnect():
    _seed_account()
    r = client.delete("/api/jira/connection")
    assert r.status_code == 200
    assert client.get("/api/jira/status").json()["connected"] is False
