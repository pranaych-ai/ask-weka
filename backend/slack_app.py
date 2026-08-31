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
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response

from . import alerts
from .audit import log_event, log_event_standalone
from .db import SessionLocal
from .models import Conversation, Message, SlackChannel, SlackDmNotice
from .prompts import build_system_prompt
from . import slack_service as svc

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


def _inbound_config() -> dict | None:
    """Non-secret inbound routing state, or None when inbound Slack is off.

    Admission (credentials + admin master switch) is separated from
    per-feature gating: the endpoint exists whenever the integration is
    active, and each event type is then routed by its own feature flag."""
    if not svc.creds_configured():
        return None
    db = SessionLocal()
    try:
        row = svc.get_integration(db)
        cfg = {
            "enabled": row.enabled,
            "verified": row.verified,
            "features": svc.parse_features(row),
            "commands": svc.parse_commands(row),
            "app_id": row.app_id,
            "bot_user_id": row.bot_user_id,
        }
        db.commit()
        # Inbound admission requires the admin master switch AND a verified
        # integration — a failed (re-)verification also clears the app-ID
        # pin, so stale verification state can never keep accepting events.
        return cfg if (cfg["enabled"] and cfg["verified"]) else None
    except Exception:
        # The database-backed master switch is authoritative once credentials
        # exist. If its state cannot be read, fail closed rather than risk
        # accepting events after an administrator disabled Slack.
        logger.exception("Slack config check failed — inbound Slack disabled")
        return None
    finally:
        db.close()


def _slack_enabled() -> bool:
    """Inbound endpoints exist only when credentials are set (Replit Secrets)
    and the admin master switch is on."""
    return _inbound_config() is not None


def _verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    secret = svc.signing_secret()
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
    # All Slack Web API access goes through the shared service boundary
    # (credentials from env only, one automatic retry on rate limits).
    return await svc.slack_api(method, payload)


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


async def _react(method: str, channel: str, ts: str) -> None:
    """Best-effort eyes reaction management — never blocks or fails an answer."""
    if not ts:
        return
    try:
        await _slack_call(method, {"channel": channel, "timestamp": ts, "name": "eyes"})
    except Exception:
        logger.debug("Slack reaction %s failed (non-fatal)", method, exc_info=True)


def _record_identity(email: str, slack_user: str) -> None:
    """First-contact identity capture: refresh the employee's Slack identity
    for future opted-in notifications without granting any consent."""
    try:
        db = SessionLocal()
        try:
            svc.upsert_slack_identity(db, email, slack_user)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Slack identity upsert failed (non-fatal)")


def _audit_inbound(username: str, kind: str, ok: bool) -> None:
    # Metadata only — never the question text.
    log_event_standalone(
        username or "unknown", "slack.inbound", f"type={kind} ok={'true' if ok else 'false'}"
    )


async def _generate_answer(history: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """The shared protected answer pipeline: same system prompt, knowledge
    scope, provider, and output safety gate as web chat. Returns the answer
    (already replaced by the safe refusal if blocked) plus safety events."""
    from .ai_safety import SAFE_REFUSAL, screen_answer
    from .providers import get_provider

    provider = get_provider()
    system = build_system_prompt("")
    parts: list[str] = []
    async for chunk in provider.stream_chat(system, history):
        parts.append(chunk)
    answer = "".join(parts).strip()
    if not answer:
        raise RuntimeError("Empty answer from model")
    safety_events: list[tuple[str, str]] = []
    gate = screen_answer(answer)
    if gate.blocked:
        answer = SAFE_REFUSAL
        safety_events.append(("chat.safety_blocked", ",".join(gate.findings)))
    return answer, safety_events


async def _answer_dm(channel: str, slack_user: str, text: str, ts: str = "") -> None:
    async with _channel_lock(channel):
        await _answer_dm_inner(channel, slack_user, text, ts)


async def _answer_dm_inner(channel: str, slack_user: str, text: str, ts: str = "") -> None:
    email = ""
    try:
        email = await _resolve_email(slack_user)
        if not email:
            await svc.notify_direct_channel(
                "dm_chat",
                channel,
                "Sorry — I can only help verified WEKA employees, and I couldn't "
                "confirm your account. Please contact #it-help.",
            )
            _audit_inbound("", "dm", False)
            return

        _record_identity(email, slack_user)
        await _react("reactions.add", channel, ts)

        from .ai_safety import screen_question

        db = SessionLocal()
        try:
            conv = _conversation_for_channel(db, channel, email)
            conv.messages.append(Message(role="user", content=text))
            log_event(db, email, "chat.message", f"conversation_id={conv.id} via=slack")
            # Input screen BEFORE the provider sees the question — logged even
            # if generation fails below.
            inbound = screen_question(text)
            if inbound.findings:
                log_event(
                    db,
                    email,
                    "chat.injection_flagged",
                    f"conversation_id={conv.id} findings={','.join(inbound.findings)} via=slack",
                )
            db.commit()
            conversation_id = conv.id
            history = [
                {"role": m.role, "content": m.content}
                for m in conv.messages[-HISTORY_BUDGET:]
            ]
        finally:
            db.close()

        answer, safety_events = await _generate_answer(history)

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
            for event, findings in safety_events:
                log_event(
                    db,
                    email,
                    event,
                    f"conversation_id={conversation_id} findings={findings} via=slack",
                )
            db.commit()
        finally:
            db.close()

        await svc.notify_direct_channel(
            "dm_chat", channel, answer[:39000], blocks=svc.answer_blocks(answer[:11000])
        )
        _audit_inbound(email, "dm", True)
    except Exception as e:
        logger.exception("Slack DM handling failed")
        # Slack DM failures never surface as 5xx responses; alert explicitly.
        alerts.record_ai_failure(f"Slack DM: {type(e).__name__}")
        _audit_inbound(email, "dm", False)
        try:
            await svc.notify_direct_channel(
                "dm_chat",
                channel,
                "Sorry — something went wrong answering that. Please try again, "
                "or ask in #it-help / #hr-help.",
            )
        except Exception:
            logger.exception("Slack error notification failed")
    finally:
        await _react("reactions.remove", channel, ts)


async def _dm_disabled_notice(channel: str, slack_user: str) -> None:
    """DM chat is off: point the employee at the web app AT MOST once per
    day. The durable insert happens before the send so Slack retries and app
    restarts can never spam an employee."""
    try:
        email = await _resolve_email(slack_user)
        if not email:
            return
        _record_identity(email, slack_user)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db = SessionLocal()
        try:
            db.add(SlackDmNotice(slack_user_id=slack_user, notice_date=today))
            db.commit()
        except Exception:
            db.rollback()
            return  # already notified today — stay silent
        finally:
            db.close()
        await svc.send_inbound_notice(
            channel,
            "Slack chat is currently turned off — you can ask me anything at "
            f"{svc.public_base_url()}",
        )
        _audit_inbound(email, "dm_disabled_notice", True)
    except Exception:
        logger.exception("Slack disabled-DM notice failed")


def _strip_mentions(text: str, bot_user_id: str = "") -> str:
    """Remove only the configured bot's own mention token; mentions of other
    users are legitimate question content and are preserved."""
    if bot_user_id:
        return re.sub(rf"<@{re.escape(bot_user_id)}(\|[^>]*)?>", "", text or "").strip()
    return re.sub(r"<@[A-Z0-9]+(\|[^>]*)?>", "", text or "").strip()


async def _answer_mention(
    channel: str, slack_user: str, raw_text: str, ts: str, thread_ts: str,
    bot_user_id: str = "",
) -> None:
    """Threaded channel mention: fresh single-exchange context — never reads
    or modifies the employee's DM history or prior channel messages."""
    reply_ts = thread_ts or ts
    email = ""
    try:
        email = await _resolve_email(slack_user)
        if not email:
            await svc.notify_direct_channel(
                "channel_mentions",
                channel,
                "Sorry — I can only help verified WEKA employees, and I couldn't "
                "confirm your account. Please contact #it-help.",
                thread_ts=reply_ts,
            )
            _audit_inbound("", "mention", False)
            return

        # A valid employee interaction either way: capture first-contact
        # identity before branching on whether a question was asked.
        _record_identity(email, slack_user)

        text = _strip_mentions(raw_text, bot_user_id)
        if not text:
            await svc.notify_direct_channel(
                "channel_mentions",
                channel,
                "Hi! Mention me with a question, e.g. `@Ask WEKA how do I request a laptop?`",
                thread_ts=reply_ts,
            )
            _audit_inbound(email, "mention_empty", True)
            return

        await _react("reactions.add", channel, ts)

        from .ai_safety import screen_question

        db = SessionLocal()
        try:
            conv = Conversation(title="Slack mention", username=email, domain="")
            db.add(conv)
            db.flush()
            conv.messages.append(Message(role="user", content=text))
            log_event(db, email, "conversation.create", f"id={conv.id} via=slack_mention")
            log_event(db, email, "chat.message", f"conversation_id={conv.id} via=slack_mention")
            inbound = screen_question(text)
            if inbound.findings:
                log_event(
                    db,
                    email,
                    "chat.injection_flagged",
                    f"conversation_id={conv.id} findings={','.join(inbound.findings)} via=slack_mention",
                )
            db.commit()
            conversation_id = conv.id
        finally:
            db.close()

        # Fresh single-exchange context: exactly this one question.
        answer, safety_events = await _generate_answer([{"role": "user", "content": text}])

        db = SessionLocal()
        try:
            msg = Message(conversation_id=conversation_id, role="assistant", content=answer)
            db.add(msg)
            db.flush()
            log_event(
                db,
                email,
                "chat.response",
                f"conversation_id={conversation_id} message_id={msg.id} via=slack_mention",
            )
            for event, findings in safety_events:
                log_event(
                    db,
                    email,
                    event,
                    f"conversation_id={conversation_id} findings={findings} via=slack_mention",
                )
            db.commit()
        finally:
            db.close()

        await svc.notify_direct_channel(
            "channel_mentions",
            channel,
            answer[:39000],
            blocks=svc.answer_blocks(answer[:11000]),
            thread_ts=reply_ts,
        )
        _audit_inbound(email, "mention", True)
    except Exception as e:
        logger.exception("Slack mention handling failed")
        alerts.record_ai_failure(f"Slack mention: {type(e).__name__}")
        _audit_inbound(email, "mention", False)
        try:
            await svc.notify_direct_channel(
                "channel_mentions",
                channel,
                "Sorry — something went wrong answering that. Please try again, "
                "or ask in #it-help / #hr-help.",
                thread_ts=reply_ts,
            )
        except Exception:
            logger.exception("Slack mention error notification failed")
    finally:
        await _react("reactions.remove", channel, ts)


@router.post("/events")
async def slack_events(request: Request):
    cfg = _inbound_config()
    if cfg is None:
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

    # Only the verified configured Slack app may deliver events. Fail closed:
    # verification pins the app ID, and until it is pinned — or when the
    # payload's api_app_id is anything other than that exact ID, including
    # omitted — the event is rejected.
    api_app_id = str(payload.get("api_app_id") or "")
    if not cfg["app_id"] or api_app_id != cfg["app_id"]:
        log_event_standalone(
            "system",
            "slack.reject",
            "reason=app_id_unpinned" if not cfg["app_id"]
            else f"reason=app_id_mismatch app_id={api_app_id[:30] or 'missing'}",
        )
        return Response(status_code=403)

    event_id = payload.get("event_id", "")
    if event_id:
        if event_id in _seen_events:
            return {"ok": True}  # retry of an event we already handled
        _seen_events[event_id] = time.time()
        while len(_seen_events) > _SEEN_MAX:
            _seen_events.popitem(last=False)

    event = payload.get("event") or {}
    features = cfg["features"]
    fresh_human = (
        not event.get("bot_id")
        and not event.get("subtype")
        and (event.get("text") or "").strip()
        and event.get("user")
        and event.get("channel")
    )
    # Fresh 1:1 messages from humans — no edits or bot echoes.
    if event.get("type") == "message" and event.get("channel_type") == "im" and fresh_human:
        # Ack within Slack's 3s window; answer in the background.
        if features.get("dm_chat"):
            asyncio.get_running_loop().create_task(
                _answer_dm(
                    event["channel"], event["user"], event["text"].strip(),
                    event.get("ts", ""),
                )
            )
        else:
            asyncio.get_running_loop().create_task(
                _dm_disabled_notice(event["channel"], event["user"])
            )
    # Channel mentions — independently gated, threaded reply.
    elif event.get("type") == "app_mention" and fresh_human and features.get("channel_mentions"):
        asyncio.get_running_loop().create_task(
            _answer_mention(
                event["channel"],
                event["user"],
                event["text"],
                event.get("ts", ""),
                event.get("thread_ts", ""),
                cfg["bot_user_id"],
            )
        )

    return {"ok": True}


# ---------- Slash commands ----------


async def _answer_command(
    slack_user: str, text: str, response_url: str, in_channel: bool
) -> None:
    """/askweka: same protected pipeline as DMs/mentions — WEKA identity
    check, input screen, provider, output safety gate, metadata-only audit.
    Fresh single-exchange context; the reply goes through the slash command's
    response_url (ephemeral or in-channel per admin configuration)."""
    email = ""
    try:
        email = await _resolve_email(slack_user)
        if not email:
            await svc.respond_to_command(
                response_url,
                "Sorry — I can only help verified WEKA employees, and I couldn't "
                "confirm your account. Please contact #it-help.",
            )
            _audit_inbound("", "command", False)
            return

        _record_identity(email, slack_user)

        if not text:
            await svc.respond_to_command(
                response_url,
                "Ask me a question, e.g. `/askweka how do I request a laptop?`",
            )
            _audit_inbound(email, "command_empty", True)
            return

        from .ai_safety import screen_question

        db = SessionLocal()
        try:
            conv = Conversation(title="Slack command", username=email, domain="")
            db.add(conv)
            db.flush()
            conv.messages.append(Message(role="user", content=text))
            log_event(db, email, "conversation.create", f"id={conv.id} via=slack_command")
            log_event(db, email, "chat.message", f"conversation_id={conv.id} via=slack_command")
            inbound = screen_question(text)
            if inbound.findings:
                log_event(
                    db,
                    email,
                    "chat.injection_flagged",
                    f"conversation_id={conv.id} findings={','.join(inbound.findings)} via=slack_command",
                )
            db.commit()
            conversation_id = conv.id
        finally:
            db.close()

        # Fresh single-exchange context: exactly this one question.
        answer, safety_events = await _generate_answer([{"role": "user", "content": text}])

        db = SessionLocal()
        try:
            msg = Message(conversation_id=conversation_id, role="assistant", content=answer)
            db.add(msg)
            db.flush()
            log_event(
                db,
                email,
                "chat.response",
                f"conversation_id={conversation_id} message_id={msg.id} via=slack_command",
            )
            for event, findings in safety_events:
                log_event(
                    db,
                    email,
                    event,
                    f"conversation_id={conversation_id} findings={findings} via=slack_command",
                )
            db.commit()
        finally:
            db.close()

        await svc.respond_to_command(
            response_url,
            answer[:39000],
            blocks=svc.answer_blocks(answer[:11000]),
            in_channel=in_channel,
        )
        _audit_inbound(email, "command", True)
    except Exception as e:
        logger.exception("Slack command handling failed")
        alerts.record_ai_failure(f"Slack command: {type(e).__name__}")
        _audit_inbound(email, "command", False)
        try:
            await svc.respond_to_command(
                response_url,
                "Sorry — something went wrong answering that. Please try again, "
                "or ask in #it-help / #hr-help.",
            )
        except Exception:
            logger.exception("Slack command error notification failed")


@router.post("/commands")
async def slack_commands(request: Request):
    cfg = _inbound_config()
    if cfg is None:
        return Response(status_code=404)
    body = await request.body()
    if not _verify_signature(
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        return Response(status_code=401)

    from urllib.parse import parse_qs

    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}

    # Same fail-closed app-ID pinning as the events endpoint.
    api_app_id = str(form.get("api_app_id") or "")
    if not cfg["app_id"] or api_app_id != cfg["app_id"]:
        log_event_standalone(
            "system",
            "slack.reject",
            "reason=app_id_unpinned" if not cfg["app_id"]
            else f"reason=app_id_mismatch app_id={api_app_id[:30] or 'missing'}",
        )
        return Response(status_code=403)

    # Only commands an admin actually configured are admitted; anything else
    # gets a safe ephemeral notice.
    command = (form.get("command") or "").strip()
    if command not in {c["command"] for c in cfg["commands"]}:
        return {
            "response_type": "ephemeral",
            "text": "Sorry — I don't recognize that command. Try `/askweka your question`.",
        }

    features = cfg["features"]
    if not features.get("slash_commands"):
        return {
            "response_type": "ephemeral",
            "text": "Slash commands aren't enabled — DM me your question, or ask at "
            f"{svc.public_base_url()}",
        }

    slack_user = form.get("user_id", "")
    response_url = form.get("response_url", "")
    if not slack_user or not response_url:
        return Response(status_code=400)

    # Ack within Slack's 3s window; answer in the background.
    asyncio.get_running_loop().create_task(
        _answer_command(
            slack_user,
            (form.get("text") or "").strip(),
            response_url,
            bool(features.get("slash_in_channel")),
        )
    )
    return {"response_type": "ephemeral", "text": ":eyes: On it — checking the knowledge base…"}


@router.post("/interactions")
async def slack_interactions(request: Request):
    if not _slack_enabled():
        return Response(status_code=404)
    body = await request.body()
    if not _verify_signature(
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        return Response(status_code=401)
    return Response(status_code=200)
