from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from deerflow.config.app_config import pop_current_app_config, push_current_app_config
from deerflow.config.effective_config import build_effective_app_config
from deerflow.runtime.user_context import get_effective_user_id


class EffectiveConfigMiddleware(BaseHTTPMiddleware):
    """Merge per-user custom models into AppConfig for the current request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        user_id = get_effective_user_id()
        if user_id == "default":
            return await call_next(request)

        merged = await build_effective_app_config(user_id=user_id)
        push_current_app_config(merged)
        try:
            return await call_next(request)
        finally:
            pop_current_app_config()
