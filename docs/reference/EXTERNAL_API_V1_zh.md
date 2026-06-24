# DeerFlow External API V1 接口文档与接入手册

> 适用对象：需要将业务系统接入 DeerFlow Agent 能力的开发、测试和运维人员  
> API 版本：V1  
> 基础路径：`/api/v1/external`  
> 机器可读规范：[external-api-v1.openapi.yaml](./external-api-v1.openapi.yaml)

## 1. 能力概览

External API V1 提供以下能力：

- 查询当前 API Key 可使用的 Skill。
- 创建持久化 Conversation。
- 在 Conversation 中异步创建 Run。
- 查询 Run 状态和最终回答。
- 取消尚未结束的 Run。
- 使用同一个 Conversation 连续调用并保留上下文。

V1 不提供同步调用、SSE 流式输出、Webhook、历史消息列表、文件上传或生成物下载。

典型调用流程：

1. DeerFlow 平台管理员生成 API Key，并配置允许使用的 Skill。
2. 外部系统调用 Skill 列表接口，确认可用能力。
3. 外部系统创建 Conversation，保存返回的 `conversation_id`。
4. 外部系统在该 Conversation 下创建 Run，保存返回的 `run_id`。
5. 外部系统轮询 Run，直至状态变为 `completed`、`failed` 或 `cancelled`。
6. 后续消息继续使用原 `conversation_id`；需要独立上下文时创建新 Conversation。

## 2. 接入前准备

平台方需要向外部系统提供：

| 项目 | 示例 | 说明 |
| --- | --- | --- |
| 服务地址 | `https://deerflow.example.com` | 生产环境必须使用 HTTPS |
| API Key | `dfk_...` | 完整 Key 只在生成时显示一次 |
| Skill 白名单 | `customer-summary` | Key 只能使用白名单内且当前已启用的 Skill |
| 联调环境 | 测试/生产 | 不同环境应使用不同 Key |

外部系统应将 API Key 保存在密钥管理服务或服务端环境变量中，不得放入前端代码、URL、日志或异常信息。

## 3. 通用约定

### 3.1 请求地址

```text
{BASE_URL}/api/v1/external/{resource}
```

以下示例假设：

```bash
export DEERFLOW_BASE_URL='https://deerflow.example.com'
export DEERFLOW_API_KEY='dfk_<key_id>_<secret>'
```

### 3.2 身份认证

所有 External API 请求必须携带：

```http
Authorization: Bearer <API_KEY>
```

API Key 仅能访问 `/api/v1/external/*`，不能访问 DeerFlow 的模型、Skill、Connector 或其他管理接口。

### 3.3 请求和响应格式

- 请求体和响应体均为 JSON。
- 有请求体时使用 `Content-Type: application/json`。
- 未声明的请求字段会被拒绝。
- 单个请求体最大为 256 KB；超出时返回 HTTP `413`。
- 时间字段使用 ISO 8601 格式，服务端时间通常为 UTC，例如 `2026-06-23T08:30:00Z`。
- 所有 External API 响应均包含 `X-Request-ID` 和 `Cache-Control: no-store` 响应头。

### 3.4 请求 ID

外部系统可传入：

```http
X-Request-ID: crm_20260623_000001
```

合法格式为 8～64 个字母、数字、下划线或连字符。未提供或格式不合法时，服务端会生成形如 `req_<uuid>` 的 ID。

成功响应体中的 `request_id` 与响应头 `X-Request-ID` 一致。报错时请将该 ID 提供给 DeerFlow 运维人员。

### 3.5 幂等

以下创建接口支持 `Idempotency-Key`：

- `POST /conversations`
- `POST /conversations/{conversation_id}/runs`

建议生产调用始终提供：

```http
Idempotency-Key: crm-order-20260623-000001
```

约束和行为：

- 长度不超过 128 个字符，且不能是空白字符串。
- 有效期为 24 小时。
- 同一 API Key、同一 `Idempotency-Key`、同一请求内容：返回第一次创建的结果。
- 同一 API Key、同一 `Idempotency-Key`、不同请求内容：返回 `409 idempotency_conflict`。
- 第一次请求仍在处理中时重复提交：返回 `409 idempotency_in_progress`。
- API Key 轮换后，新的 Key 使用新的幂等命名空间。

幂等只防止重复创建 Conversation 或 Run，不保证 Agent 的回答完全确定。

## 4. 接口清单

| 方法 | 路径 | 成功状态码 | 用途 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/external/skills` | `200` | 查询可用 Skill |
| `POST` | `/api/v1/external/conversations` | `201` | 创建 Conversation |
| `GET` | `/api/v1/external/conversations/{conversation_id}` | `200` | 查询 Conversation |
| `POST` | `/api/v1/external/conversations/{conversation_id}/runs` | `202` | 创建异步 Run |
| `GET` | `/api/v1/external/runs/{run_id}` | `200` | 查询 Run |
| `POST` | `/api/v1/external/runs/{run_id}/cancel` | `200` | 取消 Run |

## 5. 查询可用 Skill

### `GET /api/v1/external/skills`

只返回当前 API Key 白名单内、当前已启用且目标 Agent 允许使用的 Skill。

请求示例：

```bash
curl "${DEERFLOW_BASE_URL}/api/v1/external/skills" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H 'X-Request-ID: integration_skills_001'
```

响应示例：

```json
{
  "request_id": "integration_skills_001",
  "skills": [
    {
      "name": "customer-summary",
      "description": "Summarize customer history and recent activities.",
      "display_name": "Customer Summary",
      "description_zh": "总结客户历史和近期动态"
    }
  ]
}
```

`skills` 为空通常表示 API Key 尚未配置 Skill 白名单，或白名单中的 Skill 当前未启用。

## 6. 创建 Conversation

### `POST /api/v1/external/conversations`

Conversation 是对外稳定的会话资源。其内部 Thread ID 不会暴露。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 约束与说明 |
| --- | --- | --- | --- | --- |
| `source` | string | 否 | `default` | 1～64 字符；仅字母、数字、下划线、点和连字符 |
| `external_conversation_id` | string | 否 | `null` | 1～256 字符；外部系统自己的会话或业务 ID |
| `agent` | string | 否 | `lead_agent` | 1～128 字符；仅字母、数字、下划线和连字符 |
| `default_skill` | string | 否 | `null` | 当前 API Key 可用的 Skill；作为该 Conversation 的默认 Skill |
| `metadata` | object | 否 | `{}` | 外部系统自定义 JSON 元数据，编码后不超过 32 KB |

当提供 `external_conversation_id` 时，`用户 + source + external_conversation_id` 必须唯一。建议使用该组合建立外部业务对象与 DeerFlow Conversation 的稳定映射。

请求示例：

```bash
curl -X POST "${DEERFLOW_BASE_URL}/api/v1/external/conversations" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: create-crm-session-789' \
  -H 'X-Request-ID: crm_create_000789' \
  -d '{
    "source": "crm",
    "external_conversation_id": "crm-session-789",
    "agent": "lead_agent",
    "default_skill": "customer-summary",
    "metadata": {
      "tenant_id": "tenant-01",
      "customer_id": "customer-789"
    }
  }'
```

响应示例：

```json
{
  "request_id": "crm_create_000789",
  "conversation_id": "conv_8a726cd00ec54a14ad9a8a065f31d82f",
  "status": "active",
  "agent": "lead_agent",
  "default_skill": "customer-summary",
  "source": "crm",
  "external_conversation_id": "crm-session-789",
  "created_at": "2026-06-23T08:30:00Z",
  "updated_at": "2026-06-23T08:30:00Z"
}
```

外部系统必须持久化 `conversation_id`，后续 Run 通过该 ID 继续会话。

## 7. 查询 Conversation

### `GET /api/v1/external/conversations/{conversation_id}`

请求示例：

```bash
curl "${DEERFLOW_BASE_URL}/api/v1/external/conversations/conv_8a726cd00ec54a14ad9a8a065f31d82f" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}"
```

响应结构与创建 Conversation 的响应相同。

Conversation 只能由其所属用户的 API Key 查询。查询其他用户的 Conversation 时也会返回 `404 conversation_not_found`，不会泄露资源是否存在。

## 8. 创建异步 Run

### `POST /api/v1/external/conversations/{conversation_id}/runs`

Run 表示一次异步 Agent 执行。同一 Conversation 的 Run 会复用已有上下文。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 约束与说明 |
| --- | --- | --- | --- | --- |
| `message` | string | 是 | - | 1～200,000 字符，不能全为空白 |
| `skill` | string | 否 | Conversation 的 `default_skill` | 只覆盖本次 Run，不修改 Conversation |
| `mode` | string | 否 | `standard` | `standard`、`thinking`、`pro`、`ultra` 或 `flash` |
| `metadata` | object | 否 | `{}` | 本次 Run 的外部自定义 JSON 元数据，编码后不超过 32 KB |

Skill 选择优先级：

1. 本次请求的 `skill`；
2. Conversation 的 `default_skill`；
3. 不使用 Skill。

执行模式：

| mode | 用途 | 特点 |
| --- | --- | --- |
| `standard` | 默认通用任务 | 当前实现采用均衡的规划执行策略 |
| `thinking` | 较轻量推理 | 开启思考，较低推理强度 |
| `pro` | 复杂任务 | 开启规划，中等推理强度 |
| `ultra` | 高复杂度任务 | 高推理强度，可使用子 Agent |
| `flash` | 低延迟简单任务 | 不开启思考；不能与最终选中的 Skill 同时使用 |

请求示例：

```bash
curl -X POST \
  "${DEERFLOW_BASE_URL}/api/v1/external/conversations/conv_8a726cd00ec54a14ad9a8a065f31d82f/runs" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: crm-session-789-message-001' \
  -H 'X-Request-ID: crm_run_000001' \
  -d '{
    "message": "请总结该客户的历史信息，并给出下一步跟进建议。",
    "skill": "customer-summary",
    "mode": "standard",
    "metadata": {
      "business_request_id": "follow-up-001"
    }
  }'
```

响应示例：

```json
{
  "request_id": "crm_run_000001",
  "run_id": "run_a7dbbc73d6004e83a9522a61111c61ef",
  "conversation_id": "conv_8a726cd00ec54a14ad9a8a065f31d82f",
  "skill": "customer-summary",
  "status": "pending",
  "answer": null,
  "error": null,
  "created_at": "2026-06-23T08:31:00Z",
  "updated_at": "2026-06-23T08:31:00Z"
}
```

注意：

- 接口返回 HTTP `202` 仅表示 Run 已接受，不表示执行完成。
- 同一用户默认最多有 3 个处于执行中的 External Run，超限返回 `429 concurrency_limit_exceeded`。
- 同一 Conversation 不允许并发执行多个 Run，冲突时返回 `409 conversation_busy`。

## 9. 查询 Run

### `GET /api/v1/external/runs/{run_id}`

请求示例：

```bash
curl "${DEERFLOW_BASE_URL}/api/v1/external/runs/run_a7dbbc73d6004e83a9522a61111c61ef" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}"
```

执行中响应：

```json
{
  "request_id": "req_35d40ad57e6946efa595f369090334b6",
  "run_id": "run_a7dbbc73d6004e83a9522a61111c61ef",
  "conversation_id": "conv_8a726cd00ec54a14ad9a8a065f31d82f",
  "skill": "customer-summary",
  "status": "running",
  "answer": null,
  "error": null,
  "created_at": "2026-06-23T08:31:00Z",
  "updated_at": "2026-06-23T08:31:02Z"
}
```

完成响应：

```json
{
  "request_id": "req_7520ca6b83794abe93269525b3410e87",
  "run_id": "run_a7dbbc73d6004e83a9522a61111c61ef",
  "conversation_id": "conv_8a726cd00ec54a14ad9a8a065f31d82f",
  "skill": "customer-summary",
  "status": "completed",
  "answer": "该客户近期重点关注……建议下一步……",
  "error": null,
  "created_at": "2026-06-23T08:31:00Z",
  "updated_at": "2026-06-23T08:31:18Z"
}
```

Run 状态：

| 状态 | 是否终态 | 说明 |
| --- | --- | --- |
| `pending` | 否 | 已创建，等待执行 |
| `running` | 否 | 正在执行 |
| `completed` | 是 | 执行成功；最终结果位于 `answer` |
| `failed` | 是 | 执行失败；`error` 为安全化后的通用错误描述 |
| `cancelled` | 是 | 已取消或执行被中断 |

建议每 2 秒轮询一次，并在连续失败时使用指数退避。业务系统应自行设置总等待时限。

## 10. 取消 Run

### `POST /api/v1/external/runs/{run_id}/cancel`

请求示例：

```bash
curl -X POST \
  "${DEERFLOW_BASE_URL}/api/v1/external/runs/run_a7dbbc73d6004e83a9522a61111c61ef/cancel" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}"
```

响应结构与查询 Run 相同。

- Run 为 `pending`、`running` 或内部中断状态时，服务端会尝试取消。
- Run 已处于终态时，接口不会重新执行任务，返回当前状态。

## 11. 完整 Python 接入示例

```python
import os
import time
import uuid

import requests

BASE_URL = os.environ["DEERFLOW_BASE_URL"].rstrip("/")
API_KEY = os.environ["DEERFLOW_API_KEY"]

session = requests.Session()
session.headers.update(
    {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
)


def request_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def create_conversation(external_id: str) -> str:
    response = session.post(
        f"{BASE_URL}/api/v1/external/conversations",
        headers={
            "X-Request-ID": request_id("createconv"),
            "Idempotency-Key": f"conversation-{external_id}",
        },
        json={
            "source": "crm",
            "external_conversation_id": external_id,
            "default_skill": "customer-summary",
            "metadata": {"integration": "crm"},
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["conversation_id"]


def create_run(conversation_id: str, business_request_id: str, message: str) -> str:
    response = session.post(
        f"{BASE_URL}/api/v1/external/conversations/{conversation_id}/runs",
        headers={
            "X-Request-ID": request_id("createrun"),
            "Idempotency-Key": f"run-{business_request_id}",
        },
        json={
            "message": message,
            "mode": "standard",
            "metadata": {"business_request_id": business_request_id},
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["run_id"]


def wait_for_run(run_id: str, timeout_seconds: int = 300) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = session.get(
            f"{BASE_URL}/api/v1/external/runs/{run_id}",
            headers={"X-Request-ID": request_id("getrun")},
            timeout=30,
        )
        response.raise_for_status()
        run = response.json()
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(2)
    raise TimeoutError(f"Run {run_id} did not finish within {timeout_seconds}s")


conversation_id = create_conversation("crm-session-789")
run_id = create_run(
    conversation_id,
    business_request_id="follow-up-001",
    message="请总结该客户的历史信息，并给出下一步跟进建议。",
)
result = wait_for_run(run_id)

if result["status"] == "completed":
    print(result["answer"])
else:
    raise RuntimeError(
        f"Run ended with {result['status']}; request_id={result['request_id']}"
    )
```

在生产代码中，应额外处理本章下一节列出的状态码，并保证网络重试继续使用原 `Idempotency-Key`。

## 12. 错误格式与错误码

统一错误格式：

```json
{
  "error": {
    "code": "conversation_not_found",
    "message": "conversation not found",
    "request_id": "req_35d40ad57e6946efa595f369090334b6",
    "details": {}
  }
}
```

`details` 仅在有安全的补充信息时出现。参数校验失败时，`details` 为校验问题数组。

| HTTP | code | 含义 | 建议处理 |
| --- | --- | --- | --- |
| `401` | `missing_api_key` | 未提供 Bearer API Key | 补充认证头，不重试原请求 |
| `401` | `invalid_api_key` | Key 无效、过期、已吊销或已被轮换 | 更换有效 Key |
| `403` | `insufficient_scope` | Key 缺少接口所需 Scope | 联系平台管理员 |
| `404` | `agent_not_available` | Agent 不存在或不可用 | 检查 `agent` |
| `404` | `skill_not_available` | Skill 不在白名单、未启用或 Agent 不允许 | 查询 `/skills` 后修正 |
| `404` | `conversation_not_found` | Conversation 不存在或不属于当前用户 | 检查 ID 和环境 |
| `404` | `run_not_found` | Run 不存在、不是 External Run 或不属于当前用户 | 检查 ID 和环境 |
| `409` | `external_conversation_exists` | 外部会话映射已存在 | 使用 `details.conversation_id` |
| `409` | `idempotency_conflict` | 相同幂等 Key 对应不同请求 | 使用新的幂等 Key 或修正请求 |
| `409` | `idempotency_in_progress` | 相同幂等请求仍在处理中 | 稍后使用相同 Key 重试 |
| `409` | `conversation_closed` | Conversation 已关闭 | 创建新 Conversation |
| `409` | `conversation_busy` | 同一 Conversation 已有 Run 执行中 | 等待当前 Run 结束 |
| `413` | `request_too_large` | 请求体超过 256 KB | 缩小请求 |
| `422` | `invalid_request` | JSON 字段、类型、长度或格式不合法 | 根据 `details` 修正 |
| `422` | `invalid_idempotency_key` | 幂等 Key 为空或超过 128 字符 | 修正请求头 |
| `422` | `flash_not_available_with_skill` | `flash` 与最终选中的 Skill 同时使用 | 改用其他 mode 或移除 Skill |
| `429` | `concurrency_limit_exceeded` | 用户活跃 Run 达到上限 | 退避后重试 |
| `500` | `internal_error` | 服务端内部错误 | 使用相同幂等 Key 重试创建操作，并报告请求 ID |
| `503` | `external_api_unavailable` | External API 持久化未启用 | 联系平台运维 |

错误响应中的 `message` 用于诊断和展示，程序逻辑应以稳定的 `code` 为准。

## 13. 重试策略

| 场景 | 是否重试 | 策略 |
| --- | --- | --- |
| 网络超时、连接中断 | 是 | 创建操作必须保留原 `Idempotency-Key` |
| `429` | 是 | 指数退避，例如 2、4、8、16 秒 |
| `500`、`503` | 有条件 | 使用原幂等 Key 重试；持续失败时告警 |
| `idempotency_in_progress` | 是 | 短暂等待后使用相同 Key 重试 |
| `conversation_busy` | 是 | 等待当前 Run 进入终态 |
| 其他 `4xx` | 否 | 修正请求或权限后再调用 |

不要在重试时生成新的幂等 Key，否则可能创建重复 Run。

## 14. API Key 生命周期

API Key 的生成和管理属于 DeerFlow 平台侧操作，不属于外部程序的 Bearer API 调用范围。

平台操作员使用浏览器登录会话和 CSRF 保护调用：

```text
GET    /api/v1/api-keys/current
POST   /api/v1/api-keys/current/rotate
PUT    /api/v1/api-keys/current/policy
DELETE /api/v1/api-keys/current
```

关键规则：

- 每个用户同一时间只有一个 active API Key。
- 生成或轮换时，完整 Key 只返回一次。
- 轮换会立即吊销旧 Key，但已有 Conversation 仍归属于原用户，新 Key 可继续访问。
- 吊销后所有使用该 Key 的 External API 请求立即失败。
- `allowed_skills` 为空时，该 Key 无可用 Skill，但仍可创建不指定 Skill 的 Run。

## 15. 安全与业务边界

- 仅通过 HTTPS 传输 API Key。
- 不在浏览器、移动端或第三方脚本中直接调用。
- 不在日志中记录 `Authorization`、消息正文、回答或敏感 metadata。
- `metadata` 是业务辅助信息，不能用来覆盖服务端认证身份或权限。
- Agent 输出是非确定性内容。付款、审批、删除、发货等业务操作必须由外部系统再次执行权限、状态机和幂等校验。
- Run 失败时不会向外返回内部堆栈、路径或原始错误。
- 建议平台网关按 API Key 和 IP 配置请求频率限制；V1 应用进程只限制活跃 Run 并发数。

## 16. 上线检查清单

- [ ] 已获得正确环境的 Base URL 和 API Key。
- [ ] API Key 已配置所需 Skill 白名单。
- [ ] 调用 `/skills` 能看到预期 Skill。
- [ ] 外部系统已持久化 `conversation_id` 和 `run_id`。
- [ ] 两个创建接口均使用稳定且唯一的 `Idempotency-Key`。
- [ ] 已实现异步轮询、终态判断、总超时和取消。
- [ ] 已按 `error.code` 处理错误，而不是匹配 `message`。
- [ ] 日志保留 `X-Request-ID`，但不记录 API Key 和敏感正文。
- [ ] 已验证 API Key 轮换、网络超时重试、并发冲突和服务端错误场景。
- [ ] 确定性业务操作不会仅依赖 Agent 输出直接执行。

## 17. 平台部署要求

本节供 DeerFlow 平台运维参考：

- External API V1 依赖 SQLite 或 PostgreSQL，`database.backend=memory` 模式不可用。
- 必须执行数据库迁移 `2026_06_08_external_api_v1`。
- 生产环境必须设置稳定且长度至少 32 个字符的 `EXTERNAL_API_KEY_PEPPER`。
- 更换 `EXTERNAL_API_KEY_PEPPER` 会使全部现有 API Key 失效。
- 应在 Nginx 或 API Gateway 配置不大于 256 KB 的请求体限制及适当的频率限制。
