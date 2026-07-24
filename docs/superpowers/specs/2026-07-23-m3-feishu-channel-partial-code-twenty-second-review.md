# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十二轮代码复审

**状态：** 已复审；第二十一轮 2 个 P2 已关闭，Spec 轴无新 finding；仓库级全绿 Gate 及 2 个 Standards Minor 仍未关闭
**日期：** 2026-07-23

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第二十一轮复审：[2026-07-23-m3-feishu-channel-partial-code-twenty-first-review.md](./2026-07-23-m3-feishu-channel-partial-code-twenty-first-review.md)
- 第二十一轮修复报告：[2026-07-23-m3-feishu-channel-partial-twenty-first-review-fix-report.md](./2026-07-23-m3-feishu-channel-partial-twenty-first-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后当前未提交的 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 实际 diff 命令：`git diff 044fa17489b1d064286b97ea88dee65ed08060fe -- backend`
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和历史临时目录
- Spec 来源：开发计划 M3/F3.1-F3.2/M3 Review Gate、第二十一轮 review/fix，以及 `backend/CLAUDE.md` 的 Published Feishu 运行时不变量；提交消息无 issue 引用

---

## 1. 复审结论

第二十一轮的两项 P2 已完成代码侧关闭：

- ambiguous claim reconciliation 现在返回具名 `RuntimeClaimReconciliation`，在 exact-token 行锁事务中清除 lease、推进 generation，并且只在 expected claim generation 仍属于失败 attempt 时原子投影 `unhealthy`；
- local health 发布前会在 binding lifecycle lock 下重新读取并核对 generation、token、health revision、health 和 detail，foreign successor 或更新 epoch 不会被旧失败覆盖；
- shutdown 在同一个 20 秒总预算内同时 drain late claim/release 与 `_running` 中的 quiescing transport/release owner；
- ownership 未收敛时，shutdown 抛错并保持 `_shutdown_complete=False`、leader fence held；解除故障后同一 Supervisor 可继续完成；
- 第二十一轮指出的 Gateway lifespan 测试清理和 runtime lease 字段重复均已修复；README/CLAUDE 已同步最终契约；
- 89 项聚合、168 项正式五文件 Gate、Ruff、format、compileall 和 diff check 均通过。

**Spec 轴：0 个 finding。** 未发现缺失/部分实现、scope creep 或“看似实现但行为错误”。

**Standards 轴：1 个 Important、2 个 Minor。** Important 是延续的完整 backend suite auth 失败；一个新 Minor 是 Router 测试中仍有 6 处非失败安全的 Supervisor/engine 顺序清理；另一个 Minor 是已登记的 Repository 结构债务。

**第二十一轮代码修复本身：Pass。仓库级 Ready to merge：No。** 当前没有未关闭的 M3 Spec P1/P2，但仓库强制全绿 Gate 仍失败；在该 Gate 关闭前不能声明整体可合并。

---

## 2. 第二十一轮问题关闭状态

| 第二十一轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P2：ambiguous claim 清 token 后失败 health 仍停留 `unknown` | **已关闭** | exact-token 清除与 failure health 在同一行锁事务收敛；具名结果经完整 durable fingerprint 复核后才发布到本地 health；foreign successor/generation advance 均受保护 |
| P2：shutdown 吞掉 quiescing stop/release 失败并误设 complete | **已关闭** | late claim/release 与全部 quiescing owner 在同一总 deadline 内 drain；未收敛时 complete=false/fence held，后续 shutdown 可重试 |
| Standards Minor：Gateway lifespan 补偿 shutdown 可跳过 engine disposal | **已关闭** | 两处 lifespan 测试均使用嵌套 `try/finally` |
| Standards Minor：runtime lease 清除字段重复 | **已关闭** | `_clear_locked_runtime_lease()` 统一已锁 row 的字段转换，各调用方保留自己的条件与事务边界 |
| Standards Minor：AgentChannelRepository Divergent Change / Data Clumps | **未关闭（已登记）** | 继续作为 M3 合并后的独立架构重构，不在 correctness 修复中扩大范围 |
| Standards Important：完整 suite 未全绿 | **未关闭（延续）** | 本轮 fail-fast 为 `322 passed, 1 failed`，首个失败仍是范围外 auth 用例 |

---

## 3. Spec 轴

### 3.1 ambiguous claim failure-health epoch：已关闭

**Spec 依据：**

- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L781)：单个 binding start 失败记录 `health=unhealthy`，且不影响 peer
- [开发计划 F3.2 回归要求](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L786)：startup reload 的失败 binding 必须可观测为 unhealthy
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L526)：exact-token reconciliation 必须收敛失败 epoch，同时不得覆盖 foreign successor

**复核结果：**

- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L689) 的 `reconcile_runtime_claim()` 使用 `SELECT ... FOR UPDATE`，只在 exact token 命中时清除 lease；
- `expected_claim_generation` 与实际 claim generation 匹配、binding 仍 active 且无 stop fence 时，清 lease、推进 generation 和 failure health 在同一 commit 完成；
- token 已变化、generation 已推进或 successor 已存在时，不修改 successor token/health；
- reconciliation commit acknowledgement 丢失后的重试可通过 token 已清除、generation 恰好推进一次和 health/detail 指纹识别幂等结果；
- [supervisor.py](../../../backend/app/channels/supervisor.py#L619) 持有 cleanup owner直到 durable reconciliation 和必要的 local publication 完成；
- [supervisor.py](../../../backend/app/channels/supervisor.py#L658) 在 lifecycle lock 内重新读取完整 fingerprint，只有仍匹配才更新 `_health`。

Repository exact-token/foreign-successor 回归和 Supervisor acknowledgement-lost reload/peer 隔离回归均通过。本轮未找到可使旧 attempt 覆盖 successor 或再次丢失 local health 的路径。

### 3.2 shutdown ownership completion：已关闭

**Spec 依据：**

- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L526)：所有 ownership 未收敛前不得设置 complete 或释放 leader fence；失败后必须允许同一 Supervisor 重试
- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L748)：单 binding 生命周期失败不能破坏其他 binding 或 Gateway 生命周期契约

**复核结果：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L714) 会为每个 retained `_RunningChannel` 保证唯一 cleanup retry owner，并在总 deadline 内持续观察；
- [supervisor.py](../../../backend/app/channels/supervisor.py#L2016) 先关闭 admission，再停止已知 runtime，随后依次 drain late claim/release 和 quiescing owner；
- deadline 到期时抛出明确 `RuntimeError`，不执行 lock retirement、fence release 或 `_shutdown_complete=True`；
- 解除 stop/release 故障后，下一次 shutdown 继续沿原 exact token 收敛；
- 只有 `_running` 和 late ownership 全空时才释放 leader fence并设置 complete。

stubborn stop、transient/permanent release、既有 stop-failure 以及真实 Gateway lifespan 回归均锁定了失败、保留与重试成功三个状态。本轮未发现新的 ownership 漏口。

### 3.3 Spec 轴汇总

```text
缺失或部分实现：0
Scope creep：0
实现错误：0
最严重 Spec finding：无
```

真实 PostgreSQL 和双 Feishu App 仍是 M3 发布 Gate，不属于本轮本地代码 finding，也不能由 SQLite/fake transport 结果替代。

---

## 4. Standards 轴

### 4.1 Important（延续）：仓库级完整测试 Gate 仍未全绿

[backend/CLAUDE.md](../../../backend/CLAUDE.md#L675) 要求每项 bug fix 带单测、运行完整 suite 且测试通过；[backend/CONTRIBUTING.md](../../../backend/CONTRIBUTING.md#L240) 的 Before Submitting 也要求 `uv run pytest` 全绿。

本轮重新执行完整 backend fail-fast：

```text
FAILED tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
1 failed, 322 passed, 9 warnings in 76.08s
```

失败不属于本轮 M3 diff，但仓库级硬 Gate 仍未满足。因此可以判定第二十一轮 M3 修复通过代码复审，不能判定整个工作区 standards-ready 或 Ready to merge。

### 4.2 Minor / Duplicated Code：Router 测试仍有多处非失败安全的资源清理

**相关文件：** [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py#L411)

第二十一轮已修复 slow-writer fixture 和两处 Gateway lifespan 清理，但 Router 文件中仍有 6 处重复：

```python
await supervisor.shutdown()
await engine.dispose()
```

对应当前行号为 `411-412`、`462-463`、`524-525`、`581-582`、`659-660`、`719-720`。这些调用不在覆盖整个测试主体的 `finally` 中：前置断言失败时两项清理都不执行；新 shutdown 契约抛错时 engine disposal 也会跳过。失败复现可能因此遗留 scanner/runtime task、数据库连接或 event-loop 资源，掩盖原始断言并污染同进程聚合。

建议把 Router 的 engine/repository/Supervisor 生命周期收敛到 async fixture，或至少统一使用覆盖完整测试主体的外层 `try/finally`，并在其中嵌套 shutdown/dispose。该项属于测试可靠性与 Duplicated Code smell，不影响本轮生产 Spec 判定。

### 4.3 Minor（延续）：AgentChannelRepository 仍有 Divergent Change / Data Clumps / Primitive Obsession

[sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L59) 已超过一千行并包含约 40 个方法，继续混合 secret ingest、runtime lease/health、credential rotation、cleanup 与 delete 职责。`dict[str, Any]` row 以及 binding/owner/token/generation 参数也持续成组跨方法传播。

第二十一轮使用具名 `RuntimeClaimReconciliation` 和私有 lease helper 是局部改善，但没有关闭整体 Divergent Change、Data Clumps 与 Primitive Obsession。建议 M3 合并后独立引入 `BindingKey`、typed row/result，并按 runtime/secret/delete 职责拆分仓储；不建议在当前 fencing 修复中混入大范围迁移。

README/CLAUDE 已同步最终不变量，未发现 harness → app 反向依赖或其他新的 hard violation。

### 4.4 Standards 轴汇总

```text
Important：1（完整 suite Gate，延续）
Minor：2（Router 测试清理；Repository 结构债务）
最严重 Standards finding：完整 backend suite 未全绿
```

---

## 5. 验证记录

### 5.1 Repository + Router + Supervisor + Gateway lifespan 聚合

```text
89 passed, 2 warnings in 83.91s
```

命令正常退出，没有 event-loop 或 engine teardown 挂起。第二十一轮新增的 failure-health、foreign successor、quiescing stop/release 和真实 lifespan 回归均通过。

### 5.2 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

168 passed, 6 warnings in 54.52s
```

### 5.3 完整 backend suite（fail-fast）

```text
1 failed, 322 passed, 9 warnings in 76.08s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

### 5.4 静态、格式、编译与差异检查

```text
ruff check --no-cache <6 个第二十一轮直接相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 6 个文件>
6 files already formatted

python -m compileall <3 个生产 Python 文件>
passed

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe -- backend
passed
```

pytest 仍提示当前 Windows 账户不能写既有 `.pytest_cache`，但聚合、正式 Gate 和完整 fail-fast 均实际执行；完整 suite 的 exit code 1 来自已列明的 auth 断言。

---

## 6. 尚未关闭的环境 / 发布 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim commit acknowledgement 丢失、row-lock failure-health epoch、foreign successor 和 shutdown retry 的真实事务语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 和 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`；Windows LocalSandbox 平台项继续按独立平台契约处理。
5. 在合并后的独立架构任务中拆分 `AgentChannelRepository`，不要把该迁移混入当前 fencing correctness 修复。

---

## 7. 最终判定

第二十一轮的 failure-health epoch 与 shutdown ownership 两个 P2 已正确关闭：代码、聚合回归、正式五文件和静态 Gate 均通过，Spec 轴没有新的 actionable finding。

Standards 轴仍有 1 个延续 Important 和 2 个 Minor。Router 测试清理与 Repository 结构债务不改变生产 correctness 判定，但完整 backend suite 未全绿是仓库明确的合并硬 Gate。

**第二十一轮 M3 修复代码复审：Pass。**
**仓库级 Ready to merge：No。** 在 auth 全量失败关闭并完成 PostgreSQL/双 Feishu App 发布验证前，不应把本轮代码复审通过解释为整个 M3 已完成生产验收。
