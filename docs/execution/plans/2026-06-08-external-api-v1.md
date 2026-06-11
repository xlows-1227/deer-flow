# External API V1 开发计划

> **执行要求：** 实施时必须使用 `superpowers:executing-plans`，并严格按任务顺序逐项完成、验证和提交。

**目标：** 为业务系统提供第一版可用于生产集成的 External API，支持用户级 Bearer API Key、显式指定 Skill、持久化对话以及异步 DeerFlow Run。

**架构：** 新增带版本的 `/api/v1/external/*` 外部接口层，通过 Bearer API Key 识别现有 DeerFlow 用户，校验 Agent 与 Skill 权限，并复用现有 `start_run()`、`RunManager` 和 `RunStore` 执行任务。API Key、外部对话与内部 Thread 的映射、幂等记录和审计事件持久化到 DeerFlow 应用数据库；内部 LangGraph 请求模型、Thread ID 和原始事件不向外暴露。

**技术栈：** Python 3.12、FastAPI/Starlette 中间件、Pydantic、SQLAlchemy 异步 ORM、SQLite/Postgres、现有 DeerFlow `RunManager`、`RunStore`、Checkpointer、Skill 存储、Agent 配置、Connector 授权、pytest。

---

## 1. 第一版范围

第一版包含：

- 每个 DeerFlow 用户最多拥有一把有效 Bearer API Key。
- 使用浏览器会话认证和 CSRF 保护的 API Key 创建、轮换、查询、吊销和策略管理接口。
- 使用服务端稳定 Pepper 进行 API Key HMAC-SHA256 哈希。
- 每把 API Key 配置显式的 `allowed_skills` 白名单。
- External API 认证成功后映射到现有 `User`。
- 仅在 External API Key 认证成功后免除 CSRF 校验。
- 查询当前 API Key 可调用的 Skill 摘要。
- 创建和查询外部对话。
- 对话可配置可选的 `default_skill`。
- 将公开 `conversation_id` 映射到内部 `thread_id`。
- 对话创建和异步 Run 创建支持 `Idempotency-Key`。
- 在指定对话中创建异步 Run。
- 单次 Run 可以覆盖指定一个 Skill。
- 查询和取消 External Run。
- 限制每个用户的活跃 External Run 数量。
- 记录 External API 审计事件和请求 ID。
- 覆盖安全、隔离、持久化、中间件顺序和端到端测试。

第一版不包含：

- 同步消息接口。
- SSE 事件流。
- 历史消息分页。
- 生成物下载。
- 文件上传。
- Webhook 回调。
- 前端 API Key 设置页面。
- 每用户多把有效 API Key。
- 单次 Run 指定多个 Skill。
- API Key 访问现有管理接口。
- 在 `database.backend=memory` 模式下使用 External API。

## 2. 已锁定实现决策

1. API Key 按需生成，不在用户注册时自动生成。
2. 每个用户最多拥有一把有效 API Key。
3. API Key 明文只在创建或轮换时返回一次。
4. Secret 使用 `HMAC-SHA256(pepper, secret)` 存储。
5. `allowed_skills` 必须显式配置，默认值为空列表。
6. API Key 管理接口必须使用现有 Cookie JWT 认证和 CSRF 校验。
7. API Key 只能认证 `/api/v1/external/*`。
8. 没有 SQL Session Factory 时，External API V1 不可用并返回明确错误。
9. Conversation 归属 `user_id`，不归属 `api_key_id`，轮换 Key 后仍可访问历史对话。
10. Conversation 支持可选 `default_skill`。
11. 请求中的 `skill` 仅覆盖当前 Run 的 Conversation 默认 Skill。
12. 被选 Skill 必须同时满足：已启用、在 API Key 白名单内、被所选 Agent 允许、当前用户可访问。
13. Skill 选择互斥：Prompt 中只注入最终选中的一个 Skill。
14. Skill 的 `allowed-tools` 与 Connector 授权只能缩小权限，不能扩大权限。
15. 指定 Skill 的 Run 禁止使用 `mode=flash`。
16. 一个 Conversation 同时只允许一个活跃 Run，冲突返回 `409 conversation_busy`。
17. 每用户 External Run 默认并发上限为 3。
18. External API V1 只提供异步 Run 创建、查询和取消。

## 3. External API V1 接口

使用浏览器会话认证并进行 CSRF 保护：

```http
GET    /api/v1/api-keys/current
POST   /api/v1/api-keys/current/rotate
PUT    /api/v1/api-keys/current/policy
DELETE /api/v1/api-keys/current
```

使用 Bearer API Key 认证：

```http
GET  /api/v1/external/skills
POST /api/v1/external/conversations
GET  /api/v1/external/conversations/{conversation_id}
POST /api/v1/external/conversations/{conversation_id}/runs
GET  /api/v1/external/runs/{run_id}
POST /api/v1/external/runs/{run_id}/cancel
```

## 4. 建议后端目录

新增：

```text
backend/app/gateway/api_key_auth.py
backend/app/gateway/external/
  __init__.py
  audit.py
  config.py
  errors.py
  models.py
  service.py
  skill_policy.py
  status.py
backend/app/gateway/routers/api_keys.py
backend/app/gateway/routers/external.py
backend/app/gateway/thread_service.py

backend/packages/harness/deerflow/persistence/api_key/
  __init__.py
  model.py
  sql.py
backend/packages/harness/deerflow/persistence/external_conversation/
  __init__.py
  model.py
  sql.py
backend/packages/harness/deerflow/persistence/external_idempotency/
  __init__.py
  model.py
  sql.py
backend/packages/harness/deerflow/persistence/external_audit/
  __init__.py
  model.py
  sql.py
```

主要修改：

```text
backend/app/gateway/app.py
backend/app/gateway/auth_middleware.py
backend/app/gateway/csrf_middleware.py
backend/app/gateway/deps.py
backend/app/gateway/services.py
backend/app/gateway/routers/threads.py
backend/packages/harness/deerflow/agents/lead_agent/agent.py
backend/packages/harness/deerflow/runtime/runs/worker.py
backend/packages/harness/deerflow/runtime/runs/store/base.py
backend/packages/harness/deerflow/runtime/runs/store/memory.py
backend/packages/harness/deerflow/persistence/models/__init__.py
backend/packages/harness/deerflow/persistence/run/sql.py
backend/CLAUDE.md
.env.example
```

## 5. 实施任务

### 任务 1：定义 External API 领域模型和稳定错误契约

**涉及文件：**

- 新增：`backend/app/gateway/external/__init__.py`
- 新增：`backend/app/gateway/external/models.py`
- 新增：`backend/app/gateway/external/errors.py`
- 新增：`backend/app/gateway/external/status.py`
- 测试：`backend/tests/test_external_api_models.py`

**步骤：**

1. 先编写失败测试，覆盖 API Key 和 Skill 名称格式、请求字段白名单、额外字段拒绝、元数据大小限制、外部状态映射和统一错误结构。
2. 运行 `uv run pytest tests/test_external_api_models.py -q`，确认因模型不存在而失败。
3. 使用 Pydantic `extra="forbid"` 实现最小请求与响应模型；元数据最多 32 KB，只接受 JSON 类型；所有外部响应禁止出现内部 `thread_id`。
4. 定义携带 `code`、`message`、`status_code` 和可选 `details` 的 `ExternalAPIError`。
5. 再次运行测试并确认通过。
6. 提交：`git commit -m "feat: define external api contracts"`。

统一错误示例：

```json
{
  "error": {
    "code": "skill_not_available",
    "message": "The requested skill is not available.",
    "request_id": "req_x"
  }
}
```

### 任务 2：新增 External API 持久化模型和仓储

**涉及文件：**

- 新增：`backend/packages/harness/deerflow/persistence/api_key/*`
- 新增：`backend/packages/harness/deerflow/persistence/external_conversation/*`
- 新增：`backend/packages/harness/deerflow/persistence/external_idempotency/*`
- 新增：`backend/packages/harness/deerflow/persistence/external_audit/*`
- 修改：`backend/packages/harness/deerflow/persistence/models/__init__.py`
- 测试：`backend/tests/test_api_key_repository.py`
- 测试：`backend/tests/test_external_conversation_repository.py`
- 测试：`backend/tests/test_external_idempotency_repository.py`
- 测试：`backend/tests/test_external_audit_repository.py`

**步骤：**

1. 编写仓储失败测试。
2. API Key 测试覆盖事务内轮换、有效性判断、吊销与过期、策略更新、用户隔离和数据库不存明文。
3. Conversation 测试覆盖公开 ID 与内部 Thread ID 持久化、用户隔离、外部业务 ID 映射冲突和 Key 轮换后归属不变。
4. 幂等测试覆盖相同请求返回原响应、不同请求体冲突和过期记录忽略。
5. 审计测试覆盖追加元数据、禁止存储请求响应正文和按用户、Key 查询。
6. 运行仓储测试并确认失败。
7. 实现 `api_keys`、`external_conversations`、`external_idempotency_keys` 和 `external_api_audit_logs` ORM 模型及仓储。
8. 仓储统一接收 `async_sessionmaker[AsyncSession]`，所有查询必须显式包含所有者条件。
9. 在 `deerflow.persistence.models.__init__` 注册模型，确保 `Base.metadata.create_all()` 能创建表。
10. 运行仓储测试及现有持久化回归测试并确认通过。
11. 提交：`git commit -m "feat: persist external api resources"`。

验证命令：

```bash
uv run pytest tests/test_api_key_repository.py tests/test_external_conversation_repository.py tests/test_external_idempotency_repository.py tests/test_external_audit_repository.py -q
uv run pytest tests/test_persistence_scaffold.py tests/test_run_repository.py tests/test_thread_meta_repo.py -q
```

### 任务 3：实现 API Key 配置、生成、哈希和生命周期服务

**涉及文件：**

- 新增：`backend/app/gateway/external/config.py`
- 新增：`backend/app/gateway/external/service.py`
- 修改：`.env.example`
- 测试：`backend/tests/test_api_key_service.py`

**步骤：**

1. 编写失败测试，覆盖 Key 格式、至少 256 位随机性、解析、错误格式、HMAC 稳定性、常量时间比较、明文只返回一次、轮换、幂等吊销、Skill 白名单去重排序和 Pepper 缺失时关闭服务。
2. 使用独立配置 `EXTERNAL_API_KEY_PEPPER`，开发环境可持久化到 `{DEER_FLOW_HOME}/.external_api_key_pepper`。
3. Pepper 读写失败时中止 API Key 服务启动，并输出可操作但不泄露 Pepper 的错误。
4. Key 生成和 HMAC 计算放在服务层；仓储只接收 Key ID、用户 ID、Secret Hash、前缀、末四位、Scope 和 Skill 白名单。
5. 运行 `uv run pytest tests/test_api_key_service.py -q` 并确认通过。
6. 提交：`git commit -m "feat: add api key lifecycle service"`。

### 任务 4：初始化 External API 仓储和依赖获取器

**涉及文件：**

- 修改：`backend/app/gateway/deps.py`
- 测试：`backend/tests/test_external_api_dependencies.py`

**步骤：**

1. 编写失败测试，覆盖 SQL 模式初始化仓储、内存模式禁用 External API、依赖获取成功和缺失时返回 `503`。
2. 在 `langgraph_runtime()` 中仅当存在 SQL Session Factory 时初始化 External API 仓储。
3. 新增 `get_api_key_repo`、`get_external_conversation_repo`、`get_external_idempotency_repo` 和 `get_external_audit_repo`。
4. 不提供内存版 API Key 存储。
5. 运行依赖测试和运行时生命周期回归测试。
6. 提交：`git commit -m "feat: bootstrap external api repositories"`。

### 任务 5：新增 Bearer API Key 认证并修正 CSRF 中间件顺序

**涉及文件：**

- 新增：`backend/app/gateway/api_key_auth.py`
- 修改：`backend/app/gateway/auth_middleware.py`
- 修改：`backend/app/gateway/csrf_middleware.py`
- 修改：`backend/app/gateway/app.py`
- 测试：`backend/tests/test_external_api_auth.py`
- 测试：`backend/tests/test_auth_middleware.py`
- 测试：`backend/tests/test_csrf_middleware.py`

**步骤：**

1. 使用真实中间件栈编写失败测试，覆盖缺失、格式错误、未知、Secret 错误、已吊销、已过期和有效 Key。
2. 确认认证失败不会泄露 Key ID 是否存在。
3. 有效 Key 必须写入 `request.state.user`、`request.state.auth`、`request.state.auth_method`、`request.state.api_key_id`、Scope 和 Skill 白名单。
4. 实现 `ExternalAPIAuthMiddleware`：只处理 `/api/v1/external/`，验证 Bearer Key，加载真实用户，写入认证上下文，并设置或清理现有用户 ContextVar。
5. 调整中间件注册顺序，确保 CSRF 中间件能看到成功认证结果，并添加顺序回归测试。
6. `CSRFMiddleware` 只能在 `request.state.auth_method == "api_key"` 时跳过 CSRF；不能因为存在任意 `Authorization` Header 就跳过。
7. 不把 External API 加入公共路径前缀。
8. 运行认证和 CSRF 测试。
9. 提交：`git commit -m "feat: authenticate external bearer api keys"`。

验证命令：

```bash
uv run pytest tests/test_external_api_auth.py tests/test_auth_middleware.py tests/test_csrf_middleware.py -q
```

### 任务 6：新增使用浏览器认证的 API Key 管理接口

**涉及文件：**

- 新增：`backend/app/gateway/routers/api_keys.py`
- 修改：`backend/app/gateway/app.py`
- 测试：`backend/tests/test_api_keys_router.py`

**步骤：**

1. 编写失败测试，覆盖查询脱敏元数据、轮换时仅返回一次明文、旧 Key 失效、策略更新不轮换 Secret、幂等吊销、CSRF、用户隔离以及 API Key 无法调用管理接口。
2. 实现管理 Router，复用浏览器认证上下文和 API Key 生命周期服务。
3. SQL 持久化不可用时返回 `503`。
4. 响应不得暴露 Secret Hash、Pepper、用户 ID 和仓储内部字段。
5. 将 Router 注册到 `create_app()`，但必须放在 `/api/v1/external/*` 之外。
6. 运行测试并提交：`git commit -m "feat: manage user external api keys"`。

### 任务 7：修复强制指定 Skill 时的运行时一致性

**涉及文件：**

- 修改：`backend/packages/harness/deerflow/agents/lead_agent/agent.py`
- 修改：`backend/packages/harness/deerflow/runtime/runs/worker.py`
- 测试：`backend/tests/test_lead_agent_skills.py`
- 测试：`backend/tests/test_external_skill_runtime.py`

**步骤：**

1. 编写失败测试，覆盖强制 Skill 后的 Prompt、Tool 过滤、元数据、Connector Runtime Context 和 Flash Direct Path。
2. 新增唯一的最终 Skill 解析逻辑，优先级为：强制 Skill、Agent 配置 Skill、全部已启用 Skill。
3. Prompt、Tool 策略、元数据、Agent Graph 缓存键和 Connector Runtime Context 必须使用同一最终结果。
4. 强制 Skill 不存在、未启用或不被 Agent 允许时，在模型执行前关闭失败。
5. `_should_use_flash_direct_path()` 在配置包含 `skill_name` 时返回 `False`。
6. 运行测试并确认通过。
7. 提交：`git commit -m "fix: make forced skill runtime consistent"`。

验证命令：

```bash
uv run pytest tests/test_lead_agent_skills.py tests/test_external_skill_runtime.py tests/test_worker_langfuse_metadata.py -q
```

### 任务 8：实现 External Skill 权限策略和 Skill 摘要接口

**涉及文件：**

- 新增：`backend/app/gateway/external/skill_policy.py`
- 新增：`backend/app/gateway/routers/external.py`
- 修改：`backend/app/gateway/app.py`
- 测试：`backend/tests/test_external_skill_policy.py`
- 测试：`backend/tests/test_external_skills_router.py`

**步骤：**

1. 编写策略失败测试。
2. 可用 Skill 必须是已启用 Skill、Key `allowed_skills`、Agent 允许 Skill 和用户可访问 Skill 的交集。
3. `Agent.skills=[]` 表示不允许任何 Skill；`Agent.skills=None` 表示允许已启用 Skill，但仍受 Key 白名单限制。
4. 缺失、禁用、未授权和 Agent 禁止的 Skill 都返回相同的 `404 skill_not_available`。
5. 指定 Skill 时拒绝 `mode=flash`；Connector 权限只从服务端现有策略读取。
6. 编写 `GET /api/v1/external/skills` 失败测试，确保只返回摘要，不返回路径、完整内容、支持文件或 `allowed-tools`。
7. 在 `skill_policy.py` 集中实现校验，Router 只负责请求响应映射。
8. 运行测试并提交：`git commit -m "feat: expose authorized external skills"`。

### 任务 9：提取可复用 Thread 创建逻辑并新增 Conversation 服务

**涉及文件：**

- 新增：`backend/app/gateway/thread_service.py`
- 修改：`backend/app/gateway/routers/threads.py`
- 修改：`backend/app/gateway/external/service.py`
- 测试：`backend/tests/test_thread_service.py`
- 测试：`backend/tests/test_external_conversation_service.py`

**步骤：**

1. 为 `create_empty_thread()` 编写失败测试，覆盖自动生成 Thread ID、创建元数据和空 Checkpoint、显式 ID 幂等以及现有 `/api/threads` 行为不变。
2. 仅提取可复用创建逻辑，不修改无关 Thread 路由。
3. 编写 Conversation 服务失败测试，覆盖公开 ID、内部 Thread、外部业务 ID 冲突、用户隔离、默认 Skill 校验和 Key 轮换后可访问。
4. Conversation 创建与内部 Thread 创建视为一个逻辑操作。
5. Conversation 持久化失败时，补偿删除本次新建的空 Thread 和 Checkpoint；重试时不得删除既有 Thread。
6. 运行测试并提交：`git commit -m "feat: add external conversation service"`。

### 任务 10：新增 Conversation 创建和查询接口

**涉及文件：**

- 修改：`backend/app/gateway/routers/external.py`
- 测试：`backend/tests/test_external_conversations_router.py`
- 测试：`backend/tests/test_external_api_security.py`

**步骤：**

1. 编写失败测试，覆盖新建 Conversation、响应字段、内部 Thread ID 不可见、请求身份字段拒绝、外部 ID 冲突、幂等、Scope 和所有者隔离。
2. 只使用 `request.state` 中的 API Key 用户身份，禁止请求体传入用户身份或内部 ID。
3. 内部 Thread 只保存服务端控制的外部会话 ID 和来源。
4. 幂等处理顺序：对验证后的请求体计算哈希、相同重试返回原响应、不同请求体拒绝、成功响应落库后再返回。
5. 运行测试并提交：`git commit -m "feat: expose external conversations"`。

### 任务 11：新增每用户活跃 Run 计数

**涉及文件：**

- 修改：`backend/packages/harness/deerflow/runtime/runs/store/base.py`
- 修改：`backend/packages/harness/deerflow/runtime/runs/store/memory.py`
- 修改：`backend/packages/harness/deerflow/persistence/run/sql.py`
- 测试：`backend/tests/test_run_repository.py`
- 测试：`backend/tests/test_run_manager.py`

**步骤：**

1. 为 `count_inflight_by_user(user_id: str) -> int` 编写失败测试。
2. 内存和 SQL 实现只统计指定用户状态为 `pending`、`running` 的 Run。
3. 完成、失败、中断和其他用户的 Run 不计入。
4. SQL 实现使用 `COUNT(*)`，禁止加载全部活跃记录后再计数。
5. 更新实现了 `RunStore` 的测试替身。
6. 运行测试并提交：`git commit -m "feat: count active runs per user"`。

### 任务 12：新增 External 异步 Run 创建、查询和取消接口

**涉及文件：**

- 修改：`backend/app/gateway/external/service.py`
- 修改：`backend/app/gateway/routers/external.py`
- 修改：`backend/app/gateway/services.py`
- 测试：`backend/tests/test_external_runs_router.py`
- 测试：`backend/tests/test_external_api_security.py`

**步骤：**

1. 编写 Run 创建失败测试，覆盖 Conversation 内创建、幂等、Skill 优先级、服务端元数据、Flash 拒绝、关闭对话、所有者隔离、对话繁忙和用户并发上限。
2. Skill 优先级固定为 `request.skill > conversation.default_skill > 不强制指定 Skill`。
3. 客户端不能传入任意 `config`、`context`、Agent 变更、Tool 选择或 Connector 授权。
4. 编写 Run 查询和取消失败测试，覆盖稳定外部状态、公开 Conversation ID、最终答案、所有者隔离和幂等取消。
5. External Run 服务验证完外部请求后才构造内部 `RunCreateRequest`。
6. 强制设置 Conversation Agent、`multitask_strategy="reject"`、`on_disconnect="continue"`、受控 `stream_mode`、最终 `skill_name` 和服务端元数据。
7. 复用现有 `start_run()` 和 `RunManager`，禁止复制 Worker 执行逻辑。
8. 启动 Run 前预占幂等记录；启动成功后关联 `run_id`；启动前失败时释放或标记失败，允许修正后重试。
9. 添加并发测试，证明两个相同并发请求只创建一个 Run。
10. 映射冲突、并发限制、内部状态和所有者不匹配错误。
11. 运行测试并提交：`git commit -m "feat: run deerflow through external conversations"`。

验证命令：

```bash
uv run pytest tests/test_external_runs_router.py tests/test_external_api_security.py tests/test_runs_api_endpoints.py tests/test_run_manager.py -q
```

### 任务 13：新增 External API 审计和请求 ID

**涉及文件：**

- 新增：`backend/app/gateway/external/audit.py`
- 修改：`backend/app/gateway/app.py`
- 修改：`backend/app/gateway/routers/api_keys.py`
- 修改：`backend/app/gateway/routers/external.py`
- 测试：`backend/tests/test_external_api_audit.py`

**步骤：**

1. 编写失败测试，确保每个 External 请求返回 `X-Request-ID`。
2. 仅当客户端提供的请求 ID 满足严格安全格式时保留，否则生成新 ID。
3. 审计记录包含请求 ID、用户 ID、API Key ID、动作、资源、状态、耗时和 Skill。
4. API Key 轮换、吊销和策略更新写入安全审计事件。
5. 审计记录禁止包含 Authorization Header、API Key 明文、消息正文、模型答案、Skill 内容和凭据。
6. 审计写入失败时记录错误，但不能向调用方暴露内部细节。
7. 使用窄范围 External 路径中间件或显式服务包装实现审计。
8. 运行测试并提交：`git commit -m "feat: audit external api usage"`。

### 任务 14：完成 Router 注册、运维文档和端到端覆盖

**涉及文件：**

- 修改：`backend/app/gateway/app.py`
- 修改：`backend/CLAUDE.md`
- 修改：`.env.example`
- 修改：`docs/design/2026-06-08-external-api-bearer-key-design.md`
- 新增并测试：`backend/tests/test_external_api_e2e.py`

**步骤：**

1. 使用真实 FastAPI 应用、临时 SQLite 持久化和模拟 Agent 执行编写端到端失败测试。
2. 端到端流程覆盖：用户登录、轮换 Key、旧 Key 失效、Skill 查询、创建 Conversation、创建异步 Run、检查内部元数据、查询状态、取消、用户隔离、管理接口隔离和轮换后继续访问历史对话。
3. 完成应用 Router 注册。
4. 文档补充 `EXTERNAL_API_KEY_PEPPER`、内存模式不可用、Key 生命周期、接口与 curl 示例、Skill 语义、确定性业务操作警告和默认并发限制。
5. 设计决策评审确认后，将设计文档状态从 `Proposed` 更新为 `Accepted`。
6. 运行 External API 定向测试、相关回归测试、全量后端测试和 lint/format 检查。
7. 提交：`git commit -m "docs: finalize external api v1"`。

定向 External API 测试：

```bash
uv run pytest tests/test_external_api_models.py tests/test_api_key_repository.py tests/test_external_conversation_repository.py tests/test_external_idempotency_repository.py tests/test_external_audit_repository.py tests/test_api_key_service.py tests/test_external_api_dependencies.py tests/test_external_api_auth.py tests/test_api_keys_router.py tests/test_external_skill_runtime.py tests/test_external_skill_policy.py tests/test_external_skills_router.py tests/test_external_conversation_service.py tests/test_external_conversations_router.py tests/test_external_runs_router.py tests/test_external_api_audit.py tests/test_external_api_security.py tests/test_external_api_e2e.py -q
```

相关回归测试：

```bash
uv run pytest tests/test_auth.py tests/test_auth_middleware.py tests/test_csrf_middleware.py tests/test_threads_router.py tests/test_runs_api_endpoints.py tests/test_run_manager.py tests/test_run_repository.py tests/test_runtime_lifecycle_e2e.py tests/test_lead_agent_skills.py tests/test_connectors_policy.py tests/test_connectors_service.py -q
```

全量验证：

```bash
uv run pytest tests -q
uv run ruff check app packages tests
uv run ruff format --check app packages tests
```

## 6. 验收清单

### 认证和 API Key 生命周期

- [ ] 用户可以生成、查询、轮换、更新策略和吊销一把 API Key。
- [ ] API Key 明文只在创建或轮换时返回。
- [ ] 数据库不包含 API Key 或 Secret 明文。
- [ ] 轮换后旧 Key 立即失效。
- [ ] API Key 无法认证非 External Router。
- [ ] External POST 请求只有在 API Key 认证成功后才能免 CSRF。

### Skill 权限

- [ ] 业务系统可以查询当前 Key 允许调用的 Skill。
- [ ] 业务系统可以为每个 Run 指定一个 Skill。
- [ ] 请求 Skill 仅覆盖当前 Run 的 Conversation 默认 Skill。
- [ ] 未授权、已禁用和不存在的 Skill 返回不可区分的 404。
- [ ] 被选 Skill 无法扩大 Agent Tool 或 Connector 权限。
- [ ] Prompt、Tool、元数据、Connector 上下文和缓存键使用同一最终 Skill。
- [ ] 指定 Skill 后禁用 Flash Direct Path。

### Conversation 和 Run

- [ ] 创建 Conversation 会创建新的内部 Thread。
- [ ] 公开 API 永不暴露内部 Thread ID。
- [ ] API Key 轮换后仍可访问原 Conversation。
- [ ] Run 只能在当前用户拥有且有效的 Conversation 中创建。
- [ ] Conversation 和 Run 创建请求重试不会产生重复记录。
- [ ] 一个 Conversation 同时只允许一个活跃 Run。
- [ ] 每用户活跃 External Run 上限生效。
- [ ] Run 查询和取消执行所有者隔离。
- [ ] 外部状态稳定，不依赖内部状态名称。

### 安全和运维

- [ ] 没有 SQL 持久化时 External API 关闭失败。
- [ ] External API 错误不返回堆栈和内部路径。
- [ ] 审计只包含元数据，不包含 Key、Prompt、消息、答案或凭据。
- [ ] 每个 External API 响应包含请求 ID。
- [ ] 定向测试、回归测试和全量后端测试均通过。

## 7. 后续独立计划

V1 稳定后，另行制定计划实现：

- 同步 Conversation 消息接口。
- SSE 事件过滤和可恢复事件流。
- 历史消息分页。
- 生成物 ID 和下载。
- 文件上传。
- 带签名和可靠投递的 Webhook。
- 多 API Key 和自定义 Scope。
- 前端 API Key 与 Skill 策略设置页面。
