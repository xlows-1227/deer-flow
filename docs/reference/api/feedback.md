# 反馈 Feedback

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/threads/{thread_id}/runs/{run_id}/feedback`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-threads-thread-id-runs-run-id-feedback) | `/api/threads/{thread_id}/runs/{run_id}/feedback` | List Feedback |
| [`POST`](#post-api-threads-thread-id-runs-run-id-feedback) | `/api/threads/{thread_id}/runs/{run_id}/feedback` | Create Feedback |
| [`PUT`](#put-api-threads-thread-id-runs-run-id-feedback) | `/api/threads/{thread_id}/runs/{run_id}/feedback` | Upsert Feedback |
| [`DELETE`](#delete-api-threads-thread-id-runs-run-id-feedback) | `/api/threads/{thread_id}/runs/{run_id}/feedback` | Delete Run Feedback |
| [`GET`](#get-api-threads-thread-id-runs-run-id-feedback-stats) | `/api/threads/{thread_id}/runs/{run_id}/feedback/stats` | Feedback Stats |
| [`DELETE`](#delete-api-threads-thread-id-runs-run-id-feedback-feedback-id) | `/api/threads/{thread_id}/runs/{run_id}/feedback/{feedback_id}` | Delete Feedback |

## `GET /api/threads/{thread_id}/runs/{run_id}/feedback`

> List Feedback  
<a id="get-api-threads-thread-id-runs-run-id-feedback"></a>

List all feedback for a run.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<FeedbackResponse>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/feedback'
```

---

## `POST /api/threads/{thread_id}/runs/{run_id}/feedback`

> Create Feedback  
<a id="post-api-threads-thread-id-runs-run-id-feedback"></a>

Submit feedback (thumbs-up/down) for a run.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `rating` | integer | 是 | Feedback rating: +1 (positive) or -1 (negative) |
| `comment` | string | 否 | Optional text feedback |
| `message_id` | string | 否 | Optional: scope feedback to a specific message |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`FeedbackResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/feedback'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `PUT /api/threads/{thread_id}/runs/{run_id}/feedback`

> Upsert Feedback  
<a id="put-api-threads-thread-id-runs-run-id-feedback"></a>

Create or update feedback for a run (idempotent).

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `rating` | integer | 是 | Feedback rating: +1 (positive) or -1 (negative) |
| `comment` | string | 否 | Optional text feedback |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`FeedbackResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/feedback'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/threads/{thread_id}/runs/{run_id}/feedback`

> Delete Run Feedback  
<a id="delete-api-threads-thread-id-runs-run-id-feedback"></a>

Delete the current user's feedback for a run.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/feedback'
```

---

## `GET /api/threads/{thread_id}/runs/{run_id}/feedback/stats`

> Feedback Stats  
<a id="get-api-threads-thread-id-runs-run-id-feedback-stats"></a>

Get aggregated feedback stats (positive/negative counts) for a run.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`FeedbackStatsResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/feedback/stats'
```

---

## `DELETE /api/threads/{thread_id}/runs/{run_id}/feedback/{feedback_id}`

> Delete Feedback  
<a id="delete-api-threads-thread-id-runs-run-id-feedback-feedback-id"></a>

Delete a feedback record.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |
| `feedback_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/feedback/{feedback_id}'
```

---

## 数据模型

### `FeedbackCreateRequest`

FeedbackCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `rating` | integer | 是 | Feedback rating: +1 (positive) or -1 (negative) |
| `comment` | string | 否 | Optional text feedback |
| `message_id` | string | 否 | Optional: scope feedback to a specific message |

### `FeedbackResponse`

FeedbackResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `feedback_id` | string | 是 | Feedback Id |
| `run_id` | string | 是 | Run Id |
| `thread_id` | string | 是 | Thread Id |
| `user_id` | string | 否 | User Id |
| `message_id` | string | 否 | Message Id |
| `rating` | integer | 是 | Rating |
| `comment` | string | 否 | Comment |
| `created_at` | string | 否 | Created At |

### `FeedbackStatsResponse`

FeedbackStatsResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 | Run Id |
| `total` | integer | 否 | Total |
| `positive` | integer | 否 | Positive |
| `negative` | integer | 否 | Negative |

### `FeedbackUpsertRequest`

FeedbackUpsertRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `rating` | integer | 是 | Feedback rating: +1 (positive) or -1 (negative) |
| `comment` | string | 否 | Optional text feedback |

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
