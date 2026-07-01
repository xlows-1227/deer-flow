# 上传 Uploads

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/threads/{thread_id}/uploads`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`POST`](#post-api-threads-thread-id-uploads) | `/api/threads/{thread_id}/uploads` | Upload Files |
| [`GET`](#get-api-threads-thread-id-uploads-limits) | `/api/threads/{thread_id}/uploads/limits` | Get Upload Limits |
| [`GET`](#get-api-threads-thread-id-uploads-list) | `/api/threads/{thread_id}/uploads/list` | List Uploaded Files |
| [`DELETE`](#delete-api-threads-thread-id-uploads-filename) | `/api/threads/{thread_id}/uploads/{filename}` | Delete Uploaded File |

## `POST /api/threads/{thread_id}/uploads`

> Upload Files  
<a id="post-api-threads-thread-id-uploads"></a>

Upload multiple files to a thread's uploads directory.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<string> | 是 | Files |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UploadResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/uploads'
```

---

## `GET /api/threads/{thread_id}/uploads/limits`

> Get Upload Limits  
<a id="get-api-threads-thread-id-uploads-limits"></a>

Return upload limits used by the gateway for this thread.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UploadLimits`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/uploads/limits'
```

---

## `GET /api/threads/{thread_id}/uploads/list`

> List Uploaded Files  
<a id="get-api-threads-thread-id-uploads-list"></a>

List all files in a thread's uploads directory.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/uploads/list'
```

---

## `DELETE /api/threads/{thread_id}/uploads/{filename}`

> Delete Uploaded File  
<a id="delete-api-threads-thread-id-uploads-filename"></a>

Delete a file from a thread's uploads directory.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |
| `filename` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/uploads/{filename}'
```

---

## 数据模型

### `Body_upload_files_api_threads__thread_id__uploads_post`

Body_upload_files_api_threads__thread_id__uploads_post

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<string> | 是 | Files |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `UploadLimits`

Application-level upload limits exposed to clients.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `max_files` | integer | 是 | Max Files |
| `max_file_size` | integer | 是 | Max File Size |
| `max_total_size` | integer | 是 | Max Total Size |

### `UploadResponse`

Response model for file upload.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `success` | boolean | 是 | Success |
| `files` | array<object> | 是 | Files |
| `message` | string | 是 | Message |
| `skipped_files` | array<string> | 否 | Skipped Files |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
