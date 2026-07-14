# 多租户 Agent 发布平台 — M2 代码复审

**状态：** 6 项 Spec 问题与 4 项 Standards 问题/建议已完成修复并补回归测试；独立复审追加的 4 项边界问题也已关闭

**日期：** 2026-07-14

**固定点：** `3bc06941d6bf187df8d4a4a13af07752d5afd91f`

**复审对象：** `git diff 3bc06941...HEAD`，即固定点之后的 11 个 M2 功能与 Review Gate 修复提交。

**关联规格：** [2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)

---

## 1. 本轮结论

现有 focused tests、Ruff 与 Alembic head 均通过，但不能据此判定 M2 可进入 M3。本轮只读复审发现两个可由 Agent Key 调用方触发的安全/计费阻塞：

1. Published Runtime 没有真正执行 Release 的 Skill/Connector 能力交集；运行时以 owner 身份进入 Connector policy，导致同一 Connector 上未被 Release 授予的能力也可能被放行。
2. 未提供 `Idempotency-Key` 时，调用方可重复使用同一个 `X-Request-ID` 复用 quota reservation；新 Run 仍被创建，但后续 Run 不新增配额占用且不写 usage。

另有幂等 claim/结算挂接、timeout token 结算、单 Run token 上限与 Key quota 输入验证问题。上述内容是固定点复审时的结论；本文件状态列现已记录对应修复，最终 Gate 结论以修复后的独立复审为准。

---

## 2. Spec 轴

| 严重度 | 发现 | 证据与影响 | 对应规格 | 状态 |
|---|---|---|---|---|
| P0 | Release 的 Skill/Connector/工具授权没有落到真实运行时 | `publishing/resolver.py:117-133` 从未派生 `allowed_tool_names`；`agents/lead_agent/agent.py:584-594,657` 在 published 模式清空 Skill；`tools/tools.py:264-272` 在 `allowed_tool_names is None` 时仅做黑名单。`runtime_policy.py:34-40` 只传 Connector id，不传 capability；运行时又以 owner user id 执行，而 `connectors/policy.py:92-97` 对 owner 直接放行。结果是冻结 Skill 不生效，且持 Key 的调用方可能在已选择 Connector 上调用 Release 未授权的 capability。 | 实现规格 §4 第 87–89 行；安全不变量 #3/#6 | 已修复：Resolver 从冻结 Skill 派生工具白名单，Connector policy 强制精确 capability pair |
| P0 | 调用方控制的 `X-Request-ID` 可复用 quota reservation，绕过配额并丢失 usage | `external/audit.py:23-28` 接受调用方 request id；无 `Idempotency-Key` 时 `agent_public_api.py:267-274` 将该值作为 reservation identity。`agent_usage/sql.py:111-114` 对同 key 返回既有 pending/settled reservation，但 `agent_public_api.py:438-463` 仍启动新 Run。顺序重放只计一笔 reservation，后续 settle no-op；并发重放的失败分支还可能释放另一 Run 的 reservation。 | 实现规格 §7.2 第 174 行、§12 #7；开发计划 F2.5“重试不重复计费” | 已修复：仅幂等键可稳定复用，普通请求使用服务端 UUID attempt identity |
| P1 | 幂等 claim 与结算任务存在不可恢复的孤儿窗口 | `_start_public_run()` 在 `agent_public_api.py:412-436` claim 后、进入 cleanup `try` 前调用 Resolver；404/410/Resolver 错误会留下 24h 的 in-progress claim。Run 启动后又在 `agent_public_api.py:483-492` 先 `idempotency.complete()`、后挂 settlement task；complete/serialization 失败时 Run 已执行，但 reservation 不会结算、usage 不会写入，重试也卡在 claim。 | 实现规格 §7.2 第 176 行、§8 第 186 行、§12 #7 | 已修复：Resolver 在 claim 前执行；Run 启动后先挂 settlement，再序列化/complete |
| P1 | timeout 在 worker 完成 token flush 前结算 | `agent_public_api.py:315-321` 超时后只调用 `RunManager.cancel()`，该方法在 `runtime/runs/manager.py:493-498` 取消 task 但不等待；随后 `agent_public_api.py:345-369` 立即读取 token 并 settle。最终 token 由 `runtime/runs/worker.py:891-903` 的 `finally` flush/update，因此 timeout usage 可能为 0 或部分值。 | 实现规格 §8 第 186–188 行；F2.6 精确用量 | 已修复：cancel 后 join worker task，再读取最终 token 结算 |
| P1 | `max_tokens_per_run` 只被预留，没有在 Run 中强制执行 | `publishing/quota.py:179-188` 仅把上限作为 `reserved_tokens`；公共 Run 路径只执行 input bytes 与 wall-clock timeout（`agent_public_api.py:284-318`）。代码中没有达到 token 上限后停止/取消 Run 的执行点，实际单 Run 消耗可超过平台/owner/Key 硬上限。 | 实现规格 §7.1、§10；开发计划 F2.5 `EffectiveQuota.max_tokens_per_run` | 已修复：published middleware 累计模型 usage，耗尽时终止后续图执行 |
| P2 | Owner Key API 接受非法 quota override，错误延迟到调用期变成 500 | `published_agent_keys.py:18-23,33-38` 接受任意 `dict[str, int]`，没有字段白名单与正整数校验；`publishing/quota.py:66-72` 在 Resolver 阶段才抛 `ValueError`。例如 `{"daily_runs": 0}` 可成功创建/更新 Key，之后 metadata 与 Run 请求都会在解析 context 时失败。 | 实现规格 §10“所有值必须为正整数”；Owner Key API 应只允许收紧 | 已修复：create/patch 共用字段白名单与严格正整数校验，非法输入返回 422 |

未发现实质 scope creep。

---

## 3. Standards 轴

| 严重度 | 类型 | 发现 | 依据 | 状态 |
|---|---|---|---|---|
| P1 | 硬违规 | 新仓储没有一致携带 `owner_user_id`：例如 `agent_api_key/sql.py:129-279` 仅按 `agent_id`，`agent_usage/sql.py:83-273,333-360` 的 reserve/settle/get/list 不校验 owner，`external_conversation/sql.py:109-126` 的 published lookup 也无 owner；`external_audit/sql.py:28-45` 甚至允许所有 scope 均为空。当前路由虽有部分前置校验，但仓储层不变量缺失。 | 开发计划横切安全清单第 1049 行；`backend/CLAUDE.md:265` | 已修复：Key join owner；reservation/usage、conversation、audit 在仓储层 fail closed |
| P2 | 硬违规 | `agent_usage/sql.py:64-69` 的 `now_fn` 无类型注解；`dynamic_context_middleware.py:252` 的 `__init__` 缺 `-> None`。 | `backend/CONTRIBUTING.md:144` 要求函数签名使用类型注解 | 已修复 |
| P3 | 判断项：Duplicated Code | `agent_usage/sql.py:132-178` 为 Agent scope 与 credential scope 成对复制 concurrent/daily runs/tokens/RPS 检查，后续新增规则容易只更新一边；可抽一个共享 scope evaluator。 | Fowler smell baseline；非硬违规 | 已修复：抽取 `_ScopeUsage`、`_ScopeLimits` 与共享 evaluator |
| P3 | 判断项：Data Clumps / Divergent Change | `agent_public_api.py:168-211` 反复传递 `agent_id + credential_id + conversation_id`；同一文件又同时承担 HTTP、幂等、配额、Run、结算与 SSE 编排。宜抽 scope value object 与 application orchestration service。 | Fowler smell baseline；非硬违规 | 已部分收敛：抽取 `_PublishedRequestScope`；application service 拆分留作后续非阻塞重构 |

Ruff 已覆盖的格式/静态规则不重复列为 finding。

---

## 4. 验证记录

```text
git diff --check 3bc06941...HEAD：通过
M2 focused regression：68 passed
ruff check --no-cache .：All checks passed
ruff format --no-cache --check .：679 files already formatted
Alembic heads：2026_07_14_agent_audit_principals (head)
```

第一次沙箱内运行 focused regression 时，15 个 `tmp_path` 用例因 Windows Temp ACL 在 setup 阶段报错；在获批的沙箱外临时目录重跑后为 `68 passed`，无测试断言失败。测试仍未覆盖上述 6 个 Spec 边界。

本轮没有执行 PostgreSQL 并发门禁、全仓 `make test` 或真实模型 live curl；其既有基线限制仍见实现规格 §11.2–11.3。

---

## 5. 上一轮复审历史

上一轮记录的 runtime source、Connector adapter wiring、Agent/Key 双层 quota 聚合、Conversation 唯一映射、wait/SSE/cancel 测试、文档/docstring/Ruff 问题已由 `64a0a26a` 关闭。本轮发现不是这些问题的回归，而是上一轮测试未覆盖的运行时 capability enforcement、reservation identity 与终态结算边界。

---

## 6. 修复后验证记录（2026-07-14）

本轮新增回归覆盖：冻结 Skill `allowed-tools`、Connector owner shortcut 越权、非幂等 reservation identity、Resolver/serialization 孤儿窗口、timeout token flush、单 Run token 上限、Key override 写入校验，以及 Key/quota/conversation/audit owner scope。

```text
能力策略 focused：20 passed
公共 API + Key/Resolver/Token 等可执行集合：59 passed
Agent Key repository/router：16 passed
ruff check --no-cache .：All checks passed
ruff format --no-cache --check .：679 files already formatted
git diff --check：通过
```

需要 `tmp_path` 的 quota/conversation/Connector integration 用例在当前 Windows 沙箱内仍因默认 Temp ACL 于 setup 阶段失败；本轮提权重跑又被平台用量限制拒绝，因此没有把这些 setup error 计为断言通过。最终独立复审/CI 应在可写临时目录重跑 M2 focused 与全量测试。

### 6.1 最终独立复审追加项

最终 Spec 轴复审又识别出 4 个边界并完成修复：

1. 幂等 claim 在启动前写入预分配 `run_id`，RunManager 使用该 id 创建 Run；即使响应序列化或 `idempotency.complete()` 失败，重试也通过 claim 上的 `run_id` 复用原 Run，不残留只能等待过期的 409 claim。
2. Token middleware 在每次模型调用前计算当前 Run 已用 token 与本次输入 token，并把剩余额度写入模型支持的 output-token cap；无法执行 provider 级 cap 或无法预估输入的模型 fail closed，响应后的累计检查保留为防御性校验。
3. Published audit 按 `owner_user_id + agent_id` 联合过滤；只提供公开 `agent_id` 的查询直接拒绝。
4. `/runs/wait` 的幂等重放与首次请求一样等待原 Run 的本机 task 到终态，不再因 `replayed=True` 提前返回 `pending/running`。

追加后的可执行 M2 回归集合为 `78 passed`，全仓 Ruff check、679 文件 format check 与 `git diff --check` 通过。Audit/Idempotency 的新增 owner/预绑定 Run 用例改用内存 SQLite 单独执行，不依赖受限的系统临时目录。

---

**汇总：** 原 Spec 轴 6 项、Standards 轴 4 项及最终独立复审追加的 4 项均已落地修复或收敛；Data Clumps 的 application service 拆分与 token budget middleware 职责拆分仍是非阻塞后续重构。当前代码侧 Review Gate 已关闭，完整 SQLite/PostgreSQL/全仓门禁仍由 CI 补跑。
