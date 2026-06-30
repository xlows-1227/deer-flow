"""Abstract base class for sandbox provisioning backends."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
import requests

from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


def _is_running_in_container() -> bool:
    """Best-effort detection of whether the current process runs inside a container."""
    if os.environ.get("DEER_FLOW_RUNNING_IN_CONTAINER", "").lower() in {"1", "true", "yes"}:
        return True
    return Path("/.dockerenv").exists()


def _get_sandbox_access_host() -> str:
    """Resolve the host used in sandbox_url for health checks and API calls.

    ``host.docker.internal`` is meant for gateway containers reaching sandboxes on
    the host Docker daemon. When the gateway itself runs on bare metal, fall back
    to localhost so readiness checks do not time out on an unresolvable hostname.
    """
    host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost").strip() or "localhost"
    if host.lower() == "host.docker.internal" and not _is_running_in_container():
        logger.debug("DEER_FLOW_SANDBOX_HOST=host.docker.internal ignored on bare-metal gateway; using localhost")
        return "localhost"
    return host


def _normalize_sandbox_access_url(sandbox_url: str) -> str:
    """Rewrite host.docker.internal to localhost for bare-metal gateway callers."""
    parsed = urlparse(sandbox_url)
    host = (parsed.hostname or "").lower()
    if host == "host.docker.internal" and not _is_running_in_container() and parsed.port is not None:
        return urlunparse(parsed._replace(netloc=f"localhost:{parsed.port}"))
    return sandbox_url


def _sandbox_ready_urls(sandbox_url: str) -> list[str]:
    """Return candidate URLs for sandbox readiness checks, with localhost fallback."""
    urls = [sandbox_url]
    parsed = urlparse(sandbox_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if host == "host.docker.internal" and not _is_running_in_container() and port is not None:
        fallback = urlunparse(parsed._replace(netloc=f"localhost:{port}"))
        if fallback not in urls:
            urls.append(fallback)
    return urls


def wait_for_sandbox_ready(sandbox_url: str, timeout: int = 30) -> bool:
    """Poll sandbox health endpoint until ready or timeout.

    Args:
        sandbox_url: URL of the sandbox (e.g. http://k3s:30001).
        timeout: Maximum time to wait in seconds.

    Returns:
        True if sandbox is ready, False otherwise.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        for candidate_url in _sandbox_ready_urls(sandbox_url):
            try:
                response = requests.get(f"{candidate_url}/v1/sandbox", timeout=5)
                if response.status_code == 200:
                    return True
            except requests.exceptions.RequestException:
                pass
        time.sleep(1)
    return False


async def wait_for_sandbox_ready_async(sandbox_url: str, timeout: int = 30, poll_interval: float = 1.0) -> bool:
    """Async variant of sandbox readiness polling.

    Use this from async runtime paths so sandbox startup waits do not block the
    event loop. The synchronous ``wait_for_sandbox_ready`` function remains for
    existing synchronous backend/provider call sites.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            for candidate_url in _sandbox_ready_urls(sandbox_url):
                try:
                    response = await client.get(f"{candidate_url}/v1/sandbox", timeout=min(5.0, remaining))
                    if response.status_code == 200:
                        return True
                except httpx.RequestError:
                    pass
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))
    return False


class SandboxBackend(ABC):
    """Abstract base for sandbox provisioning backends.

    Two implementations:
    - LocalContainerBackend: starts Docker/Apple Container locally, manages ports
    - RemoteSandboxBackend: connects to a pre-existing URL (K8s service, external)
    """

    @abstractmethod
    def create(self, thread_id: str | None, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """Create/provision a new sandbox.

        Args:
            thread_id: Thread ID for which the sandbox is being created. Useful for backends that want to organize sandboxes by thread.
            sandbox_id: Deterministic sandbox identifier.
            extra_mounts: Additional volume mounts as (host_path, container_path, read_only) tuples.
                Ignored by backends that don't manage containers (e.g., remote).

        Returns:
            SandboxInfo with connection details.
        """
        ...

    @abstractmethod
    def destroy(self, info: SandboxInfo) -> None:
        """Destroy/cleanup a sandbox and release its resources.

        Args:
            info: The sandbox metadata to destroy.
        """
        ...

    @abstractmethod
    def is_alive(self, info: SandboxInfo) -> bool:
        """Quick check whether a sandbox is still alive.

        This should be a lightweight check (e.g., container inspect)
        rather than a full health check.

        Args:
            info: The sandbox metadata to check.

        Returns:
            True if the sandbox appears to be alive.
        """
        ...

    @abstractmethod
    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """Try to discover an existing sandbox by its deterministic ID.

        Used for cross-process recovery: when another process started a sandbox,
        this process can discover it by the deterministic container name or URL.

        Args:
            sandbox_id: The deterministic sandbox ID to look for.

        Returns:
            SandboxInfo if found and healthy, None otherwise.
        """
        ...

    def list_running(self) -> list[SandboxInfo]:
        """Enumerate all running sandboxes managed by this backend.

        Used for startup reconciliation: when the process restarts, it needs
        to discover containers started by previous processes so they can be
        adopted into the warm pool or destroyed if idle too long.

        The default implementation returns an empty list, which is correct
        for backends that don't manage local containers (e.g., RemoteSandboxBackend
        delegates lifecycle to the provisioner which handles its own cleanup).

        Returns:
            A list of SandboxInfo for all currently running sandboxes.
        """
        return []
