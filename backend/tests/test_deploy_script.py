"""Regression tests for the production Docker deploy script."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from shutil import which

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "deploy.sh"
BASH_CANDIDATES = [
    Path(r"C:\Program Files\Git\bin\bash.exe"),
    Path(which("bash")) if which("bash") else None,
]
BASH_EXECUTABLE = next(
    (str(path) for path in BASH_CANDIDATES if path is not None and path.exists() and "WindowsApps" not in str(path)),
    None,
)

if BASH_EXECUTABLE is None:
    pytestmark = pytest.mark.skip(reason="bash is required for deploy.sh tests")


def _run_script(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH_EXECUTABLE, str(SCRIPT_PATH), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _detect_mode_with_config(config_content: str) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        config_path = tmp_root / "config.yaml"
        config_path.write_text(config_content, encoding="utf-8")
        command = f"export DEER_FLOW_CONFIG_PATH='{config_path}' && source '{SCRIPT_PATH}' && detect_sandbox_mode"
        return subprocess.check_output(
            [BASH_EXECUTABLE, "-lc", command],
            text=True,
            encoding="utf-8",
        ).strip()


def test_help_exits_successfully_without_docker():
    result = _run_script("help")

    assert result.returncode == 0
    assert "Usage: scripts/deploy.sh" in result.stdout
    assert "status" in result.stdout
    assert "logs [service]" in result.stdout


def test_unknown_command_prints_usage():
    result = _run_script("bogus")

    assert result.returncode == 1
    assert "Unknown command: bogus" in result.stderr
    assert "Usage: scripts/deploy.sh" in result.stderr


def test_logs_rejects_unknown_service_before_docker_preflight():
    result = _run_script("logs", "unknown-service")

    assert result.returncode == 1
    assert "Unknown service for logs: unknown-service" in result.stderr
    assert "Usage: scripts/deploy.sh logs" in result.stderr


def test_config_does_not_print_better_auth_secret_value():
    env = os.environ.copy()
    env["BETTER_AUTH_SECRET"] = "super-secret-value-for-test"

    result = _run_script("config", env=env)

    assert result.returncode == 0
    assert "BETTER_AUTH_SECRET: set from environment" in result.stdout
    assert "super-secret-value-for-test" not in result.stdout


def test_detect_mode_defaults_to_local_when_config_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        missing_config = Path(tmpdir) / "missing.yaml"
        command = f"export DEER_FLOW_CONFIG_PATH='{missing_config}' && source '{SCRIPT_PATH}' && detect_sandbox_mode"
        output = subprocess.check_output(
            [BASH_EXECUTABLE, "-lc", command],
            text=True,
            encoding="utf-8",
        ).strip()

    assert output == "local"


def test_detect_mode_aio_without_provisioner_url():
    config = """
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
""".strip()

    assert _detect_mode_with_config(config) == "aio"


def test_detect_mode_provisioner_with_url():
    config = """
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
""".strip()

    assert _detect_mode_with_config(config) == "provisioner"


def test_detect_mode_ignores_commented_provisioner_url():
    config = """
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  # provisioner_url: http://provisioner:8002
""".strip()

    assert _detect_mode_with_config(config) == "aio"
