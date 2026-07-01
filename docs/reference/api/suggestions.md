# 建议问题 Suggestions

> 模块路由：`backend/app/gateway/routers/`　|　基础路径：`/api/threads/{thread_id}/suggestions`
> 认证：需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| [`POST`](#post-api-threads-thread-id-suggestions) | `/api/threads/{thread_id}/suggestions` | Generate Follow-up Questions |

## `POST /api/threads/{thread_id}/suggestions`

> Generate Follow-up Questions  
<a id="post-api-threads-thread-id-suggestions"></a>

Generate short follow-up questions a user might ask next, based on recent conversation context.

**路径参数**

| 名称 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `thread_id` | string | 是 |  |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `messages` | array<SuggestionMessage> | 是 | Recent conversation messages |
| `n` | integer | 否 | Number of suggestions to generate |
| `model_name` | string | 否 | Optional model override |

**响应**

- **`200`** Successful Response
  - 响应体（`application/json`）：`SuggestionsResponse`，字段见 [数据模型](#数据模型)。
- **`422`** Validation Error
  - 响应体（`application/json`）：`HTTPValidationError`，字段见 [数据模型](#数据模型)。

**请求示例（cURL）**

```bash
curl -X POST '${DEERFLOW_BASE_URL}/api/threads/{thread_id}/suggestions'
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

### `SuggestionMessage`

SuggestionMessage

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `role` | string | 是 | Message role: user|assistant |
| `content` | string | 是 | Message content as plain text |

### `SuggestionsRequest`

SuggestionsRequest

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `messages` | array<SuggestionMessage> | 是 | Recent conversation messages |
| `n` | integer | 否 | Number of suggestions to generate |
| `model_name` | string | 否 | Optional model override |

### `SuggestionsResponse`

SuggestionsResponse

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `suggestions` | array<string> | 否 | Suggested follow-up questions |

### `ValidationError`

ValidationError

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `loc` | array<string | integer> | 是 | Location |
| `msg` | string | 是 | Message |
| `type` | string | 是 | Error Type |
| `input` |  | 否 | Input |
| `ctx` | object | 否 | Context |
