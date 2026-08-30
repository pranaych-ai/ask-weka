"""Okta SSO (OIDC) authentication.

Per WEKA golden architecture:
- Okta OIDC only — no local accounts or passwords.
- Sessions: <= 8h idle, <= 24h absolute.
- Auth enforced server-side on every /api route.

Configuration (env):
- OKTA_ISSUER        e.g. https://weka.okta.com/oauth2/default
- OKTA_CLIENT_ID
- OKTA_CLIENT_SECRET (Replit Secret)

If these are not set, the app runs in DEV MODE with auth disabled and the
user recorded as "anonymous". Production must have them set.
"""

import os
import time

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from .alerts import record_admin_change, record_auth_failure
from .audit import log_event_standalone

OKTA_ISSUER = os.environ.get("OKTA_ISSUER", "").rstrip("/")
OKTA_CLIENT_ID = os.environ.get("OKTA_CLIENT_ID", "")
OKTA_CLIENT_SECRET = os.environ.get("OKTA_CLIENT_SECRET", "")

AUTH_ENABLED = bool(OKTA_ISSUER and OKTA_CLIENT_ID and OKTA_CLIENT_SECRET)

# Okta group that grants admin access (dl-app-<appname> convention).
# Comma-separated to allow e.g. "dl-app-askweka-admin,dl-app-askweka-admin-dev".
ADMIN_GROUPS = {
    g.strip()
    for g in os.environ.get("OKTA_ADMIN_GROUPS", "dl-app-askweka-admin").split(",")
    if g.strip()
}

# Bootstrap fallback until IT creates the dl-app-* groups: comma-separated
# usernames/emails that get admin. Prefer groups once they exist.
ADMIN_USERS = {
    u.strip().lower()
    for u in os.environ.get("OKTA_ADMIN_USERS", "").split(",")
    if u.strip()
}

IDLE_TIMEOUT_S = 8 * 3600      # WEKA policy: <= 8 hours idle
ABSOLUTE_TIMEOUT_S = 24 * 3600  # WEKA policy: <= 24 hours absolute

router = APIRouter()

oauth = OAuth()
if AUTH_ENABLED:
    oauth.register(
        name="okta",
        client_id=OKTA_CLIENT_ID,
        client_secret=OKTA_CLIENT_SECRET,
        server_metadata_url=f"{OKTA_ISSUER}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email groups"},
    )


def current_user(request: Request) -> dict | None:
    """Return the session user, enforcing idle + absolute timeouts."""
    if not AUTH_ENABLED:
        # Dev mode: anonymous user gets admin so the portal can be developed.
        return {
            "username": "anonymous",
            "name": "Dev mode (no SSO)",
            "email": "",
            "is_admin": True,
        }
    user = request.session.get("user")
    if not user:
        return None
    now = time.time()
    if now - user.get("last_seen", 0) > IDLE_TIMEOUT_S:
        request.session.clear()
        return None
    if now - user.get("logged_in_at", 0) > ABSOLUTE_TIMEOUT_S:
        request.session.clear()
        return None
    user["last_seen"] = now
    request.session["user"] = user
    return user


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def require_admin(request: Request) -> dict:
    """Server-side RBAC gate for every /api/admin route."""
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


def _previous_admin_state(username: str) -> bool | None:
    """Admin flag recorded at this account's most recent successful login,
    or None for a first-time sign-in. Read from the audit trail so the
    comparison survives restarts. Never raises into the login flow."""
    try:
        from .db import SessionLocal
        from .models import ActivityLog

        db = SessionLocal()
        try:
            row = (
                db.query(ActivityLog)
                .filter(ActivityLog.username == username, ActivityLog.action == "login")
                .order_by(ActivityLog.created_at.desc())
                .first()
            )
        finally:
            db.close()
        if not row:
            return None
        return "admin=True" in (row.detail or "")
    except Exception:
        return None


@router.get("/auth/login")
async def login(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse("/")
    redirect_uri = str(request.url_for("auth_callback"))
    # Behind the Replit proxy the app sees http; Okta requires https redirect URIs.
    if redirect_uri.startswith("http://") and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
        redirect_uri = "https://" + redirect_uri[len("http://"):]
    return await oauth.okta.authorize_redirect(request, redirect_uri)


@router.get("/api/callback", name="auth_callback")
async def auth_callback(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse("/")
    try:
        token = await oauth.okta.authorize_access_token(request)
    except OAuthError as e:
        # Record the failure (audit + repeated-failure alerting). The error
        # code is safe metadata; no credentials or tokens are involved.
        log_event_standalone("unknown", "login.failed", f"reason={e.error}")
        record_auth_failure(str(e.error or ""))
        raise HTTPException(401, f"Okta sign-in failed: {e.error}")
    claims = token.get("userinfo") or {}
    now = time.time()
    groups = claims.get("groups") or []
    username = claims.get("preferred_username") or claims.get("email") or "unknown"
    via_group = bool(ADMIN_GROUPS & {str(g) for g in groups})
    via_bootstrap = (
        username.lower() in ADMIN_USERS
        or (claims.get("email", "") or "").lower() in ADMIN_USERS
    )
    is_admin = via_group or via_bootstrap

    # Detect unexpected admin-rights changes: compare with this account's
    # previous recorded sign-in and alert owners when the outcome differs.
    prev_admin = _previous_admin_state(username)
    if prev_admin is not None and prev_admin != is_admin:
        record_admin_change(
            username,
            is_admin,
            "Okta group" if via_group else ("OKTA_ADMIN_USERS bootstrap list" if via_bootstrap else "none"),
        )

    request.session["user"] = {
        "username": username,
        "name": claims.get("name", ""),
        "email": claims.get("email", ""),
        "is_admin": is_admin,
        "logged_in_at": now,
        "last_seen": now,
    }
    log_event_standalone(username, "login", f"admin={is_admin}")
    return RedirectResponse("/")


@router.get("/auth/logout")
async def logout(request: Request):
    user = request.session.get("user")
    if user:
        log_event_standalone(user.get("username", "unknown"), "logout")
    request.session.clear()
    return RedirectResponse("/")


@router.get("/api/me")
def me(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {
        "username": user["username"],
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "is_admin": bool(user.get("is_admin")),
        "auth_enabled": AUTH_ENABLED,
    }
