#!/usr/bin/env bash
set -e

echo "==> Installing Python dependencies…"
pip install -q -r requirements.txt

# Build the frontend once. To force a rebuild after changing anything in
# frontend/src, delete frontend/dist and hit Run again.
if [ ! -d frontend/dist ]; then
  echo "==> Building frontend (first run only, takes a minute)…"
  (cd frontend && npm install --no-audit --no-fund && npm run build)
else
  echo "==> Frontend already built (delete frontend/dist to rebuild)."
fi

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "!!  GEMINI_API_KEY is not set. Add it in Tools -> Secrets."
  echo "!!  The app will start, but chat requests will return a 503."
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "!!  DATABASE_URL is not set. Create a database in Tools -> PostgreSQL."
  echo "!!  Falling back to a local SQLite file (poc.db) for now."
fi

echo "==> Starting Ask WEKA on port ${PORT:-8000}…"
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
