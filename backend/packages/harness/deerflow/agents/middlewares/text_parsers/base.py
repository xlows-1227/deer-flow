"""Base utilities and parser class for text-embedded tool call parsing.

Shared infrastructure used by per-tool parser modules.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def parse_python_value(raw: str) -> Any:
    """Parse a Python-literal value (string, list, dict, number, bool, None).

    Handles:
      - "string"  / 'string'
      - [a, b, c]   (list, elements recursively parsed)
      - {k: v, ...}  (dict, keys/vals recursively parsed)
      - integers, floats, True, False, None
    """
    raw = raw.strip()
    if not raw:
        return raw

    # Try JSON first (covers dicts, lists, numbers, booleans, null)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Single-quoted string (JSON doesn't accept these)
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]

    # Unquoted identifiers
    if raw == "True":
        return True
    if raw == "False":
        return False
    if raw == "None":
        return None

    # Plain number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass

    # Fallback: return as-is
    return raw


def split_top_level_args(raw: str) -> list[str]:
    """Split comma-separated kwargs, respecting nested brackets."""
    args: list[str] = []
    depth = 0
    in_string: str | None = None
    current: list[str] = []

    i = 0
    while i < len(raw):
        ch = raw[i]

        if in_string:
            current.append(ch)
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch in ('"', "'"):
            in_string = ch
            current.append(ch)
            i += 1
            continue

        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1

        if ch == "," and depth == 0:
            arg = "".join(current).strip()
            if arg:
                args.append(arg)
            current = []
        else:
            current.append(ch)

        i += 1

    tail = "".join(current).strip()
    if tail:
        args.append(tail)

    return args


def parse_python_kwargs(raw: str) -> dict[str, Any] | None:
    """Parse Python-style keyword arguments into a dict.

    e.g. ``question="hi", options=["a", "b"], count=3``
         -> ``{"question": "hi", "options": ["a", "b"], "count": 3}``
    """
    pairs: dict[str, Any] = {}
    for arg in split_top_level_args(raw):
        eq_idx = arg.find("=")
        if eq_idx == -1:
            continue
        key = arg[:eq_idx].strip()
        value_raw = arg[eq_idx + 1:].strip()
        if not key:
            continue
        pairs[key] = parse_python_value(value_raw)
    return pairs or None


def parse_json_or_kwargs(raw: str) -> dict[str, Any]:
    """Parse a raw argument string as JSON dict or Python kwargs.

    Falls back to ``{"raw": raw}`` if neither matches.
    """
    raw = raw.strip()

    # 1. Try JSON
    try:
        args = json.loads(raw)
        if isinstance(args, dict):
            return args
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Try Python kwargs
    py_kwargs = parse_python_kwargs(raw)
    if py_kwargs is not None:
        return py_kwargs

    logger.debug("Text tool call parse fallback (unknown format): %s", raw[:200])
    return {"raw": raw}


class TextToolCallParser:
    """Pattern-based parser for a single text-embedded tool call.

    Attributes:
        tool_name: The tool name to emit in the converted ``tool_calls``.
            Set to empty string when the tool name is extracted from the match
            (see ``tool_name_group``).
        pattern: Compiled regex with capture groups.
        description: Human-readable hint for logging.
        labels: Field labels for multi-capture-group patterns.
            When set, the parser uses multi-group mode: each capture group
            maps to a label in order. When None, single-group mode is used:
            the first capture group is parsed as JSON or Python kwargs.
        tool_name_group: When set, the tool name is extracted from this
            capture group index instead of using the fixed ``tool_name``.
    """

    __slots__ = ("tool_name", "pattern", "description", "labels", "tool_name_group")

    def __init__(
        self,
        tool_name: str,
        pattern: str | re.Pattern[str],
        description: str = "",
        labels: list[str] | None = None,
        tool_name_group: int | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.pattern = re.compile(pattern, re.DOTALL) if isinstance(pattern, str) else pattern
        self.description = description or tool_name
        self.labels = labels
        self.tool_name_group = tool_name_group

    def get_tool_name(self, match: re.Match[str]) -> str:
        """Return the tool name for this match.

        Override in subclasses or set ``tool_name_group`` to extract
        the name from a capture group.
        """
        if self.tool_name_group is not None:
            return match.group(self.tool_name_group)
        return self.tool_name

    def parse(self, match: re.Match[str]) -> dict[str, Any]:
        """Extract tool arguments from the regex match.

        Two modes:
        - ``labels`` set: multi-capture-group mode, each group maps to a label.
        - ``labels`` None: single-capture-group mode, parsed as JSON or kwargs.
        """
        if self.labels and match.re.groups > 1:
            args: dict[str, Any] = {}
            for i, label in enumerate(self.labels, start=1):
                if i > match.re.groups:
                    break
                value = match.group(i)
                if value is not None:
                    value = value.strip()
                    if label == "options":
                        # Try JSON array first (e.g. ["北京", "上海"])
                        try:
                            parsed = json.loads(value)
                            if isinstance(parsed, list):
                                args[label] = parsed
                                continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                        # Fallback: multi-line list
                        args[label] = [
                            line.strip(" -*\t")
                            for line in value.splitlines()
                            if line.strip()
                        ]
                    else:
                        args[label] = value
            if args:
                return args
            return {"raw": match.group(0).strip()}

        # Single capture group: JSON or Python kwargs
        raw = match.group(1).strip()
        return parse_json_or_kwargs(raw)
