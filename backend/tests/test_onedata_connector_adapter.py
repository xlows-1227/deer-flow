from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.connectors.adapters.onedata import (
    ONEDATA_API_BASE_URL_ENV,
    OneDataConnectorAdapter,
    _canonical_signature_payload,
    _request_signature,
)
from deerflow.connectors.errors import ConnectorExecutionError, ConnectorValidationError
from deerflow.connectors.schemas import (
    ONEDATA_CALL_API,
    ONEDATA_GET_PARAMS,
    ONEDATA_LIST_APIS,
    ConnectorCredentialRef,
    ConnectorInstance,
    ConnectorRuntimeContext,
)


def _instance(*, config: dict | None = None) -> ConnectorInstance:
    return ConnectorInstance(
        id="od1",
        name="onedata_prod",
        type="onedata",
        config=config or {},
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
async def test_get_params_normalizes_deployed_call_url_and_accepts_snake_case_api_id(monkeypatch):
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
                    "callUrl": "http://biz.test/v1/store/sales",
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
            {"api_id": 1001},
            {},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )

    assert result["calUrl"] == "http://biz.test/v1/store/sales"
    assert result["callUrl"] == "http://biz.test/v1/store/sales"
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


def test_hmac_signature_matches_onedata_java_example():
    body = {
        "paramData": {
            "brandCode": 1,
            "startDate": "2023-06-01",
            "endDate": "2023-06-01",
        },
        "pageNum": 1,
        "pageSize": 5000,
    }
    canonical = _canonical_signature_payload(
        body,
        secret_id="210529039cf643e8a05c0063d3ed4e04",
        timestamp="1692693803174",
    )

    assert canonical == ("brandCode=1&endDate=2023-06-01&pageNum=1&pageSize=5000&secretId=210529039cf643e8a05c0063d3ed4e04&startDate=2023-06-01&timestamp=1692693803174")
    assert (
        _request_signature(
            auth_mode="hmac_sha256",
            secret_id="210529039cf643e8a05c0063d3ed4e04",
            secret_key="45frtsrtf88df69fgfhjg023",
            timestamp="1692693803174",
            body=body,
        )
        == "jKBQ9+y4GpHiroZG+jUh2TK0IClfQ+Ob30nea5K4XMw="
    )


@pytest.mark.asyncio
async def test_call_api_supports_hmac_and_snake_case_request_fields(monkeypatch):
    monkeypatch.delenv(ONEDATA_API_BASE_URL_ENV, raising=False)
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.post = AsyncMock(return_value=_mock_response({"status": 200, "message": "success", "data": {}}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("deerflow.connectors.adapters.onedata.time.time", return_value=1692693803.174),
        patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client),
    ):
        result = await adapter.execute(
            _instance(config={"auth_mode": "hmac_sha256", "response_mode": "strict"}),
            ONEDATA_CALL_API,
            {
                "call_url": "http://biz.test/v1/store/sales",
                "param_data": {
                    "brandCode": 1,
                    "startDate": "2023-06-01",
                    "endDate": "2023-06-01",
                },
                "page_num": 1,
                "page_size": 5000,
                "has_total": False,
            },
            {},
            _context(),
            secrets={"username": "210529039cf643e8a05c0063d3ed4e04", "password": "45frtsrtf88df69fgfhjg023"},
        )

    assert result["status"] == 200
    request = client.post.await_args.kwargs
    assert request["json"] == {
        "paramData": {
            "brandCode": 1,
            "startDate": "2023-06-01",
            "endDate": "2023-06-01",
        },
        "pageSize": 5000,
        "pageNum": 1,
        "hasTotal": False,
    }
    assert request["headers"]["sign"] == "G+2v+AJFDogTiBgDhBTzIgpYMisrqSEn+gtrUVnYTxI="


@pytest.mark.asyncio
async def test_call_api_strict_mode_surfaces_business_error():
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.post = AsyncMock(return_value=_mock_response({"status": 10004000, "message": "RPC SQL查询报错: 函数执行错误: api_parse_list", "data": {"result": None}}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client):
        with pytest.raises(ConnectorExecutionError, match="api_parse_list.*status=10004000"):
            await adapter.execute(
                _instance(config={"response_mode": "strict"}),
                ONEDATA_CALL_API,
                {"callUrl": "http://biz.test/v1/store/sales", "paramData": {}},
                {},
                _context(),
                secrets={"username": "sid", "password": "skey"},
            )


@pytest.mark.asyncio
async def test_call_api_rejects_non_object_param_data_and_unpaired_pagination():
    adapter = OneDataConnectorAdapter()
    common = (_instance(), ONEDATA_CALL_API)
    tail = ({}, _context())
    secrets = {"username": "sid", "password": "skey"}

    with pytest.raises(ConnectorValidationError, match="must be a JSON object"):
        await adapter.execute(*common, {"callUrl": "http://biz.test", "param_data": ["SH001"]}, *tail, secrets=secrets)
    with pytest.raises(ConnectorValidationError, match="must be provided together"):
        await adapter.execute(*common, {"callUrl": "http://biz.test", "page_size": 20}, *tail, secrets=secrets)


@pytest.mark.asyncio
async def test_call_api_resolves_deployed_call_url_from_api_id(monkeypatch):
    monkeypatch.setenv(ONEDATA_API_BASE_URL_ENV, "http://onedata.test/v1")
    adapter = OneDataConnectorAdapter()
    discovery_client = AsyncMock()
    discovery_client.get = AsyncMock(
        return_value=_mock_response(
            {
                "code": 200,
                "msg": "success",
                "result": {
                    "apiId": 639,
                    "apiName": "PH口袋sales",
                    "callUrl": "http://biz.test/v1/store/data/getPhSales",
                    "requestParam": [{"type": "String", "name": "startDate"}],
                },
            }
        )
    )
    discovery_client.__aenter__ = AsyncMock(return_value=discovery_client)
    discovery_client.__aexit__ = AsyncMock(return_value=None)

    business_client = AsyncMock()
    business_client.post = AsyncMock(return_value=_mock_response({"status": 0, "message": "success", "data": {"result": []}}))
    business_client.__aenter__ = AsyncMock(return_value=business_client)
    business_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "deerflow.connectors.adapters.onedata.httpx.AsyncClient",
        side_effect=[discovery_client, business_client],
    ):
        result = await adapter.execute(
            _instance(),
            ONEDATA_CALL_API,
            {"apiId": 639, "paramData": {"startDate": "2026-08-01", "endDate": "2026-08-04"}},
            {},
            _context(),
            secrets={"username": "sid", "password": "skey"},
        )

    assert result["status"] == 0
    assert discovery_client.get.await_args.kwargs["params"] == {"secretId": "sid", "apiId": 639}
    business_client.post.assert_awaited_once()
    assert business_client.post.await_args.args[0] == "http://biz.test/v1/store/data/getPhSales"
    assert business_client.post.await_args.kwargs["json"]["paramData"] == {
        "startDate": "2026-08-01",
        "endDate": "2026-08-04",
    }


@pytest.mark.asyncio
async def test_full_ph_sales_discovery_params_and_request_assembly(monkeypatch):
    """Exercise the deterministic tool flow behind the matching Q&A sequence."""
    monkeypatch.setenv(ONEDATA_API_BASE_URL_ENV, "http://onedata.test/v1")
    adapter = OneDataConnectorAdapter()
    discovery_client = AsyncMock()
    discovery_client.get = AsyncMock(
        side_effect=[
            _mock_response(
                {
                    "code": 200,
                    "msg": "success",
                    "result": [{"apiId": 639, "apiName": "PH口袋sales", "apiDesc": "获取PH口袋的sales"}],
                }
            ),
            _mock_response(
                {
                    "code": 200,
                    "msg": "success",
                    "result": {
                        "apiId": 639,
                        "apiName": "PH口袋sales",
                        "callUrl": "http://apiservice/v1/store/data/getPhSales",
                        "requestParam": [
                            {"type": "String", "name": "startDate"},
                            {"type": "String", "name": "endDate"},
                            {"type": "String", "name": "store_code"},
                        ],
                    },
                }
            ),
            _mock_response(
                {
                    "code": 200,
                    "msg": "success",
                    "result": {
                        "apiId": 639,
                        "apiName": "PH口袋sales",
                        "callUrl": "http://apiservice/v1/store/data/getPhSales",
                        "requestParam": [],
                    },
                }
            ),
        ]
    )
    discovery_client.__aenter__ = AsyncMock(return_value=discovery_client)
    discovery_client.__aexit__ = AsyncMock(return_value=None)

    business_client = AsyncMock()
    business_client.post = AsyncMock(return_value=_mock_response({"status": 200, "message": "success", "data": {"result": []}}))
    business_client.__aenter__ = AsyncMock(return_value=business_client)
    business_client.__aexit__ = AsyncMock(return_value=None)
    instance = _instance(config={"auth_mode": "hmac_sha256", "response_mode": "strict"})
    secrets = {"username": "sid", "password": "skey"}

    with (
        patch(
            "deerflow.connectors.adapters.onedata.httpx.AsyncClient",
            side_effect=[discovery_client, discovery_client, discovery_client, business_client],
        ),
        patch("deerflow.connectors.adapters.onedata.time.time", return_value=1700000000),
    ):
        catalog = await adapter.execute(instance, ONEDATA_LIST_APIS, {}, {}, _context(), secrets=secrets)
        metadata = await adapter.execute(
            instance,
            ONEDATA_GET_PARAMS,
            {"api_id": 639},
            {},
            _context(),
            secrets=secrets,
        )
        result = await adapter.execute(
            instance,
            ONEDATA_CALL_API,
            {
                "api_id": 639,
                "param_data": {
                    "startDate": "2026-08-01",
                    "endDate": "2026-08-04",
                    "store_code": "KFC001",
                },
                "page_size": 20,
                "page_num": 1,
                "order_by": "store_code asc",
            },
            {},
            _context(),
            secrets=secrets,
        )

    assert catalog == {"apis": [{"apiId": 639, "apiName": "PH口袋sales", "apiDesc": "获取PH口袋的sales"}]}
    assert [item["name"] for item in metadata["requestParam"]] == ["startDate", "endDate", "store_code"]
    assert result["status"] == 200
    request = business_client.post.await_args
    assert request.args[0] == "http://apiservice/v1/store/data/getPhSales"
    assert request.kwargs["headers"] == {
        "Content-Type": "application/json",
        "secretId": "sid",
        "timestamp": "1700000000000",
        "sign": "c2yX6WZUsA3UfgWZOBRBYrTtvr6ZXQCXxMqp8cfKJPc=",
    }
    assert request.kwargs["json"] == {
        "paramData": {
            "startDate": "2026-08-01",
            "endDate": "2026-08-04",
            "store_code": "KFC001",
        },
        "pageSize": 20,
        "pageNum": 1,
        "orderBy": "store_code asc",
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
async def test_call_api_requires_api_id_or_call_url():
    adapter = OneDataConnectorAdapter()
    with pytest.raises(ConnectorValidationError, match="apiId/api_id or callUrl/calUrl"):
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
    client.get = AsyncMock(
        return_value=_mock_response(
            {
                "code": 200,
                "msg": "success",
                "result": [{"apiId": 1001, "apiName": "PH口袋sales"}],
            }
        )
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("deerflow.connectors.adapters.onedata.httpx.AsyncClient", return_value=client):
        result = await adapter.test(_instance(), {"username": "sid", "password": "skey"})

    assert result.status == "ok"
    assert ONEDATA_LIST_APIS in (result.capabilities or [])


@pytest.mark.asyncio
async def test_list_apis_accepts_http_style_success_code(monkeypatch):
    monkeypatch.setenv(ONEDATA_API_BASE_URL_ENV, "http://onedata.test/v1")
    adapter = OneDataConnectorAdapter()
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=_mock_response(
            {
                "code": 200,
                "msg": "success",
                "result": [{"apiId": 1001, "apiName": "PH口袋sales"}],
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

    assert result == {"apis": [{"apiId": 1001, "apiName": "PH口袋sales"}]}
