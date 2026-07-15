"""Feishu/Lark channel — connects to Feishu via WebSocket (no public IP needed)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import json
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx

from app.channels.base import Channel
from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.contracts import EventDeduplicator as FeishuEventDeduplicator
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.persistence.channel_mapping import SYSTEM_CHANNEL_MAPPING_SCOPE
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import get_sandbox_provider

logger = logging.getLogger(__name__)

FEISHU_INBOUND_FILE_MAX_BYTES = 50 * 1024 * 1024
FEISHU_PUBLISHED_INBOUND_MAX_FILES = 10
FEISHU_WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 15.0
FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS = 5.0
FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS = 10.0
FEISHU_PUBLISHED_DOWNLOAD_TIMEOUT_SECONDS = 60.0
FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS = 2.0


def _read_inbound_resource(stream: Any) -> bytes:
    """Read one provider resource without buffering beyond the input limit."""
    content = stream.read(FEISHU_INBOUND_FILE_MAX_BYTES + 1)
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("Feishu inbound resource did not contain bytes")
    if len(content) > FEISHU_INBOUND_FILE_MAX_BYTES:
        raise ValueError("Feishu inbound resource exceeds size limit")
    return bytes(content)


@dataclass(frozen=True)
class _MaterializedInboundFile:
    virtual_path: str
    actual_path: Path
    size: int


class FeishuEventVerifier:
    """Validate the authenticated SDK event header and replay window.

    Feishu long-connection mode authenticates the WebSocket with app
    credentials, but lark-oapi deliberately dispatches WebSocket frames through
    ``do_without_validation``. Dynamic bindings therefore compare the event
    header token here and additionally require a stable ID and fresh timestamp
    before durable deduplication. HTTP callback signatures are not present on
    this transport; ``encrypt_key`` remains part of the encrypted credential
    bundle and dispatcher construction for provider configuration parity.
    """

    def __init__(
        self,
        *,
        verification_token: str = "",
        max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._verification_token = verification_token
        self._max_age_seconds = max_age_seconds
        self._clock = clock

    @staticmethod
    def _timestamp(event: Any) -> float | None:
        header = getattr(event, "header", None)
        raw_timestamp = getattr(header, "create_time", None)
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError):
            return None
        if timestamp >= 1_000_000_000_000:
            timestamp /= 1000
        return timestamp

    def __call__(self, event: Any) -> bool:
        header = getattr(event, "header", None)
        event_id = getattr(header, "event_id", None)
        if not isinstance(event_id, str) or not event_id.strip():
            return False

        timestamp = self._timestamp(event)
        if timestamp is None or abs(self._clock() - timestamp) > self._max_age_seconds:
            return False

        if self._verification_token:
            token = getattr(header, "token", None)
            if not isinstance(token, str) or not hmac.compare_digest(token, self._verification_token):
                return False
        return True


class FeishuWebSocketSession(Protocol):
    """Blocking SDK connection owned by one Feishu channel worker thread."""

    def run(
        self,
        *,
        on_ready: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None: ...

    def stop(self, *, timeout_seconds: float) -> bool: ...


class WebSocketSessionFactory(Protocol):
    """Construct a terminable SDK session for one isolated binding."""

    def __call__(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
        message_handler: Callable[[Any], None],
        encrypt_key: str,
        verification_token: str,
    ) -> FeishuWebSocketSession: ...


RuntimeErrorCallback = Callable[[str], Awaitable[None]]
LarkEndpointResolver = Callable[[object], Awaitable[str]]


async def _resolve_lark_endpoint(client: object) -> str:
    """Fetch one SDK endpoint asynchronously with bounded network timeouts."""
    from lark_oapi.ws.const import GEN_ENDPOINT_URI, OK

    app_id = getattr(client, "_app_id", "")
    app_secret = getattr(client, "_app_secret", "")
    domain = getattr(client, "_domain", "")
    if not app_id or not app_secret:
        raise RuntimeError("Feishu app_id or app_secret is empty")

    timeout = httpx.Timeout(
        FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS,
        connect=FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        response = await http_client.post(
            f"{domain}{GEN_ENDPOINT_URI}",
            headers={"locale": "zh"},
            json={"AppID": app_id, "AppSecret": app_secret},
        )
    if response.status_code != 200:
        raise RuntimeError(f"Feishu endpoint request failed with HTTP {response.status_code}")
    payload = response.json()
    code = payload.get("code")
    if code != OK:
        raise RuntimeError(f"Feishu endpoint request failed: code={code}, msg={payload.get('msg', '')}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("URL"), str) or not data["URL"]:
        raise RuntimeError("Feishu endpoint response did not contain a WebSocket URL")

    client_config = data.get("ClientConfig")
    configure = getattr(client, "_configure", None)
    if isinstance(client_config, dict) and callable(configure):
        from lark_oapi.ws.model import ClientConfig

        configure(ClientConfig(client_config))
    return data["URL"]


class _LarkSdkRuntime:
    """Own the single event loop referenced by lark-oapi's module global."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="feishu-lark-sdk-loop",
            daemon=True,
        )
        self._thread.start()
        if not self._started.wait(5.0):
            raise RuntimeError("Feishu SDK event loop failed to start")

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        if loop is None:
            raise RuntimeError("Feishu SDK event loop is unavailable")
        return loop

    def _run(self) -> None:
        import lark_oapi.ws.client as ws_client_module

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # lark-oapi 1.x schedules receive work through this module global.
        # Assign it exactly once to a process-owned loop shared by all clients.
        ws_client_module.loop = loop
        self._started.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._stopped.set()

    def submit(self, coroutine: Awaitable[Any]) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def shutdown(self, *, timeout_seconds: float = 5.0) -> bool:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout_seconds)
        return self._stopped.is_set()


_lark_sdk_runtime: _LarkSdkRuntime | None = None
_lark_sdk_runtime_lock = threading.Lock()


def _get_lark_sdk_runtime() -> _LarkSdkRuntime:
    global _lark_sdk_runtime
    with _lark_sdk_runtime_lock:
        if _lark_sdk_runtime is None:
            _lark_sdk_runtime = _LarkSdkRuntime()
        return _lark_sdk_runtime


def shutdown_lark_sdk_runtime(*, timeout_seconds: float = 5.0) -> bool:
    """Stop the process-owned lark-oapi loop after all bindings have stopped."""
    global _lark_sdk_runtime
    with _lark_sdk_runtime_lock:
        runtime = _lark_sdk_runtime
        if runtime is None:
            return True
        stopped = runtime.shutdown(timeout_seconds=timeout_seconds)
        if stopped:
            _lark_sdk_runtime = None
        return stopped


class _LarkWebSocketSession:
    """One binding connection scheduled on the process-owned SDK loop."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
        event_handler: object,
        endpoint_resolver: LarkEndpointResolver = _resolve_lark_endpoint,
        connect_timeout_seconds: float = FEISHU_WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._event_handler = event_handler
        self._endpoint_resolver = endpoint_resolver
        self._connect_timeout_seconds = connect_timeout_seconds
        self._runtime: _LarkSdkRuntime | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: object | None = None
        self._run_future: concurrent.futures.Future[Any] | None = None
        self._stop_waiter: asyncio.Future[None] | None = None
        self._owned_tasks: set[asyncio.Task[Any]] = set()
        self._stopping = threading.Event()
        self._exited = threading.Event()

    @staticmethod
    def _belongs_to_client(task: asyncio.Task[Any], client: object) -> bool:
        coroutine = task.get_coro()
        frame = getattr(coroutine, "cr_frame", None)
        return frame is not None and frame.f_locals.get("self") is client

    async def _run_client(
        self,
        *,
        on_ready: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        import lark_oapi as lark

        loop = asyncio.get_running_loop()
        self._loop = loop
        client = lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=self._event_handler,
            log_level=lark.LogLevel.INFO,
            domain=self._domain,
            auto_reconnect=False,
        )
        self._client = client

        def handle_sdk_task_done(task: asyncio.Task[Any]) -> None:
            if self._stopping.is_set() or task.cancelled():
                return
            try:
                task.exception()
            except asyncio.CancelledError:
                return
            on_error("connection lost")
            waiter = self._stop_waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

        try:
            async with asyncio.timeout(self._connect_timeout_seconds):
                conn_url = await self._endpoint_resolver(client)
                # lark-oapi 1.5.x performs a synchronous, unbounded
                # ``requests.post`` inside ``_connect``. Resolve it above with
                # async bounded I/O, then let the SDK consume the cached URL.
                client._get_conn_url = lambda: conn_url
                await client._connect()
            self._owned_tasks.update(task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task() and self._belongs_to_client(task, client))
            if client._conn is None:
                raise RuntimeError("Feishu WebSocket connection was not established")
            ping_task = loop.create_task(client._ping_loop())
            self._owned_tasks.add(ping_task)
            for task in self._owned_tasks:
                task.add_done_callback(handle_sdk_task_done)
            if self._stopping.is_set():
                return
            self._stop_waiter = loop.create_future()
            on_ready()
            await self._stop_waiter
        finally:
            self._owned_tasks.update(task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task() and self._belongs_to_client(task, client))
            try:
                if client._conn is not None:
                    await client._disconnect()
            except Exception:
                logger.warning("Feishu WebSocket disconnect failed", exc_info=True)
            owned_tasks = [task for task in self._owned_tasks if not task.done()]
            for task in owned_tasks:
                task.cancel()
            if owned_tasks:
                await asyncio.gather(*owned_tasks, return_exceptions=True)
            self._owned_tasks.clear()
            self._stop_waiter = None
            self._client = None

    def run(
        self,
        *,
        on_ready: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._runtime = _get_lark_sdk_runtime()
        try:
            future = self._runtime.submit(self._run_client(on_ready=on_ready, on_error=on_error))
            self._run_future = future
            future.result()
        except concurrent.futures.CancelledError:
            pass
        except Exception:
            if not self._stopping.is_set():
                on_error("connection failed")
        finally:
            self._run_future = None
            self._exited.set()

    def stop(self, *, timeout_seconds: float) -> bool:
        """Close the SDK connection and confirm its worker loop has exited."""
        self._stopping.set()
        runtime = self._runtime
        if runtime is not None:

            async def request_stop() -> bool:
                waiter = self._stop_waiter
                if waiter is not None and not waiter.done():
                    waiter.set_result(None)
                    return True
                return False

            try:
                future = runtime.submit(request_stop())
                signaled = future.result(timeout=timeout_seconds)
                if not signaled:
                    run_future = self._run_future
                    if run_future is not None:
                        run_future.cancel()
            except Exception:
                run_future = self._run_future
                if run_future is not None:
                    run_future.cancel()
        return self._exited.wait(timeout_seconds)


def _default_websocket_session_factory(
    *,
    app_id: str,
    app_secret: str,
    domain: str,
    message_handler: Callable[[Any], None],
    encrypt_key: str,
    verification_token: str,
) -> FeishuWebSocketSession:
    import lark_oapi as lark

    event_handler = lark.EventDispatcherHandler.builder(encrypt_key, verification_token).register_p2_im_message_receive_v1(message_handler).build()
    return _LarkWebSocketSession(
        app_id=app_id,
        app_secret=app_secret,
        domain=domain,
        event_handler=event_handler,
    )


def _is_feishu_command(text: str) -> bool:
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in KNOWN_CHANNEL_COMMANDS


class FeishuChannel(Channel):
    """Feishu/Lark IM channel using the ``lark-oapi`` WebSocket client.

    Configuration keys (in ``config.yaml`` under ``channels.feishu``):
        - ``app_id``: Feishu app ID.
        - ``app_secret``: Feishu app secret.
        - ``verification_token``: (optional) Event verification token.

    The channel uses WebSocket long-connection mode so no public IP is required.

    Message flow:
        1. User sends a message → bot adds "OK" emoji reaction
        2. Bot replies in thread: "Working on it......"
        3. Agent processes the message and returns a result
        4. Bot replies in thread with the result
        5. Bot adds "DONE" emoji reaction to the original message
    """

    def __init__(
        self,
        bus: MessageBus,
        config: dict[str, Any] | None = None,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        binding_id: str | None = None,
        agent_id: str | None = None,
        event_deduplicator: FeishuEventDeduplicator | None = None,
        event_verifier: Callable[[Any], bool] | None = None,
        websocket_session_factory: WebSocketSessionFactory | None = None,
        startup_timeout_seconds: float = 15.0,
        runtime_error_callback: RuntimeErrorCallback | None = None,
        published_http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        resolved_config = dict(config or {})
        if app_id is not None:
            resolved_config["app_id"] = app_id
        if app_secret is not None:
            resolved_config["app_secret"] = app_secret
        if verification_token is not None:
            resolved_config["verification_token"] = verification_token
        if encrypt_key is not None:
            resolved_config["encrypt_key"] = encrypt_key
        channel_name = f"feishu:{binding_id}" if binding_id else "feishu"
        super().__init__(name=channel_name, bus=bus, config=resolved_config)
        self.binding_id = binding_id
        self.agent_id = agent_id
        self._event_deduplicator = event_deduplicator
        self._event_verifier = event_verifier or (FeishuEventVerifier(verification_token=str(resolved_config.get("verification_token", ""))) if binding_id else None)
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        self._websocket_session_factory = websocket_session_factory or _default_websocket_session_factory
        self._startup_timeout_seconds = startup_timeout_seconds
        self._runtime_error_callback = runtime_error_callback
        self._published_http_client_factory = published_http_client_factory or self._new_published_http_client
        self._ws_session: FeishuWebSocketSession | None = None
        self._startup_event = threading.Event()
        self._startup_error: RuntimeError | None = None
        self._startup_acknowledged = False
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._api_client = None
        self._CreateMessageReactionRequest = None
        self._CreateMessageReactionRequestBody = None
        self._Emoji = None
        self._PatchMessageRequest = None
        self._PatchMessageRequestBody = None
        self._background_tasks: set[asyncio.Task] = set()
        self._running_card_ids: dict[str, str] = {}
        self._running_card_tasks: dict[str, asyncio.Task] = {}
        self._CreateFileRequest = None
        self._CreateFileRequestBody = None
        self._CreateImageRequest = None
        self._CreateImageRequestBody = None
        self._GetMessageResourceRequest = None
        self._thread_lock = threading.Lock()

    def _new_published_http_client(self) -> httpx.AsyncClient:
        """Build the bounded client used for authenticated resource streaming."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS,
                read=FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS,
                write=FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS,
                pool=FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS,
            )
        )

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def websocket_thread_alive(self) -> bool:
        """Return whether this binding still owns a live SDK worker thread."""
        return self._thread is not None and self._thread.is_alive()

    async def start(self) -> None:
        if self._running:
            return

        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest,
                CreateFileRequestBody,
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
                Emoji,
                GetMessageResourceRequest,
                PatchMessageRequest,
                PatchMessageRequestBody,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )
        except ImportError:
            logger.error("lark-oapi is not installed. Install it with: uv add lark-oapi")
            return

        self._lark = lark
        self._CreateMessageRequest = CreateMessageRequest
        self._CreateMessageRequestBody = CreateMessageRequestBody
        self._ReplyMessageRequest = ReplyMessageRequest
        self._ReplyMessageRequestBody = ReplyMessageRequestBody
        self._CreateMessageReactionRequest = CreateMessageReactionRequest
        self._CreateMessageReactionRequestBody = CreateMessageReactionRequestBody
        self._Emoji = Emoji
        self._PatchMessageRequest = PatchMessageRequest
        self._PatchMessageRequestBody = PatchMessageRequestBody
        self._CreateFileRequest = CreateFileRequest
        self._CreateFileRequestBody = CreateFileRequestBody
        self._CreateImageRequest = CreateImageRequest
        self._CreateImageRequestBody = CreateImageRequestBody
        self._GetMessageResourceRequest = GetMessageResourceRequest

        app_id = self.config.get("app_id", "")
        app_secret = self.config.get("app_secret", "")
        domain = self.config.get("domain", "https://open.feishu.cn")

        if not app_id or not app_secret:
            raise RuntimeError("Feishu channel requires app_id and app_secret")
        if self.binding_id and not str(self.config.get("verification_token", "")).strip():
            raise RuntimeError("Dynamic Feishu binding requires a verification token")
        if self.binding_id and self._event_deduplicator is None:
            raise RuntimeError("Dynamic Feishu binding requires durable event deduplication")

        self._api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain).build()
        logger.info("[Feishu] using domain: %s", domain)
        self._main_loop = asyncio.get_event_loop()
        self._startup_event.clear()
        self._startup_error = None
        self._startup_acknowledged = False
        self._stop_requested = False

        # Both ws.Client construction and start() must happen in a dedicated
        # thread with its own event loop.  lark-oapi caches the running loop
        # at construction time and later calls loop.run_until_complete(),
        # which conflicts with an already-running uvloop.
        self._thread = threading.Thread(
            target=self._run_ws,
            args=(app_id, app_secret, domain),
            daemon=True,
        )
        self._thread.start()
        signalled = await asyncio.to_thread(self._startup_event.wait, self._startup_timeout_seconds)
        if not signalled or self._startup_error is not None or not self._running:
            await self.stop()
            raise self._startup_error or RuntimeError("Feishu WebSocket failed to connect")
        self._startup_acknowledged = True
        self.bus.subscribe_outbound(self._on_outbound)
        logger.info("Feishu channel started")

    def _run_ws(self, app_id: str, app_secret: str, domain: str) -> None:
        """Construct and run the lark WS client in a thread with a fresh event loop.

        The lark-oapi SDK captures a module-level event loop at import time
        (``lark_oapi.ws.client.loop``).  When uvicorn uses uvloop, that
        captured loop is the *main* thread's uvloop — which is already
        running, so ``loop.run_until_complete()`` inside ``Client.start()``
        raises ``RuntimeError``.

        We work around this by creating a plain asyncio event loop for this
        thread and patching the SDK's module-level reference before calling
        ``start()``.
        """
        try:
            session = self._websocket_session_factory(
                app_id=app_id,
                app_secret=app_secret,
                domain=domain,
                message_handler=self._on_message,
                encrypt_key=str(self.config.get("encrypt_key", "")),
                verification_token=str(self.config.get("verification_token", "")),
            )
            self._ws_session = session
            session.run(on_ready=self._on_ws_ready, on_error=self._on_ws_error)
            if not self._stop_requested and self._running:
                self._on_ws_error("connection lost")
        except Exception:
            if not self._stop_requested:
                logger.exception("Feishu WebSocket error")
                self._on_ws_error("connection failed")

    def _on_ws_ready(self) -> None:
        self._running = True
        self._startup_event.set()

    def _on_ws_error(self, _detail: str) -> None:
        if self._stop_requested:
            return
        was_ready = self._running and self._startup_acknowledged
        self._running = False
        if not self._startup_acknowledged:
            self._startup_error = RuntimeError("Feishu WebSocket failed to connect")
        self._startup_event.set()
        if was_ready and self._runtime_error_callback is not None and self._main_loop is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._runtime_error_callback("Feishu WebSocket connection lost"),
                self._main_loop,
            )
            future.add_done_callback(lambda done: self._log_future_error(done, "runtime_error_callback", "runtime"))

    async def stop(self) -> None:
        self._stop_requested = True
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        for task in list(self._running_card_tasks.values()):
            task.cancel()
        self._running_card_tasks.clear()
        session = self._ws_session
        if session is not None:
            stopped = await asyncio.to_thread(session.stop, timeout_seconds=5.0)
            if not stopped:
                raise RuntimeError("Feishu WebSocket client did not stop")
        thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join, 5.0)
            if thread.is_alive():
                raise RuntimeError("Feishu WebSocket worker thread did not exit")
        self._thread = None
        self._ws_session = None
        logger.info("Feishu channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        if not self._api_client:
            logger.warning("[Feishu] send called but no api_client available")
            return

        logger.info(
            "[Feishu] sending reply: chat_id=%s, thread_ts=%s, text_len=%d",
            msg.chat_id,
            msg.thread_ts,
            len(msg.text),
        )

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                await self._send_card_message(msg)
                return  # success
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1s, 2s
                    logger.warning(
                        "[Feishu] send failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("[Feishu] send failed after %d attempts: %s", _max_retries, last_exc)
        if last_exc is None:
            raise RuntimeError("Feishu send failed without an exception from any attempt")
        raise last_exc

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        if not self._api_client:
            return False

        # Check size limits (image: 10MB, file: 30MB)
        if attachment.is_image and attachment.size > 10 * 1024 * 1024:
            logger.warning("[Feishu] image too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False
        if not attachment.is_image and attachment.size > 30 * 1024 * 1024:
            logger.warning("[Feishu] file too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False

        try:
            if attachment.is_image:
                file_key = await self._upload_image(attachment.actual_path)
                msg_type = "image"
                content = json.dumps({"image_key": file_key})
            else:
                file_key = await self._upload_file(attachment.actual_path, attachment.filename)
                msg_type = "file"
                content = json.dumps({"file_key": file_key})

            if msg.thread_ts:
                request = self._ReplyMessageRequest.builder().message_id(msg.thread_ts).request_body(self._ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).reply_in_thread(True).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
            else:
                request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(msg.chat_id).msg_type(msg_type).content(content).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.create, request)

            logger.info("[Feishu] file sent: %s (type=%s)", attachment.filename, msg_type)
            return True
        except Exception:
            logger.exception("[Feishu] failed to upload/send file: %s", attachment.filename)
            return False

    async def _upload_image(self, path) -> str:
        """Upload an image to Feishu and return the image_key."""
        with open(str(path), "rb") as f:
            request = self._CreateImageRequest.builder().request_body(self._CreateImageRequestBody.builder().image_type("message").image(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.image.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu image upload failed: code={response.code}, msg={response.msg}")
        return response.data.image_key

    async def _upload_file(self, path, filename: str) -> str:
        """Upload a file to Feishu and return the file_key."""
        suffix = path.suffix.lower() if hasattr(path, "suffix") else ""
        if suffix in (".xls", ".xlsx", ".csv"):
            file_type = "xls"
        elif suffix in (".ppt", ".pptx"):
            file_type = "ppt"
        elif suffix == ".pdf":
            file_type = "pdf"
        elif suffix in (".doc", ".docx"):
            file_type = "doc"
        else:
            file_type = "stream"

        with open(str(path), "rb") as f:
            request = self._CreateFileRequest.builder().request_body(self._CreateFileRequestBody.builder().file_type(file_type).file_name(filename).file(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.file.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu file upload failed: code={response.code}, msg={response.msg}")
        return response.data.file_key

    async def receive_file(self, msg: InboundMessage, thread_id: str) -> InboundMessage:
        """Download a Feishu file into the thread uploads directory.

        Returns the sandbox virtual path when the image is persisted successfully.
        """
        if not msg.thread_ts:
            logger.warning("[Feishu] received file message without thread_ts, cannot associate with conversation: %s", msg)
            return msg
        files = msg.files
        if not files:
            logger.warning("[Feishu] received message with no files: %s", msg)
            return msg
        text = msg.text
        for file in files:
            if file.get("image_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["image_key"], "image", thread_id)
                text = text.replace("[image]", virtual_path, 1)
            elif file.get("file_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["file_key"], "file", thread_id)
                text = text.replace("[file]", virtual_path, 1)
        msg.text = text
        return msg

    async def materialize_published_files(
        self,
        msg: InboundMessage,
        thread_id: str,
        *,
        owner_user_id: str,
        max_input_bytes: int,
    ) -> tuple[InboundMessage, int]:
        """Stream published attachments into one trusted owner scope.

        The caller must resolve ``owner_user_id`` from the published Agent; it
        must never come from Feishu message metadata. Downloads use
        authenticated streaming HTTP, enforce count, per-file, and aggregate
        actual-byte limits, then expose the fully admitted set to the same
        owner-scoped sandbox. Cancellation closes the network response and
        removes partial host files. A blocked non-mounted sandbox upload is
        handed to a tracked cleanup task that removes both host and sandbox
        residues when the worker exits.

        Args:
            msg: Verified inbound message containing Feishu resource keys and
                matching ``[image]`` or ``[file]`` placeholders.
            thread_id: Trusted runtime thread receiving the files.
            owner_user_id: Trusted published-Agent owner used for host paths,
                sandbox acquisition, and cache ownership checks.
            max_input_bytes: Maximum combined UTF-8 text and actual attachment
                bytes admitted for the request.

        Returns:
            The message with placeholders replaced by sandbox virtual paths,
            plus the total actual attachment byte count.

        Raises:
            ValueError: Attachment metadata, count, filename, empty content,
                per-file size, or aggregate input admission is invalid.
            RuntimeError: Authentication, download, or sandbox synchronization
                fails.
            PermissionError: The thread sandbox is bound to another owner.
            asyncio.CancelledError: The caller is cancelled after bounded or
                recoverable cleanup has been arranged.
        """
        if not msg.thread_ts:
            raise ValueError("Feishu attachment message is missing its message ID")
        if len(msg.files) > FEISHU_PUBLISHED_INBOUND_MAX_FILES:
            raise ValueError("Feishu attachment count exceeds the published input limit")

        text = msg.text
        total_bytes = 0
        created_paths: list[Path] = []
        materialized_files: list[_MaterializedInboundFile] = []
        try:
            for file_info in msg.files:
                if not isinstance(file_info, dict):
                    raise ValueError("Feishu attachment metadata is invalid")
                if file_info.get("image_key"):
                    file_key = file_info["image_key"]
                    resource_type: Literal["image", "file"] = "image"
                    placeholder = "[image]"
                elif file_info.get("file_key"):
                    file_key = file_info["file_key"]
                    resource_type = "file"
                    placeholder = "[file]"
                else:
                    raise ValueError("Feishu attachment metadata has no resource key")

                remaining_bytes = max_input_bytes - total_bytes - len(text.encode("utf-8"))
                if remaining_bytes <= 0:
                    raise ValueError("Feishu attachments exceed the published input quota")
                materialized = await self._materialize_published_file(
                    msg.thread_ts,
                    str(file_key),
                    resource_type,
                    thread_id,
                    owner_user_id=owner_user_id,
                    max_bytes=min(FEISHU_INBOUND_FILE_MAX_BYTES, remaining_bytes),
                )
                materialized_files.append(materialized)
                created_paths.append(materialized.actual_path)
                total_bytes += materialized.size
                text = text.replace(placeholder, materialized.virtual_path, 1)
                if total_bytes + len(text.encode("utf-8")) > max_input_bytes:
                    raise ValueError("Feishu attachments exceed the published input quota")
            await self._sync_published_files(
                materialized_files,
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                message_id=msg.thread_ts,
            )
        except BaseException:
            for created_path in created_paths:
                try:
                    created_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[Feishu] failed to clean rejected inbound file: %s", created_path)
            raise

        msg.text = text
        return msg, total_bytes

    async def _materialize_published_file(
        self,
        message_id: str,
        file_key: str,
        resource_type: Literal["image", "file"],
        thread_id: str,
        *,
        owner_user_id: str,
        max_bytes: int,
    ) -> _MaterializedInboundFile:
        """Download one resource with bounded memory and remove partial files."""
        from deerflow.uploads.manager import (
            claim_unique_filename,
            ensure_uploads_dir,
            normalize_filename,
            open_upload_file_no_symlink,
        )

        paths = get_paths()
        paths.ensure_thread_dirs(thread_id, user_id=owner_user_id)
        uploads_dir = ensure_uploads_dir(thread_id, user_id=owner_user_id).resolve()
        extension = "png" if resource_type == "image" else "bin"
        fallback_filename = f"feishu_{file_key[-12:]}.{extension}"
        domain = str(self.config.get("domain", "https://open.feishu.cn")).rstrip("/")
        app_id = str(self.config.get("app_id", ""))
        app_secret = str(self.config.get("app_secret", ""))
        if not app_id or not app_secret:
            raise RuntimeError("Feishu channel requires app_id and app_secret")
        resource_url = f"{domain}/open-apis/im/v1/messages/{quote(message_id, safe='')}/resources/{quote(file_key, safe='')}"
        resolved_target: Path | None = None
        total_bytes = 0
        try:
            async with asyncio.timeout(FEISHU_PUBLISHED_DOWNLOAD_TIMEOUT_SECONDS):
                async with self._published_http_client_factory() as client:
                    token_response = await client.post(
                        f"{domain}/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": app_id, "app_secret": app_secret},
                    )
                    token_response.raise_for_status()
                    token_payload = token_response.json()
                    if token_payload.get("code") not in {0, None}:
                        raise RuntimeError(f"Feishu tenant token request failed: code={token_payload.get('code')}, msg={token_payload.get('msg', '')}")
                    tenant_token = token_payload.get("tenant_access_token")
                    if not isinstance(tenant_token, str) or not tenant_token:
                        raise RuntimeError("Feishu tenant token response is missing a token")

                    async with client.stream(
                        "GET",
                        resource_url,
                        params={"type": resource_type},
                        headers={"Authorization": f"Bearer {tenant_token}"},
                    ) as response:
                        response.raise_for_status()
                        content_length_header = response.headers.get("content-length")
                        if content_length_header is not None:
                            try:
                                content_length = int(content_length_header)
                            except ValueError as exc:
                                raise ValueError("Feishu resource Content-Length is invalid") from exc
                            if content_length < 0 or content_length > max_bytes:
                                raise ValueError("Feishu inbound resource exceeds size limit")

                        disposition = Message()
                        disposition["content-disposition"] = response.headers.get(
                            "content-disposition",
                            "",
                        )
                        raw_filename = disposition.get_filename() or fallback_filename
                        with self._thread_lock:
                            seen_names = {entry.name for entry in uploads_dir.iterdir() if entry.is_file()}
                            safe_name = claim_unique_filename(normalize_filename(raw_filename), seen_names)
                            resolved_target, file_handle = open_upload_file_no_symlink(
                                uploads_dir,
                                safe_name,
                            )
                        with file_handle:
                            async for chunk in response.aiter_raw():
                                total_bytes += len(chunk)
                                if total_bytes > max_bytes:
                                    raise ValueError("Feishu inbound resource exceeds size limit")
                                file_handle.write(chunk)
            if total_bytes == 0:
                raise ValueError("Feishu inbound resource is empty")
        except BaseException:
            if resolved_target is not None:
                resolved_target.unlink(missing_ok=True)
            raise

        assert resolved_target is not None
        return _MaterializedInboundFile(
            virtual_path=f"{VIRTUAL_PATH_PREFIX}/uploads/{resolved_target.name}",
            actual_path=resolved_target,
            size=total_bytes,
        )

    @staticmethod
    async def _delete_published_sandbox_files(sandbox: Any, files: list[_MaterializedInboundFile]) -> None:
        """Best-effort removal of files copied into a non-mounted sandbox."""
        for materialized in files:
            try:
                await asyncio.to_thread(sandbox.delete_file, materialized.virtual_path)
            except Exception:
                logger.warning(
                    "[Feishu] failed to clean sandbox attachment: %s",
                    materialized.virtual_path,
                    exc_info=True,
                )

    async def _finish_cancelled_sandbox_sync(
        self,
        sync_task: asyncio.Task[object],
        sandbox: Any,
        materialized_files: list[_MaterializedInboundFile],
    ) -> None:
        """Finish an uncancellable worker and remove every possible residue."""
        await asyncio.gather(sync_task, return_exceptions=True)
        await self._delete_published_sandbox_files(sandbox, materialized_files)
        for materialized in materialized_files:
            try:
                materialized.actual_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "[Feishu] failed to clean host attachment after sandbox sync: %s",
                    materialized.actual_path,
                    exc_info=True,
                )

    async def _sync_published_files(
        self,
        materialized_files: list[_MaterializedInboundFile],
        *,
        thread_id: str,
        owner_user_id: str,
        message_id: str,
    ) -> None:
        """Expose admitted files to the explicitly owner-scoped sandbox."""
        if not materialized_files:
            return
        sandbox_provider = get_sandbox_provider()
        sandbox_id = sandbox_provider.acquire(thread_id, user_id=owner_user_id)
        if sandbox_id == "local" or sandbox_provider.uses_thread_data_mounts:
            return
        sandbox = sandbox_provider.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"Sandbox not found for thread {thread_id}")

        synced_files: list[_MaterializedInboundFile] = []
        for materialized in materialized_files:
            sync_task = asyncio.create_task(
                asyncio.to_thread(
                    sandbox.update_file_from_path,
                    materialized.virtual_path,
                    str(materialized.actual_path),
                )
            )
            try:
                await asyncio.shield(sync_task)
                synced_files.append(materialized)
            except asyncio.CancelledError:
                done, _pending = await asyncio.wait(
                    {sync_task},
                    timeout=FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS,
                )
                cleanup_files = [*synced_files, materialized]
                if done:
                    await asyncio.gather(sync_task, return_exceptions=True)
                    await self._delete_published_sandbox_files(sandbox, cleanup_files)
                else:
                    cleanup_task = asyncio.create_task(
                        self._finish_cancelled_sandbox_sync(
                            sync_task,
                            sandbox,
                            cleanup_files,
                        )
                    )
                    self._track_background_task(
                        cleanup_task,
                        name="published_attachment_cleanup",
                        msg_id=message_id,
                    )
                raise
            except BaseException:
                await self._delete_published_sandbox_files(
                    sandbox,
                    [*synced_files, materialized],
                )
                raise

    async def _receive_single_file(self, message_id: str, file_key: str, type: Literal["image", "file"], thread_id: str) -> str:
        request = self._GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(type).build()

        def inner():
            return self._api_client.im.v1.message_resource.get(request)

        try:
            response = await asyncio.to_thread(inner)
        except Exception:
            logger.exception("[Feishu] resource get request failed for resource_key=%s type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not response.success():
            logger.warning(
                "[Feishu] resource get failed: resource_key=%s, type=%s, code=%s, msg=%s, log_id=%s ",
                file_key,
                type,
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return f"Failed to obtain the [{type}]"

        image_stream = getattr(response, "file", None)
        if image_stream is None:
            logger.warning("[Feishu] resource get returned no file stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        try:
            content = await asyncio.to_thread(_read_inbound_resource, image_stream)
        except ValueError as exc:
            logger.warning(
                "[Feishu] rejected inbound resource: resource_key=%s, type=%s, reason=%s",
                file_key,
                type,
                exc,
            )
            return f"Failed to obtain the [{type}]"
        except Exception:
            logger.exception("[Feishu] failed to read resource stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not content:
            logger.warning("[Feishu] empty resource content: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        paths = get_paths()
        user_id = get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=user_id)
        uploads_dir = paths.sandbox_uploads_dir(thread_id, user_id=user_id).resolve()

        ext = "png" if type == "image" else "bin"
        raw_filename = getattr(response, "file_name", "") or f"feishu_{file_key[-12:]}.{ext}"

        # Sanitize filename: preserve extension, replace path chars in name part
        if "." in raw_filename:
            name_part, ext = raw_filename.rsplit(".", 1)
            name_part = re.sub(r"[./\\]", "_", name_part)
            filename = f"{name_part}.{ext}"
        else:
            filename = re.sub(r"[./\\]", "_", raw_filename)
        resolved_target = uploads_dir / filename

        def down_load():
            # use thread_lock to avoid filename conflicts when writing
            with self._thread_lock:
                resolved_target.write_bytes(content)

        try:
            await asyncio.to_thread(down_load)
        except Exception:
            logger.exception("[Feishu] failed to persist downloaded resource: %s, type=%s", resolved_target, type)
            return f"Failed to obtain the [{type}]"

        virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{resolved_target.name}"

        try:
            sandbox_provider = get_sandbox_provider()
            sandbox_id = sandbox_provider.acquire(thread_id)
            if sandbox_id != "local":
                sandbox = sandbox_provider.get(sandbox_id)
                if sandbox is None:
                    logger.warning("[Feishu] sandbox not found for thread_id=%s", thread_id)
                    return f"Failed to obtain the [{type}]"
                sandbox.update_file(virtual_path, content)
        except Exception:
            logger.exception("[Feishu] failed to sync resource into non-local sandbox: %s", virtual_path)
            return f"Failed to obtain the [{type}]"

        logger.info("[Feishu] downloaded resource mapped: file_key=%s -> %s", file_key, virtual_path)
        return virtual_path

    # -- message formatting ------------------------------------------------

    @staticmethod
    def _build_card_content(text: str) -> str:
        """Build a Feishu interactive card with markdown content.

        Feishu's interactive card format natively renders markdown, including
        headers, bold/italic, code blocks, lists, and links.
        """
        card = {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "elements": [{"tag": "markdown", "content": text}],
        }
        return json.dumps(card)

    # -- reaction helpers --------------------------------------------------

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """Add an emoji reaction to a message."""
        if not self._api_client or not self._CreateMessageReactionRequest:
            return
        try:
            request = self._CreateMessageReactionRequest.builder().message_id(message_id).request_body(self._CreateMessageReactionRequestBody.builder().reaction_type(self._Emoji.builder().emoji_type(emoji_type).build()).build()).build()
            await asyncio.to_thread(self._api_client.im.v1.message_reaction.create, request)
            logger.info("[Feishu] reaction '%s' added to message %s", emoji_type, message_id)
        except Exception:
            logger.exception("[Feishu] failed to add reaction '%s' to message %s", emoji_type, message_id)

    async def _reply_card(self, message_id: str, text: str) -> str | None:
        """Reply with an interactive card and return the created card message ID."""
        if not self._api_client:
            return None

        content = self._build_card_content(text)
        request = self._ReplyMessageRequest.builder().message_id(message_id).request_body(self._ReplyMessageRequestBody.builder().msg_type("interactive").content(content).reply_in_thread(True).build()).build()
        response = await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
        response_data = getattr(response, "data", None)
        return getattr(response_data, "message_id", None)

    async def _create_card(self, chat_id: str, text: str) -> None:
        """Create a new card message in the target chat."""
        if not self._api_client:
            return

        content = self._build_card_content(text)
        request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("interactive").content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.create, request)

    async def _update_card(self, message_id: str, text: str) -> None:
        """Patch an existing card message in place."""
        if not self._api_client or not self._PatchMessageRequest:
            return

        content = self._build_card_content(text)
        request = self._PatchMessageRequest.builder().message_id(message_id).request_body(self._PatchMessageRequestBody.builder().content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.patch, request)

    def _track_background_task(self, task: asyncio.Task, *, name: str, msg_id: str) -> None:
        """Keep a strong reference to fire-and-forget tasks and surface errors."""
        self._background_tasks.add(task)
        task.add_done_callback(lambda done_task, task_name=name, mid=msg_id: self._finalize_background_task(done_task, task_name, mid))

    def _finalize_background_task(self, task: asyncio.Task, name: str, msg_id: str) -> None:
        self._background_tasks.discard(task)
        self._log_task_error(task, name, msg_id)

    async def _create_running_card(self, source_message_id: str, text: str) -> str | None:
        """Create the running card and cache its message ID when available."""
        running_card_id = await self._reply_card(source_message_id, text)
        if running_card_id:
            self._running_card_ids[source_message_id] = running_card_id
            logger.info("[Feishu] running card created: source=%s card=%s", source_message_id, running_card_id)
        else:
            logger.warning("[Feishu] running card creation returned no message_id for source=%s, subsequent updates will fall back to new replies", source_message_id)
        return running_card_id

    def _ensure_running_card_started(self, source_message_id: str, text: str = "Working on it...") -> asyncio.Task | None:
        """Start running-card creation once per source message."""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return None

        running_card_task = self._running_card_tasks.get(source_message_id)
        if running_card_task:
            return running_card_task

        running_card_task = asyncio.create_task(self._create_running_card(source_message_id, text))
        self._running_card_tasks[source_message_id] = running_card_task
        running_card_task.add_done_callback(lambda done_task, mid=source_message_id: self._finalize_running_card_task(mid, done_task))
        return running_card_task

    def _finalize_running_card_task(self, source_message_id: str, task: asyncio.Task) -> None:
        if self._running_card_tasks.get(source_message_id) is task:
            self._running_card_tasks.pop(source_message_id, None)
        self._log_task_error(task, "create_running_card", source_message_id)

    async def _ensure_running_card(self, source_message_id: str, text: str = "Working on it...") -> str | None:
        """Ensure the in-thread running card exists and track its message ID."""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return running_card_id

        running_card_task = self._ensure_running_card_started(source_message_id, text)
        if running_card_task is None:
            return self._running_card_ids.get(source_message_id)
        return await running_card_task

    async def _send_running_reply(self, message_id: str) -> None:
        """Reply to a message in-thread with a running card."""
        try:
            await self._ensure_running_card(message_id)
        except Exception:
            logger.exception("[Feishu] failed to send running reply for message %s", message_id)

    async def _send_card_message(self, msg: OutboundMessage) -> None:
        """Send or update the Feishu card tied to the current request."""
        source_message_id = msg.thread_ts
        if source_message_id:
            running_card_id = self._running_card_ids.get(source_message_id)
            awaited_running_card_task = False

            if not running_card_id:
                running_card_task = self._running_card_tasks.get(source_message_id)
                if running_card_task:
                    awaited_running_card_task = True
                    running_card_id = await running_card_task

            if running_card_id:
                try:
                    await self._update_card(running_card_id, msg.text)
                except Exception:
                    if not msg.is_final:
                        raise
                    logger.exception(
                        "[Feishu] failed to patch running card %s, falling back to final reply",
                        running_card_id,
                    )
                    await self._reply_card(source_message_id, msg.text)
                else:
                    logger.info("[Feishu] running card updated: source=%s card=%s", source_message_id, running_card_id)
            elif msg.is_final:
                await self._reply_card(source_message_id, msg.text)
            elif awaited_running_card_task:
                logger.warning(
                    "[Feishu] running card task finished without message_id for source=%s, skipping duplicate non-final creation",
                    source_message_id,
                )
            else:
                await self._ensure_running_card(source_message_id, msg.text)

            if msg.is_final:
                self._running_card_ids.pop(source_message_id, None)
                await self._add_reaction(source_message_id, "DONE")
            return

        await self._create_card(msg.chat_id, msg.text)

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _log_future_error(fut, name: str, msg_id: str) -> None:
        """Callback for run_coroutine_threadsafe futures to surface errors."""
        try:
            exc = fut.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except Exception:
            pass

    @staticmethod
    def _log_task_error(task: asyncio.Task, name: str, msg_id: str) -> None:
        """Callback for background asyncio tasks to surface errors."""
        try:
            exc = task.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except asyncio.CancelledError:
            logger.info("[Feishu] %s cancelled for msg_id=%s", name, msg_id)
        except Exception:
            pass

    async def _prepare_inbound(
        self,
        msg_id: str,
        inbound: InboundMessage,
        event_id: str | None = None,
    ) -> None:
        """Claim a trusted event before reactions or MessageBus dispatch."""
        if self.binding_id:
            if not event_id or self._event_deduplicator is None:
                logger.error(
                    "[Feishu] rejecting binding event without durable deduplication",
                    extra={"binding_id": self.binding_id},
                )
                return
            if not await self._event_deduplicator.claim(
                self.binding_id,
                event_id,
                system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
            ):
                logger.info(
                    "[Feishu] duplicate event dropped",
                    extra={"binding_id": self.binding_id, "event_id": event_id},
                )
                return
        reaction_task = asyncio.create_task(self._add_reaction(msg_id, "OK"))
        self._track_background_task(reaction_task, name="add_reaction", msg_id=msg_id)
        self._ensure_running_card_started(msg_id)
        await self.bus.publish_inbound(inbound)

    def _on_message(self, event: Any) -> None:
        """Validate and enqueue one SDK message callback on the main loop."""
        if self.binding_id and self._stop_requested:
            logger.info("[Feishu] ignored event received while binding is stopping")
            return
        try:
            logger.info("[Feishu] raw event received: type=%s", type(event).__name__)
            if self.binding_id and (self._event_verifier is None or not self._event_verifier(event)):
                logger.warning(
                    "[Feishu] rejected unauthenticated or stale event",
                    extra={"binding_id": self.binding_id},
                )
                return

            event_header = getattr(event, "header", None)
            event_id = getattr(event_header, "event_id", None)
            message = event.event.message
            chat_id = message.chat_id
            msg_id = message.message_id
            sender_id = event.event.sender.sender_id.open_id

            # root_id is set when the message is a reply within a Feishu thread.
            # Use it as topic_id so all replies share the same DeerFlow thread.
            root_id = getattr(message, "root_id", None) or None
            thread_id = getattr(message, "thread_id", None) or None
            raw_chat_type = getattr(message, "chat_type", None)
            chat_type = raw_chat_type if raw_chat_type in ("p2p", "group") else "p2p"

            # Parse message content
            content = json.loads(message.content)

            # files_list store the any-file-key in feishu messages, which can be used to download the file content later
            # In Feishu channel, image_keys are independent of file_keys.
            # The file_key includes files, videos, and audio, but does not include stickers.
            files_list = []

            if "text" in content:
                # Handle plain text messages
                text = content["text"]
            elif "file_key" in content:
                file_key = content.get("file_key")
                if isinstance(file_key, str) and file_key:
                    files_list.append({"file_key": file_key})
                    text = "[file]"
                else:
                    text = ""
            elif "image_key" in content:
                image_key = content.get("image_key")
                if isinstance(image_key, str) and image_key:
                    files_list.append({"image_key": image_key})
                    text = "[image]"
                else:
                    text = ""
            elif "content" in content and isinstance(content["content"], list):
                # Handle rich-text messages with a top-level "content" list (e.g., topic groups/posts)
                text_paragraphs: list[str] = []
                for paragraph in content["content"]:
                    if isinstance(paragraph, list):
                        paragraph_text_parts: list[str] = []
                        for element in paragraph:
                            if isinstance(element, dict):
                                # Include both normal text and @ mentions
                                if element.get("tag") in ("text", "at"):
                                    text_value = element.get("text", "")
                                    if text_value:
                                        paragraph_text_parts.append(text_value)
                                elif element.get("tag") == "img":
                                    image_key = element.get("image_key")
                                    if isinstance(image_key, str) and image_key:
                                        files_list.append({"image_key": image_key})
                                        paragraph_text_parts.append("[image]")
                                elif element.get("tag") in ("file", "media"):
                                    file_key = element.get("file_key")
                                    if isinstance(file_key, str) and file_key:
                                        files_list.append({"file_key": file_key})
                                        paragraph_text_parts.append("[file]")
                        if paragraph_text_parts:
                            # Join text segments within a paragraph with spaces to avoid "helloworld"
                            text_paragraphs.append(" ".join(paragraph_text_parts))

                # Join paragraphs with blank lines to preserve paragraph boundaries
                text = "\n\n".join(text_paragraphs)
            else:
                text = ""
            text = text.strip()

            logger.info(
                "[Feishu] parsed message: chat_id=%s, msg_id=%s, root_id=%s, sender=%s, text=%r",
                chat_id,
                msg_id,
                root_id,
                sender_id,
                text[:100] if text else "",
            )

            if not (text or files_list):
                logger.info("[Feishu] empty text, ignoring message")
                return

            # Only treat known slash commands as commands; absolute paths and
            # other slash-prefixed text should be handled as normal chat.
            if _is_feishu_command(text):
                msg_type = InboundMessageType.COMMAND
            else:
                msg_type = InboundMessageType.CHAT

            # DB-driven bindings keep direct chats stable per user and groups
            # stable per chat/topic. Legacy channels preserve per-message topics.
            topic_id = (root_id or thread_id or None) if self.binding_id else (root_id or msg_id)

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
                files=files_list,
                metadata={
                    "message_id": msg_id,
                    "root_id": root_id,
                    "chat_type": chat_type,
                    **({"event_id": event_id} if isinstance(event_id, str) else {}),
                    **({"binding_id": self.binding_id, "agent_id": self.agent_id} if self.binding_id else {}),
                },
            )
            inbound.topic_id = topic_id

            # Schedule on the async event loop
            if self._main_loop and self._main_loop.is_running():
                logger.info("[Feishu] publishing inbound message to bus (type=%s, msg_id=%s)", msg_type.value, msg_id)
                fut = asyncio.run_coroutine_threadsafe(
                    self._prepare_inbound(msg_id, inbound, event_id if isinstance(event_id, str) else None),
                    self._main_loop,
                )
                fut.add_done_callback(lambda f, mid=msg_id: self._log_future_error(f, "prepare_inbound", mid))
            else:
                logger.warning("[Feishu] main loop not running, cannot publish inbound message")
        except Exception:
            logger.exception("[Feishu] error processing message")
