# Assistants 兼容 Assistants Compat

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/assistants`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`POST`](#post-api-assistants-search) | `/api/assistants/search` | Search Assistants |
| [`GET`](#get-api-assistants-assistant-id) | `/api/assistants/{assistant_id}` | Get Assistant Compat |
| [`GET`](#get-api-assistants-assistant-id-graph) | `/api/assistants/{assistant_id}/graph` | Get Assistant Graph |
| [`GET`](#get-api-assistants-assistant-id-schemas) | `/api/assistants/{assistant_id}/schemas` | Get Assistant Schemas |

## `POST /api/assistants/search`

> Search Assistants  
<a id="post-api-assistants-search"></a>

Search assistants.

Returns all registered assistants (lead_agent + custom agents from config).

**请求体**（`application/json`）

类型：`AssistantSearchRequest`

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<AssistantResponse>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/assistants/search'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/assistants/{assistant_id}`

> Get Assistant Compat  
<a id="get-api-assistants-assistant-id"></a>

Get an assistant by ID.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`AssistantResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/assistants/{assistant_id}'
```

---

## `GET /api/assistants/{assistant_id}/graph`

> Get Assistant Graph  
<a id="get-api-assistants-assistant-id-graph"></a>

Get the graph structure for an assistant.

Returns a minimal graph description. Full graph introspection is
not supported in the Gateway — this stub satisfies SDK validation.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/assistants/{assistant_id}/graph'
```

---

## `GET /api/assistants/{assistant_id}/schemas`

> Get Assistant Schemas  
<a id="get-api-assistants-assistant-id-schemas"></a>

Get JSON schemas for an assistant's input/output/state.

Returns empty schemas — full introspection not supported in Gateway.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/assistants/{assistant_id}/schemas'
```

---

## 数据模型

### `AssistantResponse`

AssistantResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 是 | Assistant Id |
| `graph_id` | string | 是 | Graph Id |
| `name` | string | 是 | Name |
| `config` | object | 否 | Config |
| `metadata` | object | 否 | Metadata |
| `description` | string | 否 | Description |
| `created_at` | string | 否 | Created At |
| `updated_at` | string | 否 | Updated At |
| `version` | integer | 否 | Version |

### `AssistantSearchRequest`

AssistantSearchRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `graph_id` | string | 否 | Graph Id |
| `name` | string | 否 | Name |
| `metadata` | object | 否 | Metadata |
| `limit` | integer | 否 | Limit |
| `offset` | integer | 否 | Offset |

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
