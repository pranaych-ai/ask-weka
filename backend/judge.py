"""LLM-as-a-judge for golden-question answers.

Uses a *separate* Gemini model from the chat assistant (GEMINI_JUDGE_MODEL,
default gemini-2.5-pro vs the assistant's gemini-2.5-flash) so the grader is
independent of — and stronger than — the model being graded. Human verdicts
always override AI verdicts.
"""

import json
import logging
import os

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

JUDGE_PROMPT = """You are a strict QA judge for an internal company AI assistant.
You are given a test QUESTION, the EXPECTED TOPIC a good answer must cover,
and the assistant's ANSWER. Grade whether the answer correctly and helpfully
covers the expected topic.

Grade "fail" when the answer: misses or contradicts the expected topic, is
evasive or generic where specifics were expected, hallucinates policies, or
tells the user it cannot help when the expected topic shows it should.
Grade "pass" only when a knowledgeable employee would be well served.

Respond with JSON only: {"verdict": "pass" | "fail", "reasoning": "<1-3 concise sentences>"}"""

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=api_key)
    return _client


def judge_model() -> str:
    # gemini-pro-latest: stable alias that tracks the current pro-tier model,
    # so judge grading survives individual model retirements. Distinct from the
    # chat assistant's flash-tier model by design.
    return os.getenv("GEMINI_JUDGE_MODEL", "gemini-pro-latest")


async def judge_answer(question: str, expected_topic: str, answer: str) -> dict:
    """Grade one answer. Returns {"verdict": "pass"|"fail", "reasoning": str}.

    Raises on API/parse failure — the caller records the failure and leaves
    the result ungraded rather than inventing a verdict.
    """
    user = (
        f"QUESTION:\n{question}\n\n"
        f"EXPECTED TOPIC:\n{expected_topic or '(none given — judge general quality)'}\n\n"
        f"ANSWER:\n{answer}"
    )
    resp = await _get_client().aio.models.generate_content(
        model=judge_model(),
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user)])],
        config=types.GenerateContentConfig(
            system_instruction=JUDGE_PROMPT,
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["pass", "fail"]},
                    "reasoning": {"type": "string"},
                },
                "required": ["verdict", "reasoning"],
            },
            temperature=0,
        ),
    )
    data = json.loads(resp.text)
    verdict = data.get("verdict", "")
    if verdict not in ("pass", "fail"):
        raise ValueError(f"Judge returned invalid verdict: {verdict!r}")
    return {"verdict": verdict, "reasoning": str(data.get("reasoning", ""))[:2000]}
