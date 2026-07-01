# 无状态运行 Stateless Runs

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/runs`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`POST`](#post-api-runs-stream) | `/api/runs/stream` | Stateless Stream |
| [`POST`](#post-api-runs-wait) | `/api/runs/wait` | Stateless Wait |
| [`GET`](#get-api-runs-run-id-feedback) | `/api/runs/{run_id}/feedback` | Run Feedback |
| [`GET`](#get-api-runs-run-id-messages) | `/api/runs/{run_id}/messages` | Run Messages |

## `POST /api/runs/stream`

> Stateless Stream  
<a id="post-api-runs-stream"></a>

Create a run and stream events via SSE.

If ``config.configurable.thread_id`` is provided, the run is created
on the given thread so that conversation history is preserved.
Otherwise a new temporary thread is created.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 否 | Agent / assistant to use |
| `input` | object | 否 | Graph input (e.g. {messages: [...]}) |
| `command` | object | 否 | LangGraph Command |
| `metadata` | object | 否 | Run metadata |
| `config` | object | 否 | RunnableConfig overrides |
| `context` | object | 否 | DeerFlow context overrides (model_name, thinking_enabled, etc.) |
| `webhook` | string | 否 | Completion callback URL |
| `checkpoint_id` | string | 否 | Resume from checkpoint |
| `checkpoint` | object | 否 | Full checkpoint object |
| `interrupt_before` | array<string> | string | 否 | Nodes to interrupt before |
| `interrupt_after` | array<string> | string | 否 | Nodes to interrupt after |
| `stream_mode` | array<string> | string | 否 | Stream mode(s) |
| `stream_subgraphs` | boolean | 否 | Include subgraph events |
| `stream_resumable` | boolean | 否 | SSE resumable mode |
| `on_disconnect` | string（枚举: cancel, continue） | 否 | Behaviour on SSE disconnect |
| `on_completion` | string（枚举: delete, keep） | 否 | Delete temp thread on completion |
| `multitask_strategy` | string（枚举: reject, rollback, interrupt, enqueue） | 否 | Concurrency strategy |
| `after_seconds` | number | 否 | Delayed execution |
| `if_not_exists` | string（枚举: reject, create） | 否 | Thread creation policy |
| `feedback_keys` | array<string> | 否 | LangSmith feedback keys |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/runs/stream'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/runs/wait`

> Stateless Wait  
<a id="post-api-runs-wait"></a>

Create a run and block until completion.

If ``config.configurable.thread_id`` is provided, the run is created
on the given thread so that conversation history is preserved.
Otherwise a new temporary thread is created.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 否 | Agent / assistant to use |
| `input` | object | 否 | Graph input (e.g. {messages: [...]}) |
| `command` | object | 否 | LangGraph Command |
| `metadata` | object | 否 | Run metadata |
| `config` | object | 否 | RunnableConfig overrides |
| `context` | object | 否 | DeerFlow context overrides (model_name, thinking_enabled, etc.) |
| `webhook` | string | 否 | Completion callback URL |
| `checkpoint_id` | string | 否 | Resume from checkpoint |
| `checkpoint` | object | 否 | Full checkpoint object |
| `interrupt_before` | array<string> | string | 否 | Nodes to interrupt before |
| `interrupt_after` | array<string> | string | 否 | Nodes to interrupt after |
| `stream_mode` | array<string> | string | 否 | Stream mode(s) |
| `stream_subgraphs` | boolean | 否 | Include subgraph events |
| `stream_resumable` | boolean | 否 | SSE resumable mode |
| `on_disconnect` | string（枚举: cancel, continue） | 否 | Behaviour on SSE disconnect |
| `on_completion` | string（枚举: delete, keep） | 否 | Delete temp thread on completion |
| `multitask_strategy` | string（枚举: reject, rollback, interrupt, enqueue） | 否 | Concurrency strategy |
| `after_seconds` | number | 否 | Delayed execution |
| `if_not_exists` | string（枚举: reject, create） | 否 | Thread creation policy |
| `feedback_keys` | array<string> | 否 | LangSmith feedback keys |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/runs/wait'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/runs/{run_id}/feedback`

> Run Feedback  
<a id="get-api-runs-run-id-feedback"></a>

Return all feedback for a run.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<object>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/runs/{run_id}/feedback'
```

---

## `GET /api/runs/{run_id}/messages`

> Run Messages  
<a id="get-api-runs-run-id-messages"></a>

Return paginated messages for a run (cursor-based).

Pagination:
- after_seq: messages with seq > after_seq (forward)
- before_seq: messages with seq < before_seq (backward)
- neither: latest messages

Response: { data: [...], has_more: bool }

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `limit` | integer | 否 |  |
| `before_seq` | integer | 否 |  |
| `after_seq` | integer | 否 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/runs/{run_id}/messages'
```

---

## 数据模型

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `RunCreateRequest`

RunCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `assistant_id` | string | 否 | Agent / assistant to use |
| `input` | object | 否 | Graph input (e.g. {messages: [...]}) |
| `command` | object | 否 | LangGraph Command |
| `metadata` | object | 否 | Run metadata |
| `config` | object | 否 | RunnableConfig overrides |
| `context` | object | 否 | DeerFlow context overrides (model_name, thinking_enabled, etc.) |
| `webhook` | string | 否 | Completion callback URL |
| `checkpoint_id` | string | 否 | Resume from checkpoint |
| `checkpoint` | object | 否 | Full checkpoint object |
| `interrupt_before` | array<string> | string | 否 | Nodes to interrupt before |
| `interrupt_after` | array<string> | string | 否 | Nodes to interrupt after |
| `stream_mode` | array<string> | string | 否 | Stream mode(s) |
| `stream_subgraphs` | boolean | 否 | Include subgraph events |
| `stream_resumable` | boolean | 否 | SSE resumable mode |
| `on_disconnect` | string（枚举: cancel, continue） | 否 | Behaviour on SSE disconnect |
| `on_completion` | string（枚举: delete, keep） | 否 | Delete temp thread on completion |
| `multitask_strategy` | string（枚举: reject, rollback, interrupt, enqueue） | 否 | Concurrency strategy |
| `after_seconds` | number | 否 | Delayed execution |
| `if_not_exists` | string（枚举: reject, create） | 否 | Thread creation policy |
| `feedback_keys` | array<string> | 否 | LangSmith feedback keys |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
