# 多租户 Agent 发布平台 — M2 代码复审

**状态：** 更新版复审的 7 项 Spec P1 与 2 项 Standards 问题已修复；最终双轴复审无剩余 blocker，代码侧 M2 Review Gate 关闭

**日期：** 2026-07-14

**固定点：** `3bc06941d6bf187df8d4a4a13af07752d5afd91f`

**复审对象：** `git diff 3bc06941...HEAD`，即固定点之后的 11 个 M2 功能与 Review Gate 修复提交。

**关联规格：** [2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)

---

## 1. 本轮结论

`8b0a5b2b..50788bbf` 的 4 个修复提交关闭了上一轮的 Connector owner shortcut、caller-controlled reservation identity、wait replay 与部分幂等/timeout 窗口，但没有关闭 M2 Review Gate。修复后二次独立复审确认：

1. 冻结 Skill 仍只有 `allowed-tools` 元数据进入运行时，`SKILL.md` 正文没有进入模型提示或可读取的冻结文件树，最终验收 #4 尚未实现。
2. Connector 的精确 capability map 已贯穿 runtime context，但 `database.table.sample` 仍错误复用 `database.query` 授权。
3. `max_tokens_per_run` 只约束 lead model 的部分调用；标题、摘要模型以及 token middleware 之后追加的 loop warning 可越过 cap。
4. Published usage 仍可被全局 `run_events.track_token_usage=false` 关闭；结算任务没有持久化重试；启动阶段取消仍可留下无 worker、无 settlement 的 pending Run。
5. 新增的 Skill revision 运行时读取没有 owner/public scope；另一个保留的 `record_usage()` 入口也没有在冲突读取时验证 owner。

因此，现有 focused tests、Ruff 与 Alembic head 通过不能解释为 M2 可进入 M3。§2–§6 保留上一轮发现与修复历史；当前阻塞项以 §7 为准。

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

**历史汇总：** 原 Spec 轴 6 项、Standards 轴 4 项及第一次修复复审追加的 4 项均已落地修复或收敛。该结论已被下面的修复后二次独立复审取代。

---

## 7. 修复后二次独立复审（HEAD `50788bbf`）

### 7.1 Spec 轴新增/未关闭项

| 严重度 | 发现 | 证据与影响 | 对应规格 | 需要的回归 |
|---|---|---|---|---|
| P1 | 冻结 Skill 正文没有进入真实运行时 | `publishing/resolver.py:156-180` 只从冻结 `SKILL.md` 解析 `allowed-tools`；`agents/lead_agent/agent.py:584-596` 在 published 模式把 `available_skills` 置空，`agents/lead_agent/prompt.py:730-734` 又移除 Skill prompt。Release 中仅正文变化的 revision 对模型行为没有任何影响，运行时也没有挂载对应 content store 文件树。 | 实现规格 §3 第 56–61 行、§4 第 87 行；开发计划最终验收 #4 | 增加 `test_acceptance_4_skill_revision_pinning`：发布后修改 live Skill，真实外部 Run 必须使用旧 revision 正文，而不只是旧 `allowed-tools`。 |
| P1 | `database.table.sample` 没有按精确 capability 授权 | `connectors/service.py:508-512` 的 sample 直接调用 `query_database()`，后者在 `:465-474` 固定校验 `database.query`；`execute_connector_action()` 在 `:534-541` 即使收到 `database.table.sample` 也走该路径。结果是 sample-only Release 被拒，query-only Release 则以错误的 capability 记账/授权，违背精确 pair 约束。 | 实现规格 §3 第 69 行、§11.5 #2；安全不变量 #3/#6 | 分别覆盖 sample-only 成功、query-only 调 sample 被拒，以及 audit capability 为 `database.table.sample`。 |
| P1 | Published 标题/摘要模型绕过 Release 模型锁定与单 Run token cap | Published 模式仍在 `agents/lead_agent/agent.py:424-445` 安装 Summarization 与 Title middleware；摘要模型由 `:218-230` 的全局配置创建，Title 在 `title_middleware.py:154-180` 直接调用全局 title/default model。这些调用不经过 `TokenUsageMiddleware.wrap_model_call()`，首轮默认开启的 Title 调用即可在 lead model 用满额度后继续消耗 token，并使用 Release 之外的模型。 | 实现规格 §4 第 87 行、§11.5 #5；`max_tokens_per_run` 硬上限 | Published 模式禁用辅助模型或让其共享 Release model 与统一 Run budget；测试首轮 Title 和触发 Summarization 时所有模型调用与总 token 均受同一 context/cap 约束。 |
| P1 | Loop warning 在 token 预检之后改写最终模型请求 | `TokenUsageMiddleware` 在 `agents/lead_agent/agent.py:438-442` 先于 `LoopDetectionMiddleware`（`:469-472`）注册；前者在 `token_usage_middleware.py:340-354` 计算输入并设置 output cap，后者却在 `loop_detection_middleware.py:560-593` 的内层 `wrap_model_call` 再追加 HumanMessage。最终请求输入大于预检值，模型可在一次调用内越过 `max_tokens_per_run`，事后检查只能停止下一步，不能撤销已消耗 token。 | 实现规格 §11.5 #5“每次模型调用前按本次输入预估收紧 cap” | 构造 pending loop warning，断言 cap 使用最终请求的输入 token；预算 middleware 应位于所有请求改写 middleware 的最内层或统一在最终发送点执行。 |
| P1 | Published usage 可被全局 run-event 开关关闭 | `runtime/runs/worker.py:614-623` 直接把 `run_events_config.track_token_usage` 传给 RunJournal；`runtime/journal.py:283-303` 在 false 时不累计 token；结算又在 `agent_public_api.py:387-389` 只读取 RunRecord 汇总。运维关闭通用 token 追踪后，外部 Run 仍执行并结算为 0 token，违反精确计费。 | 实现规格 §8 第 186–188 行；开发计划 F2.6“每个外部 Run 恰好一条精确用量” | 在 `track_token_usage=false` 下跑 Published Run，usage 仍必须记录真实 token；Published 计费开关必须 fail closed/强制开启且独立于可选观测配置。 |
| P1 | Quota settlement 不是可恢复的 exactly-once 终态流程 | `_schedule_quota_settlement()` 在 `agent_public_api.py:396-421` 只对 `ledger.settle()` 调用一次，任务仅存于进程内 set，done callback 直接丢弃；Gateway lifespan 也没有 drain/retry。瞬时数据库错误或进程退出会让 reservation 保持 pending，之后仅被 expiry 标为 released，且永远没有 `agent_usage_records`。数据库唯一约束只能防重复，不能保证至少一次。 | 实现规格 §8 第 180、186–188 行；安全不变量 #7 | 注入一次 transient settle failure 和 shutdown/restart，最终必须落一条 usage 且 reservation settled；需要有界重试加持久化恢复/outbox，而不是只依赖内存 task。 |
| P1 | Run 持久化与 worker/settlement 挂接之间仍有取消孤儿窗口 | `services.py:350-360` 先持久化 pending Run，随后在 `:369-425` 还有 await/配置工作，直到 `:427-428` 才创建并绑定 worker。若请求 task 在这段被取消，`CancelledError` 不会被 `agent_public_api.py:505-521` 的 `except Exception` 捕获，quota 与 claim 不释放，Run 无 task；幂等重试又会在 `agent_public_api.py:464-473` 直接返回该 pending Run，永远不会补挂 worker/settlement。 | 实现规格 §7.2 第 176 行、§11.5 #4；安全不变量 #7 | 在 `thread_store.get/create` await 点取消 `start_run()`，断言不会遗留 pending Run/reservation/claim；重试要么安全启动同一 Run，要么清理后重建。 |

未发现与 M2 无关的实质 scope creep。

### 7.2 Standards 轴新增/未关闭项

| 严重度 | 类型 | 发现 | 依据 | 要求 |
|---|---|---|---|---|
| P1 | 硬违规 | `PublishedAgentResolver` 新增的 `SkillRevisionRepoLike.get(revision_id)`（`publishing/resolver.py:53-56,161`）不携带 owner；实际 `persistence/skill_revision/sql.py:163-166` 也按主键裸读。运行时必须只接受 `owner_scope in {"public", owner_user_id}` 的 revision，不能仅相信 Release 外键。 | `backend/CLAUDE.md:265`；开发计划横切安全清单第 1049 行 | 给协议与仓储读取增加 `owner_user_id`，同时校验 public visibility/当前 owner，并补跨 owner private revision fail-closed 测试。 |
| P2 | 硬违规 | `AgentUsageRepository.record_usage(values)` 在 `persistence/agent_usage/sql.py:313-322` 没有显式 owner 参数；`run_id` 冲突时按全局唯一键读取并返回既有行，不验证 `values.owner_user_id`，可跨 owner 返回 usage 元数据。该入口当前主要由测试使用，但仍是公开仓储方法。 | `backend/CLAUDE.md:265,353`；开发计划横切安全清单第 1049 行 | 删除未使用入口，或要求 `owner_user_id` 并在 insert/冲突读取中联合过滤，跨 owner 返回 None/冲突而不是既有数据。 |

已有的 application orchestration service 拆分、token budget middleware 职责拆分与 capability map 解析去重仍是 P3 非阻塞重构建议，不计入 Gate blocker。

### 7.3 本次验证记录

```text
git diff --check 64a0a26a...50788bbf：通过
13 个 M2/修复相关测试文件：171 passed, 2 failed
ruff check --no-cache .：All checks passed
ruff format --no-cache --check .：679 files already formatted
Alembic heads：2026_07_14_agent_audit_principals (head)
```

两条失败均为固定点前已有的 `test_run_manager.py::test_list_by_thread` 与 `test_list_by_thread_offset`：当前 Windows 时钟会让连续 `datetime.now(UTC)` 返回相同微秒，而既有测试同时要求普通调用 newest-first、显式同时间戳时 insertion-order。相关测试与排序代码均早于 M2 固定点，本次不将其列为 M2 finding；其基线限制也已记录在实现规格 §11.2。

首次沙箱内执行时，50 个 `tmp_path` 用例因 Windows Temp ACL 在 setup 阶段报错；获批使用仓库内专用临时目录重跑后，所有这些 setup error 均消失，得到上述 `171 passed, 2 failed`。本次仍未执行 PostgreSQL 并发门禁或真实模型 live smoke。

---

**截至 `50788bbf` 的历史汇总：** 上一轮修复不是回归，但修复后二次独立复审仍有 7 项 Spec P1、1 项 Standards P1 与 1 项 Standards P2。当时代码侧 M2 Review Gate 保持打开；后续关闭记录见 §8。

---

## 8. 更新版复审修复与最终复核（2026-07-14）

### 8.1 Finding 关闭矩阵

| §7 Finding | 修复结果 | 回归证据 |
|---|---|---|
| 冻结 Skill 正文未进入运行时 | `SkillRevisionRepository.get()` 增加 owner/public scope；Resolver 从同一不可变 snapshot 同时派生 `allowed-tools` 并把完整 `SKILL.md` 组合进可信 Published 指令。 | 冻结正文沿 `PublishedAgentContext → build_published_run_config()` 进入运行配置；live Skill 新正文不会替换旧 revision。跨 owner private revision fail closed。 |
| `database.table.sample` 授权错误 | sample 使用独立执行入口，以 `database.table.sample` 做策略校验和 audit，不再复用 `database.query`。 | sample-only 成功；query-only 调 sample 拒绝；audit capability 为 sample。 |
| Title/Summarization 绕过模型与预算 | Published 模式禁用两类辅助模型。 | 测试用会抛错的 auxiliary factory，断言 Published runtime 不创建、不调用。 |
| Loop warning 晚于 token 预检 | TokenUsage middleware 移到所有请求改写 middleware 之后、Clarification 之前。 | pending loop warning 已计入最终输入；provider output cap 基于改写后的请求。 |
| Published usage 可被全局开关关闭 | worker 对 `metadata.published_agent=true` 强制 RunJournal token tracking。 | `track_token_usage=false` 下，真实 `RunJournal → RunRecord → quota settlement → agent_usage_records` 仍记录 15 token。 |
| Settlement 无重试/持久化恢复 | Quota reserve 事务在 Run 持久化前预绑定服务端 `run_id`；结算有界退避重试；shutdown 限时 drain；startup 与 30 秒周期任务通过 system recovery scope 扫描 outbox。共享数据库中超过 deadline 的非终态 orphan 按 timeout 收敛。若进程在预绑定后、Run 落库前退出，deadline 后确认 Run 缺失并释放 reservation 与 owner-scoped 未完成 claim，不伪造 usage。 | transient failure 第二次成功；真实 repository + ledger + 新 RunManager 的重启回归断言 reservation settled、usage 恰好一条；过期 active、预绑定但无 Run 两个崩溃边界均覆盖。 |
| start_run 取消孤儿窗口 | `thread_store.get/create` 取消时删除无 worker 的 durable pending Run；Agent API 捕获 `CancelledError` 并释放 quota/claim。 | RunStore 无 pending row；reservation 与 claim 各释放一次。 |
| Skill revision 无 owner/public scope | 协议与 SQL 查询均要求 `owner_user_id`，并校验 public visibility 或当前 owner。 | 跨 owner private revision 返回 None/Resolver fail closed。 |
| `record_usage()` 跨 owner 冲突泄漏 | 入口要求显式 owner，payload owner 必须一致；冲突读取联合过滤 owner。 | 同 `run_id` 的其他 owner 得到 `(None, False)`，不会拿到既有 usage。 |

持久化 outbox 的两侧崩溃窗口均已明确处理：

- reservation 预绑定发生在 Run 持久化之前，因此不存在“Run 已存在、outbox 未绑定”；
- 若“outbox 已绑定、Run 尚未存在”时退出，恢复任务只在 max-run deadline 后确认 Run 仍缺失，随后释放 quota 与 owner-scoped incomplete claim；因为没有外部 Run，不写 usage。

### 8.2 最终验证记录

```text
13 个 M2/修复相关测试文件：204 passed
Gateway lifespan / Published service wiring：4 passed
ruff check --no-cache --exclude .tmp-review .：All checks passed
ruff format --no-cache --check --exclude .tmp-review .：679 files already formatted
python -m compileall -q app packages/harness/deerflow：通过
git diff --check：通过
Alembic heads：2026_07_14_agent_audit_principals (head)
```

完整 `pytest tests -q -x` 在 `118 passed` 后被固定点前已有的 Windows ACL 问题阻断：`test_aio_sandbox_provider.py::test_acquire_async_uses_async_readiness_polling[asyncio]` 对 `.deer-flow/.../workspace` 执行 `chmod(0o777)` 时返回 `PermissionError [WinError 5]`。该失败与 M2 diff 无关。本轮仍未执行 PostgreSQL 并发 CI 门禁或真实模型 live smoke。

### 8.3 最终双轴复审

- **Standards：0 个剩余 finding。** owner/system recovery 边界、README/CLAUDE 同步、middleware 顺序与 live/recovery usage 复用均复核通过。
- **Spec：0 个剩余 blocker。** 最后识别的 Run→outbox、shared-database active orphan、prebound→missing Run 三个持久化边界均已关闭并补回归。

**最终结论：** 更新版 review 文档 §7.1–§7.2 的代码侧阻塞项全部关闭，M2 可以进入后续 Gate；生产合并仍应执行 PostgreSQL 并发测试和真实模型 live smoke。
