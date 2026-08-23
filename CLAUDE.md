# Internal AI Assistant (Ask WEKA) — POC

Internal AI assistant for WEKA employees: knowledge base search, ticket creation, Notion documentation, Zoom, and a general MCP connector layer, with a manager/admin control plane and a Claude-like chat experience.

Full reference architecture and build plan: [docs/reference-architecture.md](docs/reference-architecture.md).

## Foundation choices

- Multi-model abstraction layer — not locked to one provider (Claude, GPT, Gemini interchangeable via a model router).
- Notion is the primary knowledge base source of truth.
- Deployment target: Replit (Reserved VM + Scheduled Deployment for the Notion sync worker, built-in Postgres + pgvector).

## POC decisions (locked 2026-08-23)

- **Stack:** Python/FastAPI backend + React/Vite frontend, served together (FastAPI serves `frontend/dist`).
- **Model:** Gemini first (`google-genai`, `GEMINI_API_KEY`), behind the pluggable `LLMProvider` interface in `backend/providers/` — Claude/GPT slot in later.
- **Storage:** Replit Postgres via `DATABASE_URL`, SQLite fallback for local dev.
- **Knowledge base:** `knowledge/kb.md` holds the full IT + HR KB export (118 articles + 32 service-portal request types, **~67k tokens**) and is injected into the system prompt on every request. Because it is far larger than first assumed, the Gemini provider caches the system prompt server-side (Gemini context caching, keyed on a content hash, 1h TTL, graceful fallback to inline). No vector DB yet; `backend/knowledge.py` is the seam where RAG replaces the full dump when the KB outgrows the context window.
- **Real internal data is loaded.** This conflicts with the prototype-stage "synthetic data only" rule and re-enters the SDLC at Gate 1 — see the Deviations section of `docs/ARCHITECTURE.md`. Keep the Repl and repo private until IT confirms.
- Code must stay Python 3.9-compatible (local dev machine) even though Replit runs 3.12 — use `Optional[X]`, not `X | None`.

## POC scope (phase 1 of the build order in docs/reference-architecture.md)

1. Chat UI + orchestrator + single model, streaming + history. ✅ built
2. Notion sync + RAG with citations. (KB-file dump is the interim step)

Later phases (SSO/RBAC, MCP gateway, admin portal, multi-model routing) are documented but out of scope until the POC validates the core chat + RAG loop.
