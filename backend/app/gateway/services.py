"""Run lifecycle service layer.

Centralizes the business logic for creating runs, formatting SSE
frames, and consuming stream bridge events.  Router modules
(``thread_runs``, ``runs``) are thin HTTP handlers that delegate here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

from app.gateway.deps import get_run_context, get_run_manager, get_stream_bridge
from app.gateway.draft_sandbox import (
    DRAFT_SANDBOX_METADATA_KEYS,
    draft_sandbox_thread_metadata,
    resolve_draft_sandbox_context,
)
from app.gateway.utils import sanitize_log_param
from deerflow.config.agents_config import validate_agent_slug
from deerflow.config.app_config import get_app_config
from deerflow.config.effective_config import effective_app_config_scope
from deerflow.runtime import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    ConflictError,
    DisconnectMode,
    RunManager,
    RunRecord,
    RunStatus,
    StreamBridge,
    UnsupportedStrategyError,
    run_agent,
)
from deerflow.runtime.runs.naming import resolve_root_run_name
from deerflow.runtime.user_context import runtime_user_scope

if TYPE_CHECKING:
    from deerflow.publishing.context import DraftSandboxContext, PublishedAgentContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------


def format_sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    """Format a single SSE frame.

    Field order: ``event:`` -> ``data:`` -> ``id:`` (optional) -> blank line.
    This matches the LangGraph Platform wire format consumed by the
    ``useStream`` React hook and the Python ``langgraph-sdk`` SSE decoder.
    """
    payload = json.dumps(data, default=str, ensure_ascii=False)
    parts = [f"event: {event}", f"data: {payload}"]
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append("")
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Input / config helpers
# ---------------------------------------------------------------------------


def normalize_stream_modes(raw: list[str] | str | None) -> list[str]:
    """Normalize the stream_mode parameter to a list.

    Default matches what ``useStream`` expects: values + messages-tuple.
    """
    if raw is None:
        return ["values"]
    if isinstance(raw, str):
        return [raw]
    return raw if raw else ["values"]


def normalize_input(raw_input: dict[str, Any] | None) -> dict[str, Any]:
    """Convert LangGraph Platform input format to LangChain state dict.

    Delegates dict→message coercion to ``langchain_core.messages.utils.convert_to_messages``
    so that ``additional_kwargs`` (e.g. uploaded-file metadata — gh #3132), ``id``,
    ``name``, and non-human roles (ai/system/tool) survive unchanged.  An earlier
    hand-rolled version only forwarded ``content`` and collapsed every role to
    ``HumanMessage``, which silently stripped frontend-supplied attachments.

    Malformed message dicts (missing ``role``/``type``/``content``, unsupported
    role, etc.) raise ``HTTPException(400)`` with the offending index, instead
    of bubbling up as a 500.  The gateway is a system boundary, so per-entry
    validation errors are the right shape for clients to retry against.
    """
    if raw_input is None:
        return {}
    messages = raw_input.get("messages")
    if messages and isinstance(messages, list):
        converted: list[Any] = []
        for index, msg in enumerate(messages):
            if isinstance(msg, BaseMessage):
                converted.append(msg)
            elif isinstance(msg, dict):
                try:
                    converted.extend(convert_to_messages([msg]))
                except (ValueError, TypeError, NotImplementedError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid message at input.messages[{index}]: {exc}",
                    ) from exc
            else:
                converted.append(msg)
        return {**raw_input, "messages": converted}
    return raw_input


_DEFAULT_ASSISTANT_ID = "lead_agent"


# Whitelist of run-context keys that the langgraph-compat layer forwards from
# ``body.context`` into the run config. ``config["context"]`` exists in
# LangGraph >=0.6, but these values must be written to both ``configurable``
# (for legacy ``_get_runtime_config`` consumers) and ``context`` because
# LangGraph >=1.1.9 no longer makes ``ToolRuntime.context`` fall back to
# ``configurable`` for consumers like ``setup_agent``.
_CONTEXT_CONFIGURABLE_KEYS: frozenset[str] = frozenset(
    {
        "model_name",
        "mode",
        "thinking_enabled",
        "reasoning_effort",
        "is_plan_mode",
        "subagent_enabled",
        "max_concurrent_subagents",
        "agent_name",
        "is_bootstrap",
        "skill_name",
        "external_allowed_skills",
        "connector_ids",
        "thread_id",
    }
)

_SERVER_RESERVED_CONFIG_PREFIXES: tuple[str, ...] = ("__agent_",)


def _is_server_reserved_config_key(key: object) -> bool:
    """Return whether an inbound runtime key belongs to the server."""
    return isinstance(key, str) and key.startswith(_SERVER_RESERVED_CONFIG_PREFIXES)


def _sanitize_client_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the public run-context allowlist to nested ``config.context``."""
    return {key: value for key, value in context.items() if key in _CONTEXT_CONFIGURABLE_KEYS and not _is_server_reserved_config_key(key)}


def _sanitize_client_configurable(configurable: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve public LangGraph options while removing server-owned fields."""
    return {key: value for key, value in configurable.items() if not _is_server_reserved_config_key(key)}


def merge_run_context_overrides(config: dict[str, Any], context: Mapping[str, Any] | None, *, thread_id: str | None = None) -> None:
    """Merge whitelisted keys from ``body.context`` into both ``config['configurable']``
    and ``config['context']`` so they are visible to legacy configurable readers and
    to LangGraph ``ToolRuntime.context`` consumers (e.g. the ``setup_agent`` tool —
    see issue #2677)."""
    if not context:
        return
    configurable = config.setdefault("configurable", {})
    runtime_context = config.setdefault("context", {})
    for key in _CONTEXT_CONFIGURABLE_KEYS:
        if key in context:
            value = context[key]
            if key == "thread_id" and thread_id:
                value = thread_id
            if key == "thread_id" and isinstance(configurable, dict) and configurable.get("thread_id"):
                value = configurable["thread_id"]
            if isinstance(configurable, dict):
                configurable.setdefault(key, value)
            if isinstance(runtime_context, dict):
                runtime_context.setdefault(key, value)


def inject_authenticated_user_context(config: dict[str, Any], request: Request) -> None:
    """Stamp the authenticated user into the run context for background tools.

    Tool execution may happen after the request handler has returned, so tools
    that persist user-scoped files should not rely only on ambient ContextVars.
    The value comes from server-side auth state, never from client context.
    """

    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return

    runtime_context = config.setdefault("context", {})
    if isinstance(runtime_context, dict):
        runtime_context["user_id"] = str(user_id)


def _inject_draft_sandbox_context(
    config: dict[str, Any],
    snapshot: DraftSandboxContext,
) -> None:
    """Inject only the frozen draft's selected Connector capabilities."""
    configurable = config.setdefault("configurable", {})
    runtime_context = config.setdefault("context", {})
    connector_capabilities = snapshot.connector_capability_map()
    configurable["agent_name"] = snapshot.agent_slug
    configurable["model_name"] = snapshot.model_name
    configurable["connector_ids"] = list(snapshot.connector_ids)
    configurable["connector_capabilities"] = connector_capabilities
    configurable["__agent_draft_sandbox_context"] = snapshot
    runtime_context["agent_name"] = snapshot.agent_slug
    runtime_context["connector_ids"] = list(snapshot.connector_ids)
    runtime_context["connector_capabilities"] = connector_capabilities


async def resolve_draft_sandbox_context_for_thread(
    request: Request,
    thread_id: str,
) -> DraftSandboxContext | None:
    """Resolve server-owned draft sandbox authority for a follow-up Run."""
    request_state = getattr(request, "state", None)
    user = getattr(request_state, "user", None)
    owner_user_id = str(user.id) if user is not None and getattr(request_state, "auth_method", None) == "session" else None
    app = getattr(request, "app", None)
    app_state = getattr(app, "state", None)
    return await resolve_draft_sandbox_context(
        thread_store=get_run_context(request).thread_store,
        draft_service=getattr(app_state, "draft_service", None),
        owner_user_id=owner_user_id,
        thread_id=thread_id,
    )


def _apply_trusted_draft_sandbox_metadata(
    body: Any,
    snapshot: DraftSandboxContext | None,
) -> None:
    """Strip caller-forged sandbox fields and add server-derived values."""
    metadata = dict(getattr(body, "metadata", None) or {})
    for key in DRAFT_SANDBOX_METADATA_KEYS:
        metadata.pop(key, None)
    if snapshot is not None:
        metadata.update(
            draft_sandbox_thread_metadata(
                agent_id=snapshot.agent_id,
                draft_revision=snapshot.draft_revision,
            )
        )
    body.metadata = metadata


def resolve_agent_factory(assistant_id: str | None):
    """Resolve the agent factory callable from config.

    Custom agents are implemented as ``lead_agent`` + an ``agent_name``
    injected into ``configurable`` or ``context`` — see
    :func:`build_run_config`.  All ``assistant_id`` values therefore map to the
    same factory; the routing happens inside ``make_lead_agent`` when it reads
    ``cfg["agent_name"]``.
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent

    return make_lead_agent


def build_run_config(
    thread_id: str,
    request_config: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    *,
    assistant_id: str | None = None,
) -> dict[str, Any]:
    """Build a RunnableConfig dict for the agent.

    When *assistant_id* refers to a custom agent (anything other than
    ``"lead_agent"`` / ``None``), the name is forwarded as ``agent_name`` in
    whichever runtime options container is active: ``context`` for
    LangGraph >= 0.6.0 requests, otherwise ``configurable``.
    ``make_lead_agent`` reads this key to select the matching owner-scoped
    database draft (or that owner's read-only legacy files during migration)
    — without it the agent silently runs as the default lead agent.

    This mirrors the channel manager's ``_resolve_run_params`` logic so that
    the LangGraph Platform-compatible HTTP API and the IM channel path behave
    identically.
    """
    config: dict[str, Any] = {"recursion_limit": 100}
    if request_config:
        # LangGraph >= 0.6.0 introduced ``context`` as the preferred way to
        # pass thread-level data and rejects requests that include both
        # ``configurable`` and ``context``.  If the caller already sends
        # ``context``, honour it and skip our own ``configurable`` dict.
        if "context" in request_config:
            if "configurable" in request_config:
                logger.warning(
                    "build_run_config: client sent both 'context' and 'configurable'; preferring 'context' (LangGraph >= 0.6.0). thread_id=%s, caller_configurable keys=%s",
                    thread_id,
                    list(request_config.get("configurable", {}).keys()),
                )
            context_value = request_config["context"]
            if context_value is None:
                context = {}
            elif isinstance(context_value, Mapping):
                context = _sanitize_client_context(context_value)
            else:
                raise ValueError("request config 'context' must be a mapping or null.")
            if "thread_id" in context:
                context["thread_id"] = thread_id
            config["context"] = context
        else:
            configurable_value = request_config.get("configurable", {})
            if configurable_value is None:
                configurable_value = {}
            if not isinstance(configurable_value, Mapping):
                raise ValueError("request config 'configurable' must be a mapping or null.")
            configurable = _sanitize_client_configurable(configurable_value)
            configurable["thread_id"] = thread_id
            config["configurable"] = configurable
        for k, v in request_config.items():
            if k not in ("configurable", "context"):
                config[k] = v
    else:
        config["configurable"] = {"thread_id": thread_id}

    # Inject custom agent name when the caller specified a non-default assistant.
    # Honour an explicit agent_name in the active runtime options container.
    if assistant_id and assistant_id != _DEFAULT_ASSISTANT_ID:
        agent_slug = validate_agent_slug(assistant_id)
        if "configurable" in config:
            target = config["configurable"]
        elif "context" in config:
            target = config["context"]
        else:
            target = config.setdefault("configurable", {})
        if target is not None and "agent_name" not in target:
            target["agent_name"] = agent_slug
        if target is not None and "agent_name" in target:
            target["agent_name"] = validate_agent_slug(target["agent_name"])
        config.setdefault("run_name", resolve_root_run_name(config, agent_slug))
    if metadata:
        config.setdefault("metadata", {}).update(metadata)
    return config


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


async def start_run(
    body: Any,
    thread_id: str,
    request: Request,
    *,
    published_context: PublishedAgentContext | None = None,
    draft_sandbox_context: DraftSandboxContext | None = None,
    run_id: str | None = None,
) -> RunRecord:
    """Create a RunRecord and launch the background agent task.

    Published execution establishes its trusted owner and effective config
    before any model validation or Run/Thread persistence. The child worker
    inherits those ContextVars when it is created, while the caller's ambient
    context is restored before this function returns.

    Parameters
    ----------
    body : RunCreateRequest
        The validated request body (typed as Any to avoid circular import
        with the router module that defines the Pydantic model).
    thread_id : str
        Target thread.
    request : Request
        FastAPI request — used to retrieve singletons from ``app.state``.
    published_context : PublishedAgentContext | None
        Trusted immutable Release authority for published-Agent execution.
    run_id : str | None
        Optional preallocated Run id used to bind an idempotency claim before
        execution starts.
    """
    if published_context is not None and draft_sandbox_context is not None:
        raise ValueError("a Run cannot be both published and a draft sandbox")
    if published_context is None and draft_sandbox_context is None:
        draft_sandbox_context = await resolve_draft_sandbox_context_for_thread(request, thread_id)
    _apply_trusted_draft_sandbox_metadata(body, draft_sandbox_context)
    if published_context is not None:
        owner_user_id = published_context.owner_user_id
        with runtime_user_scope(owner_user_id):
            async with effective_app_config_scope(owner_user_id):
                return await _start_run_scoped(
                    body,
                    thread_id,
                    request,
                    published_context=published_context,
                    draft_sandbox_context=None,
                    run_id=run_id,
                )
    return await _start_run_scoped(
        body,
        thread_id,
        request,
        published_context=None,
        draft_sandbox_context=draft_sandbox_context,
        run_id=run_id,
    )


async def _start_run_scoped(
    body: Any,
    thread_id: str,
    request: Request,
    *,
    published_context: PublishedAgentContext | None,
    draft_sandbox_context: DraftSandboxContext | None,
    run_id: str | None,
) -> RunRecord:
    """Run the lifecycle after any trusted Published owner scope is active."""
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)

    disconnect = DisconnectMode.cancel if body.on_disconnect == "cancel" else DisconnectMode.continue_

    body_context = getattr(body, "context", None) or {}
    model_name = published_context.model_name if published_context is not None else draft_sandbox_context.model_name if draft_sandbox_context is not None else body_context.get("model_name")

    # Coerce non-string model_name values to str before truncation.
    if model_name is not None and not isinstance(model_name, str):
        model_name = str(model_name)

    # Validate model against the allowlist when a model_name is provided.
    if model_name:
        app_config = get_app_config()
        resolved = app_config.get_model_config(model_name)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail=f"Model {model_name!r} is not in the configured model allowlist",
            )

    try:
        record = await run_mgr.create_or_reject(
            thread_id,
            body.assistant_id,
            run_id=run_id,
            on_disconnect=disconnect,
            metadata=body.metadata or {},
            kwargs={"input": body.input, "config": body.config},
            multitask_strategy=body.multitask_strategy,
            model_name=model_name,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedStrategyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # Upsert thread metadata so the thread appears in /threads/search,
    # even for threads that were never explicitly created via POST /threads
    # (e.g. stateless runs).
    try:
        existing = await run_ctx.thread_store.get(thread_id)
        if existing is None:
            await run_ctx.thread_store.create(
                thread_id,
                assistant_id=body.assistant_id,
                metadata=body.metadata,
            )
        else:
            await run_ctx.thread_store.update_status(thread_id, "running")
    except asyncio.CancelledError:
        # The Run is already durable but no worker exists yet. Remove it before
        # propagating cancellation so retries cannot replay a forever-pending
        # record and published quota/idempotency cleanup can safely proceed.
        await asyncio.shield(run_mgr.discard_unstarted(record.run_id))
        raise
    except Exception:
        logger.warning("Failed to upsert thread_meta for %s (non-fatal)", sanitize_log_param(thread_id))

    agent_factory = resolve_agent_factory(body.assistant_id)
    graph_input = normalize_input(body.input)
    if published_context is not None:
        from deerflow.publishing.runtime_policy import build_published_run_config

        config = dict(
            build_published_run_config(
                published_context,
                base_config={"metadata": dict(body.metadata or {})},
            )
        )
        configurable = config.setdefault("configurable", {})
        configurable["thread_id"] = thread_id
        configurable["user_id"] = published_context.owner_user_id
    else:
        config = build_run_config(thread_id, body.config, body.metadata, assistant_id=body.assistant_id)

        # Merge DeerFlow-specific context overrides into both ``configurable`` and ``context``.
        # The ``context`` field is a custom extension for the langgraph-compat layer
        # that carries agent configuration (model_name, thinking_enabled, etc.).
        # Only agent/runtime-relevant keys are forwarded; unknown keys are ignored.
        merge_run_context_overrides(config, getattr(body, "context", None), thread_id=thread_id)
        inject_authenticated_user_context(config, request)
        if draft_sandbox_context is not None:
            _inject_draft_sandbox_context(config, draft_sandbox_context)

    stream_modes = normalize_stream_modes(body.stream_mode)

    runtime_context = config.get("context", {})
    user_id = published_context.owner_user_id if published_context is not None else runtime_context.get("user_id") if isinstance(runtime_context, dict) else None

    async def _run_with_effective_config() -> None:
        resolved_user_id = str(user_id) if user_id else None
        user_scope = runtime_user_scope(resolved_user_id) if resolved_user_id else nullcontext()
        with user_scope:
            async with effective_app_config_scope(resolved_user_id):
                await run_agent(
                    bridge,
                    run_mgr,
                    record,
                    ctx=run_ctx,
                    agent_factory=agent_factory,
                    graph_input=graph_input,
                    config=config,
                    stream_modes=stream_modes,
                    stream_subgraphs=body.stream_subgraphs,
                    interrupt_before=body.interrupt_before,
                    interrupt_after=body.interrupt_after,
                )

    task = asyncio.create_task(_run_with_effective_config())
    record.task = task

    # Title sync is handled by worker.py's finally block which reads the
    # title from the checkpoint and calls thread_store.update_display_name
    # after the run completes.

    return record


async def sse_consumer(
    bridge: StreamBridge,
    record: RunRecord,
    request: Request,
    run_mgr: RunManager,
):
    """Async generator that yields SSE frames from the bridge.

    The ``finally`` block implements ``on_disconnect`` semantics:
    - ``cancel``: abort the background task on client disconnect.
    - ``continue``: let the task run; events are discarded.
    """
    last_event_id = request.headers.get("Last-Event-ID")
    try:
        async for entry in bridge.subscribe(record.run_id, last_event_id=last_event_id):
            if await request.is_disconnected():
                break

            if entry is HEARTBEAT_SENTINEL:
                yield ": heartbeat\n\n"
                continue

            if entry is END_SENTINEL:
                yield format_sse("end", None, event_id=entry.id or None)
                return

            yield format_sse(entry.event, entry.data, event_id=entry.id or None)

    finally:
        if record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await run_mgr.cancel(record.run_id)
