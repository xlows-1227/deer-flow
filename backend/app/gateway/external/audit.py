"""Request IDs and metadata-only auditing for External API traffic."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_API_KEY_RE = re.compile(r"df[ak]_[A-Za-z0-9_-]+")
_MAX_REQUEST_BYTES = 256 * 1024


def resolve_request_id(request: Request) -> str:
    """Accept a bounded caller request id or generate a trusted replacement."""
    supplied = request.headers.get("X-Request-ID", "")
    if _REQUEST_ID_RE.fullmatch(supplied):
        return supplied
    return f"req_{uuid4().hex}"


def _client_ip_hash(request: Request) -> str | None:
    host = request.client.host if request.client is not None else None
    return hashlib.sha256(host.encode()).hexdigest() if host else None


def _safe_user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent", "")
    return _API_KEY_RE.sub("[redacted-api-key]", value)[:256] or None


class ExternalAuditMiddleware(BaseHTTPMiddleware):
    """Attach request metadata and persist sanitized dual-principal audits."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Audit managed external routes without recording request contents."""
        managed_path = request.url.path.startswith("/api/v1/external/") or request.url.path.startswith("/api/v1/api-keys/") or request.url.path.startswith("/api/v1/agents/")
        if not managed_path:
            return await call_next(request)

        request_id = resolve_request_id(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        content_length = request.headers.get("content-length")
        if content_length is not None and content_length.isdecimal() and int(content_length) > _MAX_REQUEST_BYTES:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": "Request body exceeds the 256 KB limit.",
                        "request_id": request_id,
                    }
                },
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("Unhandled error while serving managed External API request %s", request_id)
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "internal_error",
                            "message": "An internal error occurred.",
                            "request_id": request_id,
                        }
                    },
                )
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("Cache-Control", "no-store")

        repository = getattr(request.app.state, "external_audit_repo", None)
        if repository is not None:
            try:
                user = getattr(request.state, "user", None)
                route = request.scope.get("route")
                agent_authenticated = getattr(request.state, "auth_method", None) == "agent_api_key"
                credential_id = str(getattr(request.state, "agent_key_id", "")) or None if agent_authenticated else None
                external_actor_hash = hashlib.sha256(f"agent-key:{credential_id}".encode()).hexdigest() if credential_id is not None else None
                await repository.append(
                    {
                        "request_id": request_id,
                        "user_id": (str(user.id) if user is not None and not agent_authenticated else None),
                        "api_key_id": getattr(request.state, "api_key_id", None),
                        "owner_user_id": (str(getattr(request.state, "owner_user_id", "")) or None if agent_authenticated else None),
                        "agent_id": (str(getattr(request.state, "agent_id", "")) or None if agent_authenticated else None),
                        "credential_id": credential_id,
                        "external_actor_hash": external_actor_hash,
                        "source": "api" if agent_authenticated else None,
                        "action": f"{request.method.lower()}:{getattr(route, 'name', 'unknown')}",
                        "resource_type": getattr(request.state, "external_audit_resource_type", None),
                        "resource_id": getattr(request.state, "external_audit_resource_id", None),
                        "skill_name": getattr(request.state, "external_audit_skill_name", None),
                        "method": request.method,
                        "path_template": getattr(route, "path", request.url.path),
                        "status_code": response.status_code,
                        "client_ip_hash": _client_ip_hash(request),
                        "user_agent": _safe_user_agent(request),
                        "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
                    }
                )
            except Exception:
                logger.exception("Failed to write External API audit event")
        return response
