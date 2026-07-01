# 模型 Models

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/models`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-models) | `/api/models` | List All Models |
| [`GET`](#get-api-models-model-name) | `/api/models/{model_name}` | Get Model Details |

## `GET /api/models`

> List All Models  
<a id="get-api-models"></a>

Retrieve a list of all available AI models configured in the system.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ModelsListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/models'
```

---

## `GET /api/models/{model_name}`

> Get Model Details  
<a id="get-api-models-model-name"></a>

Retrieve detailed information about a specific AI model by its name.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `model_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ModelResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/models/{model_name}'
```

---

## 数据模型

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `ModelResponse`

Response model for model information.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Unique identifier for the model |
| `model` | string | 是 | Actual provider model identifier |
| `display_name` | string | 否 | Human-readable name |
| `description` | string | 否 | Model description |
| `supports_thinking` | boolean | 否 | Whether model supports thinking mode |
| `supports_reasoning_effort` | boolean | 否 | Whether model supports reasoning effort |

### `ModelsListResponse`

Response model for listing all models.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `models` | array<ModelResponse> | 是 | Models |
| `token_usage` | TokenUsageResponse | 是 |  |

### `TokenUsageResponse`

Token usage display configuration.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Whether token usage display is enabled |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
