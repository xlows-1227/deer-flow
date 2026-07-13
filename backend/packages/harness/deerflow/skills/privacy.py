"""User-visible redaction for Skill bundle tool calls and results.

The agent and checkpointer must retain raw Skill contents so the model can use
them.  This module creates detached, user-safe copies for streams, run events,
and Gateway responses.  It intentionally does not perform serialization; the
caller applies the normal runtime serializer after redaction.
"""

from __future__ import annotations

import copy
import logging
import posixpath
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

SKILL_RESULT_PLACEHOLDER = "Skill instructions loaded."
SKILL_REDACTION_EVENT_TYPE = "skill_execution"
SUBAGENT_RESULT_PLACEHOLDER = "Task Succeeded. Result: Subagent result hidden."
SUBAGENT_ERROR_PLACEHOLDER = "Task failed. Subagent result details hidden."

_METRIC_NAMES = (
    "skill_redaction_events_total",
    "skill_redaction_fail_closed_total",
    "skill_redaction_legacy_fallback_total",
    "skill_redaction_errors_total",
)
_SAFE_METRIC_BOUNDARIES = frozenset({"runtime", "gateway", "journal", "stream", "event", "message", "other"})
_SAFE_METRIC_TOOLS = frozenset({"read_file", "ls", "grep", "glob", "bash", "other"})
_METRIC_COUNTS: dict[str, Counter[tuple[str, str]]] = {name: Counter() for name in _METRIC_NAMES}
_METRIC_LOCK = Lock()


def record_skill_redaction_metric(
    name: str,
    *,
    boundary: str,
    tool: str = "other",
    count: int = 1,
) -> None:
    """Record a low-cardinality redaction metric without sensitive labels."""

    if name not in _METRIC_COUNTS or count <= 0:
        return
    safe_boundary = boundary if boundary in _SAFE_METRIC_BOUNDARIES else "other"
    safe_tool = tool if tool in _SAFE_METRIC_TOOLS else "other"
    with _METRIC_LOCK:
        _METRIC_COUNTS[name][(safe_boundary, safe_tool)] += count


def get_skill_redaction_metrics_snapshot() -> dict[str, dict[tuple[str, str], int]]:
    """Return a detached process-local snapshot for a metrics exporter."""

    with _METRIC_LOCK:
        return {name: dict(counts) for name, counts in _METRIC_COUNTS.items()}


@dataclass(frozen=True)
class SkillExecutionDescriptor:
    """Safe metadata describing one Skill execution."""

    skill_name: str | None = None
    category: str | None = None
    skill_id: str | None = None
    skill_handle: str | None = None
    version_seq: int | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary": "Loaded skill instructions",
        }
        for key in ("skill_name", "category", "skill_id", "skill_handle", "version_seq"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class SkillProjectionEntry:
    """A run-authorized Skill projection and its safe descriptor."""

    root_path: str
    descriptor: SkillExecutionDescriptor


def _normalize_path(value: str) -> str:
    replaced = value.strip().replace("\\", "/")
    if replaced.startswith("//"):
        replaced = "/" + replaced.lstrip("/")
    normalized = posixpath.normpath(replaced)
    if replaced.startswith("/") and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.casefold()


def _is_under(path: str, root: str) -> bool:
    normalized_path = _normalize_path(path)
    normalized_root = _normalize_path(root).rstrip("/")
    return normalized_path == normalized_root or normalized_path.startswith(f"{normalized_root}/")


def _message_value(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(key, default)
    return getattr(message, key, default)


def _is_message_like(value: Any) -> bool:
    if isinstance(value, Mapping):
        message_type = value.get("type")
        return message_type in {"ai", "tool", "human", "system", "function", "remove"} and ("content" in value or "tool_calls" in value)
    return hasattr(value, "content") and (hasattr(value, "tool_calls") or hasattr(value, "tool_call_id") or hasattr(value, "type"))


def _copy_message(message: Any, updates: dict[str, Any]) -> Any:
    if isinstance(message, Mapping):
        cloned = copy.deepcopy(dict(message))
        cloned.update(updates)
        return cloned
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True, update=updates)
    cloned = copy.deepcopy(message)
    for key, value in updates.items():
        setattr(cloned, key, value)
    return cloned


def _copy_tool_call(tool_call: Any, *, args: dict[str, Any]) -> Any:
    if isinstance(tool_call, Mapping):
        cloned = copy.deepcopy(dict(tool_call))
        cloned["args"] = args
        return cloned
    model_copy = getattr(tool_call, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True, update={"args": args})
    cloned = copy.deepcopy(tool_call)
    setattr(cloned, "args", args)
    return cloned


class SkillContentRedactor:
    """Create user-safe copies of Skill-related messages and event payloads."""

    _PATH_ARGUMENT_KEYS = frozenset(
        {
            "path",
            "paths",
            "file",
            "files",
            "filepath",
            "filepaths",
            "command",
            "cmd",
            "script",
        }
    )
    _SAFE_EVENT_METADATA_KEYS = frozenset(
        {
            "caller",
            "usage",
            "latency_ms",
            "llm_call_index",
            "status",
            "error_type",
            "checkpoint_ns",
            "langgraph_checkpoint_ns",
            "namespace",
        }
    )
    _FAIL_CLOSED_ORPHAN_TOOL_NAMES = frozenset({"read_file", "grep", "glob", "ls", "bash"})

    @staticmethod
    def _projection_from_mapping(value: Any) -> SkillProjectionEntry | None:
        if not isinstance(value, Mapping):
            return None
        root_path = value.get("root_path") or value.get("projection_root")
        if not isinstance(root_path, str) or not root_path.strip().startswith(("/", "\\")):
            return None
        descriptor = SkillExecutionDescriptor(
            skill_name=str(value.get("skill_name")) if value.get("skill_name") else None,
            category=str(value.get("category")) if value.get("category") else None,
            skill_id=str(value.get("skill_id")) if value.get("skill_id") else None,
            skill_handle=str(value.get("skill_handle")) if value.get("skill_handle") else None,
            version_seq=value.get("version_seq") if isinstance(value.get("version_seq"), int) else None,
        )
        return SkillProjectionEntry(root_path=root_path, descriptor=descriptor)

    @classmethod
    def from_run_context(
        cls,
        *,
        app_config: Any | None,
        runtime_context: Mapping[str, Any] | None,
        redact_unknown_paths: bool = False,
        boundary: str = "runtime",
    ) -> SkillContentRedactor:
        """Build a redactor from explicit run grants/projection metadata.

        The worker passes the caller-provided run context directly; no ambient
        contextvar or global grant lookup is used.  This keeps subagent and
        resumable-run behavior deterministic once immutable projections land.
        """

        skills = getattr(app_config, "skills", None)
        configured_root = getattr(skills, "container_path", None)
        skills_root = configured_root if isinstance(configured_root, str) and configured_root else "/mnt/skills"
        projections: list[SkillProjectionEntry] = []
        context = runtime_context if isinstance(runtime_context, Mapping) else {}

        manifest = context.get("skill_projection_manifest")
        if isinstance(manifest, Mapping):
            manifest_entries = manifest.get("entries", [])
        else:
            manifest_entries = manifest if isinstance(manifest, list) else []
        if isinstance(manifest_entries, list):
            for entry in manifest_entries:
                projection = cls._projection_from_mapping(entry)
                if projection is not None:
                    projections.append(projection)

        grants = context.get("skill_grants")
        if isinstance(grants, list):
            for grant in grants:
                projection = cls._projection_from_mapping(grant)
                if projection is not None:
                    projections.append(projection)

        return cls(
            skills_root=skills_root,
            projections=projections,
            redact_unknown_paths=redact_unknown_paths,
            boundary=boundary,
        )

    def __init__(
        self,
        *,
        skills_root: str = "/mnt/skills",
        projections: Iterable[SkillProjectionEntry] | None = None,
        redact_unknown_paths: bool = False,
        boundary: str = "other",
    ) -> None:
        self.skills_root = skills_root
        self._projections = tuple(projections or ())
        self._redact_unknown_paths = redact_unknown_paths
        self._boundary = boundary if boundary in _SAFE_METRIC_BOUNDARIES else "other"
        self._sensitive_calls: dict[tuple[str, str, str], SkillExecutionDescriptor] = {}
        self._subagent_calls: set[tuple[str, str, str]] = set()
        self._observed_calls: set[tuple[str, str, str]] = set()

    @staticmethod
    def _call_key(run_id: str, namespace: str, tool_call_id: str) -> tuple[str, str, str]:
        return (run_id, namespace, tool_call_id)

    @staticmethod
    def _tool_call_value(tool_call: Any, key: str, default: Any = None) -> Any:
        if isinstance(tool_call, Mapping):
            return tool_call.get(key, default)
        return getattr(tool_call, key, default)

    def _descriptor_for_path(self, path: str) -> SkillExecutionDescriptor | None:
        for projection in self._projections:
            if _is_under(path, projection.root_path):
                return projection.descriptor

        if not _is_under(path, self.skills_root):
            if self._redact_unknown_paths and _normalize_path(path).startswith("/"):
                return SkillExecutionDescriptor()
            return None

        normalized_path = _normalize_path(path)
        normalized_root = _normalize_path(self.skills_root).rstrip("/")
        relative = normalized_path[len(normalized_root) :].lstrip("/")
        segments = [segment for segment in relative.split("/") if segment]
        category: str | None = None
        skill_name: str | None = None
        if segments:
            if segments[0] in {"public", "custom"}:
                category = segments[0]
                skill_name = segments[1] if len(segments) > 1 else None
            else:
                skill_name = segments[0]
        return SkillExecutionDescriptor(skill_name=skill_name, category=category)

    @staticmethod
    def _candidate_paths(value: str) -> list[str]:
        normalized = value.replace("\\", "/")
        candidates = [normalized]
        candidates.extend(token.strip("'\"`()[]{};,|&") for token in re.split(r"\s+", normalized) if "/" in token)
        return candidates

    def _descriptor_for_value(self, value: Any) -> SkillExecutionDescriptor | None:
        if isinstance(value, str):
            for candidate in self._candidate_paths(value):
                descriptor = self._descriptor_for_path(candidate)
                if descriptor is not None:
                    return descriptor
            return None
        if isinstance(value, Mapping):
            for nested in value.values():
                descriptor = self._descriptor_for_value(nested)
                if descriptor is not None:
                    return descriptor
            return None
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                descriptor = self._descriptor_for_value(nested)
                if descriptor is not None:
                    return descriptor
        return None

    def _classify_tool_call(self, tool_call: Any) -> SkillExecutionDescriptor | None:
        args = self._tool_call_value(tool_call, "args", {})
        if not isinstance(args, Mapping):
            return None

        if args.get("redacted") is True:
            return SkillExecutionDescriptor(
                skill_name=str(args.get("skill_name")) if args.get("skill_name") else None,
                category=str(args.get("category")) if args.get("category") else None,
                skill_id=str(args.get("skill_id")) if args.get("skill_id") else None,
                skill_handle=str(args.get("skill_handle")) if args.get("skill_handle") else None,
                version_seq=args.get("version_seq") if isinstance(args.get("version_seq"), int) else None,
            )

        for key in self._PATH_ARGUMENT_KEYS:
            if key not in args:
                continue
            descriptor = self._descriptor_for_value(args[key])
            if descriptor is not None:
                return descriptor

        # Future Skill tools may use a new argument name.  Projection/root
        # membership remains the security signal, so inspect remaining values.
        return self._descriptor_for_value(args)

    @staticmethod
    def _descriptor_from_safe_payload(value: Any) -> SkillExecutionDescriptor | None:
        if not isinstance(value, Mapping):
            return None
        return SkillExecutionDescriptor(
            skill_name=str(value.get("skill_name")) if value.get("skill_name") else None,
            category=str(value.get("category")) if value.get("category") else None,
            skill_id=str(value.get("skill_id")) if value.get("skill_id") else None,
            skill_handle=str(value.get("skill_handle")) if value.get("skill_handle") else None,
            version_seq=value.get("version_seq") if isinstance(value.get("version_seq"), int) else None,
        )

    def observe_message(self, message: Any, *, run_id: str, namespace: str = "") -> None:
        tool_calls = _message_value(message, "tool_calls", None)
        if not isinstance(tool_calls, (list, tuple)):
            return
        for tool_call in tool_calls:
            tool_call_id = self._tool_call_value(tool_call, "id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue
            key = self._call_key(run_id, namespace, tool_call_id)
            self._observed_calls.add(key)
            tool_name = self._tool_call_value(tool_call, "name")
            if tool_name == "task":
                self._subagent_calls.add(key)
                continue
            descriptor = self._classify_tool_call(tool_call)
            if descriptor is not None:
                self._sensitive_calls[key] = descriptor

    def _safe_tool_call_args(self, tool_call: Any, descriptor: SkillExecutionDescriptor) -> dict[str, Any]:
        args = self._tool_call_value(tool_call, "args", {})
        description = args.get("description") if isinstance(args, Mapping) else None
        safe: dict[str, Any] = {}
        if isinstance(description, str) and description.strip():
            safe["description"] = description
        safe.update({k: v for k, v in descriptor.to_safe_dict().items() if k != "summary"})
        safe["redacted"] = True
        return safe

    def _safe_subagent_tool_call_args(self, tool_call: Any) -> dict[str, Any]:
        args = self._tool_call_value(tool_call, "args", {})
        safe: dict[str, Any] = {}
        if isinstance(args, Mapping):
            for key in ("description", "subagent_type"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    safe[key] = value
        safe["redacted"] = True
        return safe

    @staticmethod
    def _safe_additional_kwargs(message: Any, descriptor: SkillExecutionDescriptor) -> dict[str, Any]:
        del message
        return {
            "visibility": "redacted",
            "event_type": SKILL_REDACTION_EVENT_TYPE,
            "skill_execution": descriptor.to_safe_dict(),
        }

    def _redact_tool_calls(self, message: Any, *, run_id: str, namespace: str) -> Any:
        tool_calls = _message_value(message, "tool_calls", None)
        if not isinstance(tool_calls, (list, tuple)):
            return copy.deepcopy(message)

        redacted_calls: list[Any] = []
        has_sensitive_call = False
        for tool_call in tool_calls:
            tool_call_id = self._tool_call_value(tool_call, "id")
            key = self._call_key(run_id, namespace, tool_call_id) if isinstance(tool_call_id, str) else None
            if key is not None and key in self._subagent_calls:
                has_sensitive_call = True
                record_skill_redaction_metric(
                    "skill_redaction_events_total",
                    boundary=self._boundary,
                    tool="other",
                )
                redacted_calls.append(
                    _copy_tool_call(
                        tool_call,
                        args=self._safe_subagent_tool_call_args(tool_call),
                    )
                )
                continue
            descriptor = self._sensitive_calls.get(key) if key is not None else None
            if descriptor is None:
                redacted_calls.append(copy.deepcopy(tool_call))
                continue
            has_sensitive_call = True
            tool_name = self._tool_call_value(tool_call, "name", "other")
            record_skill_redaction_metric(
                "skill_redaction_events_total",
                boundary=self._boundary,
                tool=tool_name if isinstance(tool_name, str) else "other",
            )
            redacted_calls.append(
                _copy_tool_call(
                    tool_call,
                    args=self._safe_tool_call_args(tool_call, descriptor),
                )
            )

        updates: dict[str, Any] = {"tool_calls": redacted_calls}
        if has_sensitive_call:
            updates["additional_kwargs"] = {}
        return _copy_message(message, updates)

    def _is_subagent_result(self, message: Any, *, run_id: str, namespace: str) -> bool:
        tool_call_id = _message_value(message, "tool_call_id")
        if isinstance(tool_call_id, str):
            key = self._call_key(run_id, namespace, tool_call_id)
            if key in self._subagent_calls:
                return True
        return _message_value(message, "name") == "task"

    @staticmethod
    def _redact_subagent_result(message: Any) -> Any:
        content = _message_value(message, "content", "")
        normalized = content.strip() if isinstance(content, str) else ""
        is_success = normalized.startswith("Task Succeeded. Result:")
        status = "completed" if is_success else "failed"
        placeholder = SUBAGENT_RESULT_PLACEHOLDER if is_success else SUBAGENT_ERROR_PLACEHOLDER
        updates: dict[str, Any] = {
            "content": placeholder,
            "additional_kwargs": {
                "visibility": "redacted",
                "event_type": "subagent_execution",
                "subagent_execution": {
                    "status": status,
                    "summary": "Subagent result hidden",
                },
            },
            "response_metadata": {},
        }
        if isinstance(message, Mapping):
            if "artifact" in message:
                updates["artifact"] = None
        elif hasattr(message, "artifact"):
            updates["artifact"] = None
        return _copy_message(message, updates)

    def _tool_result_descriptor(
        self,
        message: Any,
        *,
        run_id: str,
        namespace: str,
    ) -> SkillExecutionDescriptor | None:
        tool_call_id = _message_value(message, "tool_call_id")
        key = self._call_key(run_id, namespace, tool_call_id) if isinstance(tool_call_id, str) else None
        if key is not None and key in self._sensitive_calls:
            return self._sensitive_calls[key]
        if key is not None and key in self._observed_calls:
            return None

        additional_kwargs = _message_value(message, "additional_kwargs", {})
        if isinstance(additional_kwargs, Mapping):
            descriptor = self._descriptor_from_safe_payload(additional_kwargs.get("skill_execution"))
            if descriptor is not None:
                return descriptor

        # Legacy/orphan context-tool results have no arguments left to classify.
        # Hide them conservatively instead of returning potentially sensitive
        # Skill bundle contents. Paired, observed non-Skill calls are preserved
        # above after their arguments establish a non-sensitive association.
        name = _message_value(message, "name")
        if name in self._FAIL_CLOSED_ORPHAN_TOOL_NAMES:
            return SkillExecutionDescriptor()
        return None

    def redact_message(self, message: Any, *, run_id: str, namespace: str = "") -> Any:
        self.observe_message(message, run_id=run_id, namespace=namespace)

        if _message_value(message, "tool_calls", None) is not None:
            return self._redact_tool_calls(message, run_id=run_id, namespace=namespace)

        if _message_value(message, "tool_call_id", None) is not None:
            if self._is_subagent_result(message, run_id=run_id, namespace=namespace):
                return self._redact_subagent_result(message)
            descriptor = self._tool_result_descriptor(message, run_id=run_id, namespace=namespace)
            if descriptor is None:
                return copy.deepcopy(message)
            updates: dict[str, Any] = {
                "content": SKILL_RESULT_PLACEHOLDER,
                "additional_kwargs": self._safe_additional_kwargs(message, descriptor),
                "response_metadata": {},
            }
            if isinstance(message, Mapping):
                if "artifact" in message:
                    updates["artifact"] = None
            elif hasattr(message, "artifact"):
                updates["artifact"] = None
            return _copy_message(message, updates)

        return copy.deepcopy(message)

    def redact_messages(self, messages: Iterable[Any], *, run_id: str, namespace: str = "") -> list[Any]:
        materialized = list(messages)
        for message in materialized:
            self.observe_message(message, run_id=run_id, namespace=namespace)
        return [self.redact_message(message, run_id=run_id, namespace=namespace) for message in materialized]

    def _observe_nested(self, value: Any, *, run_id: str, namespace: str) -> None:
        if _is_message_like(value):
            self.observe_message(value, run_id=run_id, namespace=namespace)
            return
        if isinstance(value, Mapping):
            nested_namespace = namespace
            for key in ("checkpoint_ns", "langgraph_checkpoint_ns", "namespace"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    nested_namespace = candidate
                    break
            for nested in value.values():
                self._observe_nested(nested, run_id=run_id, namespace=nested_namespace)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                self._observe_nested(nested, run_id=run_id, namespace=namespace)

    def _redact_nested(self, value: Any, *, run_id: str, namespace: str) -> Any:
        if _is_message_like(value):
            return self.redact_message(value, run_id=run_id, namespace=namespace)
        if isinstance(value, Mapping):
            nested_namespace = namespace
            for key in ("checkpoint_ns", "langgraph_checkpoint_ns", "namespace"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    nested_namespace = candidate
                    break
            return {key: self._redact_nested(nested, run_id=run_id, namespace=nested_namespace) for key, nested in value.items()}
        if isinstance(value, list):
            return [self._redact_nested(nested, run_id=run_id, namespace=namespace) for nested in value]
        if isinstance(value, tuple):
            return tuple(self._redact_nested(nested, run_id=run_id, namespace=namespace) for nested in value)
        return copy.deepcopy(value)

    def redact_stream_payload(self, mode: str, payload: Any, *, run_id: str, namespace: str = "") -> Any:
        del mode  # The recursive message contract is shared by all stream modes.
        try:
            self._observe_nested(payload, run_id=run_id, namespace=namespace)
            return self._redact_nested(payload, run_id=run_id, namespace=namespace)
        except Exception:
            # Exception messages may themselves contain tool output.  Do not
            # attach the exception or traceback to this user-boundary log.
            logger.error("Skill content redaction failed for run %s", run_id)
            record_skill_redaction_metric(
                "skill_redaction_errors_total",
                boundary=self._boundary,
            )
            record_skill_redaction_metric(
                "skill_redaction_fail_closed_total",
                boundary=self._boundary,
            )
            return {
                "redaction_error": True,
                "message": "Sensitive tool payload hidden.",
            }

    def redact_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        redacted = copy.deepcopy(dict(event))
        run_id = str(event.get("run_id") or "")
        metadata = event.get("metadata")
        namespace = ""
        if isinstance(metadata, Mapping):
            candidate = metadata.get("checkpoint_ns") or metadata.get("langgraph_checkpoint_ns")
            if isinstance(candidate, str):
                namespace = candidate
        content = event.get("content")
        safe_content = self.redact_stream_payload(
            str(event.get("event_type") or "event"),
            content,
            run_id=run_id,
            namespace=namespace,
        )
        redacted["content"] = safe_content
        run_is_sensitive = any(key[0] == run_id for key in self._sensitive_calls) or any(key[0] == run_id for key in self._subagent_calls)
        category = event.get("category")
        if run_is_sensitive and category in {"trace", "error", "middleware"}:
            redacted["content"] = "Sensitive execution details hidden."
            redacted["metadata"] = {key: copy.deepcopy(value) for key, value in (metadata.items() if isinstance(metadata, Mapping) else ()) if key in self._SAFE_EVENT_METADATA_KEYS}
            return redacted
        if _is_message_like(safe_content):
            additional_kwargs = _message_value(safe_content, "additional_kwargs", {})
            if isinstance(additional_kwargs, Mapping):
                for execution_key in ("skill_execution", "subagent_execution"):
                    execution = additional_kwargs.get(execution_key)
                    if not isinstance(execution, Mapping):
                        continue
                    safe_metadata = {key: copy.deepcopy(value) for key, value in (metadata.items() if isinstance(metadata, Mapping) else ()) if key in self._SAFE_EVENT_METADATA_KEYS}
                    safe_metadata[execution_key] = copy.deepcopy(dict(execution))
                    redacted["metadata"] = safe_metadata
                    break
        return redacted

    def redact_event_batch(self, events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        materialized = list(events)
        for event in materialized:
            run_id = str(event.get("run_id") or "")
            metadata = event.get("metadata")
            namespace = ""
            if isinstance(metadata, Mapping):
                candidate = metadata.get("checkpoint_ns") or metadata.get("langgraph_checkpoint_ns")
                if isinstance(candidate, str):
                    namespace = candidate
            content = event.get("content")
            if _is_message_like(content):
                self.observe_message(content, run_id=run_id, namespace=namespace)
        return [self.redact_event(event) for event in materialized]
