# 分享 Shares

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/threads · /api/share`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-share-token) | `/api/share/{token}` | Get shared thread content (read-only, no auth required) |
| [`POST`](#post-api-threads-thread-id-share) | `/api/threads/{thread_id}/share` | Create a shareable link for a thread |

## `GET /api/share/{token}`

> Get shared thread content (read-only, no auth required)  
<a id="get-api-share-token"></a>

Return the conversation content for a public share token.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `token` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SharedThreadResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/share/{token}'
```

---

## `POST /api/threads/{thread_id}/share`

> Create a shareable link for a thread  
<a id="post-api-threads-thread-id-share"></a>

Generate a public share token for the given thread.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `expires_in_days` | integer | 否 | Number of days until the share link expires. Null means never expires. |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`CreateShareResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/share'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `CreateShareRequest`

Request body for creating a thread share.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `expires_in_days` | integer | 否 | Number of days until the share link expires. Null means never expires. |

### `CreateShareResponse`

Response model for creating a thread share.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `share_token` | string | 是 | Share Token |
| `share_url` | string | 是 | Share Url |
| `expires_at` | string | 是 | Expires At |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `SharedThreadResponse`

Response model for reading a shared thread.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 | Thread Id |
| `title` | string | 是 | Title |
| `created_at` | string | 是 | Created At |
| `messages` | array<object> | 是 | Messages |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
