"""Tests for the dev/prod environment boundary (backend/env.py).

Covers: marker stamping, cross-environment refusal in both directions,
the audited override, credential presence reporting (booleans only), and
the seed script's production refusal.
"""

import os

import pytest
from sqlalchemy import create_engine

from backend import env as env_mod
from backend.env import (
    DEVELOPMENT,
    PRODUCTION,
    app_env,
    assert_environment_boundary,
    credential_presence,
    is_deployment,
    read_marker,
)


@pytest.fixture()
def engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path}/boundary.db")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
    monkeypatch.delenv("ASKWEKA_ENV_MARKER_OVERRIDE", raising=False)


def test_dev_by_default(monkeypatch):
    assert not is_deployment()
    assert app_env() == DEVELOPMENT


def test_production_detected(monkeypatch):
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    assert is_deployment()
    assert app_env() == PRODUCTION


def test_fresh_db_gets_stamped(engine):
    assert read_marker(engine) is None
    assert assert_environment_boundary(engine) == DEVELOPMENT
    assert read_marker(engine) == DEVELOPMENT


def test_matching_marker_passes(engine):
    assert_environment_boundary(engine)
    # Second startup in same environment is fine.
    assert assert_environment_boundary(engine) == DEVELOPMENT


def test_dev_refuses_production_database(engine, monkeypatch):
    # Stamp as production, then start in dev -> refuse.
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    assert_environment_boundary(engine)
    monkeypatch.delenv("REPLIT_DEPLOYMENT")
    with pytest.raises(RuntimeError, match="boundary violation"):
        assert_environment_boundary(engine)
    # Marker untouched by the failed start.
    assert read_marker(engine) == PRODUCTION


def test_production_refuses_dev_database(engine, monkeypatch):
    assert_environment_boundary(engine)  # stamps development
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    with pytest.raises(RuntimeError, match="boundary violation"):
        assert_environment_boundary(engine)


def test_audited_override_restamps(engine, monkeypatch):
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    assert_environment_boundary(engine)
    monkeypatch.delenv("REPLIT_DEPLOYMENT")
    monkeypatch.setenv("ASKWEKA_ENV_MARKER_OVERRIDE", "1")
    assert assert_environment_boundary(engine) == DEVELOPMENT
    assert read_marker(engine) == DEVELOPMENT


def test_credential_presence_booleans_only(monkeypatch):
    for k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET",
              "GEMINI_API_KEY", "ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET",
              "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-dev-key")
    report = credential_presence()
    assert report == {"okta": False, "gemini": True, "jira": False, "slack": False}
    # No secret values ever appear in the report.
    assert "fake-dev-key" not in str(report)


def test_seed_script_refuses_production(monkeypatch, capsys):
    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    from scripts.seed_dev_data import main
    assert main() == 1
    assert "REFUSED" in capsys.readouterr().out


def test_seed_script_refuses_prod_stamped_db(monkeypatch, tmp_path, capsys):
    # App DB (from conftest) is dev; simulate a prod-stamped DB instead.
    from backend.db import engine as app_engine
    # Stamp the app DB as production temporarily.
    env_mod._stamp(app_engine, PRODUCTION)
    try:
        from scripts.seed_dev_data import main
        assert main() == 1
        assert "stamped 'production'" in capsys.readouterr().out
    finally:
        env_mod._stamp(app_engine, DEVELOPMENT)


def test_seed_script_seeds_synthetic_data():
    from backend.db import SessionLocal
    from backend.models import Conversation
    from scripts.seed_dev_data import main

    from backend.models import GoldenQuestion

    assert main() == 0
    assert main() == 0  # idempotent
    db = SessionLocal()
    try:
        rows = db.query(Conversation).filter(
            Conversation.username.like("%@example.test")
        ).all()
        assert len(rows) == 2
        seeded_gq = db.query(GoldenQuestion).filter(
            GoldenQuestion.created_by == "seed_dev_data"
        ).all()
        assert len(seeded_gq) == 2
        # Clean up so other test modules see an unchanged dataset.
        for conv in rows:
            db.delete(conv)
        for gq in seeded_gq:
            db.delete(gq)
        db.commit()
    finally:
        db.close()
