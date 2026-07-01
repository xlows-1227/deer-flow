# 工具配置 Tools

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/tools`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-tools-image-generation-config) | `/api/tools/image-generation/config` | Get image generation tool configuration |
| [`PUT`](#put-api-tools-image-generation-config) | `/api/tools/image-generation/config` | Update image generation tool configuration |

## `GET /api/tools/image-generation/config`

> Get image generation tool configuration  
<a id="get-api-tools-image-generation-config"></a>

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ImageConfigResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/tools/image-generation/config'
```

---

## `PUT /api/tools/image-generation/config`

> Update image generation tool configuration  
<a id="put-api-tools-image-generation-config"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `default_provider` | string | 否 | Default Provider |
| `output_subdir` | string | 否 | Output Subdir |
| `providers` | object | 否 | Providers |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`ImageConfigResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/tools/image-generation/config'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `ImageConfigResponse`

ImageConfigResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `default_provider` | string | 否 | Default Provider |
| `output_subdir` | string | 否 | Output Subdir |
| `providers` | object | 否 | Providers |
| `provider_metadata` | object | 否 | Provider Metadata |

### `ImageConfigUpdateRequest`

ImageConfigUpdateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `default_provider` | string | 否 | Default Provider |
| `output_subdir` | string | 否 | Output Subdir |
| `providers` | object | 否 | Providers |

### `ImageProviderRecord`

ImageProviderRecord

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `provider` | string | 是 | Provider |
| `enabled` | boolean | 否 | Enabled |
| `display_name` | string | 否 | Display Name |
| `api_key` | string | 否 | Api Key |
| `has_api_key` | boolean | 否 | Has Api Key |
| `base_url` | string | 否 | Base Url |
| `model` | string | 否 | Model |
| `timeout_seconds` | number | 否 | Timeout Seconds |
| `trust_env` | boolean | 否 | Trust Env |
| `params` | object | 否 | Params |

### `ImageProviderUpdate`

ImageProviderUpdate

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `api_key` | string | 否 | Api Key |
| `base_url` | string | 否 | Base Url |
| `model` | string | 否 | Model |
| `timeout_seconds` | number | 否 | Timeout Seconds |
| `trust_env` | boolean | 否 | Trust Env |
| `params` | object | 否 | Params |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
