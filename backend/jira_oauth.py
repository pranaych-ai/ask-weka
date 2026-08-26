"""Per-employee Jira connection via Atlassian OAuth 2.0 (3LO).

WEKA's Atlassian login is behind Okta, so when an employee clicks "Connect
Jira" they authenticate with their Okta SSO and Ask WEKA receives a token
scoped to *that employee*. Tickets are then filed in Jira AS the employee —
they are the real reporter, get Jira notifications, and their own Jira
permissions apply. No shared bot account, no impersonation.

Routing: the conversation's domain (IT / HR / …) maps to a Jira project key
via an admin-editable mapping stored in AppSetting "jira_project_map"
(JSON object, e.g. {"IT": "ITSM", "HR": "HR", "": "ITSM"} — "" is the
fallback for unclassified conversations).
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .admin import admin_audited
from .audit import log_event
from .auth import require_user
from .db import get_db
from .models import AppSetting, JiraAccount

router = APIRouter(prefix="/api/jira")
admin_router = APIRouter(prefix="/api/admin/jira", dependencies=[Depends(admin_audited)])

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
SCOPES = "read:jira-work write:jira-work read:me offline_access"

PROJECT_MAP_KEY = "jira_project_map"
DEFAULT_PROJECT_MAP = {"IT": "", "HR": "", "": ""}


def _client_creds() -> tuple[str, str]:
    cid = os.environ.get("ATLASSIAN_CLIENT_ID", "")
    secret = os.environ.get("ATLASSIAN_CLIENT_SECRET", "")
    return cid, secret


def jira_enabled() -> bool:
    cid, secret = _client_creds()
    return bool(cid and secret)


# ---------- Token encryption at rest ----------
# Refresh tokens grant long-lived access to an employee's Jira account, so
# they are never stored in plaintext: Fernet key derived from SESSION_SECRET.


def _fernet():
    from cryptography.fernet import Fernet

    secret = os.environ.get("SESSION_SECRET", "") or "dev-only-insecure"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _enc(value: str) -> str:
    if not value:
        return ""
    return "enc:" + _fernet().encrypt(value.encode()).decode()


def _dec(value: str) -> str:
    if not value.startswith("enc:"):
        return value  # legacy plaintext row
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value[4:].encode()).decode()
    except InvalidToken:
        return ""


def _redirect_uri(request: Request) -> str:
    pinned = os.environ.get("ATLASSIAN_REDIRECT_URI", "")
    if pinned:
        return pinned
    uri = str(request.url_for("jira_callback"))
    # Behind the Replit proxy the app sees http; Atlassian requires https.
    if uri.startswith("http://") and "localhost" not in uri and "127.0.0.1" not in uri:
        uri = "https://" + uri[len("http://"):]
    return uri


# ---------- Connect / callback / status ----------


@router.get("/status")
def jira_status(db: Session = Depends(get_db), user: dict = Depends(require_user)):
    acct = db.get(JiraAccount, user["username"])
    return {
        "enabled": jira_enabled(),
        "connected": bool(acct),
        "email": acct.email if acct else "",
        "site_url": acct.site_url if acct else "",
    }


@router.get("/connect")
def jira_connect(request: Request, user: dict = Depends(require_user)):
    if not jira_enabled():
        raise HTTPException(503, "Jira integration is not configured")
    state = secrets.token_urlsafe(24)
    request.session["jira_oauth_state"] = state
    cid, _ = _client_creds()
    params = httpx.QueryParams(
        {
            "audience": "api.atlassian.com",
            "client_id": cid,
            "scope": SCOPES,
            "redirect_uri": _redirect_uri(request),
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
    )
    return RedirectResponse(f"{AUTH_URL}?{params}")


@router.get("/callback")
async def jira_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    expected = request.session.pop("jira_oauth_state", None)
    if not expected or state != expected:
        raise HTTPException(400, "Invalid OAuth state")
    if not code:
        raise HTTPException(400, "Authorization was not granted")
    cid, secret = _client_creds()
    async with httpx.AsyncClient(timeout=20) as client:
        tok = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": cid,
                "client_secret": secret,
                "code": code,
                "redirect_uri": _redirect_uri(request),
            },
        )
        if tok.status_code != 200:
            raise HTTPException(502, "Jira token exchange failed")
        td = tok.json()
        access = td.get("access_token", "")
        refresh = td.get("refresh_token", "")
        expires_in = int(td.get("expires_in", 3600))
        res = await client.get(
            RESOURCES_URL, headers={"Authorization": f"Bearer {access}"}
        )
        sites = res.json() if res.status_code == 200 else []
        if not sites:
            raise HTTPException(502, "No accessible Jira site for this account")
        site = sites[0]
        me = await client.get(
            "https://api.atlassian.com/me", headers={"Authorization": f"Bearer {access}"}
        )
        med = me.json() if me.status_code == 200 else {}

    acct = db.get(JiraAccount, user["username"]) or JiraAccount(username=user["username"])
    acct.access_token = _enc(access)
    acct.refresh_token = _enc(refresh)
    acct.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    acct.cloud_id = site.get("id", "")
    acct.site_url = site.get("url", "")
    acct.account_id = med.get("account_id", "")
    acct.email = (med.get("email") or "").lower()
    db.merge(acct)
    log_event(db, user["username"], "jira.connect", f"site={acct.site_url}")
    db.commit()
    return RedirectResponse("/?jira=connected")


@router.delete("/connection")
def jira_disconnect(db: Session = Depends(get_db), user: dict = Depends(require_user)):
    acct = db.get(JiraAccount, user["username"])
    if acct:
        db.delete(acct)
        log_event(db, user["username"], "jira.disconnect", "")
        db.commit()
    return {"ok": True}


# ---------- Token refresh + issue creation ----------


# Single-flight refresh per account: Atlassian rotates refresh tokens, so two
# concurrent refreshes would invalidate each other and kill the connection.
_refresh_locks: dict = {}


def _refresh_lock(username: str) -> asyncio.Lock:
    lock = _refresh_locks.get(username)
    if lock is None:
        lock = _refresh_locks[username] = asyncio.Lock()
    return lock


def _not_expired(acct: JiraAccount) -> bool:
    exp = acct.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > datetime.now(timezone.utc)


async def _fresh_token(db: Session, acct: JiraAccount) -> str:
    if _not_expired(acct):
        return _dec(acct.access_token)
    async with _refresh_lock(acct.username):
        db.refresh(acct)  # another request may have refreshed while we waited
        if _not_expired(acct):
            return _dec(acct.access_token)
        cid, secret = _client_creds()
        async with httpx.AsyncClient(timeout=20) as client:
            tok = await client.post(
                TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": cid,
                    "client_secret": secret,
                    "refresh_token": _dec(acct.refresh_token),
                },
            )
        if tok.status_code != 200:
            raise HTTPException(
                401, "Your Jira connection expired — please reconnect Jira and try again"
            )
        td = tok.json()
        access = td.get("access_token", "")
        acct.access_token = _enc(access)
        # Atlassian uses rotating refresh tokens.
        if td.get("refresh_token"):
            acct.refresh_token = _enc(td["refresh_token"])
        acct.expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(td.get("expires_in", 3600)) - 60
        )
        db.commit()
        return access


def _adf(body: str) -> dict:
    """Plain text → Atlassian Document Format, one paragraph per line block."""
    paragraphs = [p for p in body.split("\n") if p.strip()] or [body or " "]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]}
            for p in paragraphs
        ],
    }


def get_project_map(db: Session) -> dict:
    row = db.get(AppSetting, PROJECT_MAP_KEY)
    if not row or not row.value:
        return dict(DEFAULT_PROJECT_MAP)
    try:
        data = json.loads(row.value)
        return data if isinstance(data, dict) else dict(DEFAULT_PROJECT_MAP)
    except ValueError:
        return dict(DEFAULT_PROJECT_MAP)


def project_for_domain(db: Session, domain: str) -> str:
    m = get_project_map(db)
    return (m.get(domain or "") or m.get("") or "").strip()


async def create_jira_issue(
    db: Session, acct: JiraAccount, project_key: str, title: str, body: str
) -> tuple[str, str]:
    """Files the issue as the connected employee. Returns (key, url)."""
    token = await _fresh_token(db, acct)
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": title[:255],
            "description": _adf(body),
            "issuetype": {"name": "Task"},
        }
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://api.atlassian.com/ex/jira/{acct.cloud_id}/rest/api/3/issue",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code not in (200, 201):
        detail = ""
        try:
            errs = r.json()
            detail = "; ".join(
                list(errs.get("errorMessages", []))
                + [f"{k}: {v}" for k, v in (errs.get("errors") or {}).items()]
            )
        except ValueError:
            pass
        raise HTTPException(502, f"Jira rejected the ticket ({r.status_code}): {detail or r.text[:200]}")
    data = r.json()
    key = data.get("key", "")
    url = f"{acct.site_url}/browse/{key}" if acct.site_url and key else ""
    return key, url


# ---------- Admin: project routing ----------


@admin_router.get("/mapping")
def get_mapping(db: Session = Depends(get_db)):
    return {"enabled": jira_enabled(), "mapping": get_project_map(db)}


class MappingUpdate(BaseModel):
    mapping: dict


@admin_router.put("/mapping")
def put_mapping(
    req: MappingUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    clean = {}
    for k, v in req.mapping.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise HTTPException(400, "Mapping must be domain → project key strings")
        clean[k.strip().upper() if k.strip() else ""] = v.strip().upper()
    row = db.get(AppSetting, PROJECT_MAP_KEY) or AppSetting(key=PROJECT_MAP_KEY)
    row.value = json.dumps(clean)
    db.merge(row)
    log_event(db, user["username"], "jira.mapping", row.value)
    db.commit()
    return {"ok": True, "mapping": clean}
