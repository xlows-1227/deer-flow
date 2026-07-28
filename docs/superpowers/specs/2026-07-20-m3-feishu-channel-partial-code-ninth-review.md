# 多租户 Agent 发布平台 - M3 第九轮代码复审

**状态：** 已复审，待修复
**日期：** 2026-07-20

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第八轮代码复审：[2026-07-17-m3-feishu-channel-partial-code-eighth-review.md](./2026-07-17-m3-feishu-channel-partial-code-eighth-review.md)
- 第八轮修复报告：[2026-07-17-m3-feishu-channel-partial-eighth-review-fix-report.md](./2026-07-17-m3-feishu-channel-partial-eighth-review-fix-report.md)
- 报告结构参考：[2026-07-13-m1-agent-control-plane-code-seventh-review.md](./2026-07-13-m1-agent-control-plane-code-seventh-review.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第九轮固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 本轮实现是固定点之后尚未提交的工作区差异：14 个已跟踪 backend 文件，以及新增迁移 `2026_07_17_channel_deletion_state.py`
- 已排除与本轮无关的 `config.yaml`、`frontend/src/components/workspace/workspace-header.tsx`、图片、备份和既有临时目录
- 继续采用 Spec 与 Standards 两条独立复审轴；两条轴的严重级别不交叉重排

---

## 1. 复审结论

第八轮报告中的关键路径已有明显改进：rotation readiness 失败现在会进入严格回滚；DELETE 引入了持久化 `deleting` tombstone；AIO late-create 增加了跨 provider 的生命周期 generation；Supervisor shutdown 已关闭进程内的新启动准入；cleanup discovery 也加入了持久化 cursor 和有界 reader 槽。

但当前仍未达到可合并标准。本轮 Spec 轴确认 **3 个 P1、4 个 P2**。主要阻断点是：DELETE stop 失败时只恢复数据库状态而没有恢复被部分停止的 runtime；AIO compensation 在阻塞 destroy 中被取消时会提前释放 fencing lock 并并发执行第二次 destroy；ready 后的数据库重读仍是跨 Gateway 的 check-then-act，不能阻止另一副本删除后本副本继续注册 runtime。

Standards 轴另发现 **2 组书面规范/验证缺口**：本轮新增测试函数缺少完整类型标注；新增迁移没有针对新增列和 downgrade 建立直接验证，PostgreSQL 路径在本地仍可跳过。Ruff 全绿不覆盖这两项书面要求。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **rotation 的核心 ready/rollback 路径已修复：** `_start_row(strict=True)` 会把启动失败传播给路由；旧 row 和旧 secret 不再因为普通 readiness 失败被当作成功轮换。
- **DELETE 已引入 durable tombstone：** `status=deleting`、`delete_previous_status`、startup resume 以及 start/restart/rotate 的 fail-closed 语义已经形成基础状态机。
- **AIO 正常的跨 provider successor adoption 已有 durable generation：** 两个 provider 共享 filesystem 时，旧 operation 能识别后继已接管的 deterministic sandbox。
- **shutdown 后的新容量注册已被拒绝：** create 在注册前会检查 shutdown gate，并进入补偿路径。
- **全局 cleanup discovery 已有持久化扫描 cursor：** 慢文件前缀不再必然让目录尾部永久不可见。
- **cleanup health 已改为 per-binding durable generation：** 未发现第八轮 cross-binding health 污染问题的直接回归。
- **Supervisor 进程内 shutdown/start 竞态已关闭：** shutdown admission gate 和 lifecycle drain 能覆盖尚未注册到 `_running` 的本地 start。
- **生产 file-lock helper 的类型标注已补齐，README/CLAUDE 已同步。**

---

## 3. Spec P1 问题

### 3.1 [P1] DELETE stop 失败只恢复 active row，没有恢复已部分停止的 runtime

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`delete_binding()` 在 `mark_deleting()` 后调用 `_stop_runtime()`；stop 抛错时只执行 `restore_deleting()` 并重新抛出。问题在于 `FeishuChannel.stop()` 在可能失败之前已经设置 `_stop_requested=True`、`_running=False`、取消后台任务并取消 outbound 订阅；session stop 超时或线程未退出都可能在这些不可逆动作之后抛错。

与此同时，`_stop_runtime()` 只有完整成功后才从 Supervisor 的 `_running` 移除对象。因此失败结果可能是：数据库 row 已恢复为 `active`，Supervisor map 仍声称 runtime 存在，但 channel 已不运行、已取消订阅，或遗留一个未确认退出的 WebSocket 线程。后续 `_start_row()` 还可能把这个 map entry 当作已有实例，无法自动恢复服务。

这违反第八轮 DELETE UOW 的要求：失败后若保留 row/secret，runtime 也必须保持可管理、可恢复，而不能返回“active 但不可用”的半状态。

**建议修复：**

- 将 stop 建模为可确认的状态转换；在任一步失败后不要直接恢复 `active`。
- 若旧 runtime 已被部分停止，应在同一个 binding lifecycle 临界区内重新创建并严格等待 ready，成功后再恢复 `active`。
- 如果无法恢复，保留 durable `deleting`/`unhealthy` 状态和 secret ref，让 startup janitor 或显式重试继续收敛。
- 增加 session stop 返回 `False`、thread join 后仍存活以及 stop 中途抛错三组故障注入；断言最终状态只能是“active 且新 runtime ready”或“durable deleting/unhealthy 且可重试”。

### 3.2 [P1] AIO compensation 在 backend destroy 中被取消会提前释放 fencing 并重复 destroy

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)
- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py)

**问题说明：**

`_compensate_late_created_sandbox()` 在持有 thread/file lock 时执行 `await asyncio.to_thread(self._backend.destroy, info)`。若 cleanup task 此时被取消，等待协程立刻进入 `finally` 并释放两把锁，但真正的 blocking destroy 线程仍在后台运行。外层 `_destroy_late_created_sandbox()` 随后启动同步 fallback；fallback 能重新取得刚释放的锁，并对同一个裸 sandbox ID 再执行一次 destroy。

定向 barrier 验证结果：

```text
second_destroy_started_before_first_returned=True
destroy_calls=2
```

这不只是重复调用。锁提前释放后，另一个 Gateway/provider 可以在第一个 destroy 线程尚未返回时接管或创建同一 deterministic ID；旧 destroy 仍会按裸 ID 删除后继容量，持久化 generation 的 compare-and-delete 语义因此失效。

当前 `test_cleanup_cancelled_during_async_compensation_uses_worker_fallback` 把整个 compensation helper 替换成阻塞 coroutine，只覆盖 destroy 之前被取消，没有覆盖 `to_thread(destroy)` 已开始后的真实窗口。

**建议修复：**

- compensation 必须只有一个 durable owner；destroy worker 未结束前不得释放 fencing lock，也不得启动第二个 destroy。
- 可以 shield 整个阻塞 destroy 并等待 worker 完成，或把 file-lock ownership 与最终结果明确移交给唯一后台 worker。
- backend 若支持 lease/version/conditional delete，应把 operation generation 下沉到原子删除条件，不能只依赖进程侧按 ID destroy。
- 新增确定性测试：第一 destroy 已进入但未返回时取消 cleanup，断言没有第二次 destroy、锁未释放、后继不能接管；第一 destroy 完成后才允许状态收敛。

### 3.3 [P1] ready 后重读仍未关闭跨 Gateway stale-start TOCTOU

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`_start_row()` 在 `channel.start()` ready 后读取一次当前 row，离开 `_repository_projection_lock` 后检查 `status != deleting`，然后写入本进程 `_running` 和 dynamic registry。该锁只保护当前进程；另一 Gateway 可以在最后一次 SELECT 之后提交 tombstone、停止其本地 runtime 并删除 secret/row，本 Gateway 仍会继续注册已 ready 的 channel。

现有 `test_start_ready_rechecks_cross_process_deletion_tombstone` 只让 tombstone 在释放 ready barrier 之前可见，覆盖的是“删除先于最后一次 SELECT”，没有覆盖“SELECT 已返回、注册尚未发生”的最后窗口。第八轮修复报告声称 ready 后重读关闭了跨 Gateway stale-start，但当前实现仍是 check-then-act。

**建议修复：**

- 为 active runtime 引入数据库 lease/generation，由 start 在注册前原子 claim，DELETE 原子撤销；runtime 必须持续验证 lease。
- 或明确强制单 Supervisor leader，并把该部署约束做成可验证的 leader election/admission，而不是依赖进程内锁。
- 增加双 Supervisor barrier：A 完成最后 SELECT 后暂停，B 提交 deleting/delete，A 再继续；断言 A 必须 stop 新 channel，不能写入 `_running` 或 registry。

---

## 4. Spec P2 问题

### 4.1 [P2] AIO shutdown 的 compensation handoff 仍不 durable

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)

**问题说明：**

shutdown 后的 late create 会进入 cleanup，这是改进；但实际 cleanup owner 仍是当前进程里的 operation map、async task 或 daemon thread。`.lifecycle.json` 只被当前 operation 用于 ownership 比较，startup `_reconcile_orphans()` 不读取它恢复未完成 compensation，而是把发现的容器直接收进 warm pool，等待 idle checker。

若进程在 destroy 之前退出，下一进程没有恢复该 cleanup intent；`idle_timeout=0` 或 idle checker 不运行时，容量可永久遗留。daemon thread 也不能提供进程退出后的 durable handoff。

**建议修复：** startup 应扫描 lifecycle records，区分 accepted lease 与 cancelled-create cleanup intent，并在 fencing lock 下恢复未完成 destroy；只有成功收敛后才能删除 record。增加 create 完成后、destroy 前强制终止进程，再由新 provider 启动恢复的进程级测试。

### 4.2 [P2] cleanup discovery 的有界线程方案会永久失去进展，per-binding 扫描仍可叠加阻塞 worker

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)

**问题说明：**

全局 discovery 使用容量为 2 的 `_ATTACHMENT_CLEANUP_READ_SLOTS`。reader 只有在文件读取真正返回时才释放槽；两个永久挂起的读取会永久占满全部槽，之后每个 pass 都立即得到 `TimeoutError`，cursor 虽继续移动但所有正常 job 都无法被解析。定向验证中，两条挂起 job 占槽后连续四轮均得到：

```text
jobs=[] invalid=False timed_out=True
```

此外，per-binding `_recover_attachment_cleanup_jobs()` 仍对无界 `_read_attachment_cleanup_jobs()` 使用 `wait_for(asyncio.to_thread(...))`；timeout 只能取消 asyncio waiter，不能停止线程，周期重试仍可叠加 blocking worker。`attachment_cleanup_healthy` 还会在事件循环上同步取得 file lock 并遍历整个 outbox。

本轮新增 `test_global_cleanup_discovery_cursor_reaches_slow_directory_tail` 在定向测试集内出现一次失败，但单独重复执行可通过，说明当前时序测试和实现都未证明有限轮次内的稳定进展。

**建议修复：**

- 使用可终止的隔离执行单元，或让挂起 path 进入有期限的 quarantine；不能让一次永久 I/O 永久消耗全局 reader 容量。
- 全局和 per-binding 路径统一使用同一个有界、可观测、可恢复的 discovery executor。
- 不要在事件循环同步扫描 outbox；health projection 应使用异步/缓存的 durable generation 结果。
- 增加两个永久挂起文件加一个正常尾部 job 的多 pass 测试，断言正常 job 在有限轮次内被处理，活跃 worker 数始终有界。

### 4.3 [P2] startup 只重试一次 deleting tombstone，janitor 清空 backlog 后不会继续删除

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`load_active_bindings()` 启动 cleanup janitor 后只枚举一次 deleting rows，并对每行调用一次 `delete_binding()`。如果该次调用先于 janitor 清空 attachment backlog，会捕获 `BindingCleanupPendingError` 并保留 tombstone；之后 `_run_cleanup_janitor()` 只恢复 attachment jobs，从不重新扫描 deleting rows。

因此 cleanup 随后成功也不会自动触发 row/secret 删除。除非用户再次调用 DELETE 或进程再次重启，tombstone 会无限期保留。这与第八轮修复报告所述的“startup 自动重试并最终收敛”不一致。

**建议修复：** 让 janitor 每轮 cleanup 后扫描并收敛可删除 tombstone，或为 backlog 完成事件建立 durable wakeup；增加 startup 时 backlog 尚在、首轮 janitor 清空、无需外部 DELETE 即最终删除 row/secret 的测试。

### 4.4 [P2] rotation 的 secret 擦除失败仍会产生不可重试的加密 secret 孤儿

**相关文件：**

- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)
- [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py)

**问题说明：**

严格 readiness 回滚修复了主 UOW，但 secret 擦除仍没有 durable ownership：

- rotation 失败后，`secrets.delete(new_ref)` 若失败，会覆盖原来的业务异常；new ref 没有 row/tombstone/outbox 保存，无法重试。
- rotation 成功后，删除旧 ref 失败只记录 warning；row 已只引用 new ref，旧 ref 同样永久失去 durable owner。

F3.2 要求凭据通过 SecretStore 生命周期管理；当前两个分支都可能留下无法由 binding 生命周期回收的加密 secret。

**建议修复：** 为 secret deletion 建立 durable outbox/tombstone，保存 `secret_ref + binding_id + reason`，擦除成功后再完成记录；错误响应应保留原始业务语义，同时让 cleanup 可观测、可重试。补充新 ref rollback delete 失败与旧 ref superseded delete 失败的故障注入测试。

---

## 5. Standards 轴

### 5.1 书面规范违例：本轮新增测试函数缺少完整类型标注

`backend/CONTRIBUTING.md` 要求函数签名使用类型标注。本轮新增的 [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py) 中，`_make_lifecycle_provider()`、第 526/572/608/656 行附近的新测试函数，以及内部 `create()`、`destroy()`、`blocked_compensation()` callbacks 均缺少部分参数或返回值类型。

建议至少为 pytest fixtures 使用 `Path`、`pytest.MonkeyPatch`，为动态 module/backend 使用明确 Protocol 或 `Any`，并补齐所有 callback 返回类型。Ruff 当前规则不会报告该书面规范违例。

### 5.2 迁移 Review Gate 未直接验证新增列与 downgrade

新增 [2026_07_17_channel_deletion_state.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_17_channel_deletion_state.py) 提供了 upgrade/downgrade，但 [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 本轮主要只把 expected head 更新为新 revision；SQLite/PostgreSQL 测试没有断言 `agent_channel_bindings.delete_previous_status` 的存在、类型、nullable/default 语义，也没有执行 downgrade 后验证列被移除。PostgreSQL 在本地不可用时仍会 skip。

这不足以证明开发计划要求的 SQLite/PostgreSQL 迁移兼容性。建议新增 migration-specific upgrade/downgrade 测试，并在 CI 中强制真实 PostgreSQL gate。

### 5.3 判断性设计 smell（不改变 Spec 严重级别）

1. **Duplicated Code：** AIO async/sync compensation 分别复制了 thread lock、file lock、adoption/generation 检查和 destroy 流程，取消语义已在两份实现间产生分叉。建议收敛成单一 ownership state machine，async 路径只负责等待唯一 worker。
2. **Divergent Change：** `FeishuChannel` 同时承担 WebSocket、attachment outbox、全局 discovery、claim/lease、health projection 和 shutdown drain。建议拆出独立 cleanup store/coordinator，统一全局与 per-binding 的预算和恢复语义。

---

## 6. 第八轮问题关闭状态

| 第八轮问题 | 本轮状态 | 说明 |
|---|---|---|
| P1：rotation failure rollback 不可达 | **核心关闭，cleanup 部分未关闭** | strict readiness/旧 row 回滚已修复；新旧 secret 擦除失败仍无 durable retry |
| P1：DELETE failure atomicity | **部分关闭** | tombstone 已实现；stop 失败会恢复 active 半状态，跨 Gateway stale-start 仍在 |
| P1：AIO cross-Gateway successor fencing | **部分关闭** | 正常跨 provider adoption 已修复；destroy 中取消会提前释放 lock 并重复删除 |
| P2：AIO shutdown/lost ownership | **部分关闭** | shutdown admission 已修复；进程退出后的 durable recovery consumer 仍缺失 |
| P2：cleanup scan fairness/discovery | **部分关闭** | 持久化 cursor 已实现；两个挂起 reader 可永久耗尽容量，per-binding worker 仍可叠加 |
| P2：global generation 污染 per-binding health | **已关闭** | 已改为 per-binding durable generation，未发现直接回归 |
| P2：Supervisor shutdown/start race | **进程内已关闭** | admission gate/lifecycle drain 有效；跨 Gateway 删除与注册仍需 DB lease/leader |
| Standards：生产 helper 类型标注 | **生产代码已关闭，测试未关闭** | helper 已补齐；本轮新增测试函数仍缺完整签名类型 |

---

## 7. 验证记录

### 7.1 第九轮直接回归

Supervisor、owner channel API、AIO provider、Feishu parser/cleanup 等直接回归集：

```text
139 passed, 1 failed, 5 warnings in 33.96s
```

失败项：

```text
test_feishu_parser.py::test_global_cleanup_discovery_cursor_reaches_slow_directory_tail
```

该测试单独重复执行 5 次均通过，因此当前表现为 suite timing 相关的不稳定失败，不能据此声明第九轮直接回归全绿。

迁移专项：

```text
6 passed, 1 skipped, 1 warning in 4.35s
```

skip 为本地 PostgreSQL 不可用；测试仍未验证本轮新增列和 downgrade。

### 7.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 M3、legacy channel、attachment、sandbox、user-context 和 Gateway service 测试：

```text
339 passed, 8 skipped, 5 failed, 6 warnings in 70.46s
```

5 项失败与前几轮已记录的 Windows LocalSandbox 基线一致：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

本轮不把它们判定为 M3 修复回归，但也不声明完整 Review Gate 全绿。

### 7.3 静态、格式与编译检查

```text
ruff check <13 changed Python files>: All checks passed!
ruff format --check <13 changed Python files>: 13 files already formatted
python -m compileall <7 changed source/migration files>: passed
```

### 7.4 专项行为验证

- AIO destroy 已进入 worker 后取消 cleanup：第二个 destroy 在第一个返回前开始，最终同一 sandbox ID 被调用两次 destroy。
- 全局 cleanup 两个永久挂起 reader 占满槽后，连续四轮均 `jobs=[]` 且 `timed_out=True`，正常 job 无进展。
- ready 后跨 Gateway 删除窗口、DELETE stop 半失败以及 tombstone 单次 startup retry 由控制流和锁/事务边界确认；现有测试 barrier 均未覆盖最后 check 与 act 之间的窗口。

### 7.5 未完成 Gate

- 未重新执行全量 backend `pytest tests -q`。
- 真实 PostgreSQL、双 Feishu App、远程 AIO/provisioner、多 Gateway replica、进程 kill/restart 和 Linux CI Gate 仍需部署环境验证。
- 当前直接回归集有 1 个时序失败，M3 聚焦集有 5 个既有 Windows LocalSandbox 失败，不能声明测试全绿。

---

## 8. 修复优先级

1. 修复 DELETE stop failure rollback：失败后只能收敛为 ready active runtime，或保留 durable 可重试状态。
2. 修复 AIO destroy cancellation ownership，确保单一 destroy、锁持有到 blocking worker 真正完成，并增加真实窗口 barrier 测试。
3. 用 DB lease/generation 或单 leader 关闭跨 Gateway start/delete 最后 TOCTOU。
4. 为 AIO lifecycle record 增加 startup recovery consumer，形成进程退出后的 durable compensation。
5. 统一并修复 global/per-binding cleanup discovery，保证永久挂起 I/O 下正常 job 仍能在有限轮次进展；同时让 janitor 重试 deleting tombstone。
6. 为 secret erase 建立 durable cleanup outbox，并补齐类型与迁移 upgrade/downgrade/真实 PostgreSQL gate。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭第 3 节的 3 个 P1：DELETE 失败不能恢复为 active 半运行态；AIO cleanup 取消不能提前释放 fencing 并重复 destroy；跨 Gateway 的删除不能在最后一次 SELECT 后仍允许旧副本注册 runtime。第 4 节的 durable shutdown、cleanup liveness、tombstone convergence 和 secret erase 也应形成确定性回归证据，再执行完整 backend 与真实多副本/进程恢复 Gate。
