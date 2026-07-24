# 多租户 Agent 发布平台 - M3 飞书渠道部分第二十一轮 Review 修复报告

**日期：** 2026-07-23

**关联 Review：** [2026-07-23-m3-feishu-channel-partial-code-twenty-first-review.md](./2026-07-23-m3-feishu-channel-partial-code-twenty-first-review.md)

**开发计划：** [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第二十一轮列出的 2 个 P2 已完成代码修复、对抗回归、聚合验证和运行时契约文档更新。本轮没有遗留的已知 P1/P2。仓库级全绿、真实 PostgreSQL、真实双 Feishu App 和既有 Windows 平台项仍是独立 Gate，不能据此宣称整个仓库已经 production-ready。

---

## 1. 修复结论

| Finding | 结果 | 修复 | 回归证据 |
|---|---|---|---|
| P2：claim commit 成功但 acknowledgement 丢失后，exact token 虽被清除，启动失败 health 仍停留在 `unknown`，进程内 health 也缺失 | 已修复 | `reconcile_runtime_claim()` 返回具名 `RuntimeClaimReconciliation`，在 exact-token 行锁事务中清除 lease、推进 generation，并且仅当 expected claim generation 仍属于该失败 attempt、binding 仍 active 且没有 stop fence 时，把 `unhealthy` 原子投影到新 health epoch。Supervisor 只在重新读取的 generation/token/revision/health/detail 指纹仍与事务结果一致时更新本地 health。 | 真实 SQLite Repository + 生产 Supervisor seam 覆盖 claim commit 后抛出 acknowledgement-lost、startup reload、DB/local 均 unhealthy、exact token 清除和 peer 正常运行；另有 foreign successor 不被旧失败覆盖、generation advance 不投影旧失败的 Repository/Supervisor 回归。 |
| P2：shutdown 吞掉 quiescing runtime 的 stop/release 失败，只 drain late claim，仍错误设置 complete | 已修复 | shutdown 在同一个 20 秒总 deadline 内同时 drain late claim/reconciliation ownership 和 `_running` 中全部 quiescing stop/release owner。只有所有 ownership 集合清空后才释放 leader fence 并设置 `_shutdown_complete=True`；超时会抛出明确错误、保持 complete=false 和 fence，并允许同一 Supervisor 后续重试。 | 覆盖 stubborn stop、release 瞬态失败、release 永久失败、既有 stop-failure 契约以及真实 Gateway lifespan；断言首次未收敛时失败并保留 owner/fence，解除故障后第二次 shutdown 成功。 |
| Standards Minor：Gateway lifespan 回归的补偿 shutdown 可能跳过 engine disposal | 已修复 | 两处 lifespan 测试清理均使用嵌套 `try/finally`，即使补偿 `supervisor.shutdown()` 再次失败也会执行 `engine.dispose()`。 | Gateway lifespan 测试参与 89-test 同进程聚合并正常退出。 |
| Standards Minor：runtime lease 清除字段重复 | 已修复 | 提取只接收已加锁 row 的 `_clear_locked_runtime_lease()`；`release_runtime()`、`reconcile_runtime_claim()` 和 orphan recovery 保留各自 owner/token 条件与事务边界，只复用字段状态转换。 | Repository exact-token、successor、release、recovery 用例及聚合 Gate 通过。 |
| Standards Minor：`AgentChannelRepository` Divergent Change / Data Clumps | 已登记，未混改 | 本轮保留最小原子 seam；`BindingKey`、typed row 和职责仓储拆分继续作为 M3 合并后的独立架构任务。 | 避免在 correctness 修复中扩大数据迁移和调用面。 |
| Standards Important：完整 backend suite 未全绿 | 未关闭，范围外基线 | 未修改 auth 路径。 | fail-fast 为 `322 passed, 1 failed`；首个失败仍是 `test_csrf_does_not_exempt_old_login_path`。 |

---

## 2. 最终运行时不变量

### 2.1 ambiguous claim 的 ownership 与失败 health 一起收敛

- request timeout、repository 异常、task self-cancel 和 caller cancellation 都可能发生在数据库 commit 之后，因此仍按 outcome-ambiguous claim 管理。
- Supervisor 把 exact lease token 和预期 claim generation 一起交给 system-scoped reconciliation。
- Repository 使用 `SELECT ... FOR UPDATE` 与潜在 claim transaction 串行化。
- exact token 命中时，在同一事务中清除 token/expiry、推进 runtime generation、重置 health revision；只有该 token 的 generation 仍等于 expected claim generation，且 binding 仍 active、没有 stop request，才把启动失败写到新 epoch。
- token 不同、generation 已推进或 successor 已存在时，旧失败不得修改 successor token 或 health。
- reconciliation acknowledgement 自身丢失时，具名结果可以通过“token 已清除、generation 恰好推进一次、失败 health/detail 完全匹配”幂等识别当前 durable 结果。
- 本地 `_health` 只在 durable 写成功后更新；更新前在 binding lifecycle lock 下重新读取并核对 generation、token、health revision、health 和 detail，避免 DB commit 后出现的新 successor 被旧本地结果覆盖。
- peer binding 不等待该 binding 的 reconciliation，也不共享生命周期锁。

### 2.2 shutdown completion 等价于 ownership 全部收敛

- `_shutting_down=True` 后不再接受新 runtime admission。
- shutdown 使用一个总 deadline，先停止已知 runtime，再 drain late claim/release task，最后持续观察并推动所有 quiescing cleanup task。
- stop/release 瞬态失败保留原 `_RunningChannel`、exact lease token 和唯一 cleanup retry task；成功后才从 `_running` 删除 owner。
- deadline 到期且任一 late claim、late release 或 runtime owner 尚存时，shutdown 抛出 `RuntimeError`。
- 失败路径不设置 `_shutdown_complete`，也不释放 leader fence；解除外部故障后，同一 Supervisor 的下一次 shutdown 会继续收敛。
- 只有所有 ownership 集合都为空时，才退休 lifecycle lock、释放 leader fence 并标记 complete。

### 2.3 Gateway 清理仍可诊断

- Gateway 对 Supervisor hook 保留其内部预算加普通 hook 调度余量，不会提前取消 Supervisor 的 ownership drain。
- Supervisor 报告 quiescing owner 未收敛时，Gateway 能记录该失败；Supervisor 状态仍是 complete=false 且 fence held。
- 测试补偿清理与数据库 engine disposal 使用嵌套 `try/finally`，失败诊断不会再制造测试资源泄漏。

---

## 3. 主要代码变更

- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - 新增具名 `RuntimeClaimReconciliation`。
  - `reconcile_runtime_claim()` 接受 expected claim generation 和失败 health，原子返回 exact-token release 与 failure-health 是否仍为当前结果。
  - 提取 `_clear_locked_runtime_lease()`，统一 fenced lease 清除字段转换。
- `backend/packages/harness/deerflow/persistence/agent_channel/__init__.py`
  - 导出具名 reconciliation 结果。
- `backend/app/channels/supervisor.py`
  - ambiguous claim cleanup 使用 expected attempt generation。
  - durable reconciliation 成功后按完整指纹安全发布本地失败 health。
  - 新增 quiescing runtime ownership drain；shutdown 只有在 late ownership 和 `_running` 全部清空后才完成。
- `backend/tests/test_agent_channel_repo.py`
  - 覆盖 exact-token 原子失败投影和 foreign successor 保留。
- `backend/tests/test_feishu_supervisor.py`
  - 覆盖 acknowledgement-lost reload 的 DB/local health 与 peer 隔离。
  - 覆盖 stubborn stop、release transient/permanent failure 和 generation advance。
  - 把既有 stop-failure 用例更新为新的“失败、保留、重试成功”shutdown 契约。
- `backend/tests/test_gateway_lifespan_shutdown.py`
  - 新增真实 Repository + 真实 Supervisor 的 quiescing-owner lifespan 回归。
  - 两处补偿 shutdown 与 engine disposal 改为嵌套清理。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步 failure-health epoch、foreign-successor fencing、durable fingerprint 和完整 shutdown ownership drain 不变量。

---

## 4. 自动化验证

### 4.1 TDD 红绿证据

```text
acknowledgement-lost reload（修复前）：
durable health=unknown, local health={}
FAILED: expected durable/local unhealthy

quiescing stop failure（修复前）：
shutdown_complete=True, owner retained, fence held
FAILED: expected shutdown RuntimeError

实现后最小对抗集：
8 passed, 1 warning in 14.93s

关闭路径组合：
5 passed, 1 warning in 12.81s
```

最小对抗集同时覆盖 exact failure epoch、foreign successor、generation advance、stubborn stop、transient/permanent release 和真实 Gateway lifespan。

### 4.2 Repository + Router + Supervisor + Gateway lifespan 同进程聚合

```text
89 passed, 1 warning in 82.10s
```

首次聚合暴露一个既有 stop-failure 测试仍期望旧的“shutdown 正常返回”契约，已改为断言首次失败、保留 owner、解除故障后重试成功。另一次受载聚合显示 50ms 的第二次重试预算不足以区分 SQLite 调度延迟；失败窗口仍保持 50ms，解除故障后的重试预算调整为 1 秒。最终聚合正常退出，没有 event-loop 或 engine teardown 泄漏。

### 4.3 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

168 passed, 5 warnings in 45.36s
```

### 4.4 16 文件 M3 focused Gate（Windows）

```text
445 passed, 9 skipped, 5 failed, 6 warnings in 129.11s
```

本轮 Repository、Router、Supervisor、parser、WebSocket、AIO ownership、secret 和 migration 路径均通过。5 个失败全部位于本轮未修改的 `test_local_sandbox_provider_mounts.py` Windows 平台边界：

- 4 个 POSIX container path reverse mapping / write-read roundtrip 断言；
- 1 个强制调用当前 Windows 环境不存在的 `/bin/sh`。

### 4.5 完整 backend suite（fail-fast）

```text
322 passed, 1 failed, 7 warnings in 70.83s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

首个失败与第十八至第二十一轮 Review 基线一致，属于本轮未修改的 auth 行为。本报告不把仓库级全绿 Gate 误报为通过。

### 4.6 静态、格式与编译

```text
ruff check --no-cache <6 个本轮直接相关 Python 文件>: All checks passed!
ruff format --check --no-cache <同 6 个文件>: 6 files already formatted
python -m compileall <3 个本轮生产 Python 文件>: passed
```

---

## 5. 尚未关闭的环境 / 发布 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim commit acknowledgement 丢失、`SELECT ... FOR UPDATE` 串行化、failure-health epoch、foreign successor 和 shutdown retry 的真实事务语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 和 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`；Windows LocalSandbox 平台用例按其独立平台契约处理。
5. `AgentChannelRepository` 的 `BindingKey`、typed row 和职责拆分作为 M3 合并后的独立架构任务处理。

---

## 6. 最终判定

**第二十一轮 Review 的 2 个 P2 已关闭；当前没有已知、仍未修复的第二十一轮 M3 P1/P2。**

ambiguous claim 现在不仅会清除 exact token，还会在同一原子收敛中把启动失败写入正确的新 health epoch，并在完整 durable 指纹仍匹配时同步进程内 health；旧 attempt 不会覆盖 foreign successor。shutdown 也不再把 quiescing stop/release owner 误报为完成：未收敛时会失败、保留 leader fence 和 retry owner，解除故障后同一 Supervisor 可以再次完成。

就第二十一轮代码修复和本地 M3 Gate 而言，可以进入下一轮复审/合并准备；真实 PostgreSQL、双 Feishu App、范围外 auth 全量失败及 Windows 平台项仍是最终生产发布 Gate。
