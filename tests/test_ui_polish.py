"""Suggestions endpoint and domain-scoped prompt tests."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

from fastapi.testclient import TestClient

from backend.db import SessionLocal
from backend.main import app
from backend.models import GoldenQuestion
from backend.prompts import build_system_prompt

client = TestClient(app)


def test_suggestions_include_golden_and_fallbacks():
    db = SessionLocal()
    try:
        db.add(GoldenQuestion(question="How do I enroll in the ESPP?", domain="HR"))
        db.add(GoldenQuestion(question="Inactive one", domain="IT", active=False))
        db.commit()
    finally:
        db.close()
    out = client.get("/api/suggestions").json()
    questions = [s["question"] for s in out]
    assert "How do I enroll in the ESPP?" in questions
    assert "Inactive one" not in questions
    assert 1 <= len(out) <= 6
    assert all("question" in s and "domain" in s for s in out)


def test_domain_scoped_prompt_filters_sections():
    client.post("/api/admin/kb/sections", json={
        "title": "HR only", "domain": "HR", "body": "HR-ONLY-MARKER"})
    client.post("/api/admin/kb/sections", json={
        "title": "IT only", "domain": "IT", "body": "IT-ONLY-MARKER"})
    client.post("/api/admin/kb/sections", json={
        "title": "Shared", "domain": "", "body": "SHARED-MARKER"})

    full = build_system_prompt()
    assert "HR-ONLY-MARKER" in full and "IT-ONLY-MARKER" in full

    hr = build_system_prompt("HR")
    assert "HR-ONLY-MARKER" in hr
    assert "IT-ONLY-MARKER" not in hr
    assert "SHARED-MARKER" in hr  # untagged/shared sections stay in scope
    assert "scoped this conversation to HR" in hr

    it = build_system_prompt("IT")
    assert "IT-ONLY-MARKER" in it and "HR-ONLY-MARKER" not in it


def test_resumed_conversation_keeps_its_stored_domain(monkeypatch):
    """Regression: an existing conversation's scope wins over the client value."""
    import backend.main as main_module

    class FakeProvider:
        async def stream_chat(self, system, history):
            yield "ok"

    seen = []
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(
        main_module, "build_system_prompt", lambda d="": seen.append(d) or "sys"
    )

    def drain(payload):
        with client.stream("POST", "/api/chat", json=payload) as r:
            assert r.status_code == 200
            body = "".join(r.iter_text())
        import json as _json
        first = _json.loads(body.split("\n\n")[0].removeprefix("data: "))
        return first["conversation_id"]

    conv_id = drain({"message": "HR question", "domain": "HR"})
    assert seen[-1] == "HR"

    # Second turn claims IT, but the conversation was created as HR.
    drain({"conversation_id": conv_id, "message": "follow-up", "domain": "IT"})
    assert seen[-1] == "HR"

    convs = client.get("/api/conversations").json()
    assert any(c["id"] == conv_id and c["domain"] == "HR" for c in convs)


def test_chat_rejects_bad_domain():
    r = client.post("/api/chat", json={"message": "hi", "domain": "LEGAL"})
    assert r.status_code == 400
