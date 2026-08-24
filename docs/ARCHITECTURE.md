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

## Secrets

- All secrets in Replit Secrets: `GEMINI_API_KEY`, `SESSION_SECRET`,
  `OKTA_ISSUER`, `OKTA_CLIENT_ID`, `OKTA_CLIENT_SECRET`.
- Rotation: minimum annually, and immediately on owner change/offboarding or
  suspected compromise.
