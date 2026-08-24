"""Versioned public API for other internal tools, authenticated by API key."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .analysis import extract_sources
from .api_keys import require_api_key
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


@router.post("/ask")
async def v1_ask(req: AskRequest, key: ApiKey = Depends(require_api_key)):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Empty question")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"Question too long (max {MAX_QUESTION_CHARS} chars)")
    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        raise HTTPException(503, str(e))
    system = build_system_prompt()
    chunks: list[str] = []
    try:
        async for chunk in provider.stream_chat(
            system, [{"role": "user", "content": question}]
        ):
            chunks.append(chunk)
    except Exception as e:
        raise HTTPException(502, f"Model error: {e}")
    answer = "".join(chunks)
    return {"answer": answer, "sources": extract_sources(answer)}
