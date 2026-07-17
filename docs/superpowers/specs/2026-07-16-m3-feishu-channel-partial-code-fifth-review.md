# 多租户 Agent 发布平台 - M3 第五轮代码复审

**状态：** 已复审，待修复  
**日期：** 2026-07-16

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第四轮代码复审：[2026-07-15-m3-feishu-channel-partial-code-fourth-review.md](./2026-07-15-m3-feishu-channel-partial-code-fourth-review.md)
- 第四轮修复报告：[2026-07-15-m3-feishu-channel-partial-fourth-review-fix-report.md](./2026-07-15-m3-feishu-channel-partial-fourth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第四轮复审头：`529b4d6f9d693c8ae558c99f61207ad3823e0175`
- 第五轮复审头：`5211479182c83c09a837f05e4d83764f2823af30`
- 本轮修复提交：`52114791 fix(m3): address Feishu fourth review findings`
- 固定差异：`git diff 529b4d6f9d693c8ae558c99f61207ad3823e0175...5211479182c83c09a837f05e4d83764f2823af30`
- 差异规模：21 个文件，1598 行新增、211 行删除
- 工作区已有的 `config.yaml`、`frontend/src/components/workspace/workspace-header.tsx` 及既有未跟踪文件不属于本轮提交，未纳入结论。
- 复审采用 Spec 与 Standards 两条独立轴；以下严重级别保留各轴原始判断，不在汇总时重新排序。

---

## 1. 复审结论

第四轮报告中的 started-state、Run cleanup deadline、HTTP 真流式下载、显式 sandbox owner 绑定、README/CLAUDE 和公共 API docstring 已取得实质修复，相关定向回归也通过。

但当前版本仍未达到可合并标准。本轮确认 **3 个 Spec P1、1 个 Spec P2**：Published Feishu Run 的 owner scope 直到 worker 内才建立，真实 SQL Run 持久化会在 worker 创建前失败；延迟附件清理会被 binding `stop/restart` 取消，远端删除失败也没有恢复机制；异步 dispatcher 同步调用 AIO sandbox acquisition，会阻塞 Gateway 主事件循环；正常 sandbox 同步还缺少应用级 deadline。

Standards 轴未发现书面标准硬违规，但记录了 2 个判断性设计 smell。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Run 一旦启动便不再错误释放 reservation：** `run_starter` 返回后的异常与取消统一进入 detached recovery，progress drain 取消不再降级为 unstarted。
- **timeout cleanup 已有界：** Run cancel、worker join 与 progress cleanup 均有独立短 deadline，不合作 worker 不再无限阻塞 dispatcher。
- **Published 附件改为真实 HTTP 流式读取：** 使用 async `httpx`、`Content-Length` 预检查和逐 transport chunk 累计，SDK 整包缓冲问题已关闭。
- **sandbox owner 契约已显式化：** `SandboxProvider.acquire(..., user_id=...)`、Local/AIO owner binding 与 AIO deterministic ID 已落地，并有 owner 冲突回归。
- **下载取消与部分文件清理已改善：** async response/file handle 能在取消时关闭，已登记的 host partial 可被删除。
- **文档与 docstring 已同步：** README/CLAUDE 已描述当前附件、owner、timeout 与 progress 契约；新增公共 API 具备类型和 Google 风格契约说明。

---

## 3. Spec 轴：P1 问题

### 3.1 [P1] owner scope 建立晚于 Run/Thread 持久化，真实 Published Feishu Run 无法启动

**相关位置：**

- `backend/app/gateway/services.py:341-361`
- `backend/app/gateway/services.py:367-387`
- `backend/app/gateway/services.py:418-435`
- `backend/packages/harness/deerflow/persistence/run/sql.py:80-103`
- `backend/packages/harness/deerflow/runtime/user_context.py:206-229`
- `backend/tests/conftest.py:158-219`

**问题说明：**

`start_run()` 只在 `_run_with_effective_config()` worker 内进入 `runtime_user_scope(owner_user_id)` 与 `effective_app_config_scope(owner_user_id)`。在此之前，它已经执行：

1. 使用全局 `get_app_config()` 校验 model；
2. `RunManager.create_or_reject()` 持久化 pending Run；
3. `thread_store.get/create/update_status()` 读写 ThreadMeta。

生产 SQL `RunRepository.put()` 的 `user_id` 默认值是 `AUTO`，会调用 `resolve_user_id()`；Feishu MessageBus 后台任务没有浏览器认证中间件设置的 current-user ContextVar，因此会直接抛出 `RuntimeError`。这个错误发生在 worker 创建之前且不在 `ConflictError` / `UnsupportedStrategyError` 分支内，导致真实 Published Feishu Run 无法持久化和启动。ThreadMeta 的同类错误虽然被当作 non-fatal 吞掉，仍会丢失 owner-scoped 会话元数据。

当前测试的 autouse user fixture 默认建立了用户上下文，新增测试没有使用 `@pytest.mark.no_auto_user` 配合真实 SQL store 覆盖 Feishu 后台路径，因此误把 worker 内 ContextVar 测试当成了完整生命周期验证。此外，owner 自定义且已由 Resolver 认可的 model 也可能在 owner effective config 生效前被全局 allowlist 再次误拒。

这违反设计 §6.4、§10、§13.1 与开发计划 F3.4 的受信任 owner 执行上下文要求。

**影响：**

- 使用 SQL repositories 的 Published Feishu 消息可能全部在 Run 创建前失败；
- 若未来存在 ambient/default user，Run/Thread 可能错误归属到非 owner scope；
- owner-scoped model 可能被全局配置错误拒绝；
- 配额已经预留但 Run 未创建，错误恢复只能依赖上层缺失-Run 路径。

**建议修复：**

1. 在任何 model 校验、Run/Thread 持久化和 worker 创建之前解析可信 owner，并让 `runtime_user_scope` 与 `effective_app_config_scope` 覆盖整个 Published `start_run()` 生命周期；或向 RunManager/ThreadMeta 显式传递 owner，不能用 `user_id=None` 绕过隔离。
2. owner effective config 生效后再校验 model，保持 Resolver 与运行时策略一致。
3. 增加 `@pytest.mark.no_auto_user` + 真实 SQL Run/Thread repositories 回归，断言 Run 与 ThreadMeta 的 `user_id` 都等于 `PublishedAgentContext.owner_user_id`。
4. 增加 owner 自定义 model 的 Published Feishu 启动测试，并断言上下文在成功、失败和取消后恢复。

---

### 3.2 [P1] deferred sandbox cleanup 会被 stop/restart 取消，删除失败也不可恢复

**相关位置：**

- `backend/app/channels/feishu.py:670-676`
- `backend/app/channels/feishu.py:1011-1041`
- `backend/app/channels/feishu.py:1074-1095`
- `backend/tests/test_feishu_parser.py:316-389`

**问题说明：**

当 `sandbox.update_file_from_path()` 的 `to_thread()` worker 在 dispatcher 取消后仍未退出，代码会登记 `_finish_cancelled_sandbox_sync()` 到 `_background_tasks`，等待 worker 结束后再删除 remote sandbox 与 host 文件。

但是 `FeishuChannel.stop()` 会无差别 `cancel()` 所有 `_background_tasks` 并立即 `clear()`，既不等待也不区分关键 cleanup 与可丢弃后台任务。cleanup task 在 `await asyncio.gather(sync_task, ...)` 处被取消时，底层线程仍可能继续写入，后续 sandbox/host 删除则不会执行。binding 的 stop、restart、删除或 Gateway shutdown 都能触发这个窗口。

即使 cleanup task 正常运行，`_delete_published_sandbox_files()` 仍把所有远端删除失败降级为 warning；没有重试、持久化 cleanup 记录或 unhealthy 状态，task 随后仍删除 host 文件并成功结束。瞬时远端错误因此可永久留下被取消或未通过准入的附件。

现有回归只在释放阻塞 worker 后等待 cleanup 完成，没有在等待期间调用 `channel.stop()` / Supervisor restart，也没有模拟 `delete_file()` 瞬时失败。

这违反第四轮 §3.5 所要求的“无法确认清理时登记可恢复任务”，也破坏开发计划 F3.2 的 binding 生命周期隔离与附件失败清理保证。

**影响：**

- stop/restart 后，已取消或未准入附件可能继续写入并永久留在 owner sandbox；
- 远端删除的瞬时失败没有恢复点，残留不会在后续启动被扫描；
- shutdown 清空 task 引用后无法再观测或告警未完成清理。

**建议修复：**

1. 将关键 attachment cleanup 与 card/progress 等可取消后台任务分开管理；`stop()` 对 cleanup 做有界 drain，不应直接取消后丢引用。
2. 超过 shutdown deadline 的 cleanup 写入可恢复的持久化任务/outbox，并在 startup/周期任务重试。
3. 让 remote/host 删除返回结构化结果；remote 删除未确认时不能把 cleanup 标记成功，采用有界退避和最终告警/健康态。
4. 增加“阻塞 sync → 取消 dispatcher → stop/restart → 释放 worker”的回归，最终断言 host 与 remote 都为空；再增加 remote delete 首次失败、重试成功，以及进程重启恢复用例。

---

### 3.3 [P1] async Feishu dispatcher 同步 acquire AIO sandbox，可阻塞所有 binding

**相关位置：**

- `backend/app/channels/feishu.py:1054-1056`
- `backend/packages/harness/deerflow/sandbox/sandbox_provider.py:29-39`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:580-633`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:830-862`

**问题说明：**

`_sync_published_files()` 是 async dispatcher 路径，却直接调用同步 `sandbox_provider.acquire(thread_id, user_id=owner_user_id)`。AIO provider 的同步 acquire 会执行 thread/file lock、backend discovery、容器创建和同步 readiness polling，新 sandbox 探活最长可到约 60 秒；这些操作全部发生在 Gateway 主事件循环上。

本轮已经为抽象 provider 与 AIO provider 实现了 owner-aware `acquire_async()`，但 Feishu 路径没有使用它。单个 binding 的冷启动或故障 provisioner 因此能冻结同一事件循环中的其他 binding、API 和 lifecycle 操作，违反设计 §6.3 与开发计划 F3.2 的 per-binding failure isolation。

**影响：**

- 一个附件消息可让全部 Feishu binding 数十秒无响应；
- Supervisor stop/restart、健康检查和其他 Gateway 请求会一起延迟；
- AIO backend 故障被放大为进程级可用性问题。

**建议修复：**

1. 在 async 路径使用 `await sandbox_provider.acquire_async(thread_id, user_id=owner_user_id)`。
2. 对 acquisition 使用受平台硬上限约束的 deadline；超时映射为安全外部错误并释放未启动 reservation。
3. 增加 stalled AIO acquisition 并发回归：一个 binding 阻塞时，另一个 binding 的消息和 stop/restart 仍能及时完成。

---

## 4. Spec 轴：P2 问题

### 4.1 [P2] 正常 sandbox sync 没有应用级 deadline，可在 Run 创建前长期占用 reservation

**相关位置：**

- `backend/app/channels/feishu.py:1062-1073`
- `backend/app/channels/feishu.py:1074-1095`

**问题说明：**

正常路径通过 `await asyncio.shield(sync_task)` 等待 `sandbox.update_file_from_path()`，没有应用级 timeout。底层 AIO client 的长 timeout 不能替代 Published admission deadline；sync 发生在 Run 创建之前、quota reserve 之后，因此 Release 的 `max_run_seconds` 尚未开始约束它。远端卡住时可长时间占用 reservation 与 dispatcher。

取消路径虽然有 2 秒 cleanup join deadline，但只有上层先取消时才生效，无法限制正常 admission 自身。

**建议修复：**

1. 为单文件和整批 sandbox sync 设置平台硬上限与剩余 admission budget。
2. timeout 后进入与取消相同的 deferred cleanup/recovery 状态机，并安全释放未启动 reservation。
3. 增加永久阻塞 sync 回归，断言 deadline 内返回、未创建 Run、reservation 被正确释放且最终无 host/remote 残留。

---

## 5. Standards 轴

### 5.1 书面标准检查

未发现书面标准硬违规：

- README/CLAUDE 已同步，满足 `backend/CLAUDE.md` 的文档更新要求；
- 未引入 Harness → App 反向依赖；
- 新增公共 API 具备类型、docstring 与测试。

### 5.2 判断性设计 smell（不改变 Spec 严重级别）

1. **Duplicated Code / Shotgun Surgery：** owner 解析、校验及 `(thread_id, user_id)` 传播重复分散在 SandboxProvider、Local/AIO provider 多层。owner 契约变化需要同时修改多个入口和内部方法，容易再次出现“部分路径 owner-aware、部分路径仍依赖 ambient context”的状态。
2. **Data Clumps：** sandbox、owner、lock 由多个平行字典维护。Local LRU eviction 只删除 sandbox mapping，AIO release/destroy 也没有统一处理 owner binding，多个容器的生命周期容易漂移。建议把 thread sandbox binding 聚合成单一记录，并用一个原子生命周期入口完成 acquire/reuse/release/evict。

---

## 6. 第四轮 finding 关闭状态

| 第四轮 finding | 第五轮状态 | 说明 |
|---|---|---|
| 3.1 progress drain 取消错误释放 reservation | **已关闭** | started-state 契约统一，post-start 异常转 detached recovery |
| 3.2 timeout cleanup 可无限等待 | **已关闭** | cancel、worker join 与 progress cleanup 均已有界 deadline |
| 3.3 SDK 整包缓冲，不是真流式下载 | **已关闭** | async HTTP 原始 chunk 流式读取和双层大小检查已落地 |
| 3.4 sandbox / worker owner 未贯穿 | **部分关闭** | provider owner binding 已修复；Run/Thread 持久化和 model 校验仍早于 owner scope，见 3.1 |
| 3.5 取消 `to_thread()` 后可能留 host/sandbox 文件 | **部分关闭** | 下载 partial 与常规 cleanup 已改善；deferred cleanup 会被 stop 取消，删除失败不可恢复，见 3.2 |
| 4.1 README/CLAUDE 未同步 | **已关闭** | 最终准入顺序、边界、owner 与 cleanup 语义已补充 |
| 4.2 公共附件 API docstring 不完整 | **已关闭** | Args/Returns/Raises 与 owner/取消契约已补齐 |

本轮 3.3 与 4.1 是在第四轮修复引入/重构的 sandbox admission 路径上新确认的问题，不属于对已关闭 finding 的重复计数。

---

## 7. 验证记录

### 7.1 聚焦回归

对 M3、legacy channel、附件、sandbox、user context 与 harness boundary 的较大范围组合执行结果：

```text
319 passed, 8 skipped, 7 failed, 6 warnings in 51.80s
```

7 项失败均位于本轮未修改的 Windows/platform 基线路径：

- 2 项在 setup 创建 symlink 时因当前账户缺少权限失败：`WinError 1314`；
- 5 项是 LocalSandbox 既有的 Windows path reverse-resolution、`/bin/sh` 与 write/read 路径语义失败；本轮 LocalSandbox 差异只新增 `delete_file()`，未改动这些路径。

本报告没有把这些环境/基线失败判定为本轮代码回归，也不据此声称对应安全用例已通过。

随后对本轮新增/变更核心行为单独运行 5 个测试文件及 Local explicit-owner 用例，并排除上述 2 个 symlink setup 用例：

```text
91 passed, 1 warning in 7.46s
```

现有测试全绿仍不能覆盖第 3 节问题：test autouse user context 掩盖了无认证 Feishu dispatcher，cleanup 测试未调用 `stop/restart`，也没有 stalled sync/acquire 并发用例。

### 7.2 静态、格式与差异检查

```text
ruff check --no-cache <17 changed Python files>: All checks passed!
ruff format --check --no-cache <17 changed Python files>: 17 files already formatted
git diff --check 529b4d6f...52114791: passed
```

本轮没有新增或修改数据库 migration。

### 7.3 尚未完成的 Gate

- 未重新执行全量 backend `make test`；历史 Windows 基线失败仍存在。
- 未在具备 symlink 权限的 Windows 或 Linux CI 补跑两项上传目标安全用例。
- 未执行真实 PostgreSQL、双 Feishu App 和远程 AIO sandbox smoke。
- 未验证进程 stop/restart 后的持久化附件 cleanup recovery。

---

## 8. 建议修复顺序

1. 先把 trusted owner scope 提升到 Published `start_run()` 最外层，并补无 autouse user 的真实 SQL 回归。
2. 将 attachment cleanup 从可取消 background task 中分离，增加 stop 有界 drain、持久恢复和远端删除重试。
3. 把 Feishu sandbox acquisition 改为 `await acquire_async()`，补跨 binding 非阻塞回归。
4. 为正常 sandbox sync 设置 admission deadline，并统一复用 cancel/timeout cleanup 状态机。
5. 收敛 provider 的 owner/sandbox/lock 平行状态，避免生命周期漂移。
6. 重跑聚焦测试、全 backend、Linux/symlink、真实 PostgreSQL、双 Feishu App 与远程 AIO sandbox Gate。

---

## 9. 最终判定

**Ready to merge：No。**

第四轮的 Run started-state、cleanup deadline、网络流式下载与文档问题已经关闭，但 owner scope 的建立时机仍阻断真实 SQL Published Feishu Run，关键附件清理在 stop/restart 与远端删除失败时仍不可靠，同步 AIO acquire 还会破坏 binding 隔离。完成第 3、4 节修复并补齐对应回归后，建议进行第六轮复审。
