"""Background agent execution.

Runs an agent graph inside an ``asyncio.Task``, publishing events to
a :class:`StreamBridge` as they are produced.

Uses ``graph.astream(stream_mode=[...])`` which gives correct full-state
snapshots for ``values`` mode, proper ``{node: writes}`` for ``updates``,
and ``(chunk, metadata)`` tuples for ``messages`` mode.

Note: ``events`` mode is not supported through the gateway — it requires
``graph.astream_events()`` which cannot simultaneously produce ``values``
snapshots.  The JS open-source LangGraph API server works around this via
internal checkpoint callbacks that are not exposed in the Python public API.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, cast

from langgraph.checkpoint.base import empty_checkpoint

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage, HumanMessage

from deerflow.config.app_config import AppConfig
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills.privacy import SkillContentRedactor
from deerflow.tracing import inject_langfuse_metadata

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus

logger = logging.getLogger(__name__)

_TOOL_CALL_SECTION_RE = re.compile(
    r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", re.DOTALL
)
_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\|tool_call_begin\|>.*?<\|tool_call_end\|>", re.DOTALL
)
_TOOL_CALL_ARG_RE = re.compile(
    r"<\|tool_call_argument_begin\|>.*?<\|tool_call_argument_end\|>", re.DOTALL
)
_TOOL_CALL_NAME_RE = re.compile(
    r"<\|tool_call_name_begin\|>([^<]+)<\|tool_call_name_end\|>"
)
_TOOL_CALL_NAME_JSON_RE = re.compile(
    r'"name"\s*:\s*"([^"]+)"'
)
_SINGLE_MARKER_RE = re.compile(r"<\|[^>]+\|>")
_SYSTEM_REMINDER_RE = re.compile(r"<system-rem>.*?</system-rem>", re.DOTALL)


def _extract_tool_names_from_text(text: str) -> list[str]:
    """Extract tool call names from raw model text before cleaning."""
    if not text:
        return []
    names: list[str] = []
    # Method 1: <|tool_call_name_begin|>tool_name<|tool_call_name_end|>
    for m in _TOOL_CALL_NAME_RE.finditer(text):
        name = m.group(1).strip()
        if name and name != "task":
            names.append(name)
    # Method 2: JSON-like "name": "tool_name" inside tool call blocks
    for block in _TOOL_CALL_BLOCK_RE.findall(text):
        for m in _TOOL_CALL_NAME_JSON_RE.finditer(block):
            name = m.group(1).strip()
            if name and name != "task" and name not in names:
                names.append(name)
    return names


def _tool_call_display_name(tool_call: Any) -> str | None:
    """Return a user-facing name for a tool_call.

    For the internal ``task`` subagent tool we surface the human-readable
    ``description`` when available, falling back to ``subagent_type``, so
    users can see what each call actually did.
    """
    name = getattr(tool_call, "name", None) or (tool_call.get("name") if isinstance(tool_call, dict) else "")
    if not isinstance(name, str) or not name:
        return None
    if name != "task":
        return name
    args: Any = getattr(tool_call, "args", None) or (tool_call.get("args") if isinstance(tool_call, dict) else {})
    if not isinstance(args, dict):
        return name
    description = (args.get("description") or "").strip() if isinstance(args.get("description"), str) else ""
    subagent_type = (args.get("subagent_type") or "").strip() if isinstance(args.get("subagent_type"), str) else ""
    return description or subagent_type or name


def _clean_model_text(text: str) -> str:
    if not text:
        return text
    text = _SYSTEM_REMINDER_RE.sub("", text)
    text = _TOOL_CALL_SECTION_RE.sub("", text)
    text = _TOOL_CALL_BLOCK_RE.sub("", text)
    text = _TOOL_CALL_ARG_RE.sub("", text)
    text = _SINGLE_MARKER_RE.sub("", text)
    text = text.strip()
    return text


def _clean_aimessage_content(obj: Any) -> Any:
    """Recursively strip raw tool-call markers from AIMessage/AIMessageChunk content."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return _clean_model_text(obj)
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k == "content" and isinstance(v, str):
                cleaned[k] = _clean_model_text(v)
            elif k == "content" and isinstance(v, list):
                cleaned[k] = [
                    _clean_model_text(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                cleaned[k] = _clean_aimessage_content(v)
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_clean_aimessage_content(item) for item in obj]
    if hasattr(obj, "content"):
        try:
            content = obj.content
            # Extract tool names BEFORE cleaning (from raw text markers)
            raw_text = ""
            if isinstance(content, str):
                raw_text = content
            elif isinstance(content, list):
                raw_text = "".join(item for item in content if isinstance(item, str))
            extracted_names = _extract_tool_names_from_text(raw_text)

            if isinstance(content, str):
                obj.content = _clean_model_text(content)
            elif isinstance(content, list):
                obj.content = [
                    _clean_model_text(item) if isinstance(item, str) else item
                    for item in content
                ]
            if isinstance(obj.content, str):
                cleaned_content = obj.content
            elif isinstance(obj.content, list):
                cleaned_content = "".join(
                    item for item in obj.content if isinstance(item, str)
                )
            else:
                cleaned_content = ""
            if not cleaned_content.strip():
                tc_names = list(extracted_names)
                # Also try tool_calls/tool_call_chunks attributes
                if hasattr(obj, "tool_calls") and obj.tool_calls:
                    for tc in obj.tool_calls:
                        name = _tool_call_display_name(tc)
                        if name and name not in tc_names:
                            tc_names.append(name)
                if hasattr(obj, "tool_call_chunks") and obj.tool_call_chunks:
                    for tcc in obj.tool_call_chunks:
                        name = _tool_call_display_name(tcc)
                        if name and name not in tc_names:
                            tc_names.append(name)
                if tc_names:
                    obj.content = "[工具调用: " + ", ".join(tc_names) + "]"
                else:
                    obj.content = "[工具调用已省略]"
        except Exception:
            pass
    if hasattr(obj, "additional_kwargs") and isinstance(obj.additional_kwargs, dict):
        if "reasoning_content" in obj.additional_kwargs:
            rc = obj.additional_kwargs["reasoning_content"]
            if isinstance(rc, str):
                obj.additional_kwargs["reasoning_content"] = _clean_model_text(rc)
    return obj


_TOOL_OMISSION_MARKER = "[工具调用已省略]"

# run_id -> {ai_msg_id: [tool_names]} — shared between values-stream and messages-stream
_VALUES_AI_TOOL_CACHE: dict[str, dict[str, list[str]]] = {}


def _cache_ai_tool_names_from_values(run_id: str, state_messages: Any) -> None:
    """Scan a channel-values messages list and record AI message id → tool names.

    Supports both LangChain objects (before serialization) and plain dicts
    (after serialization).  The cache is consumed by
    :func:`_enrich_tool_call_content_in_serialized` to back-fill the tool-name
    markers into streamed AIMessageChunks that do not carry tool_calls.
    """
    if not isinstance(state_messages, (list, tuple)):
        return
    cache = _VALUES_AI_TOOL_CACHE.setdefault(run_id, {})
    for m in state_messages:
        # Determine message type — dict or object.
        if isinstance(m, dict):
            msg_type = m.get("type", "")
            msg_id = m.get("id")
            tcs = m.get("tool_calls")
            tccs = m.get("tool_call_chunks")
        else:
            msg_type = getattr(m, "type", "")
            msg_id = getattr(m, "id", None)
            if not isinstance(msg_id, str):
                msg_id = getattr(m, "lc_id", None) or f"__objid_{id(m)}"
            tcs = getattr(m, "tool_calls", None)
            tccs = getattr(m, "tool_call_chunks", None)
        if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
            continue
        names: list[str] = []
        if isinstance(tcs, (list, tuple)):
            for tc in tcs:
                n = _tool_call_display_name(tc)
                if n:
                    names.append(n)
        if isinstance(tccs, (list, tuple)):
            for tcc in tccs:
                n = _tool_call_display_name(tcc)
                if n and n not in names:
                    names.append(n)
        if names:
            cache[str(msg_id)] = names


def _drop_values_cache(run_id: str) -> None:
    _VALUES_AI_TOOL_CACHE.pop(run_id, None)


def _patch_one_message_content(message: Any, names: list[str], existing_text: str = "") -> str:
    """Return the replacement content string for an AI message.

    If ``existing_text`` already contains named markers like ``[工具调用: a, b]``
    we keep them untouched.  Otherwise we either insert a named marker from
    ``names`` or the generic ``[工具调用已省略]`` placeholder.
    """
    if not names:
        if not existing_text.strip() or existing_text.strip() == _TOOL_OMISSION_MARKER:
            return _TOOL_OMISSION_MARKER
        return existing_text
    marker = "[工具调用: " + ", ".join(names) + "]"
    # If content already has this specific named marker, don't duplicate.
    if marker in existing_text:
        return existing_text
    # Replace any generic omission marker or empty content with the named one.
    stripped = existing_text.strip()
    if not stripped or stripped == _TOOL_OMISSION_MARKER:
        return marker
    # Fallback: prepend the marker so detectToolOmissions() still parses it.
    return marker + "\n" + existing_text


def _cache_and_patch_values_messages(run_id: str, messages: list[Any]) -> None:
    """Fill the values cache AND in-place patch each AI message's content.

    Works on both raw LangChain message objects (``chunk`` in worker) and
    plain dict messages (serialized responses).  After this call the messages
    list can be redacted/serialized/published and clients will see the
    tool-name markers.
    """
    if not isinstance(messages, list):
        return
    cache = _VALUES_AI_TOOL_CACHE.setdefault(run_id, {})
    for idx, m in enumerate(messages):
        if isinstance(m, dict):
            msg_type = m.get("type", "")
            if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            msg_id = m.get("id") or f"__idx_{idx}"
            tcs = m.get("tool_calls")
            tccs = m.get("tool_call_chunks")
            content = m.get("content", "") if isinstance(m.get("content", ""), str) else ""
        else:
            msg_type = getattr(m, "type", "")
            if msg_type not in ("ai", "AIMessage", "AIMessageChunk"):
                continue
            msg_id = getattr(m, "id", None)
            if not isinstance(msg_id, str):
                msg_id = getattr(m, "lc_id", None) or f"__objid_{id(m)}"
            tcs = getattr(m, "tool_calls", None)
            tccs = getattr(m, "tool_call_chunks", None)
            content_attr = getattr(m, "content", "")
            content = content_attr if isinstance(content_attr, str) else ""
        names: list[str] = []
        if isinstance(tcs, (list, tuple)):
            for tc in tcs:
                n = _tool_call_display_name(tc)
                if n:
                    names.append(n)
        if isinstance(tccs, (list, tuple)):
            for tcc in tccs:
                n = _tool_call_display_name(tcc)
                if n and n not in names:
                    names.append(n)
        if names:
            cache[str(msg_id)] = names
        new_content = _patch_one_message_content(m, names, content)
        # Only mutate if actually changed to avoid spurious downstream updates.
        if new_content != content:
            if isinstance(m, dict):
                m["content"] = new_content
            else:
                try:
                    m.content = new_content
                except Exception:
                    pass


def _enrich_tool_call_content_in_serialized(obj: Any, *, run_id: str = "") -> None:
    """After serialization, patch content from [工具调用已省略] → [工具调用: name1, name2].

    Works on the serialized ``[chunk_dict, metadata_dict]`` tuple produced
    by :func:`serialize` with ``mode="messages"``.
    """
    if not isinstance(obj, (list, tuple)) or len(obj) < 1:
        return
    chunk_dict = obj[0]
    if not isinstance(chunk_dict, dict):
        return
    content = chunk_dict.get("content", "")
    if not isinstance(content, str):
        return

    tool_calls = chunk_dict.get("tool_calls")
    tool_call_chunks = chunk_dict.get("tool_call_chunks")

    needs_patch = (
        content == ""
        or content == _TOOL_OMISSION_MARKER
        or "工具调用" in content
    )
    if not needs_patch:
        return

    names: list[str] = []
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            name = _tool_call_display_name(tc)
            if name and name not in names:
                names.append(name)
    if isinstance(tool_call_chunks, list):
        for tcc in tool_call_chunks:
            if not isinstance(tcc, dict):
                continue
            name = _tool_call_display_name(tcc)
            if name and name not in names:
                names.append(name)

    # Fallback: streamed AIMessageChunk has no tool_calls/tool_call_chunks,
    # but the values-stream may have already cached the full AI message's
    # tool_calls keyed by message id (or by last-known position).
    if not names and run_id:
        msg_id = chunk_dict.get("id")
        cache = _VALUES_AI_TOOL_CACHE.get(run_id) or {}
        if isinstance(msg_id, str) and msg_id in cache:
            names = list(cache[msg_id])
        if not names and cache:
            # Last-resort heuristic: values stream updates arrive *after* the
            # corresponding message chunks. The messages list in state is in
            # order, so the cache dict in CPython preserves insertion order.
            # Take the most recently inserted entry (last AI message).
            last_key = next(reversed(cache))
            names = list(cache[last_key])

    if names:
        new_content = "[工具调用: " + ", ".join(names) + "]"
        chunk_dict["content"] = new_content


def _make_skill_content_redactor(
    app_config: AppConfig | None,
    runtime_context: Any | None = None,
) -> SkillContentRedactor:
    return SkillContentRedactor.from_run_context(
        app_config=app_config,
        runtime_context=runtime_context if isinstance(runtime_context, dict) else None,
        boundary="runtime",
    )


def _restrict_unsafe_tracing_callbacks(config: dict[str, Any]) -> None:
    """Keep only callbacks that explicitly promise Skill-safe payload handling.

    A Gateway run can load an enabled Skill at any point.  Provider callbacks
    receive raw prompts and ToolMessages before user-boundary redaction, so
    callbacks are fail-closed unless they opt into the safety contract.
    """

    callbacks = config.get("callbacks")
    if callbacks is None:
        return
    materialized = list(callbacks) if not isinstance(callbacks, list) else callbacks
    config["callbacks"] = [callback for callback in materialized if getattr(callback, "deerflow_skill_content_safe", False) is True]


def _log_cleanup_exception(task: asyncio.Task, run_id: str, logger: logging.Logger) -> None:
    """Log an exception raised by the bridge cleanup task."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Bridge cleanup failed for run %s",
            run_id,
            exc_info=exc,
        )


# Valid stream_mode values for LangGraph's graph.astream()
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}


def _build_runtime_context(
    thread_id: str,
    run_id: str,
    caller_context: Any | None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """Build the dict that becomes ``ToolRuntime.context`` for the run.

    Always includes ``thread_id`` and ``run_id``. Additional keys from the caller's
    ``config['context']`` (e.g. ``agent_name`` for the bootstrap flow — issue #2677)
    are merged in but never override ``thread_id``/``run_id``. The resolved
    ``AppConfig`` is added by the worker so tools can consume it without ambient
    global lookups.

    langgraph 1.1+ surfaces this as ``runtime.context`` via the parent runtime stored
    under ``config['configurable']['__pregel_runtime']`` — see
    ``langgraph.pregel.main`` where ``parent_runtime.merge(...)`` is invoked.
    """
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
    if isinstance(caller_context, dict):
        for key, value in caller_context.items():
            runtime_ctx.setdefault(key, value)
    if app_config is not None:
        runtime_ctx["app_config"] = app_config
    return runtime_ctx


@dataclass(frozen=True)
class RunContext:
    """Infrastructure dependencies for a single agent run.

    Groups checkpointer, store, and persistence-related singletons so that
    ``run_agent`` (and any future callers) receive one object instead of a
    growing list of keyword arguments.
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)


def _install_runtime_context(config: dict, runtime_context: dict[str, Any]) -> None:
    existing_context = config.get("context")
    if isinstance(existing_context, dict):
        existing_context.setdefault("thread_id", runtime_context["thread_id"])
        existing_context.setdefault("run_id", runtime_context["run_id"])
        if "app_config" in runtime_context:
            existing_context["app_config"] = runtime_context["app_config"]
        return

    config["context"] = dict(runtime_context)


def _get_runtime_config(config: dict) -> dict[str, Any]:
    """Merge legacy configurable options with LangGraph runtime context."""
    cfg = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        cfg.update(context)
    return cfg


def _should_track_run_tokens(record: RunRecord, run_events_config: Any | None) -> bool:
    """Keep published-run accounting mandatory even when event tracking is off."""
    if bool((record.metadata or {}).get("published_agent")):
        return True
    return bool(getattr(run_events_config, "track_token_usage", True))


def _current_turn_has_attachment(graph_input: dict) -> bool:
    messages = graph_input.get("messages")
    if not isinstance(messages, list):
        return False

    for message in messages:
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if additional_kwargs is None and isinstance(message, dict):
            additional_kwargs = message.get("additional_kwargs")
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("files"):
            return True

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") not in (None, "text"):
                    return True
    return False


def _current_turn_has_tool_context(graph_input: dict) -> bool:
    messages = graph_input.get("messages")
    if not isinstance(messages, list):
        return False

    for message in messages:
        if getattr(message, "type", None) == "tool":
            return True
        if isinstance(message, dict) and message.get("type") == "tool":
            return True
        if getattr(message, "tool_calls", None) or getattr(message, "invalid_tool_calls", None):
            return True
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if additional_kwargs is None and isinstance(message, dict):
            additional_kwargs = message.get("additional_kwargs")
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
            return True
    return False


def _thread_has_historical_uploads(thread_id: str) -> bool:
    try:
        from deerflow.config.paths import get_paths

        uploads_dir = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
        return uploads_dir.exists() and any(path.is_file() for path in uploads_dir.iterdir())
    except Exception:
        logger.debug("Failed to inspect uploads for flash fast path", exc_info=True)
        return True


def _should_use_flash_direct_path(
    *,
    graph_input: dict,
    config: dict,
    thread_id: str,
    interrupt_before: list[str] | Literal["*"] | None,
    interrupt_after: list[str] | Literal["*"] | None,
) -> bool:
    cfg = _get_runtime_config(config)
    if cfg.get("is_bootstrap"):
        return False
    if cfg.get("skill_name"):
        return False
    if cfg.get("connector_ids"):
        return False
    if cfg.get("external_allowed_skills") is not None:
        return False
    if interrupt_before or interrupt_after:
        return False
    if cfg.get("mode") != "flash":
        return False
    if cfg.get("is_plan_mode", False) or cfg.get("subagent_enabled", False):
        return False
    if _current_turn_has_attachment(graph_input):
        return False
    if _current_turn_has_tool_context(graph_input):
        return False
    if _thread_has_historical_uploads(thread_id):
        return False
    return True


def _message_has_tool_call_request(message: Any) -> bool:
    if getattr(message, "tool_calls", None):
        return True
    if getattr(message, "invalid_tool_calls", None):
        return True
    if getattr(message, "tool_call_chunks", None):
        return True
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
        return True
    return False


def _queue_flash_memory_capture(
    *,
    thread_id: str,
    messages: list[Any],
    app_config: AppConfig,
) -> None:
    """Queue a completed flash-direct conversation for the shared memory pipeline."""
    memory_config = getattr(app_config, "memory", None)
    if memory_config is None or not memory_config.enabled:
        return

    from deerflow.agents.memory.message_processing import filter_messages_for_memory
    from deerflow.agents.memory.queue import get_memory_queue

    filtered_messages = filter_messages_for_memory(messages)
    has_user = any(getattr(message, "type", None) == "human" for message in filtered_messages)
    has_assistant = any(getattr(message, "type", None) == "ai" for message in filtered_messages)
    if not has_user or not has_assistant:
        return

    get_memory_queue().add(
        thread_id=thread_id,
        messages=filtered_messages,
        user_id=get_effective_user_id(),
    )


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # Some callable instances are unhashable; fall back to a direct check.
        return _compute_agent_factory_supports_app_config(agent_factory)


def _normalize_lg_modes(requested_modes: set[str]) -> list[str]:
    lg_modes: list[str] = []
    for m in requested_modes:
        if m == "messages-tuple":
            lg_modes.append("messages")
        elif m == "events":
            continue
        elif m in _VALID_LG_MODES:
            lg_modes.append(m)
    if not lg_modes:
        lg_modes = ["values"]

    seen: set[str] = set()
    deduped: list[str] = []
    for m in lg_modes:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    return deduped


def _coerce_messages(raw_messages: Any) -> list[BaseMessage]:
    from langchain_core.messages import BaseMessage
    from langchain_core.messages.utils import convert_to_messages

    if not raw_messages:
        return []
    if not isinstance(raw_messages, list):
        raw_messages = [raw_messages]

    messages: list[BaseMessage] = []
    for message in raw_messages:
        if isinstance(message, BaseMessage):
            messages.append(message)
        else:
            try:
                converted = convert_to_messages([message])
            except (TypeError, ValueError, NotImplementedError):
                logger.debug("Skipping non-coercible message in flash direct path: %r", message, exc_info=True)
                continue
            messages.extend(converted)
    return messages


def _checkpoint_channel_values(ckpt_tuple: Any | None) -> dict[str, Any]:
    checkpoint = getattr(ckpt_tuple, "checkpoint", None) if ckpt_tuple is not None else None
    if not isinstance(checkpoint, dict):
        return {}
    values = checkpoint.get("channel_values")
    return dict(values) if isinstance(values, dict) else {}


def _checkpoint_channel_versions(ckpt_tuple: Any | None) -> dict[str, Any]:
    checkpoint = getattr(ckpt_tuple, "checkpoint", None) if ckpt_tuple is not None else None
    if not isinstance(checkpoint, dict):
        return {}
    versions = checkpoint.get("channel_versions")
    return dict(versions) if isinstance(versions, dict) else {}


def _extract_fallback_title(messages: list[Any]) -> str | None:
    """Return a short fallback title from the first genuine human message."""
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type != "human":
            continue

        # Skip dynamic-context reminders injected by DynamicContextMiddleware.
        # They carry an additional_kwargs flag; if the object is a plain dict,
        # fall back to a content-heuristic.
        additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
        if isinstance(additional_kwargs, dict) and additional_kwargs.get("dynamic_context_reminder"):
            continue

        content = getattr(msg, "content", "") or ""
        if isinstance(content, list):
            # Extract text from multimodal content blocks
            texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            content = " ".join(texts)
        elif not isinstance(content, str):
            content = str(content)

        text = content.strip().replace("\n", " ")
        if not text:
            continue

        max_len = 50
        if len(text) > max_len:
            return text[:max_len].rstrip() + "..."
        return text

    return None


def _next_channel_version(checkpointer: Any, current: Any) -> Any:
    get_next_version = getattr(checkpointer, "get_next_version", None)
    if callable(get_next_version):
        return get_next_version(current, None)
    if isinstance(current, int):
        return current + 1
    return 1


def _flash_direct_checkpoint_metadata(ckpt_tuple: Any | None) -> dict[str, Any]:
    """Build LangGraph-compatible checkpoint metadata for flash-direct writes.

    LangGraph resumes from the latest checkpoint via
    ``checkpoint_metadata["step"] + 1`` (see ``AsyncPregelLoop.__aenter__``).
    Flash-direct bypasses the graph, so we must advance ``step`` ourselves or
    the next full-graph run (e.g. switching from flash to pro) raises
    ``KeyError('step')``.
    """
    previous_metadata = getattr(ckpt_tuple, "metadata", None) if ckpt_tuple is not None else None
    if not isinstance(previous_metadata, dict):
        previous_metadata = {}

    parents = previous_metadata.get("parents")
    if not isinstance(parents, dict):
        parents = {}

    return {
        "source": "flash_direct",
        "step": previous_metadata.get("step", -1) + 1,
        "parents": parents,
    }


async def _persist_flash_direct_checkpoint(
    *,
    checkpointer: Any | None,
    thread_id: str,
    ckpt_tuple: Any | None,
    channel_values: dict[str, Any],
    changed_channels: set[str],
) -> None:
    if checkpointer is None:
        return

    previous_checkpoint = getattr(ckpt_tuple, "checkpoint", None) if ckpt_tuple is not None else None
    previous_versions = _checkpoint_channel_versions(ckpt_tuple)
    checkpoint = empty_checkpoint()
    if isinstance(previous_checkpoint, dict):
        checkpoint["versions_seen"] = copy.deepcopy(previous_checkpoint.get("versions_seen", {}))
        checkpoint["pending_sends"] = copy.deepcopy(previous_checkpoint.get("pending_sends", []))

    new_versions: dict[str, Any] = {}
    channel_versions = dict(previous_versions)
    for channel in changed_channels:
        next_version = _next_channel_version(checkpointer, previous_versions.get(channel))
        channel_versions[channel] = next_version
        new_versions[channel] = next_version

    checkpoint["channel_values"] = channel_values
    checkpoint["channel_versions"] = channel_versions
    checkpoint["updated_channels"] = sorted(changed_channels)

    base_config = getattr(ckpt_tuple, "config", None) if ckpt_tuple is not None else None
    if not isinstance(base_config, dict):
        base_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    else:
        base_config = copy.deepcopy(base_config)
        base_config.setdefault("configurable", {})
        base_config["configurable"].setdefault("thread_id", thread_id)
        base_config["configurable"].setdefault("checkpoint_ns", "")

    await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        base_config,
        checkpoint,
        _flash_direct_checkpoint_metadata(ckpt_tuple),
        new_versions,
    )


async def _run_flash_direct_model(
    *,
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    ctx: RunContext,
    graph_input: dict,
    config: dict,
    runnable_config: Any,
    requested_modes: set[str],
    stream_subgraphs: bool,
    checkpointer: Any | None,
    pre_run_checkpoint_tuple: Any | None,
    redactor: SkillContentRedactor,
) -> bool:
    from langchain_core.messages import AIMessage, SystemMessage, message_chunk_to_message

    from deerflow.agents.lead_agent.agent import _resolve_available_skill_names, _resolve_model_name
    from deerflow.agents.lead_agent.prompt import apply_prompt_template
    from deerflow.config.agents_config import validate_agent_name
    from deerflow.config.app_config import get_app_config
    from deerflow.models.factory import get_cached_chat_model
    from deerflow.publishing.runtime_loader import (
        resolve_runtime_agent_config,
        resolve_runtime_agent_instructions,
    )

    cfg = _get_runtime_config(config)
    app_config = ctx.app_config or get_app_config()

    agent_name = validate_agent_name(cfg.get("agent_name"))
    agent_config = resolve_runtime_agent_config(cfg, agent_name=agent_name)
    agent_instructions = resolve_runtime_agent_instructions(cfg)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    agent_model_name = agent_config.model if agent_config and agent_config.model else None
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=app_config)
    model_config = app_config.get_model_config(model_name)
    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if record.model_name is not None and model_name != record.model_name:
        await run_manager.update_model_name(record.run_id, model_name)

    existing_values = _checkpoint_channel_values(pre_run_checkpoint_tuple)
    historical_messages = _coerce_messages(existing_values.get("messages"))
    input_messages = _coerce_messages(graph_input.get("messages"))
    conversation_messages = [*historical_messages, *input_messages]

    cleaned_conversation = []
    for msg in conversation_messages:
        msg_type = getattr(msg, "type", "")
        # Only clean AI and system messages, not human messages
        if hasattr(msg, "content") and msg_type in ("ai", "system", "tool"):
            msg = _clean_aimessage_content(msg)
        cleaned_conversation.append(msg)
    conversation_messages = cleaned_conversation

    system_prompt = apply_prompt_template(
        subagent_enabled=False,
        max_concurrent_subagents=cfg.get("max_concurrent_subagents", 3),
        agent_name=agent_name,
        available_skills=_resolve_available_skill_names(
            agent_config,
            False,
            cfg.get("skill_name"),
            app_config=app_config,
            external_allowed_skills=cfg.get("external_allowed_skills"),
        ),
        app_config=app_config,
        agent_instructions=agent_instructions,
    )
    model_messages = [SystemMessage(content=system_prompt), *conversation_messages]

    model = get_cached_chat_model(
        name=model_name,
        thinking_enabled=False,
        reasoning_effort=cfg.get("reasoning_effort"),
        app_config=app_config,
    ).with_config(tags=["lead_agent"])

    lg_modes = _normalize_lg_modes(requested_modes)
    logger.info("Run %s: flash direct streaming with modes %s (requested: %s)", record.run_id, lg_modes, requested_modes)

    accumulated_chunk: Any | None = None
    streamed_chunks: list[Any] = []
    metadata = {"langgraph_node": "agent", "tags": ["lead_agent"], "flash_direct": True}
    async for chunk in model.astream(model_messages, config=runnable_config):
        if record.abort_event.is_set():
            logger.info("Run %s abort requested - stopping flash direct stream", record.run_id)
            break
        accumulated_chunk = chunk if accumulated_chunk is None else accumulated_chunk + chunk
        streamed_chunks.append(chunk)

    final_ai_message = message_chunk_to_message(accumulated_chunk) if accumulated_chunk is not None else AIMessage(content="")
    if not record.abort_event.is_set() and _message_has_tool_call_request(final_ai_message):
        logger.info("Run %s: flash direct model requested tool calls; falling back to full agent graph", record.run_id)
        return False

    if "messages" in lg_modes:
        for chunk in streamed_chunks:
            cleaned_chunk = _clean_aimessage_content(chunk)
            safe_chunk = redactor.redact_stream_payload(
                "messages",
                (cleaned_chunk, metadata),
                run_id=record.run_id,
            )
            serialized = serialize(safe_chunk, mode="messages")
            _enrich_tool_call_content_in_serialized(serialized, run_id=record.run_id)
            await bridge.publish(record.run_id, _lg_mode_to_sse_event("messages"), serialized)

    final_ai_message = _clean_aimessage_content(final_ai_message)
    final_messages = [*conversation_messages, final_ai_message]
    channel_values = {
        **existing_values,
        "messages": final_messages,
        "artifacts": existing_values.get("artifacts") or [],
    }

    if "values" in lg_modes:
        # Patch final AI message content so the banner shows tool names.
        _cache_and_patch_values_messages(record.run_id, final_messages)
        channel_values["messages"] = final_messages
        safe_values = redactor.redact_stream_payload("values", channel_values, run_id=record.run_id)
        await bridge.publish(record.run_id, "values", serialize(safe_values, mode="values"))

    if not record.abort_event.is_set():
        await _persist_flash_direct_checkpoint(
            checkpointer=checkpointer,
            thread_id=record.thread_id,
            ckpt_tuple=pre_run_checkpoint_tuple,
            channel_values=channel_values,
            changed_channels={"messages", "artifacts"},
        )
        _queue_flash_memory_capture(
            thread_id=record.thread_id,
            messages=final_messages,
            app_config=app_config,
        )

    if stream_subgraphs:
        logger.debug("Run %s: flash direct path ignores stream_subgraphs because no graph/subgraphs are created", record.run_id)
    _drop_values_cache(record.run_id)
    return True


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: dict,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """Execute an agent in the background, publishing events to *bridge*."""

    # Unpack infrastructure dependencies from RunContext.
    checkpointer = ctx.checkpointer
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store

    run_id = record.run_id
    thread_id = record.thread_id
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    pre_run_checkpoint_tuple: Any | None = None
    snapshot_capture_failed = False
    redactor = _make_skill_content_redactor(ctx.app_config, config.get("context"))

    journal = None

    # Track whether "events" was requested but skipped
    if "events" in requested_modes:
        logger.info(
            "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
            run_id,
        )

    try:
        # Initialize RunJournal + write human_message event.
        # These are inside the try block so any exception (e.g. a DB
        # error writing the event) flows through the except/finally
        # path that publishes an "end" event to the SSE bridge —
        # otherwise a failure here would leave the stream hanging
        # with no terminator.
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                track_token_usage=_should_track_run_tokens(record, run_events_config),
                progress_reporter=lambda snapshot: run_manager.update_run_progress(run_id, **snapshot),
                redactor=redactor,
            )

        # 1. Mark running
        await run_manager.set_status(run_id, RunStatus.running)

        # Snapshot the latest pre-run checkpoint so rollback can restore it.
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                pre_run_checkpoint_tuple = ckpt_tuple
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }

                    # Clean corrupted messages in checkpoint to prevent 400 errors
                    channel_values = getattr(ckpt_tuple, "checkpoint", {}).get("channel_values", {})
                    messages = channel_values.get("messages") if isinstance(channel_values, dict) else None
                    if messages and isinstance(messages, list):
                        needs_clean = False
                        cleaned_messages = []
                        for m in messages:
                            msg_type = getattr(m, "type", "")
                            # Only clean AI and system messages, not human messages
                            if hasattr(m, "content") and msg_type in ("ai", "system", "tool"):
                                original_content = m.content
                                is_empty_after_clean = False
                                if isinstance(original_content, str):
                                    # Extract tool names from raw text before cleaning
                                    ext_names = _extract_tool_names_from_text(original_content)
                                    cleaned_text = _clean_model_text(original_content)
                                    needs_patch = (
                                        cleaned_text != original_content
                                        or (not cleaned_text and original_content.strip())
                                        or original_content.strip() in ("[工具调用已省略]", "")
                                        or "工具调用" in original_content
                                    )
                                    if needs_patch:
                                        tc_names = list(ext_names)
                                        if hasattr(m, "tool_calls") and m.tool_calls:
                                            for tc in m.tool_calls:
                                                name = _tool_call_display_name(tc)
                                                if name and name not in tc_names:
                                                    tc_names.append(name)
                                        if hasattr(m, "tool_call_chunks") and m.tool_call_chunks:
                                            for tcc in m.tool_call_chunks:
                                                name = _tool_call_display_name(tcc)
                                                if name and name not in tc_names:
                                                    tc_names.append(name)
                                        if tc_names:
                                            m.content = cleaned_text or ("[工具调用: " + ", ".join(tc_names) + "]")
                                        else:
                                            m.content = cleaned_text or "[工具调用已省略]"
                                        needs_clean = True
                                        is_empty_after_clean = not cleaned_text
                                if hasattr(m, "additional_kwargs") and isinstance(m.additional_kwargs, dict):
                                    rc = m.additional_kwargs.get("reasoning_content")
                                    if isinstance(rc, str):
                                        cleaned_rc = _clean_model_text(rc)
                                        if cleaned_rc != rc:
                                            m.additional_kwargs["reasoning_content"] = cleaned_rc
                                            needs_clean = True
                                if is_empty_after_clean and hasattr(m, "tool_calls") and m.tool_calls:
                                    pass
                                elif is_empty_after_clean:
                                    needs_clean = True
                            cleaned_messages.append(m)

                        if needs_clean:
                            channel_values["messages"] = cleaned_messages
                            checkpoint_data = getattr(ckpt_tuple, "checkpoint", {})
                            checkpoint_data["channel_values"] = channel_values
                            metadata = getattr(ckpt_tuple, "metadata", {})
                            try:
                                await checkpointer.aput(
                                    config_for_check,
                                    checkpoint_data,
                                    metadata,
                                    getattr(ckpt_tuple, "pending_writes", {}) or {},
                                )
                            except Exception:
                                logger.warning("Failed to write cleaned checkpoint for run %s", run_id, exc_info=True)
            except Exception:
                snapshot_capture_failed = True
                logger.warning("Could not capture pre-run checkpoint snapshot for run %s", run_id, exc_info=True)

        # 2. Publish metadata — useStream needs both run_id AND thread_id
        await bridge.publish(
            run_id,
            "metadata",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. Build the agent
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # Inject runtime context so middlewares and tools (via ToolRuntime.context) can
        # access thread-level data. langgraph-cli does this automatically; we must do it
        # manually here because we drive the graph through ``agent.astream(config=...)``
        # without passing the official ``context=`` parameter.
        runtime_ctx = _build_runtime_context(thread_id, run_id, config.get("context"), ctx.app_config)
        # Expose the run-scoped journal under a sentinel key so middleware can
        # write audit events (e.g. SafetyFinishReasonMiddleware recording
        # suppressed tool calls). Double-underscore prefix marks it as a
        # runtime-internal channel; user code must not depend on the key name.
        if journal is not None:
            runtime_ctx["__run_journal"] = journal
        _install_runtime_context(config, runtime_ctx)
        from deerflow.publishing.runtime_loader import hydrate_runtime_agent_config

        await hydrate_runtime_agent_config(
            config,
            owner_user_id=str(runtime_ctx.get("user_id") or get_effective_user_id()),
        )
        runtime = Runtime(context=cast(Any, runtime_ctx), store=store)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # Inject RunJournal as a LangChain callback handler.
        # on_llm_end captures token usage; on_chain_start/end captures lifecycle.
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # Inject Langfuse trace-attribute metadata so the langchain CallbackHandler
        # can lift session_id / user_id / trace_name / tags onto the root trace.
        # Shared helper with ``DeerFlowClient.stream`` so both entry points stay
        # in sync; caller-provided metadata wins via setdefault inside the helper.
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=record.assistant_id,
            model_name=record.model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        # Resolve after runtime context installation so context/configurable reflect
        # the agent name that this run will actually execute.
        config.setdefault("run_name", resolve_root_run_name(config, record.assistant_id))
        config.setdefault("configurable", {})["__agent_graph_runtime_key"] = (
            id(checkpointer) if checkpointer is not None else None,
            id(store) if store is not None else None,
            tuple(interrupt_before or ()),
            tuple(interrupt_after or ()),
        )
        runnable_config = RunnableConfig(**config)
        _restrict_unsafe_tracing_callbacks(runnable_config)
        if _should_use_flash_direct_path(
            graph_input=graph_input,
            config=config,
            thread_id=thread_id,
            interrupt_before=interrupt_before,
            interrupt_after=interrupt_after,
        ):
            flash_direct_handled = await _run_flash_direct_model(
                bridge=bridge,
                run_manager=run_manager,
                record=record,
                ctx=ctx,
                graph_input=graph_input,
                config=config,
                runnable_config=runnable_config,
                requested_modes=requested_modes,
                stream_subgraphs=stream_subgraphs,
                checkpointer=checkpointer,
                pre_run_checkpoint_tuple=pre_run_checkpoint_tuple,
                redactor=redactor,
            )
            if flash_direct_handled:
                if record.abort_event.is_set():
                    action = record.abort_action
                    if action == "rollback":
                        await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                        try:
                            await _rollback_to_pre_run_checkpoint(
                                checkpointer=checkpointer,
                                thread_id=thread_id,
                                run_id=run_id,
                                pre_run_checkpoint_id=pre_run_checkpoint_id,
                                pre_run_snapshot=pre_run_snapshot,
                                snapshot_capture_failed=snapshot_capture_failed,
                            )
                            logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                        except Exception:
                            logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
                    else:
                        await run_manager.set_status(run_id, RunStatus.interrupted)
                else:
                    await run_manager.set_status(run_id, RunStatus.success)
                return

        if ctx.app_config is not None and _agent_factory_supports_app_config(agent_factory):
            agent = agent_factory(config=runnable_config, app_config=ctx.app_config)
        else:
            agent = agent_factory(config=runnable_config)

        # Agent factories attach tracing callbacks at graph construction time.
        # Remove any callback that cannot guarantee pre-export Skill redaction.
        _restrict_unsafe_tracing_callbacks(runnable_config)

        # Capture the effective (resolved) model name from the agent's metadata.
        # _resolve_model_name in agent.py may return the default model if the
        # requested name is not in the allowlist — this update ensures the
        # persisted model_name reflects the actual model used.
        if record.model_name is not None:
            resolved = getattr(agent, "metadata", {}) or {}
            if isinstance(resolved, dict):
                effective = resolved.get("model_name")
                if effective and effective != record.model_name:
                    await run_manager.update_model_name(record.run_id, effective)

        # 4. Attach checkpointer and store
        if checkpointer is not None:
            agent.checkpointer = checkpointer
        if store is not None:
            agent.store = store

        # 5. Set interrupt nodes
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. Build LangGraph stream_mode list
        #    "events" is NOT a valid astream mode — skip it
        #    "messages-tuple" maps to LangGraph's "messages" mode
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                # Skipped — see log above
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # Clean any markers from input messages before passing to agent
        cleaned_graph_input = dict(graph_input) if isinstance(graph_input, dict) else graph_input
        if isinstance(cleaned_graph_input, dict) and "messages" in cleaned_graph_input:
            msgs = cleaned_graph_input["messages"]
            if isinstance(msgs, list):
                cleaned_msgs = []
                for m in msgs:
                    msg_type = getattr(m, "type", "")
                    # Only clean AI and system messages, not human messages
                    if hasattr(m, "content") and msg_type in ("ai", "system", "tool"):
                        m = _clean_aimessage_content(m)
                        if hasattr(m, "content"):
                            c = m.content
                            is_empty_or_marker = (
                                not c.strip()
                                or c.strip() == "[工具调用已省略]"
                                or "工具调用" in c
                            ) if isinstance(c, str) else False
                            if isinstance(c, str) and is_empty_or_marker:
                                # Try to extract from content text first
                                tc_names = _extract_tool_names_from_text(c)
                                if hasattr(m, "tool_calls") and m.tool_calls:
                                    for tc in m.tool_calls:
                                        name = _tool_call_display_name(tc)
                                        if name and name not in tc_names:
                                            tc_names.append(name)
                                if hasattr(m, "tool_call_chunks") and m.tool_call_chunks:
                                    for tcc in m.tool_call_chunks:
                                        name = _tool_call_display_name(tcc)
                                        if name and name not in tc_names:
                                            tc_names.append(name)
                                if tc_names:
                                    m.content = "[工具调用: " + ", ".join(tc_names) + "]"
                    cleaned_msgs.append(m)
                cleaned_graph_input["messages"] = cleaned_msgs

                graph_input = cleaned_graph_input

        # 7. Stream using graph.astream
        if len(lg_modes) == 1 and not stream_subgraphs:
            # Single mode, no subgraphs: astream yields raw chunks
            single_mode = lg_modes[0]
            async for chunk in agent.astream(graph_input, config=runnable_config, stream_mode=single_mode):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break
                cleaned_chunk = _clean_aimessage_content(chunk) if single_mode == "messages" else chunk
                sse_event = _lg_mode_to_sse_event(single_mode)
                if single_mode == "values" and isinstance(cleaned_chunk, dict):
                    # Pre-process values: ensure every AI message in state has
                    # a [工具调用: name1, name2] marker before publishing.
                    msgs = cleaned_chunk.get("messages")
                    if isinstance(msgs, list):
                        _cache_and_patch_values_messages(run_id, msgs)
                        cleaned_chunk["messages"] = msgs
                safe_chunk = redactor.redact_stream_payload(single_mode, cleaned_chunk, run_id=run_id)
                serialized_chunk = serialize(safe_chunk, mode=single_mode)
                if single_mode == "messages":
                    _enrich_tool_call_content_in_serialized(serialized_chunk, run_id=run_id)
                elif single_mode == "values":
                    if isinstance(serialized_chunk, dict):
                        msgs = serialized_chunk.get("messages")
                        if isinstance(msgs, list):
                            _cache_ai_tool_names_from_values(run_id, msgs)
                await bridge.publish(run_id, sse_event, serialized_chunk)
        else:
            # Multiple modes or subgraphs: astream yields tuples
            async for item in agent.astream(
                graph_input,
                config=runnable_config,
                stream_mode=lg_modes,
                subgraphs=stream_subgraphs,
            ):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break

                mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
                if mode is None:
                    continue

                cleaned_chunk = _clean_aimessage_content(chunk) if mode == "messages" else chunk
                sse_event = _lg_mode_to_sse_event(mode)
                if mode == "values" and isinstance(cleaned_chunk, dict):
                    msgs = cleaned_chunk.get("messages")
                    if isinstance(msgs, list):
                        _cache_and_patch_values_messages(run_id, msgs)
                        cleaned_chunk["messages"] = msgs
                safe_chunk = redactor.redact_stream_payload(mode, cleaned_chunk, run_id=run_id)
                serialized_chunk = serialize(safe_chunk, mode=mode)
                if mode == "messages":
                    _enrich_tool_call_content_in_serialized(serialized_chunk, run_id=run_id)
                elif mode == "values":
                    if isinstance(serialized_chunk, dict):
                        msgs = serialized_chunk.get("messages")
                        if isinstance(msgs, list):
                            _cache_ai_tool_names_from_values(run_id, msgs)
                await bridge.publish(run_id, sse_event, serialized_chunk)

        # 8. Final status
        if record.abort_event.is_set():
            action = record.abort_action
            if action == "rollback":
                await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                try:
                    await _rollback_to_pre_run_checkpoint(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        pre_run_checkpoint_id=pre_run_checkpoint_id,
                        pre_run_snapshot=pre_run_snapshot,
                        snapshot_capture_failed=snapshot_capture_failed,
                    )
                    logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                except Exception:
                    logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
            else:
                await run_manager.set_status(run_id, RunStatus.interrupted)
        else:
            await run_manager.set_status(run_id, RunStatus.success)

    except asyncio.CancelledError:
        action = record.abort_action
        if action == "rollback":
            await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
            try:
                await _rollback_to_pre_run_checkpoint(
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
                logger.info("Run %s was cancelled and rolled back", run_id)
            except Exception:
                logger.warning("Run %s cancellation rollback failed", run_id, exc_info=True)
        else:
            await run_manager.set_status(run_id, RunStatus.interrupted)
            logger.info("Run %s was cancelled", run_id)

    except Exception as exc:
        from deerflow.agents.middlewares.token_usage_middleware import (
            PUBLISHED_RUN_TOKEN_BUDGET_ERROR,
            PublishedRunTokenLimitError,
        )

        if isinstance(exc, PublishedRunTokenLimitError):
            error_msg = PUBLISHED_RUN_TOKEN_BUDGET_ERROR
        else:
            error_msg = "Run failed while processing the request."
        logger.error("Run %s failed with %s", run_id, type(exc).__name__, exc_info=True)
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
            },
        )

    finally:
        # Drop cross-stream values cache to avoid unbounded memory use.
        _drop_values_cache(run_id)
        # Flush any buffered journal events and persist completion data
        if journal is not None:
            try:
                await journal.flush()
            except Exception:
                logger.warning("Failed to flush journal for run %s", run_id, exc_info=True)

            try:
                # Persist token usage + convenience fields to RunStore
                completion = journal.get_completion_data()
                await run_manager.update_run_completion(run_id, status=record.status.value, **completion)
            except Exception:
                logger.warning("Failed to persist run completion for %s (non-fatal)", run_id, exc_info=True)

        # Sync title from checkpoint to threads_meta.display_name.
        # For paths that bypass the agent graph (e.g. flash direct), fall back
        # to a local title derived from the first human message.
        if checkpointer is not None and thread_store is not None:
            try:
                ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
                if ckpt_tuple is not None:
                    ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
                    channel_values = dict(ckpt.get("channel_values") or {})
                    title = channel_values.get("title")

                    if not title:
                        fallback = _extract_fallback_title(channel_values.get("messages", []))
                        if fallback:
                            title = fallback
                            channel_values["title"] = title
                            await _persist_flash_direct_checkpoint(
                                checkpointer=checkpointer,
                                thread_id=thread_id,
                                ckpt_tuple=ckpt_tuple,
                                channel_values=channel_values,
                                changed_channels={"title"},
                            )

                    if title:
                        await thread_store.update_display_name(thread_id, title)
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id, exc_info=True)

        # Update threads_meta status based on run outcome
        if thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else record.status.value
                await thread_store.update_status(thread_id, final_status)
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        await bridge.publish_end(run_id)

        cleanup_task = asyncio.create_task(bridge.cleanup(run_id, delay=60))
        cleanup_task.add_done_callback(lambda task: _log_cleanup_exception(task, run_id, logger))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_checkpointer_method(checkpointer: Any, async_name: str, sync_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a checkpointer method, supporting async and sync variants."""
    method = getattr(checkpointer, async_name, None) or getattr(checkpointer, sync_name, None)
    if method is None:
        raise AttributeError(f"Missing checkpointer method: {async_name}/{sync_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """Restore thread state to the checkpoint snapshot captured before run start."""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        logger.warning("Run %s rollback skipped: pre-run checkpoint snapshot capture failed", run_id)
        return

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        logger.warning("Run %s rollback skipped: invalid pre-run checkpoint snapshot", run_id)
        return
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        logger.warning("Run %s rollback skipped: pre-run checkpoint has no checkpoint id", run_id)
        return
    restore_marker = _new_checkpoint_marker()
    checkpoint_to_restore = {
        **checkpoint_to_restore,
        "id": restore_marker["id"],
        "ts": restore_marker["ts"],
    }
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _new_checkpoint_marker() -> dict[str, str]:
    marker = empty_checkpoint()
    return {"id": marker["id"], "ts": marker["ts"]}


def _lg_mode_to_sse_event(mode: str) -> str:
    """Map LangGraph internal stream_mode name to SSE event name.

    LangGraph's ``astream(stream_mode="messages")`` produces message
    tuples.  The SSE protocol calls this ``messages-tuple`` when the
    client explicitly requests it, but the default SSE event name used
    by LangGraph Platform is simply ``"messages"``.
    """
    # All LG modes map 1:1 to SSE event names — "messages" stays "messages"
    return mode


def _extract_human_message(graph_input: dict) -> HumanMessage | None:
    """Extract or construct a HumanMessage from graph_input for event recording.

    Returns a LangChain HumanMessage so callers can use .model_dump() to get
    the checkpoint-aligned serialization format.
    """
    from langchain_core.messages import HumanMessage

    messages = graph_input.get("messages")
    if not messages:
        return None
    last = messages[-1] if isinstance(messages, list) else messages
    if isinstance(last, HumanMessage):
        return last
    if isinstance(last, str):
        return HumanMessage(content=last) if last else None
    if hasattr(last, "content"):
        content = last.content
        return HumanMessage(content=content)
    if isinstance(last, dict):
        content = last.get("content", "")
        return HumanMessage(content=content) if content else None
    return None


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """Unpack a multi-mode or subgraph stream item into (mode, chunk).

    Returns ``(None, None)`` if the item cannot be parsed.
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # Fallback: single-element output from first mode
    return lg_modes[0] if lg_modes else None, item
