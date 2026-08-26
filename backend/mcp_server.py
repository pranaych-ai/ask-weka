"""MCP endpoint (HTTP transport) exposing the ask_weka tool.

Per WEKA policy, AI clients access data through MCP — never the database.
This is a minimal, dependency-free implementation of the MCP streamable-HTTP
transport (JSON-RPC 2.0 over POST, JSON responses). Authentication uses a
scoped, expiring bearer token issued from the admin portal (kind="mcp").

Upgrade path (documented in docs/INTEGRATIONS.md): replace bearer tokens with
a full OAuth authorization-server flow when IT provisions one.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .analysis import extract_sources
from .api_keys import key_domain, record_usage_detail, require_mcp_token
from .identity import IdentityError, verify_user_token
from .models import ApiKey
from .prompts import build_system_prompt
from .providers import get_provider

router = APIRouter()

PROTOCOL_VERSION = "2024-11-05"

ASK_TOOL = {
    "name": "ask_weka",
    "description": (
        "Ask the WEKA internal assistant a question about HR and IT policies, "
        "tools, and processes. Returns an answer grounded in the internal "
        "knowledge base, with cited sources."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask"},
            "user": {
                "type": "string",
                "description": (
                    "Optional: email/username of the end user this question is "
                    "asked on behalf of (recorded in the request log as an "
                    "UNVERIFIED claim)"
                ),
            },
            "user_token": {
                "type": "string",
                "description": (
                    "Optional: Okta-issued JWT for the end user, forwarded by "
                    "the calling app. Verified server-side; the verified "
                    "identity is recorded. Invalid tokens reject the request."
                ),
            },
        },
        "required": ["question"],
    },
}


def _rpc_result(id_, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _rpc_error(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}},
        status_code=status,
    )


async def _answer(question: str, domain: str = "") -> dict:
    provider = get_provider()
    chunks: list[str] = []
    async for chunk in provider.stream_chat(
        build_system_prompt(domain), [{"role": "user", "content": question}]
    ):
        chunks.append(chunk)
    answer = "".join(chunks)
    sources = extract_sources(answer)
    text = answer
    if sources:
        text += "\n\nCited sources:\n" + "\n".join(f"- {s}" for s in sources)
    return {"content": [{"type": "text", "text": text}], "isError": False}


@router.post("/mcp")
async def mcp_endpoint(request: Request, token: ApiKey = Depends(require_mcp_token)):
    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error", status=400)
    if isinstance(body, list):
        return _rpc_error(None, -32600, "Batch requests not supported", status=400)
    method = body.get("method")
    id_ = body.get("id")
    params = body.get("params") or {}

    # Notifications (no id) get 202 Accepted with no body per streamable HTTP.
    if id_ is None:
        return Response(status_code=202)

    if method == "initialize":
        return _rpc_result(id_, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ask-weka", "version": "1.0.0"},
        })
    if method == "ping":
        return _rpc_result(id_, {})
    if method == "tools/list":
        return _rpc_result(id_, {"tools": [ASK_TOOL]})
    if method == "tools/call":
        if params.get("name") != "ask_weka":
            return _rpc_error(id_, -32602, f"Unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        question = (args.get("question") or "").strip()
        on_behalf_of = (args.get("user") or "").strip()
        usage_id = getattr(token, "usage_id", None)
        user_verified = False
        user_token = (args.get("user_token") or "").strip()
        if user_token:
            try:
                on_behalf_of = verify_user_token(user_token)
                user_verified = True
            except IdentityError as e:
                # Spoofed/unverifiable identity claims are rejected outright,
                # with HTTP 401 so callers see an authentication failure.
                record_usage_detail(usage_id, question, on_behalf_of, 401)
                return _rpc_error(id_, -32001, str(e), status=401)
        if not question:
            record_usage_detail(usage_id, question, on_behalf_of, 400, user_verified)
            return _rpc_error(id_, -32602, "question is required")
        if len(question) > 4000:
            record_usage_detail(usage_id, question, on_behalf_of, 400, user_verified)
            return _rpc_error(id_, -32602, "question too long (max 4000 chars)")
        try:
            result = await _answer(question, key_domain(token))
            record_usage_detail(usage_id, question, on_behalf_of, 200, user_verified)
            return _rpc_result(id_, result)
        except (RuntimeError, ValueError) as e:
            record_usage_detail(usage_id, question, on_behalf_of, 503, user_verified)
            return _rpc_result(id_, {
                "content": [{"type": "text", "text": f"Model unavailable: {e}"}],
                "isError": True,
            })
        except Exception as e:
            record_usage_detail(usage_id, question, on_behalf_of, 502, user_verified)
            return _rpc_result(id_, {
                "content": [{"type": "text", "text": f"Error answering: {e}"}],
                "isError": True,
            })
    return _rpc_error(id_, -32601, f"Method not found: {method}")


@router.get("/mcp")
def mcp_get(token: ApiKey = Depends(require_mcp_token)):
    # SSE server->client stream is not used by this server.
    raise HTTPException(405, "Use POST with JSON-RPC 2.0 messages")
