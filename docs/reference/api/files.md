# 文件库 Files

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/files`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-files) | `/api/files` | List Files |
| [`GET`](#get-api-files-folders) | `/api/files/folders` | List Folders |
| [`POST`](#post-api-files-folders) | `/api/files/folders` | Create Folder |
| [`POST`](#post-api-files-upload) | `/api/files/upload` | Upload Files |
| [`GET`](#get-api-files-upload-config) | `/api/files/upload-config` | Get Upload Config |
| [`GET`](#get-api-files-path) | `/api/files/{path}` | Get File |
| [`DELETE`](#delete-api-files-path) | `/api/files/{path}` | Delete File |

## `GET /api/files`

> List Files  
<a id="get-api-files"></a>

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `folder_path` | string | 否 |  |
| `source` | string | 否 |  |
| `type` | string | 否 |  |
| `q` | string | 否 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`FileListResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/files'
```

---

## `GET /api/files/folders`

> List Folders  
<a id="get-api-files-folders"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`FolderListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/files/folders'
```

---

## `POST /api/files/folders`

> Create Folder  
<a id="post-api-files-folders"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `parent_path` | string | 否 | Parent Path |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`FileItem`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/files/folders'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/files/upload`

> Upload Files  
<a id="post-api-files-upload"></a>

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<string> | 是 | Files |
| `folder_path` | string | 否 | Folder Path |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`FileListResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/files/upload'
```

---

## `GET /api/files/upload-config`

> Get Upload Config  
<a id="get-api-files-upload-config"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`FileUploadConfigResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/files/upload-config'
```

---

## `GET /api/files/{path}`

> Get File  
<a id="get-api-files-path"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `download` | boolean | 否 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/files/{path}'
```

---

## `DELETE /api/files/{path}`

> Delete File  
<a id="delete-api-files-path"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`DeleteResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/files/{path}'
```

---

## 数据模型

### `Body_upload_files_api_files_upload_post`

Body_upload_files_api_files_upload_post

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `files` | array<string> | 是 | Files |
| `folder_path` | string | 否 | Folder Path |

### `DeleteResponse`

DeleteResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `success` | boolean | 是 | Success |
| `message` | string | 是 | Message |

### `FileItem`

FileItem

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `id` | string | 是 | Id |
| `name` | string | 是 | Name |
| `path` | string | 是 | Path |
| `kind` | string（枚举: file, folder） | 是 | Kind |
| `source` | string（枚举: uploaded, generated） | 否 | Source |
| `size` | integer | 否 | Size |
| `mime_type` | string | 否 | Mime Type |
| `extension` | string | 否 | Extension |
| `modified_at` | string(date-time) | 是 | Modified At |
| `preview_url` | string | 否 | Preview Url |
| `download_url` | string | 否 | Download Url |

### `FileListResponse`

FileListResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `folder_path` | string | 否 | Folder Path |
| `items` | array<FileItem> | 是 | Items |
| `total` | integer | 是 | Total |

### `FileUploadConfigResponse`

FileUploadConfigResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `max_upload_bytes` | integer | 是 | Max Upload Bytes |
| `max_upload_label` | string | 是 | Max Upload Label |

### `FolderCreateRequest`

FolderCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `parent_path` | string | 否 | Parent Path |

### `FolderListResponse`

FolderListResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `folders` | array<string> | 是 | Folders |

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
