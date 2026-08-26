"""Slack 1:1 assistant: WEKA employees DM the Ask WEKA bot and get the same
sourced, KB-grounded answers as the web app.

Security model:
- Every request is verified against SLACK_SIGNING_SECRET (Slack's v0 HMAC).
- The sender is resolved to their work email via users.info; anyone without a
  resolvable email is refused. The Slack workspace itself is WEKA-only.
- Every question/answer is persisted and audited exactly like web chat.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response

from .audit import log_event
from .db import SessionLocal
from .models import Conversation, Message, SlackChannel
from .prompts import build_system_prompt

logger = logging.getLogger("askweka.slack")

router = APIRouter(prefix="/api/slack")

SLACK_API = "https://slack.com/api"
HISTORY_BUDGET = 30

# Slack retries deliveries; remember recently-seen event ids (per process —
# good enough for the single-instance POC).
_seen_events: "OrderedDict[str, float]" = OrderedDict()
_SEEN_MAX = 1000

# One lock per DM channel: messages are answered strictly in order and two
# simultaneous DMs can never race on the channel→conversation mapping.
_channel_locks: dict = {}


def _channel_lock(channel: str) -> asyncio.Lock:
    lock = _channel_locks.get(channel)
    if lock is None:
        lock = _channel_locks[channel] = asyncio.Lock()
    return lock


def _slack_enabled() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_SIGNING_SECRET"))


def _verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - float(timestamp)) > 60 * 5:
            return False  # replay protection
    except ValueError:
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8', 'replace')}"
    expected = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _slack_call(method: str, payload: dict) -> dict:
    import httpx

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{SLACK_API}/{method}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.json()


def _allowed_domains() -> tuple:
    raw = os.environ.get("SLACK_ALLOWED_EMAIL_DOMAINS", "weka.io")
    return tuple(d.strip().lower() for d in raw.split(",") if d.strip())


async def _resolve_email(slack_user_id: str) -> str:
    """The employee's work email from Slack — never trusted from the message
    payload itself, and gated to WEKA email domains so workspace guests or
    external members can never use the assistant."""
    data = await _slack_call("users.info", {"user": slack_user_id})
    if not data.get("ok"):
        return ""
    user = data.get("user") or {}
    profile = user.get("profile") or {}
    if user.get("is_bot") or user.get("deleted") or user.get("is_restricted") \
            or user.get("is_ultra_restricted") or user.get("is_stranger"):
        return ""  # bots, deactivated accounts, guests, external users
    email = (profile.get("email") or "").strip().lower()
    if not email or not any(email.endswith("@" + d) for d in _allowed_domains()):
        return ""
    return email


def _conversation_for_channel(db, channel_id: str, username: str) -> Conversation:
    m = db.get(SlackChannel, channel_id)
    if m:
        conv = db.get(Conversation, m.conversation_id)
        if conv and conv.username == username:
            return conv
    conv = Conversation(title="Slack chat", username=username, domain="")
    db.add(conv)
    db.flush()
    if m:
        m.conversation_id = conv.id
    else:
        db.add(SlackChannel(channel_id=channel_id, conversation_id=conv.id))
    log_event(db, username, "conversation.create", f"id={conv.id} via=slack")
    return conv


async def _answer_dm(channel: str, slack_user: str, text: str) -> None:
    async with _channel_lock(channel):
        await _answer_dm_inner(channel, slack_user, text)


async def _answer_dm_inner(channel: str, slack_user: str, text: str) -> None:
    try:
        email = await _resolve_email(slack_user)
        if not email:
            await _slack_call(
                "chat.postMessage",
                {
                    "channel": channel,
                    "text": "Sorry — I can only help verified WEKA employees, and I couldn't "
                    "confirm your account. Please contact #it-help.",
                },
            )
            return

        db = SessionLocal()
        try:
            conv = _conversation_for_channel(db, channel, email)
            conv.messages.append(Message(role="user", content=text))
            log_event(db, email, "chat.message", f"conversation_id={conv.id} via=slack")
            db.commit()
            conversation_id = conv.id
            history = [
                {"role": m.role, "content": m.content}
                for m in conv.messages[-HISTORY_BUDGET:]
            ]
        finally:
            db.close()

        from .providers import get_provider

        provider = get_provider()
        system = build_system_prompt("")
        parts: list[str] = []
        async for chunk in provider.stream_chat(system, history):
            parts.append(chunk)
        answer = "".join(parts).strip()
        if not answer:
            raise RuntimeError("Empty answer from model")

        db = SessionLocal()
        try:
            msg = Message(conversation_id=conversation_id, role="assistant", content=answer)
            db.add(msg)
            db.flush()
            log_event(
                db,
                email,
                "chat.response",
                f"conversation_id={conversation_id} message_id={msg.id} via=slack",
            )
            db.commit()
        finally:
            db.close()

        await _slack_call(
            "chat.postMessage",
            {"channel": channel, "text": answer[:39000], "unfurl_links": False},
        )
    except Exception:
        logger.exception("Slack DM handling failed")
        try:
            await _slack_call(
                "chat.postMessage",
                {
                    "channel": channel,
                    "text": "Sorry — something went wrong answering that. Please try again, "
                    "or ask in #it-help / #hr-help.",
                },
            )
        except Exception:
            logger.exception("Slack error notification failed")


@router.post("/events")
async def slack_events(request: Request):
    if not _slack_enabled():
        return Response(status_code=404)

    body = await request.body()
    if not _verify_signature(
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        return Response(status_code=401)

    import json as _json

    try:
        payload = _json.loads(body)
    except ValueError:
        return Response(status_code=400)

    # Slack app setup handshake
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") != "event_callback":
        return {"ok": True}

    event_id = payload.get("event_id", "")
    if event_id:
        if event_id in _seen_events:
            return {"ok": True}  # retry of an event we already handled
        _seen_events[event_id] = time.time()
        while len(_seen_events) > _SEEN_MAX:
            _seen_events.popitem(last=False)

    event = payload.get("event") or {}
    # Only fresh 1:1 messages from humans — no channels, edits, or bot echoes.
    if (
        event.get("type") == "message"
        and event.get("channel_type") == "im"
        and not event.get("bot_id")
        and not event.get("subtype")
        and (event.get("text") or "").strip()
        and event.get("user")
        and event.get("channel")
    ):
        # Ack within Slack's 3s window; answer in the background.
        asyncio.get_running_loop().create_task(
            _answer_dm(event["channel"], event["user"], event["text"].strip())
        )

    return {"ok": True}
