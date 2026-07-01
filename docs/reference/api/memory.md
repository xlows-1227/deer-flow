# 记忆 Memory

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/memory`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-memory) | `/api/memory` | Get Memory Data |
| [`DELETE`](#delete-api-memory) | `/api/memory` | Clear All Memory Data |
| [`GET`](#get-api-memory-config) | `/api/memory/config` | Get Memory Configuration |
| [`POST`](#post-api-memory-consolidate) | `/api/memory/consolidate` | Consolidate Memory Profile |
| [`GET`](#get-api-memory-daily) | `/api/memory/daily` | Get Daily Memory Summaries |
| [`POST`](#post-api-memory-daily-rollup) | `/api/memory/daily/rollup` | Roll Up Daily Memory |
| [`DELETE`](#delete-api-memory-daily-date) | `/api/memory/daily/{date}` | Delete Daily Memory |
| [`DELETE`](#delete-api-memory-daily-date-purge) | `/api/memory/daily/{date}/purge` | Purge Daily Memory |
| [`POST`](#post-api-memory-daily-date-restore) | `/api/memory/daily/{date}/restore` | Restore Daily Memory |
| [`GET`](#get-api-memory-export) | `/api/memory/export` | Export Memory Data |
| [`POST`](#post-api-memory-facts) | `/api/memory/facts` | Create Memory Fact |
| [`PATCH`](#patch-api-memory-facts-fact-id) | `/api/memory/facts/{fact_id}` | Patch Memory Fact |
| [`DELETE`](#delete-api-memory-facts-fact-id) | `/api/memory/facts/{fact_id}` | Delete Memory Fact |
| [`POST`](#post-api-memory-import) | `/api/memory/import` | Import Memory Data |
| [`POST`](#post-api-memory-migrate-legacy) | `/api/memory/migrate-legacy` | Migrate Legacy Memory |
| [`GET`](#get-api-memory-profile) | `/api/memory/profile` | Get Memory Profile |
| [`POST`](#post-api-memory-reload) | `/api/memory/reload` | Reload Memory Data |
| [`GET`](#get-api-memory-status) | `/api/memory/status` | Get Memory Status |

## `GET /api/memory`

> Get Memory Data  
<a id="get-api-memory"></a>

Retrieve the current global memory data including user context, history, and facts.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/memory'
```

---

## `DELETE /api/memory`

> Clear All Memory Data  
<a id="delete-api-memory"></a>

Delete all saved memory data and reset the memory structure to an empty state.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/memory'
```

---

## `GET /api/memory/config`

> Get Memory Configuration  
<a id="get-api-memory-config"></a>

Retrieve the current memory system configuration.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryConfigResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/memory/config'
```

---

## `POST /api/memory/consolidate`

> Consolidate Memory Profile  
<a id="post-api-memory-consolidate"></a>

Rebuild the v2 profile from active daily summaries.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryProfile`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/consolidate'
```

---

## `GET /api/memory/daily`

> Get Daily Memory Summaries  
<a id="get-api-memory-daily"></a>

Retrieve one daily summary by date, or recent daily summaries by limit.

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `date` | string | 否 |  |
| `limit` | integer | 否 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`array<DailyPersonSummary> | DailyPersonSummary`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/memory/daily'
```

---

## `POST /api/memory/daily/rollup`

> Roll Up Daily Memory  
<a id="post-api-memory-daily-rollup"></a>

Manually roll up memory for a date or a specific thread.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `date` | string | 否 | Optional YYYY-MM-DD date |
| `threadId` | string | 否 | Optional thread id for per-conversation rollup |
| `force` | boolean | 否 | Reserved for future forced regeneration |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`DailyPersonSummary`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/daily/rollup'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/memory/daily/{date}`

> Delete Daily Memory  
<a id="delete-api-memory-daily-date"></a>

Soft-delete a daily summary and rebuild the profile.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `date` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`DailyPersonSummary`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/memory/daily/{date}'
```

---

## `DELETE /api/memory/daily/{date}/purge`

> Purge Daily Memory  
<a id="delete-api-memory-daily-date-purge"></a>

Permanently delete a daily memory summary.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `date` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/memory/daily/{date}/purge'
```

---

## `POST /api/memory/daily/{date}/restore`

> Restore Daily Memory  
<a id="post-api-memory-daily-date-restore"></a>

Restore a soft-deleted daily memory summary.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `date` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`DailyPersonSummary`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/daily/{date}/restore'
```

---

## `GET /api/memory/export`

> Export Memory Data  
<a id="get-api-memory-export"></a>

Export the current global memory data as JSON for backup or transfer.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/memory/export'
```

---

## `POST /api/memory/facts`

> Create Memory Fact  
<a id="post-api-memory-facts"></a>

Create a single saved memory fact manually.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 是 | Fact content |
| `category` | string | 否 | Fact category |
| `confidence` | number | 否 | Confidence score (0-1) |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/facts'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `PATCH /api/memory/facts/{fact_id}`

> Patch Memory Fact  
<a id="patch-api-memory-facts-fact-id"></a>

Partially update a single saved memory fact by its fact id while preserving omitted fields.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `fact_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 否 | Fact content |
| `category` | string | 否 | Fact category |
| `confidence` | number | 否 | Confidence score (0-1) |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PATCH '${DEERFLOW_BASE_URL}/api/memory/facts/{fact_id}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/memory/facts/{fact_id}`

> Delete Memory Fact  
<a id="delete-api-memory-facts-fact-id"></a>

Delete a single saved memory fact by its fact id.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `fact_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/memory/facts/{fact_id}'
```

---

## `POST /api/memory/import`

> Import Memory Data  
<a id="post-api-memory-import"></a>

Import and overwrite the current global memory data from a JSON payload.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `version` | string | 否 | Memory schema version |
| `lastUpdated` | string | 否 | Last update timestamp |
| `user` | UserContext | 否 |  |
| `history` | HistoryContext | 否 |  |
| `facts` | array<Fact> | 否 | Facts |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/import'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/memory/migrate-legacy`

> Migrate Legacy Memory  
<a id="post-api-memory-migrate-legacy"></a>

Back up and migrate legacy memory.json into v2 profile.json.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryProfile`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/migrate-legacy'
```

---

## `GET /api/memory/profile`

> Get Memory Profile  
<a id="get-api-memory-profile"></a>

Retrieve the v2 long-term memory profile for the current user.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryProfile`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/memory/profile'
```

---

## `POST /api/memory/reload`

> Reload Memory Data  
<a id="post-api-memory-reload"></a>

Reload memory data from the storage file, refreshing the in-memory cache.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/memory/reload'
```

---

## `GET /api/memory/status`

> Get Memory Status  
<a id="get-api-memory-status"></a>

Retrieve both memory configuration and current data in a single request.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MemoryStatusResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/memory/status'
```

---

## 数据模型

### `ContextSection`

Model for context sections (user and history).

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `summary` | string | 否 | Summary content |
| `updatedAt` | string | 否 | Last update timestamp |

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

### `DailyRollupRequest`

Request model for manual daily rollup.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `date` | string | 否 | Optional YYYY-MM-DD date |
| `threadId` | string | 否 | Optional thread id for per-conversation rollup |
| `force` | boolean | 否 | Reserved for future forced regeneration |

### `Fact`

Model for a memory fact.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `id` | string | 是 | Unique identifier for the fact |
| `content` | string | 是 | Fact content |
| `category` | string | 否 | Fact category |
| `confidence` | number | 否 | Confidence score (0-1) |
| `createdAt` | string | 否 | Creation timestamp |
| `source` | string | 否 | Source thread ID |
| `sourceError` | string | 否 | Optional description of the prior mistake or wrong approach |

### `FactCreateRequest`

Request model for creating a memory fact.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 是 | Fact content |
| `category` | string | 否 | Fact category |
| `confidence` | number | 否 | Confidence score (0-1) |

### `FactPatchRequest`

PATCH request model that preserves existing values for omitted fields.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 否 | Fact content |
| `category` | string | 否 | Fact category |
| `confidence` | number | 否 | Confidence score (0-1) |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `HistoryContext`

Model for history context.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `recentMonths` | ContextSection | 否 |  |
| `earlierContext` | ContextSection | 否 |  |
| `longTermBackground` | ContextSection | 否 |  |

### `MemoryConfigResponse`

Response model for memory configuration.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 是 | Whether memory is enabled |
| `storage_path` | string | 是 | Path to memory storage file |
| `debounce_seconds` | integer | 是 | Debounce time for memory updates |
| `max_facts` | integer | 是 | Maximum number of facts to store |
| `fact_confidence_threshold` | number | 是 | Minimum confidence threshold for facts |
| `injection_enabled` | boolean | 是 | Whether memory injection is enabled |
| `max_injection_tokens` | integer | 是 | Maximum tokens for memory injection |
| `v2_enabled` | boolean | 否 | Whether v2 daily-person memory is enabled |
| `daily_rollup_enabled` | boolean | 否 | Whether daily rollup is enabled |
| `daily_rollup_time` | string | 否 | Daily rollup time |
| `retention_days` | integer | 否 | Daily summary retention in days |
| `relevance_strategy` | string | 否 | Memory relevance strategy |
| `max_daily_snippets` | integer | 否 | Maximum daily snippets injected |
| `max_daily_snippet_tokens` | integer | 否 | Daily snippet token budget |

### `MemoryProfile`

Prompt-facing durable memory profile.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `version` | string | 否 | Version |
| `personId` | string | 是 | Personid |
| `updatedAt` | string | 否 | Updatedat |
| `overview` | string | 否 | Overview |
| `interests` | array<MemoryProfileItem> | 否 | Interests |
| `preferences` | array<MemoryProfileItem> | 否 | Preferences |
| `communicationStyle` | array<MemoryProfileItem> | 否 | Communicationstyle |
| `skillUsagePatterns` | array<MemoryProfileItem> | 否 | Skillusagepatterns |
| `topOfMind` | array<MemoryProfileItem> | 否 | Topofmind |
| `corrections` | array<MemoryProfileItem> | 否 | Corrections |
| `suppressions` | array<MemorySuppression> | 否 | Suppressions |

### `MemoryProfileItem`

A single durable memory profile item.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `id` | string | 是 | Id |
| `type` | string（枚举: interest, preference, profile, communication_style, skill_usage, top_of_mind, correction） | 是 | Type |
| `content` | string | 是 | Content |
| `confidence` | number | 否 | Confidence |
| `sourceRefs` | array<MemorySourceRef> | 否 | Sourcerefs |
| `createdAt` | string | 否 | Createdat |
| `updatedAt` | string | 否 | Updatedat |
| `status` | string（枚举: active, inactive） | 否 | Status |

### `MemoryResponse`

Response model for memory data.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `version` | string | 否 | Memory schema version |
| `lastUpdated` | string | 否 | Last update timestamp |
| `user` | UserContext | 否 |  |
| `history` | HistoryContext | 否 |  |
| `facts` | array<Fact> | 否 | Facts |

### `MemorySourceRef`

Reference to the evidence that supports a memory item.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `type` | string（枚举: daily, legacy, manual） | 否 | Type |
| `id` | string | 是 | Id |

### `MemoryStatusResponse`

Response model for memory status.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `config` | MemoryConfigResponse | 是 |  |
| `data` | MemoryResponse | 是 |  |

### `MemorySuppression`

A user-controlled rule that prevents active injection/mention.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `id` | string | 是 | Id |
| `scope` | string（枚举: profile_item, topic, daily） | 是 | Scope |
| `targetId` | string | 是 | Targetid |
| `reason` | string | 否 | Reason |
| `createdAt` | string | 否 | Createdat |
| `createdBy` | string | 否 | Createdby |

### `UserContext`

Model for user context.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `workContext` | ContextSection | 否 |  |
| `personalContext` | ContextSection | 否 |  |
| `topOfMind` | ContextSection | 否 |  |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
