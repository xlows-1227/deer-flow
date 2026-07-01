# API Key 管理 API Keys

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/v1/api-keys`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-v1-api-keys-current) | `/api/v1/api-keys/current` | Get Current Api Key |
| [`DELETE`](#delete-api-v1-api-keys-current) | `/api/v1/api-keys/current` | Revoke Current Api Key |
| [`PUT`](#put-api-v1-api-keys-current-policy) | `/api/v1/api-keys/current/policy` | Update Current Api Key Policy |
| [`POST`](#post-api-v1-api-keys-current-rotate) | `/api/v1/api-keys/current/rotate` | Rotate Current Api Key |

## `GET /api/v1/api-keys/current`

> Get Current Api Key  
<a id="get-api-v1-api-keys-current"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/api-keys/current'
```

---

## `DELETE /api/v1/api-keys/current`

> Revoke Current Api Key  
<a id="delete-api-v1-api-keys-current"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/v1/api-keys/current'
```

---

## `PUT /api/v1/api-keys/current/policy`

> Update Current Api Key Policy  
<a id="put-api-v1-api-keys-current-policy"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `allowed_skills` | array<string> | 否 | Allowed Skills |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/v1/api-keys/current/policy'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/v1/api-keys/current/rotate`

> Rotate Current Api Key  
<a id="post-api-v1-api-keys-current-rotate"></a>

**请求体**（`application/json`）

类型：`APIKeyPolicyRequest`

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/api-keys/current/rotate'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `APIKeyPolicyRequest`

APIKeyPolicyRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `allowed_skills` | array<string> | 否 | Allowed Skills |

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
