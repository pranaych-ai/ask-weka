#!/usr/bin/env bash
# Push the current dev branch to GitHub dev + main.
# Uses the Replit GitHub connection for auth — no stored tokens.
set -euo pipefail

REPO="github.com/pranaych-ai/ask-weka.git"

if [ -z "${REPLIT_CONNECTORS_HOSTNAME:-}" ] || [ -z "${REPL_IDENTITY:-}" ]; then
  echo "Error: must run inside the Replit workspace (connector env missing)." >&2
  exit 1
fi

TOKEN=$(curl -fsS "https://${REPLIT_CONNECTORS_HOSTNAME}/api/v2/connection?include_secrets=true&connector_names=github" \
  -H "Accept: application/json" -H "X_REPLIT_TOKEN: repl ${REPL_IDENTITY}" \
  | python3 -c "import sys,json; c=json.load(sys.stdin)['items'][0]['settings']; print(c.get('access_token') or c['oauth']['credentials']['access_token'])")

URL="https://x-access-token:${TOKEN}@${REPO}"

git push "$URL" dev:dev
git push "$URL" dev:main
echo "Synced dev -> GitHub dev and main."
