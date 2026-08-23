# Ask WEKA — WEKA Internal Application
<!-- This file is the app's replit.md — the agent rules installed at the repo root.
     v1.1 · Owner: WEKA IT (#it-help) · Formerly distributed as "keystone-audit.md" (misnamed). -->

## What this app is

Ask WEKA is an internal AI assistant for WEKA employees: a conversational chat UI (streaming responses, per-conversation history) that answers questions from the internal Notion-sourced knowledge base with citations, with ticket creation and other MCP tool integrations planned in later phases. Data classification: **internal** (internal KB articles and employee questions; no customer data or PII intended — dev uses placeholder KB content only).

- **Owner**: Pranay Chandur · **Backup owner**: [TODO — name one]
- **Environment**: this workspace is **dev**. Prod is a separate deployment.
- **Criticality**: low — POC; nothing breaks if it is down for a day.
- **SDLC status**: Gate 0 intake **NOT yet submitted** ([submit here](https://service.desk.weka.io/servicedesk/customer/portal/1407/group/1490/create/2204)) · Okta app registration not opened
- **Architecture diagram**: [TODO — Lucid link] · **ERD**: [TODO] · **Data-flow diagram**: [TODO] · **RBAC design**: [TODO] (see docs/ARCHITECTURE.md)

## Rules for the Agent — always follow

1. This is a WEKA internal app. Follow the **weka-architecture** skill (/.agents/skills/weka-architecture/) for all scaffolding, structural decisions, integrations, and technology choices. Consult it before choosing any framework, auth approach, or data store.
2. This app is governed by the **BA + AI SDLC framework**. If the Gate 0 intake form has not been submitted (https://service.desk.weka.io/servicedesk/customer/portal/1407/group/1490/create/2204), remind the user before doing significant build work. Any new integration, new data type, or major user-base expansion re-enters the framework at Gate 1 — tell the user when their request triggers this.
3. Auth is **Okta SSO only** — never create local accounts, signup routes, or password storage. Sessions expire at **≤8 hours idle / ≤24 hours absolute**. Access is provisioned through **dl-app-\* Okta groups**, never hardcoded user lists. MFA is inherited from Okta and never bypassed at the app layer.
4. Secrets come **only from Replit Secrets / environment variables** — never hardcode credentials and never commit .env files. All integration credentials are **IT-issued service accounts** scoped to minimum permissions; rotation is at least annual and immediate on owner change or offboarding.
5. Use **separate credentials per environment** (dev vs prod). Dev uses fake/sandbox data only — never real customer or employee PII. Test users are Okta accounts with the `test-` prefix and cannot reach production.
6. Keep this Repl **private with remix disabled** and the GitHub repo private, with **branch protection on main**. A **human must review changes (PR review) before any production deploy** — this includes AI-generated code. Deploy rights stay limited to IT + the named owner.
7. Every API route that returns or mutates WEKA data requires authentication at the API layer (not just the UI), with rate limiting, restricted CORS (no wildcard), and HSTS. HTTPS/TLS 1.2+ everywhere; webhooks verify signatures.
8. Send WEKA data only to services on **WEKA's approved-services list**. If an integration target isn't on it, stop and confirm with IT (#it-help) first.
9. Any AI/LLM feature must be isolated in its own module (server/services/ai/), use **Gemini via an IT-issued key** (or Claude via MCP) — no other providers without recorded IT approval — and be flagged to the user as requiring IT's **elevated review** plus evaluation/red-team checks before prod.
10. Logging: record logins, admin actions, and data mutations to the audit trail (never secrets or PII values) and forward structured logs to the central sink per the weka-architecture skill. Alert on failures and anomalous access.
11. Before any production deployment, and after adding any new integration or AI feature, run the **weka-compliance-audit** skill (/.agents/skills/weka-compliance-audit/). It is read-only and produces compliance-report.md. Never edit that report by hand.
12. If the user asks for something that conflicts with these rules, explain the conflict and suggest they confirm with WEKA IT (#it-help) before proceeding.

## Current development phase

Phase 1 POC (2026-08-23): streaming chat + conversation history + file-based knowledge base (`knowledge/kb.md` injected into the system prompt) is built and working. Model is Gemini behind a pluggable provider interface. **Known deviations from the weka-architecture skill are recorded in docs/ARCHITECTURE.md and are pending IT approval** — notably Python/FastAPI instead of Express/TypeScript, and no Okta SSO yet (required before anything beyond private dev). Next: Notion KB export, then Okta OIDC + RBAC before any wider rollout.
