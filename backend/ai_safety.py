"""AI safety gate: screens Gemini inputs and outputs before release.

Every path that sends model text to a user or to Slack (web chat, public API,
Slack DM, Slack digest) calls ``screen_answer`` first. The checks are
deterministic (regex-based) on purpose: they add no latency, no extra model
calls, and their behaviour is fully covered by the test suite — a second LLM
"guard" would itself be an unevaluated probabilistic component.

What the gate catches:
- **Secret-shaped strings** — API keys, private-key blocks, bearer tokens,
  password assignments. The KB should never contain these, so any appearance
  in an answer means either KB contamination or model hallucination; both are
  blocked.
- **System-prompt / KB-envelope leakage** — the literal prompt markers
  (``BEGIN KNOWLEDGE BASE`` etc.) or distinctive system-prompt sentences
  appearing in an answer indicate a successful "repeat your instructions"
  injection.
- **Injection compliance markers** — phrases that indicate the model adopted
  attacker instructions ("ignoring my previous instructions", role-switch
  admissions).

``screen_question`` additionally flags (but does not block) inbound prompt-
injection attempts so they can be audited; blocking questions outright would
break legitimate queries that merely *mention* injection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

SAFE_REFUSAL = (
    "I can't share that response — it was withheld by Ask WEKA's safety "
    "checks. Please rephrase your question, or contact #it-help if you "
    "believe this is an error."
)

# ---------------------------------------------------------------- findings


@dataclass
class ScreenResult:
    blocked: bool = False
    findings: list[str] = field(default_factory=list)


# ------------------------------------------------------------ output rules

# Secret-shaped content. Each pattern is (name, compiled regex).
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    ("jwt_token", re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\b")),
    (
        "password_assignment",
        re.compile(
            r"(?i)\b(password|passwd|api[_ ]?key|secret[_ ]?key|client[_ ]?secret|access[_ ]?token)\b"
            r"\s*(is|=|:)\s*[\"'`]?[A-Za-z0-9_\-!@#$%^&*+/=]{8,}"
        ),
    ),
    (
        "connection_string_credentials",
        re.compile(r"(?i)\b\w+://[^\s/:@]+:[^\s/@]{4,}@[^\s/@]+"),
    ),
]

# The exact envelope markers used by backend/prompts.py plus distinctive
# system-prompt sentences. Their appearance in an *answer* means the model was
# talked into echoing its instructions or raw KB envelope.
_PROMPT_LEAK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("kb_envelope_marker", re.compile(r"---\s*(BEGIN|END) KNOWLEDGE BASE\s*---")),
    (
        "system_prompt_echo",
        re.compile(
            r"(?i)(everything between the KNOWLEDGE BASE markers is reference material"
            r"|You are Ask WEKA, an internal AI assistant)"
        ),
    ),
]

_INJECTION_COMPLIANCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "injection_compliance",
        re.compile(
            r"(?i)\b(ignor(?:e|ing) (?:my|the|all) (?:previous|prior|above) instructions"
            r"|as an unrestricted AI"
            r"|I (?:am now|will now act as) DAN\b"
            r"|developer mode enabled)"
        ),
    ),
]


def screen_answer(answer: str) -> ScreenResult:
    """Screen a complete or partially-accumulated model answer.

    Returns ``blocked=True`` when the answer must not be released. Callers
    replace the answer with :data:`SAFE_REFUSAL` and audit the findings.
    """
    findings: list[str] = []
    for name, pat in _SECRET_PATTERNS + _PROMPT_LEAK_PATTERNS + _INJECTION_COMPLIANCE_PATTERNS:
        if pat.search(answer or ""):
            findings.append(name)
    if findings:
        log.warning("AI safety gate blocked an answer: %s", ",".join(findings))
        return ScreenResult(blocked=True, findings=findings)
    return ScreenResult()


# ------------------------------------------------------------- input rules

_INJECTION_INPUT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "override_instructions",
        re.compile(
            r"(?i)\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all|your)\b"
            r".{0,20}\b(instruction|prompt|rule)s?\b"
        ),
    ),
    (
        "prompt_extraction",
        re.compile(
            r"(?i)\b(repeat|print|reveal|show|output|display)\b.{0,40}"
            r"\b(system prompt|initial instructions|hidden instructions|"
            r"knowledge base markers|everything above)\b"
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"(?i)\b(you are now|pretend (?:to be|you are)|act as)\b.{0,60}"
            r"\b(DAN|unrestricted|no (?:rules|restrictions|filter)|jailbreak)\b"
        ),
    ),
    (
        "verbatim_dump",
        re.compile(
            r"(?i)\b(dump|paste|output|print)\b.{0,30}\b(entire|full|whole|raw)\b"
            r".{0,30}\b(knowledge base|kb|context|prompt)\b"
        ),
    ),
]


def screen_question(question: str) -> ScreenResult:
    """Flag likely prompt-injection attempts in a user question.

    Never blocks — a flagged question still gets answered (the system prompt
    and the output gate are the enforcement layers) but the attempt is
    surfaced so callers can audit-log it.
    """
    findings = [
        name for name, pat in _INJECTION_INPUT_PATTERNS if pat.search(question or "")
    ]
    if findings:
        log.info("Possible prompt-injection attempt flagged: %s", ",".join(findings))
    return ScreenResult(blocked=False, findings=findings)
