"""Summarization middleware extensions for DeerFlow.

This module implements Pi-agent-style compaction on top of LangChain's
SummarizationMiddleware:

- Structured summaries (Goal / Progress / Decisions / Next Steps / Files)
- Non-destructive history: raw messages remain in RunEventStore; only the
  LLM-facing ``messages`` channel is compacted.
- Compaction events are recorded via RunJournal so the frontend can render
  summary cards.
- Skill Rescue: recently-loaded skill file reads are preserved across
  summarization to avoid re-fetching.
- Dynamic Context Reminder protection: system-reminder injections are never
  summarized away.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol, override, runtime_checkable

from langchain.agents import AgentState
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, ToolMessage, get_buffer_string
from langgraph.config import get_config
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.dynamic_context_middleware import is_dynamic_context_reminder
from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structured summary prompt (Pi-agent style)
# ---------------------------------------------------------------------------

STRUCTURED_SUMMARY_PROMPT = """<role>
Context Extraction Assistant
</role>

<primary_objective>
Your sole objective is to extract the highest-quality, most relevant context from the conversation history below and produce a structured summary.
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must extract only the most important information to continue working toward the overall goal.
This structured summary will replace the conversation history presented below.
</objective_information>

<instructions>
Structure your summary using the following sections. Each section acts as a checklist — you must populate it with relevant information or explicitly state "None" if there is nothing to report:

## Goal
What is the user's primary goal or request? What overall task are you trying to accomplish? Be concise but complete.

## Constraints & Preferences
- [Requirements, constraints, or preferences mentioned by the user]

## Progress
### Done
- [x] [Completed tasks with enough detail to avoid re-doing them]

### In Progress
- [ ] [Current work that is partially done]

### Blocked
- [Issues or blockers, if any]

## Key Decisions
- **[Decision]**: [Rationale and context]

## Next Steps
1. [What should happen next, in priority order]

## Critical Context
- [Data, IDs, paths, or context needed to continue]

<read-files>
[List files that were READ during the conversation, one per line]
</read-files>

<modified-files>
[List files that were CREATED or MODIFIED during the conversation, one per line]
</modified-files>

Respond ONLY with the structured summary above. Do not include any additional text before or after it.
</instructions>

<messages>
Messages to summarize:
{messages}
</messages>"""  # noqa: E501

DEFAULT_SUMMARY_PROMPT = STRUCTURED_SUMMARY_PROMPT


# ---------------------------------------------------------------------------
# Event / hook types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SummarizationEvent:
    """Context emitted before conversation history is summarized away."""

    messages_to_summarize: tuple[AnyMessage, ...]
    preserved_messages: tuple[AnyMessage, ...]
    thread_id: str | None
    agent_name: str | None
    runtime: Runtime


@runtime_checkable
class BeforeSummarizationHook(Protocol):
    """Hook invoked before summarization removes messages from state."""

    def __call__(self, event: SummarizationEvent) -> None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_thread_id(runtime: Runtime) -> str | None:
    """Resolve the current thread ID from runtime context or LangGraph config."""
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        thread_id = config_data.get("configurable", {}).get("thread_id")
    return thread_id


def _resolve_agent_name(runtime: Runtime) -> str | None:
    """Resolve the current agent name from runtime context or LangGraph config."""
    agent_name = runtime.context.get("agent_name") if runtime.context else None
    if agent_name is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        agent_name = config_data.get("configurable", {}).get("agent_name")
    return agent_name


def _tool_call_path(tool_call: dict[str, Any]) -> str | None:
    """Best-effort extraction of a file path argument from a read_file-like tool call."""
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "filepath"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _clone_ai_message(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
    *,
    content: Any | None = None,
) -> AIMessage:
    """Clone an AIMessage while replacing its tool_calls list and optional content."""
    return clone_ai_message_with_tool_calls(message, tool_calls, content=content)


def _extract_file_ops(messages: list[AnyMessage]) -> tuple[list[str], list[str]]:
    """Scan messages for read/write file operations and return (read_files, modified_files)."""
    read_files: set[str] = set()
    modified_files: set[str] = set()

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") or ""
                path = _tool_call_path(tc)
                if not path:
                    continue
                if name in {"read_file", "read", "view", "cat"}:
                    read_files.add(path)
                elif name in {"write", "write_file", "edit", "edit_file"}:
                    modified_files.add(path)

    return sorted(read_files), sorted(modified_files)


def _record_compaction_event(
    runtime: Runtime,
    *,
    compacted_ids: list[str],
    preserved_count: int,
    summary: str,
    total_tokens_before: int,
    read_files: list[str],
    modified_files: list[str],
) -> None:
    """Write a compaction event to RunJournal so the frontend can render it."""
    if not runtime.context:
        return
    journal = runtime.context.get("__run_journal")
    if journal is None:
        return

    try:
        journal.record_middleware(
            tag="compaction",
            name="DeerFlowSummarizationMiddleware",
            hook="before_model",
            action="summarize",
            changes={
                "compacted_message_ids": compacted_ids,
                "preserved_message_count": preserved_count,
                "summary": summary,
                "total_tokens_before": total_tokens_before,
                "read_files": read_files,
                "modified_files": modified_files,
            },
        )
    except Exception:
        logger.debug("Failed to record compaction event to journal", exc_info=True)


def _summary_prompt_for_runtime(base_prompt: str, runtime: Runtime) -> str:
    """Return the active summary prompt, including optional manual-focus instructions."""
    instructions = runtime.context.get("compact_instructions") if runtime.context else None
    if not isinstance(instructions, str) or not instructions.strip():
        return base_prompt

    escaped_instructions = instructions.strip().replace("{", "{{").replace("}", "}}")
    return (
        f"{base_prompt.rstrip()}\n\n"
        "<custom_instructions>\n"
        f"{escaped_instructions}\n"
        "</custom_instructions>"
    )


# ---------------------------------------------------------------------------
# Skill bundle tracking
# ---------------------------------------------------------------------------

@dataclass
class _SkillBundle:
    """Skill-related tool calls and tool results associated with one AIMessage."""

    ai_index: int
    skill_tool_indices: tuple[int, ...]
    skill_tool_call_ids: frozenset[str]
    skill_tool_tokens: int
    skill_key: str


# ---------------------------------------------------------------------------
# Main middleware
# ---------------------------------------------------------------------------

class DeerFlowSummarizationMiddleware(SummarizationMiddleware):
    """Summarization middleware with structured summaries and compaction events."""

    def __init__(
        self,
        *args,
        skills_container_path: str | None = None,
        skill_file_read_tool_names: Collection[str] | None = None,
        before_summarization: list[BeforeSummarizationHook] | None = None,
        preserve_recent_skill_count: int = 5,
        preserve_recent_skill_tokens: int = 25_000,
        preserve_recent_skill_tokens_per_skill: int = 5_000,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._skills_container_path = skills_container_path or "/mnt/skills"
        self._skill_file_read_tool_names = frozenset(skill_file_read_tool_names or {"read_file", "read", "view", "cat"})
        self._before_summarization_hooks = before_summarization or []
        self._preserve_recent_skill_count = max(0, preserve_recent_skill_count)
        self._preserve_recent_skill_tokens = max(0, preserve_recent_skill_tokens)
        self._preserve_recent_skill_tokens_per_skill = max(0, preserve_recent_skill_tokens_per_skill)

    # -- Lifecycle hooks --

    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_summarize(state, runtime)

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return await self._amaybe_summarize(state, runtime)

    # -- Core summarization flow --

    def _prepare_compaction(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> tuple[list[AnyMessage], list[AnyMessage], int] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        force_compact = runtime.context.get("force_compact", False) if runtime.context else False

        if not force_compact and not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            if force_compact:
                logger.info(
                    "Force-compact requested but no safe turn boundary found "
                    "(recent turn too long or too few messages). Skipping compaction."
                )
            return None

        messages_to_summarize, preserved_messages = self._partition_with_skill_rescue(messages, cutoff_index)
        messages_to_summarize, preserved_messages = self._preserve_dynamic_context_reminders(messages_to_summarize, preserved_messages)

        if not messages_to_summarize:
            logger.info(
                "Compaction skipped because the safe cutoff only selected protected messages."
            )
            return None

        return messages_to_summarize, preserved_messages, total_tokens

    def _maybe_summarize(self, state: AgentState, runtime: Runtime) -> dict | None:
        prepared = self._prepare_compaction(state, runtime)
        if prepared is None:
            return None

        messages_to_summarize, preserved_messages, total_tokens = prepared
        self._fire_hooks(messages_to_summarize, preserved_messages, runtime)
        summary = self._create_summary_for_runtime(messages_to_summarize, runtime)
        new_messages = self._build_new_messages(summary)

        # --- Pi-style: record compaction event for the frontend ---
        compacted_ids = [m.id for m in messages_to_summarize if getattr(m, "id", None)]
        read_files, modified_files = _extract_file_ops(messages_to_summarize)
        _record_compaction_event(
            runtime,
            compacted_ids=compacted_ids,
            preserved_count=len(preserved_messages),
            summary=summary,
            total_tokens_before=total_tokens,
            read_files=read_files,
            modified_files=modified_files,
        )

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    async def _amaybe_summarize(self, state: AgentState, runtime: Runtime) -> dict | None:
        prepared = self._prepare_compaction(state, runtime)
        if prepared is None:
            return None

        messages_to_summarize, preserved_messages, total_tokens = prepared
        self._fire_hooks(messages_to_summarize, preserved_messages, runtime)
        summary = await self._acreate_summary_for_runtime(messages_to_summarize, runtime)
        new_messages = self._build_new_messages(summary)

        # --- Pi-style: record compaction event for the frontend ---
        compacted_ids = [m.id for m in messages_to_summarize if getattr(m, "id", None)]
        read_files, modified_files = _extract_file_ops(messages_to_summarize)
        _record_compaction_event(
            runtime,
            compacted_ids=compacted_ids,
            preserved_count=len(preserved_messages),
            summary=summary,
            total_tokens_before=total_tokens,
            read_files=read_files,
            modified_files=modified_files,
        )

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    @override
    def _build_new_messages(self, summary: str) -> list[HumanMessage]:
        """Override the base implementation to let the human message with the special name 'summary'.
        And this message will be ignored to display in the frontend, but still can be used as context for the model.
        """
        return [HumanMessage(content=f"Here is a summary of the conversation to date:\n\n{summary}", name="summary")]

    def _create_summary_for_runtime(self, messages_to_summarize: list[AnyMessage], runtime: Runtime) -> str:
        """Generate a summary with any runtime-provided manual compact instructions."""
        return self._create_summary_with_prompt(
            messages_to_summarize,
            _summary_prompt_for_runtime(self.summary_prompt, runtime),
        )

    async def _acreate_summary_for_runtime(self, messages_to_summarize: list[AnyMessage], runtime: Runtime) -> str:
        """Async variant of :meth:`_create_summary_for_runtime`."""
        return await self._acreate_summary_with_prompt(
            messages_to_summarize,
            _summary_prompt_for_runtime(self.summary_prompt, runtime),
        )

    def _create_summary_with_prompt(self, messages_to_summarize: list[AnyMessage], summary_prompt: str) -> str:
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."

        formatted_messages = get_buffer_string(trimmed_messages)
        try:
            response = self.model.invoke(
                summary_prompt.format(messages=formatted_messages).rstrip(),
                config={"metadata": {"lc_source": "summarization"}},
            )
            return response.text.strip()
        except Exception as exc:
            return f"Error generating summary: {exc!s}"

    async def _acreate_summary_with_prompt(self, messages_to_summarize: list[AnyMessage], summary_prompt: str) -> str:
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."

        formatted_messages = get_buffer_string(trimmed_messages)
        try:
            response = await self.model.ainvoke(
                summary_prompt.format(messages=formatted_messages).rstrip(),
                config={"metadata": {"lc_source": "summarization"}},
            )
            return response.text.strip()
        except Exception as exc:
            return f"Error generating summary: {exc!s}"

    # -- Split-turn protection (Pi-agent style) --

    def _find_safe_cutoff_point(
        self,
        messages: list[AnyMessage],
        cutoff_index: int,
    ) -> int:
        """Find safe cutoff that never splits a turn.

        In addition to the parent class AI/Tool pair protection, this ensures
        the cutoff always lands on a turn boundary (HumanMessage).

        A *turn* starts with a HumanMessage and includes all subsequent messages
        until the next HumanMessage.  If the raw cutoff falls inside a turn,
        it is pulled backward to the turn's start so the entire turn is
        preserved (not summarized).

        This matches Pi-agent behaviour where compaction only happens at
        turn boundaries, preventing orphaned tool-call / tool-result pairs
        and mid-turn context loss.
        """
        # 1. Apply parent class AI/Tool pair protection
        safe_cutoff = SummarizationMiddleware._find_safe_cutoff_point(messages, cutoff_index)

        if safe_cutoff <= 0 or safe_cutoff >= len(messages):
            return safe_cutoff

        # 2. Turn boundary protection: cutoff must land on a HumanMessage.
        if isinstance(messages[safe_cutoff], HumanMessage):
            return safe_cutoff

        # 3. Cutoff falls inside a turn. Walk backward to the turn boundary.
        for i in range(safe_cutoff - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                logger.debug(
                    "Split-turn protection: adjusted cutoff %d → %d (turn boundary)",
                    safe_cutoff,
                    i,
                )
                return i

        return 0

    # -- Dynamic context protection --

    def _preserve_dynamic_context_reminders(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Keep hidden dynamic-context reminders out of summary compression.

        These reminders carry the current date and optional memory. If summarization
        removes them, DynamicContextMiddleware can mistake the summary HumanMessage
        for the first user message and inject the reminder in the wrong place.
        """
        reminders = [msg for msg in messages_to_summarize if is_dynamic_context_reminder(msg)]
        if not reminders:
            return messages_to_summarize, preserved_messages

        remaining = [msg for msg in messages_to_summarize if not is_dynamic_context_reminder(msg)]
        return remaining, reminders + preserved_messages

    # -- Skill rescue --

    def _partition_with_skill_rescue(
        self,
        messages: list[AnyMessage],
        cutoff_index: int,
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Partition like the parent, then rescue recently-loaded skill bundles."""
        to_summarize, preserved = self._partition_messages(messages, cutoff_index)

        if self._preserve_recent_skill_count == 0 or self._preserve_recent_skill_tokens == 0 or not to_summarize:
            return to_summarize, preserved

        try:
            bundles = self._find_skill_bundles(to_summarize, self._skills_container_path)
        except Exception:
            logger.exception("Skill-preserving summarization rescue failed; falling back to default partition")
            return to_summarize, preserved

        if not bundles:
            return to_summarize, preserved

        rescue_bundles = self._select_bundles_to_rescue(bundles)
        if not rescue_bundles:
            return to_summarize, preserved

        bundles_by_ai_index = {bundle.ai_index: bundle for bundle in rescue_bundles}
        rescue_tool_indices = {idx for bundle in rescue_bundles for idx in bundle.skill_tool_indices}
        rescued: list[AnyMessage] = []
        remaining: list[AnyMessage] = []
        for i, msg in enumerate(to_summarize):
            bundle = bundles_by_ai_index.get(i)
            if bundle is not None and isinstance(msg, AIMessage):
                rescued_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") in bundle.skill_tool_call_ids]
                remaining_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") not in bundle.skill_tool_call_ids]

                if rescued_tool_calls:
                    rescued.append(_clone_ai_message(msg, rescued_tool_calls, content=""))
                if remaining_tool_calls or msg.content:
                    remaining.append(_clone_ai_message(msg, remaining_tool_calls))
                continue

            if i in rescue_tool_indices:
                rescued.append(msg)
                continue

            remaining.append(msg)

        return remaining, rescued + preserved

    def _find_skill_bundles(
        self,
        messages: list[AnyMessage],
        skills_root: str,
    ) -> list[_SkillBundle]:
        """Locate AIMessage + paired ToolMessage groups that load skill files."""
        bundles: list[_SkillBundle] = []
        n = len(messages)
        i = 0
        while i < n:
            msg = messages[i]
            if not (isinstance(msg, AIMessage) and msg.tool_calls):
                i += 1
                continue

            tool_calls = list(msg.tool_calls)
            skill_paths_by_id: dict[str, str] = {}
            for tc in tool_calls:
                if self._is_skill_tool_call(tc, skills_root):
                    tc_id = tc.get("id")
                    path = _tool_call_path(tc)
                    if tc_id and path:
                        skill_paths_by_id[tc_id] = path

            if not skill_paths_by_id:
                i += 1
                continue

            skill_tool_tokens = 0
            skill_key_parts: list[str] = []
            skill_tool_indices: list[int] = []
            matched_skill_call_ids: set[str] = set()

            j = i + 1
            while j < n and isinstance(messages[j], ToolMessage):
                j += 1

            for k in range(i + 1, j):
                tool_msg = messages[k]
                if isinstance(tool_msg, ToolMessage) and tool_msg.tool_call_id in skill_paths_by_id:
                    skill_tool_tokens += self.token_counter([tool_msg])
                    skill_key_parts.append(skill_paths_by_id[tool_msg.tool_call_id])
                    skill_tool_indices.append(k)
                    matched_skill_call_ids.add(tool_msg.tool_call_id)

            if not skill_tool_indices:
                i = j
                continue

            bundles.append(
                _SkillBundle(
                    ai_index=i,
                    skill_tool_indices=tuple(skill_tool_indices),
                    skill_tool_call_ids=frozenset(matched_skill_call_ids),
                    skill_tool_tokens=skill_tool_tokens,
                    skill_key="|".join(sorted(skill_key_parts)),
                )
            )
            i = j

        return bundles

    def _select_bundles_to_rescue(self, bundles: list[_SkillBundle]) -> list[_SkillBundle]:
        """Pick bundles to keep, walking newest-first under count/token budgets."""
        selected: list[_SkillBundle] = []
        if not bundles:
            return selected

        seen_skill_keys: set[str] = set()
        total_tokens = 0
        kept = 0

        for bundle in reversed(bundles):
            if kept >= self._preserve_recent_skill_count:
                break
            if bundle.skill_key in seen_skill_keys:
                continue
            if bundle.skill_tool_tokens > self._preserve_recent_skill_tokens_per_skill:
                continue
            if total_tokens + bundle.skill_tool_tokens > self._preserve_recent_skill_tokens:
                continue

            selected.append(bundle)
            total_tokens += bundle.skill_tool_tokens
            kept += 1
            seen_skill_keys.add(bundle.skill_key)

        selected.reverse()
        return selected

    def _is_skill_tool_call(self, tool_call: dict[str, Any], skills_root: str) -> bool:
        """Return True when ``tool_call`` reads a file under the configured skills root."""
        name = tool_call.get("name") or ""
        if name not in self._skill_file_read_tool_names:
            return False
        path = _tool_call_path(tool_call)
        if not path:
            return False
        normalized_root = skills_root.rstrip("/")
        return path == normalized_root or path.startswith(normalized_root + "/")

    def _fire_hooks(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
        runtime: Runtime,
    ) -> None:
        if not self._before_summarization_hooks:
            return

        event = SummarizationEvent(
            messages_to_summarize=tuple(messages_to_summarize),
            preserved_messages=tuple(preserved_messages),
            thread_id=_resolve_thread_id(runtime),
            agent_name=_resolve_agent_name(runtime),
            runtime=runtime,
        )

        for hook in self._before_summarization_hooks:
            try:
                hook(event)
            except Exception:
                hook_name = getattr(hook, "__name__", None) or type(hook).__name__
                logger.exception("before_summarization hook %s failed", hook_name)
