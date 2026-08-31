"""Central Slack communication service.

Security model (WEKA policy):
- Credentials (SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET) are read ONLY from the
  server environment (Replit Secrets). They are never stored in the database,
  never accepted from or returned to any client, never masked, never logged.
- Every outbound send passes ONE gate: env credentials present + integration
  verified & enabled by an admin + the specific feature enabled + (for
  user-specific deliveries) explicit recipient consent with a resolved Slack
  identity. Skipped sends never break the caller; they leave a low-noise
  audit record instead.
- Rate-limited calls (HTTP 429 / Slack "ratelimited") are retried exactly once
  after the advertised Retry-After delay.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .audit import log_event_standalone
from .db import SessionLocal
from .models import SlackIntegration, SlackUserPref

logger = logging.getLogger("askweka.slack.service")

SLACK_API = "https://slack.com/api"

# Feature registry: key -> (label, description, user_optin).
# user_optin=True means the delivery is user-specific and additionally
# requires the recipient's explicit consent in SlackUserPref.
FEATURES: dict[str, dict] = {
    "dm_chat": {
        "label": "DM assistant chat",
        "description": "Employees can DM the bot and get KB-grounded answers.",
        "user_optin": False,
        "default": True,
    },
    "channel_mentions": {
        "label": "Channel mentions",
        "description": "Employees can @mention the bot in a channel it was invited to and get a threaded answer.",
        "user_optin": False,
        "default": False,
    },
    "ticket_notifications": {
        "label": "Ticket notifications",
        "description": "DM an employee when their ticket is filed (Jira + Ask WEKA links).",
        "user_optin": True,
        "default": True,
    },
    "golden_notifications": {
        "label": "Golden-run notifications",
        "description": "DM the initiating admin when a golden-question run completes.",
        "user_optin": True,
        "default": True,
    },
    "regression_posts": {
        "label": "Regression channel posts",
        "description": "Post golden-run regressions to the notification channel.",
        "user_optin": False,
        "default": False,
    },
    "sync_alerts": {
        "label": "Source-sync failure alerts",
        "description": "Notify opted-in administrators when a knowledge-source sync fails.",
        "user_optin": True,
        "admin_only": True,
        "default": True,
    },
    "security_alerts": {
        "label": "Security & availability alerts",
        "description": "Post service failures, repeated sign-in failures, and admin-rights changes to the notification channel.",
        "user_optin": False,
        "default": True,
    },
    "usage_digest": {
        "label": "Usage digest",
        "description": "Scheduled AI-written usage digest posted to the notification channel.",
        "user_optin": False,
        "default": False,
    },
}

DEFAULT_COMMANDS = [
    {
        "command": "/askweka",
        "description": "Ask the WEKA assistant a question",
        "usage_hint": "How do I request a laptop?",
    }
]


# ---------- Credentials (env only — never DB, never logged) ----------


def bot_token() -> str:
    return os.environ.get("SLACK_BOT_TOKEN", "")


def signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "")


def creds_configured() -> bool:
    return bool(bot_token() and signing_secret())


# ---------- Integration row (singleton) ----------


def get_integration(db: Session) -> SlackIntegration:
    row = db.get(SlackIntegration, 1)
    if not row:
        row = SlackIntegration(id=1)
        db.add(row)
        db.flush()
    return row


def parse_features(row: SlackIntegration) -> dict[str, bool]:
    try:
        data = json.loads(row.features) if row.features else {}
    except ValueError:
        data = {}
    return {
        k: bool(data.get(k, meta["default"])) for k, meta in FEATURES.items()
    }


def parse_commands(row: SlackIntegration) -> list[dict]:
    try:
        data = json.loads(row.slash_commands) if row.slash_commands else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        return [dict(c) for c in DEFAULT_COMMANDS]
    out = []
    for c in data:
        if isinstance(c, dict) and str(c.get("command", "")).startswith("/"):
            out.append(
                {
                    "command": str(c.get("command", ""))[:32],
                    "description": str(c.get("description", ""))[:100],
                    "usage_hint": str(c.get("usage_hint", ""))[:100],
                }
            )
    return out


def parse_prefs(pref: SlackUserPref | None) -> dict[str, bool]:
    if not pref:
        return {}
    try:
        data = json.loads(pref.prefs) if pref.prefs else {}
    except ValueError:
        data = {}
    return {k: bool(v) for k, v in data.items() if k in FEATURES}


# ---------- Slack Web API (retry-once on rate limit) ----------


async def slack_api(method: str, payload: dict | None = None, *, _retried: bool = False) -> dict:
    """POST to a Slack Web API method. Retries exactly once on rate limits."""
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SLACK_API}/{method}",
            json=payload or {},
            headers={"Authorization": f"Bearer {bot_token()}"},
        )
    rate_limited = r.status_code == 429
    try:
        data = r.json()
    except ValueError:
        data = {"ok": False, "error": f"http_{r.status_code}"}
    if not rate_limited and data.get("error") == "ratelimited":
        rate_limited = True
    if rate_limited and not _retried:
        try:
            delay = min(float(r.headers.get("Retry-After", "1")), 30.0)
        except ValueError:
            delay = 1.0
        await asyncio.sleep(delay)
        return await slack_api(method, payload, _retried=True)
    if rate_limited:
        return {"ok": False, "error": "ratelimited"}
    return data


# ---------- Connection verification (auth.test → non-secret metadata) ----------


async def verify_connection(db: Session) -> SlackIntegration:
    """Reads credentials server-side, calls auth.test, and stores only
    non-secret workspace/bot metadata plus the verification status."""
    row = get_integration(db)
    if not creds_configured():
        row.verified = False
        row.app_id = ""  # never keep a stale pin past a failed verification
        row.last_verify_error = "Slack credentials are not set in Replit Secrets"
        return row
    data = await slack_api("auth.test")
    if not data.get("ok"):
        row.verified = False
        row.app_id = ""
        row.last_verify_error = str(data.get("error", "unknown_error"))[:300]
        return row
    bot_user_id = str(data.get("user_id", ""))[:30]

    # Pin the Slack app ID as part of verification. This is mandatory:
    # inbound event callbacks are rejected until an app ID is pinned, so a
    # verification that cannot establish the app identity fails closed.
    # Primary (documented) path: auth.test's bot_id -> bots.info -> bot.app_id.
    # Fallback: the bot user's profile (api_app_id / bot_id) via users.info.
    app_id = ""
    try:
        bot_id = str(data.get("bot_id") or "")
        if bot_id:
            binfo = await slack_api("bots.info", {"bot": bot_id})
            if binfo.get("ok"):
                app_id = str((binfo.get("bot") or {}).get("app_id") or "")
        if not app_id:
            info = await slack_api("users.info", {"user": bot_user_id})
            profile = (info.get("user") or {}).get("profile") or {}
            if info.get("ok"):
                app_id = str(profile.get("api_app_id") or "")
                if not app_id and profile.get("bot_id"):
                    binfo = await slack_api("bots.info", {"bot": profile["bot_id"]})
                    if binfo.get("ok"):
                        app_id = str((binfo.get("bot") or {}).get("app_id") or "")
    except Exception:
        logger.exception("Slack app-id lookup failed")
    if not app_id:
        row.verified = False
        row.app_id = ""
        row.last_verify_error = (
            "Could not determine the Slack app ID for this bot token — "
            "re-install the app and verify again"
        )
        return row

    row.verified = True
    row.last_verify_error = ""
    row.team_id = str(data.get("team_id", ""))[:30]
    row.team_name = str(data.get("team", ""))[:200]
    row.workspace_url = str(data.get("url", ""))[:300]
    row.bot_user_id = bot_user_id
    row.bot_name = str(data.get("user", ""))[:200]
    row.app_id = app_id[:30]
    row.last_verified_at = datetime.now(timezone.utc)
    return row


# ---------- The single delivery gate ----------


def check_gate(db: Session, feature: str, username: str | None = None) -> tuple[bool, str]:
    """Returns (allowed, skip_reason). ALL outbound sends go through this.

    Requires: env credentials + verified & enabled integration + the feature
    enabled by an admin. If username is given (user-specific delivery), also
    requires the employee's consent and a resolved Slack identity.
    """
    if feature not in FEATURES:
        return False, f"unknown_feature:{feature}"
    if not creds_configured():
        return False, "credentials_missing"
    row = get_integration(db)
    if not row.enabled:
        return False, "integration_disabled"
    if not row.verified:
        return False, "not_verified"
    if not parse_features(row).get(feature):
        return False, "feature_disabled"
    if username is not None and FEATURES[feature]["user_optin"]:
        pref = db.get(SlackUserPref, username)
        if not pref or not parse_prefs(pref).get(feature):
            return False, "no_consent"
        if not pref.slack_user_id:
            return False, "slack_identity_unresolved"
    return True, ""


def _audit_skip(feature: str, reason: str, username: str | None) -> None:
    # Low-noise: consent/config skips are expected and only logged at debug;
    # a single audit row records that a gated send was skipped and why.
    logger.debug("Slack send skipped feature=%s reason=%s", feature, reason)
    log_event_standalone(
        "system", "slack.skip", f"feature={feature} reason={reason}"
        + (f" user={username}" if username else "")
    )


# ---------- Block Kit helpers ----------


def text_blocks(text: str) -> list[dict]:
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}]


def to_slack_mrkdwn(text: str) -> str:
    """Convert model markdown to Slack's mrkdwn dialect (best effort).

    Links, bold, headings, and bullets — the constructs the assistant
    actually emits. Never raises; formatting is presentation-only."""
    import re

    try:
        out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text)
        out = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out)
        out = re.sub(r"(?<![\w_])__(.+?)__(?![\w_])", r"*\1*", out)
        out = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", out, flags=re.M)
        out = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", out, flags=re.M)
        return out
    except Exception:
        return text


def answer_blocks(text: str) -> list[dict]:
    """Slack-formatted answer sections plus a button back to Ask WEKA."""
    mrkdwn = to_slack_mrkdwn(text)
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in (mrkdwn[i : i + 2900] for i in range(0, min(len(mrkdwn), 2900 * 4), 2900))
        if chunk
    ] or text_blocks(mrkdwn)
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Ask WEKA"},
                    "url": public_base_url(),
                }
            ],
        }
    )
    return blocks


# ---------- Outbound sends (best-effort; never raise to callers) ----------


async def _open_dm(slack_user_id: str) -> str:
    data = await slack_api("conversations.open", {"users": slack_user_id})
    return ((data.get("channel") or {}).get("id") or "") if data.get("ok") else ""


async def notify_user(feature: str, username: str, text: str, blocks: list[dict] | None = None) -> bool:
    """DM an employee, gated on admin feature + their consent. Best-effort."""
    try:
        db = SessionLocal()
        try:
            ok, reason = check_gate(db, feature, username)
            slack_id = ""
            if ok:
                pref = db.get(SlackUserPref, username)
                slack_id = pref.slack_user_id if pref else ""
            db.commit()
        finally:
            db.close()
        if not ok:
            _audit_skip(feature, reason, username)
            return False
        channel = await _open_dm(slack_id)
        if not channel:
            _audit_skip(feature, "dm_open_failed", username)
            return False
        data = await slack_api(
            "chat.postMessage",
            {
                "channel": channel,
                "text": text[:3000],
                "blocks": blocks or text_blocks(text),
                "unfurl_links": False,
            },
        )
        if data.get("ok"):
            log_event_standalone("system", "slack.notify", f"feature={feature} kind=dm")
            return True
        _audit_skip(feature, f"send_failed:{data.get('error', 'unknown')}", username)
        return False
    except Exception:
        logger.exception("Slack user notification failed (feature=%s)", feature)
        return False


async def notify_channel(feature: str, text: str, blocks: list[dict] | None = None) -> bool:
    """Post to the configured notification channel, gated. Best-effort."""
    try:
        db = SessionLocal()
        try:
            ok, reason = check_gate(db, feature)
            channel = get_integration(db).notify_channel if ok else ""
            db.commit()
        finally:
            db.close()
        if not ok:
            _audit_skip(feature, reason, None)
            return False
        if not channel:
            _audit_skip(feature, "no_channel_configured", None)
            return False
        data = await slack_api(
            "chat.postMessage",
            {
                "channel": channel,
                "text": text[:3000],
                "blocks": blocks or text_blocks(text),
                "unfurl_links": False,
            },
        )
        if data.get("ok"):
            log_event_standalone("system", "slack.notify", f"feature={feature} kind=channel")
            return True
        _audit_skip(feature, f"send_failed:{data.get('error', 'unknown')}", None)
        return False
    except Exception:
        logger.exception("Slack channel notification failed (feature=%s)", feature)
        return False


async def notify_direct_channel(
    feature: str,
    channel: str,
    text: str,
    blocks: list[dict] | None = None,
    thread_ts: str = "",
) -> bool:
    """Post to a known Slack channel/DM while re-evaluating the central gate.

    This is used by inbound DM chat, where Slack already supplied the DM
    channel. Rechecking immediately before chat.postMessage prevents a reply
    after an administrator disables or unverifies the integration while the
    model is generating an answer.
    """
    try:
        db = SessionLocal()
        try:
            ok, reason = check_gate(db, feature)
            db.commit()
        finally:
            db.close()
        if not ok:
            _audit_skip(feature, reason, None)
            return False
        payload = {
            "channel": channel,
            "text": text[:3000],
            "blocks": blocks or text_blocks(text),
            "unfurl_links": False,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = await slack_api("chat.postMessage", payload)
        if data.get("ok"):
            log_event_standalone("system", "slack.notify", f"feature={feature} kind=direct")
            return True
        _audit_skip(feature, f"send_failed:{data.get('error', 'unknown')}", None)
        return False
    except Exception:
        logger.exception("Slack direct notification failed (feature=%s)", feature)
        return False


async def send_inbound_notice(channel: str, text: str, blocks: list[dict] | None = None) -> bool:
    """Post a service notice to a channel gated on the master switch only.

    Used for the once-daily "DM chat is disabled" pointer — by definition
    the dm_chat feature is off, so the per-feature gate cannot apply, but the
    admin master switch, verification, and env credentials still must hold."""
    try:
        db = SessionLocal()
        try:
            row = get_integration(db)
            ok = creds_configured() and row.enabled and row.verified
            db.commit()
        finally:
            db.close()
        if not ok:
            _audit_skip("dm_chat", "integration_inactive", None)
            return False
        data = await slack_api(
            "chat.postMessage",
            {
                "channel": channel,
                "text": text[:3000],
                "blocks": blocks or text_blocks(text),
                "unfurl_links": False,
            },
        )
        if data.get("ok"):
            log_event_standalone("system", "slack.notify", "feature=dm_chat kind=disabled_notice")
            return True
        _audit_skip("dm_chat", f"send_failed:{data.get('error', 'unknown')}", None)
        return False
    except Exception:
        logger.exception("Slack disabled-DM notice failed")
        return False


def upsert_slack_identity(db: Session, username: str, slack_user_id: str) -> None:
    """Record/refresh a WEKA-verified employee's Slack identity after an
    inbound interaction. Never touches consent preferences — recording the
    identity does not opt the employee into any outbound feature."""
    if not username or not slack_user_id:
        return
    pref = db.get(SlackUserPref, username)
    if not pref:
        pref = SlackUserPref(username=username)
        db.add(pref)
    pref.slack_user_id = slack_user_id
    if not pref.slack_email:
        pref.slack_email = username


async def notify_source_sync_failure(admin_username: str, text: str) -> bool:
    """Notify the initiating admin and the optional admin channel.

    The direct recipient path uses the normal consent-aware user gate. The
    channel path remains useful for a shared operations channel, but is not a
    prerequisite for attempting the administrator notification.
    """
    user_sent, channel_sent = await asyncio.gather(
        notify_user("sync_alerts", admin_username, text),
        notify_channel("sync_alerts", text),
    )
    return user_sent or channel_sent


async def send_file(feature: str, channel: str, filename: str, content: bytes, title: str = "") -> bool:
    """Upload a file to a channel via Slack's external-upload flow. Gated,
    best-effort, credentials never leave the server."""
    import httpx

    try:
        db = SessionLocal()
        try:
            ok, reason = check_gate(db, feature)
            db.commit()
        finally:
            db.close()
        if not ok:
            _audit_skip(feature, reason, None)
            return False
        step1 = await slack_api(
            "files.getUploadURLExternal",
            {"filename": filename, "length": len(content)},
        )
        if not step1.get("ok"):
            _audit_skip(feature, f"upload_url_failed:{step1.get('error', '')}", None)
            return False
        async with httpx.AsyncClient(timeout=30) as client:
            up = await client.post(step1["upload_url"], content=content)
            if up.status_code != 200:
                _audit_skip(feature, f"upload_failed:http_{up.status_code}", None)
                return False
        step3 = await slack_api(
            "files.completeUploadExternal",
            {
                "files": [{"id": step1["file_id"], "title": title or filename}],
                "channel_id": channel,
            },
        )
        if step3.get("ok"):
            log_event_standalone("system", "slack.notify", f"feature={feature} kind=file")
            return True
        _audit_skip(feature, f"complete_failed:{step3.get('error', '')}", None)
        return False
    except Exception:
        logger.exception("Slack file delivery failed (feature=%s)", feature)
        return False


# ---------- Slack identity resolution (employee opt-in) ----------


async def resolve_slack_identity(email: str) -> tuple[str, str]:
    """Look up a Slack account by WEKA email. Returns (slack_user_id, error).

    A clear mismatch error is returned when the email cannot be resolved so
    the employee knows to contact IT rather than silently missing messages."""
    email = (email or "").strip().lower()
    if not email:
        return "", "Your account has no email on file — Slack lookup needs your WEKA email."
    data = await slack_api("users.lookupByEmail", {"email": email})
    if not data.get("ok"):
        if data.get("error") in ("users_not_found", "user_not_found"):
            return "", (
                f"No Slack account matches {email}. Your Slack email must match "
                "your WEKA login email — contact #it-help if they differ."
            )
        return "", f"Slack lookup failed ({data.get('error', 'unknown')}) — try again later."
    user = data.get("user") or {}
    if user.get("deleted") or user.get("is_bot"):
        return "", f"The Slack account for {email} is not an active employee account."
    return user.get("id", ""), ""


# ---------- App manifest generation (deployment-aware) ----------


def public_base_url() -> str:
    override = os.environ.get("SLACK_APP_BASE_URL", "").strip().rstrip("/")
    if override:
        return override
    domains = os.environ.get("REPLIT_DOMAINS", "") or os.environ.get("REPLIT_DEV_DOMAIN", "")
    first = domains.split(",")[0].strip()
    return f"https://{first}" if first else "https://<your-app-domain>"


def generate_manifest(db: Session) -> dict:
    """Slack app manifest matching the enabled configuration. Contains no
    secrets — it is what an admin pastes into api.slack.com to (re)create
    the app."""
    row = get_integration(db)
    base = public_base_url()
    commands = parse_commands(row)
    features = parse_features(row)
    manifest: dict = {
        "display_information": {
            "name": "Ask WEKA",
            "description": "WEKA's internal HR/IT assistant",
            "background_color": "#1a1a2e",
        },
        "features": {
            "bot_user": {"display_name": "Ask WEKA", "always_online": True},
        },
        "oauth_config": {
            "scopes": {
                "bot": [
                    "chat:write",
                    "im:history",
                    "im:read",
                    "im:write",
                    "users:read",
                    "users:read.email",
                    "files:write",
                ]
            }
        },
        "settings": {
            "event_subscriptions": {
                "request_url": f"{base}/api/slack/events",
                "bot_events": ["message.im"],
            },
            "interactivity": {
                "is_enabled": True,
                "request_url": f"{base}/api/slack/interactions",
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }
    if features.get("dm_chat") or features.get("channel_mentions"):
        # The eyes reaction while an answer is being generated.
        manifest["oauth_config"]["scopes"]["bot"].append("reactions:write")
    if features.get("channel_mentions"):
        manifest["oauth_config"]["scopes"]["bot"].append("app_mentions:read")
        manifest["settings"]["event_subscriptions"]["bot_events"].append("app_mention")
    if commands and features.get("dm_chat"):
        manifest["oauth_config"]["scopes"]["bot"].append("commands")
        manifest["features"]["slash_commands"] = [
            {
                "command": c["command"],
                "url": f"{base}/api/slack/commands",
                "description": c["description"] or "Ask WEKA",
                "usage_hint": c.get("usage_hint", ""),
                "should_escape": False,
            }
            for c in commands
        ]
    return manifest
