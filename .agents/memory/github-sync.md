---
name: GitHub sync
description: How and when this project syncs to GitHub
---
Replit checkpoints commit locally only — nothing pushes to GitHub automatically. The user wants dev and main on github.com/pranaych-ai/ask-weka kept current.

**Why:** the remote originally held an unrelated one-commit "Initial import"; local history was force-pushed over it (Aug 26, 2026). User expects GitHub to mirror the latest code.
**How to apply:** at the end of each work session, run `bash scripts/sync-github.sh` (fetches the GitHub connector token at runtime; pushes dev -> dev and dev -> main). Don't store tokens in git config.
