# 多租户 Agent 发布平台 - M3 第八轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-17

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第七轮代码复审：[2026-07-17-m3-feishu-channel-partial-code-seventh-review.md](./2026-07-17-m3-feishu-channel-partial-code-seventh-review.md)
- 第七轮修复报告：[2026-07-17-m3-feishu-channel-partial-seventh-review-fix-report.md](./2026-07-17-m3-feishu-channel-partial-seventh-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第七轮固定点：`5211479182c83c09a837f05e4d83764f2823af30`
- 第八轮复审头：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 本轮修复提交：`044fa174 fix(m3): address Feishu channel review findings`
- 固定差异：`git diff 5211479182c83c09a837f05e4d83764f2823af30..044fa17489b1d064286b97ea88dee65ed08060fe -- <14 个相关 backend 文件>`。
- 实现差异规模：14 个 backend 文件，3547 行新增、124 行删除；同一提交还纳入第五至第七轮 review/fix 文档。
- 工作区已有的 `config.yaml`、`frontend/src/components/workspace/workspace-header.tsx`、图片、备份与临时目录不属于本轮修复提交，未纳入结论。
- 复审结构参考 M1 第七轮报告，并继续采用 Spec 与 Standards 两条独立轴；两条轴的严重级别不跨轴重排。

---

## 1. 复审结论

第七轮的三项关键机制已有实质改进：同进程 binding 删除/start/rotation 现在共享 ref-counted lifecycle lock；取消中的 OS file-lock acquire 会在工作线程真正取得句柄后再释放；AIO create 超过 120 秒不再主动取消结果；全局 cleanup 也新增了总 deadline、执行并发上限和持久 cursor。

但当前仍未达到可合并标准。本轮 Spec 轴发现 **3 个 P1、4 个 P2**：凭据轮换失败不会进入声明的回滚分支并会擦除旧 secret；DELETE 在数据库或 secret 删除失败时仍会留下半完成状态；AIO successor fencing 只检查本进程内存，跨 Gateway 仍可误删已接管 sandbox。除此之外，AIO/Supervisor shutdown、cleanup 扫描公平性以及跨 binding health generation 仍未闭环。

Standards 轴发现 **1 个书面规范违规**：本轮新增的三个 file-lock helper 缺少完整类型标注。另有三组既存/加重的设计 smell，不改变 Spec 轴严重级别。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **同进程 DELETE/start/rotation 的原始 TOCTOU 已关闭**：row 删除已进入同一 per-binding lifecycle 临界区，等待者共享同一个 ref-counted lock entry，retire 后只在最后一个使用者退出时回收。
- **取消中的 OS file-lock acquire 已关闭句柄竞态**：取消方不再提前关闭仍由 lock worker 使用的句柄；worker 最终取得锁后会执行 unlock/close。
- **同进程 successor adoption 已加 generation fencing**：旧 create 的补偿会重新进入 thread/file-lock 协议，并检查本进程 mapping 与 use version。
- **超过 120 秒的 backend create 不再因 warning deadline 丢失结果**：deadline 只记录错误日志，等待任务继续存活到 blocking create 真正完成。
- **cleanup 执行侧已有全局上限**：一次 pass 最多选择 25 个 claimable job，并发执行上限为 4；cursor 能在本次已经发现的集合内轮转。
- **health 已加入本进程 generation CAS**：旧本地快照不能覆盖同一进程内更新过的 dirty generation。
- **keyed binding lock registry 的 churn 泄漏已关闭**：entry 使用 `users`/`retired` 管理，删除和 shutdown 可安全回收 idle entry。
- **第七轮直接回归全部通过**：121 个定向测试通过，新增测试覆盖上述同进程 happy path 与故障注入。

---

## 3. Spec P1 问题

### 3.1 [P1] 凭据轮换启动失败被转换为 health，回滚分支实际不可达

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)

**问题说明：**

`FeishuSupervisor._start_row()` 在 channel 构造、secret 解密或 `channel.start()` 失败时捕获普通异常，清理 runtime 后返回 `BindingHealth(health="unhealthy", running=False)`，并不抛出。`rotate_binding_credentials()` 在写入新 `app_id/secret_ref` 后，虽然用 `try/except BaseException` 包围 stop/start 并实现了旧 row 回滚，但它只 `await _start_row()`，没有检查返回值。因此最常见的启动失败不会进入 rollback。

路由随后把 Supervisor 的正常返回解释为轮换成功，删除 `previous["secret_ref"]` 并返回 200。最终状态是：数据库永久指向无法启动的新凭据、旧 runtime 已停止、旧 secret 已被不可逆擦除，且调用方看到成功响应。

这违反开发计划 F3.2 的“新 secret 入库 → 重启实例”契约，也使第七轮修复报告所称的 credential rotation rollback 不成立。

**建议修复：**

- 让 rotation 使用会传播启动失败的严格入口，或让 `_start_row()` 返回可判定的结构化结果并在 `running=False` 时进入回滚；`load_active_bindings()` 所需的“失败隔离”应由调用层处理，而不是吞掉所有 start 失败。
- 只有新实例完成 ready handshake 后才能提交轮换并删除旧 secret；失败时恢复旧 row、重新启动旧实例，再删除新 secret。
- 增加确定性回归：新 secret 已落库 → 新 channel start 失败 → API 非 2xx、row/secret_ref 恢复、旧 runtime 再次 running、旧 secret 仍可读取、新 secret 被清理。

### 3.2 [P1] DELETE 仍不是 row、runtime 与 secret 的失败原子操作

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py)

**问题说明：**

`delete_binding()` 在 lifecycle lock 内先停止并移除 runtime，再调用 `repository.delete()`。如果数据库删除抛错或返回 `None`，函数直接退出，不会为原来 active 的 row 恢复 runtime；数据库仍声明 active，但本进程已经停止服务。

数据库删除成功后，路由才在 Supervisor 临界区之外执行 `secrets.delete()`。若 secret store 删除失败，row 已不存在，API 返回 500，`secret_ref` 也没有 durable tombstone/outbox 保存；重试 DELETE 只能得到 404，遗留加密 secret 无法再通过绑定生命周期回收。

所以本轮虽然关闭了“quiesce 后被同进程 start 重建”的竞态，却没有达到第七轮要求的失败原子性：操作必须成功收敛为 row/secret/runtime 全部删除，或拒绝并保持三者仍可管理。

**建议修复：**

- 为删除引入 durable `deleting` tombstone/cleanup outbox，至少持久化 `binding_id + secret_ref + desired status`，所有 start/restart/rotate 在 tombstone 存在时 fail closed。
- DB delete 失败且原 row 为 active 时，在释放 lifecycle lock 前恢复 runtime；若无法恢复，应保留明确、可恢复的 durable health/state，而不是留下“active 但无 runtime”。
- secret 擦除成功前不要丢失最后一个 durable ref；失败应可由重试或 startup janitor 收敛。
- 增加 repository delete exception/`None`、secret delete exception、进程重启恢复三组故障注入测试。

### 3.3 [P1] AIO late-create fencing 只在单进程生效，另一 Gateway 接管后仍可能被旧进程 destroy

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)
- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py)

**问题说明：**

late compensation 会重新取得 deterministic sandbox 对应的 file lock，这是必要改进；但锁内的 `operations`、`_thread_sandboxes`、`_sandboxes`、`_sandbox_infos`、`_warm_pool` 与 `_sandbox_use_versions` 都是当前 provider 进程内字典。

可复现的跨进程序列为：

1. Gateway A 发起 deterministic backend create，调用方取消，A 释放 acquisition file lock，blocking create 继续；
2. Gateway B 取得同一个 file lock，discover/create 同一个 sandbox ID，注册并 `accept` 后释放锁；
3. A 的旧 create 返回并重新取得 file lock；
4. A 看不到 B 的 mapping/use version，`adopted=False` 且 `accepted_after_start=False`，于是 destroy B 已接管的远端 sandbox。

file lock 只串行化临界区，不能让进程内 generation 变成跨进程事实源。现有 successor test 在同一个 provider 上直接调用 `_register_discovered_sandbox()`，没有覆盖第二个 Gateway/provider。

**建议修复：**

- 在 file lock 保护下写入可跨进程读取的 operation/adoption generation，内容至少包含 sandbox ID、owner/thread、operation token、accepted generation 与状态；补偿必须 compare-and-delete 自己仍拥有的 generation。
- 如果 backend 支持 lease/version/conditional delete，应优先使用 backend 原子 fencing token；不能只按裸 sandbox ID destroy。
- 增加两个独立 provider 实例共享 backend 与 filesystem 的 barrier 测试，强制执行 A cancel → B discover/accept → A completion，并断言 A 不 destroy。

---

## 4. Spec P2 问题

### 4.1 [P2] AIO shutdown 不接管未完成 create，正常 create 可在 shutdown 后重新注册容量

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py)

**问题说明：**

`_create_sandbox_async()` 在 blocking backend create 和 readiness 完成后无条件调用 `_register_created_sandbox()`；期间没有检查 `_shutdown_called`。同步 `shutdown()` 只快照当前 `_sandboxes/_warm_pool` 并记录 `_backend_create_operations` 数量，不等待、取消、接管或持久化未完成 create。

因此一个没有被调用方取消、但在 shutdown 快照时仍处于 create/readiness 的操作，可以在 shutdown 已销毁全部已知容量并返回后继续注册新 sandbox。此后 `_shutdown_called=True` 使再次 shutdown 直接返回，该容量不再被 provider 生命周期清理。若 late-cleanup task 随 event loop 关闭而取消，`_destroy_late_created_sandbox()` 也会直接传播 `CancelledError`，operation registry 不是 durable compensation owner。

**建议修复：**

- shutdown 开始时设置 create admission gate；完成中的 create 在注册前再次检查 shutdown epoch，若已关闭则 destroy 而不是注册。
- 为 async provider 提供可等待的 shutdown/drain，限时等待 operation；超时后把补偿所有权持久化并由下次启动恢复。
- 增加 create barrier → shutdown → create 返回，以及 cleanup task cancel/event-loop close 两组回归。

### 4.2 [P2] fairness cursor 只覆盖已扫描集合，目录尾部 job 仍可永久饥饿

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)

**问题说明：**

`_select_cleanup_jobs()` 的 cursor 只旋转 `_scan_all_cleanup_jobs()` 已经返回的 jobs；discovery 本身每一轮仍从 `outbox_dir.glob("*.json")` 的固定起点开始，并在 10 秒 deadline 到达时返回当前前缀。只要稳定目录前缀的解析成本持续吃满预算，尾部 job 永远不会进入 cursor，更不可能被调度。

定时故障注入用 20 个 job、每次读取约 10 ms、45 ms discovery deadline 连续跑两轮，结果仍是相同目录前缀：

```text
first  = [job-00, job-01, job-02, job-03]
second = [job-00, job-01, job-02, job-03, job-04]
timed_out = [True, True]
```

此外 `wait_for(asyncio.to_thread(...))` 只能取消 asyncio waiter，不能停止正在阻塞单个文件读取的工作线程；慢/挂起读取可越过总 deadline，并在后续 pass 叠加 worker。

**建议修复：**

- 给 discovery 本身增加持久、原子更新的扫描 cursor/pagination，使用稳定排序并从上次未扫描位置续读；选取 cursor 不能替代发现 cursor。
- 将“发现、选择、claim、执行”纳入同一个可观测总预算，单文件读取也要有可中断/隔离的上限。
- 增加大目录 + 慢前缀的多 pass 测试，断言有限轮数内尾部 ready job 一定被发现并执行。

### 4.3 [P2] 全局 store generation 与 per-binding health 语义不匹配，租户之间会互相污染健康状态

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py)

**问题说明：**

durable store 只有一个全局 `.store-generation`；任何 binding 写 job、heartbeat 或状态转换都会 bump。`_refresh_attachment_cleanup_health()` 却只读取当前 binding 的 jobs，再比较两次全局 generation。另一个 binding 在两次读取之间正常写入，就会让当前无 backlog 的 binding 得到 `store_stable=False` 并被标记 unhealthy，直到下一轮刷新。

定向验证在 binding A 扫描期间写入 binding B job，得到：

```text
after_binding_b_write = False
after_next_refresh = True
binding_a_healthy = True
```

反方向也仍有窗口：第二次 generation 读取结束后，到本地 `_commit_attachment_cleanup_health()` 之间没有跨进程 CAS。同一 binding 的另一个 Gateway 可在此时写入新 job，而旧进程仍把旧空快照提交为 healthy。当前 local generation 只能防住本进程 producer，不能证明共享 durable store 的 per-binding health。

**建议修复：**

- 使用 per-binding durable generation，并在同一个 binding file lock/事务内完成快照校验与 health projection；或直接从可原子查询的 durable backlog 事实派生 health。
- 不要用其他 binding 的变更使当前 binding unhealthy；同一 binding 的远端新 job 也不能被旧快照清为 healthy。
- 增加两个 binding、两个 channel/provider 实例的双向 barrier 测试，分别覆盖 cross-binding false unhealthy 与 same-binding stale healthy。

### 4.4 [P2] Supervisor shutdown 只快照 `_running`，可与尚未注册的 start 交错并留下 shutdown 后 runtime

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)

**问题说明：**

`shutdown()` 只为调用瞬间 `list(self._running)` 中的 binding 创建 stop task。若并发 `start_binding()` 已持有 lifecycle lock、正在等待 `channel.start()`，但尚未写入 `_running`，shutdown 快照看不到它，也没有 `_shutting_down` admission gate 或 active lifecycle task registry。shutdown 随后把 lock entry 标记 retired 并返回；原 start 仍可继续写入 `_running` 和 dynamic channel registry。

现有 shutdown 测试只覆盖“先完成 start，再顺序 shutdown”，没有 barrier 覆盖 start-ready 窗口。

**建议修复：**

- shutdown 起始先关闭新的 start/restart/rotate admission，再等待所有已进入的 lifecycle operation 退出；之后重新快照并 stop，直至 registry 稳定为空。
- start 在 ready 后、注册前检查 shutdown epoch，关闭期间应 stop 新 channel 并拒绝注册。
- 增加 blocked `channel.start()` → concurrent shutdown → release ready 的确定性测试，断言 shutdown 返回后 `_running` 与 dynamic registry 都为空。

---

## 5. Standards 轴

### 5.1 书面规范违规：新增 file-lock helper 缺少完整类型标注

`backend/CONTRIBUTING.md` 要求函数签名使用类型标注。本轮新增的以下函数不完整：

- `aio_sandbox_provider.py:81`：`_open_and_lock_file()` 缺返回类型；
- `aio_sandbox_provider.py:91`：`_unlock_and_close_file()` 缺 `lock_file` 参数类型；
- `aio_sandbox_provider.py:98`：`_acquire_file_lock_async()` 缺返回类型。

建议使用一致的 `TextIO`/`IO[str]` 类型补齐参数和返回值，并保留现有 cancellation-safe ownership 语义。

其余书面要求未发现新增违规：README/CLAUDE 已同步；新增测试存在；Harness 没有反向 import `app.*`；公共 API docstring 未发现缺失；Ruff、格式与编译检查通过。

### 5.2 判断性设计 smell（不改变 Spec 严重级别）

1. **Divergent Change / Feature Envy：** `feishu.py` 让 `FeishuChannel` 同时负责 WebSocket、下载、durable outbox、cleanup 状态机、全局调度与 health；全局 janitor 还要实例化完整 channel 才能复用 cleanup。建议拆出 `PublishedAttachmentCleanupStore` 和独立 coordinator。
2. **Repeated Switches / Duplicated Code：** cleanup 的 phase/token/lease 转换分散在多条“加锁 → 读取 → 校验 → replace/version+1 → 写回”路径，建议由领域状态对象集中提供原子 transition。
3. **Duplicated Code / Data Clumps：** AIO 三套 acquire 流程重复 owner/lock/bind/accept，同一 sandbox 生命周期仍由多张平行 dict 表示。建议统一内部 lease handle，并用一个聚合记录承载 operation/owner/version/activity。

---

## 6. 第七轮问题关闭状态

| 第七轮问题 | 本轮状态 | 说明 |
|---|---|---|
| P1：DELETE quiesce 与 row/secret delete 的 TOCTOU | **部分关闭** | 同进程生命周期竞态已关闭；DB/secret 删除失败原子性仍未关闭 |
| P1：旧取消 create destroy 后继已接管 sandbox | **部分关闭** | 同 provider generation fencing 已实现；跨 Gateway 的 adoption 不可见 |
| P1：取消 OS file-lock waiter 提前关闭句柄 | **已关闭** | eventual unlock/close 由 worker 接管，定向回归通过 |
| P2：超过 120 秒后丢失 compensation ownership | **部分关闭** | warning deadline 不再取消 create；shutdown/event-loop close 仍无 durable owner |
| P2：25 jobs / 10 秒未覆盖发现与公平调度 | **部分关闭** | 选择/执行有上限和 cursor；discovery 仍固定从目录头部开始 |
| P2：health stale snapshot | **部分关闭** | 本进程 generation CAS 已实现；全局 generation 污染 per-binding health，跨进程提交窗口仍在 |
| P2：per-binding lock registry 无界增长 | **已关闭** | ref-counted entry 在 retire 且 users=0 后安全回收 |

本轮新增确认的 credential rotation rollback、DELETE failure UOW 和 Supervisor shutdown/start 问题，不是上述“已关闭”项的重复描述。

---

## 7. 验证记录

### 7.1 第八轮直接回归

执行 Supervisor、owner channel API、AIO provider、Feishu parser/cleanup、WebSocket lifecycle 与 Gateway service：

```text
121 passed, 5 warnings in 23.84s
```

这些测试证明现有 happy path 和新增的同进程故障注入通过，但没有覆盖本报告的 rotation start failure、DELETE storage failure、双 provider successor adoption、shutdown barrier、discovery cursor 与跨 binding generation。

### 7.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 测试文件：

```text
324 passed, 8 skipped, 5 failed, 6 warnings in 58.41s
```

5 项失败与第五至第七轮基线一致，均来自本轮未修改的 Windows LocalSandbox 路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项为 Windows host path 反向映射/roundtrip，1 项要求本机不存在的 `/bin/sh`。本轮不把它们判定为第八轮回归，也不声明 M3 Gate 全绿。

### 7.3 静态、格式、编译与差异检查

```text
ruff check --no-cache <12 changed Python files>: All checks passed!
ruff format --check --no-cache <12 changed Python files>: 12 files already formatted
python -m compileall <6 changed source files>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

Ruff 当前规则没有覆盖第 5.1 节的完整签名类型要求，因此静态检查通过不代表该书面规范已满足。

### 7.4 专项行为验证

- 慢目录扫描连续两轮都只返回稳定前缀并 `timed_out=True`，确认选择 cursor 无法推进 discovery 尾部。
- binding A health 刷新期间写入 binding B job，会令 A 本轮错误返回 unhealthy；下一次无并发写入时才恢复。
- rotation 与 DELETE failure findings 由固定控制流确认：`_start_row()` 吞掉普通启动异常；row delete 与 secret delete 分属两个无 durable handoff 的阶段。

### 7.5 未完成 Gate

- 本轮未重新执行全量 backend `pytest tests -q`；第七轮修复报告的全量运行在 300 秒时仍无最终汇总。
- 真实 PostgreSQL、双 Feishu App、远程 AIO/provisioner、多 Gateway replica、真实进程 kill 与 Linux CI Gate 仍需部署环境验证。
- 当前 M3 聚焦集仍有 5 项 Windows LocalSandbox 基线失败，不能声明完整 Review Gate 全绿。

---

## 8. 修复优先级

1. 先修复凭据轮换的严格 ready/rollback 语义，确保失败不返回 200、不删除旧 secret。
2. 把 DELETE 建模为 durable、可重试的 row/runtime/secret 状态机，关闭 DB 与 secret store 两个失败窗口。
3. 将 AIO create/adoption generation 持久化或下沉到 backend conditional delete，补双 provider fencing 测试。
4. 为 AIO 与 Supervisor 增加 shutdown admission gate、operation drain 和 durable compensation handoff。
5. 给 cleanup discovery 增加持久扫描 cursor，并改为 per-binding durable generation/原子 health projection。
6. 补齐 file-lock helper 类型标注与本报告所有 barrier/故障注入回归，再重跑聚焦、全量 backend 和部署环境 Gate。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭第 3 节的 3 个 P1：失败轮换不能擦除旧凭据并返回成功；DELETE 不能在 DB/secret 删除失败后留下不可恢复的半状态；旧 Gateway 的 late create 不能按裸 sandbox ID destroy 另一 Gateway 已接受的后继容量。第 4 节的 shutdown、发现公平性和跨 binding/cross-process health 一致性也应形成确定性回归；同时完整 backend 与真实多副本、Feishu、AIO crash-recovery Gate 仍需补齐。
