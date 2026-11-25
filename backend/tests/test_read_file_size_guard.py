"""Tests for the ``read_file`` pre-read size guard.

The guard must reject oversized files *before* any content is read into memory
and stay a no-op for files at or below the configured limit (and when the
limit is disabled with ``0``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

# Importing the agents package first breaks a pre-existing import cycle:
# deerflow.sandbox.tools -> deerflow.agents.thread_state -> agents/__init__
#   -> tools.builtins.generate_image_tool -> deerflow.sandbox.tools
import deerflow.agents  # noqa: F401
from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.tools import (
    _read_file_max_bytes,
    _too_large_file_message,
    read_file_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    workspace.mkdir()
    uploads.mkdir()
    outputs.mkdir()
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": str(workspace),
                "uploads_path": str(uploads),
                "outputs_path": str(outputs),
            },
        },
        context={"thread_id": "thread-1"},
    )


def _config(max_bytes: int, output_chars: int = 50_000) -> SimpleNamespace:
    return SimpleNamespace(
        sandbox=SimpleNamespace(
            read_file_max_bytes=max_bytes,
            read_file_output_max_chars=output_chars,
        ),
    )


class _TrackingLocalSandbox(LocalSandbox):
    """LocalSandbox that records how many times ``read_file`` was called."""

    def __init__(self) -> None:
        super().__init__(id="local")
        self.read_calls = 0

    def read_file(self, path: str) -> str:
        self.read_calls += 1
        return super().read_file(path)


class _FakeSandbox(Sandbox):
    """Minimal sandbox whose shell output is controlled by the test."""

    def __init__(self, size_output) -> None:
        super().__init__("fake")
        self._size_output = size_output
        self.commands: list[str] = []

    def execute_command(self, command: str) -> str:
        self.commands.append(command)
        if isinstance(self._size_output, Exception):
            raise self._size_output
        return self._size_output

    def read_file(self, path: str) -> str:  # pragma: no cover - not used
        return ""

    def download_file(self, path: str) -> bytes:  # pragma: no cover
        return b""

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:  # pragma: no cover
        return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:  # pragma: no cover
        return None

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200):  # pragma: no cover
        return [], False

    def grep(self, path: str, pattern: str, **kwargs):  # pragma: no cover
        return []

    def update_file(self, path: str, content: bytes) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Sandbox.get_file_size
# ---------------------------------------------------------------------------


class TestSandboxGetFileSize:
    def test_parses_stat_output(self):
        sandbox = _FakeSandbox("1234\n")
        assert sandbox.get_file_size("/mnt/user-data/workspace/a.txt") == 1234

    def test_ignores_leading_noise(self):
        sandbox = _FakeSandbox("warning: noisy\n2048\n")
        assert sandbox.get_file_size("/x") == 2048

    def test_returns_none_when_command_fails(self):
        sandbox = _FakeSandbox(RuntimeError("boom"))
        assert sandbox.get_file_size("/x") is None

    def test_returns_none_for_non_numeric_output(self):
        sandbox = _FakeSandbox("stat: cannot stat\n")
        assert sandbox.get_file_size("/x") is None

    def test_local_sandbox_reports_real_size(self, tmp_path):
        target = tmp_path / "payload.bin"
        target.write_bytes(b"x" * 321)
        sandbox = LocalSandbox(id="local")
        assert sandbox.get_file_size(str(target)) == 321

    def test_local_sandbox_returns_none_for_missing_file(self, tmp_path):
        sandbox = LocalSandbox(id="local")
        assert sandbox.get_file_size(str(tmp_path / "missing.bin")) is None


# ---------------------------------------------------------------------------
# _read_file_max_bytes
# ---------------------------------------------------------------------------


class TestReadFileMaxBytes:
    def test_reads_configured_value(self, monkeypatch):
        monkeypatch.setattr(
            "deerflow.sandbox.tools.get_app_config",
            lambda: _config(1234),
        )
        assert _read_file_max_bytes() == 1234

    def test_defaults_when_config_unavailable(self, monkeypatch):
        def _raise():
            raise RuntimeError("no config")

        monkeypatch.setattr("deerflow.sandbox.tools.get_app_config", _raise)
        assert _read_file_max_bytes() == 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# read_file_tool guard
# ---------------------------------------------------------------------------


class TestReadFileToolGuard:
    def _run(self, tmp_path, monkeypatch, *, max_bytes: int, rel_path: str, content: str):
        runtime = _make_runtime(tmp_path)
        (tmp_path / "workspace" / rel_path).write_text(content, encoding="utf-8")
        sandbox = _TrackingLocalSandbox()
        monkeypatch.setattr(
            "deerflow.sandbox.tools.ensure_sandbox_initialized",
            lambda _runtime: sandbox,
        )
        monkeypatch.setattr(
            "deerflow.sandbox.tools.get_app_config",
            lambda: _config(max_bytes),
        )
        result = read_file_tool.func(
            runtime=runtime,
            description="test read",
            path=f"/mnt/user-data/workspace/{rel_path}",
        )
        return result, sandbox

    def test_oversized_file_is_rejected_before_reading(self, tmp_path, monkeypatch):
        result, sandbox = self._run(
            tmp_path,
            monkeypatch,
            max_bytes=10,
            rel_path="big.txt",
            content="0123456789ABCDEF",
        )

        assert "too large to load into memory" in result
        assert "start_line`/`end_line" in result
        assert sandbox.read_calls == 0

    def test_file_at_limit_is_read_normally(self, tmp_path, monkeypatch):
        result, sandbox = self._run(
            tmp_path,
            monkeypatch,
            max_bytes=10,
            rel_path="exact.txt",
            content="0123456789",
        )

        assert result == "0123456789"
        assert sandbox.read_calls == 1

    def test_small_file_is_read_normally(self, tmp_path, monkeypatch):
        result, sandbox = self._run(
            tmp_path,
            monkeypatch,
            max_bytes=1024,
            rel_path="small.txt",
            content="hello",
        )

        assert result == "hello"
        assert sandbox.read_calls == 1

    def test_zero_disables_the_guard(self, tmp_path, monkeypatch):
        result, sandbox = self._run(
            tmp_path,
            monkeypatch,
            max_bytes=0,
            rel_path="big.txt",
            content="0123456789ABCDEF",
        )

        assert result == "0123456789ABCDEF"
        assert sandbox.read_calls == 1


# ---------------------------------------------------------------------------
# Skills host reads
# ---------------------------------------------------------------------------


class TestSkillsFileGuard:
    def test_oversized_skill_file_is_rejected(self, tmp_path, monkeypatch):
        runtime = _make_runtime(tmp_path)
        skills_dir = tmp_path / "skills"
        skill_file = skills_dir / "public" / "demo" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("x" * 64, encoding="utf-8")

        monkeypatch.setattr(
            "deerflow.sandbox.tools.get_app_config",
            lambda: _config(10),
        )

        with (
            patch("deerflow.sandbox.tools._get_skills_container_path", return_value="/mnt/skills"),
            patch("deerflow.sandbox.tools._get_skills_host_path", return_value=str(skills_dir)),
        ):
            result = read_file_tool.func(
                runtime=runtime,
                description="load skill",
                path="/mnt/skills/public/demo/SKILL.md",
            )

        assert "too large to load into memory" in result

    def test_small_skill_file_is_read_normally(self, tmp_path, monkeypatch):
        runtime = _make_runtime(tmp_path)
        skills_dir = tmp_path / "skills"
        skill_file = skills_dir / "public" / "demo" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# Demo\n", encoding="utf-8")

        monkeypatch.setattr(
            "deerflow.sandbox.tools.get_app_config",
            lambda: _config(1024),
        )

        with (
            patch("deerflow.sandbox.tools._get_skills_container_path", return_value="/mnt/skills"),
            patch("deerflow.sandbox.tools._get_skills_host_path", return_value=str(skills_dir)),
        ):
            result = read_file_tool.func(
                runtime=runtime,
                description="load skill",
                path="/mnt/skills/public/demo/SKILL.md",
            )

        assert "# Demo" in result


# ---------------------------------------------------------------------------
# Message helper
# ---------------------------------------------------------------------------


def test_too_large_message_mentions_size_and_alternatives():
    message = _too_large_file_message("/mnt/user-data/workspace/big.bin", 2048, 1024)
    assert "/mnt/user-data/workspace/big.bin" in message
    assert "2,048 bytes" in message
    assert "1,024 bytes" in message
    assert "bash" in message
    assert "start_line`/`end_line" in message
