# 多租户 Agent 发布平台 - M3 第七轮代码复审

**状态：** 已复审，待修复
**日期：** 2026-07-17

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第六轮代码复审：[2026-07-16-m3-feishu-channel-partial-code-sixth-review.md](./2026-07-16-m3-feishu-channel-partial-code-sixth-review.md)
- 第六轮修复报告：[2026-07-16-m3-feishu-channel-partial-sixth-review-fix-report.md](./2026-07-16-m3-feishu-channel-partial-sixth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第六轮固定点：`5211479182c83c09a837f05e4d83764f2823af30`
- 当前 `HEAD` 仍为 `5211479182c83c09a837f05e4d83764f2823af30`；第五、六轮修复均叠加为未提交工作区差异，无法从 Git 单独切出“仅第六轮”提交。
- 固定差异：`git diff 5211479182c83c09a837f05e4d83764f2823af30 -- <14 个相关 backend 文件>`。
- 实现差异规模：14 个文件，2450 行新增、78 行删除。
- 第五/六轮 review 与修复报告是未跟踪文档，作为规范和修复声明来源读取，不计入上述实现差异规模。
- 工作区已有的 `config.yaml`、`frontend/src/components/workspace/workspace-header.tsx` 及其他既有未跟踪文件不属于本轮复审，未纳入结论。
- 复审结构参考 M1 第七轮报告，并继续采用 M3 的 Spec 与 Standards 两条独立轴；严重级别保留各轴判断，不跨轴重新排序。

---

## 1. 复审结论

第六轮要求的核心方向已经落地：Supervisor 生命周期锁已拆到 binding 级；WebSocket ready 不再等待 cleanup；outbox 具备 producer lease、delete claim 与 fencing；Gateway janitor 不依赖 binding row/secret；DELETE 在已知 backlog 下返回 409；sandbox acquisition 改为 typed handle；mounted 删除失败和 health callback 异常也有定向回归。

但当前仍未达到可合并标准。本轮 Spec 轴确认 **3 个 P1、4 个 P2**：binding DELETE 的 quiesce 与物理删除之间仍有生命周期 TOCTOU；取消中的 AIO backend create 会无条件销毁晚返回容量，可能误杀后继已接管的同一 sandbox；异步 OS file-lock 等待的取消路径会并发关闭仍由工作线程使用的句柄。另有 late create 超时后容量泄漏、全局 recovery 实际不受 25-job/10-second 总预算约束且可能饥饿、health 快照竞态，以及 per-binding lock registry 无界增长。

Standards 轴未发现书面标准硬违规，记录 3 个判断性设计 smell。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Supervisor 全局 lifecycle lock 已移除：** start/stop/restart/runtime health 按 `binding_id` 串行，active bindings 并发启动；单 binding 的阻塞启动不再直接占住所有 binding 的同一把锁。
- **WebSocket ready 与 cleanup recovery 已解耦：** dynamic channel 在 ready 前只读取本地 outbox 投影，实际 recovery 在 ready 后由后台 coordinator 执行。
- **producer 与 delete 状态机主路径已落地：** outbox 使用 `producer_pending → ready_to_delete → deleting`，producer heartbeat、claim token、lease 和 file lock 可阻止正常窗口内的提前完成。
- **binding-independent janitor 已落地：** Gateway 可以从共享 outbox 枚举 binding，而不读取 Feishu secret，也不要求 binding 仍处于 active。
- **已知 backlog 下的 DELETE 会 fail closed：** 返回 HTTP 409，并保留数据库 row 与 encrypted secret。
- **裸 sandbox ID 补偿已移除：** Feishu 成功路径 accept typed `SandboxAcquisition`，超时/取消路径只调用 provider 的 abandon。
- **mounted/local 删除失败与 health callback 瞬时异常的直接问题已关闭：** job 保留时 unhealthy，health projection 失败不会终止周期 recovery。
- **第六轮直接回归、Ruff、格式与编译检查通过。**

---

## 3. Spec 轴：P1 问题

### 3.1 [P1] binding quiesce 与数据库/secret 物理删除不在同一个生命周期临界区

**相关位置：**

- `backend/app/channels/supervisor.py:290-305`
- `backend/app/channels/supervisor.py:407-423`
- `backend/app/gateway/routers/published_agent_channels.py:303-320`

**问题说明：**

`prepare_binding_delete()` 在 per-binding lock 内检查 backlog、停止 runtime、再次检查 backlog，然后返回并释放锁。路由随后才在锁外执行 `repository.delete()` 与 `secrets.delete()`。

因此 DELETE 与 start/restart/credential rotation 之间仍有确定的 TOCTOU：

1. DELETE 在 lock 内停止 runtime，并确认无 backlog；
2. DELETE 返回路由层、释放 lock；
3. 并发 start/restart 取得同一 lock，从仍存在的 row/secret 重建 runtime；
4. DELETE 再删除 row 和 secret；
5. 进程中留下一个没有数据库所有者、无法正常管理且 secret 已被擦除的孤儿 channel。

当前路由测试只覆盖“已有 backlog 时 409 → 清理 job → 顺序重试 DELETE”，没有在 quiesce 与 row delete 之间强制并发 start/restart。该窗口违反设计 §6.3 和开发计划 F3.2/总验收 #14 的单 binding 生命周期安全，也削弱第六轮“物理 DELETE fail closed”的声明。

**建议修复：**

1. 把 row 删除纳入 Supervisor 的同一 per-binding 临界区，并让 secret 删除以删除结果为依据执行；或先持久化不可逆的 `deleting` tombstone，使所有 start/restart/update 路径在物理删除完成前拒绝重建。
2. 不要只暴露 `prepare_*` 后由路由分两阶段提交；提供一个拥有完整 lifecycle transaction/状态机的删除入口。
3. 增加 barrier 回归：DELETE 完成 stop 后暂停，放行并发 start/restart，再继续删除；最终只能是“删除成功且无 runtime”或“删除被拒绝且 row/secret/runtime 一致”。

---

### 3.2 [P1] 取消中的 backend create 可销毁后继已经接管并接受的同一 sandbox

**相关位置：**

- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:664-731`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:837-875`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:955-1011`

**问题说明：**

managed handle 的 generation fencing 只应用于 `abandon_acquisition()`。当 `_create_sandbox_async()` 在 `backend.create()` 中被取消时，provider 会另起 `_destroy_late_created_sandbox()`；该任务取得晚返回的 `SandboxInfo` 后直接调用 `backend.destroy(info)`，完全不检查 thread mapping、accepted-use version 或 operation token。

同一 thread 的 sandbox ID 是确定性的，因而存在以下竞态：

1. 请求 A 持 file lock 调用 backend create，随后被最终 deadline 取消；A 释放 thread/file lock，但 backend create 在线程中继续；
2. 请求 B 进入同一 thread，backend discovery 发现 A 刚创建的容器，注册并 accept；
3. A 的 late cleanup 收到同一个 `SandboxInfo`，无条件 destroy；
4. B 的 active mapping 仍存在，但底层容器已经被旧操作销毁。

现有 `test_cancelled_async_create_destroys_backend_capacity_that_arrives_late` 只断言“无后继消费者时应 destroy”，没有覆盖 cancel → successor discover/accept → old completion 的窗口。第六轮修复解决了“旧 late waiter 按裸 ID release 后继”的一条路径，但 backend-create 补偿仍有同类 ownership 问题。

**建议修复：**

1. 为 backend create 分配 provider-owned operation/generation token，late completion 只能销毁仍由该 operation 独占且尚未被后继 adopt/accept 的容量。
2. late destroy 前在同一 thread/file-lock 协议下重新检查 active mapping 与 accepted-use version；已经被后继接管时只完成旧 operation，不得 destroy。
3. 增加确定性回归：旧 create 被取消后暂停其返回，B discover 并 accept 同一 sandbox，再放行旧 create；断言 `destroy()` 未被调用且 B 的 `get()` 仍有效。

---

### 3.3 [P1] 等待跨进程 file lock 时取消，会关闭仍由工作线程使用的锁文件句柄

**相关位置：**

- `backend/app/channels/feishu.py:1490-1515`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:837-875`

**问题说明：**

`_discover_or_create_with_lock_async()` 用 `await asyncio.to_thread(_lock_file_exclusive, lock_file)` 等待 OS lock。若 Feishu 的最终补偿 deadline 在锁仍被其他进程/请求持有时取消 acquire task，协程立即进入 `finally`；此时局部 `locked` 仍为 `False`，代码跳过 unlock，却通过另一个工作线程关闭同一个 `lock_file`。原来的 lock worker 仍可能阻塞或随后获得锁。

这造成对同一文件对象的并发 lock/close：在不同平台上可能表现为锁调用异常、获得后无人 unlock，或错误操作已复用的文件描述符。结果是同 thread 后续 acquisition 可能永久阻塞或失去跨进程互斥。`to_thread` Future 被取消并不会停止底层阻塞调用。

当前测试验证了正常 lock wait 不阻塞 event loop，也验证了 thread lock 的后继释放，但没有覆盖“取消发生在 OS lock 尚未取得”的路径。

**建议修复：**

1. 把 OS lock acquisition 封装成 cancellation-safe operation：取消只取消调用方等待，不得并发 close；底层 worker 最终返回后必须由专门 cleanup 在同一 ownership 协议内 unlock + close。
2. 可复用 `_acquire_thread_lock_async` 的 shield/late-release 思路，但必须对 OS file handle 保留强引用直到 lock worker 已确认结束。
3. 增加跨线程 barrier 回归：先占住 file lock，启动 async acquire 并取消，随后释放原锁；确认旧 waiter 不遗留锁，新的 acquire 能在短 deadline 内完成。

---

## 4. Spec 轴：P2 问题

### 4.1 [P2] backend create 超过 120 秒后仍会丢失补偿所有权并持续泄漏容量

**相关位置：**

- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:50`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:955-1011`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:1083-1118`

**问题说明：**

`_destroy_late_created_sandbox()` 最多等待 120 秒。超时后调用 `create_task.cancel()` 并返回，但 `create_task` 包装的是 `asyncio.to_thread(self._backend.create, ...)`；取消 asyncio Task 不会停止底层线程。backend create 如果在 120 秒之后成功，返回的 `SandboxInfo` 已经没有任何消费者，也没有 durable operation id。当前仅靠下次 provider 初始化时的 startup reconciliation，意味着一个长期运行的 Gateway 可在整个进程余生保留未跟踪容量。

同步 `shutdown()` 也没有等待或接管 `_late_create_cleanup_tasks`，所以它不是该窗口的可靠恢复者。这只部分关闭了第六轮 4.2 的“late cleanup 可永久等待且不可恢复”。

**建议修复：**

- 不要通过取消包装 `to_thread` 的 Task 假设 backend 调用停止；保留一个最终回调直到真实 Future 完成，或持久化 backend operation id 并由周期 janitor 查询、adopt/条件销毁。
- 让 shutdown/reconciliation 能观察未完成 create operation，而不是只枚举进程启动前已经存在的容器。
- 增加 create 超过 compensation deadline、但在同一进程稍后成功的回归，最终必须被 adopt 或 destroy。

---

### 4.2 [P2] “25 jobs / 10 秒”没有覆盖发现与全局调度，且固定前 25 个 job 会饥饿后续 ready job

**相关位置：**

- `backend/app/channels/feishu.py:187-230`
- `backend/app/channels/feishu.py:1409-1422`
- `backend/app/channels/feishu.py:1728-1760`
- `backend/app/channels/supervisor.py:444-458`

**问题说明：**

recovery deadline 在 `_read_attachment_cleanup_jobs()` 完整扫描目录之后才创建，`jobs[:25]` 只限制执行，不限制读取。全局 janitor 又先扫描一次全部 JSON 取得 binding IDs，再对所有 binding 无并发上限地 `gather()`；每个 binding coordinator 都重新扫描全部目录。因此一次全局 pass 的实际成本是近似 `O(binding_count × job_count)`，总 acquisition/delete 并发也不是 25。

此外没有排序、优先级或持久游标。若目录枚举稳定地把 25 个持续 heartbeat 的 `producer_pending` job 放在前面，后续已经 `ready_to_delete` 的 job 每轮都被切掉，可能无限饥饿。第六轮修复报告所称“recovery pass 最多读取 25 个 jobs，总预算 10 秒”与当前实现不一致。

**建议修复：**

1. 由单个全局 coordinator 每轮只解析每个 job 一次，在扫描、claim、acquire 和 delete 之前就执行统一 deadline、并发 semaphore 和 job 上限。
2. 使用持久/轮转 cursor，或优先选择 claimable `ready_to_delete` / expired lease job，确保活跃 producer 不会永久占据窗口。
3. 增加 25 个 active producer + 第 26 个 ready job、多 binding 大目录及 stalled acquire 的负载回归，验证总扫描/并发/时长均有界且 ready job 最终完成。

---

### 4.3 [P2] cleanup health 仍由并发路径覆盖单个 bool，旧快照可把新 backlog 误报为 healthy

**相关位置：**

- `backend/app/channels/feishu.py:1350-1389`
- `backend/app/channels/feishu.py:1619-1653`
- `backend/app/channels/feishu.py:1728-1772`

**问题说明：**

`recover_published_attachment_cleanups()` 开始时无条件把 `_attachment_cleanup_unhealthy` 设为 `False`，读取一次 jobs 快照，最后在 `completed == len(jobs)` 时再次清为 `False`。与此同时，前台 admission 可以持久化新 job 并把同一个 bool 设为 `True`，另一个 cleanup execution 也会读写该值。

因此旧 recovery 快照可能在新 outbox 已写入后覆盖其 unhealthy 状态；下一次 30 秒轮询前，Supervisor/数据库可以暂时显示 healthy。delete 路由会直接扫描 outbox，所以不一定误删，但 F3.2 要求的 per-binding health 与真实 durable backlog 不一致。第六轮 mounted delete 失败的直接分支已修复，health 的并发派生问题仍未关闭。

**建议修复：**

- 不让多个任务直接写共享 bool；从 file-locked store 的当前 durable jobs、active producers/claims 与 invalid-state generation 派生 health。
- recovery 完成时以 snapshot generation/CAS 提交 health；发现期间有新 generation 时必须保持 unhealthy 并触发下一轮。
- 增加 barrier 回归：旧 recovery 读取空/旧快照后暂停，前台写入新 job，再放行 recovery；最终 health 必须保持 unhealthy。

---

### 4.4 [P2] per-binding lock registry 在 binding churn 下无界增长

**相关位置：**

- `backend/app/channels/supervisor.py:145-156`
- `backend/app/channels/supervisor.py:407-423`
- `backend/app/channels/supervisor.py:487-502`

**问题说明：**

`_binding_locks` 对每个见过的 `binding_id` 永久保存一个 `asyncio.Lock`。binding 删除、runtime 停止和 Supervisor shutdown 都不会移除条目。owner 可以持续创建/删除 binding；即使每个 Agent 同时只有一个 active binding，长生命周期 Gateway 仍会按历史 binding 总数持续增长该字典。

直接在 `prepare_binding_delete()` 返回时 `pop()` 也不安全，因为同一旧 lock 上可能已有 waiter，贸然移除会让新调用得到第二把 lock。需要显式的 keyed-lock 引用/等待者生命周期。

**建议修复：**

- 使用带 refcount/waiter count 的 keyed lock registry，仅在 binding 已物理删除、无 owner、无 holder、无 waiter 时回收。
- 增加大量 create/delete/recreate 与并发 waiter 回归，既断言 registry 收敛，也断言同一 binding 不会同时出现两把有效 lock。

---

## 5. Standards 轴

### 5.1 书面标准检查

未发现书面标准硬违规：

- README/CLAUDE 已同步；
- Harness 没有反向依赖 App；
- 新增公共 API 具有类型标注、docstring 与对应测试；
- Ruff、格式和编译检查通过。

### 5.2 判断性设计 smell（不改变 Spec 严重级别）

1. **Divergent Change：** `feishu.py:1227-2014` 让 `FeishuChannel` 同时负责 WebSocket/卡片、下载、JSON outbox、文件锁、租约/claim 状态机、sandbox 补偿与 health 投影；当前累计新增约 1000 行。建议拆出 `PublishedAttachmentCleanupStore` 与 `CleanupCoordinator`。
2. **Duplicated Code / Repeated Switches：** `feishu.py:1253-1407,1580-1700` 多个转换重复“加锁 → 读取 → 检查 phase/token → replace/version+1 → 写回”，phase 分支又散布在恢复与执行路径。建议由领域状态对象集中提供原子 transition。
3. **Duplicated Code / Data Clumps：** `aio_sandbox_provider.py:598-731` 的三种 acquire 路径重复 owner 校验、thread lock、绑定和内部 acquire；多个平行 dict 保存同一 sandbox 生命周期。建议统一为返回 lease handle 的内部入口，旧 API 自动 accept，并用聚合记录承载 sandbox/owner/version/activity。

---

## 6. 第六轮 finding 关闭状态

| 第六轮 finding | 第七轮状态 | 说明 |
|---|---|---|
| 3.1 Supervisor 全局锁内同步恢复 cleanup backlog | **部分关闭** | per-binding lock、ready 后恢复和单 binding 执行 deadline 已落地；全局 janitor 的扫描、fan-out 与公平性仍不受声明的总预算约束，见 4.2 |
| 3.2 recovery 可在 sync producer 结束前完成 outbox | **已关闭主竞态** | producer phase/heartbeat/lease 与 delete claim/fencing 已落地，未发现 producer 活跃时被正常恢复器提前完成的路径 |
| 3.3 binding 删除后 job 永久失去恢复者 | **部分关闭** | global janitor 与 backlog 409 已落地；quiesce 和物理删除仍非原子，并发 start 可产生孤儿 runtime，见 3.1 |
| 3.4 late acquire 裸 release 破坏同 thread 复用 | **部分关闭** | typed handle 与 abandon generation 已落地；backend-create late destroy 与 OS lock cancel 仍缺 ownership/cancellation fencing，见 3.2–3.3 |
| 4.1 mounted/local 删除失败仍投影 healthy | **已关闭直接分支** | unlink 失败保留 outbox 且 unhealthy；通用 health 快照仍有并发覆盖，见 4.3 |
| 4.2 late acquisition cleanup 可永久等待且不可恢复 | **部分关闭** | Feishu 最终 deadline 和普通晚返回 destroy 已落地；超过 120 秒的 `to_thread` create 仍丢失补偿所有权，见 4.1 |
| 4.3 health 持久化异常终止周期 recovery | **已关闭** | recovery 与 health projection 分开捕获，下一轮继续执行 |

---

## 7. 验证记录

### 7.1 第六轮直接回归

执行 Gateway service、Feishu parser/admission、Supervisor、owner channel API、AIO provider 与 WebSocket lifecycle：

```text
110 passed, 5 warnings in 35.34s
```

这些测试证明第六轮已有的 happy path 与定向故障注入仍通过，但没有覆盖本报告的 DELETE 两阶段竞态、successor adopt 后的 old late destroy、取消中的 OS file-lock waiter、超过 120 秒的 create，以及 25+ job 公平性。

### 7.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件。第二次完整运行结果：

```text
313 passed, 8 skipped, 5 failed, 6 warnings in 59.49s
```

5 项失败与第五/第六轮基线一致，来自本轮未修改的 Windows LocalSandbox 路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

首次完整运行还出现一次 `test_loading_active_bindings_isolates_start_failures` 的数据库 health 断言失败；该用例随后独立重复 20 次全部通过，第二次完整 M3 聚焦集也通过，因此本轮不把它列为已证实 finding，但保留为并发 Gate 稳定性观察。

### 7.3 静态、格式、编译与差异检查

```text
ruff check --no-cache <12 changed Python files>: All checks passed!
ruff format --check --no-cache <12 changed Python files>: 12 files already formatted
python -m compileall <6 changed source files>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

### 7.4 未完成 Gate

- 本轮未重新执行全量 backend `pytest tests -q`；第六轮全量运行在 300 秒时无最终汇总。
- M3 聚焦集仍有 5 项 Windows LocalSandbox 基线失败，不能声明 M3 Review Gate 全绿。
- 真实 PostgreSQL、双 Feishu App、远程 AIO/provisioner、多 Gateway replica、真实进程 kill 与 Linux CI Gate 仍需部署环境验证。

---

## 8. 修复优先级

1. 先把 binding 物理删除纳入单一 lifecycle 状态机/临界区，关闭 DELETE → concurrent start 的孤儿 runtime 窗口。
2. 给 backend create/late destroy 加 operation generation fencing，并把 OS file-lock acquisition 改成 cancellation-safe ownership。
3. 为超过最终 deadline 的 backend operation 提供持久或持续可观测的补偿者，不能通过取消 `to_thread` Task 丢失结果。
4. 把 outbox 发现、调度、执行统一到有总预算、有并发上限、有公平游标的 coordinator。
5. 从 durable generation 派生 cleanup health，并实现安全可回收的 keyed binding lock registry。
6. 增加上述并发 barrier/负载回归后，重跑直接回归、M3 聚焦集、全量 backend 与部署环境 Gate。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭第 3 节的 3 个 P1：DELETE 不能在 quiesce 后被并发 start 重建；旧取消 create 不能销毁后继已接受的 sandbox；取消中的 OS file-lock waiter 不能遗留锁或并发关闭工作线程仍在使用的句柄。第 4 节的 durable compensation、bounded/fair recovery、health 一致性与 lock registry 也应形成确定性回归；同时 M3/全量 backend 与真实多副本、Feishu、AIO crash-recovery Gate 仍需补齐。
