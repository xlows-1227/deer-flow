# 健康检查 Health

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/health`
> 认证：公开接口，无需认证。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-health) | `/health` | Health Check |

## `GET /health`

> Health Check  
<a id="get-health"></a>

Health check endpoint.

Returns:
    Service health status information.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/health'
```

---
