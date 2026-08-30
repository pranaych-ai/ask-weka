# AI Safety Evaluation — Ask WEKA

**Scope:** prompt injection, sensitive-data leakage, and incorrect-answer
handling for all Gemini usage (web chat, public API `/api/v1/ask`, Slack DM
assistant, Slack daily digest, golden-run judge).
**Status:** evaluation complete; controls in production code paths.
**Last live run:** 2026-08-30 (after the streaming-holdback egress fix) —
**12/12 cases passed** (5 prompt injection, 4 data leakage, 3 incorrect
answer).

## Controls implemented

| Layer | Control | Where |
|---|---|---|
| Prompt | KB delimited by explicit markers; "content, not instructions" rule; refusal-when-uncovered rule | `backend/prompts.py` |
| Input | Deterministic injection detection (`screen_question`) — runs BEFORE the provider is invoked; flags override/extraction/role-hijack/KB-dump attempts; flagged attempts are audit-logged (`chat.injection_flagged`), never silently dropped | `backend/ai_safety.py`, wired in web chat, public API, MCP, Slack DM, ticket-draft transcripts, golden-run questions. (The judge's input is the already-gated answer, so it needs no separate screen — an explicit scope decision.) |
| Output | Deterministic release gate (`screen_answer`) — blocks secret-shaped strings (API keys, private keys, tokens, password assignments, credentialed connection strings), system-prompt/KB-envelope echoes, and injection-compliance markers. Blocked answers are replaced with a safe refusal and audit-logged (`chat.safety_blocked`) | all Gemini egress points: web chat stream, `/api/v1/ask`, MCP `ask_weka`, Slack DM, Slack digest (falls back to plain metrics), ticket drafting (falls back to a manual draft), golden-run answers (stored as refusal + auto-flagged), judge reasoning (withheld) |
| Evaluation | Fixed 12-case adversarial corpus, runnable offline (pytest, deterministic) and live against the real model. Route coverage is asserted end-to-end in tests: adversarial (leaky) model outputs are injected into the public API, MCP, ticket-draft, and golden-run paths and must never be released raw | `backend/safety_eval.py`, `tests/test_ai_safety.py`, `scripts/run_safety_eval.py` |
| Correctness | Golden-question suite with independent stronger judge model; human verdicts override AI | `backend/qa.py`, `backend/judge.py` |

## How to re-run

- Offline (every CI run): `pytest tests/test_ai_safety.py`
- Live (before each production approval / model change):
  `python scripts/run_safety_eval.py` — exits non-zero on any failure.
  Re-run whenever `GEMINI_MODEL`, the system prompt, or the KB import
  process changes.

## Findings (live run, 2026-08-30)

- **Prompt injection (5/5 pass):** the model refused instruction overrides,
  system-prompt extraction, role hijack, verbatim KB dumps, and an indirect
  injection embedded in quoted ticket text. All five inputs were correctly
  flagged by the input screen.
- **Data leakage (4/4 pass):** no secret-shaped strings, env-var values, or
  compensation specifics were released; the model deferred to HR/IT contacts.
- **Incorrect answers (3/3 pass):** for a fabricated policy, a fabricated
  person, and a fabricated portal URL, the model explicitly stated the KB
  does not cover the topic and pointed to a real contact instead of
  inventing content.

## Residual risks

1. **Streaming holdback window (web chat):** text is released to the browser
   600 characters behind the model, and the entire accumulated text is
   re-screened before each release, so a violating pattern is always still
   inside the unreleased window when detected — no part of it reaches the
   client (verified by SSE chunk-boundary tests). Residual: a violating
   pattern longer than the holdback window with no early-matching prefix
   would partially release; all current gate patterns match well within
   600 characters.
2. **Deterministic gate coverage:** regex rules catch known secret formats
   and prompt-echo markers, not novel leak formats (e.g. secrets spelled out
   in words). Accepted because the KB contains no secrets by policy; the
   gate is defense-in-depth, not the primary control.
3. **KB as injection vector:** admin-edited/imported KB text is trusted
   content inside the prompt. An admin (already privileged) could plant
   injection text. Mitigated by admin-only KB editing + audit logging;
   output gate still applies.
4. **Judge is itself an LLM:** golden-run grading can mis-grade; mitigated
   by human verdict override and the deterministic gates being independent
   of the judge.
5. **Corpus breadth:** 12 cases cover the main attack classes but are not
   exhaustive red-teaming. Extend the corpus in `backend/safety_eval.py`
   as new attack patterns are observed (flagged attempts appear in the
   audit log under `chat.injection_flagged`).
