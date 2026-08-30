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

## Slack (DM assistant + managed notifications)

Outbound domain: `https://slack.com` (Web API) — the only Slack egress.

- **Setup**: admin portal → Slack tab generates the app manifest
  (deployment-aware URLs for `/api/slack/events`, `/api/slack/commands`,
  `/api/slack/interactions`). An admin creates the app from the manifest at
  api.slack.com, installs it, and puts the bot token + signing secret into
  Replit Secrets — credentials never pass through the app's API or UI.
- **Verification**: server-side `auth.test`; only non-secret workspace/bot
  metadata and status are stored.
- **Inbound**: all Slack endpoints verify the `v0` HMAC signature and a 5-min
  replay window. DM chat resolves the sender's WEKA email via `users.info`
  (domain-allowlisted) and reuses the normal chat pipeline + audit trail.
- **Outbound data flow**: ticket confirmations (title + Jira/Ask WEKA links)
  to the ticket owner, golden-run summaries to the initiating admin, optional
  regression posts, sync-failure alerts to the opted-in initiating admin plus
  the optional admin channel, and an AI-written usage digest (aggregate usage,
  open-ticket, and unresolved-topic counts only) to an admin-configured
  channel. Every send is gated on admin feature toggles and, for user-specific
  messages, explicit employee opt-in (Slack tab / Notifications preferences).
  Messages use Block Kit; rate-limited calls retry once.
- **Bot scopes** (minimum needed): `chat:write`, `im:read`, `im:history`,
  `im:write`, `users:read`, `users:read.email`, `files:write`, `commands`.

### Upgrade path
Tokens are scoped, expiring bearer credentials for now. When IT provisions an
OAuth authorization server, `/mcp` should move to the standard MCP OAuth flow
(dynamic client registration + PKCE); the token table and auth dependency in
`backend/api_keys.py` are the seam to replace.
