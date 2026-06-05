"""Core behaviour tests for ReferencedFilesMiddleware.

Covers:
- _files_from_kwargs: parsing & validation of ``additional_kwargs.referenced_files``
- _resolve_library_path: filesystem mapping + traversal protection
- _load_file_content: text read, size coercion, missing files, binary handling,
  per-file truncation, aggregate-size cap
- before_agent: full injection pipeline (string & list content, preserved
  additional_kwargs, edge cases)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.referenced_files_middleware import (
    _MAX_PER_FILE_CHARS,
    _MAX_TOTAL_CHARS,
    ReferencedFilesMiddleware,
)
from deerflow.config.paths import Paths

USER_ID = "test-user-autouse"  # matches the autouse conftest fixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _middleware(tmp_path: Path) -> ReferencedFilesMiddleware:
    return ReferencedFilesMiddleware(base_dir=str(tmp_path))


def _runtime() -> MagicMock:
    rt = MagicMock()
    rt.context = {"thread_id": "thread-abc"}
    return rt


def _library_dir(tmp_path: Path) -> Path:
    d = Paths(str(tmp_path)).user_documents_dir(USER_ID)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _human(content, referenced_files=None, **extra_kwargs):
    additional_kwargs = dict(extra_kwargs)
    if referenced_files is not None:
        additional_kwargs["referenced_files"] = referenced_files
    return HumanMessage(content=content, additional_kwargs=additional_kwargs)


# ---------------------------------------------------------------------------
# _files_from_kwargs
# ---------------------------------------------------------------------------


class TestFilesFromKwargs:
    def test_returns_none_when_field_absent(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw._files_from_kwargs(HumanMessage(content="hi")) is None

    def test_returns_none_for_empty_list(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw._files_from_kwargs(_human("hi", referenced_files=[])) is None

    def test_returns_none_for_non_list(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw._files_from_kwargs(_human("hi", referenced_files="not-a-list")) is None

    def test_skips_non_dict_entries(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw._files_from_kwargs(_human("hi", referenced_files=["bad", 42, None])) is None

    def test_skips_entries_missing_id(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", referenced_files=[{"name": "x.txt", "path": "x.txt"}])
        assert mw._files_from_kwargs(msg) is None

    def test_skips_entries_missing_path(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", referenced_files=[{"id": "abc", "name": "x.txt"}])
        assert mw._files_from_kwargs(msg) is None

    def test_normalizes_extension_and_size(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human(
            "hi",
            referenced_files=[
                {
                    "id": "abc",
                    "name": "Notes.TXT",
                    "path": "research/Notes.TXT",
                    "mime_type": "text/plain",
                    "extension": ".TXT",
                    "size": "1024",
                },
            ],
        )
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert len(result) == 1
        file = result[0]
        assert file["id"] == "abc"
        assert file["name"] == "Notes.TXT"
        assert file["path"] == "research/Notes.TXT"
        assert file["size"] == 1024
        assert file["extension"] == ".TXT"

    def test_falls_back_to_basename_when_name_missing(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", referenced_files=[{"id": "abc", "path": "folder/Notes.md"}])
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["name"] == "Notes.md"

    def test_size_defaults_to_zero(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", referenced_files=[{"id": "abc", "path": "x.txt"}])
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["size"] == 0


# ---------------------------------------------------------------------------
# _resolve_library_path
# ---------------------------------------------------------------------------


class TestResolveLibraryPath:
    def test_resolves_to_documents_dir(self, tmp_path):
        mw = _middleware(tmp_path)
        docs = _library_dir(tmp_path)
        target = docs / "research" / "notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hi")
        resolved = mw._resolve_library_path("research/notes.md")
        assert resolved == target

    def test_rejects_traversal_with_dotdot(self, tmp_path):
        mw = _middleware(tmp_path)
        try:
            mw._resolve_library_path("../etc/passwd")
        except ValueError as exc:
            assert "traversal" in str(exc).lower()
        else:
            raise AssertionError("expected ValueError")


# ---------------------------------------------------------------------------
# _load_file_content
# ---------------------------------------------------------------------------


class TestLoadFileContent:
    def test_reads_text_file(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "notes.md").write_text(
            "hello world",
            encoding="utf-8",
        )
        file = {
            "id": "abc",
            "name": "notes.md",
            "path": "notes.md",
            "mime_type": "text/markdown",
            "extension": ".md",
            "size": 11,
        }
        result = mw._load_file_content(file)
        assert result["status"] == "ok"
        assert result["content"] == "hello world"
        assert result["truncated"] is False

    def test_reports_missing_file(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        file = {
            "id": "abc",
            "name": "ghost.md",
            "path": "ghost.md",
            "mime_type": "text/markdown",
            "extension": ".md",
            "size": 0,
        }
        result = mw._load_file_content(file)
        assert result["status"] == "missing"
        assert "no longer exists" in (result["note"] or "").lower()

    def test_reports_binary_for_image(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "pic.png").write_bytes(b"\x89PNG fake")
        file = {
            "id": "abc",
            "name": "pic.png",
            "path": "pic.png",
            "mime_type": "image/png",
            "extension": ".png",
            "size": 8,
        }
        result = mw._load_file_content(file)
        assert result["status"] == "binary"
        assert result["content"] == ""

    def test_truncates_oversized_text_file(self, tmp_path, monkeypatch):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        # Write a file larger than the per-file cap.
        big_text = "a" * (_MAX_PER_FILE_CHARS + 10_000)
        (mw._paths.user_documents_dir(USER_ID) / "big.txt").write_text(
            big_text,
            encoding="utf-8",
        )
        file = {
            "id": "abc",
            "name": "big.txt",
            "path": "big.txt",
            "mime_type": "text/plain",
            "extension": ".txt",
            "size": len(big_text),
        }
        result = mw._load_file_content(file)
        assert result["status"] == "ok"
        assert result["truncated"] is True
        assert len(result["content"]) <= _MAX_PER_FILE_CHARS

    def test_refreshes_size_from_disk(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "real.txt").write_text("real content here")
        file = {
            "id": "abc",
            "name": "real.txt",
            "path": "real.txt",
            "mime_type": "text/plain",
            "extension": ".txt",
            # Frontend reported a stale size from the picker.
            "size": 999_999,
        }
        result = mw._load_file_content(file)
        assert result["status"] == "ok"
        # The middleware overwrites the stale size with the on-disk value.
        assert result["size"] == len("real content here")


# ---------------------------------------------------------------------------
# before_agent
# ---------------------------------------------------------------------------


class TestBeforeAgent:
    def _state(self, *messages):
        return {"messages": list(messages)}

    def test_returns_none_when_messages_empty(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw.before_agent({"messages": []}, _runtime()) is None

    def test_returns_none_when_last_message_is_ai(self, tmp_path):
        mw = _middleware(tmp_path)
        state = self._state(HumanMessage(content="q"), AIMessage(content="a"))
        assert mw.before_agent(state, _runtime()) is None

    def test_returns_none_when_no_referenced_files(self, tmp_path):
        mw = _middleware(tmp_path)
        state = self._state(_human("plain message"))
        assert mw.before_agent(state, _runtime()) is None

    def test_inlines_text_file_into_string_content(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "report.md").write_text(
            "# Findings\n\nThe experiment worked.",
            encoding="utf-8",
        )
        msg = _human(
            "summarise please",
            referenced_files=[
                {
                    "id": "abc",
                    "name": "report.md",
                    "path": "report.md",
                    "mime_type": "text/markdown",
                    "extension": ".md",
                    "size": 50,
                },
            ],
        )
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert isinstance(content, str)
        assert "<referenced_files>" in content
        assert "</referenced_files>" in content
        assert "report.md" in content
        assert "Findings" in content
        assert "experiment worked" in content
        assert "summarise please" in content

    def test_inlines_into_list_content_preserves_image_blocks(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "data.txt").write_text("x,y,z", encoding="utf-8")
        msg = _human(
            [
                {"type": "text", "text": "analyse"},
                {"type": "image", "source_type": "base64", "data": "abc"},
            ],
            referenced_files=[
                {
                    "id": "abc",
                    "name": "data.txt",
                    "path": "data.txt",
                    "mime_type": "text/plain",
                    "extension": ".txt",
                    "size": 5,
                },
            ],
        )
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        updated = result["messages"][-1].content
        assert isinstance(updated, list)
        # The image block is preserved at the end of the list.
        assert any(b.get("type") == "image" for b in updated if isinstance(b, dict))
        combined = "\n".join(b.get("text", "") for b in updated if isinstance(b, dict))
        assert "<referenced_files>" in combined
        assert "data.txt" in combined
        assert "x,y,z" in combined
        assert "analyse" in combined

    def test_preserves_additional_kwargs_on_updated_message(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "f.txt").write_text("data", encoding="utf-8")
        files_meta = [
            {
                "id": "abc",
                "name": "f.txt",
                "path": "f.txt",
                "mime_type": "text/plain",
                "extension": ".txt",
                "size": 4,
            },
        ]
        msg = _human("check file", referenced_files=files_meta, element="task")
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        updated_kwargs = result["messages"][-1].additional_kwargs
        assert updated_kwargs.get("referenced_files") == files_meta
        assert updated_kwargs.get("element") == "task"

    def test_state_referenced_files_returns_loaded_records(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "f.txt").write_text("hello", encoding="utf-8")
        msg = _human(
            "review",
            referenced_files=[
                {
                    "id": "abc",
                    "name": "f.txt",
                    "path": "f.txt",
                    "mime_type": "text/plain",
                    "extension": ".txt",
                    "size": 5,
                },
            ],
        )
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        loaded = result["referenced_files"]
        assert len(loaded) == 1
        assert loaded[0]["id"] == "abc"
        assert loaded[0]["status"] == "ok"
        assert loaded[0]["content"] == "hello"

    def test_missing_file_still_emits_block_with_status_marker(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        # Note: we DON'T create the file on disk.
        msg = _human(
            "review",
            referenced_files=[
                {
                    "id": "abc",
                    "name": "ghost.txt",
                    "path": "ghost.txt",
                    "mime_type": "text/plain",
                    "extension": ".txt",
                    "size": 0,
                },
            ],
        )
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "<referenced_files>" in content
        assert "ghost.txt" in content
        # The status is surfaced inside the block so the model can react.
        assert "Status: missing" in content

    def test_aggregate_size_cap_caps_block(self, tmp_path, monkeypatch):
        """When total inlined content would exceed the cap, the middleware
        shrinks per-file slices and reports the budget exhaust."""
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        # Two files, each ~70% of the aggregate cap. Together they would
        # exceed the cap, so the second should be truncated.
        big = "x" * int(_MAX_TOTAL_CHARS * 0.7)
        (mw._paths.user_documents_dir(USER_ID) / "a.txt").write_text(big, encoding="utf-8")
        (mw._paths.user_documents_dir(USER_ID) / "b.txt").write_text(big, encoding="utf-8")

        msg = _human(
            "review both",
            referenced_files=[
                {
                    "id": "a",
                    "name": "a.txt",
                    "path": "a.txt",
                    "mime_type": "text/plain",
                    "extension": ".txt",
                    "size": len(big),
                },
                {
                    "id": "b",
                    "name": "b.txt",
                    "path": "b.txt",
                    "mime_type": "text/plain",
                    "extension": ".txt",
                    "size": len(big),
                },
            ],
        )
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        # The aggregate block is rendered, even when content is shrunk.
        assert "<referenced_files>" in content
        # At least one file is reported truncated.
        assert "Truncated: yes" in content

    def test_message_id_preserved(self, tmp_path):
        _library_dir(tmp_path)
        mw = _middleware(tmp_path)
        (mw._paths.user_documents_dir(USER_ID) / "f.txt").write_text("x", encoding="utf-8")
        msg = _human(
            "go",
            referenced_files=[
                {
                    "id": "abc",
                    "name": "f.txt",
                    "path": "f.txt",
                    "mime_type": "text/plain",
                    "extension": ".txt",
                    "size": 1,
                },
            ],
        )
        msg.id = "original-id-99"
        result = mw.before_agent(self._state(msg), _runtime())
        assert result is not None
        assert result["messages"][-1].id == "original-id-99"
