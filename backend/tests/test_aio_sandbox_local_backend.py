import logging
import os
import subprocess
from types import SimpleNamespace

from deerflow.community.aio_sandbox import backend as sandbox_backend
from deerflow.community.aio_sandbox.local_backend import (
    LocalContainerBackend,
    _format_container_command_for_log,
    _format_container_mount,
    _redact_container_command_for_log,
    _resolve_docker_bind_host,
)


def test_format_container_mount_uses_mount_syntax_for_docker_windows_paths():
    args = _format_container_mount("docker", "D:/deer-flow/backend/.deer-flow/threads", "/mnt/threads", False)

    assert args == [
        "--mount",
        "type=bind,src=D:/deer-flow/backend/.deer-flow/threads,dst=/mnt/threads",
    ]


def test_format_container_mount_marks_docker_readonly_mounts():
    args = _format_container_mount("docker", "/host/path", "/mnt/path", True)

    assert args == [
        "--mount",
        "type=bind,src=/host/path,dst=/mnt/path,readonly",
    ]


def test_format_container_mount_keeps_volume_syntax_for_apple_container():
    args = _format_container_mount("container", "/host/path", "/mnt/path", True)

    assert args == [
        "-v",
        "/host/path:/mnt/path:ro",
    ]


def test_redact_container_command_for_log_redacts_env_values():
    redacted = _redact_container_command_for_log(
        [
            "docker",
            "run",
            "-e",
            "API_KEY=secret-value",
            "--env=TOKEN=token-value",
            "--name",
            "sandbox",
            "image",
        ]
    )

    assert "API_KEY=<redacted>" in redacted
    assert "--env=TOKEN=<redacted>" in redacted
    assert "secret-value" not in " ".join(redacted)
    assert "token-value" not in " ".join(redacted)


def test_redact_container_command_for_log_keeps_inherited_env_names():
    redacted = _redact_container_command_for_log(
        [
            "docker",
            "run",
            "-e",
            "API_KEY",
            "--env=TOKEN",
            "--name",
            "sandbox",
            "image",
        ]
    )

    assert redacted == [
        "docker",
        "run",
        "-e",
        "API_KEY",
        "--env=TOKEN",
        "--name",
        "sandbox",
        "image",
    ]


def test_format_container_command_for_log_uses_windows_quoting(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    command = _format_container_command_for_log(["docker", "run", "--name", "sandbox one", "image"])

    assert command == 'docker run --name "sandbox one" image'


def test_start_container_logs_redacted_env_values(monkeypatch, caplog):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={"API_KEY": "secret-value", "NORMAL": "visible-value"},
    )
    monkeypatch.setattr(backend, "_runtime", "docker")

    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    with caplog.at_level(logging.INFO, logger="deerflow.community.aio_sandbox.local_backend"):
        backend._start_container("sandbox-test", 18080)

    joined_cmd = " ".join(captured_cmd)
    assert "API_KEY=secret-value" in joined_cmd
    assert "NORMAL=visible-value" in joined_cmd

    log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert "API_KEY=<redacted>" in log_output
    assert "NORMAL=<redacted>" in log_output
    assert "secret-value" not in log_output
    assert "visible-value" not in log_output


def _capture_start_container_command(monkeypatch, backend: LocalContainerBackend, runtime: str = "docker") -> list[str]:
    monkeypatch.setattr(backend, "_runtime", runtime)
    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    backend._start_container("sandbox-test", 18080)
    return captured_cmd


def test_resolve_docker_bind_host_defaults_loopback_for_localhost(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)

    assert _resolve_docker_bind_host() == "127.0.0.1"


def test_resolve_docker_bind_host_keeps_dood_compatibility(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")

    assert _resolve_docker_bind_host() == "0.0.0.0"


def test_resolve_docker_bind_host_uses_ipv6_loopback_for_ipv6_sandbox_host(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "[::1]")

    assert _resolve_docker_bind_host() == "[::1]"


def test_resolve_docker_bind_host_logs_selected_bind_reason(caplog):
    with caplog.at_level(logging.DEBUG, logger="deerflow.community.aio_sandbox.local_backend"):
        assert _resolve_docker_bind_host(sandbox_host="localhost", bind_host="") == "127.0.0.1"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Docker sandbox bind: 127.0.0.1 (loopback default)" in messages


def test_resolve_docker_bind_host_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "localhost")
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "192.0.2.10")

    assert _resolve_docker_bind_host() == "192.0.2.10"


def test_start_container_binds_local_docker_port_to_loopback_by_default(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "127.0.0.1:18080:8080"


def test_start_container_keeps_broad_bind_for_dood_sandbox_host(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "0.0.0.0:18080:8080"


def test_start_container_binds_ipv6_sandbox_host_to_ipv6_loopback(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "[::1]")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "[::1]:18080:8080"


def test_start_container_disables_core_dumps_for_docker(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    ulimit_idx = captured_cmd.index("--ulimit")
    assert captured_cmd[ulimit_idx + 1] == "core=0"


def test_start_container_skips_core_ulimit_for_apple_container(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )

    captured_cmd = _capture_start_container_command(monkeypatch, backend, runtime="container")

    assert "--ulimit" not in captured_cmd


def test_start_container_keeps_apple_container_port_format(monkeypatch):

    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "127.0.0.1")

    captured_cmd = _capture_start_container_command(monkeypatch, backend, runtime="container")

    assert captured_cmd[captured_cmd.index("-p") + 1] == "18080:8080"


def _make_backend(prefix: str = "deer-flow-sandbox") -> LocalContainerBackend:
    return LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix=prefix,
        config_mounts=[],
        environment={},
    )


def _record_subprocess(monkeypatch, responses: dict[str, SimpleNamespace] | None = None) -> list[list[str]]:
    """Capture every subprocess command, replying per docker subcommand."""
    calls: list[list[str]] = []
    responses = responses or {}

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return responses.get(cmd[1], SimpleNamespace(stdout="", stderr="", returncode=0))

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


def test_stop_container_force_removes_after_stop(monkeypatch):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "docker")
    calls = _record_subprocess(monkeypatch)

    backend._stop_container("deer-flow-sandbox-abc123")

    assert calls == [
        ["docker", "stop", "deer-flow-sandbox-abc123"],
        ["docker", "rm", "-f", "-v", "deer-flow-sandbox-abc123"],
    ]


def test_stop_container_force_removes_even_when_stop_fails(monkeypatch):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "docker")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1] == "stop":
            raise subprocess.CalledProcessError(1, cmd, stderr="daemon busy")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    backend._stop_container("deer-flow-sandbox-abc123")

    assert ["docker", "rm", "-f", "-v", "deer-flow-sandbox-abc123"] in calls


def test_force_remove_container_tolerates_already_reclaimed(monkeypatch, caplog):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "docker")
    _record_subprocess(
        monkeypatch,
        {"rm": SimpleNamespace(stdout="", stderr="Error: No such container: x", returncode=1)},
    )

    with caplog.at_level(logging.WARNING, logger="deerflow.community.aio_sandbox.local_backend"):
        backend._force_remove_container("deer-flow-sandbox-abc123")

    assert caplog.records == []


def test_force_remove_container_is_docker_only(monkeypatch):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "container")
    calls = _record_subprocess(monkeypatch)

    backend._force_remove_container("deer-flow-sandbox-abc123")

    assert calls == []


def test_prune_dead_containers_removes_exited_and_dead(monkeypatch):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "docker")
    calls = _record_subprocess(
        monkeypatch,
        {"ps": SimpleNamespace(stdout="deer-flow-sandbox-aaa\ndeer-flow-sandbox-bbb\n", stderr="", returncode=0)},
    )

    assert backend.prune_dead_containers() == 2

    ps_cmd = calls[0]
    assert "-a" in ps_cmd
    assert "status=exited" in ps_cmd
    assert "status=dead" in ps_cmd
    assert "status=created" not in ps_cmd
    assert calls[1:] == [
        ["docker", "rm", "-f", "-v", "deer-flow-sandbox-aaa"],
        ["docker", "rm", "-f", "-v", "deer-flow-sandbox-bbb"],
    ]


def test_prune_dead_containers_ignores_substring_prefix_matches(monkeypatch):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "docker")
    calls = _record_subprocess(
        monkeypatch,
        {"ps": SimpleNamespace(stdout="other-deer-flow-sandbox-aaa\ndeer-flow-sandbox-bbb\n", stderr="", returncode=0)},
    )

    assert backend.prune_dead_containers() == 1
    assert calls[1:] == [["docker", "rm", "-f", "-v", "deer-flow-sandbox-bbb"]]


def test_prune_dead_containers_is_docker_only(monkeypatch):
    backend = _make_backend()
    monkeypatch.setattr(backend, "_runtime", "container")
    calls = _record_subprocess(monkeypatch)

    assert backend.prune_dead_containers() == 0
    assert calls == []


def test_create_uses_localhost_sandbox_url_on_bare_metal(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.setattr(sandbox_backend, "_is_running_in_container", lambda: False)
    monkeypatch.setattr(backend, "_start_container", lambda *_args, **_kwargs: "container-id")
    monkeypatch.setattr("deerflow.community.aio_sandbox.local_backend.get_free_port", lambda start_port=8080: 18080)

    info = backend.create(thread_id="thread-1", sandbox_id="abc123")

    assert info.sandbox_url == "http://localhost:18080"
