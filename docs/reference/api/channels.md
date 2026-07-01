# IM 渠道 Channels

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/channels`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-channels) | `/api/channels/` | Get Channels Status |
| [`POST`](#post-api-channels-name-restart) | `/api/channels/{name}/restart` | Restart Channel |

## `GET /api/channels/`

> Get Channels Status  
<a id="get-api-channels"></a>

Get the status of all IM channels.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ChannelStatusResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/channels/'
```

---

## `POST /api/channels/{name}/restart`

> Restart Channel  
<a id="post-api-channels-name-restart"></a>

Restart a specific IM channel.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ChannelRestartResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/channels/{name}/restart'
```

---

## 数据模型

### `ChannelRestartResponse`

ChannelRestartResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `success` | boolean | 是 | Success |
| `message` | string | 是 | Message |

### `ChannelStatusResponse`

ChannelStatusResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `service_running` | boolean | 是 | Service Running |
| `channels` | object | 是 | Channels |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
