---
name: weka-architecture
description: The WEKA golden architecture for internal apps built on Replit. Use this skill whenever scaffolding a new app, adding a new layer, service, integration, auth mechanism, database, or AI feature, or whenever making any structural decision about the codebase. Always consult it before choosing a framework, library, auth approach, or data store — even if the user does not mention "architecture."
version: 1.1.0
owner: WEKA IT (#it-help on Slack)
---

# WEKA Golden Architecture — Internal Apps on Replit

You are building an internal WEKA application. WEKA has a standard architecture every internal app must follow. Deviations are allowed only when the user explicitly confirms IT has approved them — record any approved deviation in docs/ARCHITECTURE.md.

This skill is preventive: every rule here maps to a check in the weka-compliance-audit skill. Build to this spec and the audit passes.

## Before you build — the SDLC gates (new in v1.1)

Every Replit solution that touches WEKA systems, data, or integrations is governed by the **BA + AI SDLC Process Framework**. Any solution that reaches production without completing it is considered ungoverned and subject to decommission.

1. **Gate 0 — intake.** Before meaningful build work, the app must be registered with IT via the IT SW / AI Solution & App Development Request form: https://service.desk.weka.io/servicedesk/customer/portal/1407/group/1490/create/2204 (portal 1407 → group 1490 → request type 2204). If the user hasn't done this, tell them at the start of the project and note it in docs/ARCHITECTURE.md.
2. **Gate 1 deliverables.** Before prototyping is formally cleared, the following must exist (create stubs in docs/ and remind the user to complete them):
   - Architecture diagram in Lucid (this app's own diagram; the Horizon diagram is the reference example)
   - **ERD** (conceptual or logical: master data, transaction data, app & security metadata)
   - **Integrations & data-flow diagram**
   - **RBAC design doc** (roles, feature permissions, row-level data authorizations)
3. **Okta app registration.** The app cannot go to production without an approved Okta application registration (IT opens the ITSUPPORT ticket). Flag this to the user before the first prod deploy.
4. **Re-entry triggers.** Any new integration, new data type / classification change, significant user-base expansion, hosting migration, or major core-dependency version change re-enters the framework at Gate 1. Tell the user when their request triggers this.
5. **Prototype stage rules.** Until Gate 3 clearance: Replit project private, synthetic/anonymized data only, no production credentials anywhere, no external sharing without IT awareness.

## The five layers

Every WEKA app is structured as five layers. Keep the boundaries explicit in the folder structure.

clients → API + auth → application services → data layer → external systems

### 1. Clients (client/)

- **Web dashboard**: React 18 + Vite + TypeScript. Tailwind for styling.
- **AI / MCP clients** (optional): if the app exposes data to Claude or other AI assistants, do it through an MCP endpoint on the API layer — never by giving AI clients direct database access.
- **Share / export views** (optional): board- or leadership-facing views are view-only exports. No mutating actions on share surfaces.

### 2. API + auth (server/)

- **Framework**: Express + TypeScript.
- **Authentication**: Okta SSO via OIDC. **Never** create local username/password accounts, signup routes, or password tables. (Audit 1.1)
- **MFA**: the app inherits WEKA's Okta MFA policy (Okta Verify). Never implement anything that bypasses MFA at the app layer. (Audit 1.6)
- **Authorization**: role-based, enforced **server-side** on every route. Client-side checks are UX only, never security. (Audit 1.2)
- **Access provisioning**: access is driven by **Okta groups** using the `dl-app-<appname>` convention (e.g. dl-app-horizon, dl-app-horizon-admin) — never individual user assignments hardcoded in the app. Groups map to the roles in the RBAC design doc. Ask IT to create the groups. (Audit 1.5)
- **Admin separation**: production admin access is a distinct role/group from dev admin access, limited to named people. (Audit 1.4)
- **Deprovisioning**: revoking a user in Okta must immediately remove app access (no long-lived app-side sessions surviving Okta revocation). This gets tested before prod. (Audit 1.7)
- **Sessions/tokens**: explicit expiry, **≤ 8 hours idle and ≤ 24 hours absolute**, per WEKA policy. (Audit 1.3)
- **MCP endpoints** (if any): protected with OAuth; scope tokens to the minimum needed, with expiry.
- **API protection** (every route): no unauthenticated endpoints that return or mutate WEKA data; auth enforced at the API layer, not just the UI (must hold up against direct curl/Postman calls); **rate limiting** on externally reachable endpoints; **CORS restricted to known origins** (never wildcard `*` for WEKA data); **HSTS header** enabled; all inputs validated against the shared Zod schema. (Audit 5.5, 5.6)

### 3. Application services (server/services/)

Organize domain logic into isolated service modules. Standard patterns:

- **Pull engine**: all syncs from external systems (NetSuite, HiBob, Slack, Notion, FX providers, etc.) go through one sync module that writes to staging tables — never straight into production tables. If the app needs access to a **production core system** (write access or sensitive reads), route it through an IT-managed integration layer / IT-provisioned scoped credentials — never a direct Replit → production-DB connection. Confirm the approach with IT. (Audit 5.7)
- **Guardrails**: validation modules that prevent bad writes (e.g. double-counting, duplicate ingestion). Validate at the service boundary with the shared Zod schema.
- **Audit service**: every mutation records who/what/when to the audit trail; versioned records with rollback where the domain needs it.
- **Jobs**: scheduled/background work lives in a dedicated jobs module with logging and failure alerting (Slack webhook or email). (Audit 7.3)
- **On-demand refresh**: syncs are user-triggered ("one button") or scheduled — with sync-health visibility. No silent background mutation of production data.

### 4. Data layer (server/db/ + shared/)

- **Database**: PostgreSQL on Neon, accessed exclusively through Drizzle ORM. No raw SQL string concatenation. Note in docs/ARCHITECTURE.md that encryption at rest is provided by Neon and confirmed by IT. (Audit 6.8)
- **Shared schema**: a single shared/schema.ts defines Drizzle tables and Zod validators; client and server both import types from here. One source of truth for shapes.
- **Staging tables**: raw external pulls land in staging_* tables, then get transformed into domain tables. Keeps external mess out of core data.
- **Audit trail**: an activity_log (or equivalent) table for logins, admin actions, and data mutations. Log events, never secrets or raw PII values. (Audit 7.1, 7.2)
- **Central log forwarding**: the in-app audit table is not sufficient on its own for production. Forward structured logs (auth events, admin actions, errors) to a **central, tamper-resistant sink)** — Google Cloud Logging or Snowflake per IT standard — so users of the app cannot modify or delete them. Retention: 12 months hot + 24 months cold per the SDLC checklist (90 days is the absolute minimum; IT is reconciling the two policies — never go below the stricter target without IT sign-off. (Audit 7.5, 7.6)
- **Alerting**: automated alerts for failures AND anomalous access (repeated auth failures, privilege escalation) to the app's Slack alert channel. (Audit 7.3, 7.7)
- **Retention & deletion**: define data retention and deletion rules for the app's own data in docs/ARCHITECTURE.md (what's kept, how long, how it's deleted). (Audit 3.4)

### 5. External systems

- Every integration uses a **service account created by IT** — never a personal account's credentials. If no service account exists yet, tell the user to request one from IT before wiring the integration. (Audit 2.2)
- API keys/service accounts are **scoped to the minimum permissions** the app needs — wildcard/admin-scoped keys are not permitted. (Audit 2.5)
- Keep a comment block or docs/INTEGRATIONS.md listing every outbound domain the app calls. (Audit 5.1)
- **External data egress**: sending WEKA data to any external service requires that service to be on WEKA's approved-services list. If it isn't, stop and tell the user to confirm with IT before wiring it. (Audit 5.8)
- HTTPS only for all outbound calls; http:// is allowed for localhost only. TLS 1.2+ everywhere. (Audit 5.4)
- Inbound webhooks must verify signatures/HMAC before processing. (Audit 5.3)
- **AI provider**: the approved LLM for in-app AI features is **Gemini**, accessed via an IT-issued service account and API key from the Gemini API issuance process (naming: gemini-<username>-<team>). Claude access is via MCP endpoints, not embedded API keys. Any other provider requires explicit IT approval recorded in docs/ARCHITECTURE.md. (Audit 6.9)

## Non-negotiable rules (apply to every file you write)

1. **Secrets**: only via Replit Secrets / environment variables. Never hardcode keys, tokens, or passwords; never commit .env. (Audit 2.1)
2. **Per-environment credentials**: distinct env var sets for dev vs prod (e.g. SLACK_TOKEN_DEV / SLACK_TOKEN_PROD or environment-scoped config). Never share keys across environments. (Audit 2.3, 4.2)
3. **Secret hygiene**: rotation minimum annually, and immediately on owner change, offboarding, or suspected compromise — record the rotation plan in docs/ARCHITECTURE.md. Enable secret scanning (GitHub Advanced Security secret scanning, or Gitleaks in CI) on the repo. (Audit 2.4, 2.6)
4. **Environment separation**: dev and prod are separate deployments. Dev uses fake/sandbox data — never a copy of real customer or employee PII. (Audit 3.2, 3.3, 4.1)
5. **Test accounts**: UAT/test users are dedicated Okta accounts with the `test-` prefix, locked out of production, and clearly distinguishable in all logs. (Audit 4.6)
6. **Version control & promotion**: git with a GitHub remote from day one; **branch protection on main**; **a human PR review is required before any production deploy**; deploy permissions limited to IT + the named owner. (Audit 4.3, 4.4, 4.5)
7. **Workspace visibility**: the Repl is **private with remix/forking disabled**; GitHub repo is private. Only named collaborators with a business need get editor access. (Audit 6.3)
8. **AI features are isolated**: any LLM/embedding/vector functionality lives in its own clearly bounded module (server/services/ai/), behind its own routes — never woven through core deterministic logic. When you add the first AI feature, tell the user it triggers IT's elevated review (data exposure, prompt injection, hallucination risk, output logging) and needs evaluation/red-team checks before prod. (Audit 6.4–6.6)
9. **Human review of AI-generated code**: a person (not just the AI agent) must review code before it goes to production — this is what the PR gate in rule 6 enforces. Deterministic paths get standard test coverage. (Audit 6.2, 6.7)
10. **Dependencies**: run npm audit after adding packages; don't ship with critical/high findings. (Audit 6.1)
11. **Access gating**: the app must require login before anything loads, or be deployed privately (private deployment, Cloudflare Access, or IP allowlist where supported). Never fully public with no gate. (Audit 5.2)

## When scaffolding a new app

1. Confirm the Gate 0 intake form has been submitted (see "Before you build"). If not, tell the user and record the status in docs/ARCHITECTURE.md.
2. Create the five-layer folder structure above.
3. Wire Okta OIDC auth (with the session limits above) and RBAC middleware before building any feature. Ask IT for the dl-app-* Okta groups.
4. Create shared/schema.ts and the audit-trail table in the first migration; wire central log forwarding before prod.
5. Create docs/ARCHITECTURE.md containing: link to the app's Lucid architecture diagram, link/stub for the ERD, integrations & data-flow diagram, and RBAC design doc, the app owner + backup owner, data classification (public / internal / confidential / PII), criticality rating and graduation criteria (conditions under which the app moves off Replit to enterprise infra — e.g. confidential/restricted data, large or external user base, write access to core systems, SLA-bound uptime, SOC 2/GDPR/ISO scope), the secret rotation plan, data retention & deletion rules, and any IT-approved deviations from this skill.
6. Remind the user: before the first production deploy — Okta app registration complete, PR review done, weka-compliance-audit skill run, and the report linked in the Internal App Registry in Notion.
