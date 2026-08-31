---
name: Slack interactive security decisions
description: Durable security rules for Slack buttons, modals, and summaries in Ask WEKA
---

Rule: Slack interactive payloads (buttons, modal metadata) must never carry content or
identity — only server-signed references — and every click handler must re-resolve the
clicking employee's identity and re-check ownership against the database before acting.

**Why:** after Slack's request signature is verified, the payload is still
attacker-controllable by any workspace member; trusting embedded ids or text would let
one employee rate, republish, or escalate another employee's conversations.

**How to apply:** any new Slack interactive element routes all displayed/persisted
content from the DB, never from the payload.

Rule: thread/channel summaries are consent-gated (admin flag, then employee opt-in),
bounded to the originating thread/channel, and neither fetched Slack messages nor the
generated summary may ever be persisted; each Slack capability delivers through its OWN
feature gate (summaries must not depend on the channel-mentions gate).

**Why:** channel history is other people's content — persistence or cross-feature
gating either leaks data or silently breaks the feature when a sibling flag is off
(caught in code review once).

**How to apply:** new Slack flows pick their own gate feature and stay in-memory for
any fetched Slack content; reinstall the manifest after feature/command changes since
scopes are emitted per enabled feature.
