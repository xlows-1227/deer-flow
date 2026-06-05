"""Middleware to inject `@`-referenced file contents into agent context.

The chat input's `@`-mention picker lets the user point at files that already
live in their per-user document library (see ``/api/files``). When the user
submits a message, the frontend forwards those picks as
``additional_kwargs.referenced_files`` on the human message. This middleware
reads them back, loads the actual file content from the host filesystem, and
prepends a ``<referenced_files>`` block to the message so the model can
immediately see and reason about the files.

Unlike :class:`UploadsMiddleware` (which only emits metadata for files in the
sandbox-mounted uploads dir), this middleware *inlines the file content*
because the user library is NOT mounted into the sandbox. The model would
otherwise be unable to use ``read_file``/``grep``/``glob`` tools to fetch
the bytes.

The middleware is intentionally conservative about size:
- Each file's content is capped at ``_MAX_PER_FILE_CHARS`` characters
- The aggregate block is capped at ``_MAX_TOTAL_CHARS`` characters
- Beyond those limits, content is truncated with an explicit marker so the
  model knows it's looking at a partial view.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------

# Per-file content cap. Bigger than the typical "summarize this" use case
# (a few pages of prose) but small enough to keep a single reference from
# blowing up the conversation.
_MAX_PER_FILE_CHARS = 200_000  # ~200 KB of text, plenty for most use cases

# Aggregate cap across all referenced files in a single message. This is
# the hard ceiling on how much the `<referenced_files>` block can grow
# before we start truncating individual entries.
_MAX_TOTAL_CHARS = 1_000_000  # 1 MB

# File extensions we treat as "read raw text". Includes common code, data,
# and config formats. Matching is case-insensitive on the suffix.
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".xml",
        ".html",
        ".htm",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".log",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".html",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".sql",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".pl",
        ".lua",
        ".r",
        ".dart",
        ".vue",
        ".svelte",
    }
)

# File extensions the model is most likely to *want* to summarize. For
# these we attempt on-the-fly conversion to text (sync, single-thread)
# when the raw text path doesn't apply.
_CONVERTIBLE_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)


def _format_size(size_bytes: int) -> str:
    """Render a byte count as a human-friendly string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _is_probably_text(file_path: Path, mime_type: str | None) -> bool:
    """Heuristic: is this file safe to read as UTF-8 text?"""
    ext = file_path.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return True
    if mime_type and (
        mime_type.startswith("text/")
        or mime_type in {"application/json", "application/xml", "application/x-yaml"}
    ):
        return True
    return False


def _try_convert_to_text(file_path: Path) -> str | None:
    """Best-effort sync conversion of a binary document to plain text.

    Returns ``None`` if no converter is available, the file is unsupported,
    or the conversion fails. The caller falls back to metadata-only in
    those cases.
    """
    try:
        # Prefer the lighter path first: a sync ``MarkItDown`` call.
        # We avoid the async ``convert_file_to_markdown`` helper because
        # ``before_agent`` runs in a sync context and we don't want to
        # spin up a thread pool for every chat turn.
        from markitdown import MarkItDown
    except ImportError:
        return None
    try:
        md = MarkItDown()
        return md.convert(str(file_path)).text_content
    except Exception:
        logger.exception("markitdown failed to convert %s", file_path.name)
        return None


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cap *text* at *limit* characters.

    Returns ``(text, truncated)`` where ``truncated`` is True if the
    input was cut. We try to break on a newline boundary near the limit
    so the model doesn't see mid-line truncations.
    """
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    last_nl = window.rfind("\n")
    if last_nl > limit // 2:
        return window[:last_nl] + "\n", True
    return window, True


# ----------------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------------


class ReferencedFilesMiddlewareState(AgentState):
    """State schema for referenced-files middleware."""

    referenced_files: NotRequired[list[dict] | None]


class ReferencedFilesMiddleware(AgentMiddleware[ReferencedFilesMiddlewareState]):
    """Inject content of `@`-picked library files into the agent context.

    Reads file metadata from the current message's
    ``additional_kwargs.referenced_files`` (set by the chat input's
    `@`-mention picker) and prepends a ``<referenced_files>`` block to
    the last human message so the model can read the content directly.

    Because the user document library is NOT mounted inside the agent's
    sandbox, the model can't use ``read_file`` / ``grep`` / ``glob`` to
    pull the bytes. We inline the content (text files verbatim, binary
    documents via ``markitdown``) so the model can answer
    "summarize this file" without making a tool call that would fail.
    """

    state_schema = ReferencedFilesMiddlewareState

    def __init__(self, base_dir: str | None = None) -> None:
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _files_from_kwargs(self, message: HumanMessage) -> list[dict] | None:
        """Extract and validate ``additional_kwargs.referenced_files``.

        Each entry must be a dict with at least ``id`` and ``path`` keys.
        ``path`` is the library-relative POSIX path. We deliberately
        *don't* check existence here — that's the job of
        :meth:`_resolve_library_path` so we can return a clear error per
        file in the rendered block.
        """
        kwargs_files = (message.additional_kwargs or {}).get("referenced_files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files: list[dict] = []
        for entry in kwargs_files:
            if not isinstance(entry, dict):
                continue
            file_id = entry.get("id")
            library_path = entry.get("path")
            name = entry.get("name") or ""
            if not isinstance(file_id, str) or not file_id:
                continue
            if not isinstance(library_path, str) or not library_path:
                continue
            files.append(
                {
                    "id": file_id,
                    "name": name or Path(library_path).name,
                    "path": library_path,
                    "mime_type": entry.get("mime_type"),
                    "extension": entry.get("extension") or Path(library_path).suffix.lower(),
                    "size": int(entry.get("size") or 0),
                }
            )
        return files if files else None

    def _resolve_library_path(self, library_path: str) -> Path:
        """Map a library-relative path to its absolute host location.

        Raises:
            ValueError: on path traversal attempts.
        """
        # ``Paths.user_documents_dir`` already mkdirs the directory; we
        # don't want that side-effect on every read, so resolve manually.
        docs_root = self._paths.user_documents_dir(get_effective_user_id()).resolve()
        # Reject any traversal attempt early.
        if ".." in Path(library_path).parts:
            raise ValueError(f"Path traversal in library path: {library_path!r}")
        target = (docs_root / library_path).resolve()
        try:
            target.relative_to(docs_root)
        except ValueError as exc:
            raise ValueError(f"Library path escapes root: {library_path!r}") from exc
        return target

    # ------------------------------------------------------------------
    # Content loading
    # ------------------------------------------------------------------

    def _load_file_content(self, file: dict) -> dict[str, Any]:
        """Read a single library file and produce a render-ready dict.

        Returns a dict with ``status`` (one of ``"ok"``, ``"missing"``,
        ``"too_large"``, ``"unreadable"``, ``"binary"``) plus the
        original metadata and the (possibly truncated) content.
        """
        result: dict[str, Any] = {
            **file,
            "status": "ok",
            "content": "",
            "truncated": False,
            "note": None,
        }
        try:
            abs_path = self._resolve_library_path(file["path"])
        except ValueError as exc:
            result["status"] = "unreadable"
            result["note"] = str(exc)
            return result

        if not abs_path.is_file():
            result["status"] = "missing"
            result["note"] = "File no longer exists in the library."
            return result

        # Refresh size from the filesystem if it disagrees with the
        # frontend's reported size — the user might have edited the file
        # in the file-management page between picker and submit.
        try:
            stat = abs_path.stat()
        except OSError as exc:
            result["status"] = "unreadable"
            result["note"] = f"Failed to stat file: {exc}"
            return result
        result["size"] = stat.st_size

        mime_type, _ = mimetypes.guess_type(abs_path.name)
        # Image files: emit a short marker so the model knows the file
        # is referenced; the model can't see it without Vision support
        # anyway, and inlining the bytes would blow the context.
        if mime_type and mime_type.startswith("image/"):
            result["status"] = "binary"
            result["note"] = (
                "Image file — content not inlined. If your model has vision, "
                "download from the file library and attach it to the chat as an image."
            )
            return result

        # Try to extract a textual representation.
        if _is_probably_text(abs_path, mime_type):
            try:
                content = abs_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Fall back to a permissive read so binary files with
                # text-y extensions don't blow up the agent run.
                try:
                    content = abs_path.read_text(encoding="utf-8", errors="replace")
                    result["note"] = "File contains non-UTF-8 bytes; some characters were replaced."
                except OSError as exc:
                    result["status"] = "unreadable"
                    result["note"] = f"Failed to read file: {exc}"
                    return result
            except OSError as exc:
                result["status"] = "unreadable"
                result["note"] = f"Failed to read file: {exc}"
                return result
        elif abs_path.suffix.lower() in _CONVERTIBLE_EXTENSIONS:
            converted = _try_convert_to_text(abs_path)
            if converted is None:
                result["status"] = "binary"
                result["note"] = (
                    "Document conversion is not available on this server. "
                    "Open the file in the file library to convert it to Markdown first."
                )
                return result
            content = converted
        else:
            result["status"] = "binary"
            result["note"] = "Binary file — content not inlined."
            return result

        content, truncated = _truncate(content, _MAX_PER_FILE_CHARS)
        result["content"] = content
        result["truncated"] = truncated
        return result

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _format_file_entry(self, file: dict[str, Any], lines: list[str], remaining_budget: int) -> int:
        """Append a single file's section to *lines*; return updated budget.

        The budget is the remaining aggregate character limit. We shrink
        each subsequent file's individual cap to fit the budget so the
        whole block never exceeds ``_MAX_TOTAL_CHARS``.
        """
        name = file.get("name") or file.get("path", "<unnamed>")
        size_str = _format_size(int(file.get("size") or 0))
        status = file.get("status", "ok")
        lines.append(f"### `{name}` ({size_str})")
        lines.append(f"- Library path: `{file.get('path', '')}`")
        if file.get("extension"):
            lines.append(f"- Extension: `{file['extension']}`")
        if file.get("mime_type"):
            lines.append(f"- MIME type: `{file['mime_type']}`")

        if status == "ok":
            content = file.get("content") or ""
            budget_for_this = min(len(content), remaining_budget)
            if budget_for_this < len(content):
                content = content[:budget_for_this]
                file["truncated"] = True
                note = file.get("note") or "Aggregated block size limit reached."
                file["note"] = f"{note} Showing first {budget_for_this} characters."
            lines.append(f"- Status: included ({len(content)} chars)")
            if file.get("truncated"):
                lines.append(
                    f"- Truncated: yes (per-file cap is {_MAX_PER_FILE_CHARS:,} chars)",
                )
            if file.get("note"):
                lines.append(f"- Note: {file['note']}")
            lines.append("")
            lines.append("```")
            lines.append(content)
            lines.append("```")
            return max(remaining_budget - len(content), 0)
        # Non-ok statuses: no content, just metadata + note.
        lines.append(f"- Status: {status}")
        if file.get("note"):
            lines.append(f"- Note: {file['note']}")
        lines.append("")
        return remaining_budget

    def _create_referenced_files_message(self, files: list[dict[str, Any]]) -> str:
        """Render the ``<referenced_files>`` block.

        Honors the aggregate ``_MAX_TOTAL_CHARS`` cap by shrinking each
        file's per-file slice as we go, so the block stays bounded even
        when the user pastes in a giant library.
        """
        lines: list[str] = ["<referenced_files>"]
        lines.append("The following files were referenced from your library via `@` mentions. Their content is inlined below so the model can answer without making tool calls.")
        lines.append("")
        if not files:
            lines.append("(no files)")
        else:
            budget = _MAX_TOTAL_CHARS
            for file in files:
                budget = self._format_file_entry(file, lines, budget)
                if budget <= 0:
                    lines.append("")
                    lines.append(
                        f"_(remaining {len(files) - files.index(file) - 1} file(s) omitted — aggregate size cap of {_MAX_TOTAL_CHARS:,} chars reached)_"
                    )
                    break
        lines.append("</referenced_files>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @override
    def before_agent(self, state: ReferencedFilesMiddlewareState, runtime: Runtime) -> dict | None:
        """Inject referenced-files content before agent execution.

        Reads ``additional_kwargs.referenced_files`` from the last human
        message, loads each file's content from the user document library,
        and prepends a ``<referenced_files>`` block to the message. The
        original ``additional_kwargs`` (including the structured
        ``referenced_files`` array) is preserved so the frontend can
        still render the chips from the streamed message.
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]
        if not isinstance(last_message, HumanMessage):
            return None

        picked = self._files_from_kwargs(last_message)
        if not picked:
            return None

        loaded: list[dict[str, Any]] = []
        for file in picked:
            try:
                loaded.append(self._load_file_content(file))
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Failed to load referenced file %s", file.get("path"))
                loaded.append(
                    {
                        **file,
                        "status": "unreadable",
                        "content": "",
                        "truncated": False,
                        "note": f"Failed to load file: {exc}",
                    },
                )

        files_block = self._create_referenced_files_message(loaded)

        # Splice the block into the last human message, preserving the
        # original content shape (string OR multimodal list) so any image
        # attachments in the same turn still go to the model intact.
        original_content = last_message.content
        if isinstance(original_content, str):
            updated_content = f"{files_block}\n\n{original_content}"
        elif isinstance(original_content, list):
            files_block_item = {"type": "text", "text": f"{files_block}\n\n"}
            updated_content = [files_block_item, *original_content]
        else:
            updated_content = original_content

        updated_message = HumanMessage(
            content=updated_content,
            id=last_message.id,
            name=last_message.name,
            additional_kwargs=last_message.additional_kwargs,
        )
        messages[last_message_index] = updated_message

        return {
            "referenced_files": loaded,
            "messages": messages,
        }
