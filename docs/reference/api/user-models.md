# 自定义模型 Custom Models

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/models/custom`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-models-custom) | `/api/models/custom` | List custom models for the current user |
| [`POST`](#post-api-models-custom) | `/api/models/custom` | Create a custom model |
| [`PUT`](#put-api-models-custom-model-id) | `/api/models/custom/{model_id}` | Update a custom model |
| [`DELETE`](#delete-api-models-custom-model-id) | `/api/models/custom/{model_id}` | Delete a custom model |

## `GET /api/models/custom`

> List custom models for the current user  
<a id="get-api-models-custom"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UserModelListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/models/custom'
```

---

## `POST /api/models/custom`

> Create a custom model  
<a id="post-api-models-custom"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `display_name` | string | 否 | Display Name |
| `provider` | string（枚举: openai, anthropic） | 是 | Provider |
| `model` | string | 是 | Model |
| `base_url` | string | 否 | Base Url |
| `api_key` | string | 否 | Api Key |
| `enabled` | boolean | 否 | Enabled |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`UserModelRecord`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/models/custom'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `PUT /api/models/custom/{model_id}`

> Update a custom model  
<a id="put-api-models-custom-model-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `model_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `display_name` | string | 否 | Display Name |
| `provider` | string（枚举: openai, anthropic） | 否 | Provider |
| `model` | string | 否 | Model |
| `base_url` | string | 否 | Base Url |
| `api_key` | string | 否 | Api Key |
| `enabled` | boolean | 否 | Enabled |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UserModelRecord`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/models/custom/{model_id}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/models/custom/{model_id}`

> Delete a custom model  
<a id="delete-api-models-custom-model-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `model_id` | string | 是 |  |

**响应**

- **`204`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/models/custom/{model_id}'
```

---

## 数据模型

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `UserModelCreateBody`

UserModelCreateBody

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `display_name` | string | 否 | Display Name |
| `provider` | string（枚举: openai, anthropic） | 是 | Provider |
| `model` | string | 是 | Model |
| `base_url` | string | 否 | Base Url |
| `api_key` | string | 否 | Api Key |
| `enabled` | boolean | 否 | Enabled |

### `UserModelListResponse`

UserModelListResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `models` | array<UserModelRecord> | 否 | Models |

### `UserModelRecord`

UserModelRecord

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `id` | string | 是 | Id |
| `user_id` | string | 是 | User Id |
| `name` | string | 是 | Name |
| `display_name` | string | 否 | Display Name |
| `provider` | string（枚举: openai, anthropic） | 是 | Provider |
| `model` | string | 是 | Model |
| `base_url` | string | 否 | Base Url |
| `enabled` | boolean | 否 | Enabled |
| `has_api_key` | boolean | 否 | Has Api Key |
| `api_key_last_four` | string | 否 | Api Key Last Four |
| `created_at` | string | 否 | Created At |
| `updated_at` | string | 否 | Updated At |

### `UserModelUpdateBody`

UserModelUpdateBody

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `display_name` | string | 否 | Display Name |
| `provider` | string（枚举: openai, anthropic） | 否 | Provider |
| `model` | string | 否 | Model |
| `base_url` | string | 否 | Base Url |
| `api_key` | string | 否 | Api Key |
| `enabled` | boolean | 否 | Enabled |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
