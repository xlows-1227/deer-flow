"""Safety filters for user memory content."""

from __future__ import annotations

import re
from collections.abc import Iterable

_UPLOAD_TAG_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://|postgresql://|mysql://|mongodb://|redis://|jdbc:)[^\s)>\"]+", re.IGNORECASE)
_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\[^\s]+|/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+){1,}|\\\\[^\s\\]+\\[^\s]+)",
)
_TOKEN_RE = re.compile(
    r"\b(?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|connection[_-]?string)\b\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_HOST_PORT_RE = re.compile(r"\b[a-zA-Z0-9.-]+\.(?:com|net|org|io|cn|local|internal):\d{2,5}\b")
_DB_HOST_RE = re.compile(r"\b(?:host|hostname|database|db|schema|table|bucket)\s*[:=]\s*[A-Za-z0-9_.\-:/]+", re.IGNORECASE)
_RESULT_DETAIL_RE = re.compile(
    r"\b(?:error|exception|traceback|failed|失败|报错|错误|连接串|数据库|表名|host|token|密钥|路径|URL)\b",
    re.IGNORECASE,
)


def scrub_memory_text(text: str) -> str:
    """Remove high-risk metadata and sensitive details from candidate memory text."""
    if not isinstance(text, str):
        return ""
    cleaned = _UPLOAD_TAG_RE.sub("", text)
    cleaned = _URL_RE.sub("[redacted-url]", cleaned)
    cleaned = _TOKEN_RE.sub("[redacted-secret]", cleaned)
    cleaned = _ABS_PATH_RE.sub("[redacted-path]", cleaned)
    cleaned = _HOST_PORT_RE.sub("[redacted-host]", cleaned)
    cleaned = _DB_HOST_RE.sub("[redacted-metadata]", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def is_high_risk_memory_text(text: str) -> bool:
    """Return whether text still appears to expose disallowed details."""
    if not isinstance(text, str) or not text.strip():
        return False
    return any(
        pattern.search(text)
        for pattern in (
            _URL_RE,
            _TOKEN_RE,
            _ABS_PATH_RE,
            _HOST_PORT_RE,
            _DB_HOST_RE,
        )
    )


def should_drop_memory_text(text: str) -> bool:
    """Return whether text should not be persisted as memory."""
    if not isinstance(text, str) or not text.strip():
        return True
    if is_high_risk_memory_text(text):
        return True
    # Avoid storing concrete task outcomes/problem details; keep rollups abstract.
    return bool(_RESULT_DETAIL_RE.search(text) and len(text) > 80)


def sanitize_memory_list(values: Iterable[str]) -> list[str]:
    """Scrub, deduplicate, and filter a list of memory strings."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = scrub_memory_text(value)
        key = cleaned.casefold()
        if not cleaned or key in seen or should_drop_memory_text(cleaned):
            continue
        seen.add(key)
        result.append(cleaned)
    return result
