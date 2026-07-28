# 多租户 Agent 发布平台 - M3 飞书渠道部分第二十轮 Review 修复报告

**日期：** 2026-07-22

**关联 Review：** [2026-07-22-m3-feishu-channel-partial-code-twentieth-review.md](./2026-07-22-m3-feishu-channel-partial-code-twentieth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第二十轮列出的 2 个 P2 已完成代码、回归测试和运行时契约文档修复；本轮没有遗留的已知 P1/P2。仓库级全绿、真实 PostgreSQL 和真实双 Feishu App 仍是独立 Gate，不能据此宣称整个仓库已经 production-ready。

---

## 1. 修复结论

| Finding | 结果 | 修复 | 回归证据 |
|---|---|---|---|
| P2：Gateway 的 5 秒外层 timeout 提前取消 Supervisor 的 20 秒 ownership drain | 已修复 | 普通 lifespan hook 继续使用 5 秒上限；仅 Feishu Supervisor 使用其内部 `RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS` 加普通 hook 预算作为调度余量，生产值为 25 秒。Supervisor 仍在内部 20 秒到期时主动失败并保留 leader fence，外层不再提前取消该契约。 | 真实 Gateway lifespan 装配真实 `FeishuSupervisor`：late claim 在普通 5 秒等比例预算之后、Supervisor 预算之前提交；断言 token 清除、shutdown complete 且 fence 释放。修复前稳定得到 `_shutdown_complete=False`，修复后通过。 |
| P2：deadline 前异常/自取消的 outcome-ambiguous claim 未进入 reconciliation，普通空读还可能误判延迟可见提交 | 已修复 | `_claim_runtime_before_deadline()` 对 task 的异常和自取消结果也先登记 explicit reconciliation owner，再传播原异常/取消。Repository 新增 system-scoped `reconcile_runtime_claim()`：在单个行锁事务中与潜在 claim transaction 串行化，并只清除 exact token；Supervisor 对事务异常保留 retry owner。 | 覆盖 commit 后 deadline 前抛异常、commit 后 self-cancel、普通 Supervisor 读取返回滞后空快照、peer 正常启动、原子操作不清除不同 token、事务失败重试，以及同 token generation advance。所有用例最终清除 exact token。 |
| Standards Minor：router 测试中 Supervisor shutdown 抛错会跳过 engine disposal | 已修复 | 外层清理改为嵌套 `try/finally`，数据库 engine disposal 独立于 Supervisor shutdown 结果执行。 | router 参与 83-test 同进程聚合，命令正常退出，无 event-loop teardown 挂起。 |
| Standards Minor：`AgentChannelRepository` Divergent Change / Data Clumps | 已登记，未混改 | 本轮只增加最小、具名的原子 runtime reconciliation seam；`BindingKey`、typed row 和职责仓储拆分仍留作 M3 合并后的独立重构。 | 不影响本轮 fencing correctness；避免在 P2 修复中扩大迁移范围。 |
| Standards Important：完整 backend suite 未全绿 | 未关闭，范围外基线 | 未修改 auth 路径。 | fail-fast 为 `321 passed, 1 failed`；首个失败仍是 `test_csrf_does_not_exempt_old_login_path`。 |

---

## 2. 最终运行时不变量

### 2.1 Gateway 不再削短 Supervisor 的清理契约

- 普通 channel service、scheduler、memory rollup 和 quota drain hook 仍受 `_SHUTDOWN_HOOK_TIMEOUT_SECONDS=5` 约束。
- Feishu Supervisor 自己拥有一个 20 秒总 deadline，用于停止 janitor/runtime 并 drain late claim/reconciliation ownership。
- Gateway 对该 hook 的外层上限是 Supervisor 内部预算加 5 秒调度余量；内部失败会先发生，外层只防御 Supervisor 实现本身失去有界性。
- 内部 deadline 到期时，Supervisor 仍抛出明确错误，不设置 `_shutdown_complete`，也不释放 leader fence。

### 2.2 task done 不等于 claim 确定回滚

- request deadline、repository 异常、task self-cancel 和 caller cancellation 都可能对应“服务端已经提交、客户端没有得到确认”。
- 所有这些 outcome-ambiguous task 都会先进入 `_late_runtime_claim_tasks`，完成回调再把 exact token 交给 reconciliation owner。
- 调用方仍观察原有异常或取消语义；cleanup ownership 不会把失败伪装成启动成功。
- peer binding 不等待该 reconciliation，也不会共享 binding lifecycle lock。

### 2.3 exact-token 收敛是单个数据库事务

- `AgentChannelRepository.reconcile_runtime_claim()` 只接受 `SYSTEM_CHANNEL_SUPERVISOR_SCOPE`。
- 它按 binding id 对 runtime row 执行 `SELECT ... FOR UPDATE`；可能仍在完成的 claim transaction 必须先与该行锁串行化。
- token 不匹配时不写入；exact token 匹配时在同一事务内清除 lease、推进 generation、重置 health revision 并提交。
- Supervisor 不再用“普通 SELECT 看不到 token”作为最终收敛证据，也不再在 SELECT 与 release 之间暴露 generation race。
- 数据库/连接瞬态异常不会丢失 owner；reconciliation 按短 backoff 重试，并由 shutdown 的 20 秒总 deadline 有界 drain。

---

## 3. 主要代码变更

- `backend/app/gateway/app.py`
  - Feishu Supervisor shutdown 使用独立外层预算；其他 hook 的 5 秒边界保持不变。
- `backend/app/channels/supervisor.py`
  - deadline 前异常和 self-cancel claim 也进入 reconciliation。
  - late claim cleanup 改用 Repository 原子 exact-token seam，并对瞬态失败持续持有 retry ownership。
  - 日志统一描述为 outcome-ambiguous claim，不再误称为只发生于 deadline 之后。
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - 新增 system-scoped、row-locked `reconcile_runtime_claim()`。
- `backend/tests/test_gateway_lifespan_shutdown.py`
  - 新增真实 lifespan + 真实 Supervisor 的跨普通 hook 预算 drain 回归。
- `backend/tests/test_feishu_supervisor.py`
  - 新增 pre-deadline exception、self-cancel 和 delayed-read-visibility 回归。
  - 原 transient/generation 测试改为验证新的原子 reconciliation seam。
- `backend/tests/test_agent_channel_repo.py`
  - 新增 exact-token 原子清除与 foreign-token 保留测试。
- `backend/tests/test_agent_channels_router.py`
  - Supervisor shutdown 与 engine disposal 使用嵌套清理。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步 Gateway/Supervisor 双层 deadline、全 outcome reconciliation 和 row-lock exact-token 不变量。

---

## 4. 自动化验证

### 4.1 TDD 红绿证据

```text
Gateway lifespan：修复前 shutdown_complete=False；修复后 1 passed
deadline 前异常：修复前 runtime token 在 1 秒内未收敛；修复后 1 passed
Repository 原子 seam：修复前 AttributeError；实现后 1 passed
滞后普通读取：修复前 runtime token 在 1 秒内未收敛；改用行锁事务后 1 passed
self-cancel claim：1 passed
claim 对抗集：6 passed, 52 deselected
```

### 4.2 Repository + router + Supervisor + Gateway lifespan 同进程聚合

```text
83 passed, 1 warning in 93.67s
```

命令正常退出，没有 router / event-loop teardown 挂起。一次先导聚合曾在受载 SQLite 上让既有 non-cooperative transport 用例的 2 秒 failure-health projection 超时；该用例单独复跑通过，随后完整 83-test 聚合通过，未形成可重复的本轮回归。

### 4.3 正式 5 文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

164 passed, 5 warnings in 59.77s
```

### 4.4 16 文件 M3 focused Gate（Windows）

```text
334 passed, 9 skipped, 7 failed, 5 warnings in 148.39s
```

命令正常结束；本轮改动涉及的 Gateway lifespan、Supervisor、Repository、router、parser、WebSocket、migration、secret 和 AIO ownership 用例均通过。7 个失败均在本轮未修改的 Windows 平台边界：

- `test_channel_file_attachments.py` 2 项：当前账户没有 Windows symlink 创建权限（`WinError 1314`）。
- `test_local_sandbox_provider_mounts.py` 5 项：4 个 POSIX container path reverse mapping/roundtrip 断言，以及 1 个强制调用本机不存在 `/bin/sh` 的用例。

### 4.5 完整 backend suite（fail-fast）

Windows 环境没有 `make` 可执行文件，执行 Makefile `test` target 的等价 pytest 主体：

```text
321 passed, 1 failed, 7 warnings in 85.06s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

首个失败与第十八至第二十轮 Review 记录一致，属于本轮未修改的 auth 行为。本报告不把仓库级全绿 Gate 误报为通过。

### 4.6 静态、格式、编译与差异检查

```text
ruff check --no-cache <7 个本轮直接相关 Python 文件>: All checks passed!
ruff format --check --no-cache <同 7 个文件>: 7 files already formatted
python -m compileall <3 个本轮生产 Python 文件>: passed
git diff --check 044fa174... -- backend docs/superpowers/specs: passed
```

---

## 5. 尚未关闭的环境 / 发布 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 commit acknowledgement 丢失、异常返回、行锁串行化、exact-token reconciliation retry 和 shutdown drain 的真实事务语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 和 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`；Windows symlink 与 LocalSandbox 平台用例按其独立平台契约处理。
5. `AgentChannelRepository` 的 `BindingKey`、typed row 和职责拆分作为 M3 合并后的独立架构任务处理。

---

## 6. 最终判定

**第二十轮 Review 的 2 个 P2 已关闭；当前没有已知、仍未修复的第二十轮 M3 P1/P2。**

Gateway 现在不会用普通 hook 的 5 秒预算取消 Supervisor 的 20 秒 ownership drain；deadline 前异常、自取消和延迟可见的 claim 也不会脱离显式 cleanup ownership。exact-token 判定与清除已经下沉为行锁事务，普通空读不再被当作最终收敛证明。

就第二十轮代码修复和本地 M3 Gate 而言，可以进入下一轮复审/合并准备；真实 PostgreSQL、双 Feishu App、范围外 auth 全量失败及 Windows 平台项仍是最终生产发布 Gate。
