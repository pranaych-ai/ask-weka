---
name: AI safety gate coverage
description: Every Gemini egress point must pass the deterministic safety gate; live eval must be re-run on model/prompt changes.
---

Any code path that sends Gemini output to a user, Slack, an MCP client, or
persists it for display must pass the deterministic output gate before
release. User-controlled input going to the model gets flag-and-audit input
screening, and that screening must run (and be committed) BEFORE the provider
call so the attempt is recorded even when generation fails.

**Why:** Compliance requires access-boundary checks on all AI output, and the
gate only protects paths that call it. Egress points are easy to miss beyond
the obvious chat routes — anything that drafts, grades, or summarizes with the
model is also egress.

**How to apply:** Wire the gate before persistence and any send; streaming
paths must hold back a tail window and re-screen the full accumulated text
before each release (post-hoc stream cutting is insufficient). Re-run the live
safety eval whenever the model, system prompt, or KB import changes, and record
the run in the evaluation doc. Eval refusal-marker regexes are heuristics — a
"failure" may be a safe answer with unanticipated phrasing; check the excerpt
before treating it as a regression.
