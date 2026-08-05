from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from deerflow.connectors.errors import ConnectorExecutionError, ConnectorValidationError
from deerflow.connectors.schemas import (
    ONEDATA_CALL_API,
    ONEDATA_GET_PARAMS,
    ONEDATA_LIST_APIS,
    ConnectorInstance,
    ConnectorMetadata,
    ConnectorRuntimeContext,
    ConnectorTestResult,
)

ONEDATA_API_BASE_URL_ENV = "ONEDATA_API_BASE_URL"
ONEDATA_SUCCESS_CODES = frozenset({-9999800, 200})
ONEDATA_BUSINESS_SUCCESS_CODES = frozenset({-9999800, 0, 200})

ONEDATA_AUTH_LEGACY_RAW = "legacy_raw"
ONEDATA_AUTH_HMAC_SHA256 = "hmac_sha256"
ONEDATA_AUTH_MODES = frozenset({ONEDATA_AUTH_LEGACY_RAW, ONEDATA_AUTH_HMAC_SHA256})

ONEDATA_RESPONSE_RAW = "raw"
ONEDATA_RESPONSE_STRICT = "strict"
ONEDATA_RESPONSE_MODES = frozenset({ONEDATA_RESPONSE_RAW, ONEDATA_RESPONSE_STRICT})

_MISSING = object()
_REQUEST_FIELD_ALIASES = {
    "pageSize": ("pageSize", "page_size"),
    "pageNum": ("pageNum", "page_num"),
    "orderBy": ("orderBy", "order_by"),
    "maxSize": ("maxSize", "max_size"),
    "hasTotal": ("hasTotal", "has_total"),
}


def _discovery_base_url() -> str:
    base = (os.getenv(ONEDATA_API_BASE_URL_ENV) or "").strip().rstrip("/")
    if not base:
        raise ConnectorValidationError(
            f"{ONEDATA_API_BASE_URL_ENV} is not set. Configure the OneData discovery base URL in the environment.",
            recoverable=True,
        )
    return base


def _credentials(secrets: dict[str, Any]) -> tuple[str, str]:
    secret_id = str(secrets.get("username") or secrets.get("secretId") or "").strip()
    secret_key = str(secrets.get("password") or secrets.get("secretKey") or "").strip()
    if not secret_id or not secret_key:
        raise ConnectorValidationError("OneData connector requires secretId and secretKey", recoverable=True)
    return secret_id, secret_key


def _timeout_seconds(policy: dict[str, Any] | None) -> float:
    if not policy:
        return 30.0
    for key in ("request_timeout_ms", "timeout_ms", "statement_timeout_ms"):
        value = policy.get(key)
        if isinstance(value, int) and value > 0:
            return value / 1000
    return 30.0


def _api_id(args: dict[str, Any]) -> Any | None:
    for key in ("apiId", "api_id"):
        value = args.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _call_url(values: dict[str, Any]) -> str:
    return str(values.get("callUrl") or values.get("calUrl") or values.get("call_url") or "").strip()


def _first_value(values: Mapping[str, Any], keys: Sequence[str], *, default: Any = _MISSING) -> Any:
    for key in keys:
        if key in values:
            return values[key]
    return default


def _configured_mode(
    config: dict[str, Any],
    *,
    snake_key: str,
    camel_key: str,
    default: str,
    allowed: frozenset[str],
) -> str:
    value = str(config.get(snake_key) or config.get(camel_key) or default).strip().lower()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConnectorValidationError(f"Invalid OneData {snake_key}: {value}. Expected one of: {choices}", recoverable=True)
    return value


def _request_body(args: dict[str, Any]) -> dict[str, Any]:
    param_data = _first_value(args, ("paramData", "param_data"), default={})
    if param_data is None:
        param_data = {}
    if not isinstance(param_data, dict):
        raise ConnectorValidationError("OneData paramData/param_data must be a JSON object", recoverable=True)

    body: dict[str, Any] = {"paramData": param_data}
    for target, aliases in _REQUEST_FIELD_ALIASES.items():
        value = _first_value(args, aliases)
        if value is not _MISSING and value is not None:
            body[target] = value

    if ("pageSize" in body) != ("pageNum" in body):
        raise ConnectorValidationError("OneData pageSize/page_size and pageNum/page_num must be provided together", recoverable=True)
    return body


def _java_string(value: Any) -> str:
    """Mirror the Java service's scalar/list conversion used for signing."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(_java_string(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _canonical_signature_payload(body: dict[str, Any], *, secret_id: str, timestamp: str) -> str:
    flattened = {key: value for key, value in body.items() if key != "debugConfig"}
    param_data = flattened.pop("paramData", None)
    if isinstance(param_data, dict):
        flattened.update(param_data)
    flattened.update({"timestamp": timestamp, "secretId": secret_id})
    return "&".join(f"{key}={_java_string(flattened[key])}" for key in sorted(flattened)).replace(" ", "")


def _request_signature(
    *,
    auth_mode: str,
    secret_id: str,
    secret_key: str,
    timestamp: str,
    body: dict[str, Any],
) -> str:
    if auth_mode == ONEDATA_AUTH_LEGACY_RAW:
        return secret_key
    canonical = _canonical_signature_payload(body, secret_id=secret_id, timestamp=timestamp)
    digest = hmac.new(secret_key.encode(), canonical.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _is_success_code(value: Any, allowed: frozenset[int]) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return int(str(value).strip()) in allowed
    except (TypeError, ValueError):
        return False


def _validate_business_response(payload: dict[str, Any], *, response_mode: str) -> None:
    if response_mode != ONEDATA_RESPONSE_STRICT or "status" not in payload:
        return
    status = payload.get("status")
    if _is_success_code(status, ONEDATA_BUSINESS_SUCCESS_CODES):
        return
    message = str(payload.get("message") or "OneData business API failed")
    raise ConnectorExecutionError(f"{message[:500]} (status={status})", recoverable=True)


def _raise_for_discovery(payload: dict[str, Any], *, action: str) -> Any:
    code = payload.get("code")
    if not _is_success_code(code, ONEDATA_SUCCESS_CODES):
        message = payload.get("msg") or payload.get("message") or f"OneData {action} failed"
        raise ConnectorExecutionError(f"{message} (code={code})", recoverable=True)
    return payload.get("result")


class OneDataConnectorAdapter:
    type = "onedata"
    display_name = "OneData"

    async def test(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> ConnectorTestResult:
        start = time.perf_counter()
        try:
            await self._list_apis(secrets)
            return ConnectorTestResult(
                status="ok",
                latency_ms=int((time.perf_counter() - start) * 1000),
                capabilities=[ONEDATA_LIST_APIS, ONEDATA_GET_PARAMS, ONEDATA_CALL_API],
            )
        except ConnectorValidationError as exc:
            return ConnectorTestResult(status="error", latency_ms=int((time.perf_counter() - start) * 1000), message=exc.message)
        except ConnectorExecutionError as exc:
            return ConnectorTestResult(status="error", latency_ms=int((time.perf_counter() - start) * 1000), message=exc.message)
        except httpx.HTTPError as exc:
            return ConnectorTestResult(status="error", latency_ms=int((time.perf_counter() - start) * 1000), message=str(exc))

    async def introspect(self, instance: ConnectorInstance, secrets: dict[str, Any]) -> ConnectorMetadata:  # noqa: ARG002
        return ConnectorMetadata()

    async def execute(
        self,
        instance: ConnectorInstance,
        capability: str,
        args: dict[str, Any],
        policy: dict[str, Any],
        context: ConnectorRuntimeContext,  # noqa: ARG002
        *,
        secrets: dict[str, Any] | None = None,
    ) -> Any:
        resolved = secrets or {}
        if capability == ONEDATA_LIST_APIS:
            return {"apis": await self._list_apis(resolved)}
        if capability == ONEDATA_GET_PARAMS:
            api_id = _api_id(args)
            if api_id is None:
                raise ConnectorValidationError("onedata.get_params requires apiId or api_id", recoverable=True)
            return await self._get_params(resolved, api_id=api_id)
        if capability == ONEDATA_CALL_API:
            return await self._call_api(instance, resolved, args=args, policy=policy)
        raise ConnectorValidationError(f"Unsupported OneData capability: {capability}", recoverable=True)

    async def _list_apis(self, secrets: dict[str, Any]) -> list[dict[str, Any]]:
        secret_id, _secret_key = _credentials(secrets)
        url = f"{_discovery_base_url()}/agent/apis"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params={"secretId": secret_id})
            response.raise_for_status()
            payload = response.json()
        result = _raise_for_discovery(payload, action="list_apis")
        if result is None:
            return []
        if not isinstance(result, list):
            raise ConnectorExecutionError("OneData list_apis returned unexpected result shape", recoverable=True)
        return result

    async def _get_params(self, secrets: dict[str, Any], *, api_id: Any) -> dict[str, Any]:
        secret_id, _secret_key = _credentials(secrets)
        url = f"{_discovery_base_url()}/agent/params"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params={"secretId": secret_id, "apiId": api_id})
            response.raise_for_status()
            payload = response.json()
        result = _raise_for_discovery(payload, action="get_params")
        if not isinstance(result, dict):
            raise ConnectorExecutionError("OneData get_params returned unexpected result shape", recoverable=True)
        normalized = dict(result)
        call_url = _call_url(normalized)
        if call_url:
            # Deployed OneData versions use both spellings. Return both so
            # generic tool callers do not need to know which version answered.
            normalized["callUrl"] = call_url
            normalized["calUrl"] = call_url
        return normalized

    async def _call_api(
        self,
        instance: ConnectorInstance,
        secrets: dict[str, Any],
        *,
        args: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        secret_id, secret_key = _credentials(secrets)
        auth_mode = _configured_mode(
            instance.config,
            snake_key="auth_mode",
            camel_key="authMode",
            default=ONEDATA_AUTH_LEGACY_RAW,
            allowed=ONEDATA_AUTH_MODES,
        )
        response_mode = _configured_mode(
            instance.config,
            snake_key="response_mode",
            camel_key="responseMode",
            default=ONEDATA_RESPONSE_RAW,
            allowed=ONEDATA_RESPONSE_MODES,
        )
        call_url = _call_url(args)
        if not call_url:
            api_id = _api_id(args)
            if api_id is None:
                raise ConnectorValidationError("onedata.call_api requires apiId/api_id or callUrl/calUrl", recoverable=True)
            metadata = await self._get_params(secrets, api_id=api_id)
            call_url = _call_url(metadata)
            if not call_url:
                raise ConnectorExecutionError("OneData get_params returned no callUrl/calUrl", recoverable=True)

        body = _request_body(args)

        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
            "secretId": secret_id,
            "timestamp": timestamp,
            "sign": _request_signature(
                auth_mode=auth_mode,
                secret_id=secret_id,
                secret_key=secret_key,
                timestamp=timestamp,
                body=body,
            ),
        }
        timeout = _timeout_seconds(policy)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(call_url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorExecutionError(f"OneData call_api request failed: {exc}", recoverable=True) from exc

        if not isinstance(payload, dict):
            raise ConnectorExecutionError("OneData call_api returned unexpected response shape", recoverable=True)
        _validate_business_response(payload, response_mode=response_mode)
        return payload
