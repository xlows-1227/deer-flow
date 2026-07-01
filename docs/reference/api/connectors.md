# 连接器平台 Connectors

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/connectors · /api/connector-types`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`GET`](#get-api-connector-audit) | `/api/connector-audit` | List All Connector Audit |
| [`GET`](#get-api-connector-types) | `/api/connector-types` | List Connector Types |
| [`GET`](#get-api-connector-types-type-name) | `/api/connector-types/{type_name}` | Get Connector Type |
| [`POST`](#post-api-connector-types-type-name-test) | `/api/connector-types/{type_name}/test` | Test Connector Type Config |
| [`GET`](#get-api-connectors) | `/api/connectors` | List Connectors |
| [`POST`](#post-api-connectors) | `/api/connectors` | Create Connector |
| [`GET`](#get-api-connectors-connector-id) | `/api/connectors/{connector_id}` | Get Connector |
| [`PATCH`](#patch-api-connectors-connector-id) | `/api/connectors/{connector_id}` | Update Connector |
| [`DELETE`](#delete-api-connectors-connector-id) | `/api/connectors/{connector_id}` | Delete Connector |
| [`POST`](#post-api-connectors-connector-id-actions) | `/api/connectors/{connector_id}/actions` | Call Connector Action |
| [`GET`](#get-api-connectors-connector-id-audit) | `/api/connectors/{connector_id}/audit` | List Connector Audit |
| [`POST`](#post-api-connectors-connector-id-disable) | `/api/connectors/{connector_id}/disable` | Disable Connector |
| [`POST`](#post-api-connectors-connector-id-enable) | `/api/connectors/{connector_id}/enable` | Enable Connector |
| [`GET`](#get-api-connectors-connector-id-grants) | `/api/connectors/{connector_id}/grants` | List Connector Grants |
| [`POST`](#post-api-connectors-connector-id-grants) | `/api/connectors/{connector_id}/grants` | Create Connector Grant |
| [`PATCH`](#patch-api-connectors-connector-id-grants-grant-id) | `/api/connectors/{connector_id}/grants/{grant_id}` | Update Connector Grant |
| [`DELETE`](#delete-api-connectors-connector-id-grants-grant-id) | `/api/connectors/{connector_id}/grants/{grant_id}` | Delete Connector Grant |
| [`POST`](#post-api-connectors-connector-id-introspect) | `/api/connectors/{connector_id}/introspect` | Introspect Connector |
| [`POST`](#post-api-connectors-connector-id-query) | `/api/connectors/{connector_id}/query` | Query Connector |
| [`GET`](#get-api-connectors-connector-id-resources) | `/api/connectors/{connector_id}/resources` | Get Connector Schema |
| [`POST`](#post-api-connectors-connector-id-sample) | `/api/connectors/{connector_id}/sample` | Sample Connector Table |
| [`GET`](#get-api-connectors-connector-id-schema) | `/api/connectors/{connector_id}/schema` | Get Connector Schema |
| [`POST`](#post-api-connectors-connector-id-test) | `/api/connectors/{connector_id}/test` | Test Connector |
| [`POST`](#post-api-connectors-connector-id-test-config) | `/api/connectors/{connector_id}/test-config` | Test Existing Connector Config |

## `GET /api/connector-audit`

> List All Connector Audit  
<a id="get-api-connector-audit"></a>

**响应**

- **`200`** Successful Response

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connector-audit'
```

---

## `GET /api/connector-types`

> List Connector Types  
<a id="get-api-connector-types"></a>

**响应**

- **`200`** Successful Response

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connector-types'
```

---

## `GET /api/connector-types/{type_name}`

> Get Connector Type  
<a id="get-api-connector-types-type-name"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `type_name` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connector-types/{type_name}'
```

---

## `POST /api/connector-types/{type_name}/test`

> Test Connector Type Config  
<a id="post-api-connector-types-type-name-test"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `type_name` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `config` | object | 否 | Config |
| `credential` | object | 否 | Credential |
| `default_policy` | object | 否 | Default Policy |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connector-types/{type_name}/test'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/connectors`

> List Connectors  
<a id="get-api-connectors"></a>

**响应**

- **`200`** Successful Response

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connectors'
```

---

## `POST /api/connectors`

> Create Connector  
<a id="post-api-connectors"></a>

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `display_name` | string | 否 | Display Name |
| `type` | string | 是 | Type |
| `config` | object | 否 | Config |
| `credential` | object | 是 | Credential |
| `default_policy` | object | 否 | Default Policy |

**响应**

- **`201`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/connectors/{connector_id}`

> Get Connector  
<a id="get-api-connectors-connector-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}'
```

---

## `PATCH /api/connectors/{connector_id}`

> Update Connector  
<a id="patch-api-connectors-connector-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `display_name` | string | 否 | Display Name |
| `config` | object | 否 | Config |
| `credential` | object | 否 | Credential |
| `default_policy` | object | 否 | Default Policy |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PATCH '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/connectors/{connector_id}`

> Delete Connector  
<a id="delete-api-connectors-connector-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`204`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}'
```

---

## `POST /api/connectors/{connector_id}/actions`

> Call Connector Action  
<a id="post-api-connectors-connector-id-actions"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `capability` | string | 是 | Capability |
| `args` | object | 否 | Args |
| `reason` | string | 否 | Reason |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/actions'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/connectors/{connector_id}/audit`

> List Connector Audit  
<a id="get-api-connectors-connector-id-audit"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/audit'
```

---

## `POST /api/connectors/{connector_id}/disable`

> Disable Connector  
<a id="post-api-connectors-connector-id-disable"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/disable'
```

---

## `POST /api/connectors/{connector_id}/enable`

> Enable Connector  
<a id="post-api-connectors-connector-id-enable"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/enable'
```

---

## `GET /api/connectors/{connector_id}/grants`

> List Connector Grants  
<a id="get-api-connectors-connector-id-grants"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/grants'
```

---

## `POST /api/connectors/{connector_id}/grants`

> Create Connector Grant  
<a id="post-api-connectors-connector-id-grants"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `subject_type` | string | 是 | Subject Type |
| `subject_id` | string | 是 | Subject Id |
| `capabilities` | array<string> | 是 | Capabilities |
| `policy_override` | object | 否 | Policy Override |

**响应**

- **`201`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/grants'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `PATCH /api/connectors/{connector_id}/grants/{grant_id}`

> Update Connector Grant  
<a id="patch-api-connectors-connector-id-grants-grant-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |
| `grant_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `subject_type` | string | 是 | Subject Type |
| `subject_id` | string | 是 | Subject Id |
| `capabilities` | array<string> | 是 | Capabilities |
| `policy_override` | object | 否 | Policy Override |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X PATCH '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/grants/{grant_id}'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `DELETE /api/connectors/{connector_id}/grants/{grant_id}`

> Delete Connector Grant  
<a id="delete-api-connectors-connector-id-grants-grant-id"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |
| `grant_id` | string | 是 |  |

**响应**

- **`204`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X DELETE '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/grants/{grant_id}'
```

---

## `POST /api/connectors/{connector_id}/introspect`

> Introspect Connector  
<a id="post-api-connectors-connector-id-introspect"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/introspect'
```

---

## `POST /api/connectors/{connector_id}/query`

> Query Connector  
<a id="post-api-connectors-connector-id-query"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `sql` | string | 是 | Sql |
| `reason` | string | 否 | Reason |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/query'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/connectors/{connector_id}/resources`

> Get Connector Schema  
<a id="get-api-connectors-connector-id-resources"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/resources'
```

---

## `POST /api/connectors/{connector_id}/sample`

> Sample Connector Table  
<a id="post-api-connectors-connector-id-sample"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `schema` | string | 是 | Schema |
| `table` | string | 是 | Table |
| `limit` | integer | 否 | Limit |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/sample'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## `GET /api/connectors/{connector_id}/schema`

> Get Connector Schema  
<a id="get-api-connectors-connector-id-schema"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X GET '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/schema'
```

---

## `POST /api/connectors/{connector_id}/test`

> Test Connector  
<a id="post-api-connectors-connector-id-test"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/test'
```

---

## `POST /api/connectors/{connector_id}/test-config`

> Test Existing Connector Config  
<a id="post-api-connectors-connector-id-test-config"></a>

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `connector_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `config` | object | 否 | Config |
| `credential` | object | 否 | Credential |
| `default_policy` | object | 否 | Default Policy |

**响应**

- **`200`** Successful Response
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/connectors/{connector_id}/test-config'
  -H 'Content-Type: application/json'
  -d '{ … }'
```

---

## 数据模型

### `ConnectorActionRequest`

ConnectorActionRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `capability` | string | 是 | Capability |
| `args` | object | 否 | Args |
| `reason` | string | 否 | Reason |

### `ConnectorConfigTestRequest`

ConnectorConfigTestRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `config` | object | 否 | Config |
| `credential` | object | 否 | Credential |
| `default_policy` | object | 否 | Default Policy |

### `ConnectorCreateRequest`

ConnectorCreateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | Name |
| `display_name` | string | 否 | Display Name |
| `type` | string | 是 | Type |
| `config` | object | 否 | Config |
| `credential` | object | 是 | Credential |
| `default_policy` | object | 否 | Default Policy |

### `ConnectorGrantRequest`

ConnectorGrantRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `subject_type` | string | 是 | Subject Type |
| `subject_id` | string | 是 | Subject Id |
| `capabilities` | array<string> | 是 | Capabilities |
| `policy_override` | object | 否 | Policy Override |

### `ConnectorQueryRequest`

ConnectorQueryRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `sql` | string | 是 | Sql |
| `reason` | string | 否 | Reason |

### `ConnectorSampleRequest`

ConnectorSampleRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `schema` | string | 是 | Schema |
| `table` | string | 是 | Table |
| `limit` | integer | 否 | Limit |

### `ConnectorUpdateRequest`

ConnectorUpdateRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 否 | Name |
| `display_name` | string | 否 | Display Name |
| `config` | object | 否 | Config |
| `credential` | object | 否 | Credential |
| `default_policy` | object | 否 | Default Policy |

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
