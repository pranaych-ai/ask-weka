---
name: Gemini model retirement
description: Why GEMINI_MODEL is pinned and what breaks if it's unset
---
The app's coded default Gemini model (`gemini-2.5-flash`) was retired by Google (404 "no longer available to new users"). `GEMINI_MODEL=gemini-3.6-flash` is set as a shared env var to override it.

**Why:** chat requests stream back a 404 error event when the model is unavailable.
**How to apply:** if chat suddenly returns model 404s again, bump `GEMINI_MODEL` to the current Gemini flash model rather than editing code.

The QA judge (backend, `GEMINI_JUDGE_MODEL`) defaults to the stable alias `gemini-pro-latest` specifically to survive retirements — pinned pro models (`gemini-2.5-pro`) 404'd and preview ones (`gemini-3.1-pro-preview`) hit 503 high-demand. Prefer `-latest` aliases for new model defaults; `client.models.list()` shows what's currently available.
