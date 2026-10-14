"""Parser for DSML / XML-style tool call blobs.

Some models emit tool calls as XML instead of structured tool_calls or
Python-style function-call text.  We look for <invoke> tags regardless of
outer wrapper (DSML, tool_calls, or none).
"""

from __future__ import annotations

import re
from typing import Any

from .base import TextToolCallParser


# Tag prefix that tolerates every observed wrapper form:
#   <invoke>, <DSML|invoke>, <|DSML|invoke>, <||DSML||invoke>
# The model-specific control tokens are pipe-delimited with a variable
# number of pipes, so we allow any mix of pipes/whitespace around "DSML".
_TAG_PREFIX = r"[|\s]*(?:DSML)?[|\s]*"

_INVOKE_PATTERN = re.compile(
    rf"<\s*{_TAG_PREFIX}invoke\b[^>]*name\s*=\s*[\"']([^\"']+)[\"'][^>]*>"
    rf"(.*?)</\s*{_TAG_PREFIX}invoke\s*>",
    re.DOTALL | re.IGNORECASE,
)

_PARAM_PATTERN = re.compile(
    rf"<\s*{_TAG_PREFIX}parameter\b[^>]*name\s*=\s*[\"']([^\"']+)[\"'][^>]*>"
    rf"(.*?)</\s*{_TAG_PREFIX}parameter\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Reasoning-only fields some models attach that are not valid tool args.
_IGNORED_PARAMS = {"explanation"}


class DsmlToolCallParser(TextToolCallParser):
    """Parse DSML / XML-style <invoke> blocks into tool_call dicts.

    The tool name is extracted dynamically from the name="..." attribute,
    so this works for ANY tool the model references.  Parameters are
    collected from every <parameter> child tag.
    """

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            tool_name="",
            pattern=_INVOKE_PATTERN,
            description="DSML XML-style <invoke> parser",
            tool_name_group=1,
        )

    def get_tool_name(self, match: re.Match[str]) -> str:
        return match.group(1).strip()

    def parse(self, match: re.Match[str]) -> dict[str, Any]:
        inner = match.group(2) or ""
        args: dict[str, Any] = {}
        for pm in _PARAM_PATTERN.finditer(inner):
            key = pm.group(1).strip()
            val = pm.group(2).strip()
            if key and key.lower() not in _IGNORED_PARAMS:
                args[key] = val
        return args or {}


PARSERS: list[TextToolCallParser] = [DsmlToolCallParser()]
