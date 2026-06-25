# DeerFlow External API V1 测试手册

> 适用对象：External API V1 联调、验收、回归测试
> 接口文档：[EXTERNAL_API_V1_zh.md](./EXTERNAL_API_V1_zh.md)
> OpenAPI 规范：[external-api-v1.openapi.yaml](./external-api-v1.openapi.yaml)
> 基础路径：`/api/v1/external`
> 更新日期：2026-06-24

## 1. 测试目标

本手册用于验证 DeerFlow External API V1 是否满足外部系统接入要求，重点覆盖：

- Bearer API Key 鉴权、轮换、吊销和 Skill 白名单。
- Conversation 创建、查询、外部业务 ID 映射和幂等重放。
- Run 创建、异步轮询、状态映射、取消和并发限制。
- 错误码、请求 ID、请求体大小限制、审计和安全边界。
- OpenAPI 契约与实际 FastAPI 路由一致性。

External API V1 对外只开放以下 6 个机器接口：

| 方法 | 路径 | 主要验证点 |
| --- | --- | --- |
| `GET` | `/api/v1/external/skills` | Skill 白名单与摘要字段 |
| `POST` | `/api/v1/external/conversations` | 创建会话、外部映射、幂等 |
| `GET` | `/api/v1/external/conversations/{conversation_id}` | 查询会话、用户隔离 |
| `POST` | `/api/v1/external/conversations/{conversation_id}/runs` | 创建异步 Run、Skill、模式、并发 |
| `GET` | `/api/v1/external/runs/{run_id}` | 查询 Run 状态和结果 |
| `POST` | `/api/v1/external/runs/{run_id}/cancel` | 取消 Run、终态幂等 |

平台侧 API Key 管理接口位于 `/api/v1/api-keys/current*`，仅供浏览器登录会话使用，不作为外部系统机器接口交付；但本手册仍包含平台侧验收项。

## 2. 测试范围

### 2.1 范围内

- API Key 认证中间件：只接受 `Authorization: Bearer <API_KEY>`。
- External API 统一错误结构。
- `X-Request-ID` 透传、生成和响应头回写。
- `Cache-Control: no-store` 响应头。
- `Idempotency-Key` 创建接口重试语义。
- `source + external_conversation_id` 业务映射唯一性。
- 外部请求模型禁止内部字段。
- Skill 权限：已启用 Skill、Agent 可用 Skill、API Key 白名单三者交集。
- Run 状态对外稳定映射：
  - `pending` -> `pending`
  - `running` -> `running`
  - `success` -> `completed`
  - `interrupted` -> `cancelled`
  - `error` / `timeout` / 未识别状态 -> `failed`
- 审计只记录元数据，不记录 API Key、消息正文、回答内容。

### 2.2 范围外

- 通过 External API 安装、编辑、启停或删除 Skill。
- 通过 External API 管理 Connector、模型配置、文件系统、内部 Thread。
- 同步阻塞式 Run 返回完整执行流。
- Agent 回答内容的确定性校验。Agent 输出可能因模型、上下文和工具状态变化而不同，应只验证接口契约和业务可接受性。

## 3. 测试环境

### 3.1 环境要求

| 项目 | 要求 |
| --- | --- |
| DeerFlow Gateway | 部署包含 External API V1 路由和中间件的版本 |
| 持久化 | SQLite 或 PostgreSQL；`database.backend=memory` 下 External API 应失败关闭 |
| 环境变量 | 生产环境设置稳定的 `EXTERNAL_API_KEY_PEPPER`，长度至少 32 字符 |
| API Key | 通过平台侧浏览器会话生成；完整 Key 只显示一次 |
| Skill | 至少启用一个测试 Skill，例如 `sales-report` |
| 网络 | 外部测试机可访问 `DEERFLOW_BASE_URL` |
| 日志 | 可查看 Gateway 日志和 External API audit 表，便于问题定位 |

### 3.2 测试账号和数据

建议准备两个用户，验证用户隔离和 Key 轮换：

| 标识 | 用途 |
| --- | --- |
| `qa_user_a` | 主流程、幂等、Run、取消 |
| `qa_user_b` | 隔离测试，不能访问 `qa_user_a` 创建的资源 |

建议准备以下变量：

```bash
export DEERFLOW_BASE_URL="https://deerflow.example.com"
export DEERFLOW_API_KEY="dfk_xxx"
export QA_SOURCE="external-qa"
export QA_EXTERNAL_CONVERSATION_ID="qa-conv-20260624-001"
export QA_REQUEST_ID="qa-req-20260624-001"
```

### 3.3 通用请求头

所有 External API 请求均应带：

```http
Authorization: Bearer <DEERFLOW_API_KEY>
Content-Type: application/json
X-Request-ID: <8-64位字母数字下划线或中划线，可选>
```

创建 Conversation 和创建 Run 时建议额外带：

```http
Idempotency-Key: <稳定业务幂等键，最长 128 字符>
```

### 3.4 通用响应断言

每个 External API 响应至少验证：

- 响应头包含 `X-Request-ID`。
- 响应头包含 `Cache-Control: no-store`。
- 成功响应不包含内部字段：`thread_id`、`user_id`、`config`、`context`、`connector_ids`。
- 错误响应符合：

```json
{
  "error": {
    "code": "stable_error_code",
    "message": "human readable message",
    "request_id": "req_xxx",
    "details": {}
  }
}
```

程序逻辑应断言 `error.code`，不要依赖 `message` 文案。

## 4. 自动化回归测试

### 4.1 核心 External API 回归

在 `backend` 目录执行：

```bash
uv run pytest \
  tests/test_external_api_models.py \
  tests/test_external_conversations_router.py \
  tests/test_external_runs_router.py \
  tests/test_external_api_auth.py \
  tests/test_external_api_audit.py \
  tests/test_external_api_e2e.py \
  -q
```

通过标准：

- 所有用例通过。
- 不出现 External API 相关失败。
- 如存在无关 warning，需记录但不阻断上线，除非 warning 指向鉴权、安全、持久化或契约问题。

### 4.2 完整 External API 影响面回归

建议在发版前执行：

```bash
uv run pytest \
  tests/test_external_api_models.py \
  tests/test_external_api_dependencies.py \
  tests/test_external_api_auth.py \
  tests/test_external_api_audit.py \
  tests/test_external_api_e2e.py \
  tests/test_external_conversations_router.py \
  tests/test_external_runs_router.py \
  tests/test_external_repositories.py \
  tests/test_external_skill_policy.py \
  tests/test_external_skill_runtime.py \
  tests/test_api_key_service.py \
  tests/test_api_keys_router.py \
  -q
```

### 4.3 OpenAPI 契约校验

在 `backend` 目录执行：

```bash
uv run python - <<'PY'
from pathlib import Path
import yaml
from fastapi import FastAPI
from app.gateway.routers.external import router

spec = yaml.safe_load(Path("../docs/reference/external-api-v1.openapi.yaml").read_text(encoding="utf-8"))
app = FastAPI()
app.include_router(router)
generated = app.openapi()

actual = {
    (method, route.path)
    for route in router.routes
    for method in route.methods
    if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}
}
documented = {
    (method.upper(), path)
    for path, operations in spec["paths"].items()
    for method in operations
    if method.lower() in {"get", "post", "put", "delete", "patch"}
}
assert actual == documented, (actual - documented, documented - actual)

for path, item in spec["paths"].items():
    for method, operation in item.items():
        if method not in {"get", "post", "put", "delete", "patch"}:
            continue
        documented_success = {code for code in operation["responses"] if code.startswith("2")}
        generated_success = {code for code in generated["paths"][path][method]["responses"] if code.startswith("2")}
        assert documented_success == generated_success, (path, method, documented_success, generated_success)

refs = []
def walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                refs.append(child)
            walk(child)
    elif isinstance(value, list):
        for child in value:
            walk(child)
walk(spec)
for ref in refs:
    assert ref.startswith("#/")
    node = spec
    for segment in ref[2:].split("/"):
        node = node[segment.replace("~1", "/").replace("~0", "~")]

print(f"External API OpenAPI contract OK: {len(documented)} operations, {len(refs)} refs")
PY
```

通过标准：

- 文档接口数量为 6。
- 文档路径、方法与 FastAPI 路由完全一致。
- 成功状态码一致。
- 所有本地 `$ref` 可解析。

## 5. 人工联调主流程

### 5.1 查询 Skill

```bash
curl "${DEERFLOW_BASE_URL}/api/v1/external/skills" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H "X-Request-ID: ${QA_REQUEST_ID}-skills"
```

期望：

- HTTP `200`。
- `skills` 只包含该 API Key 白名单允许、系统已启用、目标 Agent 可使用的 Skill。
- 每个 Skill 仅包含 `name`、`description`、`display_name`、`description_zh`。

### 5.2 创建 Conversation

```bash
curl -X POST "${DEERFLOW_BASE_URL}/api/v1/external/conversations" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: ${QA_REQUEST_ID}-create-conv" \
  -H "Idempotency-Key: conv-${QA_EXTERNAL_CONVERSATION_ID}" \
  -d '{
    "source": "'"${QA_SOURCE}"'",
    "external_conversation_id": "'"${QA_EXTERNAL_CONVERSATION_ID}"'",
    "agent": "lead_agent",
    "default_skill": "sales-report",
    "metadata": {
      "test_case": "manual-main-flow",
      "customer_id": "customer-001"
    }
  }'
```

期望：

- HTTP `201`。
- 返回 `conversation_id`，格式通常为 `conv_<hex>`。
- `status` 为 `active`。
- 返回 `source` 和 `external_conversation_id` 与请求一致。
- 不返回内部 `thread_id`。

保存：

```bash
export QA_CONVERSATION_ID="<response.conversation_id>"
```

### 5.3 查询 Conversation

```bash
curl "${DEERFLOW_BASE_URL}/api/v1/external/conversations/${QA_CONVERSATION_ID}" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H "X-Request-ID: ${QA_REQUEST_ID}-get-conv"
```

期望：

- HTTP `200`。
- `conversation_id`、`source`、`external_conversation_id` 与创建结果一致。
- 不返回内部 `thread_id`。

### 5.4 创建 Run

```bash
curl -X POST "${DEERFLOW_BASE_URL}/api/v1/external/conversations/${QA_CONVERSATION_ID}/runs" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: ${QA_REQUEST_ID}-create-run" \
  -H "Idempotency-Key: run-${QA_EXTERNAL_CONVERSATION_ID}-001" \
  -d '{
    "message": "请生成一份简短的销售数据分析摘要，用于接口联调验收。",
    "mode": "standard",
    "metadata": {
      "test_case": "manual-main-flow",
      "message_seq": 1
    }
  }'
```

期望：

- HTTP `202`。
- 返回 `run_id`。
- `conversation_id` 等于当前 Conversation。
- `status` 为 `pending` 或 `running`。
- 如果 Conversation 配置了 `default_skill`，返回 `skill` 为该 Skill。

保存：

```bash
export QA_RUN_ID="<response.run_id>"
```

### 5.5 轮询 Run

```bash
curl "${DEERFLOW_BASE_URL}/api/v1/external/runs/${QA_RUN_ID}" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H "X-Request-ID: ${QA_REQUEST_ID}-poll-run"
```

期望：

- HTTP `200`。
- `status` 属于 `pending`、`running`、`completed`、`failed`、`cancelled`。
- 终态为：
  - `completed`：`answer` 可以为空或字符串；若有业务断言，只断言格式和安全性。
  - `failed`：`error` 为通用错误描述，不包含堆栈、路径、密钥、数据库信息。
  - `cancelled`：表示已取消或内部中断。

建议轮询策略：

- 间隔 2～5 秒。
- 最长等待按业务 SLA 配置，例如 2～10 分钟。
- 只对 `pending`、`running` 继续轮询。

### 5.6 取消 Run

```bash
curl -X POST "${DEERFLOW_BASE_URL}/api/v1/external/runs/${QA_RUN_ID}/cancel" \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H "X-Request-ID: ${QA_REQUEST_ID}-cancel-run"
```

期望：

- HTTP `200`。
- 非终态 Run 进入 `cancelled`，或稍后查询变为 `cancelled`。
- 如果 Run 已经处于 `completed`、`failed`、`cancelled`，接口返回当前状态，不重新执行任务。

## 6. 详细测试用例

### 6.1 鉴权与 Key 生命周期

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| AUTH-001 | 缺少 Bearer | 不带 `Authorization` 调用任意 External API | `401 missing_api_key` |
| AUTH-002 | Bearer 格式错误 | `Authorization: Bearer invalid` | `401 invalid_api_key` |
| AUTH-003 | 有效 Key | 使用有效 Key 调用 `/skills` | `200`，认证用户为 Key 所属用户 |
| AUTH-004 | Key 不能访问内部接口 | 使用 API Key 调用 `/api/models` 等内部接口 | `401`，不能绕过普通认证 |
| AUTH-005 | 旧 Key 轮换后失效 | 生成 Key A，轮换 Key B，再使用 Key A | `401 invalid_api_key` |
| AUTH-006 | 吊销后失效 | 删除当前 Key 后再调用 External API | `401 invalid_api_key` |
| AUTH-007 | 持久化不可用 | 未初始化 API Key Repository 时调用 External API | `503 external_api_unavailable` |
| AUTH-008 | API Key 不绕过管理接口 | 使用 Bearer Key 调 `/api/v1/api-keys/current` | `401`；管理接口仅接受浏览器 session |

### 6.2 平台侧 API Key 管理

这些接口只用于平台 UI 或管理端测试，不提供给外部机器系统。

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| KEY-001 | 查询无 Key | 浏览器 session 调 `GET /api/v1/api-keys/current` | `200 {"exists": false}` |
| KEY-002 | 生成 Key | `POST /api/v1/api-keys/current/rotate`，body 含 `allowed_skills` | `201`，只本次返回完整 `api_key` |
| KEY-003 | 查询当前 Key | 生成后调用 `GET /api/v1/api-keys/current` | 返回 `masked_key`，不返回完整明文 |
| KEY-004 | 更新 Skill 白名单 | `PUT /api/v1/api-keys/current/policy` | 返回排序去重后的 `allowed_skills` |
| KEY-005 | 吊销 Key | `DELETE /api/v1/api-keys/current` | `{"revoked": true}`；重复调用仍稳定返回 true |
| KEY-006 | 非 session 鉴权 | 非浏览器 session 调管理接口 | `401` |
| KEY-007 | 不存在或未启用 Skill | 轮换或更新策略时传入缺失 Skill | `422 skill_not_available` |

### 6.3 Skill 能力列表

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| SKILL-001 | 正常查询 | Key 白名单含 `sales-report` 且 Skill 已启用 | `200`，列表包含 `sales-report` |
| SKILL-002 | 白名单为空 | 使用无 `allowed_skills` 的 Key 查询 | `200`，`skills` 为空数组 |
| SKILL-003 | Agent 不允许 | 自定义 Agent 不包含该 Skill | 查询或使用时不出现该 Skill |
| SKILL-004 | 摘要字段检查 | 查询 `/skills` | 不返回内部路径、配置、源码或凭据 |
| SKILL-005 | Scope 不足 | 移除 `external:skills:read` 后查询 | `403 insufficient_scope` |

### 6.4 Conversation 创建与查询

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| CONV-001 | 最小创建 | `{}` 或仅 `source` | `201`，默认 `agent=lead_agent` |
| CONV-002 | 带外部映射创建 | 传 `source + external_conversation_id` | `201`，响应回显映射 |
| CONV-003 | 带默认 Skill 创建 | 传 `default_skill=sales-report` | `201`，响应 `default_skill` 正确 |
| CONV-004 | 幂等重放 | 相同 Key、相同 `Idempotency-Key`、相同 body 重试 | 返回第一次的 `conversation_id`，不新增会话 |
| CONV-005 | 幂等冲突 | 相同 Key、相同 `Idempotency-Key`、不同 body | `409 idempotency_conflict` |
| CONV-006 | 幂等处理中 | 同一幂等请求还未完成时并发重试 | `409 idempotency_in_progress` |
| CONV-007 | 外部映射重复 | 不带幂等 Key 重复创建相同 `source + external_conversation_id` | `409 external_conversation_exists`，`details.conversation_id` 指向已有会话 |
| CONV-008 | 非法 source | `source="../crm"` 或空白 | `422 invalid_request` |
| CONV-009 | 非法 agent | 不存在或不安全 agent 名称 | `404 agent_not_available` 或 `422 invalid_request` |
| CONV-010 | 非法默认 Skill | 不存在、未启用或不在白名单 | `404 skill_not_available` |
| CONV-011 | metadata 过大 | `metadata` 超过 32 KB | `422 invalid_request` |
| CONV-012 | 禁止内部字段 | body 带 `thread_id`、`user_id`、`config`、`context`、`connector_ids` | `422 invalid_request` |
| CONV-013 | 查询存在会话 | `GET /conversations/{conversation_id}` | `200`，不返回 `thread_id` |
| CONV-014 | 查询不存在会话 | 随机 `conversation_id` | `404 conversation_not_found` |
| CONV-015 | 用户隔离 | User B 查询 User A 的 `conversation_id` | `404 conversation_not_found` |
| CONV-016 | Scope 不足 | 移除 create/read scope | `403 insufficient_scope` |

### 6.5 Run 创建、查询和取消

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| RUN-001 | 使用默认 Skill 创建 | Conversation 有 `default_skill`，Run 不传 `skill` | `202`，响应 `skill` 为默认 Skill |
| RUN-002 | 显式 Skill 创建 | Run 传 `skill=sales-report` | `202`，使用该 Skill |
| RUN-003 | 无 Skill flash 模式 | Conversation 无默认 Skill，Run 传 `mode=flash` | `202`，可使用 flash |
| RUN-004 | Skill + flash 禁止 | 有默认 Skill 或显式 Skill 时传 `mode=flash` | `422 flash_not_available_with_skill` |
| RUN-005 | thinking/pro/ultra 模式 | 分别传 `mode=thinking/pro/ultra` | `202`，Run 可创建 |
| RUN-006 | standard 模式 | 传 `mode=standard` | `202`；内部按 pro 级计划模式执行 |
| RUN-007 | 空消息 | `message=""` 或全空白 | `422 invalid_request` |
| RUN-008 | 超长消息 | `message` 超过 200000 字符 | `422 invalid_request` |
| RUN-009 | 非法 Skill 名 | `skill="../unsafe"` | `422 invalid_request` |
| RUN-010 | Skill 不可用 | Skill 未启用或不在 Key 白名单 | `404 skill_not_available` |
| RUN-011 | metadata 过大或非 JSON | 超过 32 KB 或包含 NaN | `422 invalid_request` |
| RUN-012 | 禁止内部字段 | body 带 `agent`、`thread_id`、`context`、`config`、`connector_ids` | `422 invalid_request` |
| RUN-013 | Conversation 不存在 | 随机 `conversation_id` 创建 Run | `404 conversation_not_found` |
| RUN-014 | Conversation 已关闭 | 对 closed Conversation 创建 Run | `409 conversation_closed` |
| RUN-015 | Conversation 忙 | 内部同一 Thread 拒绝多任务 | `409 conversation_busy` |
| RUN-016 | 并发超限 | 同用户进行中 Run 数达到默认限制 3 | `429 concurrency_limit_exceeded` |
| RUN-017 | 幂等重放 | 相同 Conversation、相同 `Idempotency-Key`、相同 body | 返回第一次的 `run_id` |
| RUN-018 | 幂等冲突 | 相同 Conversation、相同 `Idempotency-Key`、不同 body | `409 idempotency_conflict` |
| RUN-019 | 幂等按 Conversation 区分 | 不同 Conversation 使用相同 body 与幂等 Key | 不应误命中其他 Conversation 的请求哈希 |
| RUN-020 | 查询 pending/running | 创建后立即查询 | `200`，状态为 `pending` 或 `running` |
| RUN-021 | 查询 completed | 等待正常完成后查询 | `200 completed`，可返回 `answer` |
| RUN-022 | 查询 failed | 模拟内部错误或超时 | `200 failed`，`error="The run failed."` 或通用描述，无内部细节 |
| RUN-023 | 查询 cancelled | 取消后查询 | `200 cancelled` |
| RUN-024 | 查询非 External Run | 用内部 Run ID 查询 | `404 run_not_found` |
| RUN-025 | 用户隔离 | User B 查询 User A 的 Run | `404 run_not_found` |
| RUN-026 | 取消非终态 Run | `POST /runs/{run_id}/cancel` | `200`，Run 最终为 `cancelled` |
| RUN-027 | 取消终态 Run | 对 completed/failed/cancelled 再 cancel | `200`，返回当前状态，不重新执行 |
| RUN-028 | Scope 不足 | 移除 create/read/cancel scope | `403 insufficient_scope` |

### 6.6 请求 ID、错误、安全与审计

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| SEC-001 | 合法请求 ID | `X-Request-ID: qa_req_12345678` | 响应头和 body 中 request_id 一致 |
| SEC-002 | 非法请求 ID | `X-Request-ID: ../unsafe` | 服务端生成 `req_<hex>`，不回显非法值 |
| SEC-003 | 请求体过大 | `Content-Length` 大于 256 KB | `413 request_too_large` |
| SEC-004 | 未处理异常 | 模拟 route 内部异常 | `500 internal_error`，不返回堆栈 |
| SEC-005 | API Key 不入日志 | User-Agent 或日志上下文中出现 `dfk_...` | 审计和安全日志应脱敏或不记录 |
| SEC-006 | 审计字段 | 完成主流程后检查 audit 表 | 记录 request_id、user_id、api_key_id、method、path_template、status_code、duration_ms |
| SEC-007 | 审计不记录正文 | 检查 audit 表 | 不包含 message、answer、Authorization、原始 API Key、metadata 明细 |
| SEC-008 | 响应不缓存 | 任意 External API 响应 | `Cache-Control: no-store` |
| SEC-009 | 错误码稳定 | 对所有异常场景检查 `error.code` | 代码稳定，message 可读但不作为程序判断依据 |

## 7. 幂等与重试专项

### 7.1 Conversation 幂等

1. 使用 `Idempotency-Key: conv-retry-001` 创建 Conversation。
2. 断开客户端或重复发送完全相同请求。
3. 验证返回同一 `conversation_id`。
4. 修改 `external_conversation_id` 或 `metadata` 后使用同一幂等 Key。
5. 验证返回 `409 idempotency_conflict`。

通过标准：

- 同一 API Key、同一幂等 Key、同一请求体只创建一次。
- 幂等命名空间按 API Key 隔离；Key 轮换后新 Key 使用新的幂等命名空间。

### 7.2 Run 幂等

1. 对同一 Conversation 使用 `Idempotency-Key: run-retry-001` 创建 Run。
2. 重试同一请求。
3. 验证返回同一 `run_id`。
4. 对相同幂等 Key 修改 `message` 或 `mode`。
5. 验证返回 `409 idempotency_conflict`。

通过标准：

- 网络超时或 5xx 重试时必须保留原 `Idempotency-Key`。
- 不允许因客户端重试创建重复 Run。
- 不同 Conversation 的请求哈希必须区分，避免错误重放。

## 8. 并发与可靠性专项

| ID | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| REL-001 | 同用户并发 Run 限制 | 同时创建超过 `active_run_limit_per_user` 个 Run | 超过部分 `429 concurrency_limit_exceeded` |
| REL-002 | 并发失败释放幂等 claim | 并发超限时携带幂等 Key | 后续可使用同 Key 正常重试，不永久卡住 |
| REL-003 | 轮询稳定性 | 连续轮询同一 Run | 不改变资源状态，不产生重复执行 |
| REL-004 | 取消与轮询竞态 | 创建 Run 后立即 cancel，同时轮询 | 最终状态为 `cancelled` 或已完成的终态，不能出现 500 |
| REL-005 | API Key 轮换期间 | 创建 Conversation 后轮换 Key，再继续查询和创建 Run | 旧 Key 401，新 Key 可访问同用户已有 Conversation |

## 9. 外部系统验收清单

外部系统上线前必须确认：

- [ ] 所有请求均使用 HTTPS。
- [ ] API Key 只保存在服务端密钥系统，不进入前端、日志、报错、埋点。
- [ ] 所有创建 Conversation / Run 的请求都使用稳定 `Idempotency-Key`。
- [ ] 网络重试、`500`、`503`、超时重试时复用原幂等 Key。
- [ ] 程序按 `error.code` 分支处理，不匹配 `message`。
- [ ] 只对 `pending`、`running` 继续轮询；终态停止轮询。
- [ ] 对 `completed` 的 `answer` 做业务侧安全校验，不直接触发付款、审批、删除、发货等高风险动作。
- [ ] 已验证 Key 轮换、吊销、Skill 白名单变更后的行为。
- [ ] 已验证并发超限和取消场景。
- [ ] 已记录 `X-Request-ID`，便于与 DeerFlow 审计日志对账。

## 10. 缺陷记录模板

| 字段 | 内容 |
| --- | --- |
| 缺陷编号 | 例如 `EXTAPI-BUG-001` |
| 测试环境 | base URL、版本、数据库类型 |
| 测试账号 | 用户、Key ID 或 masked key，不记录完整 API Key |
| 用例 ID | 例如 `RUN-017` |
| 请求 ID | `X-Request-ID` 响应头 |
| 请求摘要 | 方法、路径、脱敏后的 body |
| 实际结果 | HTTP 状态码、`error.code`、响应摘要 |
| 期望结果 | 按本手册用例填写 |
| 复现步骤 | 可重复执行的 curl 或脚本 |
| 影响范围 | 鉴权 / 幂等 / Run / 审计 / 文档等 |
| 严重程度 | Blocker / Critical / Major / Minor |

## 11. 测试完成标准

满足以下条件可认为 External API V1 测试通过：

1. 核心 External API 自动化回归全部通过。
2. OpenAPI 契约校验通过，6 个外部接口与实际路由一致。
3. 人工联调主流程从 Skill 查询到 Run 完成或取消全链路通过。
4. 鉴权、Key 轮换、幂等、异常、并发、审计、安全用例无 Blocker / Critical 缺陷。
5. 已确认平台部署满足持久化和 `EXTERNAL_API_KEY_PEPPER` 要求。
6. 已向外部系统交付接口文档、OpenAPI 规范和本测试手册。
