from __future__ import annotations

import os
import time
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
ONEDATA_SUCCESS_CODE = -9999800


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


def _raise_for_discovery(payload: dict[str, Any], *, action: str) -> Any:
    code = payload.get("code")
    if code != ONEDATA_SUCCESS_CODE:
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
        instance: ConnectorInstance,  # noqa: ARG002
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
            api_id = args.get("apiId")
            if api_id is None:
                raise ConnectorValidationError("onedata.get_params requires apiId", recoverable=True)
            return await self._get_params(resolved, api_id=api_id)
        if capability == ONEDATA_CALL_API:
            return await self._call_api(resolved, args=args, policy=policy)
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
        return result

    async def _call_api(self, secrets: dict[str, Any], *, args: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        secret_id, secret_key = _credentials(secrets)
        cal_url = str(args.get("calUrl") or args.get("callUrl") or "").strip()
        if not cal_url:
            raise ConnectorValidationError("onedata.call_api requires calUrl from onedata.get_params", recoverable=True)

        body: dict[str, Any] = {"paramData": args.get("paramData") if isinstance(args.get("paramData"), dict) else {}}
        for key in ("pageSize", "pageNum", "orderBy", "maxSize", "hasTotal"):
            if key in args and args[key] is not None:
                body[key] = args[key]

        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
            "secretId": secret_id,
            "timestamp": timestamp,
            "sign": secret_key,
        }
        timeout = _timeout_seconds(policy)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(cal_url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorExecutionError(f"OneData call_api request failed: {exc}", recoverable=True) from exc

        if not isinstance(payload, dict):
            raise ConnectorExecutionError("OneData call_api returned unexpected response shape", recoverable=True)
        return payload
