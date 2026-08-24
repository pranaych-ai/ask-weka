"""Knowledge base management tests (dev mode, throwaway SQLite DB)."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import io

from fastapi.testclient import TestClient

from backend.db import SessionLocal
from backend.knowledge import load_knowledge, split_markdown_sections
from backend.main import app
from backend.models import ActivityLog, KBSection
from backend.prompts import build_system_prompt

client = TestClient(app)


def _audit_actions():
    db = SessionLocal()
    try:
        return [r.action for r in db.query(ActivityLog).all()]
    finally:
        db.close()


def test_legacy_import_seeded_sections():
    # main.py imports knowledge/kb.md on startup when the table is empty.
    sections = client.get("/api/admin/kb/sections").json()
    assert len(sections) > 3
    assert all(s["version"] == 1 for s in sections)


def test_split_markdown_sections():
    text = "intro line\n# One\nbody1\n## Two\nbody2\n"
    parts = split_markdown_sections(text)
    assert [t for t, _ in parts] == ["Preamble", "One", "Two"]
    assert "body2" in parts[2][1]


def test_prompt_built_from_active_sections_and_updates_without_restart():
    s = client.post(
        "/api/admin/kb/sections",
        json={"title": "Test policy", "domain": "IT", "body": "UNIQUE-MARKER-XYZ policy text"},
    ).json()
    assert "UNIQUE-MARKER-XYZ" in build_system_prompt()

    # Deactivate -> disappears from prompt immediately (no redeploy)
    client.patch(f"/api/admin/kb/sections/{s['id']}", json={"active": False})
    assert "UNIQUE-MARKER-XYZ" not in build_system_prompt()
    client.patch(f"/api/admin/kb/sections/{s['id']}", json={"active": True})
    assert "UNIQUE-MARKER-XYZ" in build_system_prompt()


def test_versioning_and_revert():
    s = client.post(
        "/api/admin/kb/sections",
        json={"title": "Version me", "body": "original body"},
    ).json()
    sid = s["id"]
    upd = client.patch(f"/api/admin/kb/sections/{sid}", json={"body": "edited body"}).json()
    assert upd["version"] == 2
    versions = client.get(f"/api/admin/kb/sections/{sid}/versions").json()
    assert versions[0]["version"] == 1

    reverted = client.post(f"/api/admin/kb/sections/{sid}/revert", json={"version": 1}).json()
    assert reverted["version"] == 3
    body = client.get(f"/api/admin/kb/sections/{sid}").json()["body"]
    assert body == "original body"
    assert client.post(f"/api/admin/kb/sections/{sid}/revert", json={"version": 99}).status_code == 404

    actions = _audit_actions()
    for a in ("kb.section.create", "kb.section.update", "kb.section.revert"):
        assert a in actions
    # Audit details never contain section content
    db = SessionLocal()
    try:
        assert not any("edited body" in (r.detail or "") for r in db.query(ActivityLog).all())
    finally:
        db.close()


def test_sources_upload_sync_and_manual_placeholder():
    src = client.post(
        "/api/admin/kb/sources",
        json={"name": "HR upload", "type": "upload", "domain": "HR"},
    ).json()
    r = client.post(
        f"/api/admin/kb/sources/{src['id']}/sync",
        files={"file": ("hr.md", io.BytesIO(b"# HR doc\nSYNCED-CONTENT-123"), "text/markdown")},
    )
    assert r.status_code == 200
    section = r.json()["section"]
    assert section["source_id"] == src["id"]
    assert "SYNCED-CONTENT-123" in load_knowledge()

    # Re-sync updates the same section with a version bump
    r2 = client.post(
        f"/api/admin/kb/sources/{src['id']}/sync",
        files={"file": ("hr.md", io.BytesIO(b"SYNCED-CONTENT-456"), "text/markdown")},
    )
    assert r2.json()["section"]["id"] == section["id"]
    assert r2.json()["section"]["version"] == 2
    assert "SYNCED-CONTENT-456" in load_knowledge()
    assert "SYNCED-CONTENT-123" not in load_knowledge()

    sources = client.get("/api/admin/kb/sources").json()
    me = next(s for s in sources if s["id"] == src["id"])
    assert me["last_sync_status"] == "ok"

    # Connector types are manual placeholders
    notion = client.post(
        "/api/admin/kb/sources", json={"name": "Notion KB", "type": "notion"}
    ).json()
    assert client.post(f"/api/admin/kb/sources/{notion['id']}/sync").status_code == 400
    assert "kb.source.sync" in _audit_actions()

    # Bad uploads rejected
    assert client.post(
        f"/api/admin/kb/sources/{src['id']}/sync",
        files={"file": ("x.bin", io.BytesIO(b"\xff\xfe\x00binary"), "application/octet-stream")},
    ).status_code == 400


def test_active_and_position_changes_are_versioned_and_revertable():
    # A brand-new section that is deactivated must have history to revert to.
    s = client.post(
        "/api/admin/kb/sections", json={"title": "Toggle me", "body": "toggle body"}
    ).json()
    sid = s["id"]
    orig_position = s["position"]
    off = client.patch(f"/api/admin/kb/sections/{sid}", json={"active": False}).json()
    assert off["version"] == 2 and off["active"] is False
    versions = client.get(f"/api/admin/kb/sections/{sid}/versions").json()
    assert versions[0]["version"] == 1 and versions[0]["active"] is True
    assert versions[0]["position"] == orig_position

    moved = client.patch(f"/api/admin/kb/sections/{sid}", json={"position": 999}).json()
    assert moved["version"] == 3 and moved["position"] == 999

    reverted = client.post(f"/api/admin/kb/sections/{sid}/revert", json={"version": 1}).json()
    assert reverted["active"] is True and reverted["version"] == 4
    assert reverted["position"] == orig_position
    got = client.get(f"/api/admin/kb/sections/{sid}").json()
    assert got["body"] == "toggle body" and got["position"] == orig_position


def test_audit_never_records_query_string_values():
    # Content smuggled into query params must not appear in audit details.
    client.get("/api/admin/kb/sections?leak=SECRET-KB-CONTENT-999")
    db = SessionLocal()
    try:
        details = [r.detail or "" for r in db.query(ActivityLog).all()]
    finally:
        db.close()
    assert not any("SECRET-KB-CONTENT-999" in d for d in details)
    assert any("params=leak" in d for d in details)


def test_concurrent_edit_conflict_returns_409_and_keeps_history():
    s = client.post(
        "/api/admin/kb/sections", json={"title": "Race", "body": "v1 body"}
    ).json()
    sid = s["id"]
    # Simulate a lost-update race: another writer bumped the version between
    # this request's read and write by pre-inserting the conflicting snapshot.
    db = SessionLocal()
    try:
        from backend.models import KBSectionVersion

        db.add(KBSectionVersion(section_id=sid, version=1, title="Race", body="v1 body"))
        db.commit()
    finally:
        db.close()
    r = client.patch(f"/api/admin/kb/sections/{sid}", json={"body": "v2 body"})
    assert r.status_code == 409
    # Original content untouched
    assert client.get(f"/api/admin/kb/sections/{sid}").json()["body"] == "v1 body"


def test_section_validation():
    assert client.post("/api/admin/kb/sections", json={"title": " "}).status_code == 400
    assert client.post("/api/admin/kb/sections", json={"title": "x", "domain": "NOPE"}).status_code == 400
    assert client.get("/api/admin/kb/sections/nonexistent").status_code == 404
