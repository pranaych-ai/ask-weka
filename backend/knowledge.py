"""Knowledge base loader.

The KB lives in the database as sections (kb_sections); admins manage them in
the portal and changes take effect immediately — no redeploy. The assistant
prompt is assembled from active sections in order.

A one-time import seeds the tables from the legacy knowledge/kb.md export the
first time the app starts with an empty kb_sections table.

When the KB outgrows the context window, this module is the seam where
retrieval (pgvector RAG) replaces the full dump.
"""

import os
import re
from pathlib import Path

from sqlalchemy import func

from .db import SessionLocal
from .models import KBSection

KB_PATH = Path(os.getenv("KB_PATH", Path(__file__).resolve().parent.parent / "knowledge" / "kb.md"))

# Cache keyed on a cheap DB stamp (count + latest update) so edits invalidate
# it automatically across requests without a restart.
_cache: dict = {"stamp": None, "text": ""}

_HR_HINT = re.compile(r"\bHR\b|human resources|benefits|payroll|onboarding", re.IGNORECASE)
_IT_HINT = re.compile(r"\bIT\b|service portal|software|security|network", re.IGNORECASE)


def _guess_domain(title: str) -> str:
    if _HR_HINT.search(title):
        return "HR"
    if _IT_HINT.search(title):
        return "IT"
    return ""


def split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split a markdown document into (title, body) chunks on H1/H2 headings.

    Content before the first heading becomes a "Preamble" section. Very small
    trailing fragments stay attached to their heading.
    """
    sections: list[tuple[str, list[str]]] = []
    current_title = "Preamble"
    current_lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^#{1,2}\s+(.*)", line)
        if m:
            if current_lines and "".join(current_lines).strip():
                sections.append((current_title, current_lines))
            current_title = m.group(1).strip() or "Untitled"
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines and "".join(current_lines).strip():
        sections.append((current_title, current_lines))
    return [(t[:300], "\n".join(ls).strip()) for t, ls in sections]


def import_legacy_file_if_empty() -> int:
    """One-time import of knowledge/kb.md into kb_sections. Returns sections created."""
    db = SessionLocal()
    try:
        if db.query(KBSection.id).first() is not None:
            return 0
        try:
            text = KB_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        created = 0
        for pos, (title, body) in enumerate(split_markdown_sections(text)):
            db.add(
                KBSection(
                    title=title,
                    domain=_guess_domain(title),
                    body=body,
                    position=pos,
                    updated_by="system-import",
                )
            )
            created += 1
        db.commit()
        return created
    finally:
        db.close()


def _stamp(db) -> tuple:
    return (
        db.query(func.count(KBSection.id)).scalar(),
        str(db.query(func.max(KBSection.updated_at)).scalar()),
    )


def load_knowledge() -> str:
    """Assemble the KB text from active sections (cached until sections change)."""
    db = SessionLocal()
    try:
        stamp = _stamp(db)
        if _cache["stamp"] != stamp:
            rows = (
                db.query(KBSection)
                .filter(KBSection.active == True)  # noqa: E712
                .order_by(KBSection.position.asc(), KBSection.created_at.asc())
                .all()
            )
            _cache["text"] = "\n\n".join(s.body for s in rows if s.body.strip())
            _cache["stamp"] = stamp
        return _cache["text"]
    finally:
        db.close()
