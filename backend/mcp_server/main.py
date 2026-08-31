"""MCP server FastAPI app. JSON-RPC 2.0 over HTTP POST /mcp.

Exposes Securo's built-in tools (read-only + propose-mutations) over the
Model Context Protocol. Runs as a separate container; gated by the
`agents` profile in docker-compose.
"""
from __future__ import annotations

import logging
import hashlib
import json
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.database import async_session_maker
from mcp_server import tools as _tools_pkg  # noqa: F401  triggers tool registration
from mcp_server.auth import verify_request
from mcp_server.registry import REGISTRY, call_tool, list_tools
from mcp_server.tools._helpers import resolve_workspace_id
from sqlalchemy import select
from app.agents.models.mcp_token import McpTokenRevocation
from app.agents.models.mcp_audit import McpToolAudit

logger = logging.getLogger(__name__)

app = FastAPI(title="Securo MCP Server", openapi_url=None, docs_url=None)


SERVER_INFO = {
    "name": "securo-builtin",
    "version": "0.1.0",
}
PROTOCOL_VERSION = "2024-11-05"


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


@app.get("/health")
async def health():
    return {"status": "ok", "tools": len(REGISTRY)}


@app.post("/mcp")
async def mcp(request: Request) -> JSONResponse:
    # Auth first — never accept unauthenticated calls.
    try:
        ctx = verify_request(request)
    except Exception as exc:  # HTTPException from verify_request
        status_code = getattr(exc, "status_code", 401)
        detail = getattr(exc, "detail", str(exc))
        return JSONResponse(
            status_code=status_code,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": str(detail)}},
        )

    if ctx.jti:
        async with async_session_maker() as session:
            revoked = await session.scalar(select(McpTokenRevocation.id).where(McpTokenRevocation.jti == ctx.jti))
        if revoked is not None:
            return JSONResponse(status_code=401, content={"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "token revoked"}})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_err(None, -32700, "parse error"))

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_err(None, -32600, "invalid request"))

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return JSONResponse(status_code=400, content=_err(req_id, -32600, "invalid request"))

    if method == "initialize":
        return JSONResponse(
            content=_ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })
        )

    if method == "tools/list":
        return JSONResponse(content=_ok(req_id, {"tools": list_tools(ctx)}))

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return JSONResponse(content=_err(req_id, -32602, "tools/call requires 'name'"))
        try:
            async with async_session_maker() as session:
                started = time.monotonic()
                audit_workspace_id = await resolve_workspace_id(session, ctx)
                result = await call_tool(session, ctx, name, arguments)
                request_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()
                session.add(McpToolAudit(
                    user_id=ctx.user_id,
                    workspace_id=audit_workspace_id,
                    jti=ctx.jti,
                    tool_name=name,
                    access=REGISTRY[name].access if name in REGISTRY else "read",
                    request_hash=request_hash,
                    result_status="ok",
                    duration_ms=int((time.monotonic() - started) * 1000),
                ))
                await session.commit()
            # MCP wraps tool output in `content` blocks. Use the structured
            # variant — many clients (and our own runtime) prefer JSON.
            return JSONResponse(content=_ok(req_id, {
                "content": [{"type": "text", "text": _safe_json(result)}],
                "structuredContent": result,
                "isError": False,
            }))
        except KeyError as exc:
            return JSONResponse(content=_err(req_id, -32601, str(exc)))
        except Exception as exc:  # noqa: BLE001
            try:
                async with async_session_maker() as audit_session:
                    audit_workspace_id = await resolve_workspace_id(audit_session, ctx)
                    request_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()
                    audit_session.add(McpToolAudit(
                        user_id=ctx.user_id,
                        workspace_id=audit_workspace_id,
                        jti=ctx.jti,
                        tool_name=name if isinstance(name, str) else "unknown",
                        access=REGISTRY[name].access if name in REGISTRY else "read",
                        request_hash=request_hash,
                        result_status="error",
                        duration_ms=0,
                    ))
                    await audit_session.commit()
            except Exception:
                logger.exception("MCP audit write failed")
            logger.exception("MCP tool failure: %s", name)
            return JSONResponse(content=_ok(req_id, {
                "content": [{"type": "text", "text": f"Tool error: {exc}"}],
                "isError": True,
            }))

    return JSONResponse(content=_err(req_id, -32601, f"unknown method: {method}"))


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)
