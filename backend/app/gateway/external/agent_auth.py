"""Bearer authentication for stable published-Agent API routes."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.authz import AuthContext
from deerflow.runtime.user_context import reset_current_user, set_current_user

AGENT_API_PREFIX = "/api/v1/agents/"
_AGENT_PATH_RE = re.compile(r"^/api/v1/agents/([^/]+)(?:/|$)")


@dataclass(frozen=True)
class PublishedAgentPrincipal:
    """Synthetic owner principal used only for tenant-scoped persistence."""

    id: str


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


class AgentAPIAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate Agent Keys before CSRF and browser-session auth."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Authenticate Agent API routes and establish trusted request state."""
        match = _AGENT_PATH_RE.match(request.url.path)
        if match is None:
            return await call_next(request)
        requested_agent_id = match.group(1)
        # The route path is a controlled resource identifier. Attach it before
        # credential validation so metadata-only failure audits can still be
        # visible to the target owner without changing the external response.
        request.state.agent_id = requested_agent_id
        request.state.external_audit_resource_type = "agent"
        request.state.external_audit_resource_id = requested_agent_id

        key_repo = getattr(request.app.state, "agent_api_key_repo", None)
        agent_repo = getattr(request.app.state, "published_agent_repo", None)
        if key_repo is None or agent_repo is None:
            return _error(503, "agent_api_unavailable", "Published Agent API persistence is unavailable.")

        owner_user_id = await agent_repo.get_owner(requested_agent_id)
        if owner_user_id is not None:
            request.state.owner_user_id = str(owner_user_id)

        scheme, _, credential = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not credential:
            return _error(401, "missing_agent_key", "A Bearer Agent Key is required.")
        try:
            key = await key_repo.verify(credential)
        except ValueError:
            key = None
        if key is None:
            return _error(401, "invalid_agent_key", "The Agent Key is invalid or expired.")
        if key["agent_id"] != requested_agent_id:
            return _error(404, "agent_not_found", "Published Agent not found.")
        # Only expose the credential identifier to audit after it is proven to
        # belong to the requested Agent. Otherwise a cross-Agent failure would
        # leak another tenant's stable Key ID into the target owner's console.
        request.state.agent_key_id = key["id"]

        if owner_user_id is None:
            return _error(404, "agent_not_found", "Published Agent not found.")

        await key_repo.touch_last_used(key["id"])
        principal = PublishedAgentPrincipal(id=str(owner_user_id))
        request.state.user = principal
        request.state.auth = AuthContext(user=principal, permissions=[])
        request.state.auth_method = "agent_api_key"
        request.state.agent_key = key
        token = set_current_user(principal)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)


__all__ = ["AGENT_API_PREFIX", "AgentAPIAuthMiddleware", "PublishedAgentPrincipal"]
