# WEKA Compliance Self-Audit Report

- App name: Ask WEKA
- Environment audited: dev
- Date: 2026-08-31
- Run by: Pranay Chandur
- Audit skill version: 1.1.0
- AI/Probabilistic Features: Y
- SDLC intake (Gate 0): Yes — user previously confirmed the IT intake and Okta app registration are complete

## Summary

35 Yes | 11 No | 5 IT-VERIFY

## Results

| # | Requirement | Module | Type | Verdict | Evidence |
|---|-------------|--------|------|---------|----------|
| 1.1 | SSO login, no local accounts | Identity & Access | AUTO | Yes | `backend/auth.py` uses Authlib Okta OIDC and no local password/account table or signup route was found. Production refuses startup without Okta configuration (`backend/main.py`). |
| 1.2 | Role-based authorization | Identity & Access | AUTO | Yes | `require_admin` and `admin_audited` enforce server-side administration; OIDC `groups` claims map to roles in `backend/auth.py`. |
| 1.3 | Session timeout/token expiry within policy | Identity & Access | AUTO | Yes | `backend/auth.py` enforces an 8-hour idle and 24-hour absolute timeout; `backend/main.py` sets the 8-hour session-cookie lifetime. |
| 1.4 | Production admin access limited to specific people and separate from dev | Identity & Access | ASK | Yes | Previous user answer: `pranay.chandur@weka.io`. The answer stated dev and production configurations are separate. |
| 1.5 | Access granted through Okta groups | Identity & Access | ASK | Yes | Previous user answer: “Okta groups.” |
| 1.6 | No MFA bypass | Identity & Access | AUTO | Yes | No production MFA-bypass route or workaround was found. The anonymous-admin path is explicitly development-only when Okta is unconfigured; production fails closed without Okta settings. |
| 1.7 | Okta deprovisioning immediately removes access | Identity & Access | IT-VERIFY | IT-VERIFY | Requires IT confirmation and a production deprovisioning test. |
| 2.1 | No hardcoded secrets | Secrets & Credentials | AUTO | Yes | Re-run secret-pattern scan found no real credential values or committed real `.env` files. Credentials, including `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET`, are read from environment variables; token-shaped test values are dummy fixtures. |
| 2.2 | Credentials belong to a service account | Secrets & Credentials | ASK | Yes | Previous user answer: “Service account.” |
| 2.3 | Per-environment keys | Secrets & Credentials | AUTO | Yes | `backend/env.py` distinguishes workspace development from `REPLIT_DEPLOYMENT` production; `docs/ARCHITECTURE.md` and `docs/INTEGRATIONS.md` require independently configured dev/prod Okta, Gemini, Jira, and Slack credentials in separate secrets panes. |
| 2.4 | Key rotation plan | Secrets & Credentials | ASK | Yes | Previous user answer: “yes.” `docs/ARCHITECTURE.md` records annual rotation and immediate rotation on owner change, offboarding, or suspected compromise. |
| 2.5 | Minimum-permission scopes | Secrets & Credentials | AUTO | Yes | Slack's generated manifest uses granular feature-dependent bot scopes, not admin/wildcard scopes (`backend/slack_service.py`); thread-history and link scopes are added only when their relevant feature is enabled. API keys have the `ask` scope (`docs/INTEGRATIONS.md`). |
| 2.6 | Secret scanning enabled | Secrets & Credentials | AUTO | No | No Gitleaks configuration, secret-scanning CI workflow, or visible GitHub Advanced Security evidence was found. |
| 3.1 | Data classification | Data Handling | ASK | Yes | Previous user answer: “Internal company info.” |
| 3.2 | Dev uses fake/sample rather than production data | Data Handling | ASK | No | Previous user answer: “Copy of prod data.” Current code/docs now enforce an environment-stamped database boundary and document synthetic dev seeding (`backend/env.py`, `docs/ARCHITECTURE.md`), but the recorded ASK answer remains a production-copy concern requiring owner verification. |
| 3.3 | Dev contains no real customer/employee personal information | Data Handling | ASK | Yes | Previous user answer: “no.” |
| 3.4 | Retention and deletion rules documented | Data Handling | AUTO | Yes | `docs/ARCHITECTURE.md` documents conversation, feedback, audit, Slack metadata/preference retention and deletion behavior. |
| 3.5 | External data egress inventory compiled | Data Handling | AUTO | Yes | Inventory compiled: Slack (`slack.com`, Slack-supplied `hooks.slack.com` responses), Atlassian (`api.atlassian.com`, `auth.atlassian.com`), configured Okta (`weka.okta.com`), and Gemini via `google-genai`. The list is ready for IT approved-services confirmation. |
| 4.1 | Separate dev and production deployments | Environment Separation | AUTO | Yes | `.replit` has a deployment configuration; `backend/env.py` identifies workspace/local development separately from Replit deployment production and refuses a cross-environment database. |
| 4.2 | Separate integration credentials per environment | Environment Separation | AUTO | Yes | `backend/env.py`, `docs/ARCHITECTURE.md`, and `docs/INTEGRATIONS.md` explicitly require independently managed workspace/dev and deployment/prod credentials, including separate dev Slack workspace app and production Slack app. |
| 4.3 | Version control with GitHub remote | Environment Separation | AUTO | No | A `.git` repository exists, but `git remote -v` showed GitSafe and Replit subrepl remotes only; no GitHub remote was present. |
| 4.4 | Human review before production, including AI-written code | Environment Separation | ASK | Yes | Previous user answer: “always.” |
| 4.5 | Production deployment limited to IT plus named owner | Environment Separation | ASK | Yes | Previous user answer: `pranay.chandur@weka.io`. |
| 4.6 | Test accounts use the `test-` prefix | Environment Separation | AUTO | Yes | No test accounts or seed identities violating the convention were found in code, seed data, or docs. |
| 5.1 | Outbound call inventory | Network & Integration Security | AUTO | Yes | Source review found external application egress to `slack.com`, Slack response hooks at `hooks.slack.com`, `api.atlassian.com`, `auth.atlassian.com`, configured Okta OIDC/JWKS at `weka.okta.com`, and Gemini through the `google-genai` SDK. |
| 5.2 | App URL restricted or login-gated | Network & Integration Security | ASK | Yes | Previous user answer: “Restricted or login before anything loads.” Production requires Okta configuration and data routes enforce server-side authentication. |
| 5.3 | Webhook signature validation | Network & Integration Security | AUTO | Yes | Slack events, commands, and interactivity read the untouched raw body and verify v0 HMAC plus a five-minute replay window before parsing; event callbacks also pin and check `api_app_id` (`backend/slack_app.py`). Jira OAuth validates state. |
| 5.4 | No plaintext HTTP outbound URLs | Network & Integration Security | AUTO | Yes | Re-run scan found no outbound plaintext HTTP endpoint. The only non-local matches in application code rewrite proxy-generated `http://` redirect URIs to HTTPS (`backend/auth.py`, `backend/jira_oauth.py`). |
| 5.5 | API-layer auth on every data route | Network & Integration Security | AUTO | Yes | User/data routes use `require_user`; admin routers use audited server-side admin dependencies; public API and MCP routes authenticate API/MCP bearer credentials; Slack inbound routes use signature validation. `/api/healthz` is the intentional non-data liveness exception. |
| 5.6 | Rate limiting, restricted CORS, and HSTS | Network & Integration Security | AUTO | No | API-key-specific request limits exist, but no general externally reachable endpoint rate-limit middleware, restricted CORS policy, or HSTS header configuration was found. |
| 5.7 | Production core systems accessed through IT-managed/scoped integration | Network & Integration Security | ASK | Yes | Previous user answer: “IT-managed/scoped.” |
| 6.1 | Dependency vulnerability scan | Code, Dependency & AI Feature Security | AUTO | No | Re-run audits: `.pythonlibs/bin/pip-audit -r requirements.txt` found none; `pnpm audit --audit-level high` found 4 high findings (plus 1 low); `frontend/npm audit` found 1 high and 1 moderate finding through Vite/esbuild. |
| 6.2 | Human code review | Code, Dependency & AI Feature Security | ASK | Yes | Previous user answer: “yes.” |
| 6.3 | Repo/Repl not publicly forkable | Code, Dependency & AI Feature Security | AUTO | IT-VERIFY | Workspace files do not expose Replit visibility/remix settings or GitHub repository privacy; requires platform confirmation. |
| 6.4 | AI/probabilistic feature detection | Code, Dependency & AI Feature Security | AUTO | Yes | AI/Probabilistic Features = Y. Gemini is used for chat/answers, golden-run grading, ticket drafting, Slack thread summaries, and usage digests (`backend/providers/gemini.py`, `backend/judge.py`, `backend/slack_app.py`, `backend/slack_scheduler.py`). |
| 6.5 | Elevated IT review of AI features | Code, Dependency & AI Feature Security | ASK | Yes | Previous user answer: “yes.” AI features found: Gemini answer generation, grading, ticket drafting, Slack summaries, and usage digests. |
| 6.6 | AI isolated behind clear boundaries | Code, Dependency & AI Feature Security | AUTO | Yes | Provider access is isolated in `backend/providers/`; deterministic controls and evaluation are in `backend/ai_safety.py` and `backend/safety_eval.py`, with explicit call-site safety gates. |
| 6.7 | AI evaluation/red-team testing | Code, Dependency & AI Feature Security | ASK | No | Previous user answer: “not_yet.” The current repository has a fixed adversarial safety suite and documented pre-production live evaluation, but the recorded required owner answer has not confirmed completed evaluation. |
| 6.8 | Approved AI provider | Code, Dependency & AI Feature Security | AUTO | Yes | `backend/providers/gemini.py` and `docs/ARCHITECTURE.md` show Gemini using the environment-only `GEMINI_API_KEY`; no other active AI provider was found. |
| 6.9 | Encryption at rest documented | Code, Dependency & AI Feature Security | AUTO | No | `docs/ARCHITECTURE.md` identifies Replit-managed PostgreSQL but does not document encryption at rest for that store or its provider control. |
| 7.1 | Access/admin-action logging | Logging, Monitoring & Audit | AUTO | Yes | `activity_log` records login/logout, feedback, conversation deletion, Slack metadata, alerts, and audited admin actions (`backend/audit.py`, `backend/auth.py`, admin routers). |
| 7.2 | Logs do not leak secrets or PII | Logging, Monitoring & Audit | AUTO | Yes | Re-run logging review found no credential values, question text, request bodies, or raw feedback attached to logging statements. Slack inbound audit is metadata-only, and alerts explicitly exclude secrets/KB content/request bodies. |
| 7.3 | Automatic notification when the app breaks | Logging, Monitoring & Audit | ASK | No | Previous user answer: “no.” Current code now includes gated Slack security/availability alerting for error spikes and AI-provider failure (`backend/alerts.py`), but the recorded owner answer does not confirm operational notification. |
| 7.4 | Central tamper-resistant log forwarding | Logging, Monitoring & Audit | AUTO | No | No central log sink configuration was found. `docs/ARCHITECTURE.md` still marks forwarding to Cloud Logging or Snowflake as TODO. |
| 7.5 | Alerts for repeated failed logins or sudden admin-rights grant | Logging, Monitoring & Audit | ASK | No | Previous user answer: “no.” Current code records and alerts on repeated Okta failures and admin-rights changes (`backend/alerts.py`), but the recorded owner answer does not confirm operational alerting. |
| 7.6 | Log retention meets WEKA policy | Logging, Monitoring & Audit | IT-VERIFY | IT-VERIFY | Requires IT confirmation of the 12-month hot / 24-month cold target and production minimum. |
| 8.1 | Owner and backup owner named | Governance & Lifecycle | ASK | No | Previous user answer named `pranay.chandur@weka.io` as owner but did not provide a backup owner; `replit.md` still shows the backup owner as TODO. |
| 8.2 | Replit vendor posture confirmed | Governance & Lifecycle | IT-VERIFY | IT-VERIFY | Requires IT confirmation of SOC 2 and DPA posture. |
| 8.3 | Business criticality recorded | Governance & Lifecycle | ASK | Yes | Previous user answer: “Important.” |
| 8.4 | App registered in Internal App Registry | Governance & Lifecycle | IT-VERIFY | IT-VERIFY | Requires IT confirmation and a review date. |
| 8.5 | SDLC intake and Okta registration | Governance & Lifecycle | ASK | Yes | Previous user answer: “Yes — both.” |

## Items needing your attention (all No verdicts)

- **Enable repository secret scanning:** add Gitleaks or equivalent repository/CI scanning.
- **Verify that development has no production-data copy:** the prior owner answer says it does; validate the newly enforced synthetic-data boundary and correct the recorded status when confirmed.
- **Add an approved GitHub remote:** the workspace currently exposes GitSafe/Replit remotes only.
- **Add edge/API protections:** configure general rate limiting, restricted CORS, and HSTS for externally reachable routes.
- **Resolve dependency findings:** update the vulnerable pnpm and frontend Vite/esbuild dependency paths, then re-run both audits.
- **Complete and record AI red-team evaluation:** confirm prompt injection, data leakage, and wrong-answer handling tests have been completed before production.
- **Document database encryption at rest:** record the PostgreSQL provider control and IT confirmation in the architecture documentation.
- **Confirm application-failure notification operationally:** the Slack alerting implementation exists, but the prior owner answer says no automatic notification occurs.
- **Forward logs centrally:** configure tamper-resistant log shipping and retention.
- **Confirm anomalous-access alerting operationally:** repeated-login and admin-change code exists, but the prior owner answer says no alert is received.
- **Name a backup owner:** record a second responsible owner for continuity.

## Items IT will close out (all IT-VERIFY)

- Test that Okta deprovisioning immediately removes production access.
- Confirm Replit/Repl visibility and fork/remix restrictions.
- Confirm log retention meets the WEKA target.
- Confirm Replit SOC 2 and DPA posture.
- Confirm Internal App Registry registration and review date.

Next step for the user: link this report in the app's row in the Internal App Registry in Notion and notify IT.