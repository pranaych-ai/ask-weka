#!/bin/bash
# Post-merge setup for Ask WEKA: install deps and rebuild the frontend so the
# running app serves the merged code. Idempotent and non-interactive.
set -e

cd "$(dirname "$0")/.."

# Backend deps (fast no-op when already satisfied)
pip install -q -r requirements.txt

# Frontend: install deps and rebuild the static bundle served by FastAPI
cd frontend
npm install --no-audit --no-fund
npm run build
