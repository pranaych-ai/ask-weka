"""End-user identity verification for the service API and MCP endpoint.

Calling apps can assert who is asking in two ways:
- ``user`` (plain string): recorded in the usage log but flagged UNVERIFIED —
  it is caller-controlled and must never be trusted for access decisions.
- ``user_token`` (Okta-issued JWT forwarded by the app): verified against the
  Okta issuer's JWKS. Only a token that passes signature + expiry + issuer
  checks yields a VERIFIED identity in the audit trail. Invalid or
  unverifiable tokens are rejected outright (never silently downgraded).

When per-user KB permissions exist, answer scoping should key off the
verified identity produced here — never the plain ``user`` string.
"""

import os
import time

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from .auth import AUTH_ENABLED, OKTA_CLIENT_ID, OKTA_ISSUER

# Audiences this service accepts in a forwarded user token. Configure
# OKTA_USER_TOKEN_AUDIENCE (comma-separated) for access tokens minted for a
# dedicated API resource; by default we accept ID tokens for this app's own
# Okta client and access tokens for the org default authorization server.
_AUD_ENV = os.environ.get("OKTA_USER_TOKEN_AUDIENCE", "")


def _allowed_audiences() -> set[str]:
    if _AUD_ENV.strip():
        return {a.strip() for a in _AUD_ENV.split(",") if a.strip()}
    return {a for a in (OKTA_CLIENT_ID, "api://default") if a}


class IdentityError(ValueError):
    """A user identity token was presented but could not be verified."""


_JWKS_TTL_S = 3600
_jwks_cache: dict = {"keys": None, "fetched": 0.0}


def _jwks():
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["fetched"] > _JWKS_TTL_S:
        meta = httpx.get(
            f"{OKTA_ISSUER}/.well-known/openid-configuration", timeout=10
        ).json()
        jwks = httpx.get(meta["jwks_uri"], timeout=10).json()
        _jwks_cache["keys"] = JsonWebKey.import_key_set(jwks)
        _jwks_cache["fetched"] = now
    return _jwks_cache["keys"]


def verify_user_token(token: str) -> str:
    """Verify an Okta-issued JWT forwarded by a calling app and return the
    end user's identity (email/username). Raises IdentityError if the token
    cannot be verified — callers must reject the request, not fall back to
    treating the claim as unverified."""
    if not AUTH_ENABLED:
        raise IdentityError(
            "user_token cannot be verified: Okta is not configured on this "
            "server. Omit user_token, or pass the plain 'user' field "
            "(recorded as unverified)."
        )
    try:
        claims = JsonWebToken(["RS256"]).decode(token, _jwks())
        claims.validate()  # exp / nbf / iat
    except (JoseError, ValueError, httpx.HTTPError) as e:
        raise IdentityError(f"Invalid user token: {e}")
    if str(claims.get("iss", "")).rstrip("/") != OKTA_ISSUER:
        raise IdentityError("Invalid user token: issuer mismatch")
    aud = claims.get("aud")
    auds = {str(a) for a in (aud if isinstance(aud, list) else [aud]) if a}
    if not (auds & _allowed_audiences()):
        # A token minted by the same Okta org for a *different* app must not
        # be accepted as a verified identity for this service.
        raise IdentityError("Invalid user token: audience mismatch")
    ident = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("sub")
        or ""
    )
    if not ident:
        raise IdentityError("Invalid user token: no identity claim (email/sub)")
    return str(ident)
