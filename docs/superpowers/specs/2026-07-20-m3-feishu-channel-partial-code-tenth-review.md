# 多租户 Agent 发布平台 - M3 第十轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-20

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第九轮代码复审：[2026-07-20-m3-feishu-channel-partial-code-ninth-review.md](./2026-07-20-m3-feishu-channel-partial-code-ninth-review.md)
- 第九轮修复报告：[2026-07-20-m3-feishu-channel-partial-ninth-review-fix-report.md](./2026-07-20-m3-feishu-channel-partial-ninth-review-fix-report.md)
- 报告结构参考：[2026-07-13-m1-agent-control-plane-code-seventh-review.md](./2026-07-13-m1-agent-control-plane-code-seventh-review.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第十轮固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 当前 HEAD 仍为固定点；本轮修复是未提交工作区差异，没有新增 commit
- 相关范围为 15 个已跟踪 backend 文件，以及新增迁移 `2026_07_17_channel_deletion_state.py`
- 已跟踪 backend 差异规模：2089 行新增、201 行删除；新增迁移另行纳入
- `config.yaml`、前端文件、图片、备份和既有临时目录不属于本轮修复，未纳入结论
- 继续采用 Spec 与 Standards 两条独立复审轴；两条轴不交叉重排严重级别

---

## 1. 复审结论

第九轮修复取得了几项实质进展：AIO destroy 的正常 cancellation 路径已经收敛为一个同步 ownership worker，不再启动第二个并发 destroy；janitor 每轮都会重试 deleting tombstone；两个挂起 cleanup reader 不再立即占死仅有的两个逻辑槽；SQLite 迁移已增加 upgrade/downgrade 专项；AIO 和 migration 新增测试的主要类型缺口也已补齐。

但当前仍未达到可合并标准。本轮 Spec 轴确认 **3 个 P1、4 个 P2**。3 个 P1 均已确定性复现：runtime claim 提交后的 DELETE 仍允许旧 Gateway 注册 runtime；stop 失败恢复期间的 post-claim 异常仍会留下 `active + 无 runtime`；PATCH 对 crash-recovery `rotation_candidate` 执行普通 cleanup 时会删除 row 当前正在引用的 secret。

Standards 轴另有 **3 组硬缺口**：新增测试签名的类型标注仍只部分补齐；迁移/PostgreSQL/全量测试 Review Gate 仍未闭环；README 对 cleanup reader 上限的描述与实现不一致。另有 2 项判断性设计 smell，不参与 Spec 严重级别排序。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **AIO cancellation 的重复 destroy 主路径已关闭：** destroy 开始后取消 cleanup，async 路径会等待同一个同步 worker，单次 cancellation 下不再提前释放 file/thread fencing，也不再启动第二次 fallback destroy。
- **tombstone 周期收敛已实现：** `recover_cleanup_state()` 每轮依次运行 attachment cleanup、secret cleanup 和 deleting row 重试，backlog 清空后不再必须重启或再次调用 DELETE。
- **两个挂起 reader 的原始特例已改善：** 超时 path 会进入 quarantine 并释放逻辑 scan slot，扫描 cursor 可继续走向后续正常文件。
- **secret cleanup 已有 durable row 字段和 matching acknowledgement：** rollback/superseded ref 的普通失败可由 janitor 重试。
- **SQLite 迁移专项已覆盖新增列和 downgrade：** 新增列、nullable/default 以及删除列路径已有直接断言。
- **AIO、migration 新增测试的函数类型标注已补齐。**
- **生产文档已同步新增 tombstone、runtime claim、secret cleanup、AIO lifecycle 和 quarantine 设计。**
- **harness → app import firewall 仍通过。**

---

## 3. Spec P1 问题

### 3.1 [P1] stale-start TOCTOU 只从最后 SELECT 后移到了 runtime claim 之后

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`_start_row()` 在 channel ready 后调用 `claim_runtime()`；claim 提交返回后，才把 channel 写入本进程 `_running` 和 dynamic registry。另一 Gateway 可以在 claim 提交之后、内存注册之前执行 DELETE：`mark_deleting()` 会撤销 token、推进 generation，甚至继续删除 secret/row，但启动方没有再验证 token，仍会注册已经失去数据库 lease 的 runtime。

确定性 barrier 结果：

```text
returned_running=True
row_status=deleting
runtime_lease_token=None
registered=True
channel_running=True
```

当前 `test_start_claim_rejects_delete_committed_after_final_row_read` 的 barrier 位于最后 row read 与 claim 之间，只证明 tombstone 先于 claim 时会被拒绝，没有覆盖 claim 已提交后的最后窗口。

此外，`claim_runtime()` 没有拒绝已有非空 token，会直接覆盖另一个 Gateway 的 runtime lease；它不能提供双副本互斥。开发计划风险表原本明确第一版采用单实例 Supervisor，而当前代码和文档把不完整 token 描述为多 Gateway fencing，既没有强制单 leader，也没有实现可持续 lease。

**建议修复：**

- 若坚持第一版单实例，必须通过 leader election/admission 强制只有一个 Supervisor 能 start/delete，而不只是写文档约束。
- 若支持多副本，claim 必须拒绝未过期的其他 token，并具有 owner/heartbeat/expiry；DELETE 必须等待或撤销远端 lease，runtime 需要持续检测撤销并自行 stop。
- 新增 claim 返回后暂停、另一 Gateway 完整 DELETE、启动方继续的 barrier；断言 channel 必须 stop，不能写入 `_running`/registry。

### 3.2 [P1] stop 失败恢复在 post-claim 异常时仍会留下 active 无 runtime

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

stop 部分失败后，旧 map entry 已正确视为不可信并移除；但恢复路径调用 `_start_row(..., restore_deleting=True)` 时，`claim_runtime()` 会在本地 registry 和 health 写入之前直接把 row 从 `deleting` 改成 `active` 并清除 `delete_previous_status`。

若 claim 之后的 `register_dynamic_channel()` 或 `_record_health()` 失败，`_start_row()` 只 stop 新 channel、移除 map 并释放 token，不会把 row 重新标记为 tombstone。外层 delete 只记录 recovery error，随后重新抛出原 stop 异常。

在恢复 runtime 已 ready 并 claim 成功后注入一次 health repository 失败，结果为：

```text
delete_error=partial stop
row_status=active
runtime_lease_token=None
registered=()
new_channel_running=False
```

这与第九轮要求的最终不变量相反：失败只能收敛为“ready active runtime”或“durable retryable tombstone”。

**建议修复：**

- 恢复 claim 不应提前把 tombstone 改成 active；使用 `recovering`/provisional token，完成本地注册与必要投影后再用 CAS 最终切换 active。
- 如果最终 active CAS 之后的本地步骤仍可能失败，异常路径必须用 matching token/generation 原子恢复 tombstone。
- 增加 post-claim 的 registry failure、health failure、task cancellation 三个 barrier 测试。

### 3.3 [P1] PATCH 会把 crash-recovery rotation candidate 当作垃圾删除，擦除当前凭据

**相关文件：**

- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)
- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py)
- [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py)

**问题说明：**

进程若在 row 已切换到 candidate、但 cleanup reason 仍是 `rotation_candidate` 时退出，startup janitor 会先调用 `recover_staged_secret_cleanup()`，正确把 cleanup owner 转换为旧 ref 的 `rotation_superseded`。

但新的 PATCH 在发现任意 `secret_cleanup_ref` 时直接调用 `cleanup_binding_secrets()`；该公开路径只执行 `_erase_secret_cleanup_row()`，没有 candidate recovery。于是它会删除 `secret_cleanup_ref` 指向的 candidate，而此时 row 的当前 `secret_ref` 也正是 candidate，并同时清空 `rotation_previous_secret_ref`。

定向恢复状态验证：

```text
row_secret_is_candidate=True
current_secret_exists=False
previous_secret_exists=True
secret_cleanup_ref=None
rotation_previous_secret_ref=None
```

此后如果进程重启，active row 无法读取当前 secret；若下一次 rotation 失败，rollback 也只会恢复到已被删除的 candidate ref。

**建议修复：**

- 所有 secret cleanup 入口必须共享同一状态机；遇到 `rotation_candidate` 必须先执行 crash recovery，再决定应删除 candidate 还是 previous ref。
- 禁止通用 cleanup helper 删除与 row 当前 `secret_ref` 相同的值，除非该 row 已是 deleting 且正在完成 physical delete。
- 增加“crash after credential row switch → owner PATCH”测试，断言当前 secret 始终存在，旧 ref 才进入 superseded cleanup。

---

## 4. Spec P2 问题

### 4.1 [P2] AIO destroy 失败或过早 startup reconciliation 仍会丢失 durable cleanup intent

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)
- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py)

**问题说明：**

单 worker fencing 已修复，但 durability 没有闭环。`_run_worker_create_compensation()` 无论 `_compensate_late_created_sandbox_sync()` 是否成功，都会在 `finally` 调用 `_finish_backend_create_operation()`；async 路径记录异常后也继续调用同一 finish。finish 会删除 operation 仍拥有的 lifecycle record。

注入一次 transient backend destroy failure：

```text
destroy_failed=True
cleanup_pending_record_exists=False
operation_tracked=False
```

因此下一进程没有任何 retry intent，与修复报告“异常保留 record”不一致。

另一个窗口位于 startup reconciliation：只要 backend 列表中存在任意其他 running sandbox，所有当前未出现在该快照中的 `cleanup_pending` records 都会被直接删除。如果旧 Gateway 的 blocking create 尚未物化目标 sandbox，新 Gateway 会删除其 record；旧 create 随后返回时因 generation ownership 已消失而保留容量。专项结果为 `record_exists=False`。

**建议修复：**

- 分离“移除进程内 operation”与“ack durable cleanup record”；只有 matching destroy 成功或经可靠 backend 查询确认目标不存在后才能删除 record。
- 对尚未 materialize 的 `cleanup_pending` 使用创建时间/owner heartbeat/lease，不能凭一次 `list_running()` miss 删除。
- 增加 destroy transient failure retry，以及 Gateway A create 未返回、Gateway B reconcile、A 随后返回的双 provider barrier。

### 4.2 [P2] quarantine 满 8 个挂起 reader 后，正常 job 再次永久停摆

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)

**问题说明：**

超时后释放逻辑 semaphore 让两个挂起 path 的特例有进展，但每个不同 path 都会留下一个实际 daemon reader 和 quarantine future。实现最多累计 8 个 quarantine entries；达到上限后，在检查 logical read slot 之前直接拒绝所有新 path，因此正常 job 也无法读取。

8 个永久挂起文件加一个正常尾部文件连续 20 轮的结果：

```text
max_quarantine=8
last_five_passes: jobs=[] timed_out=True quarantine=8
normal_job_seen=False
```

已完成但对应 path 不再存在的 future 也没有全局 prune 入口，会永久占用 quarantine 表。当前测试只覆盖两个挂起 path，不能证明一般的有限进展。

**建议修复：** 使用真正可终止的隔离单元，或将 quarantine 与新路径 admission 分离；表满时仍要保留处理非 quarantine 正常 job 的容量。每轮应清理 done/stale entries，并增加超过上限、文件消失和正常尾部 job 的测试。

### 4.3 [P2] 新 secret 在 DB stage 前仍有无主窗口，删除竞态返回 500 并泄漏 ref

**相关文件：**

- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)
- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py)

**问题说明：**

PATCH 先执行 `SecretStore.put()`，随后才进入 Supervisor 并调用 `stage_secret_cleanup()`。进程在两者之间退出时，新 ref 没有 row/outbox owner。

即使不退出也有确定性竞态：初始 owner read 后创建 new ref，另一 Gateway 把 binding 标记 deleting；Supervisor 抛 `BindingCleanupPendingError`。路由没有把该异常映射为 409，而是进入通用 `BaseException`；随后调用 row-based `cleanup_binding_secrets()`，但 row 从未 stage 过该 new ref，因此 cleanup no-op，API 返回 500 且 ref 仍存在：

```text
error=BindingCleanupPendingError
new_secret_exists_after_cleanup=True
```

`BindingNotFoundError` 分支同样只尝试 row-based cleanup，row 已删除时也无法找到 new ref。

**建议修复：** 至少在所有 pre-stage 异常分支直接擦除本次 `new_ref` 并正确映射 404/409；要关闭进程崩溃窗口，需要独立于 binding row 的 durable secret-ingest outbox，或让 SecretStore.put 本身生成可枚举、可回收的 pending ownership record。

### 4.4 [P2] 统一 global scan 重新引入跨 binding health 污染

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)

**问题说明：**

`_refresh_attachment_cleanup_health()` 虽然只筛选当前 binding 的 jobs，但 `healthy` 同时依赖全局 `invalid` 和 `timed_out`。任意其他 binding 的损坏/挂起文件都会让当前 clean binding 变成 unhealthy。

在 outbox 放入一个属于其他路径的无效 JSON，刷新 clean binding A：

```text
refresh=False
binding_a_healthy=False
```

这与第九轮修复报告和 README 的“其他租户不能使 clean binding unhealthy”相反。现有 cross-binding 测试只覆盖另一个 binding 的正常 job，没有覆盖 invalid/hung discovery。

**建议修复：** 为 discovery 建立可在读取前确定 binding 的索引/目录分区，让 per-binding health 只消费自己的 scan completeness；无法归属的损坏记录应进入独立 global store health，而不是污染每个 binding。补充另一个 binding 的 invalid/hung path 测试。

---

## 5. Standards 轴

### 5.1 书面规范违例：新增测试签名的类型标注仍只部分关闭

`backend/CONTRIBUTING.md` 要求所有函数签名使用类型标注。AIO 和 migration 的本轮新增函数已补齐，但以下 diff 内测试仍缺 fixture、参数或返回值类型：

- [test_agent_channel_repo.py](../../../backend/tests/test_agent_channel_repo.py)：第 156、193、225、262 行附近；
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)：第 888 行附近；
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)：第 436、474、536、574、608、631、666 行附近；
- 同一 Supervisor 测试中的 callbacks：第 479、581、614、637、672 行附近。

因此第九轮修复报告所称“新增/修改测试及 callbacks 补齐类型”不准确。Ruff 当前配置不会检查该书面要求。

### 5.2 migration/TDD Review Gate 仍未闭环

SQLite 新增列和 downgrade 测试是有效改进；但 [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 的 PostgreSQL 路径不执行 downgrade，也没有完整验证 default、字符串长度和所有列语义，本地仍允许 skip。开发计划要求迁移 upgrade/downgrade 在 SQLite 与 PostgreSQL 双向兼容，`backend/CLAUDE.md` 还要求 bug fix 运行全量 suite 且测试通过。

本轮没有执行全量 backend；直接回归反而得到 1 个 suite-only failure。`test_loading_active_bindings_isolates_start_failures` 单独重复 10 次均通过，但在直接集合中数据库 health 偶发保持 `unknown`，并且该测试启动 janitor 后没有 shutdown，失败日志显示 fixture 数据库关闭后 janitor 仍在查询。

因此当前不能声明 TDD/Review Gate 已关闭。建议强制真实 PostgreSQL upgrade/downgrade CI，补齐 schema 断言，并让所有启动 janitor 的测试在 `finally` 中 shutdown。

### 5.3 文档与实现不一致：reader 上限不是两个 daemon readers

`backend/CLAUDE.md` 的文档更新政策要求 README/CLAUDE 与代码准确同步。[README.md](../../../backend/README.md) 声称全局 pass 把不可中断读取限制为两个 isolated daemon readers；实际实现会在每次 timeout 后释放两个逻辑 slot，并累计最多 8 个 quarantined readers，同时还可再运行两个 active readers。

应把文档区分为 logical active slots、quarantine 上限和实际 OS threads；更重要的是先修复第 4.2 节的 quarantine-full liveness，避免把不成立的不变量写入运维文档。

### 5.4 判断性设计 smell（不改变 Spec 严重级别）

1. **Primitive Obsession / Data Clumps：** Supervisor 与 Repository 持续以 `dict[str, Any]` 和裸字符串表达 binding row、status、cleanup reason、token/generation。建议引入 typed row DTO、状态 enum 和聚合 lifecycle transition。
2. **Divergent Change：** `feishu.py` 同时承担消息通道、attachment outbox、global discovery、quarantine、claim/lease、health projection 和 shutdown drain。建议拆出独立 cleanup store/coordinator，统一预算、隔离和恢复语义。

---

## 6. 第九轮问题关闭状态

| 第九轮问题 | 本轮状态 | 说明 |
|---|---|---|
| P1：DELETE stop 失败恢复 active 半状态 | **部分关闭** | 旧半停止 runtime 已丢弃；恢复 claim 后的 registry/health/cancel 异常仍会留下 active 无 runtime |
| P1：AIO destroy 中取消导致重复 destroy | **已关闭主路径** | 单次 cancellation 等待唯一同步 worker，未发现第二次 destroy 回归 |
| P1：ready 后跨 Gateway stale-start | **部分关闭** | tombstone 先于 claim 可阻止启动；claim 返回后仍可被 DELETE 撤销并继续本地注册 |
| P2：AIO shutdown compensation 不 durable | **部分关闭** | startup consumer 已有；destroy failure 和 pre-materialization reconciliation 会丢 record |
| P2：cleanup discovery 永久失去进展 | **部分关闭** | 两挂起 path 特例已修复；8 个 quarantine 满后再次全局停摆 |
| P2：startup 只重试一次 tombstone | **已关闭** | 每轮 janitor 都会重试 deleting rows |
| P2：rotation secret erase 无 durable owner | **部分关闭** | 普通 rollback/superseded retry 已有；candidate pre-clean 和 pre-stage new ref 仍可泄漏/误删 |
| Standards：新增测试类型标注 | **部分关闭** | AIO/migration 已补齐，repo/parser/supervisor 仍有缺口 |
| Standards：迁移 upgrade/downgrade Gate | **部分关闭** | SQLite 已覆盖；PostgreSQL downgrade/完整 schema 与强制 CI 仍缺失 |

---

## 7. 验证记录

### 7.1 第十轮直接回归

执行 repository、Owner Channel API、Supervisor、AIO provider、Feishu parser/cleanup、WebSocket、Gateway service 和迁移测试：

```text
154 passed, 1 failed, 1 skipped, 5 warnings in 36.81s
```

失败项：

```text
test_feishu_supervisor.py::test_loading_active_bindings_isolates_start_failures
expected database health=unhealthy, actual=unknown
```

该测试单独启动新进程重复 10 次均通过，表明它是集合时序/测试隔离相关的不稳定失败；不能据此声明直接回归全绿。唯一 skip 为本地 PostgreSQL 不可用。

### 7.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 测试文件：

```text
347 passed, 8 skipped, 5 failed, 6 warnings in 66.84s
```

5 个失败仍是此前记录的 Windows LocalSandbox 基线：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

本轮不把它们判定为第九轮修复回归，但也不声明 M3 Review Gate 全绿。

### 7.3 边界、静态、格式与编译检查

```text
tests/test_harness_boundary.py: 1 passed
ruff check <14 changed Python files>: All checks passed!
ruff format --check <14 changed Python files>: 14 files already formatted
python -m compileall <changed source/migration targets>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

### 7.4 专项行为验证

- claim 提交后写入 deleting：start 仍返回 running 并注册 channel，row lease 已被撤销。
- stop 失败恢复 claim 后注入 health 写失败：row 为 active、token 为空、本地 runtime/registry 为空。
- 模拟 crash 后 row 当前 ref 等于 rotation candidate，再执行 PATCH 的预清理形状：当前 candidate 被擦除，previous ownership 同时丢失。
- AIO destroy 抛 transient error：`cleanup_pending` record 与 operation 均被清除。
- 8 个永久挂起 cleanup files：quarantine 达 8 后连续 pass 无 job，正常尾部文件从未被发现。
- PATCH 在 SecretStore.put 后遭遇 deleting：抛 `BindingCleanupPendingError`，row-based cleanup 后 new ref 仍存在。
- 另一个 binding 的 invalid cleanup JSON 会把 clean binding 的 health 投影为 unhealthy。

### 7.5 未完成 Gate

- 未执行全量 backend `pytest tests -q`。
- 真实 PostgreSQL upgrade/downgrade、双 Feishu App、远程 AIO/provisioner、多 Gateway leader/lease、真实进程 kill/restart 和 Linux CI Gate 仍未执行。
- 当前直接回归存在 1 个 suite-only failure，M3 聚焦集存在 5 个既有 Windows LocalSandbox failure，不能声明测试全绿。

---

## 8. 修复优先级

1. 先关闭 runtime claim 后的 DELETE 窗口，并明确选择“强制单 leader”或“完整多副本 lease/heartbeat/revocation”模型。
2. 让 stop-failure recovery 在全部 post-claim 异常下保持 tombstone，只有完整 ready/registration/CAS 后才能变 active。
3. 统一 secret cleanup 状态机，禁止普通 cleanup 擦除 row 当前 candidate；同时为 SecretStore.put → DB stage 窗口建立 durable owner。
4. 保留 AIO destroy failure 的 lifecycle record，并避免用单次 list snapshot 删除尚未 materialize 的 cleanup intent。
5. 修复 quarantine-full 和 cross-binding health 污染，增加超过上限、invalid/hung peer 的确定性测试。
6. 补齐测试类型、janitor teardown、PostgreSQL downgrade/完整 schema 与全量 backend Gate，并修正文档 reader 上限。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭第 3 节的 3 个 P1：DELETE 不能在 claim 之后仍留下无 lease runtime；stop recovery 的 post-claim 失败不能恢复出 active 空壳；rotation candidate cleanup 不能删除 row 当前凭据。第 4 节的 AIO durability、cleanup liveness、pre-stage secret ownership 和 per-binding health 也应形成确定性回归，再完成真实 PostgreSQL、多副本、进程恢复和全量 backend Gate。
