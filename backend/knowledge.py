"""Knowledge base loader.

POC approach: the entire KB lives in knowledge/kb.md (exported from Notion) and
is injected into the system prompt on every request. The file is re-read only
when its mtime changes, so you can update the KB without restarting the app.

When the KB outgrows the context window, this module is the seam where
retrieval (pgvector RAG) replaces the full-file dump.
"""

import os
from pathlib import Path

KB_PATH = Path(os.getenv("KB_PATH", Path(__file__).resolve().parent.parent / "knowledge" / "kb.md"))

_cache: dict = {"mtime": None, "text": ""}


def load_knowledge() -> str:
    try:
        mtime = KB_PATH.stat().st_mtime
    except FileNotFoundError:
        return ""
    if _cache["mtime"] != mtime:
        _cache["text"] = KB_PATH.read_text(encoding="utf-8")
        _cache["mtime"] = mtime
    return _cache["text"]
