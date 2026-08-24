"""QA dashboard & golden-question tests (dev mode, throwaway SQLite DB)."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

from fastapi.testclient import TestClient

from backend import qa as qa_module
from backend.db import SessionLocal
from backend.main import app
from backend.models import ActivityLog, Feedback

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


def _seed_feedback(**kw):
    db = SessionLocal()
    try:
        fb = Feedback(
            message_id="m-" + os.urandom(4).hex(),
            question=kw.get("question", "How do I reset my password?"),
            answer_summary="Use the portal.",
            thumbs=kw.get("thumbs", "down"),
            feedback_text=kw.get("feedback_text", "not helpful"),
            domain=kw.get("domain", "IT"),
        )
        db.add(fb)
        db.commit()
        return fb.id
    finally:
        db.close()


def test_stats_endpoint():
    _seed_feedback(thumbs="down")
    _seed_feedback(thumbs="up", domain="HR", feedback_text="")
    data = client.get("/api/admin/qa/stats").json()
    assert data["by_day"] and data["by_domain"]
    assert isinstance(data["open_reviews"], int)
    assert any(f["thumbs"] == "down" for f in data["recent_negative"])


def test_feedback_filters_and_review():
    fid = _seed_feedback(thumbs="down", domain="HR")
    r = client.get("/api/admin/qa/feedback?thumbs=down&domain=HR").json()
    assert any(i["id"] == fid for i in r["items"])
    assert all(i["thumbs"] == "down" and i["domain"] == "HR" for i in r["items"])

    _clear_audit()
    assert client.patch(f"/api/admin/qa/feedback/{fid}", json={"review_status": "resolved"}).json()["ok"]
    r2 = client.get("/api/admin/qa/feedback?status=resolved").json()
    assert any(i["id"] == fid for i in r2["items"])
    assert any(a == "qa.feedback.review" for a, _ in _audit_actions())
    assert client.patch(f"/api/admin/qa/feedback/{fid}", json={"review_status": "bogus"}).status_code == 400
    assert client.get("/api/admin/qa/feedback?from=nope").status_code == 400


def test_to_date_is_inclusive_of_whole_day():
    from datetime import datetime, timezone

    fid = _seed_feedback(thumbs="up", domain="IT", feedback_text="")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = client.get(f"/api/admin/qa/feedback?to={today}").json()
    assert any(i["id"] == fid for i in r["items"])


class FakeProvider:
    """First question answers with a source; second without (auto-flag)."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, system, history):
        self.calls += 1
        if self.calls == 1:
            yield "See [the wiki](https://wiki.weka.io/page)."
        else:
            yield "I am not sure."


def test_golden_crud_run_and_verdict(monkeypatch):
    q1 = client.post("/api/admin/qa/golden", json={"question": "How to get a laptop?", "domain": "IT"}).json()
    q2 = client.post("/api/admin/qa/golden", json={"question": "What is the PTO policy?"}).json()
    assert q2["domain"] == "HR"  # auto-classified
    assert client.post("/api/admin/qa/golden", json={"question": "  "}).status_code == 400

    # deactivate/reactivate
    assert client.patch(f"/api/admin/qa/golden/{q1['id']}", json={"active": False}).json()["active"] is False
    assert client.patch(f"/api/admin/qa/golden/{q1['id']}", json={"active": True}).json()["active"] is True

    monkeypatch.setattr(qa_module, "get_provider", lambda: FakeProvider())
    _clear_audit()
    run = client.post("/api/admin/qa/golden/run").json()
    assert run["status"] == "done" and run["total"] == 2
    flagged = [r for r in run["results"] if r["auto_flagged"]]
    cited = [r for r in run["results"] if not r["auto_flagged"]]
    assert len(flagged) == 1 and len(cited) == 1
    assert cited[0]["sources_count"] == 1
    actions = [a for a, _ in _audit_actions()]
    assert "qa.golden.run" in actions

    # verdict marking
    rid = run["results"][0]["id"]
    assert client.patch(f"/api/admin/qa/golden/results/{rid}", json={"verdict": "pass"}).json()["ok"]
    detail = client.get(f"/api/admin/qa/golden/runs/{run['id']}").json()
    assert detail["passed"] == 1
    assert client.patch(f"/api/admin/qa/golden/results/{rid}", json={"verdict": "nope"}).status_code == 400

    # runs list
    runs = client.get("/api/admin/qa/golden/runs").json()
    assert any(r["id"] == run["id"] for r in runs)


def test_golden_run_with_no_active_questions():
    db = SessionLocal()
    try:
        from backend.models import GoldenQuestion
        for g in db.query(GoldenQuestion).all():
            g.active = False
        db.commit()
    finally:
        db.close()
    assert client.post("/api/admin/qa/golden/run").status_code == 400
