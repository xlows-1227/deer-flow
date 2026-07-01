# 会话内运行 Thread Runs

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/threads/{thread_id}/runs`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-threads-thread-id-messages) | `/api/threads/{thread_id}/messages` | List Thread Messages |
| [`GET`](#get-api-threads-thread-id-runs) | `/api/threads/{thread_id}/runs` | List Runs |
| [`POST`](#post-api-threads-thread-id-runs) | `/api/threads/{thread_id}/runs` | Create Run |
| [`POST`](#post-api-threads-thread-id-runs-stream) | `/api/threads/{thread_id}/runs/stream` | Stream Run |
| [`POST`](#post-api-threads-thread-id-runs-wait) | `/api/threads/{thread_id}/runs/wait` | Wait Run |
| [`GET`](#get-api-threads-thread-id-runs-run-id) | `/api/threads/{thread_id}/runs/{run_id}` | Get Run |
| [`POST`](#post-api-threads-thread-id-runs-run-id-cancel) | `/api/threads/{thread_id}/runs/{run_id}/cancel` | Cancel Run |
| [`GET`](#get-api-threads-thread-id-runs-run-id-events) | `/api/threads/{thread_id}/runs/{run_id}/events` | List Run Events |
| [`GET`](#get-api-threads-thread-id-runs-run-id-join) | `/api/threads/{thread_id}/runs/{run_id}/join` | Join Run |
| [`GET`](#get-api-threads-thread-id-runs-run-id-messages) | `/api/threads/{thread_id}/runs/{run_id}/messages` | List Run Messages |
| [`GET`](#get-api-threads-thread-id-runs-run-id-stream) | `/api/threads/{thread_id}/runs/{run_id}/stream` | Stream Existing Run |
| [`POST`](#post-api-threads-thread-id-runs-run-id-stream) | `/api/threads/{thread_id}/runs/{run_id}/stream` | Stream Existing Run |
| [`GET`](#get-api-threads-thread-id-token-usage) | `/api/threads/{thread_id}/token-usage` | Thread Token Usage |

## `GET /api/threads/{thread_id}/messages`

> List Thread Messages  
<a id="get-api-threads-thread-id-messages"></a>

Return displayable messages for a thread (across all runs), with feedback attached.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `limit` | integer | 否 |  |
| `before_seq` | integer | 否 |  |
| `after_seq` | integer | 否 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<object>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/messages'
```

---

## `GET /api/threads/{thread_id}/runs`

> List Runs  
<a id="get-api-threads-thread-id-runs"></a>

List all runs for a thread.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `limit` | integer | 否 |  |
| `offset` | integer | 否 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<RunResponse>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs'
```

---

## `POST /api/threads/{thread_id}/runs`

> Create Run  
<a id="post-api-threads-thread-id-runs"></a>

Create a background run (returns immediately).

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

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
  - 响应体（`application/json`）：`RunResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/threads/{thread_id}/runs/stream`

> Stream Run  
<a id="post-api-threads-thread-id-runs-stream"></a>

Create a run and stream events via SSE.

The response includes a ``Content-Location`` header with the run's
resource URL, matching the LangGraph Platform protocol.  The
``useStream`` React hook uses this to extract run metadata.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

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
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/stream'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/threads/{thread_id}/runs/wait`

> Wait Run  
<a id="post-api-threads-thread-id-runs-wait"></a>

Create a run and block until it completes, returning the final state.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

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
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/wait'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/threads/{thread_id}/runs/{run_id}`

> Get Run  
<a id="get-api-threads-thread-id-runs-run-id"></a>

Get details of a specific run.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`RunResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}'
```

---

## `POST /api/threads/{thread_id}/runs/{run_id}/cancel`

> Cancel Run  
<a id="post-api-threads-thread-id-runs-run-id-cancel"></a>

Cancel a running or pending run.

- action=interrupt: Stop execution, keep current checkpoint (can be resumed)
- action=rollback: Stop execution, revert to pre-run checkpoint state
- wait=true: Block until the run fully stops, return 204
- wait=false: Return immediately with 202

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `wait` | boolean | 否 | Block until run completes after cancel |
| `action` | string（枚举: interrupt, rollback） | 否 | Cancel action |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/cancel'
```

---

## `GET /api/threads/{thread_id}/runs/{run_id}/events`

> List Run Events  
<a id="get-api-threads-thread-id-runs-run-id-events"></a>

Return the full event stream for a run (debug/audit).

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `event_types` | string | 否 |  |
| `limit` | integer | 否 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<object>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/events'
```

---

## `GET /api/threads/{thread_id}/runs/{run_id}/join`

> Join Run  
<a id="get-api-threads-thread-id-runs-run-id-join"></a>

Join an existing run's SSE stream.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/join'
```

---

## `GET /api/threads/{thread_id}/runs/{run_id}/messages`

> List Run Messages  
<a id="get-api-threads-thread-id-runs-run-id-messages"></a>

Return paginated messages for a specific run.

Response: { data: [...], has_more: bool }

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
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
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/messages'
```

---

## `GET /api/threads/{thread_id}/runs/{run_id}/stream`

> Stream Existing Run  
<a id="get-api-threads-thread-id-runs-run-id-stream"></a>

Join an existing run's SSE stream (GET), or cancel-then-stream (POST).

The LangGraph SDK's ``joinStream`` and ``useStream`` stop button both use
``POST`` to this endpoint.  When ``action=interrupt`` or ``action=rollback``
is present the run is cancelled first; the response then streams any
remaining buffered events so the client observes a clean shutdown.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `action` | string（枚举: interrupt, rollback） | 否 | Cancel action |
| `wait` | integer | 否 | Block until cancelled (1) or return immediately (0) |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/stream'
```

---

## `POST /api/threads/{thread_id}/runs/{run_id}/stream`

> Stream Existing Run  
<a id="post-api-threads-thread-id-runs-run-id-stream"></a>

Join an existing run's SSE stream (GET), or cancel-then-stream (POST).

The LangGraph SDK's ``joinStream`` and ``useStream`` stop button both use
``POST`` to this endpoint.  When ``action=interrupt`` or ``action=rollback``
is present the run is cancelled first; the response then streams any
remaining buffered events so the client observes a clean shutdown.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `run_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `action` | string（枚举: interrupt, rollback） | 否 | Cancel action |
| `wait` | integer | 否 | Block until cancelled (1) or return immediately (0) |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/runs/{run_id}/stream'
```

---

## `GET /api/threads/{thread_id}/token-usage`

> Thread Token Usage  
<a id="get-api-threads-thread-id-token-usage"></a>

Thread-level token usage aggregation.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `include_active` | boolean | 否 | Include running run progress snapshots |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadTokenUsageResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/token-usage'
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

### `RunResponse`

RunResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 | Run Id |
| `thread_id` | string | 是 | Thread Id |
| `assistant_id` | string | 否 | Assistant Id |
| `status` | string | 是 | Status |
| `metadata` | object | 否 | Metadata |
| `kwargs` | object | 否 | Kwargs |
| `multitask_strategy` | string | 否 | Multitask Strategy |
| `created_at` | string | 否 | Created At |
| `updated_at` | string | 否 | Updated At |
| `total_input_tokens` | integer | 否 | Total Input Tokens |
| `total_output_tokens` | integer | 否 | Total Output Tokens |
| `total_tokens` | integer | 否 | Total Tokens |
| `llm_call_count` | integer | 否 | Llm Call Count |
| `lead_agent_tokens` | integer | 否 | Lead Agent Tokens |
| `subagent_tokens` | integer | 否 | Subagent Tokens |
| `middleware_tokens` | integer | 否 | Middleware Tokens |
| `message_count` | integer | 否 | Message Count |

### `ThreadTokenUsageCallerBreakdown`

ThreadTokenUsageCallerBreakdown

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `lead_agent` | integer | 否 | Lead Agent |
| `subagent` | integer | 否 | Subagent |
| `middleware` | integer | 否 | Middleware |

### `ThreadTokenUsageModelBreakdown`

ThreadTokenUsageModelBreakdown

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `tokens` | integer | 否 | Tokens |
| `runs` | integer | 否 | Runs |

### `ThreadTokenUsageResponse`

ThreadTokenUsageResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 | Thread Id |
| `total_tokens` | integer | 否 | Total Tokens |
| `total_input_tokens` | integer | 否 | Total Input Tokens |
| `total_output_tokens` | integer | 否 | Total Output Tokens |
| `total_runs` | integer | 否 | Total Runs |
| `by_model` | object | 否 | By Model |
| `by_caller` | ThreadTokenUsageCallerBreakdown | 否 |  |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
