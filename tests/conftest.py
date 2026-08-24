"""Shared test setup: force dev mode and a FRESH throwaway SQLite DB per run.

This runs before any test module imports the backend, so the DB file is
deleted here to keep the suite repeatable.
"""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)

_DB_FILE = "/tmp/askweka_pytest.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"

if os.path.exists(_DB_FILE):
    os.remove(_DB_FILE)
