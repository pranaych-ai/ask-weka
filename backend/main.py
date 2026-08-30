import json
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
from .ai_safety import SAFE_REFUSAL, screen_answer, screen_question
from .prompts import build_system_prompt
from .providers import get_provider

# How many recent messages to send to the model. Older turns are dropped for
# the POC; summarization of older turns is a later-phase improvement.
HISTORY_BUDGET = 30
# Chars of model output withheld behind the safety gate while streaming; any
# violating pattern is shorter than this, so it is caught before release.
SAFETY_HOLDBACK = 600

Base.metadata.create_all(bind=engine)

# Enforce the dev/prod environment boundary before touching any data: a
# database stamped for one environment refuses to serve the other.
from .env import assert_environment_boundary, log_environment_summary  # noqa: E402

assert_environment_boundary(engine)
log_environment_summary()

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

    # conversations.domain (per-conversation HR/IT scope filter)
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
            "domain VARCHAR(20) NOT NULL DEFAULT ''"
        ))
    else:
        _ccols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(conversations)")]
        if "domain" not in _ccols:
            _conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN domain VARCHAR(20) NOT NULL DEFAULT ''"
            )

    # feedback.review_status (added for the QA dashboard)
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS "
            "review_status VARCHAR(20) NOT NULL DEFAULT 'open'"
        ))
    else:
        _fcols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(feedback)")]
        if "review_status" not in _fcols:
            _conn.exec_driver_sql(
                "ALTER TABLE feedback ADD COLUMN review_status VARCHAR(20) "
                "NOT NULL DEFAULT 'open'"
            )

    # kb_section_versions.position (added so revert restores ordering)
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE kb_section_versions ADD COLUMN IF NOT EXISTS "
            "\"position\" INTEGER NOT NULL DEFAULT 0"
        ))
    else:
        _vcols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(kb_section_versions)")]
        if _vcols and "position" not in _vcols:
            _conn.exec_driver_sql(
                "ALTER TABLE kb_section_versions ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
            )

    # api_keys.enabled/allowed_domains + api_key_usage.question/on_behalf_of
    # (Ask-WEKA-as-a-service: per-app off-switch, domain scoping, request log)
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS "
            "enabled BOOLEAN NOT NULL DEFAULT TRUE"
        ))
        _conn.execute(_text(
            "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS "
            "allowed_domains VARCHAR(60) NOT NULL DEFAULT ''"
        ))
        _conn.execute(_text(
            "ALTER TABLE api_key_usage ADD COLUMN IF NOT EXISTS "
            "question VARCHAR(500) NOT NULL DEFAULT ''"
        ))
        _conn.execute(_text(
            "ALTER TABLE api_key_usage ADD COLUMN IF NOT EXISTS "
            "on_behalf_of VARCHAR(120) NOT NULL DEFAULT ''"
        ))
        _conn.execute(_text(
            "ALTER TABLE api_key_usage ADD COLUMN IF NOT EXISTS "
            "user_verified BOOLEAN NOT NULL DEFAULT FALSE"
        ))
    else:
        _kcols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(api_keys)")]
        if _kcols and "enabled" not in _kcols:
            _conn.exec_driver_sql(
                "ALTER TABLE api_keys ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1"
            )
        if _kcols and "allowed_domains" not in _kcols:
            _conn.exec_driver_sql(
                "ALTER TABLE api_keys ADD COLUMN allowed_domains VARCHAR(60) NOT NULL DEFAULT ''"
            )
        _ucols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(api_key_usage)")]
        if _ucols and "question" not in _ucols:
            _conn.exec_driver_sql(
                "ALTER TABLE api_key_usage ADD COLUMN question VARCHAR(500) NOT NULL DEFAULT ''"
            )
        if _ucols and "on_behalf_of" not in _ucols:
            _conn.exec_driver_sql(
                "ALTER TABLE api_key_usage ADD COLUMN on_behalf_of VARCHAR(120) NOT NULL DEFAULT ''"
            )
        if _ucols and "user_verified" not in _ucols:
            _conn.exec_driver_sql(
                "ALTER TABLE api_key_usage ADD COLUMN user_verified BOOLEAN NOT NULL DEFAULT 0"
            )

    # golden_results.ai_verdict / ai_reasoning (LLM-as-a-judge grading)
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE golden_results ADD COLUMN IF NOT EXISTS "
            "ai_verdict VARCHAR(10) NOT NULL DEFAULT ''"
        ))
        _conn.execute(_text(
            "ALTER TABLE golden_results ADD COLUMN IF NOT EXISTS "
            "ai_reasoning TEXT NOT NULL DEFAULT ''"
        ))
    else:
        _gcols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(golden_results)")]
        if _gcols and "ai_verdict" not in _gcols:
            _conn.exec_driver_sql(
                "ALTER TABLE golden_results ADD COLUMN ai_verdict VARCHAR(10) NOT NULL DEFAULT ''"
            )
        if _gcols and "ai_reasoning" not in _gcols:
            _conn.exec_driver_sql(
                "ALTER TABLE golden_results ADD COLUMN ai_reasoning TEXT NOT NULL DEFAULT ''"
            )

    # conversations.resolution ("" | "solved" | "ticket") — solve-first metric
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
            "resolution VARCHAR(20) NOT NULL DEFAULT ''"
        ))
    else:
        _rcols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(conversations)")]
        if _rcols and "resolution" not in _rcols:
            _conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN resolution VARCHAR(20) NOT NULL DEFAULT ''"
            )

    # tickets.jira_key / jira_url — per-user Jira filing (Atlassian OAuth)
    if _dialect == "postgresql":
        _conn.execute(_text(
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_key VARCHAR(30) NOT NULL DEFAULT ''"
        ))
        _conn.execute(_text(
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_url VARCHAR(300) NOT NULL DEFAULT ''"
        ))
    else:
        _tcols = [r[1] for r in _conn.exec_driver_sql("PRAGMA table_info(tickets)")]
        if _tcols and "jira_key" not in _tcols:
            _conn.exec_driver_sql(
                "ALTER TABLE tickets ADD COLUMN jira_key VARCHAR(30) NOT NULL DEFAULT ''"
            )
        if _tcols and "jira_url" not in _tcols:
            _conn.exec_driver_sql(
                "ALTER TABLE tickets ADD COLUMN jira_url VARCHAR(300) NOT NULL DEFAULT ''"
            )

    # Unique history versions per KB section (works on postgres and sqlite)
    _conn.execute(_text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_section_version "
        "ON kb_section_versions (section_id, version)"
    ))

# One-time anonymization: hash any pre-existing plaintext feedback usernames
# so no name stays attached to what people asked (feedback is anonymous).
def _anonymize_legacy_feedback() -> None:
    import hashlib
    import re

    _hex64 = re.compile(r"^[0-9a-f]{64}$")
    db = SessionLocal()
    try:
        rows = db.query(Feedback).all()
        changed = False
        for f in rows:
            if f.username and not _hex64.match(f.username):
                f.username = hashlib.sha256(
                    f.username.strip().lower().encode()
                ).hexdigest()
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()


_anonymize_legacy_feedback()

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
from .api_keys import router as keys_router  # noqa: E402
from .kb_admin import router as kb_router  # noqa: E402
from .knowledge import import_legacy_file_if_empty  # noqa: E402
from .mcp_server import router as mcp_router  # noqa: E402
from .public_api import router as public_router  # noqa: E402
from .qa import router as qa_router  # noqa: E402
from .jira_oauth import admin_router as jira_admin_router  # noqa: E402
from .jira_oauth import router as jira_router  # noqa: E402
from .slack_app import router as slack_router  # noqa: E402
from .slack_admin import prefs_router as slack_prefs_router  # noqa: E402
from .slack_admin import router as slack_admin_router  # noqa: E402
from . import slack_scheduler  # noqa: E402
from .tickets import admin_router as tickets_admin_router  # noqa: E402
from .tickets import router as tickets_router  # noqa: E402

# One-time import of the legacy knowledge/kb.md export into the database.
import_legacy_file_if_empty()

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(qa_router)
app.include_router(kb_router)
app.include_router(keys_router)
app.include_router(public_router)
app.include_router(mcp_router)
app.include_router(tickets_router)
app.include_router(tickets_admin_router)
app.include_router(slack_router)
app.include_router(slack_admin_router)
app.include_router(slack_prefs_router)
app.include_router(jira_router)
app.include_router(jira_admin_router)


@app.on_event("startup")
async def _start_slack_scheduler():
    """Single-instance digest scheduler tied to the app lifecycle."""
    slack_scheduler.start()


@app.on_event("shutdown")
async def _stop_slack_scheduler():
    await slack_scheduler.stop()


from . import alerts  # noqa: E402


@app.middleware("http")
async def _server_error_alerting(request: Request, call_next):
    """Count unhandled server failures and alert owners on error spikes.

    Only the route path is recorded — never query strings, headers, or
    bodies, which could carry content or tokens.
    """
    try:
        response = await call_next(request)
    except Exception:
        alerts.record_server_error(request.url.path)
        raise
    if response.status_code >= 500:
        alerts.record_server_error(request.url.path)
    return response


@app.get("/api/healthz")
def healthz():
    """Unauthenticated liveness probe for deployment health checks."""
    return {"ok": True}


# ---------- Conversations API ----------


class ConversationOut(BaseModel):
    id: str
    title: str
    domain: str
    resolution: str
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
        ConversationOut(
            id=c.id,
            title=c.title,
            domain=c.domain,
            resolution=c.resolution,
            updated_at=c.updated_at.isoformat(),
        )
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

from .analysis import classify_domain as _classify_domain  # noqa: E402
from .analysis import extract_sources as _extract_sources  # noqa: E402
from .analysis import summarize as _summarize  # noqa: E402


def _rater_hash(username: str) -> str:
    """Keyed pseudonym of the rater (HMAC with the server secret): enables
    one-feedback-per-person per message without storing who rated what, and
    cannot be reversed by hashing a list of known employee names."""
    import hashlib
    import hmac as _hmac

    key = (SESSION_SECRET or "dev-only-insecure").encode()
    return _hmac.new(key, username.strip().lower().encode(), hashlib.sha256).hexdigest()


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

    # Feedback is anonymous by design: only a one-way hash of the username is
    # stored (needed to keep one feedback row per message per person), and it
    # is never exposed through the admin API or UI.
    rater = _rater_hash(user["username"])

    # One feedback row per (message, user): update in place so a changed or
    # cleared thumb never leaves contradictory rows behind.
    fb = (
        db.query(Feedback)
        .filter(Feedback.message_id == msg.id, Feedback.username == rater)
        .first()
    )
    if not fb:
        fb = Feedback(message_id=msg.id, username=rater)
        db.add(fb)
    fb.question = question.strip()
    fb.answer_summary = _summarize(answer)
    fb.thumbs = req.thumbs or ""
    if req.feedback_text is not None:
        fb.feedback_text = req.feedback_text.strip()
    fb.logged_time = datetime.now(timezone.utc)
    fb.cited_sources = "\n".join(_extract_sources(answer))
    fb.domain = _classify_domain(question, answer)
    # Audit records that feedback was submitted, but never which question it
    # was for — feedback must stay anonymous, so no message/conversation ids.
    log_event(
        db,
        user["username"],
        "feedback.submit",
        f"thumbs={fb.thumbs or 'none'} has_text={bool(fb.feedback_text)}",
    )
    db.commit()
    return {"ok": True, "id": fb.id}


# ---------- Suggested starter questions ----------

from .models import GoldenQuestion  # noqa: E402

DEFAULT_SUGGESTIONS = [
    {"question": "How do I request a new laptop?", "domain": "IT"},
    {"question": "What benefits does WEKA offer?", "domain": "HR"},
    {"question": "How do I reset my Okta password?", "domain": "IT"},
    {"question": "How do I submit a PTO request?", "domain": "HR"},
]


@app.get("/api/suggestions")
def suggestions(db: Session = Depends(get_db), user: dict = Depends(require_user)):
    """Starter questions for the empty state, drawn from the golden-question
    suite (admin-curated, known-good) with static fallbacks."""
    rows = (
        db.query(GoldenQuestion)
        .filter(GoldenQuestion.active == True)  # noqa: E712
        .order_by(GoldenQuestion.created_at.desc())
        .limit(6)
        .all()
    )
    out = [{"question": g.question, "domain": g.domain} for g in rows]
    for d in DEFAULT_SUGGESTIONS:
        if len(out) >= 6:
            break
        if all(d["question"] != o["question"] for o in out):
            out.append(d)
    return out[:6]


# ---------- Chat (SSE streaming) ----------


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    domain: str = ""  # "" | "HR" | "IT" — optional scope filter


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest, user: dict = Depends(require_user)):
    if not req.message.strip():
        raise HTTPException(400, "Empty message")
    if req.domain not in ("", "HR", "IT"):
        raise HTTPException(400, "domain must be HR, IT, or empty")

    # Persist the user message (and create the conversation if needed) before
    # streaming starts, so history survives even if the stream dies mid-way.
    db = SessionLocal()
    try:
        if req.conversation_id:
            conv = db.get(Conversation, req.conversation_id)
            if not conv or conv.username != user["username"]:
                raise HTTPException(404, "Conversation not found")
            # Scope is fixed at conversation creation; the stored value wins
            # over whatever the client sends for later turns.
            domain = conv.domain
        else:
            domain = req.domain
            title = req.message.strip().splitlines()[0][:60]
            conv = Conversation(title=title, username=user["username"], domain=domain)
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
    system = build_system_prompt(domain)

    inbound = screen_question(req.message)
    if inbound.findings:
        db3 = SessionLocal()
        try:
            log_event(
                db3,
                user["username"],
                "chat.injection_flagged",
                f"conversation_id={conversation_id} findings={','.join(inbound.findings)}",
            )
            db3.commit()
        finally:
            db3.close()

    # Streaming safety: text is released to the browser SAFETY_HOLDBACK chars
    # behind what the model has produced, and the *entire* accumulated text is
    # screened before every release. Any violating pattern is therefore still
    # inside the unreleased holdback window when detected, so no part of it
    # ever reaches the client; the stream is replaced with a safe refusal.
    async def event_stream():
        yield _sse({"conversation_id": conversation_id, "title": conversation_title})
        full_response = []
        assistant_message_id = None
        blocked_findings: list[str] = []
        released = 0
        try:
            async for chunk in provider.stream_chat(system, history):
                full_response.append(chunk)
                text = "".join(full_response)
                gate = screen_answer(text)
                if gate.blocked:
                    blocked_findings = gate.findings
                    full_response = [SAFE_REFUSAL]
                    yield _sse({"error": SAFE_REFUSAL, "safety_blocked": True})
                    break
                safe_len = len(text) - SAFETY_HOLDBACK
                if safe_len > released:
                    yield _sse({"delta": text[released:safe_len]})
                    released = safe_len
            else:
                # Stream ended cleanly: the complete text passed the gate on
                # the final chunk, so the holdback window can be released.
                text = "".join(full_response)
                if len(text) > released:
                    yield _sse({"delta": text[released:]})
        except Exception as e:  # surface provider errors to the UI
            alerts.record_ai_failure(type(e).__name__)
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
                    if blocked_findings:
                        log_event(
                            db2,
                            user["username"],
                            "chat.safety_blocked",
                            f"conversation_id={conversation_id} "
                            f"findings={','.join(blocked_findings)}",
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
        # index.html must never be cached: it references hashed asset names,
        # and a stale copy makes browsers load an outdated (or missing) bundle.
        return FileResponse(
            DIST / "index.html", headers={"Cache-Control": "no-cache"}
        )
