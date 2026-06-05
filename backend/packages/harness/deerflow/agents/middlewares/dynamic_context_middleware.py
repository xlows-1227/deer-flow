"""Middleware to inject dynamic context (memory, current date) as a system-reminder.

The system prompt is kept fully static for maximum prefix-cache reuse across users
and sessions.  The current date is always injected.  Per-user memory is also injected
when ``memory.injection_enabled`` is True in the app config.  Both are delivered once
per conversation as a dedicated <system-reminder> HumanMessage inserted before the
first user message (frozen-snapshot pattern).

When a conversation spans midnight the middleware detects the date change and injects
a lightweight date-update reminder as a separate HumanMessage before the current turn.
This correction is persisted so subsequent turns on the new day see a consistent history
and do not re-inject.

Reminder format:

    <system-reminder>
    <memory>...</memory>

    <current_date>2026-05-08, Friday</current_date>
    </system-reminder>

Date-update format:

    <system-reminder>
    <current_date>2026-05-09, Saturday</current_date>
    </system-reminder>
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"<current_date>([^<]+)</current_date>")
_CONNECTOR_ID_RE = re.compile(r"<connector_id>([^<]*)</connector_id>")
_DYNAMIC_CONTEXT_REMINDER_KEY = "dynamic_context_reminder"
_SUMMARY_MESSAGE_NAME = "summary"


def _extract_date(content: str) -> str | None:
    """Return the first <current_date> value found in *content*, or None."""
    m = _DATE_RE.search(content)
    return m.group(1) if m else None


def _extract_connector_ids(content: str) -> tuple[str, ...] | None:
    """Return selected connector ids from a reminder, or None if no marker exists."""
    if "<selected_connectors>" not in content:
        return None
    return tuple(item for item in _CONNECTOR_ID_RE.findall(content) if item)


def is_dynamic_context_reminder(message: object) -> bool:
    """Return whether *message* is a hidden dynamic-context reminder."""
    return isinstance(message, HumanMessage) and bool(message.additional_kwargs.get(_DYNAMIC_CONTEXT_REMINDER_KEY))


def _last_injected_date(messages: list) -> str | None:
    """Scan messages in reverse and return the most recently injected date.

    Detection uses the ``dynamic_context_reminder`` additional_kwargs flag rather
    than content substring matching, so user messages containing ``<system-reminder>``
    are not mistakenly treated as injected reminders.
    """
    for msg in reversed(messages):
        if is_dynamic_context_reminder(msg):
            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
            return _extract_date(content_str)
    return None


def _last_injected_connector_ids(messages: list) -> tuple[str, ...] | None:
    """Scan messages in reverse and return the most recent selected connector marker."""
    for msg in reversed(messages):
        if is_dynamic_context_reminder(msg):
            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
            connector_ids = _extract_connector_ids(content_str)
            if connector_ids is not None:
                return connector_ids
    return None


def _is_user_injection_target(message: object) -> bool:
    """Return whether *message* can receive a dynamic-context reminder."""
    return isinstance(message, HumanMessage) and not is_dynamic_context_reminder(message) and message.name != _SUMMARY_MESSAGE_NAME


def _runtime_connector_ids(runtime: Runtime | None) -> tuple[str, ...]:
    context = runtime.context if runtime is not None else {}
    if not isinstance(context, dict):
        return ()
    raw = context.get("connector_ids")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if item)


def _xml_value(value: Any) -> str:
    return escape(str(value))


def _xml_tag(name: str, value: Any) -> str:
    return f"<{name}>{_xml_value(value)}</{name}>"


def _connector_runtime_context(runtime: Runtime | None):
    from deerflow.connectors.schemas import ConnectorRuntimeContext
    from deerflow.runtime.user_context import resolve_runtime_user_id

    ctx = runtime.context if runtime is not None else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    return ConnectorRuntimeContext(
        user_id=resolve_runtime_user_id(runtime),
        thread_id=str(ctx.get("thread_id")) if ctx.get("thread_id") else None,
        run_id=str(ctx.get("run_id")) if ctx.get("run_id") else None,
        agent_id=str(ctx.get("agent_name")) if ctx.get("agent_name") else None,
        skill_name=str(ctx.get("skill_name")) if ctx.get("skill_name") else None,
        connector_ids=list(_runtime_connector_ids(runtime)) or None,
    )


async def _load_selected_connector_summaries(runtime: Runtime | None, *, app_config: AppConfig | None = None) -> list[dict[str, Any]] | None:
    connector_ids = _runtime_connector_ids(runtime)
    if not connector_ids:
        return None
    try:
        from deerflow.connectors.service import make_connector_service

        return await make_connector_service(app_config=app_config).list_available_summaries(
            context=_connector_runtime_context(runtime),
            capability="database.query",
        )
    except Exception as exc:
        try:
            from deerflow.connectors.errors import ConnectorError
        except Exception:
            ConnectorError = ()  # type: ignore[assignment]
        if isinstance(exc, ConnectorError):
            logger.debug("Selected connector summaries are unavailable for dynamic context: %s", exc)
            return None
        logger.exception("Failed to load selected connector summaries for dynamic context")
        return None


def _load_selected_connector_summaries_sync(runtime: Runtime | None, *, app_config: AppConfig | None = None) -> list[dict[str, Any]] | None:
    connector_ids = _runtime_connector_ids(runtime)
    if not connector_ids:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_load_selected_connector_summaries(runtime, app_config=app_config))
    logger.debug("Skipping sync connector summary load because an event loop is already running")
    return None


def _build_connector_summary_xml(summary: dict[str, Any]) -> str:
    lines = ["<connector>"]
    for source_key, tag_name in (
        ("id", "connector_id"),
        ("name", "connector_name"),
        ("display_name", "connector_display_name"),
        ("type", "connector_type"),
        ("status", "status"),
    ):
        value = summary.get(source_key)
        if value not in (None, ""):
            lines.append(_xml_tag(tag_name, value))
    connection = summary.get("connection")
    if isinstance(connection, dict) and connection:
        lines.append("<connection>")
        for key in ("host", "port", "query_port", "database"):
            if key in connection and connection[key] not in (None, ""):
                lines.append(_xml_tag(key, connection[key]))
        lines.append("</connection>")
    capabilities = summary.get("capabilities")
    if isinstance(capabilities, list) and capabilities:
        lines.append("<capabilities>")
        for capability in capabilities:
            lines.append(_xml_tag("capability", capability))
        lines.append("</capabilities>")
    policy_summary = summary.get("policy_summary")
    if isinstance(policy_summary, dict) and policy_summary:
        lines.append("<policy_summary>")
        for key in ("mode", "max_rows", "statement_timeout_ms", "require_limit"):
            if key in policy_summary:
                lines.append(_xml_tag(key, policy_summary[key]))
        lines.append("</policy_summary>")
    lines.append("</connector>")
    return "\n".join(lines)


def _build_selected_connectors_section(connector_ids: tuple[str, ...], connector_summaries: list[dict[str, Any]] | None = None) -> str:
    if connector_ids:
        connector_items = (
            "\n".join(_build_connector_summary_xml(summary) for summary in connector_summaries)
            if connector_summaries
            else "\n".join(f"<connector_id>{escape(connector_id)}</connector_id>" for connector_id in connector_ids)
        )
        return "\n".join(
            [
                "<selected_connectors>",
                "The user selected these connectors for this chat turn:",
                connector_items,
                "",
                "When the user asks about data in the selected connector, use the connector tools.",
                "Use the connector_id above when calling connector tools. Match user text such as a database or connector name against connector_name, connector_display_name, connector_type, and connection.database.",
                "Call `list_connectors` if you need to refresh or verify the selected connector list.",
                "For database questions, inspect schema with `inspect_connector` when needed, then run read-only SELECT SQL with `query_database`.",
                "Do not ask the user for database credentials; connector tools resolve credentials securely server-side.",
                "</selected_connectors>",
            ]
        )
    return "\n".join(
        [
            "<selected_connectors>",
            "",
            "No connector is selected for this chat turn. Do not rely on connector ids from earlier reminders unless the user selects one again.",
            "</selected_connectors>",
        ]
    )


class DynamicContextMiddleware(AgentMiddleware):
    """Inject memory and current date into HumanMessages as a <system-reminder>.

    First turn
    ----------
    Prepends a full system-reminder (memory + date) to the first HumanMessage and
    persists it (same message ID).  The first message is then frozen for the whole
    session — its content never changes again, so the prefix cache can hit on every
    subsequent turn.

    Midnight crossing
    -----------------
    If the conversation spans midnight, the current date differs from the date that
    was injected earlier.  In that case a lightweight date-update reminder is prepended
    to the **current** (last) HumanMessage and persisted.  Subsequent turns on the new
    day see the corrected date in history and skip re-injection.
    """

    def __init__(self, agent_name: str | None = None, *, app_config: AppConfig | None = None):
        super().__init__()
        self._agent_name = agent_name
        self._app_config = app_config

    def _build_full_reminder(self, *, connector_ids: tuple[str, ...] = (), connector_summaries: list[dict[str, Any]] | None = None) -> str:
        from deerflow.agents.lead_agent.prompt import _get_memory_context

        # Memory injection is gated by injection_enabled; date is always included.
        injection_enabled = self._app_config.memory.injection_enabled if self._app_config else True
        memory_context = _get_memory_context(self._agent_name, app_config=self._app_config) if injection_enabled else ""
        current_date = datetime.now().strftime("%Y-%m-%d, %A")

        lines: list[str] = ["<system-reminder>"]
        if memory_context:
            lines.append(memory_context.strip())
            lines.append("")  # blank line separating memory from date
        lines.append(f"<current_date>{current_date}</current_date>")
        if connector_ids:
            lines.append("")
            lines.append(_build_selected_connectors_section(connector_ids, connector_summaries))
        lines.append("</system-reminder>")

        return "\n".join(lines)

    def _build_date_update_reminder(self) -> str:
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        return "\n".join(
            [
                "<system-reminder>",
                f"<current_date>{current_date}</current_date>",
                "</system-reminder>",
            ]
        )

    def _build_connector_update_reminder(self, connector_ids: tuple[str, ...], connector_summaries: list[dict[str, Any]] | None = None) -> str:
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        return "\n".join(
            [
                "<system-reminder>",
                f"<current_date>{current_date}</current_date>",
                "",
                _build_selected_connectors_section(connector_ids, connector_summaries),
                "</system-reminder>",
            ]
        )

    def _build_date_and_connector_update_reminder(self, connector_ids: tuple[str, ...], connector_summaries: list[dict[str, Any]] | None = None) -> str:
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        return "\n".join(
            [
                "<system-reminder>",
                f"<current_date>{current_date}</current_date>",
                "",
                _build_selected_connectors_section(connector_ids, connector_summaries),
                "</system-reminder>",
            ]
        )

    @staticmethod
    def _make_reminder_and_user_messages(original: HumanMessage, reminder_content: str) -> tuple[HumanMessage, HumanMessage]:
        """Return (reminder_msg, user_msg) using the ID-swap technique.

        reminder_msg takes the original message's ID so that add_messages replaces it
        in-place (preserving position).  user_msg carries the original content with a
        derived ``{id}__user`` ID and is appended immediately after by add_messages.

        If the original message has no ID a stable UUID is generated so the derived
        ``{id}__user`` ID never collapses to the ambiguous ``None__user`` string.
        """
        stable_id = original.id or str(uuid.uuid4())
        reminder_msg = HumanMessage(
            content=reminder_content,
            id=stable_id,
            additional_kwargs={"hide_from_ui": True, _DYNAMIC_CONTEXT_REMINDER_KEY: True},
        )
        user_msg = HumanMessage(
            content=original.content,
            id=f"{stable_id}__user",
            name=original.name,
            additional_kwargs=original.additional_kwargs,
        )
        return reminder_msg, user_msg

    def _inject(self, state, runtime: Runtime | None = None, *, connector_summaries: list[dict[str, Any]] | None = None) -> dict | None:
        messages = list(state.get("messages", []))
        if not messages:
            return None

        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        last_date = _last_injected_date(messages)
        connector_ids = _runtime_connector_ids(runtime)
        last_connector_ids = _last_injected_connector_ids(messages)
        connector_selection_changed = connector_ids != last_connector_ids if last_connector_ids is not None else bool(connector_ids)
        logger.debug(
            "DynamicContextMiddleware._inject: msg_count=%d last_date=%r current_date=%r connector_ids=%r last_connector_ids=%r",
            len(messages),
            last_date,
            current_date,
            connector_ids,
            last_connector_ids,
        )

        if last_date is None:
            # ── First turn: inject full reminder as a separate HumanMessage ─────
            first_idx = next((i for i, m in enumerate(messages) if _is_user_injection_target(m)), None)
            if first_idx is None:
                return None
            full_reminder = self._build_full_reminder(connector_ids=connector_ids, connector_summaries=connector_summaries)
            logger.info(
                "DynamicContextMiddleware: injecting full reminder (len=%d, has_memory=%s, has_connectors=%s) into first HumanMessage id=%r",
                len(full_reminder),
                "<memory>" in full_reminder,
                bool(connector_ids),
                messages[first_idx].id,
            )
            reminder_msg, user_msg = self._make_reminder_and_user_messages(messages[first_idx], full_reminder)
            return {"messages": [reminder_msg, user_msg]}

        if last_date == current_date and not connector_selection_changed:
            # ── Same day: nothing to do ──────────────────────────────────────────
            return None

        # ── Midnight crossed: inject date-update reminder as a separate HumanMessage ──
        last_human_idx = next((i for i in reversed(range(len(messages))) if _is_user_injection_target(messages[i])), None)
        if last_human_idx is None:
            return None

        if last_date != current_date and connector_selection_changed:
            reminder_content = self._build_date_and_connector_update_reminder(connector_ids, connector_summaries)
        elif last_date != current_date:
            reminder_content = self._build_date_update_reminder()
        else:
            reminder_content = self._build_connector_update_reminder(connector_ids, connector_summaries)

        reminder_msg, user_msg = self._make_reminder_and_user_messages(messages[last_human_idx], reminder_content)
        logger.info(
            "DynamicContextMiddleware: injected update reminder before current turn (date_changed=%s, connector_selection_changed=%s)",
            last_date != current_date,
            connector_selection_changed,
        )
        return {"messages": [reminder_msg, user_msg]}

    @override
    def before_agent(self, state, runtime: Runtime) -> dict | None:
        summaries = _load_selected_connector_summaries_sync(runtime, app_config=self._app_config)
        return self._inject(state, runtime, connector_summaries=summaries)

    @override
    async def abefore_agent(self, state, runtime: Runtime) -> dict | None:
        summaries = await _load_selected_connector_summaries(runtime, app_config=self._app_config)
        return self._inject(state, runtime, connector_summaries=summaries)
