# 多租户 Agent 发布平台 - M3 第十一轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-20

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第十轮代码复审：[2026-07-20-m3-feishu-channel-partial-code-tenth-review.md](./2026-07-20-m3-feishu-channel-partial-code-tenth-review.md)
- 第十轮修复报告：[2026-07-20-m3-feishu-channel-partial-tenth-review-fix-report.md](./2026-07-20-m3-feishu-channel-partial-tenth-review-fix-report.md)
- 报告结构参考：[2026-07-13-m1-agent-control-plane-code-seventh-review.md](./2026-07-13-m1-agent-control-plane-code-seventh-review.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第十一轮固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 当前 HEAD 仍为固定点；第十轮修复是未提交工作区差异，没有新增 commit
- 相关范围为 17 个已跟踪 backend 文件，以及新增迁移 `2026_07_17_channel_deletion_state.py`
- 已跟踪 backend 差异规模：3292 行新增、319 行删除；新增迁移另行纳入
- `config.yaml`、前端文件、图片、备份和既有临时目录不属于本轮修复，未纳入结论
- 继续采用 Spec 与 Standards 两条独立复审轴；两条轴不交叉重排严重级别

---

## 1. 复审结论

第十轮修复关闭了多项确定性缺陷：runtime claim 返回后的 DELETE 已增加 matching-token final confirm；`restore_deleting` 不再在本地注册完成前提前恢复 `active`；rotation candidate 的普通 cleanup 会先恢复状态机；AIO transient destroy failure 会保留 durable intent；per-binding cleanup index 已隔离其他 binding 的 invalid global record；测试签名和 SQLite 双向迁移断言也已补齐。

但当前仍未达到可合并标准。本轮 Spec 轴确认 **3 个 P1、3 个 P2**：stop/lease 只证明数据库 token 消失，不能证明 Feishu SDK runtime 已退出；pending-secret janitor 可以基于过期快照删除已经成为当前凭据的密文；AIO 周期 reconcile 会把其他 Gateway 正在使用的 sandbox 当作本地 warm capacity 并在 idle 后销毁。三个 P2 分别是 cleanup reader 在 10 个永久挂起路径后再次失去进展、进程 kill 留下的 `creating` lifecycle 无恢复者，以及 POST create 仍存在普通 `put()` 到数据库落行之间的无主密文窗口。

Standards 轴确认类型标注、Ruff、format、SQLite migration 和 harness boundary 已关闭；但全量 backend/PostgreSQL Review Gate 仍无成功证据，M3 聚合集本轮还出现一次新增 cleanup 测试的顺序相关失败。代码注释也仍把实际最多 10 个 daemon readers 描述为 bounded pair。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **claim 后 DELETE 的本地发布窗口已关闭：** `_running`、dynamic registry 和 health 投影后增加 matching-token `confirm_runtime()`；DELETE 在 confirm 前撤销 lease 时，启动方会清理本地 runtime。
- **stop 失败恢复的 provisional 状态已改善：** `restore_deleting=True` 的 claim 不再提前把 tombstone 改成 `active`；registry、health 和 cancellation 的 post-claim 测试均能保留 `deleting`。
- **rotation candidate 的顺序恢复已关闭：** 普通 cleanup 会先运行 `recover_staged_secret_cleanup()`，非 deleting row 的当前 `secret_ref` 还有 erase guard。
- **AIO transient destroy 与 absent snapshot 已改善：** destroy 抛错时保留 operation/`cleanup_pending`；单次 backend snapshot 缺席不再确认 cleanup。
- **per-binding health 的 global invalid 污染已关闭顺序场景：** health 只读取 binding-specific index；其他 binding 的 invalid JSON 不再直接污染 clean binding。
- **测试类型标注缺口已关闭：** 对 diff 内新增/修改签名执行 AST 复核，未发现第十轮列出的缺失项。
- **SQLite migration 双向路径已补齐：** 新增列的类型、nullable/default、downgrade 和 re-upgrade 均有测试；本地 PostgreSQL 仍按环境 skip。
- **Ruff、format、compileall、`git diff --check` 和 harness boundary 均通过。**

---

## 3. Spec P1 问题

### 3.1 [P1] lease release/expiry 不能证明 Feishu runtime 已退出，stop 失败会产生双实例或幽灵机器人

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`_stop_runtime()` 只有在 `channel.stop()` 成功返回后才取消 heartbeat、release lease 和移除 registry；但它的调用方在 stop 抛错时会直接丢弃本地 runtime entry、取消 heartbeat 并 release token。`_monitor_runtime_lease()` 观察到 durable revocation 后，若 `channel.stop()` 抛错，也只记录异常然后永久退出。DELETE 随后把 lease expiry 当成 quiesced，可以删除 secret 和 row；它没有任何证据证明 SDK worker/thread 已真正退出。

delete 的 stop-failure recovery 还会在 release 旧 token 后立即启动 replacement。使用一个“stop 抛错且保持 `is_running=True`”的 channel fake，确定性结果为：

```text
delete_error=RuntimeError
instances=2
states=[True, True]
row_status=active
replacement_lease_present=True
```

即旧 WebSocket 仍活着，新 WebSocket 也已确认 lease。现有测试中的失败 fake 会在抛错前把自身标记为 stopped，因此没有覆盖真实 SDK thread 无法关闭的失败形状。

同一根因还影响非 runtime 所在副本执行 STOP：`stop_binding()` 的本地 `_running` 为空时，`_stop_runtime()` 直接 no-op，随后 `deactivate()` 清除远端 token并返回成功；远端 runtime 至少会继续到下一次 heartbeat，若其 stop 再失败就会永久成为 `inactive + 无 lease` 的幽灵实例。

这违反 F3.2 的“先 stop”生命周期语义、每 binding 单 runtime 不变量，以及第十轮修复报告所声明的 DELETE 等待 release/expiry 后即可安全物理删除。

**建议修复：**

- 把“实际 transport/SDK worker 已退出”的 matching acknowledgement 作为 lease release 的前置条件；stop 抛错时保留 fencing ownership并周期重试，不能启动 replacement。
- 远端 STOP 应先写 revocation，再等待原 lease owner 明确 release；未确认退出时返回 retryable 409/503，而不是提前返回成功。
- 若底层 SDK 无法可靠确认退出，需要引入 provider-side generation fencing，使旧 runtime 即使线程存活也无法继续投递/接收业务事件。
- 增加 stop 抛错且 `is_running/thread_alive` 仍为 true、远端 STOP、lease expiry 后接管三组 barrier 测试。

### 3.2 [P1] pending-secret janitor 的检查后删除竞态可擦除已经成为当前凭据的密文

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [secret_store.py](../../../backend/packages/harness/deerflow/publishing/secret_store.py)
- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`_recover_pending_secret_ingests()` 先读取 binding row，在内存中计算 pending ref 是否已被 row 拥有，然后基于这个快照执行 `SecretStore.delete()`。这一段既没有 binding lifecycle lock，也没有 per-ref claim/CAS；PATCH 的 `stage_secret_cleanup() → acknowledge_pending() → update_credentials()` 可以在 owner check 和 delete 之间完成。

确定性 barrier：janitor 先读到旧 row 并暂停，PATCH 随后完成 stage、ack 和 current-ref 切换，再恢复 janitor。结果为：

```text
row_points_to_new_ref=True
new_ciphertext_missing=True
```

如果 runtime 已在 janitor 删除前解密新凭据，PATCH 可以成功并继续回收旧 secret，最终数据库只剩一个指向不存在密文的当前 ref；下一次 restart/start 将永久失败。120 秒 grace 只能降低发生概率，无法消除长生命周期锁、数据库停顿或跨进程调度后的 TOCTOU。

这说明第十轮 P2 的 durable pending owner 只关闭了“可枚举”问题，没有关闭 owner transfer 与 janitor erase 的原子性。

**建议修复：**

- 为 pending ref 增加可持久化的 janitor claim；janitor 只有在 matching claim 后才能删除，PATCH 的 DB stage/ack 必须检测并拒绝已被 claim 的 ref。
- 或把 pending ingest 放入数据库事务/outbox，使“最终 owner recheck → erase”与“binding stage → owner transfer”共享同一行锁/CAS。
- 增加同进程与双 Gateway barrier：janitor owner check 后暂停，PATCH 完成 row switch，恢复 janitor；断言 current ref 永远不可删除。

### 3.3 [P1] 周期 AIO orphan reconcile 会销毁其他 Gateway 正在使用的 active sandbox

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)
- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py)

**问题说明：**

`_reconcile_orphans()` 会把 backend `list_running()` 返回、但当前进程本地 maps 不认识的所有 sandbox 无条件放入本进程 `_warm_pool`。新增的 always-on idle checker 每 60 秒再次运行 reconcile；当本地 warm timestamp 超过 `idle_timeout` 后，`_cleanup_idle_sandboxes()` 直接调用 backend destroy。

在两个 Gateway 共用 Docker/provisioner backend 时，Gateway B 无法区分 Gateway A 正在执行 Run 的 active sandbox，却会把它当成自己的未使用 warm capacity；默认约 10 分钟后 B 可以销毁 A 的活跃运行环境。A 的真实活动不会更新 B 的 `_last_activity` 或 warm timestamp。

代码注释已经明确承认“无法区分 orphan 与其他进程正在使用”，但当前策略仍把这种对象纳入本地 idle eviction。它违反 M2/M3 的多租户运行隔离，也与本轮新增的跨 Gateway lifecycle/attachment recovery 目标冲突。

**建议修复：**

- 为 sandbox 建立 durable owner/heartbeat lease，只有确认 owner 失效后才允许其他 Gateway adopt 为 warm/cleanup。
- `cleanup_pending` recovery 只能销毁持有 matching cleanup intent/generation 的目标；普通 discovery target 不应自动成为本进程 idle eviction 候选。
- 增加两个 provider 共享 fake backend 的测试：A 持续使用 sandbox，B 多轮 reconcile + 超过 idle timeout，断言 B 不得 destroy；A owner lease 失效后才允许接管。

---

## 4. Spec P2 问题

### 4.1 [P2] quarantine liveness 只把永久饥饿阈值从 8 个挂起 reader 移到了 10 个

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)

**问题说明：**

前 8 个永久挂起 reader 会进入 quarantine 并释放两个 logical permits；第 9、10 个在 quarantine 满后进入 saturated map，并永久持有两个真实 permits。此后 `_ATTACHMENT_CLEANUP_READ_SLOTS.acquire(blocking=False)` 对所有正常 tail path 都失败，global scan 只能持续 timeout。

确定性专项结果：

```text
quarantined=8
saturated=2
normal_tail=timeout
```

当前回归只构造 8 个 hung jobs 加 1 个正常 job，因此恰好没有占满两个 saturated slots；它不能证明修复报告所称的“quarantine 满后仍保留正常 path admission”。

**建议修复：** 使用真正可终止的隔离单元（例如可 kill 的子进程）读取不可信持久状态，或永久保留不允许 saturated work 占用的正常扫描容量；新增 10 个永久挂起 path、正常尾部 job 和多轮 cursor 的确定性测试。

### 4.2 [P2] 进程在 AIO lifecycle 仍为 `creating` 时被 kill，durable cleanup 没有恢复者

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)
- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py)

**问题说明：**

`_begin_backend_create_operation()` 在调用 backend create 前写入 `state="creating"`；只有进程内 cancellation handler 有机会把它改成 `cleanup_pending`。若进程在 backend create 已开始、但 cancellation handler 尚未运行时被 kill，新进程的 reconcile 只收集 `cleanup_pending`，完全忽略 stale `creating`。

目标稍后 materialize 后会被当作普通 warm sandbox 收养；当 `idle_timeout=0` 时 idle eviction 被关闭，container 和 `creating` record 都会永久存在。当前所谓 materialization recovery 测试预置的仍是 `cleanup_pending`，没有覆盖真正的 `creating + process kill`。

**建议修复：** 给 `creating` 增加 owner heartbeat/lease 和 stale transition；owner 失效后以 generation + sandbox file lock 原子转换为 cleanup pending，并只在 matching target 实际出现、destroy 成功后确认。补充真实进程 kill 或双 provider barrier。

### 4.3 [P2] POST create 仍有 `SecretStore.put → DB create` 的无 durable owner 崩溃窗口

**相关文件：**

- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)
- [secret_store.py](../../../backend/packages/harness/deerflow/publishing/secret_store.py)
- [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py)

**问题说明：**

PATCH 已改用 `put_pending()`，但 POST create 仍先调用普通 `SecretStore.put()`，随后才执行 repository create。进程在密文写入后、数据库 row 提交前退出时，该 ref 既不在 binding row，也没有 pending record；janitor 无法枚举，密文会永久泄漏。异常补偿中的 `delete()` 再次失败时同样没有 durable retry owner。

**建议修复：** 由路由预生成 binding id，create 与 PATCH 统一使用 `put_pending()`；数据库 row 提交后 matching ack，失败/崩溃由 janitor恢复。增加“put 返回后进程退出”和“DB commit 后 ack 前退出”两条故障注入测试。

---

## 5. Standards 轴

### 5.1 全量 backend / PostgreSQL Review Gate 仍未闭环

`backend/CLAUDE.md` 要求 bug fix 运行完整 suite 且通过，开发计划要求迁移在 SQLite 与 PostgreSQL 上双向可执行。本轮本地 migration 路径仍有 1 个 PostgreSQL skip；第十轮修复报告记录的全量 `pytest tests -q` 在 300 秒、52% 处超时，当前仍没有成功的全量汇总。

本轮 M3 聚合集还出现一次新增测试失败：`test_cleanup_discovery_reaches_normal_job_when_quarantine_is_full` 期望 `max_quarantine == 8`，聚合运行实际为 7；该测试独立重复 10 次均通过，说明 global future/thread 状态存在顺序相关或 teardown 不完整。它不改变第 4.1 节的生产 liveness 结论，但当前不能据此声明 M3 Review Gate 全绿。

### 5.2 reader 注释仍与实际资源上限不一致

[feishu.py](../../../backend/app/channels/feishu.py) 的 global scan 注释仍写“a bounded pair of daemon readers”，而实现和 README/CLAUDE 正文允许 8 个 released-slot quarantine readers 加 2 个 saturated readers，实际 daemon reader 上限为 10。应明确区分 logical active slots 与实际 OS threads，避免运维人员按错误资源上限排障。

### 5.3 判断性设计 smell（不改变 Spec 严重级别）

1. **Duplicated Code / Hidden Temporal Coupling：** Supervisor 用多个布尔标志手工表达 claim → register → health → confirm → rollback 顺序，三个异常分支重复同一补偿调用；建议用 lifecycle guard/transaction object 收敛状态转移。
2. **Primitive Obsession / Data Clumps：** repository 和 Supervisor 用 `dict[str, Any]`、裸 status/reason/token 字符串表达越来越复杂的 lease/cleanup 状态；建议引入 typed DTO、enum 和显式 transition result。
3. **Divergent Change：** `feishu.py` 已同时负责 WebSocket、消息、附件下载、outbox、global scan、quarantine、health 和 recovery；建议拆出 attachment cleanup store/coordinator。

---

## 6. 第十轮问题关闭状态

| 第十轮问题 | 本轮状态 | 说明 |
|---|---|---|
| P1：claim 后 DELETE 注册旧 runtime | **部分关闭** | matching-token confirm 已阻止本地发布；实际 stop 失败时 lease release/expiry 仍可留下幽灵 runtime |
| P1：stop 恢复 post-claim 留下 active 空壳 | **部分关闭** | registry/health/cancel 已保留 tombstone；transport stop 失败会同时保留旧实例并启动 replacement |
| P1：PATCH 预清理误删 rotation candidate | **已关闭顺序路径** | candidate recovery 与 current-ref erase guard 已生效；pending janitor 另有独立 TOCTOU |
| P2：AIO destroy/absent snapshot 丢 intent | **部分关闭** | `cleanup_pending` transient failure 已关闭；`creating + process kill` 仍无恢复者 |
| P2：quarantine 满后失去进展 | **部分关闭** | 8 个挂起路径可前进；10 个挂起路径会占满 saturated slots |
| P2：SecretStore 到 DB stage 无 durable owner | **部分关闭** | PATCH 已有 pending record；owner transfer/delete 不原子，POST create 仍使用普通 put |
| P2：global invalid 污染 per-binding health | **已关闭顺序路径** | binding index 已隔离其他 binding 的 invalid record |
| Standards：测试类型标注 | **已关闭** | AST 复核未发现缺失 |
| Standards：迁移/全量 Gate | **部分关闭** | SQLite 双向路径通过；真实 PostgreSQL 和全量 backend 尚无通过证据 |
| Standards：reader 上限文档 | **部分关闭** | README/CLAUDE 正文已改；代码注释仍写 bounded pair |

---

## 7. 验证记录

### 7.1 第十一轮直接回归

覆盖 repository、Owner Channel API、Supervisor、AIO provider、Feishu parser/cleanup、WebSocket lifecycle、Gateway services、SecretStore 与 migration：

```text
171 passed, 1 skipped, 5 warnings in 36.23s
```

唯一 skip 为本地未提供 PostgreSQL。

### 7.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件：

```text
357 passed, 8 skipped, 6 failed, 6 warnings in 70.59s
```

其中 5 项仍是此前记录的 Windows LocalSandbox 基线；新增的 1 项是本轮 cleanup quarantine 测试在聚合集内得到 `max_quarantine=7`、独立运行重复 10 次通过的顺序相关失败。

### 7.3 静态、边界、编译与差异检查

```text
ruff check <16 changed Python files>: All checks passed!
ruff format --check <16 changed Python files>: 16 files already formatted
tests/test_harness_boundary.py: 1 passed（单独边界检查）
python -m compileall <changed source/migration targets>: passed
git diff --check -- backend: passed
```

### 7.4 专项行为验证

- stop 抛错且旧 channel 保持 running：delete recovery 产生两个同时 running 的实例，row 恢复为 active 并持有 replacement lease。
- pending janitor 在 owner check 后暂停，PATCH 完成 stage/ack/current-ref switch，再恢复 janitor：row 指向新 ref，但密文已被删除。
- 10 个永久挂起 cleanup paths：8 个 quarantined + 2 个 saturated，占满全部实际 permits，正常 tail path 永久 timeout。
- 代码路径审查确认 stale `creating` 不进入 lifecycle recovery 集合，POST create 的普通 secret ref 也不进入 pending 枚举。

### 7.5 未完成 Gate

- 未取得全量 backend `pytest tests -q` 成功汇总。
- 未执行真实 PostgreSQL upgrade/downgrade、双 Feishu App、远程 AIO/provisioner、多 Gateway leader/lease 和真实进程 kill/restart。
- M3 聚焦集仍有 5 项既有 Windows LocalSandbox failure 和 1 项本轮顺序相关测试 failure，不能声明 Review Gate 全绿。

---

## 8. 修复优先级

1. 先让 lease 与实际 runtime quiescence 绑定：stop 未确认成功时不得 release/expiry-delete，也不得启动 replacement；远端 STOP 必须等待 acknowledgement。
2. 为 pending secret owner transfer 和 janitor erase 建立同一 ref claim/CAS，确保 current ref 不会被过期快照删除。
3. 修复 AIO 跨 Gateway ownership：普通 discovery 不能进入本地 idle eviction；为 active/creating/cleanup 建立 durable lease 与 generation。
4. 关闭 10 个 hung readers 后的 global liveness，并用可终止 reader 边界替代永久 daemon thread。
5. 把 POST create 纳入 pending-ingest 协议，补齐 crash/ack 故障注入。
6. 修复聚合集测试隔离，完成真实 PostgreSQL、Linux M3 和全量 backend Gate，并同步 reader 注释。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭第 3 节的 3 个 P1：数据库 lease 消失不能替代 SDK runtime 已退出的证据；pending janitor 不能删除已由 binding row 接管的当前密文；AIO reconcile 不能销毁其他 Gateway 的活跃 sandbox。第 4 节的 cleanup liveness、stale `creating` 和 POST secret ownership 也应形成确定性回归，再完成 PostgreSQL、进程恢复、多 Gateway 和全量测试 Gate。
