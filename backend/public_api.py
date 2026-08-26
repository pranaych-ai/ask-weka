"""Versioned public API for other internal tools, authenticated by API key."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from typing import Optional

from .analysis import extract_sources
from .api_keys import key_domain, record_usage_detail, require_api_key
from .identity import IdentityError, verify_user_token
from .models import ApiKey
from .prompts import build_system_prompt
from .providers import get_provider

router = APIRouter(prefix="/api/v1")

MAX_QUESTION_CHARS = 4000


@router.get("/health")
def v1_health(key: ApiKey = Depends(require_api_key)):
    return {"ok": True, "key": key.name}


class AskRequest(BaseModel):
    question: str
    # Optional end-user context from the calling app (email/username). This is
    # caller-controlled: it is recorded in the request log but flagged as
    # UNVERIFIED — it is never trusted for access decisions.
    user: Optional[str] = None
    # Optional Okta-issued JWT for the end user, forwarded by the calling app.
    # If present it is verified (signature, expiry, issuer) and the verified
    # identity is recorded; an invalid token rejects the whole request.
    user_token: Optional[str] = None


@router.post("/ask")
async def v1_ask(req: AskRequest, key: ApiKey = Depends(require_api_key)):
    usage_id = getattr(key, "usage_id", None)
    question = req.question.strip()
    on_behalf_of = (req.user or "").strip()
    user_verified = False
    if req.user_token:
        try:
            on_behalf_of = verify_user_token(req.user_token.strip())
            user_verified = True
        except IdentityError as e:
            # Spoofed/unverifiable identity claims are rejected, not downgraded.
            record_usage_detail(usage_id, question, on_behalf_of, 401)
            raise HTTPException(401, str(e))
    if not question:
        record_usage_detail(usage_id, "", on_behalf_of, 400, user_verified)
        raise HTTPException(400, "Empty question")
    if len(question) > MAX_QUESTION_CHARS:
        record_usage_detail(usage_id, question, on_behalf_of, 400, user_verified)
        raise HTTPException(400, f"Question too long (max {MAX_QUESTION_CHARS} chars)")
    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        record_usage_detail(usage_id, question, on_behalf_of, 503, user_verified)
        raise HTTPException(503, str(e))
    # Keys can be scoped to one KB domain (HR or IT); "" = full internal KB.
    system = build_system_prompt(key_domain(key))
    chunks: list[str] = []
    try:
        async for chunk in provider.stream_chat(
            system, [{"role": "user", "content": question}]
        ):
            chunks.append(chunk)
    except Exception as e:
        record_usage_detail(usage_id, question, on_behalf_of, 502, user_verified)
        raise HTTPException(502, f"Model error: {e}")
    answer = "".join(chunks)
    record_usage_detail(usage_id, question, on_behalf_of, 200, user_verified)
    return {"answer": answer, "sources": extract_sources(answer)}
