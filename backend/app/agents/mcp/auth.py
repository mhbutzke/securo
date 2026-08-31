from __future__ import annotations

import time
import uuid
from typing import Optional

from jose import jwt

from app.agents.config import get_agent_settings


JWT_ISSUER = "securo-backend"
JWT_AUDIENCE = "securo-mcp"
JWT_ALGO = "HS256"


def mint_token(
    *,
    user_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    agent_id: Optional[uuid.UUID] = None,
    ttl_seconds: Optional[int] = None,
    external: bool = False,
    scopes: Optional[list[str]] = None,
    audience: str = JWT_AUDIENCE,
) -> str:
    """Mint an MCP JWT scoped to a (user, workspace) pair.

    `workspace_id` is recommended for every internal call so MCP tools
    operate within the right tenant. It's optional only because long-
    lived external tokens issued before the multi-workspace migration
    still verify — the MCP server falls back to the user's default
    workspace when the claim is absent.
    """
    s = get_agent_settings()
    if not s.mcp_jwt_secret:
        raise ValueError("MCP JWT secret is not configured")
    now = int(time.time())
    if audience != JWT_AUDIENCE:
        raise ValueError("Unsupported MCP audience")
    normalized_scopes = sorted(set(scopes or (["read"] if external else ["read", "write"])))
    if not set(normalized_scopes).issubset({"read", "write"}) or not normalized_scopes:
        raise ValueError("MCP scopes must be read and/or write")
    payload = {
        "sub": str(user_id),
        "iss": JWT_ISSUER,
        "aud": audience,
        "iat": now,
        "exp": now + (ttl_seconds or s.mcp_jwt_ttl_seconds),
        "jti": str(uuid.uuid4()),
        "scopes": normalized_scopes,
    }
    if workspace_id:
        payload["ws_id"] = str(workspace_id)
    if conversation_id:
        payload["conv_id"] = str(conversation_id)
    if agent_id:
        payload["agent_id"] = str(agent_id)
    if external:
        payload["ext"] = True
    return jwt.encode(payload, s.mcp_jwt_secret, algorithm=JWT_ALGO)
