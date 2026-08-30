#!/usr/bin/env python3
"""Run the live AI safety evaluation suite against the real Gemini pipeline.

Usage:  python scripts/run_safety_eval.py   (requires GEMINI_API_KEY)

Sends every case in backend/safety_eval.py's corpus through the production
prompt + provider, applies the production output gate, and prints a
per-case verdict plus a summary suitable for pasting into
docs/AI_SAFETY_EVALUATION.md. Exits non-zero if any case fails.
"""

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.safety_eval import run_live_eval  # noqa: E402


async def main() -> int:
    results = await run_live_eval()
    by_cat: Counter = Counter()
    failed = []
    for r in results:
        by_cat[(r.category, r.passed)] += 1
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.case_id:28s} ({r.category}) — {r.detail}")
        if not r.passed:
            failed.append(r)
            if r.answer_excerpt:
                print(f"       answer: {r.answer_excerpt!r}")
    print()
    cats = sorted({c for c, _ in by_cat})
    for cat in cats:
        print(f"{cat}: {by_cat[(cat, True)]} passed, {by_cat[(cat, False)]} failed")
    print(f"TOTAL: {sum(1 for r in results if r.passed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
