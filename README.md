# Ask WEKA — Internal AI Assistant (POC)

Phase-1 POC: conversational assistant with streaming responses, per-session
history, a Claude-style sidebar of past conversations, and a file-based
knowledge base injected into the system prompt.

Architecture reference: [docs/reference-architecture.md](docs/reference-architecture.md)

## Stack

- **Backend:** Python / FastAPI, SSE streaming, SQLAlchemy
- **Frontend:** React + Vite (built statically, served by FastAPI)
- **Model:** Gemini via `google-genai`, behind a pluggable `LLMProvider`
  interface ([backend/providers/](backend/providers/)) — Claude/GPT slot in later
- **Storage:** Postgres on Replit (`DATABASE_URL`), SQLite fallback locally
- **Knowledge base:** [knowledge/kb.md](knowledge/kb.md), loaded into the system
  prompt on every request (re-read automatically when the file changes)

## Run on Replit

Follow the KeyStone deployment guide order — the lockdown steps come before the
code, not after.

1. **Submit the Gate 0 intake form** if you haven't:
   [IT SW / AI Solution & App Development Request](https://service.desk.weka.io/servicedesk/customer/portal/1407/group/1490/create/2204).
   An app that reaches production without it is ungoverned and subject to
   decommission.
2. **Create the Repl and lock it down**: new Repl → set it **Private** →
   **disable remix/forking** in workspace settings.
3. **Load the code**: upload `ask-weka-replit.zip` and unzip it at the repo root
   (`unzip ask-weka-replit.zip` in the Shell), or import the private GitHub repo
   once one exists. Confirm `.replit`, `replit.md`, and `.agents/` all landed —
   they are hidden files and easy to lose in a drag-and-drop.
4. **Connect a private GitHub repo** (Version Control pane → Connect to GitHub).
   Ask IT for branch protection on `main` and secret scanning.
5. **Add secrets** (Tools → Secrets): `GEMINI_API_KEY`. See `.env.example` for
   the full list. The key must be IT-issued, not personal.
6. **Create the database**: Tools → PostgreSQL → create. Replit sets
   `DATABASE_URL` automatically; without it the app falls back to SQLite.
7. **Hit Run.** The first run installs Python deps and builds the frontend
   (a minute or two), then serves on port 8000. Later runs skip the build.
8. **Verify the agent picked up KeyStone** — ask the Replit AI chat:
   *"What rules and skills are you following for this app, and what does the
   weka-architecture skill say about authentication?"* It should answer with
   Okta SSO, `dl-app-*` groups, and the ≤8h idle / ≤24h absolute session limits.
   If not, the files are in the wrong place — recheck step 3.
9. **Run the baseline compliance audit**: *"Read the weka-compliance-audit skill
   and run the compliance self-audit."* It is read-only and writes
   `compliance-report.md`. Early "No" verdicts are expected — that is your
   to-do list, not a blocker.

Optional env vars: `GEMINI_MODEL` (default `gemini-2.5-flash`), `LLM_PROVIDER`
(default `gemini`), `KB_PATH`.

### Rebuilding the frontend

`start.sh` only builds when `frontend/dist` is absent. After changing anything
in `frontend/src`, delete the folder and re-run:

```bash
rm -rf frontend/dist
```

## Run locally

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
GEMINI_API_KEY=... uvicorn backend.main:app --reload
```

For frontend development with hot reload, run `npm run dev` in `frontend/`
(proxies `/api` to `localhost:8000`).

## Loading the knowledge base

Replace `knowledge/kb.md` with a markdown export of your Notion pages. One
`# Page Title` heading per article with its Notion URL underneath, so the
assistant can cite sources. No restart needed — the file is re-read when it
changes.

## What's deliberately NOT here yet (later phases)

- RAG / pgvector retrieval (the KB fits in the prompt at POC scale)
- SSO + RBAC-filtered retrieval
- MCP gateway / tool calling (Jira tickets, Notion page creation)
- Admin portal, audit log, multi-model routing
