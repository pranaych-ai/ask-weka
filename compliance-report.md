# WEKA Compliance Self-Audit Report

- App name: Ask WEKA
- Environment audited: dev
- Date: 2026-08-30
- Run by: Pranay Chandur
- Audit skill version: 1.1.0
- AI/Probabilistic Features: Y
- SDLC intake (Gate 0): Yes — user confirmed the IT intake and Okta app registration are complete

## Summary

33 Yes | 13 No | 5 IT-VERIFY

## Results

| # | Requirement | Module | Type | Verdict | Evidence |
|---|-------------|--------|------|---------|----------|
| 1.1 | SSO login, no local accounts | Identity & Access | AUTO | Yes | `backend/auth.py` uses Authlib Okta OIDC; no local password/account tables or signup route found. |
| 1.2 | Role-based authorization | Identity & Access | AUTO | Yes | `require_admin` and `admin_audited` enforce admin access server-side; Okta group claims map to roles in `backend/auth.py`. |
| 1.3 | Session timeout/token expiry within policy | Identity & Access | AUTO | Yes | `backend/auth.py` explicitly enforces 8-hour idle and 24-hour absolute limits; `backend/main.py` sets an 8-hour session cookie lifetime. |
| 1.4 | Production admin access limited to specific people and separate from dev | Identity & Access | ASK | Yes | User answer: `pranay.chandur@weka.io`. The dev and production configurations are separate in `.replit`. |
| 1.5 | Access granted through Okta groups | Identity & Access | ASK | Yes | User answer: “Okta groups”; `backend/auth.py` uses the `groups` OIDC claim and `dl-app-*` group convention. |
| 1.6 | No MFA bypass | Identity & Access | AUTO | Yes | No bypass route or MFA workaround found. Dev anonymous-admin behavior is explicitly limited to missing Okta configuration; production refuses to start without Okta settings. |
| 1.7 | Okta deprovisioning immediately removes access | Identity & Access | IT-VERIFY | IT-VERIFY | Requires IT confirmation and a production deprovisioning test. |
| 2.1 | No hardcoded secrets | Secrets & Credentials | AUTO | Yes | Secret-pattern scan found no real hardcoded credentials; Slack/Gemini/Okta/Atlassian credentials are read from environment secrets. Token-shaped values found in tests are labeled dummy fixtures only. |
| 2.2 | Credentials belong to a service account | Secrets & Credentials | ASK | Yes | User answer: “Service account.” |
| 2.3 | Per-environment keys | Secrets & Credentials | AUTO | No | The code uses the same environment variable names across environments; no separate dev/prod credential names were found. |
| 2.4 | Key rotation plan | Secrets & Credentials | ASK | Yes | User answered yes; `docs/ARCHITECTURE.md` documents annual rotation and immediate rotation on owner change, offboarding, or suspected compromise. |
| 2.5 | Minimum-permission scopes | Secrets & Credentials | AUTO | Yes | Slack manifest requests focused bot scopes (`chat:write`, user lookup, DM, files, commands); Jira and MCP credentials are scoped in their respective flows. |
| 2.6 | Secret scanning enabled | Secrets & Credentials | AUTO | No | No `.gitleaks.toml` or repository secret-scanning workflow was visible in the workspace. |
| 3.1 | Data classification | Data Handling | ASK | Yes | User answer: “Internal company info.” `docs/ARCHITECTURE.md` classifies the data as internal. |
| 3.2 | Dev uses fake/sample rather than production data | Data Handling | ASK | No | User answer: “Copy of prod data.” |
| 3.3 | Dev contains no real customer/employee personal information | Data Handling | ASK | Yes | User answer: “no.” |
| 3.4 | Retention and deletion rules documented | Data Handling | AUTO | Yes | `docs/ARCHITECTURE.md` documents conversation, feedback, audit, Slack metadata, and preference retention/deletion behavior. |
| 3.5 | External data egress inventory compiled | Data Handling | AUTO | Yes | Inventory compiled from source: Slack (`slack.com`), Atlassian (`api.atlassian.com`, `auth.atlassian.com`), Okta (`weka.okta.com`), and Gemini through the approved provider SDK. IT approval remains a confirmation item. |
| 4.1 | Separate dev and production deployments | Environment Separation | AUTO | Yes | `.replit` contains a dev workflow on port 8000 and a separate autoscale deployment configuration; user identified production administration separately. |
| 4.2 | Separate integration credentials per environment | Environment Separation | AUTO | No | No distinct dev/prod integration environment variable names were found. |
| 4.3 | Version control with GitHub remote | Environment Separation | AUTO | No | A `.git` repository exists, but `git remote -v` showed only the Replit GitSafe backup remote, not a GitHub remote. |
| 4.4 | Human review before production, including AI-written code | Environment Separation | ASK | Yes | User answer: “always.” |
| 4.5 | Production deployment limited to IT plus named owner | Environment Separation | ASK | Yes | User answer: `pranay.chandur@weka.io`. |
| 4.6 | Test accounts use the `test-` prefix | Environment Separation | AUTO | Yes | No test accounts or seed identities violating the convention were found in source, seed data, or docs. |
| 5.1 | Outbound call inventory | Network & Integration Security | AUTO | Yes | Source inventory compiled: `slack.com`, `api.atlassian.com`, `auth.atlassian.com`, `weka.okta.com`, plus the approved Gemini SDK endpoint. |
| 5.2 | App URL restricted or login-gated | Network & Integration Security | ASK | Yes | User answer: “Restricted or login before anything loads.” Production requires Okta configuration and protected routes require authentication. |
| 5.3 | Webhook signature validation | Network & Integration Security | AUTO | Yes | Slack events, commands, and interactions verify v0 HMAC signatures and replay timestamps; Jira OAuth validates state. |
| 5.4 | No plaintext HTTP outbound URLs | Network & Integration Security | AUTO | Yes | No actual outbound plaintext HTTP endpoint was found; the only `http://` matches rewrite proxy-generated redirect URIs to HTTPS. |
| 5.5 | API-layer auth on every data route | Network & Integration Security | AUTO | Yes | User/data routes use `require_user`, admin routers use `admin_audited`, public API routes use API-key authentication, and Slack inbound routes use HMAC verification. Health checks are intentionally non-data exceptions. |
| 5.6 | Rate limiting, restricted CORS, and HSTS | Network & Integration Security | AUTO | No | No rate-limit middleware, restricted CORS policy, or HSTS header configuration was found. |
| 5.7 | Production core systems accessed through IT-managed/scoped integration | Network & Integration Security | ASK | Yes | User answer: “IT-managed/scoped.” |
| 6.1 | Dependency vulnerability scan | Code, Dependency & AI Feature Security | AUTO | No | `pip-audit -r requirements.txt` reported no known vulnerabilities; `npm audit` reported 1 high and 1 moderate vulnerability through Vite/esbuild. |
| 6.2 | Human code review | Code, Dependency & AI Feature Security | ASK | Yes | User answer: “yes.” |
| 6.3 | Repo/Repl not publicly forkable | Code, Dependency & AI Feature Security | AUTO | IT-VERIFY | Workspace files do not expose Replit visibility, remix, or GitHub privacy settings; requires platform confirmation. |
| 6.4 | AI/probabilistic feature detection | Code, Dependency & AI Feature Security | AUTO | Yes | AI features detected: Gemini chat/answer generation, Gemini golden-run grading, and Gemini-written usage digests. |
| 6.5 | Elevated IT review of AI features | Code, Dependency & AI Feature Security | ASK | Yes | User answer: “yes.” |
| 6.6 | AI isolated behind clear boundaries | Code, Dependency & AI Feature Security | AUTO | Yes | Gemini provider and judge logic are separated under `backend/providers/` and `backend/judge.py`, with explicit provider selection. |
| 6.7 | AI evaluation/red-team testing | Code, Dependency & AI Feature Security | ASK | No | User answer: “not_yet.” |
| 6.8 | Approved AI provider | Code, Dependency & AI Feature Security | AUTO | Yes | `docs/ARCHITECTURE.md` and `backend/providers/gemini.py` show Gemini via `GEMINI_API_KEY` from environment secrets. |
| 6.9 | Encryption at rest documented | Code, Dependency & AI Feature Security | AUTO | No | `docs/ARCHITECTURE.md` does not document encryption-at-rest for the PostgreSQL data store. |
| 7.1 | Access/admin-action logging | Logging, Monitoring & Audit | AUTO | Yes | Login/logout and admin actions are written to `activity_log`; admin routes use the central audit dependency. |
| 7.2 | Logs do not leak secrets or PII | Logging, Monitoring & Audit | AUTO | Yes | Source review found no logging of credential values; architecture documentation states audit events exclude secrets and raw PII. |
| 7.3 | Automatic notification when the app breaks | Logging, Monitoring & Audit | ASK | No | User answer: “no.” Slack sync alerts exist for an enabled feature, but general application failure alerting is not configured. |
| 7.4 | Central tamper-resistant log forwarding | Logging, Monitoring & Audit | AUTO | No | No central sink configuration was found; `docs/ARCHITECTURE.md` labels forwarding to Cloud Logging or Snowflake as a TODO. |
| 7.5 | Alerts for repeated failed logins or sudden admin-rights grant | Logging, Monitoring & Audit | ASK | No | User answer: “no.” |
| 7.6 | Log retention meets WEKA policy | Logging, Monitoring & Audit | IT-VERIFY | IT-VERIFY | Requires IT confirmation of the 12-month hot / 24-month cold target and production minimum. |
| 8.1 | Owner and backup owner named | Governance & Lifecycle | ASK | No | User named `pranay.chandur@weka.io` as owner but did not provide a backup owner. |
| 8.2 | Replit vendor posture confirmed | Governance & Lifecycle | IT-VERIFY | IT-VERIFY | Requires IT confirmation of SOC 2 and DPA posture. |
| 8.3 | Business criticality recorded | Governance & Lifecycle | ASK | Yes | User answer: “Important.” |
| 8.4 | App registered in Internal App Registry | Governance & Lifecycle | IT-VERIFY | IT-VERIFY | Requires IT confirmation and a review date. |
| 8.5 | SDLC intake and Okta registration | Governance & Lifecycle | ASK | Yes | User answered: “Yes — both.” |

## Items needing your attention (all No verdicts)

- **Use separate credentials for dev and production:** create environment-specific integration credentials and confirm Slack/Jira/Gemini separation.
- **Enable repository secret scanning:** add Gitleaks or equivalent CI/repository scanning.
- **Do not use a production-data copy in dev:** replace it with synthetic or sandbox data and verify the dev database.
- **Add a GitHub remote or document the approved version-control path:** the current workspace exposes only a GitSafe backup remote.
- **Add rate limiting, restricted CORS, and HSTS:** protect externally reachable routes with the approved edge/app controls.
- **Resolve the npm dependency findings:** update or pin the Vite/esbuild dependency chain and rerun the audit.
- **Complete AI red-team testing:** test prompt injection, data leakage, and wrong-answer handling before production.
- **Document encryption at rest:** record the PostgreSQL provider/control and IT confirmation in the architecture notes.
- **Add application failure alerting:** route service failures to the approved owner/admin notification channel.
- **Forward logs centrally:** configure tamper-resistant log shipping and verify retention.
- **Add anomalous-access alerts:** alert on repeated failed logins and unexpected admin privilege changes.
- **Name a backup owner:** record a second responsible owner for continuity.

## Items IT will close out (all IT-VERIFY)

- Test that Okta deprovisioning immediately removes production access.
- Confirm Replit/Repl visibility and fork/remix restrictions.
- Confirm log retention meets the WEKA target.
- Confirm Replit SOC 2 and DPA posture.
- Confirm Internal App Registry registration and review date.

Next step for the user: link this report in the app's row in the Internal App Registry in Notion and notify IT.