"""Shared feedback persistence rules — used by web chat and Slack actions.

Privacy invariants (enforced here so every entry point shares them):
- The rater is stored ONLY as a keyed pseudonym (HMAC of the username with
  the server secret) — feedback is anonymous at rest and in every admin view.
- One feedback row per (message, rater): updates replace, never duplicate.
- Question/answer context is loaded server-side from the stored message,
  never trusted from a client or a Slack payload.
- The audit record never links the rater to the message.
"""

import hashlib
import hmac
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .analysis import classify_domain, extract_sources, summarize
from .audit import log_event
from .models import Feedback, Message


def rater_hash(username: str) -> str:
    """Keyed pseudonym of the rater (HMAC with the server secret): enables
    one-feedback-per-person per message without storing who rated what, and
    cannot be reversed by hashing a list of known employee names."""
    key = (os.environ.get("SESSION_SECRET", "") or "dev-only-insecure").encode()
    return hmac.new(key, username.strip().lower().encode(), hashlib.sha256).hexdigest()


def record_feedback(
    db: Session,
    username: str,
    msg: Message,
    thumbs: str,
    feedback_text: str | None = None,
    via: str = "web",
) -> Feedback:
    """Persist/update the caller's feedback for an assistant message they own.

    The caller MUST have already verified `msg` is an assistant message in a
    conversation owned by `username`. Commits are left to the caller."""
    answer = msg.content
    question = ""
    for m in msg.conversation.messages:
        if m.id == msg.id:
            break
        if m.role == "user":
            question = m.content

    rater = rater_hash(username)
    fb = (
        db.query(Feedback)
        .filter(Feedback.message_id == msg.id, Feedback.username == rater)
        .first()
    )
    if not fb:
        fb = Feedback(message_id=msg.id, username=rater)
        db.add(fb)
    fb.question = question.strip()
    fb.answer_summary = summarize(answer)
    fb.thumbs = thumbs or ""
    if feedback_text is not None:
        fb.feedback_text = feedback_text.strip()
    fb.logged_time = datetime.now(timezone.utc)
    fb.cited_sources = "\n".join(extract_sources(answer))
    fb.domain = classify_domain(question, answer)
    # Audit records that feedback was submitted, but never which message —
    # feedback must stay anonymous, so no message/conversation ids.
    log_event(
        db,
        username,
        "feedback.submit",
        f"thumbs={fb.thumbs or 'none'} has_text={bool(fb.feedback_text)} via={via}",
    )
    return fb
