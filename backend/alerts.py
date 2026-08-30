"""Security & availability alerting for owners/admins.

Routes actionable alerts to the approved owner/admin Slack channel through
the central slack_service gate (feature "security_alerts"). Every alert is
also written to the audit log, so nothing is lost when Slack is disabled.

Design rules:
- No secrets and no unnecessary personal data in alert text. Identifiers
  (usernames) appear only where required for the admin to act (e.g. an
  admin-rights change); volume alerts carry counts, never who/what content.
- Deduplication: each alert key has a cooldown window; repeats within the
  window are suppressed (counted, and the count is included next time).
- Severity is explicit: CRITICAL / WARNING / INFO.
- Best-effort: alerting never raises into the request path.
"""

import asyncio
import logging
import time
from collections import deque

from .audit import log_event_standalone

logger = logging.getLogger("askweka.alerts")

# Per-key cooldown between Slack posts (seconds). Audit rows are always written.
DEDUP_COOLDOWN_S = 30 * 60

SEVERITY_EMOJI = {"critical": ":rotating_light:", "warning": ":warning:", "info": ":information_source:"}

# ---- thresholds ----
AUTH_FAILURE_THRESHOLD = 5          # failed sign-ins ...
AUTH_FAILURE_WINDOW_S = 10 * 60     # ... within this window -> alert
SERVER_ERROR_THRESHOLD = 5          # unhandled 5xx errors ...
SERVER_ERROR_WINDOW_S = 5 * 60      # ... within this window -> alert

# In-memory state (single-instance app; scheduler runs in-process too).
_last_sent: dict[str, float] = {}       # alert key -> last Slack post time
_suppressed: dict[str, int] = {}        # alert key -> count suppressed since last post
_auth_failures: deque[float] = deque()  # timestamps of recent auth failures
_server_errors: deque[float] = deque()  # timestamps of recent 5xx errors
_lock = asyncio.Lock()


def _prune(dq: deque, window: float, now: float) -> None:
    while dq and now - dq[0] > window:
        dq.popleft()


async def send_alert(key: str, severity: str, title: str, detail: str = "") -> bool:
    """Post a deduplicated alert to the owner/admin channel.

    Always writes an audit row. Posts to Slack at most once per key per
    cooldown window; suppressed repeats are counted and reported on the
    next post for that key.
    """
    severity = severity if severity in SEVERITY_EMOJI else "warning"
    log_event_standalone(
        "system", "alert.raised", f"severity={severity} key={key} {detail}"[:2000]
    )
    now = time.time()
    async with _lock:
        last = _last_sent.get(key, 0.0)
        if now - last < DEDUP_COOLDOWN_S:
            _suppressed[key] = _suppressed.get(key, 0) + 1
            log_event_standalone("system", "alert.deduplicated", f"key={key}")
            return False
        suppressed = _suppressed.pop(key, 0)
        _last_sent[key] = now

    text = f"{SEVERITY_EMOJI[severity]} *[{severity.upper()}] {title}*"
    if detail:
        text += f"\n{detail}"
    if suppressed:
        text += f"\n_({suppressed} similar alert(s) suppressed in the last {DEDUP_COOLDOWN_S // 60} min)_"
    text += "\n_Response: see docs/ARCHITECTURE.md → Alerting & incident response._"

    from .slack_service import notify_channel

    sent = await notify_channel("security_alerts", text)
    if not sent:
        # The audit row above is the fallback record; note delivery failure.
        log_event_standalone("system", "alert.delivery_skipped", f"key={key}")
    return sent


def _fire_and_forget(coro) -> None:
    """Schedule an alert from sync or async context without blocking/raising."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception:
            logger.exception("Alert dispatch failed")
    except Exception:
        logger.exception("Alert dispatch failed")


# ---------- Repeated authentication failures ----------


def record_auth_failure(reason: str = "") -> None:
    """Count a failed sign-in; alert when the threshold is crossed.

    Deliberately does NOT include who failed to sign in — the audit log has
    the per-event record; the alert only carries the aggregate count.
    """
    now = time.time()
    _prune(_auth_failures, AUTH_FAILURE_WINDOW_S, now)
    _auth_failures.append(now)
    count = len(_auth_failures)
    if count >= AUTH_FAILURE_THRESHOLD:
        _fire_and_forget(
            send_alert(
                "auth.repeated_failures",
                "warning",
                "Repeated sign-in failures",
                f"{count} failed Okta sign-ins in the last "
                f"{AUTH_FAILURE_WINDOW_S // 60} minutes"
                + (f" (latest reason: {reason[:100]})" if reason else "")
                + ". Check the audit log (action=login.failed) and Okta system log.",
            )
        )


# ---------- Unexpected admin-rights changes ----------


def record_admin_change(username: str, granted: bool, source: str) -> None:
    """Alert when a sign-in yields different admin rights than the previous
    sign-in for the same account (grant = critical, revoke = info)."""
    _fire_and_forget(
        send_alert(
            f"auth.admin_change:{username}:{granted}",
            "critical" if granted else "info",
            "Admin rights " + ("granted" if granted else "revoked"),
            f"Account `{username}` signed in with admin rights "
            f"{'GRANTED' if granted else 'REVOKED'} (previous sign-in differed). "
            f"Source: {source}. If this change was not expected, review Okta "
            "group membership (dl-app-askweka-admin) and OKTA_ADMIN_USERS.",
        )
    )


# ---------- Service failures ----------


def record_server_error(path: str = "") -> None:
    """Count an unhandled 5xx; alert when the threshold is crossed.

    Only the route path (no query string, headers, or body) is included.
    """
    now = time.time()
    _prune(_server_errors, SERVER_ERROR_WINDOW_S, now)
    _server_errors.append(now)
    count = len(_server_errors)
    if count >= SERVER_ERROR_THRESHOLD:
        _fire_and_forget(
            send_alert(
                "app.server_errors",
                "critical",
                "Application errors spiking",
                f"{count} unhandled server errors in the last "
                f"{SERVER_ERROR_WINDOW_S // 60} minutes "
                f"(latest path: {path[:120] or 'n/a'}). "
                "Check workflow/deployment logs.",
            )
        )


def record_ai_failure(detail: str = "") -> None:
    """AI provider failure during chat — alert (deduplicated by key)."""
    _fire_and_forget(
        send_alert(
            "app.ai_provider_failure",
            "warning",
            "AI provider failing",
            "Chat responses are failing at the AI provider"
            + (f": {detail[:200]}" if detail else "")
            + ". Verify GEMINI_API_KEY validity and the GEMINI_MODEL setting.",
        )
    )
