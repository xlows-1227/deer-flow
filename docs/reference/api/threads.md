# 会话 Threads

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/threads`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`POST`](#post-api-threads) | `/api/threads` | Create Thread |
| [`POST`](#post-api-threads-search) | `/api/threads/search` | Search Threads |
| [`GET`](#get-api-threads-thread-id) | `/api/threads/{thread_id}` | Get Thread |
| [`PATCH`](#patch-api-threads-thread-id) | `/api/threads/{thread_id}` | Patch Thread |
| [`DELETE`](#delete-api-threads-thread-id) | `/api/threads/{thread_id}` | Delete Thread Data |
| [`POST`](#post-api-threads-thread-id-history) | `/api/threads/{thread_id}/history` | Get Thread History |
| [`POST`](#post-api-threads-thread-id-memory-rollup) | `/api/threads/{thread_id}/memory/rollup` | Rollup Thread Memory |
| [`GET`](#get-api-threads-thread-id-sandbox-files) | `/api/threads/{thread_id}/sandbox/files` | List Thread Sandbox Files |
| [`GET`](#get-api-threads-thread-id-state) | `/api/threads/{thread_id}/state` | Get Thread State |
| [`POST`](#post-api-threads-thread-id-state) | `/api/threads/{thread_id}/state` | Update Thread State |

## `POST /api/threads`

> Create Thread  
<a id="post-api-threads"></a>

Create a new thread.

Writes a thread_meta record (so the thread appears in /threads/search)
and an empty checkpoint (so state endpoints work immediately).
Idempotent: returns the existing record when ``thread_id`` already exists.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 否 | Optional thread ID (auto-generated if omitted) |
| `assistant_id` | string | 否 | Associate thread with an assistant |
| `metadata` | object | 否 | Initial metadata |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/threads/search`

> Search Threads  
<a id="post-api-threads-search"></a>

Search and list threads.

Delegates to the configured ThreadMetaStore implementation
(SQL-backed for sqlite/postgres, Store-backed for memory mode).

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `metadata` | object | 否 | Metadata filter (exact match) |
| `limit` | integer | 否 | Maximum results |
| `offset` | integer | 否 | Pagination offset |
| `status` | string | 否 | Filter by thread status |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<ThreadResponse>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/search'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/threads/{thread_id}`

> Get Thread  
<a id="get-api-threads-thread-id"></a>

Get thread info.

Reads metadata from the ThreadMetaStore and derives the accurate
execution status from the checkpointer.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}'
```

---

## `PATCH /api/threads/{thread_id}`

> Patch Thread  
<a id="patch-api-threads-thread-id"></a>

Merge metadata into a thread record.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `metadata` | object | 否 | Metadata to merge |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PATCH '${DEERFLOW_BASE_URL}/api/threads/{thread_id}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/threads/{thread_id}`

> Delete Thread Data  
<a id="delete-api-threads-thread-id"></a>

Delete local persisted filesystem data for a thread.

Cleans DeerFlow-managed thread directories, removes checkpoint data,
and removes the thread_meta row from the configured ThreadMetaStore
(sqlite or memory).

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadDeleteResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/threads/{thread_id}'
```

---

## `POST /api/threads/{thread_id}/history`

> Get Thread History  
<a id="post-api-threads-thread-id-history"></a>

Get checkpoint history for a thread.

Messages are read from the checkpointer's channel values (the
authoritative source) and serialized via
:func:`~deerflow.runtime.serialization.serialize_channel_values`.
Only the latest (first) checkpoint carries the ``messages`` key to
avoid duplicating them across every entry.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `limit` | integer | 否 | Maximum entries |
| `before` | string | 否 | Cursor for pagination |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<HistoryEntry>`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/history'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/threads/{thread_id}/memory/rollup`

> Rollup Thread Memory  
<a id="post-api-threads-thread-id-memory-rollup"></a>

Summarize only this thread and incrementally merge it into user memory.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`DailyPersonSummary`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/memory/rollup'
```

---

## `GET /api/threads/{thread_id}/sandbox/files`

> List Thread Sandbox Files  
<a id="get-api-threads-thread-id-sandbox-files"></a>

List files in the thread sandbox's user-data directory.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SandboxFilesResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/sandbox/files'
```

---

## `GET /api/threads/{thread_id}/state`

> Get Thread State  
<a id="get-api-threads-thread-id-state"></a>

Get the latest state snapshot for a thread.

Channel values are serialized to ensure LangChain message objects
are converted to JSON-safe dicts.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadStateResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/state'
```

---

## `POST /api/threads/{thread_id}/state`

> Update Thread State  
<a id="post-api-threads-thread-id-state"></a>

Update thread state (e.g. for human-in-the-loop resume or title rename).

Writes a new checkpoint that merges *body.values* into the latest
channel values, then syncs any updated ``title`` field through the
ThreadMetaStore abstraction so that ``/threads/search`` reflects the
change immediately in both sqlite and memory backends.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `values` | object | 否 | Channel values to merge |
| `checkpoint_id` | string | 否 | Checkpoint to branch from |
| `checkpoint` | object | 否 | Full checkpoint object |
| `as_node` | string | 否 | Node identity for the update |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ThreadStateResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/state'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `DailyPersonSummary`

Reviewable per-user, per-day memory evidence.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `version` | string | 否 | Version |
| `id` | string | 是 | Id |
| `personId` | string | 是 | Personid |
| `date` | string | 是 | Date |
| `timezone` | string | 否 | Timezone |
| `summary` | string | 否 | Summary |
| `interests` | array<string> | 否 | Interests |
| `preferences` | array<string> | 否 | Preferences |
| `profileSignals` | array<string> | 否 | Profilesignals |
| `recentFocus` | array<string> | 否 | Recentfocus |
| `skillUsagePatterns` | array<string> | 否 | Skillusagepatterns |
| `corrections` | array<string> | 否 | Corrections |
| `sourceThreads` | array<string> | 否 | Sourcethreads |
| `sourceRuns` | array<string> | 否 | Sourceruns |
| `status` | string（枚举: active, deleted） | 否 | Status |
| `deletedAt` | string | 否 | Deletedat |
| `updatedAt` | string | 否 | Updatedat |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `HistoryEntry`

Single checkpoint history entry.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `checkpoint_id` | string | 是 | Checkpoint Id |
| `parent_checkpoint_id` | string | 否 | Parent Checkpoint Id |
| `metadata` | object | 否 | Metadata |
| `values` | object | 否 | Values |
| `created_at` | string | 否 | Created At |
| `next` | array<string> | 否 | Next |

### `SandboxFileInfo`

Single file inside a thread's sandbox user-data directory.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | Sandbox virtual path, e.g. /mnt/user-data/outputs/report.xlsx |
| `name` | string | 是 | File name |
| `size` | integer | 是 | File size in bytes |
| `modified_at` | number | 是 | Unix timestamp when the file was last modified |
| `source` | string | 是 | Top-level sandbox bucket: workspace, uploads, outputs, or user-data |
| `extension` | string | 否 | Lowercase file extension without dot |
| `mime_type` | string | 否 | Detected MIME type |

### `SandboxFilesResponse`

Response model for sandbox file listing.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<SandboxFileInfo> | 是 | Files |
| `count` | integer | 是 | Count |
| `truncated` | boolean | 否 | Truncated |

### `ThreadCreateRequest`

Request body for creating a thread.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 否 | Optional thread ID (auto-generated if omitted) |
| `assistant_id` | string | 否 | Associate thread with an assistant |
| `metadata` | object | 否 | Initial metadata |

### `ThreadDeleteResponse`

Response model for thread cleanup.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `success` | boolean | 是 | Success |
| `message` | string | 是 | Message |

### `ThreadHistoryRequest`

Request body for checkpoint history.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `limit` | integer | 否 | Maximum entries |
| `before` | string | 否 | Cursor for pagination |

### `ThreadPatchRequest`

Request body for patching thread metadata.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `metadata` | object | 否 | Metadata to merge |

### `ThreadResponse`

Response model for a single thread.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 | Unique thread identifier |
| `status` | string | 否 | Thread status: idle, busy, interrupted, error |
| `created_at` | string | 否 | ISO timestamp |
| `updated_at` | string | 否 | ISO timestamp |
| `metadata` | object | 否 | Thread metadata |
| `values` | object | 否 | Current state channel values |
| `interrupts` | object | 否 | Pending interrupts |

### `ThreadSearchRequest`

Request body for searching threads.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `metadata` | object | 否 | Metadata filter (exact match) |
| `limit` | integer | 否 | Maximum results |
| `offset` | integer | 否 | Pagination offset |
| `status` | string | 否 | Filter by thread status |

### `ThreadStateResponse`

Response model for thread state.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `values` | object | 否 | Current channel values |
| `next` | array<string> | 否 | Next tasks to execute |
| `metadata` | object | 否 | Checkpoint metadata |
| `checkpoint` | object | 否 | Checkpoint info |
| `checkpoint_id` | string | 否 | Current checkpoint ID |
| `parent_checkpoint_id` | string | 否 | Parent checkpoint ID |
| `created_at` | string | 否 | Checkpoint timestamp |
| `tasks` | array<object> | 否 | Interrupted task details |

### `ThreadStateUpdateRequest`

Request body for updating thread state (human-in-the-loop resume).

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `values` | object | 否 | Channel values to merge |
| `checkpoint_id` | string | 否 | Checkpoint to branch from |
| `checkpoint` | object | 否 | Full checkpoint object |
| `as_node` | string | 否 | Node identity for the update |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
