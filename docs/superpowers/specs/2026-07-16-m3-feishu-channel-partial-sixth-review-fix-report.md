# 多租户 Agent 发布平台 — M3 第六轮 Review 修复报告

**日期：** 2026-07-16  
**关联 Review：** [2026-07-16-m3-feishu-channel-partial-code-sixth-review.md](./2026-07-16-m3-feishu-channel-partial-code-sixth-review.md)  
**状态：** 第六轮 Review 列出的 4 项 Spec P1 与 3 项 Spec P2 已完成代码侧修复和本地自动化核验。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| Supervisor 全局锁内同步恢复 cleanup backlog | 已修复 | Supervisor lifecycle lock 改为 per-binding lock；`FeishuChannel.start()` 只做本地 backlog 投影，WebSocket ready 后才启动 recovery coordinator；active bindings 并发启动；单次 recovery 限制为最多 25 jobs / 10 秒，并给 acquire/delete 独立 deadline | A binding 的 `start()` 永久阻塞时，B 的 start/restart/stop 均在 200 ms 内完成；阻塞 recovery 不延迟 WebSocket ready；stalled remote delete 在应用 deadline 内返回并保留 job |
| recovery 可在 sync producer 结束前提前完成 outbox | 已修复 | cleanup job 持久化为 `producer_pending → ready_to_delete → deleting`；producer token 持续 heartbeat 续租，worker 真正退出后才在文件锁内推进 phase；delete 使用 claim token、lease、版本与 fencing；进程崩溃后仅接管过期 lease | 旧 sync worker 阻塞且可能二次写入时，新 channel recovery 返回 0，job/remote 均保留；worker 退出后才删除 remote/host/outbox；Gateway janitor 可接管已过期 producer lease |
| binding 删除后 job 永久失去恢复者 | 已修复 | Gateway 启动独立全局 janitor，按共享 outbox 中的 binding IDs 恢复，不依赖 active binding row、Feishu secret 或 WebSocket；DELETE 在 backlog 存在时返回 409，并保留 row/secret；停止期间新出现 backlog 时恢复 active runtime | inactive binding 的 expired-producer job 在 `load_active_bindings()` 后由 janitor 清除；pending outbox 下 DELETE=409，row 与当前 encrypted secret 均保留，job 清除后重试 DELETE 成功 |
| late acquisition 按裸 sandbox ID release 可能破坏同 thread 复用 | 已修复 | `SandboxProvider` 新增 `SandboxAcquisition`、`acquire_with_lease_async()`、`accept_acquisition()`、`abandon_acquisition()`；Feishu 成功路径 accept、超时路径 abandon；AIO 记录 active-before 与 accepted-use generation，仅条件释放本次引入且未被后继接受的容量 | abandon 一个复用 handle 后，第一请求的 `_thread_sandboxes` 与 active sandbox 保持不变；Feishu late completion 只调用 abandon，测试显式禁止裸 `release()` |
| mounted/local 删除失败仍可能投影 healthy | 已修复 | recovery 以有效 outbox/invalid/pending/claim 结果决定最终 health；mounted host unlink 失败显式 unhealthy，只有 job 全部完成后恢复 healthy | mounted unlink 首次失败时 outbox 保留且 `attachment_cleanup_healthy=False`；成功重试移除 outbox 后才恢复 true |
| late acquisition cleanup 可永久等待 | 已修复 | foreground 15 秒 deadline 之外新增 30 秒最终补偿 deadline和 0.5 秒 cancel drain；永不返回 task 被取消并释放引用；AIO backend create 使用 shielded operation，取消后晚返回的 `SandboxInfo` 由 provider 后台 destroy | never-returning acquisition 在最终 deadline 被取消，cleanup task 集合归零且 health unhealthy；取消中的 backend create 晚返回后调用 destroy，未注册 active sandbox |
| health 持久化异常会终止周期 recovery | 已修复 | recovery execution 与 health projection 分开捕获；非取消异常只记录，下一轮继续 cleanup 并重试 dirty health projection | `update_health` 等价 callback 首次抛错后，第二轮 recovery 与 health projection 均继续并成功 |

---

## 2. 最终运行时不变量

### 2.1 Binding 生命周期隔离

- Supervisor 只在同一 `binding_id` 内串行 start/stop/restart/runtime health；不存在跨外部网络、WebSocket、sandbox 或 cleanup I/O 持有的进程级 lifecycle lock。
- `load_active_bindings()` 并发启动各 active binding；单个 binding 启动失败或阻塞不延迟 peers。
- `FeishuChannel.start()` 在 ready handshake 前只读取本地 outbox 并投影 unhealthy；实际 recovery 是 ready 后的 per-binding background task。
- recovery pass 最多读取 25 个 jobs，总预算 10 秒；未处理或未完成 job 留给下一轮。

### 2.2 Durable producer 与 delete fencing

- 不确定 sync cleanup 在 worker 仍可能写入时以 `producer_pending` 落盘，包含不可猜测 producer token 与 lease deadline。
- 同进程 active producer token 阻止本进程其他 channel 接管；heartbeat 每 5 秒把 30 秒 producer lease 原子续期。
- producer 真正退出后，持 token 的 coordinator 在 file lock 内推进为 `ready_to_delete`；进程重启只能接管已过期 producer lease。
- delete 前必须取得带 15 秒 lease 的 `deleting` claim；另一个进程只能在 claim 过期后 fencing 接管。正常失败会释放 claim 回 `ready_to_delete`，进程崩溃则由 lease 恢复。
- remote delete 单次调用 deadline 为 2 秒，最多重试 3 次；remote 与 host 均确认删除后，且 claim token 仍匹配，才能 unlink JSON outbox。

### 2.3 Binding-independent recovery 与删除

- Supervisor 启动一个 30 秒 Gateway janitor，直接扫描 `${DEER_FLOW_HOME:-.deer-flow}/published-attachment-cleanup/`。
- job 已持久化受信任 owner、thread 与受限 `/mnt/user-data/uploads/*` virtual paths；janitor 不读取 Feishu credential，不要求 active binding 或 WebSocket。
- pending/invalid outbox 下物理 DELETE fail closed：HTTP 409，数据库 row 与 encrypted secret 保留。
- cleanup execution 和 health projection 解耦；repository 瞬时错误不会停止后续 recovery。

### 2.4 Sandbox acquisition ownership

- timeout-sensitive caller 不再从裸 sandbox ID 推断所有权；provider 返回 typed `SandboxAcquisition` handle。
- foreground 在 admission deadline 内成功后显式 accept；late completion 只交给 provider abandon。
- AIO 对同 thread acquisition 在 thread lock 内记录 existing-active 与 accepted-use generation。abandon 只有在本 operation 引入 active mapping、generation 未被后继接受且 mapping 仍匹配时才 release。
- AIO async backend create 被取消后仍保留 completion task；若 capacity 晚创建，则由 provider destroy，不进入 active cache。
- 永不返回 acquisition 在最终 compensation deadline 后取消；不会无限持有 Channel/provider/request 引用。

---

## 3. 自动化验证

### 3.1 第六轮直接回归

执行 owner SQL lifecycle、Feishu parser/admission、Supervisor、owner channel API、AIO provider 与 WebSocket lifecycle：

```text
110 passed, 5 warnings in 17.16s
```

覆盖：

- blocked binding start 与 recovery 的跨 binding 隔离；
- producer lease/heartbeat、restart fencing、delete claim 与 mounted health；
- inactive binding 的 Gateway janitor recovery 与 pending DELETE 409 retention；
- managed acquisition accept/abandon、same-thread reuse 与 late backend create destroy；
- never-returning acquisition 的最终 cancellation；
- health projection 首次失败后的下一轮恢复。

### 3.2 M3 聚焦集

执行 README/CLAUDE 所列 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件：

```text
313 passed, 8 skipped, 5 failed, 6 warnings in 42.08s
```

5 项失败与第五/第六轮 Review 基线一致，均来自本轮未修改的 Windows LocalSandbox 路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项是 Windows host path 反向映射/roundtrip 语义，1 项要求本机不存在的 `/bin/sh`。本轮未修改 `local_sandbox.py`，因此不把这些失败判定为第六轮回归，也不声明对应测试通过。

### 3.3 静态、格式、编译与差异检查

```text
ruff check --no-cache <12 changed Python files>: All checks passed!
ruff format --check --no-cache <12 changed Python files>: 12 files already formatted
python -m compileall <6 changed source files>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

本轮没有新增或修改数据库 migration。cleanup phase/lease/claim 状态仍使用应用状态目录内的 per-job JSON outbox与 file lock。

### 3.4 全量 backend Gate

本轮重新执行全量 `pytest tests -q`，在 300 秒工具 deadline 时推进到 58%，没有生成最终测试汇总；超时前已出现多项非本轮聚焦路径失败。本报告不声明全量 backend 通过。

---

## 4. 仍需部署环境完成的 Gate

1. 真实 PostgreSQL 下验证 owner SQL lifecycle、health projection retry 与 DELETE 409/重试流程。
2. 两个真实 Feishu App 的并行长连接、per-binding start/stop/restart、凭据轮换与 backlog health smoke。
3. 真实远程 AIO/provisioner 验证冷启动取消、late backend create destroy、managed handle generation 与 2 秒 delete deadline。
4. 至少两个 Gateway replicas 共享数据库与 `${DEER_FLOW_HOME}`，验证 producer heartbeat、file-lock claim fencing、claim crash takeover 和 binding-independent janitor。
5. 真实进程 kill（producer_pending、deleting、backend create 三个窗口）后重启，确认 remote、host、outbox 最终收敛。
6. Linux CI 或修复 Windows LocalSandbox 基线后重跑完整 M3/全 backend 套件。

---

## 5. 最终判定

**第六轮 Review 的 4 项 P1 与 3 项 P2：代码侧已关闭。**

第六轮直接回归全部通过；M3 聚焦集除 5 项既有 Windows LocalSandbox 基线外通过。当前可以进入第七轮代码复审，但在全量 backend、Linux CI 和第 4 节真实多副本/Feishu/AIO crash-recovery Gate 完成前，本报告不宣称最终 Ready to merge。
