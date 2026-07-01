# MCP 配置 MCP

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/mcp`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-mcp-config) | `/api/mcp/config` | Get MCP Configuration |
| [`POST`](#post-api-mcp-servers) | `/api/mcp/servers` | Create a user-owned MCP server |
| [`PUT`](#put-api-mcp-servers-name) | `/api/mcp/servers/{name}` | Update a user-owned MCP server |
| [`DELETE`](#delete-api-mcp-servers-name) | `/api/mcp/servers/{name}` | Delete a user-owned MCP server |
| [`PUT`](#put-api-mcp-servers-name-enabled) | `/api/mcp/servers/{name}/enabled` | Update MCP server enabled state for current user |

## `GET /api/mcp/config`

> Get MCP Configuration  
<a id="get-api-mcp-config"></a>

Retrieve the current user's MCP server configurations.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`McpConfigResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/mcp/config'
```

---

## `POST /api/mcp/servers`

> Create a user-owned MCP server  
<a id="post-api-mcp-servers"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `enabled` | boolean | 否 | Enabled |
| `type` | string | 否 | Type |
| `command` | string | 否 | Command |
| `args` | array<string> | 否 | Args |
| `env` | object | 否 | Env |
| `url` | string | 否 | Url |
| `headers` | object | 否 | Headers |
| `oauth` | McpOAuthConfigSchema | 否 |  |
| `description` | string | 否 | Description |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`McpServerRecord`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/mcp/servers'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `PUT /api/mcp/servers/{name}`

> Update a user-owned MCP server  
<a id="put-api-mcp-servers-name"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `type` | string | 否 | Type |
| `command` | string | 否 | Command |
| `args` | array<string> | 否 | Args |
| `env` | object | 否 | Env |
| `url` | string | 否 | Url |
| `headers` | object | 否 | Headers |
| `oauth` | McpOAuthConfigSchema | 否 |  |
| `description` | string | 否 | Description |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`McpServerRecord`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/mcp/servers/{name}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/mcp/servers/{name}`

> Delete a user-owned MCP server  
<a id="delete-api-mcp-servers-name"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**响应**

- **`204`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/mcp/servers/{name}'
```

---

## `PUT /api/mcp/servers/{name}/enabled`

> Update MCP server enabled state for current user  
<a id="put-api-mcp-servers-name-enabled"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 是 | Enabled |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`McpServerRecord`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/mcp/servers/{name}/enabled'
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

### `McpConfigResponse`

McpConfigResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `mcp_servers` | object | 否 | Mcp Servers |

### `McpOAuthConfigSchema`

McpOAuthConfigSchema

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `token_url` | string | 否 | Token Url |
| `grant_type` | string（枚举: client_credentials, refresh_token） | 否 | Grant Type |
| `client_id` | string | 否 | Client Id |
| `client_secret` | string | 否 | Client Secret |
| `refresh_token` | string | 否 | Refresh Token |
| `scope` | string | 否 | Scope |
| `audience` | string | 否 | Audience |
| `token_field` | string | 否 | Token Field |
| `token_type_field` | string | 否 | Token Type Field |
| `expires_in_field` | string | 否 | Expires In Field |
| `default_token_type` | string | 否 | Default Token Type |
| `refresh_skew_seconds` | integer | 否 | Refresh Skew Seconds |
| `extra_token_params` | object | 否 | Extra Token Params |

### `McpServerCreateRequest`

McpServerCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `enabled` | boolean | 否 | Enabled |
| `type` | string | 否 | Type |
| `command` | string | 否 | Command |
| `args` | array<string> | 否 | Args |
| `env` | object | 否 | Env |
| `url` | string | 否 | Url |
| `headers` | object | 否 | Headers |
| `oauth` | McpOAuthConfigSchema | 否 |  |
| `description` | string | 否 | Description |

### `McpServerEnabledRequest`

McpServerEnabledRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 是 | Enabled |

### `McpServerRecord`

McpServerRecord

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `enabled` | boolean | 否 | Enabled |
| `type` | string | 否 | Type |
| `command` | string | 否 | Command |
| `args` | array<string> | 否 | Args |
| `env` | object | 否 | Env |
| `url` | string | 否 | Url |
| `headers` | object | 否 | Headers |
| `oauth` | McpOAuthConfigSchema | 否 |  |
| `description` | string | 否 | Description |
| `source` | string（枚举: system, user） | 否 | Source |
| `editable` | boolean | 否 | Editable |

### `McpServerUpdateRequest`

McpServerUpdateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `enabled` | boolean | 否 | Enabled |
| `type` | string | 否 | Type |
| `command` | string | 否 | Command |
| `args` | array<string> | 否 | Args |
| `env` | object | 否 | Env |
| `url` | string | 否 | Url |
| `headers` | object | 否 | Headers |
| `oauth` | McpOAuthConfigSchema | 否 |  |
| `description` | string | 否 | Description |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
