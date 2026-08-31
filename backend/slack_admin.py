"""Slack integration management APIs.

- /api/admin/slack/*: admin-only (centrally audited) — non-secret config,
  server-side connection verification, manifest generation, test DM, disable.
- /api/slack/prefs: authenticated employee notification preferences with
  Slack email lookup and explicit consent records.

No endpoint here accepts, stores, returns, masks, or logs credential values.
The bot token / signing secret live only in Replit Secrets.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .admin import admin_audited
from .audit import log_event
from .auth import require_user
from .db import get_db
from .models import SlackUserPref
from . import slack_service as svc

router = APIRouter(prefix="/api/admin/slack", dependencies=[Depends(admin_audited)])
prefs_router = APIRouter(prefix="/api/slack")


def _config_out(db: Session) -> dict:
    row = svc.get_integration(db)
    return {
        # env-credential presence only — never values, never masks
        "credentials_configured": svc.creds_configured(),
        "enabled": row.enabled,
        "verified": row.verified,
        "team_id": row.team_id,
        "team_name": row.team_name,
        "workspace_url": row.workspace_url,
        "bot_user_id": row.bot_user_id,
        "bot_name": row.bot_name,
        "app_id": row.app_id,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verify_error": row.last_verify_error,
        "features": svc.parse_features(row),
        "feature_meta": {
            k: {"label": m["label"], "description": m["description"], "user_optin": m["user_optin"]}
            for k, m in svc.FEATURES.items()
        },
        "notify_channel": row.notify_channel,
        "digest_time": row.digest_time,
        "digest_timezone": row.digest_timezone,
        "digest_last_run": row.digest_last_run,
        "slash_commands": svc.parse_commands(row),
    }


@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    return _config_out(db)


class CommandIn(BaseModel):
    command: str
    description: str = ""
    usage_hint: str = ""
    domain: str = ""
    instructions: str = ""


class ConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    features: Optional[dict] = None
    notify_channel: Optional[str] = None
    digest_time: Optional[str] = None
    digest_timezone: Optional[str] = None
    slash_commands: Optional[list[CommandIn]] = None


@router.put("/config")
def put_config(
    req: ConfigPatch,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    row = svc.get_integration(db)
    if req.enabled is not None:
        row.enabled = req.enabled
    if req.features is not None:
        unknown = set(req.features) - set(svc.FEATURES)
        if unknown:
            raise HTTPException(400, f"Unknown features: {sorted(unknown)}")
        merged = svc.parse_features(row)
        for k, v in req.features.items():
            merged[k] = bool(v)
        row.features = json.dumps(merged)
    if req.notify_channel is not None:
        row.notify_channel = req.notify_channel.strip()[:120]
    if req.digest_time is not None:
        t = req.digest_time.strip()
        parts = t.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) \
                or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            raise HTTPException(400, "digest_time must be HH:MM (24h)")
        row.digest_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    if req.digest_timezone is not None:
        from zoneinfo import ZoneInfo

        tz = req.digest_timezone.strip() or "UTC"
        try:
            ZoneInfo(tz)
        except Exception:
            raise HTTPException(400, f"Unknown timezone: {tz}")
        row.digest_timezone = tz
    if req.slash_commands is not None:
        cmds = []
        for c in req.slash_commands:
            cmd = c.command.strip()
            if not cmd.startswith("/") or len(cmd) < 2 or " " in cmd:
                raise HTTPException(400, f"Invalid slash command: {cmd!r}")
            if c.domain not in ("", "IT", "HR"):
                raise HTTPException(400, "Command domain must be IT, HR, or empty")
            cmds.append(
                {
                    "command": cmd[:32],
                    "description": c.description.strip()[:100],
                    "usage_hint": c.usage_hint.strip()[:100],
                    "domain": c.domain,
                    "instructions": c.instructions.strip()[:2000],
                }
            )
        row.slash_commands = json.dumps(cmds)
    log_event(db, user["username"], "slack.config.update", f"enabled={row.enabled}")
    db.commit()
    return _config_out(db)


@router.post("/verify")
async def verify(db: Session = Depends(get_db), user: dict = Depends(admin_audited)):
    row = await svc.verify_connection(db)
    log_event(
        db, user["username"], "slack.verify",
        f"verified={row.verified}" + (f" error={row.last_verify_error}" if row.last_verify_error else ""),
    )
    db.commit()
    return _config_out(db)


@router.post("/disable")
def disable(db: Session = Depends(get_db), user: dict = Depends(admin_audited)):
    row = svc.get_integration(db)
    row.enabled = False
    log_event(db, user["username"], "slack.disable", "")
    db.commit()
    return _config_out(db)


@router.get("/manifest")
def manifest(db: Session = Depends(get_db)):
    m = svc.generate_manifest(db)
    return {"manifest": m, "manifest_json": json.dumps(m, indent=2), "base_url": svc.public_base_url()}


class TestSend(BaseModel):
    channel: str = ""  # empty = DM the calling admin


@router.post("/test")
async def send_test(
    req: TestSend,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    """Send a test message. Bypasses per-feature gates (it IS the connection
    test) but still requires env credentials + verified + enabled."""
    if not svc.creds_configured():
        raise HTTPException(503, "Slack credentials are not set in Replit Secrets")
    row = svc.get_integration(db)
    if not (row.enabled and row.verified):
        raise HTTPException(400, "Verify the connection and enable the integration first")
    text = ":white_check_mark: Ask WEKA Slack integration test — you can safely ignore this."
    if req.channel.strip():
        target = req.channel.strip()
    else:
        email = (user.get("email") or user["username"]).strip().lower()
        slack_id, err = await svc.resolve_slack_identity(email)
        if not slack_id:
            raise HTTPException(400, err or "Could not resolve your Slack account")
        data = await svc.slack_api("conversations.open", {"users": slack_id})
        if not data.get("ok"):
            raise HTTPException(502, f"Could not open a DM ({data.get('error', 'unknown')})")
        target = data["channel"]["id"]
    if not await svc.notify_direct_channel("dm_chat", target, text):
        raise HTTPException(
            502,
            "Test message was blocked by Slack configuration or could not be delivered",
        )
    log_event(db, user["username"], "slack.test", f"channel={'dm' if not req.channel else 'channel'}")
    db.commit()
    return {"ok": True}


# ---------- Employee notification preferences ----------


@prefs_router.get("/prefs")
def get_prefs(db: Session = Depends(get_db), user: dict = Depends(require_user)):
    row = svc.get_integration(db)
    admin_features = svc.parse_features(row)
    available = [
        {
            "key": k,
            "label": m["label"],
            "description": m["description"],
            "enabled": bool(
                svc.creds_configured() and row.enabled and row.verified and admin_features.get(k)
            ),
        }
        for k, m in svc.FEATURES.items()
        if m["user_optin"] and (not m.get("admin_only") or user.get("is_admin"))
    ]
    pref = db.get(SlackUserPref, user["username"])
    return {
        "integration_active": bool(svc.creds_configured() and row.enabled and row.verified),
        "available": available,
        "prefs": svc.parse_prefs(pref),
        "slack_linked": bool(pref and pref.slack_user_id),
        "slack_email": pref.slack_email if pref else "",
    }


class PrefsIn(BaseModel):
    prefs: dict


@prefs_router.put("/prefs")
async def put_prefs(
    req: PrefsIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    allowed = {
        k
        for k, m in svc.FEATURES.items()
        if m["user_optin"] and (not m.get("admin_only") or user.get("is_admin"))
    }
    unknown = set(req.prefs) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown preferences: {sorted(unknown)}")
    pref = db.get(SlackUserPref, user["username"]) or SlackUserPref(username=user["username"])
    merged = svc.parse_prefs(pref)
    for k, v in req.prefs.items():
        merged[k] = bool(v)
    opting_in = any(merged.values())

    # Resolve the Slack identity from the WEKA email at opt-in time so a
    # mismatch surfaces immediately as a clear error, not a silent miss.
    if opting_in and not pref.slack_user_id:
        if not svc.creds_configured():
            raise HTTPException(503, "Slack integration is not configured yet")
        email = (user.get("email") or user["username"]).strip().lower()
        slack_id, err = await svc.resolve_slack_identity(email)
        if not slack_id:
            raise HTTPException(400, err)
        pref.slack_user_id = slack_id
        pref.slack_email = email

    pref.prefs = json.dumps(merged)
    db.merge(pref)
    log_event(
        db, user["username"], "slack.prefs.update",
        " ".join(f"{k}={v}" for k, v in sorted(merged.items())),
    )
    db.commit()
    return {"ok": True, "prefs": merged, "slack_linked": bool(pref.slack_user_id)}
