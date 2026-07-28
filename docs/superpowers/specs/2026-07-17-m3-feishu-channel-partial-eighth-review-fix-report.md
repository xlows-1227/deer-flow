# 多租户 Agent 发布平台 — M3 第八轮 Review 修复报告

**日期：** 2026-07-17
**关联 Review：** [2026-07-17-m3-feishu-channel-partial-code-eighth-review.md](./2026-07-17-m3-feishu-channel-partial-code-eighth-review.md)
**状态：** 第八轮 Review 列出的 3 项 Spec P1、4 项 Spec P2 与 1 项 Standards 类型标注问题已完成代码侧修复和本地自动化核验。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：凭据轮换启动失败未进入回滚 | 已修复 | rotation 使用严格 ready 入口；新 runtime 未 ready 时抛出 `BindingStartError`，恢复旧 `app_id/secret_ref` 并重新启动旧 runtime。Owner API 清理新 secret、保留旧 secret并返回 502 | API 故障注入覆盖新 secret 入库、新 channel start 失败、旧 row/runtime/secret 恢复与新 secret 擦除 |
| P1：DELETE 的 row/runtime/secret 失败原子性 | 已修复 | 新增数据库 `deleting` tombstone 与 `delete_previous_status`；删除顺序为 tombstone → stop → secret erase → physical row delete。start/restart/rotate/activate/deactivate/update credentials 对 tombstone fail closed；startup 自动重试 tombstone | 覆盖 tombstone 写入异常、repository delete exception/`None`、secret delete exception、重启恢复、backlog 409 与并发 lifecycle |
| P1：AIO late-create fencing 仅单进程有效 | 已修复 | deterministic sandbox 的 file lock 内写入 durable lifecycle generation、operation token 与状态；另一 provider discover/adopt 会推进 generation；旧补偿仅 compare-and-delete 自己仍拥有的 generation | 两个独立 provider 共享 backend/filesystem，强制 A cancel → B discover/accept → A completion；旧 provider 未调用 destroy |
| P2：AIO shutdown 后 create 可重新注册 | 已修复 | shutdown 先关闭 create admission；注册动作在同一 provider lock 内再次检查 shutdown。已返回 backend capacity 在 shutdown 后完成时会被 destroy 而非注册；async cleanup 被取消时，补偿所有权原子移交 backend worker/daemon fallback | create barrier → shutdown → create return 后得到明确失败；另覆盖 cleanup 在等待 create 与执行 async compensation 两个阶段被取消，capacity 均最终 destroy |
| P2：cleanup discovery 尾部可永久饥饿 | 已修复 | 新增与执行选择 cursor 分离的 persisted discovery cursor，稳定排序并在解析候选前推进；单文件读取由两个 bounded daemon slots 隔离并受剩余 pass deadline 限制，挂起读取不会叠加无界 worker | 20 个 job、慢读取和短 deadline 连续多 pass，尾部 `slow-job-19` 在有限轮数内被发现；原 25/4 全局上限测试保持通过 |
| P2：全局 generation 污染 per-binding health | 已修复 | durable generation 改为 per-binding file；job write/transition/delete 只 bump 所属 binding。healthy projection 在 binding generation lock 内读快照并执行 local CAS；健康属性还会读取同 binding 的 durable backlog | binding B 在 binding A 扫描期间写 job，A 仍 healthy；第二个 Gateway 为同 binding 写 job后，第一个 Gateway 立即观察为 unhealthy |
| P2：Supervisor shutdown/start 可留下晚注册 runtime | 已修复 | shutdown 设置 admission gate，快照并等待所有已有 lifecycle lock entry，再停止 registry；channel ready 后、注册前同时检查 shutdown 和数据库 tombstone | blocked `channel.start()` → concurrent shutdown → ready 放行，start 被拒绝并 stop，shutdown 返回后 `_running` 与 dynamic runtime 均为空 |
| Standards：file-lock helper 缺完整类型标注 | 已修复 | `_open_and_lock_file()`、`_unlock_and_close_file()`、`_acquire_file_lock_async()` 及相邻 lock helper 统一使用 `TextIO` 与泛型 Task/Future 标注 | Ruff、format、compileall 通过；书面签名要求已满足 |

---

## 2. 最终运行时不变量

### 2.1 Credential rotation 与 durable deletion

- active credential rotation 只有在新 WebSocket runtime 完成 ready handshake 后才提交成功并擦除旧 secret。
- 新凭据启动失败时，数据库恢复旧 `app_id/secret_ref`，旧 runtime 在同一 binding lifecycle lock 内重新启动；Owner API 只擦除被拒绝的新 secret。
- DELETE 在停止 runtime 前先把最后一个 durable `secret_ref` 写入 `status=deleting` 的 row tombstone；secret store 或数据库失败都不会丢失重试依据。
- physical row delete 只接受 `deleting` row。secret 已删除但 row delete 失败时，重试依赖 secret delete 的幂等语义继续收敛。
- tombstone 存在时，start/restart/rotation 以及 repository activate/deactivate/update-credentials 均 fail closed；ready 后注册 runtime 前还会重新查询数据库，关闭跨 Gateway 的 stale-start 窗口。
- Gateway startup 先恢复所有 tombstone，再加载 active rows。attachment backlog 仍存在时保留 tombstone和恢复责任。

### 2.2 AIO cross-process create ownership

- 每个 thread-scoped backend create 持有 operation token 与 durable generation；状态文件与 deterministic sandbox file lock 位于相同 owner/thread 目录。
- 第二个 Gateway 在 file lock 内 discover/adopt 时推进 durable generation，旧 Gateway 的 late compensation 因 compare 失败而保留后继容量。
- 没有 successor 时，取消后的 capacity 仍由原 operation destroy；正常完成、失败或补偿完成后只清理自己拥有的 lifecycle state。
- shutdown 关闭新 create admission；正在执行的 create 在注册前再次检查 epoch，shutdown 后到达的 capacity 被 destroy。
- event-loop shutdown 取消 late-cleanup task 时，operation 的 mutable handoff record 由 cleanup 与 backend worker 原子 claim；backend 未返回时由原 worker补偿，已经返回时启动 daemon fallback，不丢失 destroy ownership。
- OS file-lock worker 始终拥有 handle；调用方取消只转交 eventual unlock/close，不并发关闭 live handle。

### 2.3 Fair discovery 与 tenant-local health

- discovery cursor 与 claimable-job selection cursor 相互独立；cursor 在候选解析前原子推进，因此慢前缀或单个慢文件不会把后续 pass 固定在目录头部。
- 单候选读取受 pass 剩余 deadline 约束，并最多占用两个 daemon reader slots；外层 scan worker 可以按 deadline 返回，后续 pass 继续从下一候选扫描。
- 每轮仍只选择最多 25 个 claimable jobs、最多 4 个 execution 并发，并受同一个 10 秒总预算约束。
- generation 按 binding 隔离；其他 binding 的 producer、heartbeat 或 transition 不会使当前 binding false-unhealthy。
- healthy 只有在 local generation CAS 与 file-locked same-binding durable snapshot 都为空时提交；同 binding 的另一 Gateway 写入后，durable health projection仍会返回 unhealthy。

### 2.4 Supervisor shutdown

- shutdown 开始即关闭 start/restart/rotation/load admission。
- 所有已经进入 ref-counted lifecycle registry 的操作都会被 shutdown 等待；ready 后的 runtime 在注册前检查 shutdown并自行 stop。
- shutdown 最终停止稳定 registry 快照并退休 keyed lock entries，不改变数据库的 active desired status。

---

## 3. 数据库变更

新增 Alembic revision：

```text
2026_07_14_channel_mappings
  → 2026_07_17_channel_deletion_state
```

`agent_channels` 新增可空字段：

```text
delete_previous_status VARCHAR(16)
```

它只在 `status=deleting` 时保存删除前的 desired status。数据库 row 在 secret 成功擦除和 physical delete 完成前始终保留最后一个 durable `secret_ref`。

---

## 4. 自动化验证

### 4.1 第八轮直接回归

执行 repository、owner channel API、Supervisor、AIO provider、Feishu parser/cleanup、WebSocket lifecycle、Gateway services 与完整迁移链：

```text
146 passed, 1 skipped, 5 warnings in 30.50s
```

### 4.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件：

```text
339 passed, 8 skipped, 5 failed, 6 warnings in 56.17s
```

5 项失败与第五至第八轮 Review 基线一致，均来自本轮未修改的 Windows LocalSandbox 路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项为 Windows host path 反向映射/roundtrip，1 项要求本机不存在的 `/bin/sh`。第一次聚焦运行还暴露了新增 ready-row re-fetch 与 SQLite in-memory 单连接并发 health projection 的时序；Supervisor 随后对 ready re-fetch/health persistence 增加 repository projection serialization，定向回归和最终聚焦运行均已通过。

### 4.3 静态、格式、编译与差异检查

```text
ruff check --no-cache <13 changed Python files>: All checks passed!
ruff format --check --no-cache <13 changed Python files>: 13 files already formatted
python -m compileall <7 changed source/migration targets>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

### 4.4 全量 backend Gate

本轮不重复执行此前连续超过 300 秒且无最终汇总的全量 `pytest tests -q`。第七轮报告已记录该环境限制；本报告不宣称全量 backend 通过。

---

## 5. 尚未关闭的非代码/环境 Gate

1. 修复或在 Linux CI 规避 5 项 Windows LocalSandbox 既有基线后重跑完整 M3 Gate。
2. 在可完成的 CI runner 上取得全量 backend `pytest tests -q` 最终汇总。
3. 真实 PostgreSQL 下验证 tombstone、并发 DELETE/start/rotation 与 per-binding health projection。
4. 两个真实 Feishu App 验证失败轮换 rollback、parallel WebSocket 与 backlog/tombstone 运维。
5. 真实远程 AIO/provisioner 验证跨 Gateway durable generation、shutdown 与进程 kill 后 reconciliation。
6. 至少两个 Gateway replicas 验证 discovery cursor、bounded reader、per-binding generation 与 stale-start fencing。

第八轮 Standards 轴列出的三组 design smell 是判断性架构建议，不是书面规范违规。本轮没有在行为修复中进行高风险的 `FeishuChannel` store/coordinator 拆分或 AIO lifecycle 聚合重构；它们仍适合作为后续独立架构任务。

---

## 6. 最终判定

**第八轮 Review 的 3 项 Spec P1、4 项 Spec P2 与类型标注问题：代码侧已关闭。**

直接回归和迁移链通过。由于 M3 聚焦集仍有既有 Windows LocalSandbox 基线、全量 backend 无最终汇总，且真实 PostgreSQL/Feishu/AIO/多副本 crash-recovery Gate 尚未执行，本报告不宣称最终 Ready to merge。
