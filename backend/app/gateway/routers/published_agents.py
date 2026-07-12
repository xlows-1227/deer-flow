"""Gateway router for the published-agent control plane (F1.4 draft CRUD).

All endpoints require a browser session (CSRF is enforced by the global
middleware) and are owner-scoped: a cross-owner read returns 404 rather than
revealing that the resource exists. ``DraftService`` is the single writer;
this router only translates HTTP into service calls and maps domain errors to
HTTP statuses.

Publish / rollback / release-history endpoints are added in F1.5.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from deerflow.publishing.draft_service import (
    ConnectorNotGrantableError,
    DraftConflictError,
    DraftService,
    SkillNotSelectableError,
)

router = APIRouter(prefix="/api/published-agents", tags=["published-agents"])


# ---------------------------------------------------------------------------
# auth + DI
# ---------------------------------------------------------------------------


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or getattr(request.state, "auth_method", None) != "session":
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user.id)


def get_draft_service(request: Request) -> DraftService:
    """Return the process-wide ``DraftService``.

    The default implementation builds a service from the persistence layer and
    platform skill/connector subsystems on ``app.state``; tests override this
    dependency with an in-memory variant.
    """
    service = getattr(request.app.state, "draft_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Published-agent service not available")
    return service


# ---------------------------------------------------------------------------
# request / response models
# ---------------------------------------------------------------------------


class CreateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    avatar_ref: str | None = None


class PatchDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(..., ge=1)
    agent_markdown: str | None = None
    soul_markdown: str | None = None
    model_name: str | None = None
    tool_groups: list[str] | None = None
    quota_overrides: dict[str, Any] | None = None
    skills: list[dict[str, str]] | None = None
    connector_grants: list[dict[str, str]] | None = None


def _agent_summary(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": agent["id"],
        "slug": agent["slug"],
        "display_name": agent["display_name"],
        "description": agent.get("description"),
        "avatar_ref": agent.get("avatar_ref"),
        "status": agent["status"],
        "current_release_id": agent.get("current_release_id"),
        "created_at": agent.get("created_at"),
        "updated_at": agent.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_agent(
    payload: CreateAgentRequest,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    try:
        agent = await service.create_agent(
            owner_user_id=owner,
            slug=payload.slug,
            display_name=payload.display_name,
            description=payload.description,
            avatar_ref=payload.avatar_ref,
        )
    except ValueError as exc:
        # Duplicate slug within owner.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _agent_summary(agent)


@router.get("")
async def list_agents(
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> list[dict[str, Any]]:
    owner = _user_id(request)
    agents = await service.list_agents(owner)
    return [_agent_summary(a) for a in agents]


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    agent = await service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    draft = await service.get_draft(agent_id, owner_user_id=owner)
    return {**_agent_summary(agent), "draft": draft}


@router.patch("/{agent_id}/draft")
async def patch_draft(
    agent_id: str,
    payload: PatchDraftRequest,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    # Existence check first so a missing agent yields 404 (not 409).
    agent = await service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        if payload.skills is not None:
            await service.set_skills(agent_id, owner_user_id=owner, skills=payload.skills)
        if payload.connector_grants is not None:
            await service.set_connector_grants(
                agent_id, owner_user_id=owner, grants=payload.connector_grants
            )
        return await service.update_draft(
            agent_id,
            owner_user_id=owner,
            revision=payload.revision,
            agent_markdown=payload.agent_markdown,
            soul_markdown=payload.soul_markdown,
            model_name=payload.model_name,
            tool_groups=payload.tool_groups,
            quota_overrides=payload.quota_overrides,
        )
    except SkillNotSelectableError as exc:
        raise HTTPException(status_code=422, detail={"code": "skill_not_selectable", "message": str(exc)}) from exc
    except ConnectorNotGrantableError as exc:
        raise HTTPException(status_code=422, detail={"code": "connector_not_grantable", "message": str(exc)}) from exc
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "revision_conflict", "message": str(exc)}) from exc


@router.post("/{agent_id}/archive")
async def archive_agent(
    agent_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    agent = await service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await service.archive(agent_id, owner_user_id=owner)
    updated = await service.get_agent(agent_id, owner_user_id=owner)
    return _agent_summary(updated or agent)


@router.post("/{agent_id}/suspend")
async def suspend_agent(
    agent_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    agent = await service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await service.suspend(agent_id, owner_user_id=owner)
    updated = await service.get_agent(agent_id, owner_user_id=owner)
    return _agent_summary(updated or agent)


@router.post("/{agent_id}/resume")
async def resume_agent(
    agent_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    agent = await service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await service.resume(agent_id, owner_user_id=owner)
    updated = await service.get_agent(agent_id, owner_user_id=owner)
    return _agent_summary(updated or agent)
