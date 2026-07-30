# 多租户 Agent 发布平台 — M3 第五轮 Review 修复报告

**日期：** 2026-07-16  
**关联 Review：** [2026-07-16-m3-feishu-channel-partial-code-fifth-review.md](./2026-07-16-m3-feishu-channel-partial-code-fifth-review.md)  
**状态：** 第五轮 Review 列出的 3 项 Spec P1 与 1 项 Spec P2 已完成代码侧修复和本地自动化核验。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| Published owner scope 晚于 Run/Thread 持久化 | 已修复 | `start_run()` 在 model allowlist 校验、`RunManager.create_or_reject()`、ThreadMeta upsert 和 worker 创建之前进入可信 `runtime_user_scope(owner)` 与 owner effective-config scope；worker 继承同一 ContextVar，调用方在返回、失败或取消后恢复 | `@no_auto_user` + 真实 SQLite SQL Run/Thread repositories 断言两行 `user_id=owner-a`；仅存在于 owner effective config 的自定义 model 可启动；worker 取消后调用方 ContextVar 为空 |
| deferred cleanup 被 stop/restart 取消，远端删除不可恢复 | 已修复 | cleanup task 与 card/progress task 分组；stop 只对 cleanup 做 2 秒有界 drain，不取消或丢引用；不确定 cleanup 先原子写入 per-job JSON outbox，远端删除最多重试 3 次；startup 与 30 秒周期任务恢复；Supervisor 保持连接同时持久化 redacted cleanup health | 阻塞 sync → 取消 → stop → 释放 worker 后 host/remote 均为空；首次 delete 失败、第二次成功；永久失败留下 outbox，新 binding 实例恢复后 outbox/remote 清空；cleanup health 可在不停止 binding 的情况下切换 unhealthy/healthy |
| async dispatcher 同步 acquire AIO sandbox | 已修复 | Feishu admission 使用 `acquire_async()`；兼容 provider 的 fallback 也通过 `to_thread()`；acquire 有 15 秒 deadline；超时/取消后继续跟踪晚完成 acquisition 并释放 sandbox capacity | 一个 binding 的 acquire 阻塞时，另一个 binding 的附件 admission 与 stop 在 200 ms 内完成；acquire deadline 返回后，晚完成 sandbox 被 release |
| 正常 sandbox sync 没有应用级 deadline | 已修复 | 非挂载 sandbox sync 增加 60 秒单文件与 120 秒整批 deadline；timeout 与 cancellation 复用同一 durable cleanup 状态机 | 永久阻塞 sync 在 deadline 内抛出安全 timeout，未等待 Run 创建；释放 worker 后 host/remote 均为空；既有 Published runtime 回归确认 materialization 失败只执行 `release_unstarted()`，不调用 executor/settlement |

---

## 2. 最终运行时不变量

### 2.1 Published owner 生命周期

- `PublishedAgentContext.owner_user_id` 在 `start_run()` 的最外层建立受信任 scope，不依赖 Feishu dispatcher 的 ambient browser user。
- owner effective config 在 model allowlist 校验前生效，因此 Resolver 已认可的 owner custom model 不会被 global-only allowlist 二次误拒。
- pending Run 与 ThreadMeta 的 SQL 写入、worker `create_task()`、middleware、uploads、outputs 和最终 attachment resolution 使用同一 owner。
- child worker 持有复制后的 owner/effective-config ContextVars；调用 `start_run()` 的父 task 在成功返回、异常和取消路径均恢复原上下文。

### 2.2 异步附件准入 deadline

- sandbox provider acquisition 只走 async lifecycle；平台 admission deadline 为 15 秒。
- acquisition 在 deadline 后仍可能由底层不可取消线程/后端完成，因此 late-acquisition cleanup 会等待结果并调用 provider `release()`，避免长期占用 active capacity。
- 非挂载 sandbox 的单文件同步最多 60 秒，整批同步最多 120 秒；两者都发生在 Run 创建前，并受应用级边界约束。
- acquire/sync timeout 作为附件准入失败返回，Published runtime 只释放按精确 `run_id` 预绑定但尚未启动的 reservation，不创建 Run 或 usage settlement。

### 2.3 Durable attachment cleanup

- card/progress/recovery loop 属于可取消 background tasks；可能决定租户附件残留的 cleanup tasks 使用独立集合。
- binding stop 对 cleanup 最多 drain 2 秒；超过 deadline 的 task 保留强引用继续运行，不被 stop/restart 主动取消。
- 每个不确定 cleanup 在 `${DEER_FLOW_HOME:-.deer-flow}/published-attachment-cleanup/` 使用独立 JSON 文件和 atomic replace 落盘，只记录 binding、owner、thread 与受限 `/mnt/user-data/uploads/*` virtual paths，不记录凭据或任意 host path。
- remote delete 最多尝试 3 次并有界退避；remote 未确认或 host delete 失败时 outbox 不完成，记录 error/critical log 并上报 unhealthy。
- binding startup 和运行期 30 秒 recovery pass 重新获取 owner-scoped sandbox 并幂等删除；只有 remote 与 host 都确认删除后才移除 outbox。

---

## 3. 自动化验证

### 3.1 第五轮直接回归

执行 owner SQL lifecycle、Feishu parser/admission、Supervisor health、owner channel API 与 Published Run flow：

```text
97 passed, 1 warning in 8.76s
```

覆盖：

- `@pytest.mark.no_auto_user` + 真实 SQL Run/Thread owner 持久化；
- owner custom model 与 worker cancellation ContextVar 恢复；
- stop/restart cleanup drain、remote delete retry、outbox restart recovery；
- stalled acquire 的跨 binding 非阻塞、acquire deadline 与 late release；
- sync deadline 与最终 host/remote cleanup；
- running binding 的 cleanup unhealthy/healthy 持久化更新。

### 3.2 M3 聚焦集

执行 README/CLAUDE 所列 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件：

```text
303 passed, 8 skipped, 5 failed, 6 warnings in 48.25s
```

5 项失败均来自本轮未修改的 Windows LocalSandbox 基线路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项是 Windows host path 反向映射/roundtrip 语义，1 项要求本机不存在的 `/bin/sh`。第五轮变更没有修改 `local_sandbox.py`；这些失败与第五轮 Review 的基线记录一致。本报告不把它们声明为通过。

### 3.3 静态、格式与编译检查

```text
ruff check --no-cache <7 changed Python files>: All checks passed!
ruff format --check --no-cache <7 changed Python files>: passed
python -m compileall app/channels/feishu.py app/channels/supervisor.py app/gateway/services.py: passed
git diff --check: passed
```

本轮没有新增或修改数据库 migration。附件 cleanup recovery 使用应用状态目录内的 per-job outbox。

### 3.4 全量 backend Gate

`pytest tests -q` 在本地运行 300 秒后到达工具 deadline，只推进到约 24%，未生成最终测试汇总，因此本报告不声明全量 backend 通过。超时前已经出现多项非本轮聚焦路径失败；需要在 CI 或更长运行窗口重新执行并取得完整报告。

---

## 4. 仍需部署环境完成的 Gate

以下内容不能由本地 fake/SQLite 回归替代：

1. 真实 PostgreSQL 下验证无 HTTP user context 的 Published Feishu Run/Thread owner 落库与 custom-model 启动。
2. 两个真实 Feishu App 的并行长连接、stop/restart、凭据轮换、卡片进度与大文件 smoke。
3. 真实远程 AIO/provisioner 的冷启动 15 秒边界、late acquisition release、60/120 秒 sync deadline 与进程重启 outbox recovery。
4. 多 Gateway replica 共享 `${DEER_FLOW_HOME}` 时的 cleanup job 幂等竞争与 redacted health 更新。
5. 在 Linux CI 或修复 Windows LocalSandbox 基线后重跑完整 M3/全 backend 套件。

---

## 5. 最终判定

**第五轮 Review 的 3 项 P1 与 1 项 P2：代码侧已关闭。**

第五轮直接回归全部通过，M3 聚焦集除 5 项已知 Windows LocalSandbox 基线外通过。本报告不把全量 backend 超时或第 4 节的真实 Feishu/PostgreSQL/远程 sandbox Gate 声明为通过；完成这些部署验证后再做最终 merge 判定。
