# Ask WEKA — Architecture Notes

Internal AI assistant for WEKA employees (HR/IT questions over the internal knowledge base).

## Stack

- **Backend**: Python / FastAPI (serves the API and the built React frontend)
- **Frontend**: React 18 + Vite
- **Database**: PostgreSQL (Replit-managed; `DATABASE_URL`), SQLAlchemy ORM
- **LLM**: Gemini via `google-genai` (`GEMINI_API_KEY`, model via `GEMINI_MODEL`)
- **Auth**: Okta SSO (OIDC via authlib) — no local accounts

> Approved deviation note: this app predates the Express/TypeScript golden
> stack; it uses FastAPI + SQLAlchemy. Structure still follows the five-layer
> model (client / API+auth / services / data / external).

## RBAC design (roles ↔ Okta groups)

| Role  | Okta group (dl-app-* convention) | Access |
|-------|----------------------------------|--------|
| User  | any authenticated WEKA employee assigned to the app | Chat, own conversations, feedback |
| Admin | `dl-app-askweka-admin` (override via `OKTA_ADMIN_GROUPS`, comma-separated) | Everything above + `/admin` portal and all `/api/admin/*` routes |

- Group claims come from the OIDC `groups` scope; the Okta authorization
  server must include a **groups claim** for this app (ask IT).
- **ACTION FOR IT**: create the `dl-app-askweka` and `dl-app-askweka-admin`
  Okta groups and assign the app + members.
- **Temporary bootstrap exception**: `OKTA_ADMIN_USERS` (comma-separated
  usernames/emails) grants admin until the dl-app-* groups exist. This is a
  deliberate, documented deviation — remove the env var once IT creates the
  groups so role resolution is group-only.
- Authorization is enforced **server-side** on every `/api/admin` route
  (`require_admin` dependency); the client-side gate is UX only.
- Dev mode (Okta env vars unset) runs with an anonymous user that has admin,
  so the portal can be developed locally. Production always has Okta set.
- Sessions: ≤ 8 h idle, ≤ 24 h absolute (WEKA policy), enforced in
  `backend/auth.py`.

## Slack integration (managed notifications + DM assistant)

- **Credentials**: `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` live **only in
  Replit Secrets**. No API or UI accepts, stores, returns, masks, or logs
  credential values (IT policy — DB storage of these secrets is explicitly
  not approved).
- **ERD additions**: `slack_integration` (singleton, non-secret config +
  verified workspace/bot metadata, feature toggles, digest schedule/last-run,
  slash-command definitions) and `slack_user_prefs` (per-employee consent per
  feature + Slack user id/email resolved from their WEKA email).
- **Delivery gate**: every outbound send passes one gate
  (`slack_service.check_gate`): env credentials present + integration
  verified & admin-enabled + feature enabled + recipient consent for
  user-specific deliveries. Skips are best-effort (never break the caller)
  and leave low-noise `slack.skip` audit rows.
- **Outbound events**: ticket-created DM (Jira + Ask WEKA links), golden-run
  completion DM to the initiating admin, optional regression channel posts,
  source-sync failure alerts to opted-in initiating admins plus the optional
  admin channel, and a scheduled usage digest (aggregate counts only,
  including open tickets and unresolved topics, written by the approved
  Gemini provider).
- **Scheduler**: single-instance (POC deviation, documented) minute-tick
  asyncio task tied to the FastAPI lifecycle; digest runs are claimed with an
  atomic conditional UPDATE on `slack_integration.digest_last_run`, so
  restarts never double-post.
- **Inbound**: Slack event/command/interactivity endpoints verify the v0
  HMAC signature and replay window before any processing; DM chat honors the
  DB-managed feature config with env-credential fallback.
- **RBAC**: `/api/admin/slack/*` is admin-only (centrally audited);
  `/api/slack/prefs` requires an authenticated employee and only exposes
  administrator-enabled, opt-in features.
- **Retention**: Slack metadata/preferences are non-secret configuration and
  consent records, kept until changed by an admin/employee; deleted with the
  app database. Outbound messages contain no KB content beyond what the
  recipient already has access to (their own ticket, admin-only run summaries,
  aggregate usage counts).

## Alerting & incident response

Owner/admin alerts are posted to the configured Slack notification channel
through the central delivery gate (feature **"Security & availability
alerts"**, enabled by default once the integration is verified). Every alert
is also written to the audit log (`alert.raised`) so nothing is lost when
Slack is disabled or misconfigured (`alert.delivery_skipped` records that).

**Coverage** (`backend/alerts.py`):

| Alert | Trigger | Severity |
|-------|---------|----------|
| Application errors spiking | ≥ 5 unhandled 5xx responses within 5 min (HTTP middleware) | CRITICAL |
| AI provider failing | AI answering fails on any interface — web chat (SSE), MCP tool calls (JSON-RPC errors travel over HTTP 200), or Slack DM (background worker) | WARNING |
| Repeated sign-in failures | ≥ 5 failed Okta sign-ins within 10 min | WARNING |
| Admin rights granted | An account signs in with admin rights it did not have at its previous sign-in | CRITICAL |
| Admin rights revoked | The reverse transition | INFO |

**Content rules**: alerts never contain secrets, tokens, KB content, question
text, or request bodies. Volume alerts carry aggregate counts only; the
admin-rights alert names the account (required to act) and the source of the
grant (Okta group vs. `OKTA_ADMIN_USERS` bootstrap list).

**Deduplication**: each alert key posts at most once per 30 minutes;
suppressed repeats are counted and reported in the next post
(`alert.deduplicated` audit rows record every suppression).

**Response expectations**:
- CRITICAL — acknowledge within 1 business hour. For error spikes, check the
  deployment logs; for an unexpected admin grant, verify the Okta group
  change with IT and revoke if unapproved.
- WARNING — review same business day. Repeated sign-in failures: check the
  audit log (`login.failed`) and the Okta system log for the source.
- INFO — no action required; confirm the change was expected.

Known limitation (POC): dedup windows and failure counters are in-memory and
reset on restart — acceptable for the single-instance deployment; move to the
database if the app is ever scaled out.

## Audit trail

- `activity_log` table records who/what/when: logins, logouts, feedback
  submissions, conversation deletions, and all future admin actions.
- Events only — never secrets or raw PII values.
- Read-only viewer in the admin portal (Audit page) with filters and paging.
- **TODO (before broad prod rollout)**: forward structured logs to a central,
  tamper-resistant sink (Google Cloud Logging or Snowflake per IT standard);
  retention 12 months hot + 24 months cold.

## Data classification & retention

- Data: internal (questions/answers over internal KB), usernames/emails from
  Okta. No customer data.
- Retention: conversations and feedback kept until deleted by the user/admin;
  audit log retained per the policy above. (To be finalized with IT.)

## SDLC / governance status

- Gate 0 intake form: **confirm with owner** (register via IT SW / AI Solution
  request portal if not done).
- Okta app registration: dev + prod redirect URIs registered.
- Deployment: private visibility until SSO rollout is approved.
- Planned MCP endpoint and new integrations re-enter the framework at Gate 1.

## Environment separation (dev vs prod)

- Two environments: **development** (Replit workspace / laptop) and
  **production** (the Replit deployment; `REPLIT_DEPLOYMENT` is set).
  Detection is centralized in `backend/env.py`.
- **Database boundary (enforced in code)**: on startup the database is
  stamped with the environment that created it (`environment_marker` table).
  If the stamp doesn't match the running environment the app **refuses to
  start** — dev can never run against the production database or a copy of
  it, and production can never start against a dev database. A deliberate,
  audited re-stamp requires `ASKWEKA_ENV_MARKER_OVERRIDE=1` (never set in
  normal operation).
- **Dev data is synthetic**: `python -m scripts.seed_dev_data` seeds
  clearly-fake sample data (refuses to run in production or against a
  production-stamped database). Production data must never be copied into
  dev; if a sanitized snapshot is ever approved by IT, the override flag
  documents the exception.
- **Credentials are managed independently per environment**: workspace
  secrets serve development; the deployment's secrets pane serves
  production. Okta (dev app registration + dev redirect URI), Gemini
  (separate dev API key), Jira/Atlassian (dev OAuth app), and Slack (dev
  workspace app) must each use dev-tier credentials in the workspace and
  production credentials only in the deployment pane. No secret value ever
  lives in source control.
- **Verification without exposing secrets**: startup logs one line —
  `environment=<env> credentials_configured={okta/gemini/jira/slack: yes|no}`
  (presence booleans only) — so each environment's configuration can be
  audited from logs. `tests/test_env_separation.py` covers the boundary.

## Secrets

- All secrets in Replit Secrets: `GEMINI_API_KEY`, `SESSION_SECRET`,
  `OKTA_ISSUER`, `OKTA_CLIENT_ID`, `OKTA_CLIENT_SECRET`,
  `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`, and (when the Slack app
  is installed) `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`.
- Rotation: minimum annually, and immediately on owner change/offboarding or
  suspected compromise.
