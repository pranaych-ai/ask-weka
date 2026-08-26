"""API key & MCP token lifecycle (admin-only) plus key authentication helpers.

Keys are shown once at creation and stored only as SHA-256 hashes.
Rate limiting is a per-key fixed window (requests per minute), in-memory —
adequate for a single-process deployment; move to Redis if scaled out.
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .admin import admin_audited
from .audit import log_event
from .db import SessionLocal, get_db
from .models import ApiKey, ApiKeyUsage

router = APIRouter(prefix="/api/admin/keys", dependencies=[Depends(admin_audited)])

KINDS = ("api", "mcp")
DOMAINS = ("HR", "IT")
RATE_LIMIT_MAX = 600
MCP_TOKEN_MAX_DAYS = 90
USAGE_QUESTION_MAX = 500


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- authentication (used by public API and MCP endpoint) ----------

# key_id -> (window_start_epoch_minute, count)
_rate: dict[str, tuple[int, int]] = {}


def _check_rate(key: ApiKey) -> None:
    minute = int(time.time() // 60)
    window, count = _rate.get(key.id, (minute, 0))
    if window != minute:
        window, count = minute, 0
    if count >= key.rate_limit_per_min:
        raise HTTPException(429, "Rate limit exceeded — try again in a minute")
    _rate[key.id] = (window, count + 1)


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def authenticate_key(request: Request, kind: str) -> ApiKey:
    """Validate the presented credential: exists, right kind, not revoked or
    expired, and within its rate limit. Records usage. Raises 401/429."""
    token = _bearer_token(request)
    if not token:
        raise HTTPException(401, "Missing API credential (Authorization: Bearer ...)")
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.key_hash == _hash(token)).first()
        if not key or key.kind != kind or key.revoked:
            raise HTTPException(401, "Invalid or revoked credential")
        if not key.enabled:
            raise HTTPException(401, "Credential is disabled")
        if key.expires_at is not None:
            exp = key.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < _now():
                raise HTTPException(401, "Credential expired")
        _check_rate(key)
        key.usage_count += 1
        key.last_used_at = _now()
        usage = ApiKeyUsage(key_id=key.id, endpoint=request.url.path, status_code=200)
        db.add(usage)
        db.commit()
        db.refresh(key)
        db.expunge(key)
        key.usage_id = usage.id  # transient: lets the handler attach detail
        return key
    finally:
        db.close()


def record_usage_detail(
    usage_id: Optional[str],
    question: str = "",
    on_behalf_of: str = "",
    status_code: int = 200,
    user_verified: bool = False,
) -> None:
    """Attach the asked question / end-user context to the usage row created
    during authentication, so every service request shows who asked what
    through which app."""
    if not usage_id:
        return
    db = SessionLocal()
    try:
        usage = db.get(ApiKeyUsage, usage_id)
        if usage:
            usage.question = question[:USAGE_QUESTION_MAX]
            usage.on_behalf_of = on_behalf_of[:120]
            usage.user_verified = user_verified
            usage.status_code = status_code
            db.commit()
    finally:
        db.close()


def key_domain(key: ApiKey) -> str:
    """The single KB domain a key is restricted to, or "" for full access.

    Deduplicates defensively so a malformed allowlist like "HR,HR" can never
    widen access beyond the single listed domain."""
    domains = {d for d in key.allowed_domains.split(",") if d}
    if not domains or domains == set(DOMAINS):
        return ""
    return sorted(domains)[0] if len(domains) == 1 else ""


def require_api_key(request: Request) -> ApiKey:
    return authenticate_key(request, "api")


def require_mcp_token(request: Request) -> ApiKey:
    return authenticate_key(request, "mcp")


# ---------- admin lifecycle endpoints ----------


def _key_out(k: ApiKey, usage_24h: Optional[int] = None) -> dict:
    return {
        "id": k.id,
        "kind": k.kind,
        "name": k.name,
        "owner": k.owner,
        "scope": k.scope,
        "allowed_domains": k.allowed_domains,
        "enabled": k.enabled,
        "prefix": k.prefix,
        "rate_limit_per_min": k.rate_limit_per_min,
        "revoked": k.revoked,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "usage_count": k.usage_count,
        "usage_24h": usage_24h,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "created_by": k.created_by,
        "created_at": k.created_at.isoformat(),
    }


@router.get("")
def keys_list(kind: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ApiKey)
    if kind:
        q = q.filter(ApiKey.kind == kind)
    keys = q.order_by(ApiKey.created_at.desc()).all()
    since = _now() - timedelta(hours=24)
    counts = dict(
        db.query(ApiKeyUsage.key_id, func.count(ApiKeyUsage.id))
        .filter(ApiKeyUsage.created_at >= since)
        .group_by(ApiKeyUsage.key_id)
        .all()
    )
    return [_key_out(k, counts.get(k.id, 0)) for k in keys]


class KeyCreate(BaseModel):
    name: str
    owner: str = ""
    kind: str = "api"
    scope: str = "ask"
    allowed_domains: str = ""  # "" = full KB, or comma list of HR/IT
    rate_limit_per_min: int = 30
    expires_days: Optional[int] = None  # required for kind="mcp"


@router.post("")
def key_create(
    req: KeyCreate, db: Session = Depends(get_db), user: dict = Depends(admin_audited)
):
    if not req.name.strip():
        raise HTTPException(400, "Name required")
    if req.kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {KINDS}")
    if req.scope != "ask":
        raise HTTPException(400, "Only the 'ask' scope is supported")
    domains = sorted({d.strip().upper() for d in req.allowed_domains.split(",") if d.strip()})
    if any(d not in DOMAINS for d in domains):
        raise HTTPException(400, f"allowed_domains entries must be one of {DOMAINS}")
    if not (1 <= req.rate_limit_per_min <= RATE_LIMIT_MAX):
        raise HTTPException(400, f"rate_limit_per_min must be 1..{RATE_LIMIT_MAX}")
    expires_at = None
    if req.kind == "mcp":
        days = req.expires_days or 30
        if not (1 <= days <= MCP_TOKEN_MAX_DAYS):
            raise HTTPException(400, f"expires_days must be 1..{MCP_TOKEN_MAX_DAYS}")
        expires_at = _now() + timedelta(days=days)
    elif req.expires_days:
        expires_at = _now() + timedelta(days=req.expires_days)

    plaintext = ("aw_mcp_" if req.kind == "mcp" else "aw_") + secrets.token_urlsafe(32)
    key = ApiKey(
        kind=req.kind,
        name=req.name.strip(),
        owner=req.owner.strip(),
        scope=req.scope,
        allowed_domains=",".join(domains),
        key_hash=_hash(plaintext),
        prefix=plaintext[:10],
        rate_limit_per_min=req.rate_limit_per_min,
        expires_at=expires_at,
        created_by=user["username"],
    )
    db.add(key)
    db.flush()
    log_event(db, user["username"], f"apikey.create", f"id={key.id} kind={key.kind}")
    db.commit()
    out = _key_out(key)
    out["key"] = plaintext  # shown exactly once
    return out


@router.post("/{key_id}/revoke")
def key_revoke(
    key_id: str, db: Session = Depends(get_db), user: dict = Depends(admin_audited)
):
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "Key not found")
    key.revoked = True
    log_event(db, user["username"], "apikey.revoke", f"id={key.id} kind={key.kind}")
    db.commit()
    return _key_out(key)


class KeyEnabledUpdate(BaseModel):
    enabled: bool


@router.post("/{key_id}/enabled")
def key_set_enabled(
    key_id: str,
    req: KeyEnabledUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    """Reversible per-app off-switch (unlike revoke, which is permanent)."""
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "Key not found")
    if key.revoked:
        raise HTTPException(400, "Key is revoked; issue a new one instead")
    key.enabled = req.enabled
    action = "apikey.enable" if req.enabled else "apikey.disable"
    log_event(db, user["username"], action, f"id={key.id} kind={key.kind}")
    db.commit()
    return _key_out(key)


@router.get("/{key_id}/usage")
def key_usage(key_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """Recent requests for one key: who asked what through this app."""
    if not db.get(ApiKey, key_id):
        raise HTTPException(404, "Key not found")
    limit = max(1, min(limit, 200))
    rows = (
        db.query(ApiKeyUsage)
        .filter(ApiKeyUsage.key_id == key_id)
        .order_by(ApiKeyUsage.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": u.id,
            "endpoint": u.endpoint,
            "question": u.question,
            "on_behalf_of": u.on_behalf_of,
            "user_verified": u.user_verified,
            "status_code": u.status_code,
            "created_at": u.created_at.isoformat(),
        }
        for u in rows
    ]
