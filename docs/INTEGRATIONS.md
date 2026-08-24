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
- Tools exposed: `ask_weka` — `{ question: string }` in, answer text with
  cited sources out. Read-only; no write-capable tools.

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

### Upgrade path
Tokens are scoped, expiring bearer credentials for now. When IT provisions an
OAuth authorization server, `/mcp` should move to the standard MCP OAuth flow
(dynamic client registration + PKCE); the token table and auth dependency in
`backend/api_keys.py` are the seam to replace.
