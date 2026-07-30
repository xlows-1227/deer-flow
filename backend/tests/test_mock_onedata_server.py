from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SUCCESS_CODE = -9999800
MOCK_SECRET_ID = "mock-secret-id"
MOCK_SECRET_KEY = "mock-secret-key"


def _load_app():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mock_onedata_server.py"
    spec = importlib.util.spec_from_file_location("mock_onedata_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


@pytest.fixture
def client():
    return TestClient(_load_app())


def test_list_apis_returns_catalog(client: TestClient):
    response = client.get("/v1/agent/apis", params={"secretId": MOCK_SECRET_ID})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == SUCCESS_CODE
    assert payload["msg"] == "success"
    assert isinstance(payload["result"], list)
    assert len(payload["result"]) >= 1
    assert {"apiId", "apiName", "apiDesc"} <= set(payload["result"][0])


def test_list_apis_rejects_invalid_secret(client: TestClient):
    response = client.get("/v1/agent/apis", params={"secretId": "bad"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 4009
    assert payload["result"] is None


def test_get_params_returns_cal_url_pointing_at_mock(client: TestClient):
    response = client.get(
        "/v1/agent/params",
        params={"secretId": MOCK_SECRET_ID, "apiId": 1001},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == SUCCESS_CODE
    result = payload["result"]
    assert result["apiId"] == 1001
    assert result["calUrl"].endswith("/v1/biz/store/sales")
    assert isinstance(result["requestParam"], list)
    assert isinstance(result["responseParam"], list)


def test_get_params_unknown_api(client: TestClient):
    response = client.get(
        "/v1/agent/params",
        params={"secretId": MOCK_SECRET_ID, "apiId": 9999},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 4010
    assert payload["result"] is None


def test_biz_call_echoes_param_data(client: TestClient):
    response = client.post(
        "/v1/biz/store/sales",
        headers={
            "Content-Type": "application/json",
            "secretId": MOCK_SECRET_ID,
            "timestamp": "1700000000000",
            "sign": MOCK_SECRET_KEY,
        },
        json={"paramData": {"storeCode": "SH001"}, "pageSize": 10, "pageNum": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == 0
    assert payload["message"] == "success"
    assert payload["data"]["result"]
    assert payload["data"]["pageNum"] == 1
    assert payload["data"]["pageSize"] == 10


def test_biz_call_rejects_bad_sign(client: TestClient):
    response = client.post(
        "/v1/biz/store/sales",
        headers={
            "Content-Type": "application/json",
            "secretId": MOCK_SECRET_ID,
            "timestamp": "1700000000000",
            "sign": "wrong-key",
        },
        json={"paramData": {"storeCode": "SH001"}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] != 0
