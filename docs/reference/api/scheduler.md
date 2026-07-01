# 定时任务 Scheduler

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/scheduler`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-scheduler-tasks) | `/api/scheduler/tasks` | List Tasks |
| [`POST`](#post-api-scheduler-tasks) | `/api/scheduler/tasks` | Create Task |
| [`GET`](#get-api-scheduler-tasks-task-id) | `/api/scheduler/tasks/{task_id}` | Get Task |
| [`PUT`](#put-api-scheduler-tasks-task-id) | `/api/scheduler/tasks/{task_id}` | Update Task |
| [`DELETE`](#delete-api-scheduler-tasks-task-id) | `/api/scheduler/tasks/{task_id}` | Delete Task |
| [`POST`](#post-api-scheduler-tasks-task-id-cancel) | `/api/scheduler/tasks/{task_id}/cancel` | Cancel Task |
| [`GET`](#get-api-scheduler-tasks-task-id-history) | `/api/scheduler/tasks/{task_id}/history` | Task History |
| [`POST`](#post-api-scheduler-tasks-task-id-run) | `/api/scheduler/tasks/{task_id}/run` | Run Task |
| [`PATCH`](#patch-api-scheduler-tasks-task-id-toggle) | `/api/scheduler/tasks/{task_id}/toggle` | Toggle Task |

## `GET /api/scheduler/tasks`

> List Tasks  
<a id="get-api-scheduler-tasks"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/scheduler/tasks'
```

---

## `POST /api/scheduler/tasks`

> Create Task  
<a id="post-api-scheduler-tasks"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `prompt` | string | 是 | Prompt |
| `repeat_type` | string（枚举: once, daily, weekly） | 是 | Repeat Type |
| `execution_time` | string | 是 | HH:MM in local time |
| `timezone` | string | 否 | Timezone |
| `day_of_week` | integer | 否 | Day Of Week |
| `is_enabled` | boolean | 否 | Is Enabled |
| `model_name` | string | 否 | Model Name |
| `mode` | string（枚举: flash, thinking, pro, ultra） | 否 | Mode |
| `reasoning_effort` | string（枚举: minimal, low, medium, high） | 否 | Reasoning Effort |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/scheduler/tasks'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/scheduler/tasks/{task_id}`

> Get Task  
<a id="get-api-scheduler-tasks-task-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}'
```

---

## `PUT /api/scheduler/tasks/{task_id}`

> Update Task  
<a id="put-api-scheduler-tasks-task-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `prompt` | string | 否 | Prompt |
| `repeat_type` | string（枚举: once, daily, weekly） | 否 | Repeat Type |
| `execution_time` | string | 否 | Execution Time |
| `timezone` | string | 否 | Timezone |
| `day_of_week` | integer | 否 | Day Of Week |
| `is_enabled` | boolean | 否 | Is Enabled |
| `model_name` | string | 否 | Model Name |
| `mode` | string（枚举: flash, thinking, pro, ultra） | 否 | Mode |
| `reasoning_effort` | string（枚举: minimal, low, medium, high） | 否 | Reasoning Effort |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/scheduler/tasks/{task_id}`

> Delete Task  
<a id="delete-api-scheduler-tasks-task-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**响应**

- **`204`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}'
```

---

## `POST /api/scheduler/tasks/{task_id}/cancel`

> Cancel Task  
<a id="post-api-scheduler-tasks-task-id-cancel"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskCancelResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}/cancel'
```

---

## `GET /api/scheduler/tasks/{task_id}/history`

> Task History  
<a id="get-api-scheduler-tasks-task-id-history"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskHistoryResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}/history'
```

---

## `POST /api/scheduler/tasks/{task_id}/run`

> Run Task  
<a id="post-api-scheduler-tasks-task-id-run"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskRunResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}/run'
```

---

## `PATCH /api/scheduler/tasks/{task_id}/toggle`

> Toggle Task  
<a id="patch-api-scheduler-tasks-task-id-toggle"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ScheduledTaskResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PATCH '${DEERFLOW_BASE_URL}/api/scheduler/tasks/{task_id}/toggle'
```

---

## 数据模型

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `ScheduledRunSummary`

ScheduledRunSummary

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `run_id` | string | 是 | Run Id |
| `thread_id` | string | 否 | Thread Id |
| `status` | string | 是 | Status |
| `created_at` | string | 否 | Created At |
| `updated_at` | string | 否 | Updated At |
| `error` | string | 否 | Error |

### `ScheduledTaskCancelResponse`

ScheduledTaskCancelResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `ok` | boolean | 是 | Ok |
| `reason` | string | 是 | Reason |
| `message` | string | 是 | Message |

### `ScheduledTaskCreate`

ScheduledTaskCreate

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `prompt` | string | 是 | Prompt |
| `repeat_type` | string（枚举: once, daily, weekly） | 是 | Repeat Type |
| `execution_time` | string | 是 | HH:MM in local time |
| `timezone` | string | 否 | Timezone |
| `day_of_week` | integer | 否 | Day Of Week |
| `is_enabled` | boolean | 否 | Is Enabled |
| `model_name` | string | 否 | Model Name |
| `mode` | string（枚举: flash, thinking, pro, ultra） | 否 | Mode |
| `reasoning_effort` | string（枚举: minimal, low, medium, high） | 否 | Reasoning Effort |

### `ScheduledTaskHistoryResponse`

ScheduledTaskHistoryResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task` | ScheduledTaskResponse | 是 |  |
| `runs` | array<ScheduledRunSummary> | 否 | Runs |

### `ScheduledTaskListResponse`

ScheduledTaskListResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `tasks` | array<ScheduledTaskResponse> | 是 | Tasks |
| `total` | integer | 是 | Total |

### `ScheduledTaskResponse`

ScheduledTaskResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `prompt` | string | 是 | Prompt |
| `repeat_type` | string（枚举: once, daily, weekly） | 是 | Repeat Type |
| `execution_time` | string | 是 | HH:MM in local time |
| `timezone` | string | 否 | Timezone |
| `day_of_week` | integer | 否 | Day Of Week |
| `is_enabled` | boolean | 否 | Is Enabled |
| `model_name` | string | 否 | Model Name |
| `mode` | string（枚举: flash, thinking, pro, ultra） | 否 | Mode |
| `reasoning_effort` | string（枚举: minimal, low, medium, high） | 否 | Reasoning Effort |
| `id` | string | 是 | Id |
| `last_run_at` | string(date-time) | 否 | Last Run At |
| `last_run_status` | string | 否 | Last Run Status |
| `last_run_thread_id` | string | 否 | Last Run Thread Id |
| `last_run_id` | string | 否 | Last Run Id |
| `next_run_at` | string(date-time) | 否 | Next Run At |
| `created_at` | string(date-time) | 是 | Created At |
| `updated_at` | string(date-time) | 是 | Updated At |

### `ScheduledTaskRunResponse`

ScheduledTaskRunResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 | Thread Id |
| `run_id` | string | 是 | Run Id |

### `ScheduledTaskUpdate`

ScheduledTaskUpdate

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `prompt` | string | 否 | Prompt |
| `repeat_type` | string（枚举: once, daily, weekly） | 否 | Repeat Type |
| `execution_time` | string | 否 | Execution Time |
| `timezone` | string | 否 | Timezone |
| `day_of_week` | integer | 否 | Day Of Week |
| `is_enabled` | boolean | 否 | Is Enabled |
| `model_name` | string | 否 | Model Name |
| `mode` | string（枚举: flash, thinking, pro, ultra） | 否 | Mode |
| `reasoning_effort` | string（枚举: minimal, low, medium, high） | 否 | Reasoning Effort |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
