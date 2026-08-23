---
name: weka-replit-compliance-audit
description: Self-audit a Replit-hosted internal app against the WEKA IT, Security & Compliance Review Checklist and the BA + AI SDLC requirements. Run in dev mode. Read-only - never modifies code. Produces compliance-report.md with a Yes/No/IT-VERIFY verdict per requirement.
version: 1.1.0
owner: WEKA IT (#it-help on Slack)
---

# WEKA Replit Compliance Self-Audit

You are performing a READ-ONLY compliance audit of this Replit application.

## Ground rules

1. NEVER modify, create, or delete any file except the final report (compliance-report.md at the repo root).
2. Check EVERY requirement below, in order. Never skip or stop early.
3. Each requirement gets exactly one verdict: Yes, No, or IT-VERIFY.
4. A failed check is simply recorded as "No". Do not block, do not mark the audit as failed, just record and continue.
5. Every verdict needs evidence: a file path, a command output summary, or the user's exact answer.
6. Three check types:
   - AUTO: you verify it yourself by inspecting the workspace. Follow the detection instructions.
   - ASK: you cannot know this. Ask the user the exact question given, in plain language. Record their answer verbatim as evidence.
   - IT-VERIFY: outside this workspace. Do NOT ask the user, do NOT guess. Record verdict "IT-VERIFY" with evidence "Requires IT confirmation".
7. Ask all ASK questions in ONE batch at the start (after a quick workspace scan), so the user is not interrupted repeatedly. Keep questions friendly and jargon-free.
8. If the user is unsure about an ASK question, record "Unsure - [their words]" and set verdict to No.

## Requirements

### Module 1 - Identity & Access

1.1 (AUTO) SSO login, no local accounts. Search the code for Okta/OIDC/SAML libraries or middleware (e.g., okta, oidc-client, passport-openidconnect, authlib). Also search for local username/password tables or signup routes. Yes = SSO present AND no local account system. No = local accounts found or no auth at all.

1.2 (AUTO) Role-based authorization. Look for role/permission checks in code (role fields, permission decorators, admin route guards). Yes = roles enforced server-side. No = every logged-in user can do everything.

1.3 (AUTO) Session timeout / token expiry within WEKA policy. Look for session or JWT expiry settings. Yes = expiry explicitly set AND within limits (≤8 hours idle, ≤24 hours absolute). No = none found or exceeds limits (record the configured values as evidence).

1.4 (ASK) "Is admin access to the PRODUCTION app limited to specific people, separate from the dev version? Who has it?"

1.5 (ASK) "Is access to this app granted through Okta groups (names like dl-app-something), or are users added one by one inside the app?" Yes = Okta groups. No = individual/in-app assignment.

1.6 (AUTO) No MFA bypass. Scan for any code that skips, disables, or works around Okta authentication or MFA (backdoor routes, auth-bypass flags, hardcoded allowlists that skip login). Yes = none found. No = list them.

1.7 (IT-VERIFY) Deprovisioning tested: revoking a user in Okta immediately removes app access.

### Module 2 - Secrets & Credentials

2.1 (AUTO) No hardcoded secrets. Scan all source files and any committed .env files for API keys, tokens, passwords (patterns like sk-, xoxb-, AKIA, AIza, client_secret=, password=, PRIVATE KEY blocks). Yes = none found and secrets are read from environment variables / Replit Secrets. No = any hardcoded secret found (list file paths, NEVER print the secret value itself).

2.2 (ASK) "Do the keys and credentials this app uses belong to a service account (a non-human account IT created), or to a person's own account?" Yes = service account. No = personal.

2.3 (AUTO) Per-environment keys. Check whether config distinguishes dev vs prod credentials (different env var names, environment-based config files). Yes = separate. No = same keys everywhere or cannot distinguish.

2.4 (ASK) "Is there a plan to rotate (replace) this app's keys at least once a year, and immediately if the owner changes or leaves?" Yes = plan exists (documented in docs/ARCHITECTURE.md counts). No = no plan. (Whether rotation actually happens: IT-VERIFY note.)

2.5 (AUTO) Keys scoped to minimum permissions. Where determinable from code/config (API scopes requested, OAuth scopes listed), check that credentials are not admin/wildcard scoped. Yes = minimal scopes or not determinable-but-documented. No = admin/wildcard scopes found. If truly not determinable, mark IT-VERIFY.

2.6 (AUTO) Secret scanning enabled. Look for Gitleaks config (.gitleaks.toml, CI step) or evidence of GitHub Advanced Security. Yes = present. No = absent. If repo settings not visible from workspace, mark IT-VERIFY with a note.

### Module 3 - Data Handling

3.1 (ASK) "What kind of data does this app handle? Public info, internal company info, confidential (pricing/deals), or personal data about people (PII)?" Record answer; verdict Yes if user can classify it, No if unknown.

3.2 (ASK) "In the DEV version, are you using fake/sample/sandbox data, or a copy of real production data?" Yes = fake/sandbox. No = real data.

3.3 (ASK) "Does the dev version contain any real customer or employee personal information?" Yes verdict = user answers no it does not. No verdict = it does.

3.4 (AUTO) Retention & deletion rules documented. Check docs/ARCHITECTURE.md (or equivalent) for data retention and deletion rules. Yes = documented. No = absent. (Whether the rules meet WEKA policy: IT-VERIFY note.)

3.5 (AUTO) External data egress to approved services only. Cross-check the outbound inventory (5.1) and flag any external service receiving WEKA data. Verdict Yes if the list was compiled and flagged for IT; include the list. IT confirms approved-services status (record IT-VERIFY note for confirmation).

### Module 4 - Environment Separation

4.1 (AUTO) Separate dev and prod deployments. Check for deployment config (.replit, replit.nix, deployment settings) and ask the workspace context. If unclear from files, convert to ASK: "Is there one Replit app for dev and a separate deployment for prod, or is it all one?" Yes = separate. No = single shared.

4.2 (AUTO) Separate integration credentials per environment. Check env var naming / config for dev vs prod variants of integration keys (Slack, Notion, etc.). Yes = separate. No = shared.

4.3 (AUTO) Version control with GitHub remote. Check for a .git directory and GitHub remote. Yes = git repo with GitHub remote. No = no version control. (Branch protection on main: IT-VERIFY note.)

4.4 (ASK) "Before changes go to production, does a person review them first (a pull request review)? This includes code the AI wrote." Yes = always. No = no or sometimes.

4.5 (ASK) "Who can deploy this app to production? Is it limited to IT plus the named owner?" Yes = limited. No = broader or unknown.

4.6 (AUTO) Test accounts follow the test- prefix. Search code, seed data, and docs for test user references. Yes = test accounts use the test- prefix (or no test accounts exist - state that as evidence). No = test accounts exist without the convention.

### Module 5 - Network & Integration Security

5.1 (AUTO) Outbound call inventory. List every external API/MCP/webhook endpoint found in the code (domains only). This is informational: verdict Yes if you could compile the list, and include the list as evidence.

5.2 (ASK) "Is the app's URL open to the whole internet, or restricted (private deployment, VPN, IP allowlist, Cloudflare Access, or login before anything loads)?" Yes = restricted or login-gated. No = fully public with no gate.

5.3 (AUTO) Webhook signature validation. If the app receives webhooks, check the handlers for signature/HMAC verification. Yes = validated. No = webhooks accepted without verification. If no webhooks exist, verdict Yes with evidence "No inbound webhooks".

5.4 (AUTO) No plaintext HTTP. Scan for outbound "http://" URLs (excluding localhost). Yes = none. No = list them.

5.5 (AUTO) API-layer auth on every data route. Inspect route definitions: every route returning or mutating WEKA data has auth middleware applied server-side (would hold against direct curl, not just UI). Yes = all covered. No = list unprotected routes.

5.6 (AUTO) Rate limiting, CORS, HSTS. Check for rate-limit middleware on externally reachable endpoints, a CORS policy restricted to known origins (no wildcard * on data endpoints), and the HSTS header. Yes = all three present (or documented N/A for private deployments). No = list what's missing.

5.7 (ASK) "Does this app connect directly to any production core system (like the production NetSuite or a production database of another system), or does it go through IT-provided credentials/an IT-managed integration layer?" Yes = IT-managed/scoped. No = direct unreviewed connection.

### Module 6 - Code, Dependency & AI Feature Security

6.1 (AUTO) Dependency vulnerability scan. Run the appropriate audit (npm audit, pip-audit, or equivalent). Yes = no critical/high findings. No = report the count.

6.2 (ASK) "Has a person (not just the AI) reviewed the code before it went, or goes, to production?"

6.3 (AUTO) Repo/Repl not publicly forkable. Check what you can from workspace settings/files (private Repl, remix disabled, private GitHub repo); if not determinable, mark IT-VERIFY.

6.4 (AUTO) AI/Probabilistic feature detection. Scan dependencies and code for LLM SDKs and AI patterns: anthropic, openai, google-generativeai, gemini, cohere, langchain, llamaindex, embeddings, vector stores (pinecone, chroma, pgvector), semantic search, chat completion calls. Record the flag prominently: AI/Probabilistic Features = Y or N, with the evidence. This is informational - verdict Yes means the detection ran.

6.5 (Conditional ASK - only if 6.4 found AI features) "This app has AI-driven features (I found: [list]). Has IT done its elevated review of these features yet?" Yes = reviewed. No = not yet. If 6.4 = N, verdict Yes with evidence "No AI features detected".

6.6 (AUTO) Deterministic by default. If 6.4 = N, verdict Yes. If 6.4 = Y, check whether AI features are isolated behind clear boundaries (separate modules/routes, e.g. server/services/ai/) rather than woven through core logic. Yes = isolated. No = mixed into core flows.

6.7 (Conditional ASK - only if 6.4 = Y) "Have the AI features been through evaluation or red-team style testing (checking for prompt injection, data leakage, wrong-answer handling) before production?" Yes = tested. No = not yet. If 6.4 = N, verdict Yes with evidence "No AI features detected".

6.8 (AUTO) Approved AI provider. If 6.4 = Y, check which provider is used. Yes = Gemini via env-var key (IT-issued) and/or Claude via MCP OAuth. No = other providers or embedded third-party AI keys without a documented IT approval in docs/ARCHITECTURE.md. If 6.4 = N, verdict Yes.

6.9 (AUTO) Encryption at rest documented. Check docs/ARCHITECTURE.md for a note on encryption at rest for each data store (Neon-provided counts). Yes = documented. No = absent. (Confirmation of the setting itself: IT-VERIFY note.)

### Module 7 - Logging, Monitoring & Audit

7.1 (AUTO) Access/admin-action logging exists. Look for logging of logins and admin actions. Yes = present. No = absent.

7.2 (AUTO) Logs don't leak secrets/PII. Scan logging statements for variables that look like tokens, passwords, or personal data being logged. Yes = clean. No = list the lines.

7.3 (ASK) "If the app breaks or errors out, does anyone get notified automatically (Slack alert, email)?"

7.4 (AUTO) Central, tamper-resistant log forwarding. Check for log shipping to a central sink (Google Cloud Logging, Snowflake, or equivalent) rather than only an in-app database table. Yes = forwarding configured. No = app-local logs only.

7.5 (ASK) "If someone tried to log in and failed many times, or a user suddenly gained admin rights, would anyone be alerted?" Yes = anomalous-access alerting exists. No = no.

7.6 (IT-VERIFY) Log retention meets WEKA policy (SDLC target: 12 months hot + 24 months cold; 90-day absolute production minimum - IT is reconciling the two figures).

### Module 8 - Governance & Lifecycle

8.1 (ASK) "Who is the owner of this app, and who is the backup owner if they're away or leave?" Yes = both named. No = missing either.

8.2 (IT-VERIFY) Replit vendor posture (SOC 2, DPA) confirmed by IT.

8.3 (ASK) "How important is this app? If it went down for a day, what breaks?" Record answer as the criticality note; verdict Yes if answered.

8.4 (IT-VERIFY) App is registered in the Internal App Registry with a review date.

8.5 (ASK) "Was this app registered with IT through the SDLC intake form (the IT SW / AI Solution & App Development Request) before it was built - and if it's heading to production, has the Okta app registration been opened?" Yes = both (or intake yes and Okta in progress with a ticket). No = neither/unknown.

## Report format

After all checks, write compliance-report.md at the repo root, overwriting any previous version:

# WEKA Compliance Self-Audit Report

- App name: [ask or infer]
- Environment audited: [dev/prod]
- Date: [today]
- Run by: [user's name - ask]
- Audit skill version: 1.1.0
- AI/Probabilistic Features: [Y/N from check 6.4]
- SDLC intake (Gate 0): [status from 8.5]

## Summary
[X] Yes | [Y] No | [Z] IT-VERIFY

## Results
| # | Requirement | Module | Type | Verdict | Evidence |
|---|-------------|-------|------|---------|----------|
(one row per requirement, in order)

## Items needing your attention (all No verdicts)
(bullet list with a one-line plain-English suggestion each)

## Items IT will close out (all IT-VERIFY)
(bullet list)

Next step for the user: link this report in your app's row in the Internal App Registry in Notion and notify IT.

Finally, tell the user in chat: the summary counts, the AI/Probabilistic flag, the SDLC intake status, and the top 3 things to fix first (if any No verdicts exist). Keep it friendly and jargon-free.
