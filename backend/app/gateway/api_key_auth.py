"""Bearer API Key authentication for the versioned External API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.authz import AuthContext
from app.gateway.external.errors import ExternalAPIError
from app.gateway.external.service import APIKeyService
from deerflow.runtime.user_context import reset_current_user, set_current_user

EXTERNAL_API_PREFIX = "/api/v1/external/"


def _error_response(error: ExternalAPIError, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response(request_id=getattr(request.state, "request_id", None)),
    )


class ExternalAPIAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate External API requests before CSRF and session auth."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not request.url.path.startswith(EXTERNAL_API_PREFIX):
            return await call_next(request)

        repository = getattr(request.app.state, "api_key_repo", None)
        if repository is None:
            return _error_response(
                ExternalAPIError(
                    code="external_api_unavailable",
                    message="External API persistence is not available.",
                    status_code=503,
                ),
                request,
            )

        authorization = request.headers.get("Authorization", "")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            return _error_response(
                ExternalAPIError(code="missing_api_key", message="A Bearer API Key is required.", status_code=401),
                request,
            )

        try:
            key = await APIKeyService(repository).authenticate(credential)
        except ValueError:
            key = None
        if key is None:
            return _error_response(
                ExternalAPIError(code="invalid_api_key", message="The API Key is invalid or expired.", status_code=401),
                request,
            )

        from app.gateway.deps import get_local_provider

        user = await get_local_provider().get_user(key["user_id"])
        if user is None:
            return _error_response(
                ExternalAPIError(code="invalid_api_key", message="The API Key is invalid or expired.", status_code=401),
                request,
            )

        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=list(key.get("scopes") or []))
        request.state.auth_method = "api_key"
        request.state.api_key_id = key["id"]
        request.state.external_scopes = list(key.get("scopes") or [])
        request.state.allowed_skills = list(key.get("allowed_skills") or [])
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)
