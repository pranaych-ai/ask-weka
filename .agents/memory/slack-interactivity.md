---
name: Slack interactive security pattern
description: Conventions for Slack buttons/modals, summaries, and unfurls in Ask WEKA
---

Rule: Slack interactive elements never carry content or identity. Button values and
modal private_metadata are HMAC-signed references (`sign_action`/`parse_action` in
slack_service, keyed on SESSION_SECRET, kinds `m`=message, `c`=conversation). Handlers
re-resolve the clicker via users.info and re-check ownership server-side.

**Why:** Slack payloads are attacker-controllable after signature verification (any
workspace user can craft clicks); trusting ids or text would let one employee rate,
repost, or escalate another's conversations.

**How to apply:** any new Slack button/modal must (1) sign its reference, (2) verify
via parse_action, (3) re-check ownership from the DB, (4) route content from the DB
only. Summaries/catch-up: admin flag → employee opt-in gate order, bounded fetch
(thread, or channel 24h/200 msgs), and NEVER persist fetched Slack content or the
summary. Manifest scopes are emitted per enabled feature only — reinstall manifest
after feature/command changes.
