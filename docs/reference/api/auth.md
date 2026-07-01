# 认证 Auth

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/v1/auth`
> 认证：部分接口公开（登录/注册/初始化/OAuth 回调），部分需要已登录会话（详见各接口说明）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-v1-auth-callback-provider) | `/api/v1/auth/callback/{provider}` | Oauth Callback |
| [`POST`](#post-api-v1-auth-change-password) | `/api/v1/auth/change-password` | Change Password |
| [`POST`](#post-api-v1-auth-initialize) | `/api/v1/auth/initialize` | Initialize Admin |
| [`POST`](#post-api-v1-auth-login-local) | `/api/v1/auth/login/local` | Login Local |
| [`POST`](#post-api-v1-auth-logout) | `/api/v1/auth/logout` | Logout |
| [`GET`](#get-api-v1-auth-me) | `/api/v1/auth/me` | Get Me |
| [`GET`](#get-api-v1-auth-oauth-provider) | `/api/v1/auth/oauth/{provider}` | Oauth Login |
| [`POST`](#post-api-v1-auth-register) | `/api/v1/auth/register` | Register |
| [`GET`](#get-api-v1-auth-setup-status) | `/api/v1/auth/setup-status` | Setup Status |

## `GET /api/v1/auth/callback/{provider}`

> Oauth Callback  
<a id="get-api-v1-auth-callback-provider"></a>

OAuth callback endpoint.

Handles the OAuth provider's callback after user authorization.
Currently a placeholder.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `provider` | string | 是 |  |

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `code` | string | 是 |  |
| `state` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/auth/callback/{provider}'
```

---

## `POST /api/v1/auth/change-password`

> Change Password  
<a id="post-api-v1-auth-change-password"></a>

Change password for the currently authenticated user.

Also handles the first-boot setup flow:
- If new_email is provided, updates email (checks uniqueness)
- If user.needs_setup is True and new_email is given, clears needs_setup
- Always increments token_version to invalidate old sessions
- Re-issues session cookie with new token_version

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `current_password` | string | 是 | Current Password |
| `new_password` | string | 是 | New Password |
| `new_email` | string(email) | 否 | New Email |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MessageResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/auth/change-password'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/v1/auth/initialize`

> Initialize Admin  
<a id="post-api-v1-auth-initialize"></a>

Create the first admin account on initial system setup.

Only callable when no admin exists. Returns 409 Conflict if an admin
already exists.

On success, the admin account is created with ``needs_setup=False`` and
the session cookie is set.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `email` | string(email) | 是 | Email |
| `password` | string | 是 | Password |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`UserResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/auth/initialize'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `POST /api/v1/auth/login/local`

> Login Local  
<a id="post-api-v1-auth-login-local"></a>

Local email/password login.

**请求体**（`application/x-www-form-urlencoded`）

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`LoginResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/auth/login/local'
```

---

## `POST /api/v1/auth/logout`

> Logout  
<a id="post-api-v1-auth-logout"></a>

Logout current user by clearing the cookie.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`MessageResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/auth/logout'
```

---

## `GET /api/v1/auth/me`

> Get Me  
<a id="get-api-v1-auth-me"></a>

Get current authenticated user info.

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`UserResponse`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/auth/me'
```

---

## `GET /api/v1/auth/oauth/{provider}`

> Oauth Login  
<a id="get-api-v1-auth-oauth-provider"></a>

Initiate OAuth login flow.

Redirects to the OAuth provider's authorization URL.
Currently a placeholder - requires OAuth provider implementation.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `provider` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/auth/oauth/{provider}'
```

---

## `POST /api/v1/auth/register`

> Register  
<a id="post-api-v1-auth-register"></a>

Register a new user account (always 'user' role).

The first admin is created explicitly through /initialize. This endpoint creates regular users.
Auto-login by setting the session cookie.

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `email` | string(email) | 是 | Email |
| `password` | string | 是 | Password |

**响应**

- **`201`** Successful Response
  - 响应体（`application/json`）：`UserResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/v1/auth/register'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/v1/auth/setup-status`

> Setup Status  
<a id="get-api-v1-auth-setup-status"></a>

Check if an admin account exists. Returns needs_setup=True when no admin exists.

**响应**

- **`200`** Successful Response

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/v1/auth/setup-status'
```

---

## 数据模型

### `ChangePasswordRequest`

Request model for password change (also handles setup flow).

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `current_password` | string | 是 | Current Password |
| `new_password` | string | 是 | New Password |
| `new_email` | string(email) | 否 | New Email |

### `HTTPValidationError`

HTTPValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `detail` | array<ValidationError> | 否 | Detail |

### `InitializeAdminRequest`

Request model for first-boot admin account creation.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `email` | string(email) | 是 | Email |
| `password` | string | 是 | Password |

### `LoginResponse`

Response model for login — token only lives in HttpOnly cookie.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `expires_in` | integer | 是 | Expires In |
| `needs_setup` | boolean | 否 | Needs Setup |

### `MessageResponse`

Generic message response.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `message` | string | 是 | Message |

### `RegisterRequest`

Request model for user registration.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `email` | string(email) | 是 | Email |
| `password` | string | 是 | Password |

### `UserResponse`

Response model for user info endpoint.

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `id` | string | 是 | Id |
| `email` | string | 是 | Email |
| `system_role` | string（枚举: admin, user） | 是 | System Role |
| `needs_setup` | boolean | 否 | Needs Setup |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
