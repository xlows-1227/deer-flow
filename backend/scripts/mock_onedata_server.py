"""Local OneData discovery + business API mock for connector testing.

Run from backend/:
    PYTHONPATH=. uv run python scripts/mock_onedata_server.py

Docker Compose Gateway (container → host mock):
    PYTHONPATH=. uv run python scripts/mock_onedata_server.py \\
      --public-base http://host.docker.internal:18087

Then point DeerFlow at it:
    # local Gateway
    export ONEDATA_API_BASE_URL=http://127.0.0.1:18087/v1
    # Docker Gateway
    export ONEDATA_API_BASE_URL=http://host.docker.internal:18087/v1

Create an onedata connector with:
    secretId  = mock-secret-id
    secretKey = mock-secret-key
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

SUCCESS_CODE = -9999800
MOCK_SECRET_ID = "mock-secret-id"
MOCK_SECRET_KEY = "mock-secret-key"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 18087

app = FastAPI(title="OneData Mock", version="0.1.0")

# Host/port used when building calUrl; overridden by CLI before serve.
_public_base = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"


def _ok(result: Any) -> dict[str, Any]:
    return {"code": SUCCESS_CODE, "msg": "success", "result": result, "cost": None}


def _fail(code: int, msg: str) -> dict[str, Any]:
    return {"code": code, "msg": msg, "result": None, "cost": None}


def _biz_ok(*, result: Any, page_num: int | None = None, page_size: int | None = None) -> dict[str, Any]:
    rows = result if isinstance(result, list) else [result]
    data: dict[str, Any] = {
        "result": result,
        "size": len(rows) if isinstance(result, list) else (1 if result is not None else 0),
        "total": len(rows) if isinstance(result, list) else (1 if result is not None else 0),
    }
    if page_num is not None:
        data["pageNum"] = page_num
    if page_size is not None:
        data["pageSize"] = page_size
    return {"status": 0, "message": "success", "cost": 1, "data": data}


def _biz_fail(message: str, *, status: int = 401) -> dict[str, Any]:
    return {"status": status, "message": message, "cost": 1, "data": None}


def _require_secret(secret_id: str | None) -> dict[str, Any] | None:
    if not secret_id or secret_id != MOCK_SECRET_ID:
        return _fail(4009, "参数值不合法: secretId无效")
    return None


def _apis() -> list[dict[str, Any]]:
    return [
        {
            "apiId": 1001,
            "apiName": "门店销量查询",
            "apiDesc": "按门店编码查询近7天销量（mock）",
        },
        {
            "apiId": 1002,
            "apiName": "门店基础信息",
            "apiDesc": "查询门店基础属性（mock）",
        },
    ]


def _params_for(api_id: int) -> dict[str, Any] | None:
    catalog = {item["apiId"]: item for item in _apis()}
    meta = catalog.get(api_id)
    if meta is None:
        return None

    if api_id == 1001:
        return {
            "apiId": api_id,
            "apiName": meta["apiName"],
            "calUrl": f"{_public_base}/v1/biz/store/sales",
            "requestParam": [
                {
                    "id": "1",
                    "type": "String",
                    "name": "storeCode",
                    "rule": "",
                    "value": "SH001",
                    "description": "门店编码",
                    "parentId": None,
                    "priority": 1,
                    "encryptionFlag": False,
                    "decryptionFlag": False,
                    "scope": None,
                }
            ],
            "responseParam": [
                {
                    "id": "2",
                    "type": "Number",
                    "name": "salesQty",
                    "rule": "",
                    "value": "",
                    "description": "销量数量",
                    "parentId": None,
                    "priority": 1,
                    "encryptionFlag": False,
                    "decryptionFlag": False,
                    "scope": None,
                }
            ],
        }

    return {
        "apiId": api_id,
        "apiName": meta["apiName"],
        "calUrl": f"{_public_base}/v1/biz/store/info",
        "requestParam": [
            {
                "id": "1",
                "type": "String",
                "name": "storeCode",
                "rule": "",
                "value": "SH001",
                "description": "门店编码",
                "parentId": None,
                "priority": 1,
                "encryptionFlag": False,
                "decryptionFlag": False,
                "scope": None,
            }
        ],
        "responseParam": [
            {
                "id": "2",
                "type": "String",
                "name": "storeName",
                "rule": "",
                "value": "",
                "description": "门店名称",
                "parentId": None,
                "priority": 1,
                "encryptionFlag": False,
                "decryptionFlag": False,
                "scope": None,
            }
        ],
    }


def _auth_biz(secret_id: str | None, sign: str | None) -> dict[str, Any] | None:
    if secret_id != MOCK_SECRET_ID:
        return _biz_fail("参数值不合法: secretId无效", status=4009)
    # DeerFlow connector sends plaintext secretKey as sign (no HMAC).
    if sign != MOCK_SECRET_KEY:
        return _biz_fail("鉴权失败: sign无效", status=401)
    return None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/agent/apis")
async def list_apis(secretId: str | None = Query(default=None)) -> JSONResponse:
    err = _require_secret(secretId)
    if err is not None:
        return JSONResponse(err)
    return JSONResponse(_ok(_apis()))


@app.get("/v1/agent/params")
async def get_params(
    secretId: str | None = Query(default=None),
    apiId: int | None = Query(default=None),
) -> JSONResponse:
    err = _require_secret(secretId)
    if err is not None:
        return JSONResponse(err)
    if apiId is None:
        return JSONResponse(_fail(4009, "参数值不合法: apiId为空"))
    result = _params_for(apiId)
    if result is None:
        return JSONResponse(_fail(4010, "找不到对象: 接口不存在"))
    return JSONResponse(_ok(result))


@app.post("/v1/biz/store/sales")
async def store_sales(
    request: Request,
    secretId: str | None = Header(default=None),
    sign: str | None = Header(default=None),
) -> JSONResponse:
    err = _auth_biz(secretId, sign)
    if err is not None:
        return JSONResponse(err)
    body = await request.json()
    param_data = body.get("paramData") if isinstance(body.get("paramData"), dict) else {}
    store_code = str(param_data.get("storeCode") or "UNKNOWN")
    page_num = body.get("pageNum")
    page_size = body.get("pageSize")
    return JSONResponse(
        _biz_ok(
            result=[
                {"storeCode": store_code, "salesQty": 128, "queryEcho": param_data},
                {"storeCode": store_code, "salesQty": 96, "dayOffset": 1},
            ],
            page_num=page_num if isinstance(page_num, int) else None,
            page_size=page_size if isinstance(page_size, int) else None,
        )
    )


@app.post("/v1/biz/store/info")
async def store_info(
    request: Request,
    secretId: str | None = Header(default=None),
    sign: str | None = Header(default=None),
) -> JSONResponse:
    err = _auth_biz(secretId, sign)
    if err is not None:
        return JSONResponse(err)
    body = await request.json()
    param_data = body.get("paramData") if isinstance(body.get("paramData"), dict) else {}
    store_code = str(param_data.get("storeCode") or "UNKNOWN")
    return JSONResponse(
        _biz_ok(
            result={
                "storeCode": store_code,
                "storeName": f"Mock Store {store_code}",
                "city": "上海市",
                "queryEcho": param_data,
            }
        )
    )


def main() -> None:
    global _public_base

    parser = argparse.ArgumentParser(description="OneData mock server for DeerFlow connector tests")
    parser.add_argument("--host", default=os.getenv("ONEDATA_MOCK_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("ONEDATA_MOCK_PORT", str(DEFAULT_PORT))))
    parser.add_argument(
        "--public-base",
        default=os.getenv("ONEDATA_MOCK_PUBLIC_BASE", ""),
        help="Base URL embedded into calUrl (default: http://{host}:{port})",
    )
    args = parser.parse_args()
    if args.public_base:
        _public_base = args.public_base.rstrip("/")
    elif args.host in {"0.0.0.0", "::"}:
        _public_base = f"http://127.0.0.1:{args.port}"
    else:
        _public_base = f"http://{args.host}:{args.port}"

    print(f"OneData mock listening on http://{args.host}:{args.port}")
    print(f"  public calUrl base: {_public_base}")
    print(f"  ONEDATA_API_BASE_URL={_public_base}/v1")
    print(f"  secretId={MOCK_SECRET_ID}")
    print(f"  secretKey={MOCK_SECRET_KEY}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
