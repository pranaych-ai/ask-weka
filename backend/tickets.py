"""Solve-first ticketing: the assistant tries to fix the issue in chat; if it
can't, it drafts a ticket the employee reviews and approves before anything is
filed. Deflection (issues solved without a ticket) is the success metric."""

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .admin import admin_audited
from .audit import log_event
from .auth import require_user
from .db import get_db
from .jira_oauth import JiraAccount, create_jira_issue, project_for_domain
from .models import Conversation, Ticket

router = APIRouter(prefix="/api/tickets")
admin_router = APIRouter(prefix="/api/admin/tickets", dependencies=[Depends(admin_audited)])

TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")


def _owned_conversation(db: Session, conversation_id: str, username: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.username != username:
        raise HTTPException(404, "Conversation not found")
    return conv


# ---------- Employee endpoints ----------


class ConversationRef(BaseModel):
    conversation_id: str


@router.post("/solved")
def mark_solved(
    req: ConversationRef,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    """Employee confirms the assistant fixed it — counts as a deflected ticket."""
    conv = _owned_conversation(db, req.conversation_id, user["username"])
    if conv.resolution == "ticket":
        raise HTTPException(400, "A ticket was already created for this conversation")
    conv.resolution = "solved"
    log_event(db, user["username"], "chat.solved", f"conversation_id={conv.id}")
    db.commit()
    return {"ok": True, "resolution": conv.resolution}


DRAFT_PROMPT = """You are drafting an internal support ticket from a chat transcript.
The employee tried to solve the problem with an AI assistant and it did not work.
Write a concise ticket a support agent can act on immediately.

Respond with ONLY a JSON object, no markdown fences, in this exact shape:
{"title": "<one line, max 90 chars>", "body": "<ticket text>"}

The body MUST use this structure (plain text, short lines):
Issue: <what is broken / needed, 1-3 sentences>
Already tried: <bullet list of every fix attempted in the chat and its outcome>
Additional context: <environment, error messages, anything else useful; omit if none>

Never invent steps that are not in the transcript. Do not include employee names."""


async def draft_ticket_content(db: Session, conv: Conversation, username: str) -> dict:
    """AI-drafts a ticket from an (already ownership-checked) conversation.
    Nothing is stored — the employee reviews, edits, and must explicitly
    approve before filing. Shared by the web endpoint and Slack's modal."""
    if conv.resolution == "ticket":
        raise HTTPException(400, "A ticket was already created for this conversation")
    if not conv.messages:
        raise HTTPException(400, "Conversation is empty")

    from .providers import get_provider

    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        raise HTTPException(503, str(e))

    transcript = "\n\n".join(
        f"{'Employee' if m.role == 'user' else 'Assistant'}: {m.content}"
        for m in conv.messages[-30:]
    )
    # Input screen: the transcript is user-controlled content going to Gemini.
    # Flag-and-audit only — the output gate below is the enforcement layer.
    from .ai_safety import screen_question

    inbound = screen_question(transcript)
    if inbound.findings:
        log_event(
            db,
            username,
            "chat.injection_flagged",
            f"via=ticket_draft conversation_id={conv.id} findings={','.join(inbound.findings)}",
        )
        db.commit()
    parts: list[str] = []
    try:
        async for chunk in provider.stream_chat(
            DRAFT_PROMPT, [{"role": "user", "content": transcript[:24000]}]
        ):
            parts.append(chunk)
    except Exception as e:
        raise HTTPException(502, f"Could not draft ticket: {e}")

    raw = "".join(parts).strip()
    # Output safety gate: never surface a secret-shaped or prompt-echoing
    # draft to the employee — fall back to a plain draft they write themselves.
    from .ai_safety import screen_answer

    gate = screen_answer(raw)
    if gate.blocked:
        log_event(
            db,
            username,
            "chat.safety_blocked",
            f"via=ticket_draft conversation_id={conv.id} findings={','.join(gate.findings)}",
        )
        db.commit()
        return {
            "title": conv.title[:200],
            "body": "(AI draft withheld by safety checks — please describe the issue.)",
            "domain": conv.domain,
        }
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
        title = str(data.get("title", "")).strip()[:200]
        body = str(data.get("body", "")).strip()
    except (ValueError, AttributeError):
        # Fall back to a usable draft rather than failing the flow.
        title = conv.title[:200]
        body = raw[:5000]
    if not title:
        title = conv.title[:200]
    return {"title": title, "body": body, "domain": conv.domain}


@router.post("/draft")
async def draft_ticket(
    req: ConversationRef,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    conv = _owned_conversation(db, req.conversation_id, user["username"])
    return await draft_ticket_content(db, conv, user["username"])


class TicketCreate(BaseModel):
    conversation_id: str
    title: str
    body: str


def _ticket_out(t: Ticket) -> dict:
    return {
        "id": t.id,
        "conversation_id": t.conversation_id,
        "username": t.username,
        "domain": t.domain,
        "title": t.title,
        "body": t.body,
        "status": t.status,
        "jira_key": t.jira_key,
        "jira_url": t.jira_url,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


async def file_ticket(
    db: Session, username: str, conversation_id: str, title: str, body: str
) -> Ticket:
    """Core solve-first filing flow, shared by the web endpoint and Slack's
    review/approve modal. Only ever called after the employee approved the
    (editable) draft. When the employee has connected Jira (per-user
    Okta-backed SSO), the ticket is also filed in Jira AS them, routed to the
    project mapped for the conversation's team. Raises HTTPException on any
    validation/authorization failure; commits on success."""
    title = title.strip()
    body = body.strip()
    if not title:
        raise HTTPException(400, "Title required")
    if len(title) > 200 or len(body) > 10000:
        raise HTTPException(400, "Title or body too long")
    conv = _owned_conversation(db, conversation_id, username)
    existing = db.query(Ticket).filter(Ticket.conversation_id == conv.id).first()
    if existing:
        raise HTTPException(400, "A ticket already exists for this conversation")

    acct = db.get(JiraAccount, username)
    project = ""
    if acct:
        project = project_for_domain(db, conv.domain)
        if not project:
            raise HTTPException(
                503,
                "No Jira project is configured for this team yet — "
                "ask an admin to set the Jira routing, or disconnect Jira to file locally",
            )

    # Reserve the local ticket FIRST: the DB unique constraint on
    # conversation_id makes this the lock that prevents two concurrent
    # approvals from filing duplicate Jira issues.
    t = Ticket(
        conversation_id=conv.id,
        username=username,
        domain=conv.domain,
        title=title,
        body=body,
    )
    conv.resolution = "ticket"
    db.add(t)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "A ticket already exists for this conversation")

    if acct:
        try:
            t.jira_key, t.jira_url = await create_jira_issue(db, acct, project, title, body)
        except HTTPException:
            # No half-filed state: if Jira rejects it, nothing is stored and
            # the employee can retry or fix the draft. (A token refresh may
            # have committed mid-flight, so also delete any persisted row.)
            db.rollback()
            leftover = db.query(Ticket).filter(Ticket.conversation_id == conv.id).first()
            if leftover and not leftover.jira_key:
                db.delete(leftover)
                c2 = db.get(Conversation, conv.id)
                if c2:
                    c2.resolution = ""
                db.commit()
            raise
    log_event(
        db,
        username,
        "ticket.create",
        f"id={t.id} conversation_id={conv.id}" + (f" jira={t.jira_key}" if t.jira_key else ""),
    )
    db.commit()

    # Best-effort Slack notification AFTER the commit — a Slack outage or a
    # skipped gate can never change the outcome of ticket creation.
    import asyncio as _asyncio

    from . import slack_service as _slack

    lines = [f"*Your ticket was filed:* {t.title}"]
    if t.jira_key and t.jira_url:
        lines.append(f"• Jira: <{t.jira_url}|{t.jira_key}>")
    lines.append(f"• Ask WEKA: <{_slack.public_base_url()}/|open your conversations>")
    _asyncio.get_running_loop().create_task(
        _slack.notify_user("ticket_notifications", username, "\n".join(lines))
    )
    return t


@router.post("")
async def create_ticket(
    req: TicketCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    """Web approval endpoint — thin wrapper over the shared filing core."""
    t = await file_ticket(db, user["username"], req.conversation_id, req.title, req.body)
    return _ticket_out(t)


@router.get("")
def my_tickets(db: Session = Depends(get_db), user: dict = Depends(require_user)):
    rows = (
        db.query(Ticket)
        .filter(Ticket.username == user["username"])
        .order_by(Ticket.created_at.desc())
        .limit(50)
        .all()
    )
    return [_ticket_out(t) for t in rows]


# ---------- Admin endpoints ----------


@admin_router.get("")
def admin_list(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Ticket)
    if status:
        if status not in TICKET_STATUSES:
            raise HTTPException(400, "Invalid status")
        q = q.filter(Ticket.status == status)
    rows = q.order_by(Ticket.created_at.desc()).limit(200).all()
    return [_ticket_out(t) for t in rows]


@admin_router.get("/stats")
def admin_stats(db: Session = Depends(get_db)):
    """The feature's success number: how many issues were solved WITHOUT a
    ticket, next to how many became tickets."""
    solved = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.resolution == "solved")
        .scalar()
        or 0
    )
    tickets = db.query(func.count(Ticket.id)).scalar() or 0
    open_tickets = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.status.in_(("open", "in_progress")))
        .scalar()
        or 0
    )
    total = solved + tickets
    return {
        "solved_without_ticket": solved,
        "tickets_created": tickets,
        "open_tickets": open_tickets,
        "deflection_rate": round(solved / total, 3) if total else None,
    }


class StatusUpdate(BaseModel):
    status: str


@admin_router.patch("/{ticket_id}")
def admin_update(
    ticket_id: str,
    req: StatusUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    if req.status not in TICKET_STATUSES:
        raise HTTPException(400, "Invalid status")
    t = db.get(Ticket, ticket_id)
    if not t:
        raise HTTPException(404, "Ticket not found")
    t.status = req.status
    log_event(db, user["username"], "ticket.status", f"id={t.id} status={req.status}")
    db.commit()
    return _ticket_out(t)
