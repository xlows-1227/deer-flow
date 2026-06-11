"""Memory API router for retrieving and managing global memory data."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deerflow.agents.memory.compat import (
    add_manual_profile_item,
    delete_profile_item,
    profile_to_legacy_memory,
    update_profile_item,
)
from deerflow.agents.memory.consolidation import ProfileConsolidator
from deerflow.agents.memory.migration import legacy_memory_to_profile, migrate_legacy_memory
from deerflow.agents.memory.models import DailyPersonSummary, MemoryProfile
from deerflow.agents.memory.queue import get_memory_queue
from deerflow.agents.memory.rollup import DailyRollupService
from deerflow.agents.memory.storage_v2 import get_memory_storage_v2
from deerflow.agents.memory.updater import (
    clear_memory_data,
    create_memory_fact,
    delete_memory_fact,
    get_memory_data,
    import_memory_data,
    reload_memory_data,
    update_memory_fact,
)
from deerflow.config.memory_config import get_memory_config
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api", tags=["memory"])


class ContextSection(BaseModel):
    """Model for context sections (user and history)."""

    summary: str = Field(default="", description="Summary content")
    updatedAt: str = Field(default="", description="Last update timestamp")


class UserContext(BaseModel):
    """Model for user context."""

    workContext: ContextSection = Field(default_factory=ContextSection)
    personalContext: ContextSection = Field(default_factory=ContextSection)
    topOfMind: ContextSection = Field(default_factory=ContextSection)


class HistoryContext(BaseModel):
    """Model for history context."""

    recentMonths: ContextSection = Field(default_factory=ContextSection)
    earlierContext: ContextSection = Field(default_factory=ContextSection)
    longTermBackground: ContextSection = Field(default_factory=ContextSection)


class Fact(BaseModel):
    """Model for a memory fact."""

    id: str = Field(..., description="Unique identifier for the fact")
    content: str = Field(..., description="Fact content")
    category: str = Field(default="context", description="Fact category")
    confidence: float = Field(default=0.5, description="Confidence score (0-1)")
    createdAt: str = Field(default="", description="Creation timestamp")
    source: str = Field(default="unknown", description="Source thread ID")
    sourceError: str | None = Field(default=None, description="Optional description of the prior mistake or wrong approach")


class MemoryResponse(BaseModel):
    """Response model for memory data."""

    version: str = Field(default="1.0", description="Memory schema version")
    lastUpdated: str = Field(default="", description="Last update timestamp")
    user: UserContext = Field(default_factory=UserContext)
    history: HistoryContext = Field(default_factory=HistoryContext)
    facts: list[Fact] = Field(default_factory=list)


def _map_memory_fact_value_error(exc: ValueError) -> HTTPException:
    """Convert updater validation errors into stable API responses."""
    if exc.args and exc.args[0] == "confidence":
        detail = "Invalid confidence value; must be between 0 and 1."
    else:
        detail = "Memory fact content cannot be empty."
    return HTTPException(status_code=400, detail=detail)


class FactCreateRequest(BaseModel):
    """Request model for creating a memory fact."""

    content: str = Field(..., min_length=1, description="Fact content")
    category: str = Field(default="context", description="Fact category")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score (0-1)")


class FactPatchRequest(BaseModel):
    """PATCH request model that preserves existing values for omitted fields."""

    content: str | None = Field(default=None, min_length=1, description="Fact content")
    category: str | None = Field(default=None, description="Fact category")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="Confidence score (0-1)")


class MemoryConfigResponse(BaseModel):
    """Response model for memory configuration."""

    enabled: bool = Field(..., description="Whether memory is enabled")
    storage_path: str = Field(..., description="Path to memory storage file")
    debounce_seconds: int = Field(..., description="Debounce time for memory updates")
    max_facts: int = Field(..., description="Maximum number of facts to store")
    fact_confidence_threshold: float = Field(..., description="Minimum confidence threshold for facts")
    injection_enabled: bool = Field(..., description="Whether memory injection is enabled")
    max_injection_tokens: int = Field(..., description="Maximum tokens for memory injection")
    v2_enabled: bool = Field(default=True, description="Whether v2 daily-person memory is enabled")
    daily_rollup_enabled: bool = Field(default=True, description="Whether daily rollup is enabled")
    daily_rollup_time: str = Field(default="23:55", description="Daily rollup time")
    retention_days: int | None = Field(default=None, description="Daily summary retention in days")
    relevance_strategy: str = Field(default="rules", description="Memory relevance strategy")
    max_daily_snippets: int = Field(default=3, description="Maximum daily snippets injected")
    max_daily_snippet_tokens: int = Field(default=600, description="Daily snippet token budget")


class MemoryStatusResponse(BaseModel):
    """Response model for memory status."""

    config: MemoryConfigResponse
    data: MemoryResponse


class DailyRollupRequest(BaseModel):
    """Request model for manual daily rollup."""

    date: str | None = Field(default=None, description="Optional YYYY-MM-DD date")
    threadId: str | None = Field(default=None, description="Optional thread id for per-conversation rollup")
    force: bool = Field(default=False, description="Reserved for future forced regeneration")


def _get_v2_profile_for_response(user_id: str) -> MemoryProfile:
    config = get_memory_config()
    if getattr(config, "v2_enabled", False) and getattr(config, "migrate_legacy_on_startup", True):
        return migrate_legacy_memory(user_id)
    return get_memory_storage_v2().load_profile(user_id)


def _get_memory_response_data(user_id: str) -> dict:
    config = get_memory_config()
    if getattr(config, "v2_enabled", False):
        profile = _get_v2_profile_for_response(user_id)
        return profile_to_legacy_memory(profile)
    return get_memory_data(user_id=user_id)


@router.get(
    "/memory",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Get Memory Data",
    description="Retrieve the current global memory data including user context, history, and facts.",
)
async def get_memory() -> MemoryResponse:
    """Get the current global memory data.

    Returns:
        The current memory data with user context, history, and facts.

    Example Response:
        ```json
        {
            "version": "1.0",
            "lastUpdated": "2024-01-15T10:30:00Z",
            "user": {
                "workContext": {"summary": "Working on DeerFlow project", "updatedAt": "..."},
                "personalContext": {"summary": "Prefers concise responses", "updatedAt": "..."},
                "topOfMind": {"summary": "Building memory API", "updatedAt": "..."}
            },
            "history": {
                "recentMonths": {"summary": "Recent development activities", "updatedAt": "..."},
                "earlierContext": {"summary": "", "updatedAt": ""},
                "longTermBackground": {"summary": "", "updatedAt": ""}
            },
            "facts": [
                {
                    "id": "fact_abc123",
                    "content": "User prefers TypeScript over JavaScript",
                    "category": "preference",
                    "confidence": 0.9,
                    "createdAt": "2024-01-15T10:30:00Z",
                    "source": "thread_xyz"
                }
            ]
        }
        ```
    """
    memory_data = _get_memory_response_data(get_effective_user_id())
    return MemoryResponse(**memory_data)


@router.post(
    "/memory/reload",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Reload Memory Data",
    description="Reload memory data from the storage file, refreshing the in-memory cache.",
)
async def reload_memory() -> MemoryResponse:
    """Reload memory data from file.

    This forces a reload of the memory data from the storage file,
    useful when the file has been modified externally.

    Returns:
        The reloaded memory data.
    """
    user_id = get_effective_user_id()
    if getattr(get_memory_config(), "v2_enabled", False):
        memory_data = profile_to_legacy_memory(get_memory_storage_v2().load_profile(user_id))
    else:
        memory_data = reload_memory_data(user_id=user_id)
    return MemoryResponse(**memory_data)


@router.delete(
    "/memory",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Clear All Memory Data",
    description="Delete all saved memory data and reset the memory structure to an empty state.",
)
async def clear_memory() -> MemoryResponse:
    """Clear all persisted memory data."""
    try:
        user_id = get_effective_user_id()
        if getattr(get_memory_config(), "v2_enabled", False):
            get_memory_queue().clear_user(user_id)
            get_memory_storage_v2().clear_user_memory(user_id)
            memory_data = profile_to_legacy_memory(get_memory_storage_v2().load_profile(user_id))
        else:
            memory_data = clear_memory_data(user_id=user_id)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to clear memory data.") from exc

    return MemoryResponse(**memory_data)


@router.post(
    "/memory/facts",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Create Memory Fact",
    description="Create a single saved memory fact manually.",
)
async def create_memory_fact_endpoint(request: FactCreateRequest) -> MemoryResponse:
    """Create a single fact manually."""
    try:
        user_id = get_effective_user_id()
        if getattr(get_memory_config(), "v2_enabled", False):
            profile = add_manual_profile_item(request.content, request.category, request.confidence, user_id=user_id)
            memory_data = profile_to_legacy_memory(profile)
        else:
            memory_data = create_memory_fact(
                content=request.content,
                category=request.category,
                confidence=request.confidence,
                user_id=user_id,
            )
    except ValueError as exc:
        raise _map_memory_fact_value_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to create memory fact.") from exc

    return MemoryResponse(**memory_data)


@router.delete(
    "/memory/facts/{fact_id}",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Delete Memory Fact",
    description="Delete a single saved memory fact by its fact id.",
)
async def delete_memory_fact_endpoint(fact_id: str) -> MemoryResponse:
    """Delete a single fact from memory by fact id."""
    try:
        user_id = get_effective_user_id()
        if getattr(get_memory_config(), "v2_enabled", False):
            profile = delete_profile_item(fact_id, user_id=user_id)
            memory_data = profile_to_legacy_memory(profile)
        else:
            memory_data = delete_memory_fact(fact_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Memory fact '{fact_id}' not found.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to delete memory fact.") from exc

    return MemoryResponse(**memory_data)


@router.patch(
    "/memory/facts/{fact_id}",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Patch Memory Fact",
    description="Partially update a single saved memory fact by its fact id while preserving omitted fields.",
)
async def update_memory_fact_endpoint(fact_id: str, request: FactPatchRequest) -> MemoryResponse:
    """Partially update a single fact manually."""
    try:
        user_id = get_effective_user_id()
        if getattr(get_memory_config(), "v2_enabled", False):
            profile = update_profile_item(
                fact_id,
                content=request.content,
                category=request.category,
                confidence=request.confidence,
                user_id=user_id,
            )
            memory_data = profile_to_legacy_memory(profile)
        else:
            memory_data = update_memory_fact(
                fact_id=fact_id,
                content=request.content,
                category=request.category,
                confidence=request.confidence,
                user_id=user_id,
            )
    except ValueError as exc:
        raise _map_memory_fact_value_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Memory fact '{fact_id}' not found.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to update memory fact.") from exc

    return MemoryResponse(**memory_data)


@router.get(
    "/memory/export",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Export Memory Data",
    description="Export the current global memory data as JSON for backup or transfer.",
)
async def export_memory() -> MemoryResponse:
    """Export the current memory data."""
    memory_data = _get_memory_response_data(get_effective_user_id())
    return MemoryResponse(**memory_data)


@router.post(
    "/memory/import",
    response_model=MemoryResponse,
    response_model_exclude_none=True,
    summary="Import Memory Data",
    description="Import and overwrite the current global memory data from a JSON payload.",
)
async def import_memory(request: MemoryResponse) -> MemoryResponse:
    """Import and persist memory data."""
    try:
        user_id = get_effective_user_id()
        if getattr(get_memory_config(), "v2_enabled", False):
            profile = legacy_memory_to_profile(user_id, request.model_dump())
            profile = get_memory_storage_v2().save_profile(user_id, profile)
            memory_data = profile_to_legacy_memory(profile)
        else:
            memory_data = import_memory_data(request.model_dump(), user_id=user_id)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to import memory data.") from exc

    return MemoryResponse(**memory_data)


@router.get(
    "/memory/profile",
    response_model=MemoryProfile,
    summary="Get Memory Profile",
    description="Retrieve the v2 long-term memory profile for the current user.",
)
async def get_memory_profile() -> MemoryProfile:
    """Get the v2 memory profile."""
    return _get_v2_profile_for_response(get_effective_user_id())


@router.get(
    "/memory/daily",
    response_model=list[DailyPersonSummary] | DailyPersonSummary | None,
    summary="Get Daily Memory Summaries",
    description="Retrieve one daily summary by date, or recent daily summaries by limit.",
)
async def get_daily_memory(date: str | None = None, limit: int = 30):
    """Get daily summaries."""
    storage = get_memory_storage_v2()
    user_id = get_effective_user_id()
    if date:
        return storage.load_daily(user_id, date)
    return storage.list_daily(user_id, limit=limit)


@router.post(
    "/memory/daily/rollup",
    response_model=DailyPersonSummary | None,
    summary="Roll Up Daily Memory",
    description="Manually roll up memory for a date or a specific thread.",
)
async def rollup_daily_memory(request: DailyRollupRequest) -> DailyPersonSummary | None:
    """Manually roll up daily memory."""
    user_id = get_effective_user_id()
    get_memory_queue().flush_user(user_id)
    service = DailyRollupService()
    if request.threadId:
        summary = service.rollup_thread(user_id, request.threadId, request.date)
    else:
        summary = service.rollup_date(user_id, request.date, source_kind="manual")
    if summary is not None:
        ProfileConsolidator().rebuild_profile(user_id)
    return summary


@router.delete(
    "/memory/daily/{date}",
    response_model=DailyPersonSummary | None,
    summary="Delete Daily Memory",
    description="Soft-delete a daily summary and rebuild the profile.",
)
async def delete_daily_memory(date: str) -> DailyPersonSummary | None:
    """Soft-delete a daily memory summary."""
    user_id = get_effective_user_id()
    summary = get_memory_storage_v2().soft_delete_daily(user_id, date)
    ProfileConsolidator().rebuild_profile(user_id)
    return summary


@router.post(
    "/memory/daily/{date}/restore",
    response_model=DailyPersonSummary | None,
    summary="Restore Daily Memory",
)
async def restore_daily_memory(date: str) -> DailyPersonSummary | None:
    """Restore a soft-deleted daily memory summary."""
    user_id = get_effective_user_id()
    summary = get_memory_storage_v2().restore_daily(user_id, date)
    ProfileConsolidator().rebuild_profile(user_id)
    return summary


@router.delete(
    "/memory/daily/{date}/purge",
    response_model=dict,
    summary="Purge Daily Memory",
)
async def purge_daily_memory(date: str) -> dict:
    """Permanently delete a daily memory summary."""
    user_id = get_effective_user_id()
    deleted = get_memory_storage_v2().purge_daily(user_id, date)
    ProfileConsolidator().rebuild_profile(user_id)
    return {"deleted": deleted}


@router.post(
    "/memory/consolidate",
    response_model=MemoryProfile,
    summary="Consolidate Memory Profile",
)
async def consolidate_memory_profile() -> MemoryProfile:
    """Rebuild the v2 profile from active daily summaries."""
    return ProfileConsolidator().rebuild_profile(get_effective_user_id())


@router.post(
    "/memory/migrate-legacy",
    response_model=MemoryProfile,
    summary="Migrate Legacy Memory",
)
async def migrate_legacy_memory_endpoint() -> MemoryProfile:
    """Back up and migrate legacy memory.json into v2 profile.json."""
    return migrate_legacy_memory(get_effective_user_id(), force=True)


@router.get(
    "/memory/config",
    response_model=MemoryConfigResponse,
    summary="Get Memory Configuration",
    description="Retrieve the current memory system configuration.",
)
async def get_memory_config_endpoint() -> MemoryConfigResponse:
    """Get the memory system configuration.

    Returns:
        The current memory configuration settings.

    Example Response:
        ```json
        {
            "enabled": true,
            "storage_path": ".deer-flow/memory.json",
            "debounce_seconds": 30,
            "max_facts": 100,
            "fact_confidence_threshold": 0.7,
            "injection_enabled": true,
            "max_injection_tokens": 2000
        }
        ```
    """
    config = get_memory_config()
    return MemoryConfigResponse(
        enabled=config.enabled,
        storage_path=config.storage_path,
        debounce_seconds=config.debounce_seconds,
        max_facts=config.max_facts,
        fact_confidence_threshold=config.fact_confidence_threshold,
        injection_enabled=config.injection_enabled,
        max_injection_tokens=config.max_injection_tokens,
        v2_enabled=config.v2_enabled,
        daily_rollup_enabled=config.daily_rollup_enabled,
        daily_rollup_time=config.daily_rollup_time,
        retention_days=config.retention_days,
        relevance_strategy=config.relevance_strategy,
        max_daily_snippets=config.max_daily_snippets,
        max_daily_snippet_tokens=config.max_daily_snippet_tokens,
    )


@router.get(
    "/memory/status",
    response_model=MemoryStatusResponse,
    response_model_exclude_none=True,
    summary="Get Memory Status",
    description="Retrieve both memory configuration and current data in a single request.",
)
async def get_memory_status() -> MemoryStatusResponse:
    """Get the memory system status including configuration and data.

    Returns:
        Combined memory configuration and current data.
    """
    config = get_memory_config()
    memory_data = _get_memory_response_data(get_effective_user_id())

    return MemoryStatusResponse(
        config=MemoryConfigResponse(
            enabled=config.enabled,
            storage_path=config.storage_path,
            debounce_seconds=config.debounce_seconds,
            max_facts=config.max_facts,
            fact_confidence_threshold=config.fact_confidence_threshold,
            injection_enabled=config.injection_enabled,
            max_injection_tokens=config.max_injection_tokens,
            v2_enabled=config.v2_enabled,
            daily_rollup_enabled=config.daily_rollup_enabled,
            daily_rollup_time=config.daily_rollup_time,
            retention_days=config.retention_days,
            relevance_strategy=config.relevance_strategy,
            max_daily_snippets=config.max_daily_snippets,
            max_daily_snippet_tokens=config.max_daily_snippet_tokens,
        ),
        data=MemoryResponse(**memory_data),
    )
