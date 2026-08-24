"""Shared answer-analysis helpers (source extraction, domain classification)."""

import re

HR_KEYWORDS = (
    "hr", "payroll", "benefits?", "leaves?", "vacation", "pto", "hiring",
    "onboarding", "offboarding", "salary", "compensation", "recruit(?:ing|er|ment)?",
    "insurance", "401k", "holidays?", "maternity", "paternity", "bamboohr?",
)
_HR_RE = re.compile(r"\b(?:" + "|".join(HR_KEYWORDS) + r")\b", re.IGNORECASE)

_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<!\()https?://[^\s)\]>\"']+")


def extract_sources(answer: str) -> list[str]:
    sources = _MD_LINK.findall(answer)
    for url in _BARE_URL.findall(answer):
        if url not in sources:
            sources.append(url)
    return sources


def classify_domain(question: str, answer: str) -> str:
    return "HR" if _HR_RE.search(f"{question}\n{answer}") else "IT"


def summarize(answer: str, limit: int = 300) -> str:
    # Strip markdown links/formatting, collapse whitespace, truncate.
    text = _MD_LINK.sub(lambda m: m.group(0).split("]")[0][1:], answer)
    text = re.sub(r"[#*`>_|-]{1,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")
