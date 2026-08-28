"""Gateway router for the published-agent control plane (F1.4 draft CRUD).

All endpoints require a browser session (CSRF is enforced by the global
middleware) and are owner-scoped: a cross-owner read returns 404 rather than
revealing that the resource exists. ``DraftService`` is the single writer;
this router only translates HTTP into service calls and maps domain errors to
HTTP statuses.

Publish / rollback / release-history endpoints are added in F1.5.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.gateway.deps import (
    get_agent_usage_repo,
    get_external_audit_repo,
    get_thread_store,
)
from app.gateway.draft_sandbox import (
    build_draft_sandbox_context,
    draft_sandbox_thread_metadata,
    resolve_draft_sandbox_context,
)
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import start_run
from deerflow.config.agents_config import validate_agent_slug
from deerflow.persistence.agent_usage import AgentUsageRepository
from deerflow.persistence.external_audit import ExternalAuditRepository
from deerflow.publishing.draft_service import (
    ConnectorNotGrantableError,
    DraftConflictError,
    DraftService,
    InvalidAgentStateTransitionError,
    SkillNotSelectableError,
)
from deerflow.publishing.import_service import AgentImportService, ImportAlreadyExistsError
from deerflow.publishing.publish_service import (
    PublishError,
    PublishService,
    ReleaseNotFoundError,
)
from deerflow.publishing.quota import PlatformQuota, resolve_effective_quota

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


class DraftSandboxRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(..., min_length=1, max_length=200_000)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class DraftSandboxThreadResponse(BaseModel):
    agent_id: str
    agent_slug: str
    thread_id: str
    draft_revision: int
    skill_names: list[str]
    connector_ids: list[str]
    model_name: str | None = None
    billable: Literal[False] = False


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
    """Create one owner-scoped published-Agent identity and draft."""
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


@router.get("/{agent_id}/draft/options")
async def get_draft_options(
    agent_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    """Return owner-authorized capability choices for the Studio editor."""
    owner = _user_id(request)
    if await service.get_agent(agent_id, owner_user_id=owner) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"skills": service.list_selectable_skills(owner_user_id=owner)}


@router.get("/{agent_id}/usage")
async def get_agent_usage(
    agent_id: str,
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    source: Literal["api", "feishu"] | None = Query(default=None),
    key_id: str | None = Query(default=None, min_length=1, max_length=64),
    service: DraftService = Depends(get_draft_service),
    repository: AgentUsageRepository = Depends(get_agent_usage_repo),
) -> dict[str, Any]:
    """Return owner-scoped daily usage aggregates for one Agent."""
    owner = _user_id(request)
    if await service.get_agent(agent_id, owner_user_id=owner) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    since = datetime.now(UTC) - timedelta(days=days - 1)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    return await repository.aggregate_daily(
        owner_user_id=owner,
        agent_id=agent_id,
        since=since,
        source=source,
        credential_id=key_id,
        model_costs=getattr(request.app.state, "publishing_model_costs", {}),
    )


def _platform_quota(request: Request) -> PlatformQuota:
    value = getattr(
        request.app.state,
        "publishing_platform_quota",
        None,
    )
    return value if isinstance(value, PlatformQuota) else PlatformQuota()


def _quota_values(platform: PlatformQuota) -> dict[str, int]:
    return {
        "max_concurrent_runs": platform.max_concurrent_runs_per_agent,
        "daily_runs": platform.daily_runs_default,
        "daily_tokens": platform.daily_tokens_default,
        "max_run_seconds": platform.max_run_seconds,
        "max_tokens_per_run": platform.max_tokens_per_run,
        "max_input_bytes": platform.max_input_bytes,
        "inbound_rps": platform.inbound_rps,
    }


@router.get("/{agent_id}/quota")
async def get_agent_quota_policy(
    agent_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    """Preview the next Release's owner quota under platform hard caps."""
    owner = _user_id(request)
    if await service.get_agent(agent_id, owner_user_id=owner) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    draft = await service.get_draft(agent_id, owner_user_id=owner)
    if draft is None:
        raise HTTPException(status_code=404, detail="Agent draft not found")
    platform = _platform_quota(request)
    owner_overrides = dict(draft.get("quota_overrides") or {})
    effective = resolve_effective_quota(
        platform,
        owner_overrides,
        {},
    )
    return {
        "agent_id": agent_id,
        "platform_defaults": _quota_values(platform),
        "owner_overrides": owner_overrides,
        "effective": {
            "max_concurrent_runs": effective.max_concurrent_runs,
            "daily_runs": effective.daily_runs,
            "daily_tokens": effective.daily_tokens,
            "max_run_seconds": effective.max_run_seconds,
            "max_tokens_per_run": effective.max_tokens_per_run,
            "max_input_bytes": effective.max_input_bytes,
            "inbound_rps": effective.inbound_rps,
        },
    }


def _audit_category(status_code: int) -> str:
    if status_code == 429:
        return "quota"
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        return "capability"
    return "request"


@router.get("/{agent_id}/audit")
async def get_agent_rejection_audit(
    agent_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    service: DraftService = Depends(get_draft_service),
    repository: ExternalAuditRepository = Depends(get_external_audit_repo),
) -> list[dict[str, Any]]:
    """Return a metadata-only owner view of recent rejected requests."""
    owner = _user_id(request)
    if await service.get_agent(agent_id, owner_user_id=owner) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    rows = await repository.list(
        owner_user_id=owner,
        agent_id=agent_id,
        minimum_status_code=400,
        limit=limit,
    )
    return [
        {
            "id": row["id"],
            "request_id": row["request_id"],
            "source": row.get("source"),
            "credential_id": row.get("credential_id"),
            "category": _audit_category(int(row["status_code"])),
            "action": row["action"],
            "resource_type": row.get("resource_type"),
            "resource_id": row.get("resource_id"),
            "skill_name": row.get("skill_name"),
            "method": row["method"],
            "path_template": row["path_template"],
            "status_code": row["status_code"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


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
            model_name_provided="model_name" in payload.model_fields_set,
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


@router.post("/{agent_id}/draft/sandbox-runs", status_code=202)
async def create_draft_sandbox_run(
    agent_id: str,
    payload: DraftSandboxRunRequest,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> dict[str, Any]:
    """Start an owner-only Run from a frozen draft without Published billing."""
    owner = _user_id(request)
    agent = await service.get_agent(agent_id, owner_user_id=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    resolved_id = str(agent["id"])
    draft = await service.get_draft(resolved_id, owner_user_id=owner)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    snapshot = build_draft_sandbox_context(
        owner_user_id=owner,
        agent_id=resolved_id,
        agent=agent,
        draft=draft,
    )
    thread_id = str(uuid4())
    body = RunCreateRequest(
        assistant_id="lead_agent",
        input={"messages": [{"role": "user", "content": payload.message}]},
        metadata=draft_sandbox_thread_metadata(
            agent_id=resolved_id,
            draft_revision=snapshot.draft_revision,
        )
        | {
            "agent_name": snapshot.agent_slug,
            "agent_display_name": str(agent.get("display_name") or snapshot.agent_slug),
        },
        context={
            "agent_name": snapshot.agent_slug,
            "model_name": snapshot.model_name,
            "connector_ids": list(snapshot.connector_ids),
        },
        stream_mode=["values", "messages-tuple", "custom"],
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    record = await start_run(
        body,
        thread_id,
        request,
        draft_sandbox_context=snapshot,
    )
    status = getattr(record.status, "value", str(record.status))
    return {
        "agent_id": agent_id,
        "thread_id": record.thread_id,
        "run_id": record.run_id,
        "status": status,
        "draft_revision": snapshot.draft_revision,
        "billable": False,
    }


@router.get(
    "/draft/sandbox-threads/{thread_id}",
    response_model=DraftSandboxThreadResponse,
)
async def get_draft_sandbox_thread(
    thread_id: str,
    request: Request,
    service: DraftService = Depends(get_draft_service),
) -> DraftSandboxThreadResponse:
    """Return the frozen capability scope for an owner sandbox conversation."""
    snapshot = await resolve_draft_sandbox_context(
        thread_store=get_thread_store(request),
        draft_service=service,
        owner_user_id=_user_id(request),
        thread_id=thread_id,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Draft sandbox not found")
    return DraftSandboxThreadResponse(
        agent_id=snapshot.agent_id,
        agent_slug=snapshot.agent_slug,
        thread_id=thread_id,
        draft_revision=snapshot.draft_revision,
        skill_names=list(snapshot.skill_names),
        connector_ids=list(snapshot.connector_ids),
        model_name=snapshot.model_name,
        billable=False,
    )


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
    try:
        await service.archive(agent_id, owner_user_id=owner)
    except InvalidAgentStateTransitionError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_state_transition", "message": str(exc)}) from exc
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
    try:
        await service.suspend(agent_id, owner_user_id=owner)
    except InvalidAgentStateTransitionError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_state_transition", "message": str(exc)}) from exc
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
    try:
        await service.resume(agent_id, owner_user_id=owner)
    except InvalidAgentStateTransitionError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_state_transition", "message": str(exc)}) from exc
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
