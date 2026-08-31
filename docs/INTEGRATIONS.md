# Integrating with Ask WEKA

Ask WEKA can be used programmatically by other internal tools and by AI
assistants. Access is issued and revoked from the admin portal (`/admin` →
API Keys / MCP), and every credential lifecycle action is audited.

## REST API (for internal tools)

Authentication: an admin-issued API key, scope `ask`, with a per-key
rate limit (requests/minute). Keys are stored hashed and shown once.

Send the key as `Authorization: Bearer <key>` (or `X-API-Key: <key>`).

### `GET /api/v1/health`
Returns `{"ok": true, "key": "<key name>"}` — use to verify a key.

### `POST /api/v1/ask`
```json
{ "question": "How do I request a new laptop?" }
```
Optional end-user identity fields:
- `user` (string, email/username): recorded in the admin usage log but
  flagged **unverified** — it is caller-controlled and never trusted for
  access decisions.
- `user_token` (Okta-issued JWT for the end user, forwarded by the app):
  verified server-side (signature via Okta JWKS, expiry, issuer). The
  verified identity is recorded (overriding `user`) and flagged **verified**.
  An invalid or unverifiable token rejects the request with `401` — it is
  never downgraded to an unverified claim. When per-user KB permissions
  land, answers will be scoped to this verified identity.

  The token's `aud` claim must match this service: set
  `OKTA_USER_TOKEN_AUDIENCE` (comma-separated) to the accepted audience(s);
  by default the app's own Okta client ID and `api://default` are accepted.
  Tokens minted by the same Okta org for other apps are rejected.
Response:
```json
{ "answer": "…markdown answer…", "sources": ["IT Service Portal — Hardware", "…"] }
```
Errors: `401` invalid/revoked/expired key, `429` rate limit exceeded,
`400` empty or too-long question (max 4000 chars), `503` model unavailable.

Usage per key (count, last used, 24h volume) is visible on the admin API Keys
page; every request is logged (endpoint + timestamp only, never content).

## MCP endpoint (for AI assistants — Claude, etc.)

Per WEKA policy, AI clients access internal data through MCP, never directly.

- Endpoint: `https://<app-domain>/mcp` (MCP streamable HTTP transport,
  JSON-RPC 2.0 over POST)
- Auth: expiring bearer token issued from the admin MCP page
  (`Authorization: Bearer <token>`, max lifetime 90 days, revocable)
- Tools exposed: `ask_weka` — `{ question: string, user?: string,
  user_token?: string }` in, answer text with cited sources out. Read-only;
  no write-capable tools. `user` / `user_token` behave exactly as on
  `POST /api/v1/ask` above (unverified claim vs. verified Okta identity).

### Claude Desktop / Claude Code example
```json
{
  "mcpServers": {
    "ask-weka": {
      "type": "http",
      "url": "https://ask-weka.replit.app/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## Per-environment credentials

Every integration credential is configured **independently per environment**
(workspace secrets for development, the deployment secrets pane for
production) — never shared, never in source control:

| Integration | Dev-tier credential | Production credential |
|-------------|---------------------|-----------------------|
| Okta SSO | dev app registration + dev redirect URI (or unset for anonymous dev mode) | production Okta app |
| Gemini | separate dev API key | production API key |
| Jira (Atlassian OAuth) | dev OAuth app | production OAuth app |
| Slack | app installed in a dev/sandbox workspace | production workspace app |

The database is stamped per environment and the app refuses to start across
the boundary (see `docs/ARCHITECTURE.md` → Environment separation). Startup
logs report `environment=` plus presence booleans for each credential group,
so per-env configuration is verifiable without exposing any secret value.

## Slack (DM assistant + managed notifications)

Outbound domain: `https://slack.com` (Web API) — the only Slack egress.

- **Setup**: admin portal → Slack tab generates the app manifest
  (deployment-aware URLs for `/api/slack/events`, `/api/slack/commands`,
  `/api/slack/interactions`). An admin creates the app from the manifest at
  api.slack.com, installs it, and puts the bot token + signing secret into
  Replit Secrets — credentials never pass through the app's API or UI.
- **Verification**: server-side `auth.test`; only non-secret workspace/bot
  metadata and status are stored.
- **Inbound (trust boundary)**: three public endpoints — `/api/slack/events`,
  `/api/slack/commands`, `/api/slack/interactions`. Slack is untrusted: each
  endpoint verifies the `v0` HMAC signature over the **raw request body** and
  a 5-min replay window *before parsing*, pins the `api_app_id` to the app ID
  captured at verification time (fail-closed when unpinned), acknowledges
  within Slack's 3-second window, and does all real work asynchronously.
  DM chat and channel mentions are independently feature-gated by the admin.
  All flows resolve the sender's WEKA email via `users.info`
  (domain-allowlisted) and reuse the normal chat pipeline, AI safety gates,
  and audit trail (metadata only — no question text). Mentions get a fresh
  single-exchange context and a threaded reply; when DM chat is disabled the
  bot points at the web app at most once per employee per day. Answers are
  Slack-formatted with an "Open Ask WEKA" button, with a temporary eyes
  reaction while generating.
- **Slash commands**: admin-defined commands may carry a knowledge **domain**
  (`IT`/`HR`/all) and administrator-authored **instructions** (non-secret,
  ≤1000 chars) that scope/constrain the same protected pipeline used by web
  chat. Command answers are ephemeral by default and offer **Post to
  channel**, which republishes only the already-approved stored answer after
  re-verifying the clicking employee's ownership.
- **Interactive actions**: every answer carries thumbs-up/down buttons and,
  for the employee's own conversations, **Create ticket**. Button values are
  HMAC-signed references (message/conversation id only — never content or
  identity); the click handler re-resolves the clicker's email via
  `users.info`, re-verifies the signature, and re-checks ownership
  server-side. Feedback writes through the shared web feedback rules
  (one rating per employee/message, anonymous keyed rater pseudonym at rest,
  domain/source context) so Slack ratings appear in the existing QA views.
  Create ticket opens a review/edit/approve modal (AI draft is best-effort
  and safety-gated); explicit submission files through the shared solve-first
  core as the employee's own Jira connection, or the employee gets a safe
  link to connect Jira in Ask WEKA first. Nothing is ever filed
  automatically.
- **Thread/channel summaries** (gate order: admin feature flag → employee
  opt-in): mentioning the bot with "catch me up"/"summarize"/"tl;dr" fetches
  at most the current thread, or the current channel's last 24 h / 200
  messages (`conversations.replies`/`history` — the bot can only read
  channels it was invited to). The transcript goes through the input screen,
  approved Gemini pipeline, and output safety gate, and the reply (key
  points / decisions / action items with owners) lands in the originating
  thread. **Neither fetched Slack messages nor the summary are ever written
  to the application database** — the audit row records scope + message
  count only.
- **App Home**: opening the bot's Home tab publishes a user-specific view
  (what Ask WEKA is, admin-enabled features, the employee's own opt-in
  status, links to Ask WEKA/preferences). No conversation content.
- **Link unfurls**: only `https` links on this deployment's own domain get a
  static, metadata-only card. Conversation titles/questions/answers are never
  fetched or exposed, and foreign URLs are never unfurled.
- **Outbound data flow**: ticket confirmations (title + Jira/Ask WEKA links)
  to the ticket owner, golden-run summaries to the initiating admin, optional
  regression posts, sync-failure alerts to the opted-in initiating admin plus
  the optional admin channel, and an AI-written usage digest (aggregate usage,
  open-ticket, and unresolved-topic counts only) to an admin-configured
  channel. Every send is gated on admin feature toggles and, for user-specific
  messages, explicit employee opt-in (Slack tab / Notifications preferences).
  Messages use Block Kit; rate-limited calls retry once.
- **Bot scopes** (minimum needed): `chat:write`, `im:read`, `im:history`,
  `im:write`, `users:read`, `users:read.email`, `files:write`; plus, only
  when the matching feature is enabled: `commands` (slash commands),
  `app_mentions:read` (mentions/summaries), `channels:history` +
  `groups:history` (summaries), `links:read` + `links:write` (unfurls). The
  generated manifest emits exactly the events/scopes/home-tab/unfurl-domain
  entries for enabled capabilities — reinstall it after changing features or
  commands.
- **Production review**: enabling summaries grants channel-history read
  scopes — call this out in the IT review; content is processed in memory
  only and never persisted.

### Upgrade path
Tokens are scoped, expiring bearer credentials for now. When IT provisions an
OAuth authorization server, `/mcp` should move to the standard MCP OAuth flow
(dynamic client registration + PKCE); the token table and auth dependency in
`backend/api_keys.py` are the seam to replace.
