from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.connectors.adapters.onedata import ONEDATA_API_BASE_URL_ENV, OneDataConnectorAdapter
from deerflow.connectors.errors import ConnectorValidationError
from deerflow.connectors.schemas import (
    ONEDATA_CALL_API,
    ONEDATA_GET_PARAMS,
    ONEDATA_LIST_APIS,
    ConnectorCredentialRef,
    ConnectorInstance,
    ConnectorRuntimeContext,
)


def _instance() -> ConnectorInstance:
    return ConnectorInstance(
        id="od1",
        name="onedata_prod",
        type="onedata",
        config={},
        credential=ConnectorCredentialRef(provider="inline", username="sid", password="skey"),
    )


def _context() -> ConnectorRuntimeContext:
    return ConnectorRuntimeContext(user_id="u1")


def _mock_response(payload: dict, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    return response


@pytest.mark.asyncio
async def test_list_apis_uses_env_base_url(monkeypatch):
    monkeypatch.setenv(ONEDATA_API_BASE_URL_ENV, "http://onedata.test/v1")
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=_mock_response(
            {
                "code": -9999800,
                "msg": "success",
                "result": [{"apiId": 1001, "apiName": "门店销量查询", "apiDesc": "desc"}],
            }
        )
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client):
        result = await adapter.execute(
            _instance(),
            ONEDATA_LIST_APIS,
            {},
            {},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )

    assert result["apis"][0]["apiId"] == 1001
    client.get.assert_awaited_once()
    assert client.get.await_args.args[0] == "http://onedata.test/v1/agent/apis"
    assert client.get.await_args.kwargs["params"] == {"secretId": "sid"}


@pytest.mark.asyncio
async def test_get_params_returns_cal_url(monkeypatch):
    monkeypatch.setenv(ONEDATA_API_BASE_URL_ENV, "http://onedata.test/v1")
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=_mock_response(
            {
                "code": -9999800,
                "msg": "success",
                "result": {
                    "apiId": 1001,
                    "apiName": "门店销量查询",
                    "calUrl": "http://biz.test/v1/store/sales",
                    "requestParam": [],
                    "responseParam": [],
                },
            }
        )
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client):
        result = await adapter.execute(
            _instance(),
            ONEDATA_GET_PARAMS,
            {"apiId": 1001},
            {},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )

    assert result["calUrl"] == "http://biz.test/v1/store/sales"
    assert client.get.await_args.args[0] == "http://onedata.test/v1/agent/params"


@pytest.mark.asyncio
async def test_call_api_posts_cal_url_with_secret_key_as_sign(monkeypatch):
    monkeypatch.delenv(ONEDATA_API_BASE_URL_ENV, raising=False)
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.post = AsyncMock(
        return_value=_mock_response(
            {
                "status": 0,
                "message": "success",
                "data": {"result": [{"cnt": 1}], "size": 1, "total": 1},
            }
        )
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client):
        result = await adapter.execute(
            _instance(),
            ONEDATA_CALL_API,
            {
                "calUrl": "http://biz.test/v1/store/sales",
                "paramData": {"storeCode": "SH001"},
                "pageSize": 20,
                "pageNum": 1,
            },
            {"request_timeout_ms": 5000},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )

    assert result["status"] == 0
    client.post.assert_awaited_once()
    assert client.post.await_args.args[0] == "http://biz.test/v1/store/sales"
    headers = client.post.await_args.kwargs["headers"]
    assert headers["secretId"] == "sid"
    assert headers["sign"] == "skey"
    assert "timestamp" in headers
    assert client.post.await_args.kwargs["json"] == {
        "paramData": {"storeCode": "SH001"},
        "pageSize": 20,
        "pageNum": 1,
    }


@pytest.mark.asyncio
async def test_list_apis_requires_env_base_url(monkeypatch):
    monkeypatch.delenv(ONEDATA_API_BASE_URL_ENV, raising=False)
    adapter = OneDataConnectorAdapter()
    with pytest.raises(ConnectorValidationError, match=ONEDATA_API_BASE_URL_ENV):
        await adapter.execute(
            _instance(),
            ONEDATA_LIST_APIS,
            {},
            {},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )


@pytest.mark.asyncio
async def test_call_api_requires_cal_url():
    adapter = OneDataConnectorAdapter()
    with pytest.raises(ConnectorValidationError, match="calUrl"):
        await adapter.execute(
            _instance(),
            ONEDATA_CALL_API,
            {"paramData": {}},
            {},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )


@pytest.mark.asyncio
async def test_test_connection_ok(monkeypatch):
    monkeypatch.setenv(ONEDATA_API_BASE_URL_ENV, "http://onedata.test/v1")
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response({"code": -9999800, "msg": "success", "result": []}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client):
        result = await adapter.test(_instance(), {"username": "sid", "password": "skey"})

    assert result.status == "ok"
    assert ONEDATA_LIST_APIS in (result.capabilities or [])
