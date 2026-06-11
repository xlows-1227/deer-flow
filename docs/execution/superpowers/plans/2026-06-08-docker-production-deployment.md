# Docker Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production Docker deployment easier to operate with script commands, Make aliases, and a Chinese deployment manual.

**Architecture:** Keep `scripts/deploy.sh` as the single production deployment entrypoint. Add small shell functions for argument parsing, preflight, config display, status/log/restart operations, and reuse the existing compose project name and compose file.

**Tech Stack:** Bash, Docker Compose, GNU/BSD-compatible shell utilities, Make, Markdown, pytest-based shell regression tests.

---

## File Structure

- Modify `scripts/deploy.sh`: add production command interface, preflight helpers, config/status/log/restart commands, and safer command dispatch.
- Modify `Makefile`: add production aliases that delegate to `scripts/deploy.sh`.
- Create `backend/tests/test_deploy_script.py`: regression tests for no-Docker script behavior and sandbox mode detection.
- Create `docs/DOCKER_PRODUCTION_DEPLOYMENT_zh.md`: Chinese production deployment manual.
- Modify `README_zh.md`: link the production Docker section to the new manual.

### Task 1: Add Failing Deploy Script Regression Tests

**Files:**
- Create: `backend/tests/test_deploy_script.py`
- Test: `backend/tests/test_deploy_script.py`

- [ ] **Step 1: Write failing tests for the new script interface**

```python
"""Regression tests for the production Docker deploy script."""

from __future__ import annotations

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
        command = (
            f"source '{SCRIPT_PATH}' && "
            f"DEER_FLOW_CONFIG_PATH='{config_path}' && "
            "detect_sandbox_mode"
        )
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


def test_detect_mode_defaults_to_local_when_config_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        missing_config = Path(tmpdir) / "missing.yaml"
        command = (
            f"source '{SCRIPT_PATH}' && "
            f"DEER_FLOW_CONFIG_PATH='{missing_config}' && "
            "detect_sandbox_mode"
        )
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
```

- [ ] **Step 2: Run the tests to verify they fail before implementation**

Run: `cd backend && uv run pytest tests/test_deploy_script.py -v`

Expected: tests fail because `help` and source-safe command dispatch are not implemented.

### Task 2: Implement Deploy Script Operations

**Files:**
- Modify: `scripts/deploy.sh`
- Test: `backend/tests/test_deploy_script.py`

- [ ] **Step 1: Add command parsing and source guard**

Implement `usage`, `parse_args`, and `main`, and call `main "$@"` only when the script is executed directly:

```bash
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
```

- [ ] **Step 2: Add preflight and configuration helpers**

Implement helpers named `docker_available`, `compose_available`, `ensure_runtime_paths`, `ensure_better_auth_secret`, `detect_sandbox_mode`, `print_config`, `require_docker`, and `select_services`.

- [ ] **Step 3: Add operations**

Implement `build`, `start`, `up`, `down`, `status`, `logs`, `restart`, and `config` operations. `logs` accepts no service or one of `nginx`, `frontend`, `gateway`, `provisioner`.

- [ ] **Step 4: Run deploy script tests**

Run: `cd backend && uv run pytest tests/test_deploy_script.py -v`

Expected: all tests pass.

### Task 3: Add Makefile Production Aliases

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add phony targets**

Add `prod-config`, `prod-status`, `prod-logs`, and `prod-restart` to `.PHONY`.

- [ ] **Step 2: Add help text**

Add help rows under Docker Production Commands:

```make
	@echo "  make prod-config     - Show resolved production Docker deployment config"
	@echo "  make prod-status     - Show production Docker container status"
	@echo "  make prod-logs       - Tail production Docker logs"
	@echo "  make prod-restart    - Restart production Docker services"
```

- [ ] **Step 3: Add target implementations**

```make
prod-config:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh config

prod-status:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh status

prod-logs:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh logs

prod-restart:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh restart
```

### Task 4: Add Chinese Production Deployment Manual

**Files:**
- Create: `docs/DOCKER_PRODUCTION_DEPLOYMENT_zh.md`

- [ ] **Step 1: Write the manual**

Include sections for overview, server requirements, first deployment, config files, environment variables, operations, updates, sandbox modes, security, and troubleshooting.

- [ ] **Step 2: Ensure commands match script and Makefile**

Check that the manual references `make up`, `make down`, `make prod-config`, `make prod-status`, `make prod-logs`, `make prod-restart`, and `scripts/deploy.sh logs [service]`.

### Task 5: Link Manual From Chinese README

**Files:**
- Modify: `README_zh.md`

- [ ] **Step 1: Add the manual link**

In the Docker production section, add:

```markdown
完整服务器部署、环境变量、更新、日志和排障流程见 [Docker 生产部署手册](docs/DOCKER_PRODUCTION_DEPLOYMENT_zh.md)。
```

### Task 6: Verify And Review

**Files:**
- All changed files

- [ ] **Step 1: Run syntax and unit checks**

Run:

```bash
bash -n scripts/deploy.sh
cd backend && uv run pytest tests/test_deploy_script.py tests/test_docker_sandbox_mode_detection.py -v
```

- [ ] **Step 2: Run script smoke checks**

Run:

```bash
scripts/deploy.sh help
scripts/deploy.sh config
```

- [ ] **Step 3: Run compose config when Docker is available**

Run:

```bash
docker compose -p deer-flow -f docker/docker-compose.yaml config
```

If Docker is unavailable, record that runtime compose verification was skipped.

- [ ] **Step 4: Review the diff**

Run: `git diff -- scripts/deploy.sh Makefile docs/DOCKER_PRODUCTION_DEPLOYMENT_zh.md README_zh.md backend/tests/test_deploy_script.py`

Expected: changes match the design, do not print secrets, and do not modify Docker development behavior.
