"""Gateway router for the published-agent control plane (F1.4 draft CRUD).

All endpoints require a browser session (CSRF is enforced by the global
middleware) and are owner-scoped: a cross-owner read returns 404 rather than
revealing that the resource exists. ``DraftService`` is the single writer;
this router only translates HTTP into service calls and maps domain errors to
HTTP statuses.

Publish / rollback / release-history endpoints are added in F1.5.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deerflow.config.agents_config import validate_agent_slug
from deerflow.publishing.draft_service import (
    ConnectorNotGrantableError,
    DraftConflictError,
    DraftService,
    SkillNotSelectableError,
)
from deerflow.publishing.import_service import AgentImportService, ImportAlreadyExistsError
from deerflow.publishing.publish_service import (
    PublishError,
    PublishService,
    ReleaseNotFoundError,
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


def get_publish_service(request: Request) -> PublishService:
    """Return the process-wide ``PublishService`` (tests override this)."""
    service = getattr(request.app.state, "publish_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Publish service not available")
    return service


def get_import_service(request: Request) -> AgentImportService:
    """Return the process-wide ``AgentImportService`` (tests override this)."""
    service = getattr(request.app.state, "import_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Import service not available")
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

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        return validate_agent_slug(value)


class SkillSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_name: str = Field(..., min_length=1, max_length=128)
    # Accepted for API compatibility but ignored by DraftService, which always
    # derives the authoritative source from the Skill index.
    source: Literal["public", "private"] | None = None

    @field_validator("skill_name")
    @classmethod
    def validate_skill_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("skill_name must be non-empty")
        return value


class ConnectorGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_instance_id: str = Field(..., min_length=1, max_length=64)
    capability: str = Field(..., min_length=1, max_length=80)

    @field_validator("connector_instance_id", "capability")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value


class PatchDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(..., ge=1)
    agent_markdown: str | None = None
    soul_markdown: str | None = None
    model_name: str | None = None
    tool_groups: list[str] | None = None
    quota_overrides: dict[str, Any] | None = None
    skills: list[SkillSelectionRequest] | None = None
    connector_grants: list[ConnectorGrantRequest] | None = None

    @model_validator(mode="after")
    def reject_duplicate_nested_entries(self):
        if self.skills is not None:
            names = [entry.skill_name for entry in self.skills]
            if len(names) != len(set(names)):
                raise ValueError("skills must not contain duplicate skill_name values")
        if self.connector_grants is not None:
            grants = [(entry.connector_instance_id, entry.capability) for entry in self.connector_grants]
            if len(grants) != len(set(grants)):
                raise ValueError("connector_grants must not contain duplicate instance/capability pairs")
        return self


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_no: int = Field(..., ge=1)


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
        # Single atomic update: the main row and the skills/connector_grants
        # sub-tables are committed together under the revision check, so a 409
        # leaves the sub-tables untouched (code-review Critical-3).
        return await service.update_draft_bundle(
            agent_id,
            owner_user_id=owner,
            revision=payload.revision,
            agent_markdown=payload.agent_markdown,
            soul_markdown=payload.soul_markdown,
            model_name=payload.model_name,
            tool_groups=payload.tool_groups,
            quota_overrides=payload.quota_overrides,
            skills=([entry.model_dump(exclude_none=True) for entry in payload.skills] if payload.skills is not None else None),
            connector_grants=([entry.model_dump() for entry in payload.connector_grants] if payload.connector_grants is not None else None),
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


# ---------------------------------------------------------------------------
# publish / release history / rollback (F1.5)
# ---------------------------------------------------------------------------


@router.post("/{agent_id}/releases", status_code=201)
async def publish_agent(
    agent_id: str,
    request: Request,
    service: PublishService = Depends(get_publish_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    try:
        return await service.publish(agent_id, owner_user_id=owner)
    except PublishError as exc:
        # Distinguish "agent not found" (404) from validation failures (422) so
        # the caller can tell a missing resource from an unpublishable draft
        # (code-review Important-4).
        if exc.violations and exc.violations[0].code == "AGENT_NOT_FOUND":
            raise HTTPException(status_code=404, detail="Agent not found") from exc
        if any(v.code == "DRAFT_REVISION_CONFLICT" for v in exc.violations):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "draft_revision_conflict",
                    "message": "Draft changed while it was being published; retry.",
                },
            ) from exc
        raise HTTPException(
            status_code=422,
            detail={
                "code": "publish_validation_failed",
                "violations": [{"code": v.code, "message": v.message, "field": v.field} for v in exc.violations],
            },
        ) from exc


@router.get("/{agent_id}/releases")
async def list_releases(
    agent_id: str,
    request: Request,
    service: PublishService = Depends(get_publish_service),
    draft_service: DraftService = Depends(get_draft_service),
) -> list[dict[str, Any]]:
    owner = _user_id(request)
    # Return 404 (not an empty list) when the agent is missing or belongs to
    # another owner, so the caller can distinguish "no history" from "not mine"
    # (code-review Important-4).
    agent = await draft_service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await service.list_releases(agent_id, owner_user_id=owner)


@router.get("/{agent_id}/releases/{release_no}")
async def get_release(
    agent_id: str,
    release_no: int,
    request: Request,
    service: PublishService = Depends(get_publish_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    release = await service.get_release(agent_id, owner_user_id=owner, release_no=release_no)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


@router.post("/{agent_id}/rollback")
async def rollback_agent(
    agent_id: str,
    payload: RollbackRequest,
    request: Request,
    service: PublishService = Depends(get_publish_service),
) -> dict[str, Any]:
    owner = _user_id(request)
    try:
        return await service.rollback(agent_id, owner_user_id=owner, release_no=payload.release_no)
    except ReleaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc


# ---------------------------------------------------------------------------
# legacy import (F1.7)
# ---------------------------------------------------------------------------


@router.get("/import/candidates")
async def list_import_candidates(
    request: Request,
    service: AgentImportService = Depends(get_import_service),
) -> list[dict[str, Any]]:
    """List legacy filesystem agents owned by the caller that can be imported."""
    owner = _user_id(request)
    candidates = service.list_candidates(owner)
    return [
        {
            "name": c.name,
            "display_name": c.display_name,
            "description": c.description,
            "model_name": c.model_name,
            "tool_groups": c.tool_groups,
            "skills": c.skills,
            "has_soul": bool(c.soul_markdown),
        }
        for c in candidates
    ]


@router.post("/import", status_code=201)
async def import_legacy_agent(
    request: Request,
    payload: dict[str, Any],
    service: AgentImportService = Depends(get_import_service),
) -> dict[str, Any]:
    """Import one legacy filesystem agent as a draft. Never auto-publishes."""
    owner = _user_id(request)
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    try:
        return await service.import_agent(owner, str(name))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImportAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
