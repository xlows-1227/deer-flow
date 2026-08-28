"""LLM error handling middleware with retry/backoff and user-facing fallbacks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from deerflow.agents.middlewares.token_usage_middleware import (
    PublishedRunTokenLimitError,
)
from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# NOTE: 403 is intentionally excluded — it is ambiguous between auth failures
# (invalid key, forbidden) and concurrency limits (Kimi "concurrent request
# limit").  Concurrency-related 403s are retried via _BUSY_PATTERNS below;
# auth-related 403s fall through to _AUTH_PATTERNS and are not retried.
_RETRIABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_BUSY_PATTERNS = (
    "server busy",
    "temporarily unavailable",
    "try again later",
    "please retry",
    "please try again",
    "overloaded",
    "high demand",
    "rate limit",
    "concurrent",
    "access_terminated",
    "负载较高",
    "服务繁忙",
    "稍后重试",
    "请稍后重试",
)
_QUOTA_PATTERNS = (
    "insufficient_quota",
    "quota",
    "billing",
    "credit",
    "payment",
    "余额不足",
    "超出限额",
    "额度不足",
    "欠费",
)
_AUTH_PATTERNS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "permission",
    "forbidden",
    "access denied",
    "无权",
    "未授权",
)


class LLMErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Retry transient LLM errors and surface graceful assistant messages."""

    retry_max_attempts: int = 3
    retry_base_delay_ms: int = 1000
    retry_cap_delay_ms: int = 8000

    def __init__(self, *, app_config: AppConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.circuit_failure_threshold = app_config.circuit_breaker.failure_threshold
        self.circuit_recovery_timeout_sec = app_config.circuit_breaker.recovery_timeout_sec

        # Circuit Breaker state
        self._circuit_lock = threading.Lock()
        self._circuit_failure_count = 0
        self._circuit_open_until = 0.0
        self._circuit_state = "closed"
        self._circuit_probe_in_flight = False

    def _check_circuit(self) -> bool:
        """Returns True if circuit is OPEN (fast fail), False otherwise."""
        with self._circuit_lock:
            now = time.time()

            if self._circuit_state == "open":
                if now < self._circuit_open_until:
                    return True
                self._circuit_state = "half_open"
                self._circuit_probe_in_flight = False

            if self._circuit_state == "half_open":
                if self._circuit_probe_in_flight:
                    return True
                self._circuit_probe_in_flight = True
                return False

            return False

    def _record_success(self) -> None:
        with self._circuit_lock:
            if self._circuit_state != "closed" or self._circuit_failure_count > 0:
                logger.info("Circuit breaker reset (Closed). LLM service recovered.")
            self._circuit_failure_count = 0
            self._circuit_open_until = 0.0
            self._circuit_state = "closed"
            self._circuit_probe_in_flight = False

    def _record_failure(self) -> None:
        with self._circuit_lock:
            if self._circuit_state == "half_open":
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                self._circuit_state = "open"
                self._circuit_probe_in_flight = False
                logger.error(
                    "Circuit breaker probe failed (Open). Will probe again after %ds.",
                    self.circuit_recovery_timeout_sec,
                )
                return

            self._circuit_failure_count += 1
            if self._circuit_failure_count >= self.circuit_failure_threshold:
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                if self._circuit_state != "open":
                    self._circuit_state = "open"
                    self._circuit_probe_in_flight = False
                    logger.error(
                        "Circuit breaker tripped (Open). Threshold reached (%d). Will probe after %ds.",
                        self.circuit_failure_threshold,
                        self.circuit_recovery_timeout_sec,
                    )

    def _classify_error(self, exc: BaseException) -> tuple[bool, str]:
        detail = _extract_error_detail(exc)
        lowered = detail.lower()
        error_code = _extract_error_code(exc)
        status_code = _extract_status_code(exc)
        exc_name = exc.__class__.__name__.lower()

        if _matches_any(lowered, _QUOTA_PATTERNS) or _matches_any(str(error_code).lower(), _QUOTA_PATTERNS):
            return False, "quota"

        # Kimi (Moonshot) concurrency limit: returns 403 with
        # error.message containing "concurrent request limit" wrapped in
        # PermissionDeniedError / APIError. The str(exc) may be the JSON
        # body or just the class name, so we also inspect status_code +
        # exception class name as a fallback to catch this reliably.
        #
        # Priority: check busy BEFORE auth because Kimi's 403 for concurrency
        # is a PermissionDeniedError (contains "permission" in class name)
        # which would otherwise be misclassified as "auth".
        if _matches_any(lowered, _BUSY_PATTERNS):
            return True, "busy"
        if status_code == 403 and ("concurrent" in lowered or "concurrent" in exc_name):
            return True, "busy"
        # Kimi SDK sometimes wraps 403 as PermissionDeniedError with the
        # actual JSON body in an `error` attribute — check that too.
        if status_code == 403 and "permissiondenied" in exc_name:
            # Extract nested error message if available
            nested_msg = _extract_nested_error_message(exc)
            if nested_msg and "concurrent" in nested_msg.lower():
                return True, "busy"
        if _matches_any(lowered, _AUTH_PATTERNS):
            return False, "auth"

        if exc_name in {
            "apitimeouterror",
            "apiconnectionerror",
            "internalservererror",
            "readerror",  # httpx.ReadError: connection dropped mid-stream
            "remoteprotocolerror",  # httpx: server closed connection unexpectedly
        }:
            return True, "transient"
        if status_code in _RETRIABLE_STATUS_CODES:
            return True, "transient"

        return False, "generic"

    def _build_retry_delay_ms(self, attempt: int, exc: BaseException) -> int:
        retry_after = _extract_retry_after_ms(exc)
        if retry_after is not None:
            return retry_after
        backoff = self.retry_base_delay_ms * (2 ** max(0, attempt - 1))
        return min(backoff, self.retry_cap_delay_ms)

    def _build_retry_message(self, attempt: int, wait_ms: int, reason: str) -> str:
        seconds = max(1, round(wait_ms / 1000))
        reason_text = "服务繁忙" if reason == "busy" else "服务暂时不可用"
        return f"LLM 请求第 {attempt}/{self.retry_max_attempts} 次重试：{reason_text}。{seconds} 秒后重试。"

    def _build_circuit_breaker_message(self) -> str:
        return "服务连续失败过多，熔断器已触发，请稍后再试。"

    def _build_user_message(self, exc: BaseException, reason: str) -> str:
        if reason == "quota":
            return "当前模型账户额度不足，请联系管理员充值后重试。"
        if reason == "auth":
            return "当前模型账户认证失败，请检查 API Key 是否正确。"
        if reason in {"busy", "transient"}:
            return "服务暂时繁忙，请稍后重试。"
        return "抱歉，服务暂时不可用，请稍后重试。"

    def _emit_retry_event(self, attempt: int, wait_ms: int, reason: str) -> None:
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            if writer is None:
                logger.debug(
                    "No stream writer available — llm_retry event (attempt %d) skipped",
                    attempt,
                )
                return
            writer(
                {
                    "type": "llm_retry",
                    "attempt": attempt,
                    "max_attempts": self.retry_max_attempts,
                    "wait_ms": wait_ms,
                    "reason": reason,
                    "message": self._build_retry_message(attempt, wait_ms, reason),
                }
            )
        except Exception:
            logger.debug("Failed to emit llm_retry event", exc_info=True)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if self._check_circuit():
            return AIMessage(content=self._build_circuit_breaker_message())

        retry_count = 0
        while True:
            try:
                response = handler(request)
                self._record_success()
                return response
            except (GraphBubbleUp, PublishedRunTokenLimitError):
                # Preserve LangGraph control-flow signals and fail-closed
                # Published runtime policy errors. Turning either into an
                # AIMessage would make the Run appear successfully completed.
                with self._circuit_lock:
                    if self._circuit_state == "half_open":
                        self._circuit_probe_in_flight = False
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                if retriable and retry_count < self.retry_max_attempts:
                    retry_count += 1
                    wait_ms = self._build_retry_delay_ms(retry_count, exc)
                    logger.warning(
                        "Transient LLM error (retry %d/%d); retrying in %dms: %s",
                        retry_count,
                        self.retry_max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(retry_count, wait_ms, reason)
                    time.sleep(wait_ms / 1000)
                    continue
                logger.warning(
                    "LLM call failed after %d retries: %s",
                    retry_count,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:
                    self._record_failure()
                return AIMessage(content=self._build_user_message(exc, reason))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if self._check_circuit():
            return AIMessage(content=self._build_circuit_breaker_message())

        retry_count = 0
        while True:
            try:
                response = await handler(request)
                self._record_success()
                return response
            except (GraphBubbleUp, PublishedRunTokenLimitError):
                # Preserve LangGraph control-flow signals and fail-closed
                # Published runtime policy errors. Turning either into an
                # AIMessage would make the Run appear successfully completed.
                with self._circuit_lock:
                    if self._circuit_state == "half_open":
                        self._circuit_probe_in_flight = False
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                if retriable and retry_count < self.retry_max_attempts:
                    retry_count += 1
                    wait_ms = self._build_retry_delay_ms(retry_count, exc)
                    logger.warning(
                        "Transient LLM error (retry %d/%d); retrying in %dms: %s",
                        retry_count,
                        self.retry_max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(retry_count, wait_ms, reason)
                    await asyncio.sleep(wait_ms / 1000)
                    continue
                logger.warning(
                    "LLM call failed after %d retries: %s",
                    retry_count,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:
                    self._record_failure()
                return AIMessage(content=self._build_user_message(exc, reason))


def _matches_any(detail: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in detail for pattern in patterns)


def _extract_error_code(exc: BaseException) -> Any:
    for attr in ("code", "error_code"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            return value

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                value = error.get(key)
                if value not in (None, ""):
                    return value
    return None


def _extract_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _extract_retry_after_ms(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    raw = None
    header_name = ""
    for key in ("retry-after-ms", "Retry-After-Ms", "retry-after", "Retry-After"):
        header_name = key
        if hasattr(headers, "get"):
            raw = headers.get(key)
        if raw:
            break
    if not raw:
        return None

    try:
        multiplier = 1 if "ms" in header_name.lower() else 1000
        return max(0, int(float(raw) * multiplier))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            delta = target.timestamp() - time.time()
            return max(0, int(delta * 1000))
        except (TypeError, ValueError, OverflowError):
            return None


def _extract_error_detail(exc: BaseException) -> str:
    detail = str(exc).strip()
    if detail:
        return detail
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return exc.__class__.__name__


def _extract_nested_error_message(exc: BaseException) -> str | None:
    """Try to pull the JSON ``error.message`` out of SDK-wrapped exceptions.

    Kimi / Moonshot SDK often wraps the raw HTTP error (JSON body) inside
    a top-level exception (``PermissionDeniedError``, ``APIError``, …).
    The nested body contains the canonical error message that our
    pattern-matching logic relies on.
    """
    # Walk a common nesting pattern: exc.error is a pydantic model or dict
    error_obj = getattr(exc, "error", None)
    if error_obj is not None:
        # Pydantic model
        msg = getattr(error_obj, "message", None)
        if isinstance(msg, str):
            return msg
        # dict
        if isinstance(error_obj, dict):
            msg = error_obj.get("message")
            if isinstance(msg, str):
                return msg
    # Some SDKs expose the raw response body
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.json() if hasattr(resp, "json") else None
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    msg = err.get("message")
                    if isinstance(msg, str):
                        return msg
                msg = body.get("message")
                if isinstance(msg, str):
                    return msg
        except Exception:
            pass
    return None
