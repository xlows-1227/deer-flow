"""Shared helpers for invite-code registration tests."""

from __future__ import annotations

import asyncio


def wire_invite_code_repo(app) -> None:
    """Attach InviteCodeRepository to a FastAPI app for auth contract tests."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.invite_code import InviteCodeRepository

    sf = get_session_factory()
    if sf is not None:
        app.state.invite_code_repo = InviteCodeRepository(sf)


def get_unused_invite_code() -> str:
    """Return the first unused invite code from the current test database."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.invite_code import InviteCodeRepository

    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("get_unused_invite_code() requires an initialized persistence engine")
    code = asyncio.run(InviteCodeRepository(sf).get_unused_code())
    if code is None:
        raise RuntimeError("No unused invite codes available in test database")
    return code


def register_payload(*, email: str, password: str = "Tr0ub4dor3a", invite_code: str | None = None) -> dict[str, str]:
    """Build a valid /register JSON payload for tests."""
    return {
        "email": email,
        "password": password,
        "invite_code": invite_code if invite_code is not None else get_unused_invite_code(),
    }
