# 多租户 Agent 发布平台 - M3 第六轮代码复审

**状态：** 已复审，待修复
**日期：** 2026-07-16

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第五轮代码复审：[2026-07-16-m3-feishu-channel-partial-code-fifth-review.md](./2026-07-16-m3-feishu-channel-partial-code-fifth-review.md)
- 第五轮修复报告：[2026-07-16-m3-feishu-channel-partial-fifth-review-fix-report.md](./2026-07-16-m3-feishu-channel-partial-fifth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第五轮固定点：`5211479182c83c09a837f05e4d83764f2823af30`
- 当前 `HEAD` 仍为 `5211479182c83c09a837f05e4d83764f2823af30`；本轮修复尚未提交，复审目标是工作区差异。
- 固定差异：`git diff 5211479182c83c09a837f05e4d83764f2823af30 -- <9 个相关 backend 文件>`
- 实现差异规模：9 个文件，1116 行新增、50 行删除。
- 第五轮 review 与修复报告是未跟踪文档，作为本轮规范和修复声明来源读取，不计入上述实现差异规模。
- 工作区已有的 `config.yaml`、`frontend/src/components/workspace/workspace-header.tsx` 及其他既有未跟踪文件不属于本轮修复，未纳入结论。
- 复审采用 Spec 与 Standards 两条独立轴；以下严重级别保留各轴原始判断，不跨轴重新排序。

---

## 1. 复审结论

第五轮的 owner lifecycle 与正常 sandbox sync deadline 已真实关闭：Published `start_run()` 在 model 校验、Run/Thread SQL 持久化和 worker 创建前建立 owner/effective-config scope；Feishu sandbox acquisition 已切到 async lifecycle；单文件与整批 sandbox sync 也有 60/120 秒应用级边界。对应 `@no_auto_user` SQL、owner custom model、跨 binding 非阻塞和 timeout 回归均通过。

但 durable cleanup 与 late acquisition 补偿仍未达到可合并标准。本轮 Spec 轴确认 **4 个 P1、3 个 P2**：同步启动恢复在 Supervisor 全局锁内执行；outbox 可在 producer 仍可能写入时被恢复器提前完成；binding 删除可让 job 永久失去恢复者；late acquire 会裸 `release` 可能已被其他请求复用的 sandbox；另外 health、永不返回的 late acquisition 和周期恢复任务仍有可靠性缺口。

Standards 轴未发现书面标准硬违规，记录 3 个判断性设计 smell。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Published owner scope 已覆盖完整启动生命周期：** owner ContextVar 与 effective config 在 model allowlist、Run/Thread SQL 持久化和 worker 创建前生效，父 task 返回后恢复。
- **真实 SQL owner 回归已补齐：** `@pytest.mark.no_auto_user` + SQLite SQL repositories 断言 Run 与 ThreadMeta 均落到 `owner-a`，worker 内也保持同一 owner。
- **owner custom model 不再被 global-only allowlist 误拒：** model 校验发生在 owner effective config 内。
- **async sandbox acquisition 主路径已落地：** Feishu 使用 `acquire_async()`；同步兼容 provider 通过 `to_thread()`，不会直接阻塞 Gateway event loop。
- **正常 sandbox sync deadline 已落地：** acquisition 15 秒、单文件 sync 60 秒、整批 sync 120 秒，timeout/cancellation 进入 cleanup 安排。
- **stop 不再主动取消 critical cleanup：** card/progress/background tasks 与 `_cleanup_tasks` 分组；stop 对 cleanup 做 2 秒 drain 后保留强引用。
- **远端删除具备有限重试，cleanup job 已有本地 outbox：** JSON 使用临时文件替换，startup 与周期任务能够读取同 binding job。
- **README/CLAUDE 已同步，新增行为具备定向测试。**

---

## 3. Spec 轴：P1 问题

### 3.1 [P1] binding 启动在 Supervisor 全局锁内同步恢复全部 cleanup job，破坏跨 binding 隔离

**相关位置：**

- `backend/app/channels/feishu.py:605-659`
- `backend/app/channels/feishu.py:1334-1366`
- `backend/app/channels/supervisor.py:277-290`
- `backend/app/channels/supervisor.py:394-401`

**问题说明：**

动态 `FeishuChannel.start()` 在 WebSocket ready handshake 之前同步 `await recover_published_attachment_cleanups()`。Supervisor 的 `start_binding()` / `load_active_bindings()` 又在单个进程级 `_lifecycle_lock` 内等待整个 `channel.start()`。

恢复会串行遍历该 binding 的所有 job。每个 job 最多先等待 15 秒 sandbox acquire，而 `sandbox.delete_file()` 自身没有应用级调用 deadline；job 数量也没有单次恢复上限或总预算。一个 binding 的大 backlog、故障 provisioner 或挂起 delete 因此能长期占住 Supervisor 全局锁，使其他 binding 的 start、stop、restart 和 health transition 全部等待。

这违反开发计划 F3.2 的“单个绑定启动失败不影响其他绑定”与总验收 #14 的动态生命周期隔离。新增“一个正常消息不受另一个 stalled acquire 影响”的测试只覆盖消息准入，没有覆盖 Supervisor 持锁启动恢复。

**建议修复：**

1. `start()` 只做快速、只读的 backlog 检查并标记 unhealthy，WebSocket ready 后把恢复交给 per-binding 后台 coordinator；不要在 ready handshake 前同步清空全部 job。
2. 将进程级 lifecycle lock 拆为 registry 锁与 per-binding lock，不能跨外部网络、sandbox 或 cleanup I/O 持有全局锁。
3. 为单次 recovery pass 设置 job 数、总时长和单次 delete deadline；未完成 job 留给下一轮。
4. 增加双 binding 回归：A 的 recovery acquire/delete 永久阻塞时，B 的 start/stop/restart 仍在短 deadline 内完成。

---

### 3.2 [P1] outbox 可在 sync producer 结束前被恢复器确认完成，之后重新产生无记录残留

**相关位置：**

- `backend/app/channels/feishu.py:1289-1314`
- `backend/app/channels/feishu.py:1334-1375`
- `backend/app/channels/feishu.py:1407-1461`

**问题说明：**

取消或 timeout 后，代码先持久化 cleanup job，再等待最多 2 秒让不可取消的 `to_thread(update_file_from_path)` producer 退出；producer 未结束时，job 已经对周期恢复、新 channel 实例或其他 Gateway replica 可见。

job 没有 `producer_pending/ready` phase、lease、claim 或 fencing token。恢复器可以在旧 producer 仍阻塞时：

1. 删除当时可见的 remote/host 文件；
2. 将 outbox JSON unlink，认为 cleanup 已完成；
3. 随后旧 producer 恢复并再次写入 remote 文件。

如果进程或旧 channel 在 producer 完成后的第二次 in-memory cleanup 前退出，remote 文件已经重新出现，而 durable job 已被提前删除，后续没有任何恢复依据。restart 测试只在旧 producer 已释放且首次 delete 已失败后才创建新 channel，未覆盖“恢复先于 producer 完成”的窗口。

这破坏第五轮要求的“只有 remote 与 host 删除都确认后才完成 outbox”；在仍可能写入的 producer 存在时，一次瞬时 delete 不能构成最终确认。

**建议修复：**

1. 把 job 建模为持久状态机：`producer_pending → ready_to_delete → deleting → completed`。
2. 同进程 coordinator 在 producer 真正退出后以原子 CAS/fencing token 推进到 `ready_to_delete`；普通周期恢复不能处理仍有活跃 producer lease 的 job。
3. startup 在确认原进程 lease 已过期后才接管 `producer_pending` job，因为进程重启意味着旧 producer 已不存在。
4. 增加回归：worker 阻塞时启动 recovery/restart，确认 job 不会提前完成；释放 worker 并模拟旧实例退出后，最终 remote/host 和 outbox 均为空。

---

### 3.3 [P1] cleanup pending 时仍允许删除 binding，进程退出后 outbox 永久无人恢复

**相关位置：**

- `backend/app/channels/feishu.py:744-767`
- `backend/app/channels/feishu.py:1155-1169`
- `backend/app/channels/feishu.py:1378-1389`
- `backend/app/gateway/routers/published_agent_channels.py:303-315`

**问题说明：**

DELETE 路由对 active binding 调用 `stop_binding()`，但 stop 对 critical cleanup 只 drain 2 秒；pending task 与 outbox 仍存在时，路由随后直接删除 binding row 和 secret。

当前 outbox 只由同一个 `binding_id` 的 channel startup/30 秒周期任务扫描。binding row 被删除后，Supervisor 不会再实例化该 binding。若旧进程在 in-memory cleanup 完成前退出，job、host/remote 残留将永久没有恢复者。现有恢复设计因此覆盖 restart，却没有覆盖第五轮报告明确列出的 binding 删除生命周期。

**建议修复：**

1. 将 cleanup recovery 提升为不依赖活跃 binding/secret 的 Gateway 级 janitor；job 已包含 owner、thread 与受限 virtual path，删除不需要 Feishu credential。
2. 或在 binding 删除前要求 cleanup 全部完成；超时则返回冲突/accepted-delete 状态并保留 tombstone，直到全局 recovery 完成再物理删除。
3. 增加 `pending cleanup → DELETE binding → 模拟进程重启` 回归，最终必须删除 remote、host、outbox 和 tombstone。

---

### 3.4 [P1] late acquisition 无条件 release 可能释放另一个请求正在使用的同 thread sandbox

**相关位置：**

- `backend/app/channels/feishu.py:1175-1238`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:668-677`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:881-905`

**问题说明：**

acquire timeout/cancellation 后，Feishu 保留 shielded acquisition task；task 晚返回任意字符串 sandbox id 时，补偿逻辑无条件调用 `sandbox_provider.release(sandbox_id)`。

但 AIO `acquire_async()` 会优先复用同 thread 的进程内 sandbox。典型竞态是：请求 A 正在创建/使用 thread sandbox，请求 B 等待相同 thread lock 并超时；B 的 late task 随后返回 A 已经激活的 cached id。B 的补偿 `release()` 会从 `_sandboxes` 与 `_thread_sandboxes` 删除该活跃映射并放入可淘汰 warm pool，导致 A 的后续 `get()` 失败或容器被驱逐。

当前 provider contract 只返回裸 id，调用方无法知道本次 acquire 是新建 lease、复用还是仅观察到已有资源，因此不能安全地通过裸 id 做补偿释放。现有测试的 fake provider 每次返回独占 id，没有覆盖 reuse。

**建议修复：**

1. 让 provider 返回具有 ownership/lease token 的 acquisition handle，并仅释放本次 acquire 实际持有的 lease；provider 内部用 refcount 或 generation 做条件释放。
2. 或提供 provider 级 cancel/abandon API，由 provider 决定 late completion 是否需要回收，调用方不要按 sandbox id 猜测所有权。
3. 增加同 thread 双请求回归：第二次 acquire timeout 后晚返回第一个请求的 id，不得移除第一请求仍在使用的 active mapping。

---

## 4. Spec 轴：P2 问题

### 4.1 [P2] mounted/local cleanup 删除失败后仍可能把 binding 报为 healthy

**相关位置：**

- `backend/app/channels/feishu.py:1274-1287`
- `backend/app/channels/feishu.py:1334-1375`

**问题说明：**

recovery 对 `sandbox_id == "local"` 或 `uses_thread_data_mounts` 的分支中，如果 `_delete_published_host_files()` 返回 `False`，代码只 `continue`：job 保留，但没有设置 `_attachment_cleanup_unhealthy=True`。当本轮没有其他失败时，方法最后也不会因为 `completed != len(jobs)` 自动标记 unhealthy。

Supervisor 因而可能持久化 `health=healthy`，虽然 outbox 和 host 残留仍存在。这与 F3.2 的每 binding 健康态以及 README/CLAUDE 的“pending outbox keeps binding unhealthy”声明不一致。

**建议修复：**

- 让 health 从当前 binding 的有效 outbox/invalid job/in-flight 状态派生，而不是由多个并发路径直接写一个 bool；任何未完成 job 都必须 unhealthy。
- 增加 mounted host unlink 失败回归，断言 outbox 保留且 Supervisor/数据库 health 为 unhealthy，成功重试后才恢复 healthy。

---

### 4.2 [P2] acquire deadline 只约束前台请求，late cleanup 本身可永久等待且不可恢复

**相关位置：**

- `backend/app/channels/feishu.py:1193-1238`
- `backend/app/channels/feishu.py:1718-1729`

**问题说明：**

前台在 15 秒后返回 timeout，但 `_release_late_sandbox_acquisition()` 会无限等待 shielded acquire task。provider 永不返回时，该 cleanup task 永久保留 channel、provider 和 request 相关引用；stop 只等待 2 秒且不取消它。late acquisition cleanup 也没有 durable job，进程退出时无法确认远端 provisioner 是否已创建容量。

**建议修复：**

- 将 acquisition 的取消/补偿纳入 provider 自身的 lease 状态机，并给后台等待设置最终 deadline 与可观测状态。
- 如果后端 create 可在调用方退出后完成，持久化 acquisition operation id，由 provider/Gateway janitor 查询并条件释放。
- 增加永不返回 acquisition、进程 shutdown 与后端晚创建三类测试。

---

### 4.3 [P2] 一次 health 持久化异常会永久终止该 binding 的周期 cleanup recovery

**相关位置：**

- `backend/app/channels/feishu.py:1378-1405`
- `backend/app/channels/supervisor.py:338-361`

**问题说明：**

30 秒 recovery loop 在每次 `recover_published_attachment_cleanups()` 后直接 `await _runtime_health_callback(...)`。如果数据库 health update 瞬时失败，异常会逃出整个 while loop；task 只被 done callback 记录日志，不会重启。此后仍在 outbox 的失败 job 将不再周期重试，直到人工 restart binding 或重启 Gateway。

**建议修复：**

- recovery loop 必须逐轮捕获非取消异常、记录有界退避并继续；cleanup retry 不能依赖 health 写入成功。
- 将 cleanup execution 与 health projection 解耦，health 更新失败保留 dirty projection，由下一轮补写。
- 增加 repository `update_health` 首次失败、恢复后下一轮 cleanup/health 均成功的回归。

---

## 5. Standards 轴

### 5.1 书面标准检查

未发现书面标准硬违规：

- README/CLAUDE 已同步，满足 `backend/CLAUDE.md` 的文档更新策略；
- 改动位于 App 层，没有引入 Harness → App 反向依赖；
- 新增行为具有类型、公共 docstring 与测试；
- Ruff 与格式检查通过。

### 5.2 判断性设计 smell（不改变 Spec 严重级别）

1. **Divergent Change：** `feishu.py:1102-1562` 让同一个 Channel 同时承担 HTTP 下载、sandbox admission、JSON outbox、恢复调度、删除重试和健康上报；本轮该文件新增约 500 行。建议拆出 `PublishedAttachmentLifecycle` 与 `CleanupOutbox`，Channel 只负责平台消息编排。
2. **Duplicated Code / Data Clumps：** `_background_tasks` / `_cleanup_tasks` 以及两套 track/finalize 逻辑同形，`(task, name, msg_id)` 持续成组传播。建议使用 typed `TaskRegistry`，通过 lifecycle policy 表达 cancel、drain 与 durable ownership。
3. **Primitive Obsession：** 单个 `_attachment_cleanup_unhealthy: bool` 同时表示 durable backlog、invalid job、in-flight cleanup 和删除失败，且被实时 cleanup 与周期恢复并发写入，无法准确表达真实状态。建议串行 coordinator，并从结构化 job/error 状态派生 health projection。

---

## 6. 第五轮 finding 关闭状态

| 第五轮 finding | 第六轮状态 | 说明 |
|---|---|---|
| 3.1 owner scope 晚于 Run/Thread 持久化 | **已关闭** | scope 覆盖 model、SQL persistence、worker creation；真实 SQL/no-auto-user 回归通过 |
| 3.2 deferred cleanup 被 stop 取消、删除失败不可恢复 | **部分关闭** | stop 不再取消 cleanup、远端删除有重试、outbox 已落地；producer fencing、binding delete/global recovery 和 bounded startup 仍缺失，见 3.1–3.3 |
| 3.3 async dispatcher 同步 acquire sandbox | **主问题已关闭，补偿路径仍有 P1** | async acquisition 不阻塞 event loop；late completion 裸 release 可能破坏复用 sandbox，见 3.4 |
| 4.1 正常 sandbox sync 无应用 deadline | **已关闭** | 15/60/120 秒 admission boundary 已落地，timeout 进入 cleanup 安排 |

---

## 7. 验证记录

### 7.1 第五轮直接回归

执行 owner SQL lifecycle、Feishu parser/admission、Supervisor、owner channel API 与 Published Run flow：

```text
97 passed, 1 warning in 10.83s
```

这些测试证明 owner scope、async acquire 主路径、常规 stop drain、单实例 outbox retry 与 sync deadline 生效，但未覆盖本报告第 3、4 节的跨实例 producer fencing、binding DELETE、same-thread reuse、mounted unlink failure 和 health callback failure。

### 7.2 M3 聚焦集

执行 README/CLAUDE 所列 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件：

```text
303 passed, 8 skipped, 5 failed, 6 warnings in 48.41s
```

5 项失败均来自本轮未修改的 Windows LocalSandbox 基线路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项是 Windows host path 反向映射/roundtrip 语义，1 项要求本机不存在的 `/bin/sh`。本轮未修改 `local_sandbox.py`，因此没有把这些失败判定为本轮回归，也不据此声称对应用例已通过。

### 7.3 静态、格式、编译与差异检查

```text
ruff check --no-cache <7 changed Python files>: All checks passed!
ruff format --check --no-cache <7 changed Python files>: 7 files already formatted
python -m compileall <3 changed source files>: passed
git diff --check <fixed worktree diff>: passed
```

本轮没有新增或修改数据库 migration；cleanup recovery 使用应用状态目录内的 JSON outbox。

### 7.4 尚未完成的 Gate

- 未取得全量 backend `pytest tests -q` 的最终通过报告。
- 未在 Linux CI 或修复 Windows LocalSandbox 基线后跑通完整 M3 集。
- 未执行真实 PostgreSQL、双 Feishu App、远程 AIO/provisioner 与多 Gateway replica smoke。
- 未验证 producer/recovery fencing、binding 删除后的全局 cleanup、same-thread late acquisition lease 与 process-crash recovery。

---

## 8. 建议修复顺序

1. 先为 outbox 增加 producer phase、lease/claim 与 fencing，阻止 worker 结束前被恢复器提前完成。
2. 将 cleanup recovery 提升为 binding-independent 全局 janitor，或以 tombstone 阻止 pending cleanup 的 binding 被物理删除。
3. 改造 sandbox acquisition contract，引入 lease/ownership token，禁止 late completion 按裸 sandbox id release。
4. 从 Supervisor 全局 lifecycle lock 中移除同步 recovery；使用 per-binding coordinator、总预算和单次 I/O deadline。
5. 从结构化 cleanup 状态派生 health，修复 mounted delete failure，并让周期 loop 在 health 写入失败后继续。
6. 补齐上述竞态回归，再重跑第五轮直接集、M3、全 backend、Linux、真实 PostgreSQL/Feishu/AIO 与多副本 Gate。

---

## 9. 最终判定

**Ready to merge：No。**

第五轮的 owner scope 与正常 admission deadline 已关闭，但当前 outbox 仍可能在 producer 结束前被提前完成，binding 删除后也可能永久失去恢复者；late acquisition 补偿还可能释放其他请求正在使用的 sandbox。完成第 3、4 节修复并补齐跨实例、删除和 lease 回归后，建议进行第七轮复审。
