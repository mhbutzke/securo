"""Tool registry for the MCP server.

Each tool is a Python coroutine registered via the @tool decorator. The
registry holds (name → ToolSpec) for /mcp's `tools/list` and `tools/call`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.auth import CallContext


ToolHandler = Callable[..., Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)
    handler: ToolHandler
    # Optional. When True, the tool produces a preview (no DB writes); the
    # frontend asks the user to confirm before applying. Drives UI hints.
    is_proposal: bool = False
    access: str = "read"
    tags: list[str] = field(default_factory=list)


REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    *,
    description: str,
    parameters: dict[str, Any],
    is_proposal: bool = False,
    access: str | None = None,
    tags: list[str] | None = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator. The handler must be an async function with signature
    `async def handler(session: AsyncSession, ctx: CallContext, **kwargs)`.
    """
    def deco(fn: ToolHandler) -> ToolHandler:
        if name in REGISTRY:
            raise RuntimeError(f"duplicate tool registration: {name}")
        # Proposal tools are read-capable previews; their optional ``apply``
        # path is gated separately at call time by the write scope.
        resolved_access = access or "read"
        if resolved_access not in {"read", "write"}:
            raise ValueError("MCP tool access must be read or write")
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
            is_proposal=is_proposal,
            access=resolved_access,
            tags=list(tags or []),
        )
        return fn
    return deco


def list_tools(ctx: CallContext | None = None) -> list[dict[str, Any]]:
    """MCP-compatible tool list payload."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "inputSchema": s.parameters,
            "_securo": {"is_proposal": s.is_proposal, "access": s.access, "tags": s.tags},
        }
        for s in REGISTRY.values()
        if ctx is None or not ctx.external or ctx.has_scope(s.access)
    ]


async def call_tool(
    session: AsyncSession,
    ctx: CallContext,
    name: str,
    arguments: dict[str, Any] | None,
) -> Any:
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown tool: {name}")
    if ctx.external and not ctx.has_scope(spec.access):
        raise PermissionError("MCP token is read-only")
    if ctx.external and spec.is_proposal and arguments and arguments.get("apply") is True and not ctx.has_scope("write"):
        raise PermissionError("MCP token is read-only")
    return await spec.handler(session=session, ctx=ctx, **(arguments or {}))
