from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.community.aio_sandbox import backend as readiness


class _FakeAsyncClient:
    def __init__(self, *, responses: list[object], calls: list[str], timeout: float, request_timeouts: list[float] | None = None) -> None:
        self._responses = responses
        self._calls = calls
        self._timeout = timeout
        self._request_timeouts = request_timeouts

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, *, timeout: float):
        self._calls.append(url)
        if self._request_timeouts is not None:
            self._request_timeouts.append(timeout)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _FakeLoop:
    def __init__(self, times: list[float]) -> None:
        self._times = times
        self._index = 0

    def time(self) -> float:
        value = self._times[self._index]
        self._index += 1
        return value


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_uses_nonblocking_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[SimpleNamespace(status_code=503), SimpleNamespace(status_code=200)],
            calls=calls,
            timeout=timeout,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(readiness.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(readiness.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requests.get should not be used")))
    monkeypatch.setattr(readiness.time, "sleep", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("time.sleep should not be used")))

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=5, poll_interval=0.05) is True

    assert calls == ["http://sandbox/v1/sandbox", "http://sandbox/v1/sandbox"]
    assert sleeps == [0.05]


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_retries_request_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[readiness.httpx.ConnectError("not ready"), SimpleNamespace(status_code=200)],
            calls=calls,
            timeout=timeout,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(readiness.asyncio, "sleep", fake_sleep)

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=5, poll_interval=0.01) is True

    assert len(calls) == 2
    assert sleeps == [0.01]


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_clamps_request_and_sleep_to_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    request_timeouts: list[float] = []
    sleeps: list[float] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[SimpleNamespace(status_code=503)],
            calls=calls,
            timeout=timeout,
            request_timeouts=request_timeouts,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(readiness.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(readiness.asyncio, "get_running_loop", lambda: _FakeLoop([100.0, 100.5, 101.75, 102.0]))

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=2, poll_interval=1.0) is False

    assert calls == ["http://sandbox/v1/sandbox"]
    assert request_timeouts == [1.5]
    assert sleeps == [0.25]


def test_get_sandbox_access_host_uses_localhost_on_bare_metal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.setattr(readiness, "_is_running_in_container", lambda: False)

    assert readiness._get_sandbox_access_host() == "localhost"


def test_get_sandbox_access_host_keeps_docker_internal_in_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.setattr(readiness, "_is_running_in_container", lambda: True)

    assert readiness._get_sandbox_access_host() == "host.docker.internal"


def test_normalize_sandbox_access_url_rewrites_docker_internal_on_bare_metal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_is_running_in_container", lambda: False)

    assert readiness._normalize_sandbox_access_url("http://host.docker.internal:8080") == "http://localhost:8080"


def test_wait_for_sandbox_ready_falls_back_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float):
        calls.append(url)
        if url.startswith("http://localhost:"):
            return SimpleNamespace(status_code=200)
        raise readiness.requests.exceptions.ConnectionError("unresolved host")

    monkeypatch.setattr(readiness, "_is_running_in_container", lambda: False)
    monkeypatch.setattr(readiness.requests, "get", fake_get)
    monkeypatch.setattr(readiness.time, "sleep", lambda *_args, **_kwargs: None)

    assert readiness.wait_for_sandbox_ready("http://host.docker.internal:8080", timeout=1) is True
    assert calls[0] == "http://host.docker.internal:8080/v1/sandbox"
    assert calls[1] == "http://localhost:8080/v1/sandbox"
