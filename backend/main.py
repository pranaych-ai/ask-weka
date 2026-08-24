import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .admin import router as admin_router
from .audit import log_event
from .auth import AUTH_ENABLED, require_user
from .auth import router as auth_router
from .db import Base, engine, get_db, SessionLocal
from .models import Conversation, Feedback, Message
from .prompts import build_system_prompt
from .providers import get_provider

# How many recent messages to send to the model. Older turns are dropped for
# the POC; summarization of older turns is a later-phase improvement.
HISTORY_BUDGET = 30

Base.metadata.create_all(bind=engine)

# Lightweight migration: add conversations.username for pre-existing tables
# (create_all does not alter existing tables).
with engine.begin() as _conn:
    from sqlalchemy import text as _text

    _dialect = engine.dialect.name
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
            "username VARCHAR(120) NOT NULL DEFAULT 'anonymous'"
        ))
        _conn.execute(_text(
            "CREATE INDEX IF NOT EXISTS ix_conversations_username "
            "ON conversations (username)"
        ))
    else:  # sqlite (local dev)
        _cols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(conversations)")]
        if "username" not in _cols:
            _conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN username VARCHAR(120) "
                "NOT NULL DEFAULT 'anonymous'"
            )

app = FastAPI(title="Ask WEKA POC")

import os

from starlette.middleware.sessions import SessionMiddleware

# Fail closed in production: never run deployed without SSO and a real secret.
IS_DEPLOYMENT = bool(os.environ.get("REPLIT_DEPLOYMENT"))
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if IS_DEPLOYMENT:
    if not AUTH_ENABLED:
        raise RuntimeError(
            "Refusing to start in production without Okta SSO configured "
            "(OKTA_ISSUER / OKTA_CLIENT_ID / OKTA_CLIENT_SECRET)."
        )
    if len(SESSION_SECRET) < 32:
        raise RuntimeError(
            "Refusing to start in production without a strong SESSION_SECRET "
            "(>= 32 chars) set in deployment secrets."
        )

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET or "dev-only-insecure",
    max_age=8 * 3600,  # cookie lifetime = idle limit; absolute enforced in auth.py
    same_site="lax",
    https_only=IS_DEPLOYMENT,  # Secure cookies in prod; TLS terminates at the Replit proxy
)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/api/healthz")
def healthz():
    """Unauthenticated liveness probe for deployment health checks."""
    return {"ok": True}


# ---------- Conversations API ----------


class ConversationOut(BaseModel):
    id: str
    title: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str


@app.get("/api/conversations")
def list_conversations(
    db: Session = Depends(get_db), user: dict = Depends(require_user)
) -> list[ConversationOut]:
    rows = (
        db.query(Conversation)
        .filter(Conversation.username == user["username"])
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return [
        ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at.isoformat())
        for c in rows
    ]


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
) -> list[MessageOut]:
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.username != user["username"]:
        raise HTTPException(404, "Conversation not found")
    return [MessageOut(id=m.id, role=m.role, content=m.content) for m in conv.messages]


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.username != user["username"]:
        raise HTTPException(404, "Conversation not found")
    db.delete(conv)
    log_event(db, user["username"], "conversation.delete", f"id={conversation_id}")
    db.commit()
    return {"ok": True}


# ---------- Feedback ----------

HR_KEYWORDS = (
    "hr", "payroll", "benefits?", "leaves?", "vacation", "pto", "hiring",
    "onboarding", "offboarding", "salary", "compensation", "recruit(?:ing|er|ment)?",
    "insurance", "401k", "holidays?", "maternity", "paternity", "bamboohr?",
)
_HR_RE = re.compile(r"\b(?:" + "|".join(HR_KEYWORDS) + r")\b", re.IGNORECASE)

_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<!\()https?://[^\s)\]>\"']+")


def _extract_sources(answer: str) -> list[str]:
    sources = _MD_LINK.findall(answer)
    for url in _BARE_URL.findall(answer):
        if url not in sources:
            sources.append(url)
    return sources


def _classify_domain(question: str, answer: str) -> str:
    return "HR" if _HR_RE.search(f"{question}\n{answer}") else "IT"


def _summarize(answer: str, limit: int = 300) -> str:
    # Strip markdown links/formatting, collapse whitespace, truncate.
    text = _MD_LINK.sub(lambda m: m.group(0).split("]")[0][1:], answer)
    text = re.sub(r"[#*`>_|-]{1,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class FeedbackRequest(BaseModel):
    message_id: str
    thumbs: Optional[str] = None  # "up" | "down" | None (clear)
    feedback_text: Optional[str] = None  # None = leave unchanged


@app.post("/api/feedback")
def submit_feedback(
    req: FeedbackRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    if req.thumbs not in (None, "", "up", "down"):
        raise HTTPException(400, "thumbs must be 'up' or 'down'")
    if req.feedback_text is not None and len(req.feedback_text) > 5000:
        raise HTTPException(400, "Feedback text too long (max 5000 chars)")

    # Question and answer are loaded server-side from the stored message —
    # never trusted from the client.
    msg = db.get(Message, req.message_id)
    if not msg or msg.role != "assistant" or msg.conversation.username != user["username"]:
        raise HTTPException(404, "Assistant message not found")
    answer = msg.content
    question = ""
    for m in msg.conversation.messages:
        if m.id == msg.id:
            break
        if m.role == "user":
            question = m.content

    username = user["username"]

    # One feedback row per (message, user): update in place so a changed or
    # cleared thumb never leaves contradictory rows behind.
    fb = (
        db.query(Feedback)
        .filter(Feedback.message_id == msg.id, Feedback.username == username)
        .first()
    )
    if not fb:
        fb = Feedback(message_id=msg.id, username=username)
        db.add(fb)
    fb.question = question.strip()
    fb.answer_summary = _summarize(answer)
    fb.thumbs = req.thumbs or ""
    if req.feedback_text is not None:
        fb.feedback_text = req.feedback_text.strip()
    fb.logged_time = datetime.now(timezone.utc)
    fb.cited_sources = "\n".join(_extract_sources(answer))
    fb.domain = _classify_domain(question, answer)
    log_event(
        db,
        username,
        "feedback.submit",
        f"message_id={msg.id} thumbs={fb.thumbs or 'none'} has_text={bool(fb.feedback_text)}",
    )
    db.commit()
    return {"ok": True, "id": fb.id}


# ---------- Chat (SSE streaming) ----------


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest, user: dict = Depends(require_user)):
    if not req.message.strip():
        raise HTTPException(400, "Empty message")

    # Persist the user message (and create the conversation if needed) before
    # streaming starts, so history survives even if the stream dies mid-way.
    db = SessionLocal()
    try:
        if req.conversation_id:
            conv = db.get(Conversation, req.conversation_id)
            if not conv or conv.username != user["username"]:
                raise HTTPException(404, "Conversation not found")
        else:
            title = req.message.strip().splitlines()[0][:60]
            conv = Conversation(title=title, username=user["username"])
            db.add(conv)
            db.flush()  # assign conv.id so audit details reference IDs, never content
            log_event(db, user["username"], "conversation.create", f"id={conv.id}")

        conv.messages.append(Message(role="user", content=req.message))
        log_event(db, user["username"], "chat.message", f"conversation_id={conv.id}")
        db.commit()
        conversation_id = conv.id
        conversation_title = conv.title
        history = [
            {"role": m.role, "content": m.content}
            for m in conv.messages[-HISTORY_BUDGET:]
        ]
    finally:
        db.close()

    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        raise HTTPException(503, str(e))
    system = build_system_prompt()

    async def event_stream():
        yield _sse({"conversation_id": conversation_id, "title": conversation_title})
        full_response = []
        assistant_message_id = None
        try:
            async for chunk in provider.stream_chat(system, history):
                full_response.append(chunk)
                yield _sse({"delta": chunk})
        except Exception as e:  # surface provider errors to the UI
            yield _sse({"error": str(e)})
        finally:
            if full_response:
                db2 = SessionLocal()
                try:
                    msg = Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content="".join(full_response),
                    )
                    db2.add(msg)
                    conv2 = db2.get(Conversation, conversation_id)
                    conv2.updated_at = datetime.now(timezone.utc)
                    db2.flush()  # assign msg.id for the audit record
                    log_event(
                        db2,
                        user["username"],
                        "chat.response",
                        f"conversation_id={conversation_id} message_id={msg.id}",
                    )
                    db2.commit()
                    assistant_message_id = msg.id
                finally:
                    db2.close()
        yield _sse({"done": True, "message_id": assistant_message_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- Frontend (built React app) ----------

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(DIST / "index.html")
