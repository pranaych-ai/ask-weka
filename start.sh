#!/usr/bin/env bash
set -e

# Skip pip install when requirements.txt hasn't changed since the last
# successful install (same stamp approach as the frontend build below).
PIP_STAMP=.pip-install-stamp
if [ ! -f "$PIP_STAMP" ] || [ requirements.txt -nt "$PIP_STAMP" ]; then
  echo "==> Installing Python dependencies…"
  pip install -q -r requirements.txt
  touch "$PIP_STAMP"
else
  echo "==> Python dependencies up to date, skipping pip install."
fi

# Build the frontend when dist is missing or any source/config file is newer
# than the last build, so restarts always serve the latest UI.
BUILD_STAMP=frontend/dist/.build-stamp
needs_build=0
if [ ! -d frontend/dist ] || [ ! -f "$BUILD_STAMP" ]; then
  needs_build=1
elif [ -n "$(find frontend/src frontend/index.html frontend/package.json \
    frontend/vite.config.* frontend/tsconfig*.json frontend/tailwind.config.* \
    frontend/postcss.config.* -type f -newer "$BUILD_STAMP" -print -quit 2>/dev/null)" ]; then
  needs_build=1
fi

if [ "$needs_build" = "1" ]; then
  echo "==> Building frontend (sources changed or first run)…"
  # Skip npm install when node_modules is already in sync with package-lock.json.
  needs_install=0
  if [ ! -d frontend/node_modules ] || [ ! -f frontend/node_modules/.package-lock.json ]; then
    needs_install=1
  elif [ frontend/package.json -nt frontend/node_modules/.package-lock.json ] || \
       [ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]; then
    needs_install=1
  fi
  if [ "$needs_install" = "1" ]; then
    echo "==> Installing frontend dependencies…"
    (cd frontend && npm install --no-audit --no-fund)
  else
    echo "==> Frontend dependencies up to date, skipping npm install."
  fi
  (cd frontend && npm run build)
  touch "$BUILD_STAMP"
else
  echo "==> Frontend up to date."
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
