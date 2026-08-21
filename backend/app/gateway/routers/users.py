"""User listing endpoints for share-picker dialogs and admin views.

The authenticated session is required because user listings expose
personally-identifiable information (email addresses).  The route is
intentionally narrow — it only surfaces the fields the frontend share
dialog actually needs (id + email + system_role), never hashes or tokens.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user_from_request, get_local_provider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["users"])


class UserListItemResponse(BaseModel):
    """Compact user view used by sharee pickers and ownership chips."""

    id: str = Field(..., description="User UUID")
    email: str = Field(..., description="Unique email address")
    system_role: str = Field(..., description="Either 'admin' or 'user'")


class UsersListResponse(BaseModel):
    users: list[UserListItemResponse]


@router.get(
    "/users",
    response_model=UsersListResponse,
    summary="List Registered Users",
    description=(
        "Return all registered users ordered by email.  Requires an "
        "authenticated session; used by the custom-skill share dialog to "
        "populate the left-hand candidate list."
    ),
)
async def list_users(
    request: Request,
    _current_user=Depends(get_current_user_from_request),
) -> UsersListResponse:
    try:
        provider = get_local_provider()
        users = await provider.repository.list_users()
        return UsersListResponse(
            users=[
                UserListItemResponse(
                    id=str(u.id),
                    email=str(u.email),
                    system_role=u.system_role,
                )
                for u in users
            ]
        )
    except Exception as exc:  # noqa: BLE001 — request boundary: log and surface.
        logger.exception("Failed to list users")
        raise HTTPException(status_code=500, detail=f"Failed to list users: {exc}") from exc
