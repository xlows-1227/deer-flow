# 多租户 Agent 发布平台 — M2 实现规格（已发布运行时与 Agent API）

**状态：** 实现与独立代码复审修复完成，代码侧 M2 Review Gate 已关闭；仓库全量测试、PostgreSQL 并发门禁与真实模型 smoke 仍待 CI/部署环境确认

**日期：** 2026-07-14

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- M2 代码复审：[2026-07-14-m2-published-runtime-agent-api-code-review.md](./2026-07-14-m2-published-runtime-agent-api-code-review.md)

**覆盖范围：** M2 的 6 个功能项（F2.1–F2.6）：受信任发布上下文、无记忆运行策略、Agent API Key、Agent 公共 API、分层配额与预留/结算、精确用量与双主体审计。

---

## 1. 实现概述

M2 在 M1 的稳定 Agent 身份与不可变 Release 之上增加对外执行面：

- 外部请求通过绑定到单个 Agent 的 `dfa_...` Key 认证。
- `PublishedAgentResolver` 将稳定 `agent_id` 解析为创建 Run 时冻结的受信任上下文。
- Published Run 复用现有 `RunManager` / LangGraph 执行链，但强制无长期记忆、无管理工具、无调用方策略覆盖。
- Conversation、Run、幂等键与配额均按 Agent Key credential 隔离。
- 平台/owner 的 Agent 级硬限制与 Key 级收紧限制同时执行；拒绝发生在 Run 创建之前。
- 四种终态统一结算，并以 `run_id` 唯一约束写入恰好一条用量记录。
- 对外 JSON 与 SSE 使用显式白名单序列化，不暴露 Release、owner、指令、模型策略、Skill/Connector 内部信息或路径。

M2 没有替换运行时，也没有修改 M1 Release 的不可变语义。

---

## 2. 架构落点

| 职责 | 落点 | 说明 |
|---|---|---|
| 受信任上下文 | `packages/harness/deerflow/publishing/context.py` | 冻结 `PublishedAgentContext`；`memory_enabled=True` 构造即失败 |
| Agent 解析 | `packages/harness/deerflow/publishing/resolver.py` | 稳定身份 → 当前 Release → Connector/Quota 交集 |
| Published 运行策略 | `packages/harness/deerflow/publishing/runtime_policy.py` | 只从受信 context 构建 `RunnableConfig` |
| 配额策略 | `packages/harness/deerflow/publishing/quota.py` | 平台 → owner → Key 单调收紧；Ledger 门面 |
| Agent Key | `persistence/agent_api_key/` | 慢哈希、多 active Key、轮换/撤销/过期 |
| 配额与用量 | `persistence/agent_usage/` | 原子预留、幂等终态、精确用量、日聚合 |
| Conversation 隔离 | `persistence/external_conversation/` | 增加 `credential_id`，保留 External API V1 兼容 |
| 双主体审计 | `persistence/external_audit/` | owner principal 与 external actor principal 分离 |
| Owner API | `app/gateway/routers/published_agent_keys.py`、`published_agents.py` | Key 生命周期与用量查询；session + CSRF |
| Agent 公共 API | `app/gateway/routers/agent_public_api.py` | metadata / Conversation / async/wait/SSE/get/cancel |
| 认证与序列化 | `app/gateway/external/agent_auth.py`、`agent_serialization.py` | Agent Key 中间件与响应白名单 |

`packages/harness/deerflow/` 不 import `app.*`；`tests/test_harness_boundary.py` 保持通过。

---

## 3. 受信任上下文与 Resolver（F2.1）

`PublishedAgentContext` 是单个外部 Run 的冻结授权快照，包含：

- `owner_user_id`、稳定 `agent_id`、内部 `release_id`；
- `source: "api" | "feishu"`、`credential_id`、`external_actor`；
- Conversation scope、correlation/idempotency 标识；
- 固定 Skill revision id、当前有效 Connector capability、工具组、模型与组合指令；
- 生效配额；
- 常量 `memory_enabled=False`。

Resolver 流程：

1. owner-scoped 加载 Agent；draft/无指针返回不可用，suspended/archived 返回暂停。
2. owner-scoped 加载 `current_release_id` 指向的不可变 Release，并复核 `release.agent_id`。
3. 对每条 Release Connector grant，通过 `ConnectorServiceRepo` 读取当前 active Connector 与权威 Connector type capability；撤销、禁用、未知类型或能力不支持均 fail closed。
4. 用 `compose_agent_instructions()` 固定 AGENT→SOUL 指令。
5. 解析 Agent 级与 Key 级配额，组装冻结 context。

公共 API 始终向 Resolver 传 `source="api"`。为了兼容旧
`external_conversations(user_id, source, external_conversation_id)` 唯一约束，数据库映射内部使用
`source="agent-api:<credential_id>"`；该内部编码绝不进入运行时 context。

正在执行的 Run 保持创建时 Release；后续新 Run 才读取新的 current pointer。

---

## 4. 无记忆 Published 运行策略（F2.2）

`build_published_run_config(context)` 只保留 tracing 相关根字段，并用受信 context 重建 `configurable`：

- `memory_enabled=False`，不安装 `MemoryMiddleware`，不注入 `<memory>`/`USER.md`，不入 memory queue；
- `subagent_enabled=False`、`max_concurrent_subagents=0`、`is_plan_mode=False`；
- 模型、指令、Skill revision、Connector、工具组只来自 Release/context；
- `setup_agent`、`update_agent`、Skill/Connector 管理类工具不可用；
- 最终工具集合为平台白名单、Release 工具组、固定 Skill/Connector 要求的交集。

入站 body/config 中伪造的 owner、Release、model、skills、connectors、memory 或内部 `__agent_*` 字段不会进入执行授权。

---

## 5. Agent API Key（F2.3）

### 5.1 数据与安全

`agent_api_keys` 支持同一 Agent 多个 active Key。明文格式为
`dfa_<key-id>_<secret>`，只在创建/轮换响应出现一次。持久化仅保存：

- scrypt 慢哈希（含随机 salt 与部署 pepper）；
- `key_prefix` / `last_four`；
- name、status、quota overrides；
- last-used、expires、revoked、rotation lineage 时间/引用。

撤销立即生效；轮换默认让旧 Key 重叠 24 小时，可用
`AGENT_API_KEY_ROTATION_OVERLAP_SECONDS` 调整。`last_used_at` 默认按 60 秒节流更新。

### 5.2 Owner 管理 API

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/api/published-agents/{agent_id}/keys` | 创建，201，明文一次性返回 |
| GET | `/api/published-agents/{agent_id}/keys` | 安全列表，无哈希/明文 |
| PATCH | `/api/published-agents/{agent_id}/keys/{key_id}` | 改名或收紧 Key 配额 |
| POST | `/api/published-agents/{agent_id}/keys/{key_id}/rotate` | 签发 successor，201 |
| POST | `/api/published-agents/{agent_id}/keys/{key_id}/revoke` | 立即撤销 |

以上路由仅接受 owner 浏览器 session，写操作由全局 CSRF 保护。Agent Key 不能调用管理 API。

---

## 6. Agent 公共 API（F2.4）

前缀 `/api/v1/agents/{agent_id}`，认证：
`Authorization: Bearer <agent-api-key>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `` | `{agent_id, display_name, description, avatar}` |
| POST | `/conversations` | 创建 credential-scoped Conversation |
| GET | `/conversations/{cid}` | 获取 scoped Conversation |
| POST | `/conversations/{cid}/runs` | 异步创建，202 |
| POST | `/conversations/{cid}/runs/wait` | 同步等待 |
| POST | `/conversations/{cid}/runs/stream` | sanitized SSE |
| GET | `/conversations/{cid}/runs/{run_id}` | 获取 scoped Run |
| POST | `/conversations/{cid}/runs/{run_id}/cancel` | 取消并返回最新状态 |

认证/隔离语义：

- 缺失、无效、撤销、过期 Key：401。
- Key 与路径 Agent 不匹配：404。
- 未发布 Agent：404；suspended/archived：410。
- Conversation 与 Run 同时校验 `agent_id + credential_id + conversation_id`。
- 不同 Key 可以复用相同外部 Conversation id；内部 source 编码避免旧 V1 唯一约束冲突。

请求模型 `extra="forbid"`：Conversation 只接收外部 id/metadata；Run 只接收 message/metadata。同步、异步、SSE 使用不同 operation scope 的 `Idempotency-Key`；同键同请求复用原 Run，同键不同 body 返回 409。

公共状态映射：`success→completed`、`error→failed`、`interrupted→cancelled`、`timeout→timeout`。错误正文是通用信息，不返回内部异常或授权数据。

---

## 7. 配额、预留与结算（F2.5）

### 7.1 配额继承

`resolve_effective_quota()` 对每项执行单调最小值：

```text
Agent 级 = min(平台硬上限, owner/Release 覆盖或继承)
Key 级   = min(Agent 级, Key 覆盖或继承)
```

`EffectiveQuota` 同时保留 Agent-wide 与 credential-specific 聚合限额，避免“某个低限额 Key 消耗其他 Key 配额”，同时防止多个 Key 合计绕过 Agent 硬上限。

### 7.2 原子预留

`AgentUsageRepository.reserve_quota()`：

- SQLite 使用 `BEGIN IMMEDIATE`；PostgreSQL 使用 Agent id 派生的 advisory xact lock。
- 先释放超时 pending 预留，再读取当日 Agent scope 与 credential scope。
- 同时检查并发、daily runs、daily tokens、inbound rps。
- `request_key` 唯一；重复 reserve 返回原预留。
- 超限抛出带 `retry_after` 的领域错误，API 映射为 `429 + Retry-After`。
- 预留成功之后才调用 `start_run()`；启动失败释放预留并释放未完成幂等 claim。

### 7.3 终态

success/cancelled/timeout/failed 只允许 pending→settled 一次。Gateway shutdown 期间未确认终态的预留保持 pending，由下一次 reserve 的过期清理安全释放，避免错误计费。

---

## 8. 用量与双主体审计（F2.6）

`agent_usage_records` 以 `UniqueConstraint(run_id)` 保证每个外部 Run 恰好一条。结算更新与 usage insert 在同一事务中执行；重复终态回调 no-op。

记录字段包括 owner、Agent、source、credential、external actor hash、Conversation/Run、model、input/output/total tokens、latency、status、通用 error class、idempotency/correlation id 与时间。原始 external actor 不落库。

Owner 查询：

```http
GET /api/published-agents/{agent_id}/usage?days=30
```

返回 UTC 日维度 runs/tokens/status 分布与 totals，owner 不匹配返回 404。

Agent API 审计分离两类身份：

- `owner_user_id`：Agent 拥有者，用于归属与租户边界；
- `credential_id + external_actor_hash`：外部调用主体；
- `user_id` 对 Agent Key 请求保持空，避免把外部调用者伪装成 owner session。

审计仅记录 metadata；User-Agent 中的 `dfa_...` / `dfk_...` 会被脱敏。

---

## 9. 数据库迁移

M2 迁移链：

```text
2026_07_13_draft_skill_mode
  → 2026_07_14_agent_api_keys
  → 2026_07_14_agent_conversation_scope
  → 2026_07_14_agent_usage_quota
  → 2026_07_14_agent_audit_principals
```

主要变化：

- 新增 `agent_api_keys`；
- `external_conversations` 增加 `credential_id` 与 Agent/credential 索引/约束；
- 新增 `agent_quota_reservations`、`agent_usage_records`；
- `external_api_audit_logs` 增加 owner/Agent/credential/external actor hash/source 字段。

迁移对表不存在场景 fail-safe，并由 SQLite upgrade-to-head 测试覆盖；PostgreSQL 语法/并发门禁需 CI 的 PostgreSQL job 确认。

---

## 10. 配置

`config.example.yaml` 已提升到 config version 11，并增加：

```yaml
publishing:
  platform_quota:
    max_concurrent_runs_per_agent: 8
    max_input_bytes: 262144
    max_run_seconds: 600
    max_tokens_per_run: 200000
    inbound_rps: 20
    daily_runs_default: 1000
    daily_tokens_default: 2000000
```

所有值必须为正整数。owner/Key 未设置字段继承上层，不能关闭或突破平台值。

---

## 11. Review Gate 与验证记录

### 11.1 已通过

- M2 最终全功能回归：`82 passed, 1 skipped`（修复聚焦子集为 `72 passed, 1 skipped`）。
- External API V1 / 无记忆运行时 / harness boundary / threads / Run API / RunManager 扩大兼容回归：`153 passed, 10 deselected`。
- `ruff check --no-cache .`：通过。
- `ruff format --no-cache --check .`：`679 files already formatted`。
- wait、SSE、get、cancel、异步与幂等均有路由级测试；SSE 测试断言内部 Release 数据被移除。
- 成功、失败、取消、超时均覆盖幂等结算与 usage 记账。

本地跳过项为未配置 PostgreSQL 的条件测试；生产合并仍应由 PostgreSQL CI 执行迁移和并发门禁。

### 11.2 全量测试基线限制

完整 `pytest tests` 在本工作区运行到 100%，但没有全绿，输出包含多组既有配置/平台失败。首个确定性失败为：

```text
tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
1 failed, 266 passed（--maxfail=1）
```

当前实现仍在 `csrf_middleware.py` 豁免旧 `/api/v1/auth/login`，而测试要求不豁免；M2 diff 未修改该逻辑。另有两条可独立稳定复现的既有 `RunManager.list_by_thread` 同时间戳排序失败；M2 未修改 Run repository/排序实现。全量输出还包含 live client 环境变量、Windows 特权/本地认证配置相关失败。

因此本规格不声明“全仓 make test 全绿”。M2 相关测试与兼容集合已通过，但严格 Review Gate 的全量项仍需在干净基线或 CI 中确认。

### 11.3 冒烟范围

本轮没有连接真实外部模型执行 live curl。等价 ASGI 路由测试覆盖：Agent Key 认证、同步 wait、SSE、异步、get、cancel、幂等重放、撤销 Key 401、跨 Agent/credential 404、配额 429。部署环境仍应按 [backend API 文档](../../../backend/docs/API.md#published-agent-api-m2) 的 curl 示例做一次 live smoke。

### 11.4 独立复审补充（2026-07-14）

以 `3bc06941d6bf187df8d4a4a13af07752d5afd91f` 为固定点重新执行 Spec/Standards 双轴复审后，发现现有测试未覆盖的阻塞边界：

- Release 的 Skill revision、Connector capability 与工具策略没有完整进入真实运行时，Connector owner shortcut 还可能放行 Release 未授予的 capability；
- 无 `Idempotency-Key` 时，调用方可复用 `X-Request-ID` 复用 reservation，创建额外 Run 而不新增配额/usage；
- idempotency claim、Run 启动与 settlement task 挂接之间存在孤儿窗口；
- timeout 在 worker 最终 token flush 前结算，`max_tokens_per_run` 也没有运行时强制点；
- Key quota override 缺少写入期字段/正整数验证。

详细证据、严重度与 Standards 轴结果见 [M2 代码复审](./2026-07-14-m2-published-runtime-agent-api-code-review.md)。这些问题关闭并补回归测试前，不应将 §11.1 的 focused regression 通过解释为 M2 Review Gate 通过。

### 11.5 复审问题修复结果（2026-07-14）

复审列出的 6 项 Spec 问题已按本规格关闭：

1. Resolver 从 Release 固定的 Skill revision/content store 读取冻结 `SKILL.md`，按既有 Skill 语义派生 `allowed_tool_names`；快照缺失、损坏或 name 不一致均 fail closed。
2. Published runtime 将 Connector 授权以精确 `(connector_id, capability)` map 写入受信 `runtime.context`；Connector policy 在 owner shortcut 之前检查该 map，cached schema、query、generic action 与 summary 均走同一约束。
3. 未提供 `Idempotency-Key` 时，quota request key 使用服务端 UUID attempt identity，不再使用 caller 可控的 correlation/`X-Request-ID`；显式幂等请求仍保持稳定重放。
4. Agent/Release Resolver 在 idempotency claim 之前执行；claim 预先绑定 `run_id`，RunManager 使用同一 id 创建 Run；Run 启动后 settlement task 在响应序列化与 `idempotency.complete()` 之前挂接。即使序列化或 complete 失败，重试仍复用 claim 对应的原 Run，且不会留下只能等待过期的 in-progress 窗口。
5. timeout 取消后等待 worker task 的 `finally`/journal token flush 完成再结算；published graph 始终安装带 `max_tokens_per_run` 的 token middleware。每次模型调用前按当前 Run 已用量和本次输入预估收紧 provider output-token cap；不能执行 cap/预估的 provider fail closed，响应后再做累计防御性检查，达到上限时终止后续模型/工具循环。
6. Agent Key create/patch 只接受 `max_concurrent_runs`、`daily_runs`、`daily_tokens`、`max_run_seconds`、`max_tokens_per_run`、`max_input_bytes`、`inbound_rps`，且值必须为非布尔正整数，非法输入返回 422。

Standards 轴同步完成：Agent Key 管理通过 `published_agents.owner_user_id` join 校验；quota reservation 的 reserve/settle/release/get/list、published conversation lookup 与 audit list 均在仓储层携带 owner/principal scope；补齐 `now_fn` / `__init__` 类型标注；Agent/credential quota 判断抽为共享 scope evaluator；公共 API 的三元 scope 收敛为 `_PublishedRequestScope`。

修复后可在当前权限运行的测试记录为：能力策略 focused `20 passed`；公共 API、Key、Resolver、Token 等集合 `59 passed`；Agent Key repository/router `16 passed`；Ruff check、Ruff format check 与 `git diff --check` 通过。依赖 `tmp_path` 的 quota/conversation/Connector integration 在当前 Windows 沙箱因 Temp ACL 于 setup 阶段失败，提权重跑又被平台用量限制拒绝，需由 CI/可写临时目录补跑；本节不把 setup error 记为断言通过，也不据此声明全仓测试全绿。

最终独立复审追加的幂等 complete/serialization 窗口、单次模型调用越过 token cap、audit Agent 查询缺 owner 联合过滤也已关闭；追加后的可执行 M2 回归集合为 `78 passed`。新增 SQLite owner/idempotency 仓储用例使用内存数据库定向验证，不依赖受限的系统临时目录。

同步 wait 的幂等重放也与首次请求统一：只要原 Run 的本机 task 仍存在，两次请求都等待同一 task 到终态；并发同键回归断言只启动一个 Run，且两次响应均为同一 completed 结果。

---

## 12. 安全不变量

1. Agent API Key 明文只返回一次，数据库无明文。
2. caller 不能设置 owner principal 或 external actor principal。
3. caller 不能选择 Release、model、Skill、Connector、memory 或管理工具。
4. 跨 Agent、跨 credential、跨 owner 访问返回不泄露存在性的 404。
5. Release id 只存在于受信 context/结构化内部日志，不进入公共 JSON/SSE。
6. Connector 撤销/禁用即时影响新 Run，且不修改不可变 Release。
7. 被配额拒绝的请求不创建 Run；重复请求不重复 Run/计费。
8. 用量与审计只保存 external actor 哈希，不保存原始外部主体或请求内容。

---

## 13. M2 提交记录

| Commit | 功能 |
|---|---|
| `a728669a` | PublishedAgentResolver 与受信 context |
| `0a259c27` | 无记忆 Published runtime policy |
| `fac24891` | Agent-scoped API Key 生命周期 |
| `4ac57606` | credential-scoped Agent public API |
| `71468ae0` | 分层 quota 与幂等 reservation |
| `a0a3de7c` | 精确 usage 与双主体 audit |

Review Gate 修复与文档同步在上述功能提交之后完成，包含 runtime source 分离、权威 Connector adapter、Agent/Key 双层配额、Conversation source 兼容编码、完整公共流程测试、Ruff 格式化及文档补齐。
