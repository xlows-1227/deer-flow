# 外部 API V1 External API

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/v1/external`
> 认证：使用 Bearer API Key 认证：请求头 `Authorization: Bearer <API_KEY>`，仅能访问 `/api/v1/external/*`。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`POST`](#post-api-v1-external-conversations) | `/api/v1/external/conversations` | Create External Conversation |
| [`GET`](#get-api-v1-external-conversations-conversation-id) | `/api/v1/external/conversations/{conversation_id}` | Get External Conversation |
| [`POST`](#post-api-v1-external-conversations-conversation-id-runs) | `/api/v1/external/conversations/{conversation_id}/runs` | Create External Run |
| [`GET`](#get-api-v1-external-runs-run-id) | `/api/v1/external/runs/{run_id}` | Get External Run |
| [`POST`](#post-api-v1-external-runs-run-id-cancel) | `/api/v1/external/runs/{run_id}/cancel` | Cancel External Run |
| [`GET`](#get-api-v1-external-skills) | `/api/v1/external/skills` | List External Skills |

## `POST /api/v1/external/conversations`

> Create External Conversation  
<a id="post-api-v1-external-conversations"></a>

**请求头**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `Idempotency-Key` | string | 否 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `source` | string | 否 | Source |
| `external_conversation_id` | string | 否 | External Conversation Id |
| `agent` | string | 否 | Agent |
| `default_skill` | string | 否 | Default Skill |
| `metadata` | object | 否 | Metadata |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`ExternalConversationResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/external/conversations'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/v1/external/conversations/{conversation_id}`

> Get External Conversation  
<a id="get-api-v1-external-conversations-conversation-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `conversation_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ExternalConversationResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/external/conversations/{conversation_id}'
```

---

## `POST /api/v1/external/conversations/{conversation_id}/runs`

> Create External Run  
<a id="post-api-v1-external-conversations-conversation-id-runs"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `conversation_id` | string | 是 |  |

**请求头**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `Idempotency-Key` | string | 否 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `message` | string | 是 | Message |
| `skill` | string | 否 | Skill |
| `mode` | string（枚举: standard, thinking, pro, ultra, flash） | 否 | Mode |
| `metadata` | object | 否 | Metadata |

**响应**

- **`202`** Successful Response
  - 响应体（`application/json`）：`ExternalRunResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/external/conversations/{conversation_id}/runs'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/v1/external/runs/{run_id}`

> Get External Run  
<a id="get-api-v1-external-runs-run-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ExternalRunResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/external/runs/{run_id}'
```

---

## `POST /api/v1/external/runs/{run_id}/cancel`

> Cancel External Run  
<a id="post-api-v1-external-runs-run-id-cancel"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ExternalRunResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/external/runs/{run_id}/cancel'
```

---

## `GET /api/v1/external/skills`

> List External Skills  
<a id="get-api-v1-external-skills"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ExternalSkillsResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/external/skills'
```

---

## 数据模型

### `ExternalConversationCreateRequest`

ExternalConversationCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `source` | string | 否 | Source |
| `external_conversation_id` | string | 否 | External Conversation Id |
| `agent` | string | 否 | Agent |
| `default_skill` | string | 否 | Default Skill |
| `metadata` | object | 否 | Metadata |

### `ExternalConversationResponse`

ExternalConversationResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `request_id` | string | 否 | Request Id |
| `conversation_id` | string | 是 | Conversation Id |
| `status` | string（枚举: active, closed） | 是 | Status |
| `agent` | string | 是 | Agent |
| `default_skill` | string | 否 | Default Skill |
| `source` | string | 否 | Source |
| `external_conversation_id` | string | 否 | External Conversation Id |
| `created_at` | string(date-time) | 是 | Created At |
| `updated_at` | string(date-time) | 是 | Updated At |

### `ExternalRunCreateRequest`

ExternalRunCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `message` | string | 是 | Message |
| `skill` | string | 否 | Skill |
| `mode` | string（枚举: standard, thinking, pro, ultra, flash） | 否 | Mode |
| `metadata` | object | 否 | Metadata |

### `ExternalRunResponse`

ExternalRunResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `request_id` | string | 否 | Request Id |
| `run_id` | string | 是 | Run Id |
| `conversation_id` | string | 是 | Conversation Id |
| `skill` | string | 否 | Skill |
| `status` | string（枚举: pending, running, completed, failed, cancelled） | 是 | Status |
| `answer` | string | 否 | Answer |
| `error` | string | 否 | Error |
| `created_at` | string(date-time) | 否 | Created At |
| `updated_at` | string(date-time) | 否 | Updated At |

### `ExternalSkillSummary`

ExternalSkillSummary

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `description` | string | 否 | Description |
| `display_name` | string | 否 | Display Name |
| `description_zh` | string | 否 | Description Zh |

### `ExternalSkillsResponse`

ExternalSkillsResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `request_id` | string | 否 | Request Id |
| `skills` | array<ExternalSkillSummary> | 是 | Skills |

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
