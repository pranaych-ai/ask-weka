"""Central environment detection and dev/prod boundary enforcement.

Two environments exist:
  - "production": the Replit deployment (REPLIT_DEPLOYMENT is set). Uses the
    production Postgres database and production-tier credentials configured
    in the deployment's secrets pane.
  - "development": the workspace / local dev. Uses the dev database (or a
    local SQLite file) and dev-tier credentials from workspace secrets.

The database itself is stamped with the environment that first created it
(`environment_marker` table). On startup we refuse to run if the current
environment does not match the stamp — so dev can never silently point at a
production-data copy, and production can never start against a dev database.

Secrets are never read or printed here beyond presence booleans.
"""

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("askweka.env")

PRODUCTION = "production"
DEVELOPMENT = "development"

# Escape hatch for a deliberate, audited re-stamp (e.g. restoring a sanitized
# snapshot into dev). Never set this in normal operation.
_OVERRIDE_VAR = "ASKWEKA_ENV_MARKER_OVERRIDE"


def is_deployment() -> bool:
    """True when running as a Replit deployment (production)."""
    return bool(os.environ.get("REPLIT_DEPLOYMENT"))


def app_env() -> str:
    return PRODUCTION if is_deployment() else DEVELOPMENT


def credential_presence() -> dict:
    """Booleans only — which integration credential groups are configured in
    THIS environment. Used for startup logging / verification; values are
    never read into the report."""
    e = os.environ
    return {
        "okta": bool(e.get("OKTA_ISSUER") and e.get("OKTA_CLIENT_ID") and e.get("OKTA_CLIENT_SECRET")),
        "gemini": bool(e.get("GEMINI_API_KEY")),
        "jira": bool(e.get("ATLASSIAN_CLIENT_ID") and e.get("ATLASSIAN_CLIENT_SECRET")),
        "slack": bool(e.get("SLACK_BOT_TOKEN") and e.get("SLACK_SIGNING_SECRET")),
    }


def read_marker(engine: Engine) -> str | None:
    """Return the environment stamped on this database, or None."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS environment_marker ("
            "id INTEGER PRIMARY KEY, environment VARCHAR(20) NOT NULL, "
            "created_at VARCHAR(40) NOT NULL)"
        ))
        row = conn.execute(text(
            "SELECT environment FROM environment_marker WHERE id = 1"
        )).fetchone()
        return row[0] if row else None


def _stamp(engine: Engine, environment: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS environment_marker ("
            "id INTEGER PRIMARY KEY, environment VARCHAR(20) NOT NULL, "
            "created_at VARCHAR(40) NOT NULL)"
        ))
        conn.execute(text("DELETE FROM environment_marker WHERE id = 1"))
        conn.execute(
            text("INSERT INTO environment_marker (id, environment, created_at) "
                 "VALUES (1, :env, :ts)"),
            {"env": environment, "ts": now},
        )


def assert_environment_boundary(engine: Engine) -> str:
    """Enforce the dev/prod database boundary at startup.

    - An unstamped database is stamped with the current environment.
    - A stamped database must match the current environment; otherwise we
      refuse to start (fail closed) unless the audited override is set, in
      which case the database is re-stamped.

    Returns the effective environment.
    """
    env = app_env()
    marker = read_marker(engine)
    if marker is None:
        _stamp(engine, env)
        log.info("environment_marker: stamped database as %s", env)
        return env
    if marker == env:
        return env
    if os.environ.get(_OVERRIDE_VAR) == "1":
        log.warning(
            "environment_marker: OVERRIDE — re-stamping %s database as %s",
            marker, env,
        )
        _stamp(engine, env)
        return env
    raise RuntimeError(
        f"Environment boundary violation: this is the {env} environment but "
        f"the database is stamped '{marker}'. Development must never use a "
        f"production database (or a copy of one), and production must never "
        f"start against a dev database. Point DATABASE_URL at the correct "
        f"database, or — only for a deliberate, audited migration — set "
        f"{_OVERRIDE_VAR}=1 to re-stamp."
    )


def log_environment_summary() -> None:
    """One startup log line auditors can use to verify per-env credentials
    without ever exposing values."""
    presence = credential_presence()
    log.info(
        "environment=%s credentials_configured=%s",
        app_env(),
        {k: ("yes" if v else "no") for k, v in presence.items()},
    )
