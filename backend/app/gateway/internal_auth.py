"""Process-local authentication for Gateway internal callers.

The internal auth token is derived from a stable shared secret so that all
Gateway workers (uvicorn/gunicorn processes) accept the same token.  If
``AUTH_JWT_SECRET`` is set, it is used as the derivation key; otherwise a
process-local fallback is generated for single-process development mode.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from types import SimpleNamespace

from deerflow.runtime.user_context import DEFAULT_USER_ID

logger = logging.getLogger(__name__)

INTERNAL_AUTH_HEADER_NAME = "X-DeerFlow-Internal-Token"
_INTERNAL_AUTH_TOKEN: str | None = None


def _derive_internal_token() -> str:
    """Derive a stable internal auth token from the shared JWT secret.

    All workers that share the same JWT secret will compute the same token,
    which is required for multi-process deployments where the channel manager
    calls Gateway over HTTP and requests may be routed to any worker.

    Priority:
      1. ``get_auth_config().jwt_secret`` (auto-generated and persisted by the
         auth config layer, covers Docker Compose with multiple workers).
      2. ``AUTH_JWT_SECRET`` environment variable (explicit override).
      3. Process-local random fallback (single-process development only).
    """
    secret: str | None = None
    try:
        from app.gateway.auth.config import get_auth_config

        secret = get_auth_config().jwt_secret
    except Exception:
        logger.debug("Could not read JWT secret from auth config, falling back to env", exc_info=True)

    if not secret:
        secret = os.environ.get("AUTH_JWT_SECRET")

    if secret:
        return hmac.new(secret.encode(), b"deerflow-internal-token", hashlib.sha256).hexdigest()

    # Single-process development fallback.  This token changes on every process
    # restart, so it is NOT suitable for multi-worker deployments.
    logger.warning(
        "AUTH_JWT_SECRET is not set and no persisted auth secret is available; "
        "using a process-local internal auth token. Multi-worker Gateway "
        "deployments require a shared JWT secret so all workers accept the "
        "same internal token."
    )
    return secrets.token_urlsafe(32)


def get_internal_auth_token() -> str:
    """Return the cached internal auth token, computing it on first call."""
    global _INTERNAL_AUTH_TOKEN
    if _INTERNAL_AUTH_TOKEN is None:
        _INTERNAL_AUTH_TOKEN = _derive_internal_token()
    return _INTERNAL_AUTH_TOKEN


def create_internal_auth_headers() -> dict[str, str]:
    """Return headers that authenticate same-process Gateway internal calls."""
    return {INTERNAL_AUTH_HEADER_NAME: get_internal_auth_token()}


def is_valid_internal_auth_token(token: str | None) -> bool:
    """Return True when *token* matches the shared internal token."""
    return bool(token) and secrets.compare_digest(token, get_internal_auth_token())


def get_internal_user():
    """Return the synthetic user used for trusted internal channel calls."""
    return SimpleNamespace(id=DEFAULT_USER_ID, system_role="internal")
