# 智能体 Agents

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/agents`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-agents) | `/api/agents` | List Custom Agents |
| [`POST`](#post-api-agents) | `/api/agents` | Create Custom Agent |
| [`GET`](#get-api-agents-check) | `/api/agents/check` | Check Agent Name |
| [`GET`](#get-api-agents-name) | `/api/agents/{name}` | Get Custom Agent |
| [`PUT`](#put-api-agents-name) | `/api/agents/{name}` | Update Custom Agent |
| [`DELETE`](#delete-api-agents-name) | `/api/agents/{name}` | Delete Custom Agent |
| [`GET`](#get-api-user-profile) | `/api/user-profile` | Get User Profile |
| [`PUT`](#put-api-user-profile) | `/api/user-profile` | Update User Profile |

## `GET /api/agents`

> List Custom Agents  
<a id="get-api-agents"></a>

List all custom agents available in the agents directory, including their soul content.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`AgentsListResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/agents'
```

---

## `POST /api/agents`

> Create Custom Agent  
<a id="post-api-agents"></a>

Create a new custom agent with its config and SOUL.md.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase) |
| `description` | string | 否 | Agent description |
| `model` | string | 否 | Optional model override |
| `tool_groups` | array<string> | 否 | Optional tool group whitelist |
| `skills` | array<string> | 否 | Optional skill whitelist (None=all enabled, []=none) |
| `soul` | string | 否 | SOUL.md content — agent personality and behavioral guardrails |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`AgentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/agents'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/agents/check`

> Check Agent Name  
<a id="get-api-agents-check"></a>

Validate an agent name and check if it is available (case-insensitive).

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`object`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/agents/check'
```

---

## `GET /api/agents/{name}`

> Get Custom Agent  
<a id="get-api-agents-name"></a>

Retrieve details and SOUL.md content for a specific custom agent.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`AgentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/agents/{name}'
```

---

## `PUT /api/agents/{name}`

> Update Custom Agent  
<a id="put-api-agents-name"></a>

Update an existing custom agent's config and/or SOUL.md.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `description` | string | 否 | Updated description |
| `model` | string | 否 | Updated model override |
| `tool_groups` | array<string> | 否 | Updated tool group whitelist |
| `skills` | array<string> | 否 | Updated skill whitelist (None=all, []=none) |
| `soul` | string | 否 | Updated SOUL.md content |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`AgentResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/agents/{name}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/agents/{name}`

> Delete Custom Agent  
<a id="delete-api-agents-name"></a>

Delete a custom agent and all its files (config, SOUL.md, memory).

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
curl -X DELETE '${DEERFLOW_BASE_URL}/api/agents/{name}'
```

---

## `GET /api/user-profile`

> Get User Profile  
<a id="get-api-user-profile"></a>

Read the global USER.md file that is injected into all custom agents.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UserProfileResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/user-profile'
```

---

## `PUT /api/user-profile`

> Update User Profile  
<a id="put-api-user-profile"></a>

Write the global USER.md file that is injected into all custom agents.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 否 | USER.md content — describes the user's background and preferences |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UserProfileResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PUT '${DEERFLOW_BASE_URL}/api/user-profile'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `AgentCreateRequest`

Request body for creating a custom agent.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase) |
| `description` | string | 否 | Agent description |
| `model` | string | 否 | Optional model override |
| `tool_groups` | array<string> | 否 | Optional tool group whitelist |
| `skills` | array<string> | 否 | Optional skill whitelist (None=all enabled, []=none) |
| `soul` | string | 否 | SOUL.md content — agent personality and behavioral guardrails |

### `AgentResponse`

Response model for a custom agent.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Agent name (hyphen-case) |
| `description` | string | 否 | Agent description |
| `model` | string | 否 | Optional model override |
| `tool_groups` | array<string> | 否 | Optional tool group whitelist |
| `skills` | array<string> | 否 | Optional skill whitelist (None=all, []=none) |
| `soul` | string | 否 | SOUL.md content |

### `AgentUpdateRequest`

Request body for updating a custom agent.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `description` | string | 否 | Updated description |
| `model` | string | 否 | Updated model override |
| `tool_groups` | array<string> | 否 | Updated tool group whitelist |
| `skills` | array<string> | 否 | Updated skill whitelist (None=all, []=none) |
| `soul` | string | 否 | Updated SOUL.md content |

### `AgentsListResponse`

Response model for listing all custom agents.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `agents` | array<AgentResponse> | 是 | Agents |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `UserProfileResponse`

Response model for the global user profile (USER.md).

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 否 | USER.md content, or null if not yet created |

### `UserProfileUpdateRequest`

Request body for setting the global user profile.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `content` | string | 否 | USER.md content — describes the user's background and preferences |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
