"""Admin invite-code management endpoints: batch create and paginated list."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.gateway.auth.models import User
from app.gateway.deps import get_invite_code_repo
from app.gateway.routers.admin_users import _require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/invite-codes", tags=["admin-invite-codes"])


class BatchCreateRequest(BaseModel):
    """Request body for batch invite-code creation."""

    count: int = Field(..., gt=0, le=500, description="Number of codes to generate (1-500)")


class InviteCodeItemResponse(BaseModel):
    code: str
    used: bool
    used_by_email: str | None = None
    created_at: str
    used_at: str | None = None


class InviteCodesListResponse(BaseModel):
    codes: list[InviteCodeItemResponse]
    total: int
    page: int
    page_size: int


class BatchCreateResponse(BaseModel):
    created: list[str] = Field(..., description="The newly generated invite codes")


def _fmt_dt(dt) -> str | None:
    """Format a datetime to ISO string; None stays None."""
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


@router.post(
    "/batch",
    response_model=BatchCreateResponse,
    summary="Batch Create Invite Codes (admin)",
    description="Generate the given number of random invite codes. Admin only.",
)
async def batch_create_invite_codes(
    body: BatchCreateRequest,
    request: Request,
    admin: User = Depends(_require_admin_user),
) -> BatchCreateResponse:
    repo = get_invite_code_repo(request)
    codes = await repo.batch_create(body.count)
    logger.info("Admin %s batch-created %d invite codes", admin.email, len(codes))
    return BatchCreateResponse(created=codes)


@router.get(
    "",
    response_model=InviteCodesListResponse,
    summary="List Invite Codes (admin)",
    description=(
        "Paginated list of invite codes. Default sort: unused first, then "
        "earliest created. Pass sort_by=created_at|used_at and "
        "sort_order=asc|desc to let the user choose the ordering. Admin only."
    ),
)
async def list_invite_codes(
    request: Request,
    admin: User = Depends(_require_admin_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    sort_by: Literal["created_at", "used_at"] | None = Query(default=None),
    sort_order: Literal["asc", "desc"] | None = Query(default=None),
) -> InviteCodesListResponse:
    repo = get_invite_code_repo(request)
    rows, total = await repo.list_paginated(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return InviteCodesListResponse(
        codes=[
            InviteCodeItemResponse(
                code=r["code"],
                used=r["used"],
                used_by_email=r["used_by_email"],
                created_at=_fmt_dt(r["created_at"]),
                used_at=_fmt_dt(r["used_at"]),
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
