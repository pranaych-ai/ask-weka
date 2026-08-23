---
name: Gemini model retirement
description: Why GEMINI_MODEL is pinned and what breaks if it's unset
---
The app's coded default Gemini model (`gemini-2.5-flash`) was retired by Google (404 "no longer available to new users"). `GEMINI_MODEL=gemini-3.6-flash` is set as a shared env var to override it.

**Why:** chat requests stream back a 404 error event when the model is unavailable.
**How to apply:** if chat suddenly returns model 404s again, bump `GEMINI_MODEL` to the current Gemini flash model rather than editing code.
