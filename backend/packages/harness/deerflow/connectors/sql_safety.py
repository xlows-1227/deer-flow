from __future__ import annotations

import hashlib
import re

from deerflow.connectors.errors import ConnectorSqlSafetyError
from deerflow.connectors.schemas import DatabasePolicy, SqlSafetyResult

_DENIED_KEYWORDS = {
    "alter",
    "analyze",
    "admin",
    "call",
    "create",
    "delete",
    "desc",
    "describe",
    "drop",
    "explain",
    "grant",
    "insert",
    "kill",
    "load",
    "merge",
    "optimize",
    "rename",
    "replace",
    "revoke",
    "set",
    "show",
    "truncate",
    "update",
    "use",
}

_TABLE_RE = re.compile(
    r"\b(?:from|join)\s+((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][\w$]*))?)",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\blimit\s+\d+\b", re.IGNORECASE)


def _strip_identifier(value: str) -> str:
    return value.strip().strip("`").strip('"')


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments while preserving comment-like text inside quoted literals.

    Supports ``--`` / ``#`` line comments and ``/* ... */`` block comments.
    Quoted regions cover single quotes, double quotes, and backtick identifiers.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                cur = sql[i]
                out.append(cur)
                if cur == quote:
                    # SQL escaped quote: '' or "" or ``
                    if i + 1 < n and sql[i + 1] == quote:
                        out.append(quote)
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            while i < n and sql[i] not in ("\n", "\r"):
                i += 1
            out.append(" ")
            continue
        if ch == "#":
            i += 1
            while i < n and sql[i] not in ("\n", "\r"):
                i += 1
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < n else n
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).strip()


def _without_single_trailing_semicolon(sql: str) -> str:
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if ";" in stripped:
        raise ConnectorSqlSafetyError("Multiple SQL statements are not allowed", recoverable=True)
    return stripped


def _remove_string_literals(sql: str) -> str:
    """Replace quoted string literals with empty placeholders so keywords inside strings are not matched."""
    # Remove single-quoted strings (best-effort; nested quotes not supported).
    no_single = re.sub(r"'[^']*'", "''", sql)
    # Remove double-quoted strings.
    return re.sub(r'"[^"]*"', '""', no_single)


def extract_tables(sql: str, *, default_schema: str | None = None) -> list[str]:
    tables: list[str] = []
    for match in _TABLE_RE.finditer(sql):
        raw = re.sub(r"\s+", "", match.group(1))
        parts = [_strip_identifier(part) for part in raw.split(".")]
        if len(parts) == 1 and default_schema:
            table = f"{default_schema}.{parts[0]}"
        else:
            table = ".".join(parts)
        if table and table not in tables:
            tables.append(table)
    return tables


def _is_select_or_with_select(sql: str) -> bool:
    first = re.match(r"^\s*([A-Za-z]+)", sql)
    if not first:
        return False
    keyword = first.group(1).lower()
    if keyword == "select":
        return True
    if keyword == "with":
        cleaned = _remove_string_literals(sql)
        return re.search(r"\bselect\b", cleaned, re.IGNORECASE) is not None
    return False


def _check_denied_keywords(sql: str) -> None:
    cleaned = _remove_string_literals(sql)
    for keyword in re.findall(r"\b[A-Za-z_]+\b", cleaned.lower()):
        if keyword in _DENIED_KEYWORDS:
            raise ConnectorSqlSafetyError(f"SQL keyword is not allowed for read-only connectors: {keyword}", recoverable=True)


def _check_table_policy(tables: list[str], policy: DatabasePolicy) -> None:
    blocked = {item.lower() for item in policy.blocked_tables}
    allowed_schemas = {item.lower() for item in policy.allowed_schemas or []}
    allowed_tables = {item.lower() for item in policy.allowed_tables or []}
    for table in tables:
        lower = table.lower()
        short = lower.split(".")[-1]
        schema = lower.split(".")[0] if "." in lower else None
        if lower in blocked or short in blocked:
            raise ConnectorSqlSafetyError(f"Table is blocked by connector policy: {table}", recoverable=True)
        if allowed_schemas and schema not in allowed_schemas:
            raise ConnectorSqlSafetyError(f"Schema is not allowed by connector policy: {schema or '(unknown)'}", recoverable=True)
        if allowed_tables and lower not in allowed_tables and short not in allowed_tables:
            raise ConnectorSqlSafetyError(f"Table is not allowed by connector policy: {table}", recoverable=True)


def validate_read_only_sql(
    sql: str,
    *,
    policy: DatabasePolicy,
    dialect: str,
    default_schema: str | None = None,
) -> SqlSafetyResult:
    del dialect  # Kept in the API so future parser-backed validation can branch per connector type.
    cleaned = _without_single_trailing_semicolon(_strip_sql_comments(sql))
    normalized = _normalize_sql(cleaned)
    if not normalized:
        raise ConnectorSqlSafetyError("SQL is empty", recoverable=True)
    if not _is_select_or_with_select(normalized):
        raise ConnectorSqlSafetyError("Only SELECT and WITH ... SELECT statements are allowed", recoverable=True)
    _check_denied_keywords(normalized)
    tables = extract_tables(normalized, default_schema=default_schema)
    _check_table_policy(tables, policy)
    safe_sql = normalized
    if not _LIMIT_RE.search(normalized):
        if policy.require_limit:
            safe_sql = f"{normalized} LIMIT {policy.max_rows}"
        else:
            safe_sql = normalized
    preview = safe_sql[:500]
    sql_hash = "sha256:" + hashlib.sha256(safe_sql.encode("utf-8")).hexdigest()
    return SqlSafetyResult(sql=safe_sql, tables=tables, normalized_preview=preview, sql_hash=sql_hash)
