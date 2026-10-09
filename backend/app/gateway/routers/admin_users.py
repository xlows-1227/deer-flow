"""Admin user-management endpoints: list accounts, reset password, soft delete.

All routes require an authenticated admin session.  Deletion is logical:
the ``users`` row survives so historical references (skill shares, thread
ownership) stay resolvable, but the account can no longer authenticate
and is excluded from user listings and share pickers.

The password applied by the reset action is configured through the
``AUTH_USER_RESET_PASSWORD`` environment variable (see ``.env.example``);
``GET /reset-password-config`` returns it so the admin UI can show the
exact value in its confirmation dialog.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.auth import LDAP_PROVIDER_TAG
from app.gateway.auth.config import get_auth_config
from app.gateway.auth.models import User
from app.gateway.auth.password import hash_password_async
from app.gateway.deps import get_current_user_from_request, get_local_provider, get_skill_share_repo
from app.gateway.routers.auth import _normalize_ldap_account_name
from app.gateway.routers.skills import _transfer_custom_skill_ownership

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class AdminUserItemResponse(BaseModel):
    """Compact account view for the admin user-management list."""

    id: str = Field(..., description="User UUID")
    email: str = Field(..., description="Account email")
    system_role: str = Field(..., description="Either 'admin' or 'user'")
    oauth_id: str | None = Field(None, description="SAM/domain account (LDAP oauth_id)")


class AdminUsersListResponse(BaseModel):
    users: list[AdminUserItemResponse]
    total: int = Field(..., description="Total active users matching the search filter")
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Page size used for this response")


class ResetPasswordConfigResponse(BaseModel):
    value: str = Field(..., description="Password applied by the reset-password action")


class UpdateUserOauthIdRequest(BaseModel):
    """Request body for admin updating a user's SAM account."""

    oauth_id: str | None = Field(None, description="New sAMAccountName; null/empty to clear")


class AdminUserActionResponse(BaseModel):
    id: str
    email: str


async def _require_admin_user(
    user: User = Depends(get_current_user_from_request),
) -> User:
    if user.system_role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


@router.get(
    "",
    response_model=AdminUsersListResponse,
    summary="List User Accounts (admin)",
    description="Return active (non-deleted) accounts ordered by email, with optional email search and pagination. Admin only.",
)
async def list_admin_users(
    admin: User = Depends(_require_admin_user),
    search: str | None = Query(default=None, description="Email substring filter (case-insensitive ilike)"),
    page: int = Query(default=1, ge=1, description="Page number, 1-indexed"),
    page_size: int = Query(default=20, ge=1, le=200, description="Page size, 1-200"),
) -> AdminUsersListResponse:
    provider = get_local_provider()
    users, total = await provider.repository.list_users_paginated(
        search=search,
        page=page,
        page_size=page_size,
    )
    return AdminUsersListResponse(
        users=[AdminUserItemResponse(id=str(u.id), email=str(u.email), system_role=u.system_role, oauth_id=u.oauth_id) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/reset-password-config",
    response_model=ResetPasswordConfigResponse,
    summary="Get Configured Reset Password (admin)",
    description=("Return the password that the reset-password action applies, so the admin UI can show the exact value in its confirmation dialog. Configured via AUTH_USER_RESET_PASSWORD in .env. Admin only."),
)
async def get_reset_password_config(
    admin: User = Depends(_require_admin_user),
) -> ResetPasswordConfigResponse:
    return ResetPasswordConfigResponse(value=get_auth_config().user_reset_password)


@router.post(
    "/{user_id}/reset-password",
    response_model=AdminUserActionResponse,
    summary="Reset User Password (admin)",
    description=("Set the user's password to the configured reset value (AUTH_USER_RESET_PASSWORD) and revoke the user's outstanding sessions. Admin only."),
)
async def reset_user_password(
    user_id: str,
    admin: User = Depends(_require_admin_user),
) -> AdminUserActionResponse:
    provider = get_local_provider()
    user = await provider.repository.get_user_by_id(user_id)
    if user is None or user.deleted:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = await hash_password_async(get_auth_config().user_reset_password)
    # Bump token_version so outstanding JWTs are revoked immediately.
    user.token_version = user.token_version + 1
    await provider.repository.update_user(user)
    logger.info("Admin %s reset password for user %s", admin.email, user.email)
    return AdminUserActionResponse(id=str(user.id), email=str(user.email))


@router.patch(
    "/{user_id}/oauth-id",
    response_model=AdminUserActionResponse,
    summary="Update User SAM Account (admin)",
    description=(
        "Set the user's SAM (domain) account stored in ``oauth_id``. "
        "The value is normalised to a bare sAMAccountName. Uniqueness is "
        "enforced — another user already linked to the same account is "
        "rejected with 409. Admin only."
    ),
)
async def update_user_oauth_id(
    user_id: str,
    body: UpdateUserOauthIdRequest,
    admin: User = Depends(_require_admin_user),
) -> AdminUserActionResponse:
    provider = get_local_provider()
    user = await provider.repository.get_user_by_id(user_id)
    if user is None or user.deleted:
        raise HTTPException(status_code=404, detail="User not found")

    if body.oauth_id is not None and body.oauth_id.strip():
        new_id = _normalize_ldap_account_name(body.oauth_id)
        existing = await provider.get_user_by_oauth(LDAP_PROVIDER_TAG, new_id)
        if existing is not None and str(existing.id) != str(user.id):
            raise HTTPException(
                status_code=409,
                detail=f"域账号 {new_id} 已绑定其他用户（{existing.email}）",
            )
        user.oauth_provider = LDAP_PROVIDER_TAG
        user.oauth_id = new_id
    else:
        user.oauth_id = None
        user.oauth_provider = None

    await provider.repository.update_user(user)
    logger.info("Admin %s updated SAM account for user %s to %s", admin.email, user.email, user.oauth_id or "(cleared)")
    return AdminUserActionResponse(id=str(user.id), email=str(user.email))


@router.delete(
    "/{user_id}",
    response_model=AdminUserActionResponse,
    summary="Delete User Account (admin, logical)",
    description=(
        "Soft-delete the account: it can no longer log in, its sessions are "
        "revoked, and it disappears from user listings and skill-share "
        "pickers. The row itself is kept for referential integrity. Any "
        "custom skills owned by the deleted account are reassigned to the "
        "first active admin so they are not orphaned. Admins cannot delete "
        "their own account. Admin only."
    ),
)
async def delete_user(
    user_id: str,
    request: Request,
    admin: User = Depends(_require_admin_user),
) -> AdminUserActionResponse:
    if user_id.lower() == str(admin.id).lower():
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    provider = get_local_provider()
    user = await provider.repository.get_user_by_id(user_id)
    if user is None or user.deleted:
        raise HTTPException(status_code=404, detail="User not found")

    deleted = await provider.repository.soft_delete_user(user_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Reassign the deleted user's custom skills and share grants to the
    # first remaining active admin so they are not orphaned. The self-delete
    # guard above guarantees ``admin`` is a different user from the deleted
    # one, so at least one admin (``admin``) survives to receive the
    # transfer. ``list_users`` is ordered by email asc and excludes
    # soft-deleted rows, so the picked receiver is deterministic.
    remaining_users = await provider.repository.list_users()
    first_admin = next((u for u in remaining_users if u.system_role == "admin"), None)
    if first_admin is not None and first_admin.id != deleted.id:
        try:
            share_repo = get_skill_share_repo(request)
        except Exception:
            logger.warning(
                "SkillShareRepository unavailable during delete of %s; skipping share transfer",
                deleted.email,
                exc_info=True,
            )
            share_repo = None
        transferred = await _transfer_custom_skill_ownership(
            from_user_id=str(deleted.id),
            to_user_id=str(first_admin.id),
            share_repo=share_repo,
        )
        logger.info(
            "Transferred %d skill(s) and %d share grant(s) from %s to admin %s",
            transferred["skills_transferred"],
            transferred["shares_transferred"],
            deleted.email,
            first_admin.email,
        )

    logger.info("Admin %s soft-deleted user %s (%s)", admin.email, deleted.email, deleted.id)
    return AdminUserActionResponse(id=str(deleted.id), email=str(deleted.email))
