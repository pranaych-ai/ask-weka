"""Single-instance digest scheduler (explicitly single-instance for the POC).

A resilient asyncio task started/stopped with the FastAPI lifecycle checks
once a minute whether the configured usage digest is due. Idempotency across
restarts: the digest's due-date key (YYYY-MM-DD in the configured timezone)
is claimed with an atomic conditional UPDATE on slack_integration before any
post happens, so the same day can never be posted twice.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func

from .db import SessionLocal
from .models import Conversation, Feedback, Message, SlackIntegration, Ticket
from . import slack_service as svc

logger = logging.getLogger("askweka.slack.scheduler")

_task: asyncio.Task | None = None

DIGEST_PROMPT = (
    "You write a short internal Slack digest for the Ask WEKA assistant. "
    "Given usage metrics for the last 24 hours, write 3-6 friendly bullet "
    "lines in Slack mrkdwn (use *bold*, no headings, no preamble). Be "
    "factual — never invent numbers that are not in the input."
)


def collect_metrics(db) -> dict:
    """Aggregate, non-PII usage counts for the digest — no message content."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return {
        "conversations_started_24h": db.query(func.count(Conversation.id))
        .filter(Conversation.created_at >= since).scalar() or 0,
        "messages_24h": db.query(func.count(Message.id))
        .filter(Message.created_at >= since).scalar() or 0,
        "thumbs_up_24h": db.query(func.count(Feedback.id))
        .filter(Feedback.logged_time >= since, Feedback.thumbs == "up").scalar() or 0,
        "thumbs_down_24h": db.query(func.count(Feedback.id))
        .filter(Feedback.logged_time >= since, Feedback.thumbs == "down").scalar() or 0,
        "tickets_created_24h": db.query(func.count(Ticket.id))
        .filter(Ticket.created_at >= since).scalar() or 0,
        "open_tickets_total": db.query(func.count(Ticket.id))
        .filter(Ticket.status.in_(("open", "in_progress"))).scalar() or 0,
        "unresolved_topics_total": db.query(func.count(Feedback.id))
        .filter(Feedback.review_status == "open").scalar() or 0,
        "solved_without_ticket_total": db.query(func.count(Conversation.id))
        .filter(Conversation.resolution == "solved").scalar() or 0,
    }


async def _write_digest(metrics: dict) -> str:
    """Digest text through the existing approved Gemini provider, with a
    plain-metrics fallback so a model outage never kills the post."""
    lines = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in metrics.items())
    try:
        from .providers import get_provider

        provider = get_provider()
        parts: list[str] = []
        async for chunk in provider.stream_chat(
            DIGEST_PROMPT, [{"role": "user", "content": lines}]
        ):
            parts.append(chunk)
        text = "".join(parts).strip()
        if text:
            return text[:2800]
    except Exception:
        logger.exception("Digest generation via provider failed — using plain metrics")
    return "*Ask WEKA daily digest*\n" + lines


def _due_key(row: SlackIntegration, now_utc: datetime) -> str:
    """Return today's date key when the digest is due now (or overdue), else ''."""
    try:
        tz = ZoneInfo(row.digest_timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    local = now_utc.astimezone(tz)
    try:
        hh, mm = (int(p) for p in (row.digest_time or "09:00").split(":"))
    except ValueError:
        hh, mm = 9, 0
    if (local.hour, local.minute) < (hh, mm):
        return ""
    return local.strftime("%Y-%m-%d")


def claim_run(key: str) -> bool:
    """Atomically claim the digest run for `key`. True only for the winner —
    a restart (or a lost race) can never produce a duplicate post."""
    db = SessionLocal()
    try:
        updated = (
            db.query(SlackIntegration)
            .filter(SlackIntegration.id == 1, SlackIntegration.digest_last_run != key)
            .update({SlackIntegration.digest_last_run: key}, synchronize_session=False)
        )
        db.commit()
        return updated == 1
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def unclaim_run(key: str, previous: str) -> None:
    """Roll the claim back if nothing was posted, so a transient failure is
    retried on the next minute tick instead of silently skipping the day."""
    db = SessionLocal()
    try:
        (
            db.query(SlackIntegration)
            .filter(SlackIntegration.id == 1, SlackIntegration.digest_last_run == key)
            .update({SlackIntegration.digest_last_run: previous}, synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def check_once(now_utc: datetime | None = None) -> bool:
    """One scheduler tick. Returns True when a digest was posted."""
    now_utc = now_utc or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        row = svc.get_integration(db)
        ok, _reason = svc.check_gate(db, "usage_digest")
        due = _due_key(row, now_utc)
        previous = row.digest_last_run
        already = row.digest_last_run == due
        has_channel = bool(row.notify_channel)
        metrics = collect_metrics(db) if (ok and due and not already and has_channel) else None
        db.commit()
    finally:
        db.close()
    if not (ok and due and not already and has_channel):
        return False
    if not claim_run(due):
        return False  # another worker/restart already posted today
    text = await _write_digest(metrics)
    posted = await svc.notify_channel("usage_digest", text)
    if not posted:
        unclaim_run(due, previous)
    return posted


async def _loop() -> None:
    logger.info("Slack digest scheduler started (single-instance, 60s tick)")
    while True:
        try:
            await check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Digest scheduler tick failed — continuing")
        await asyncio.sleep(60)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
