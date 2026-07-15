# 多租户 Agent 发布平台 - M3 第四轮代码复审

**状态：** 已复审，待修复
**日期：** 2026-07-15

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第三轮代码复审：[2026-07-15-m3-feishu-channel-partial-code-third-review.md](./2026-07-15-m3-feishu-channel-partial-code-third-review.md)
- 第三轮修复报告：[2026-07-15-m3-feishu-channel-partial-third-review-fix-report.md](./2026-07-15-m3-feishu-channel-partial-third-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第三轮复审头：`5eb9a84751dc04010316ad4f12d6811210b012e2`
- 第四轮复审头：`529b4d6f9d693c8ae558c99f61207ad3823e0175`
- 本轮修复提交：`529b4d6f fix(m3): address Feishu third review findings`
- 固定差异：`git diff 5eb9a84751dc04010316ad4f12d6811210b012e2...529b4d6f9d693c8ae558c99f61207ad3823e0175`
- 工作区已有的 `config.yaml`、`frontend/src/components/workspace/workspace-header.tsx` 修改不属于本轮提交，未纳入结论。
- 复审继续采用 Spec 与 Standards 两条独立轴；以下严重级别保留各轴原始判断。

---

## 1. 复审结论

本轮已经真实关闭第三轮中的若干问题：共享 SDK loop 上的 endpoint 获取改成了异步且有界的 HTTP 请求；Resolver、配额预留与附件下载的主顺序已调整；附件数量、单文件与聚合输入边界开始执行；慢 progress callback 不再阻塞最终 stream state 与 artifact 收集；入站主机路径和出站 artifact 解析开始显式携带 owner；重复的 event claim Protocol 也已收敛。

但当前版本仍未达到可合并标准。本轮复审发现 **5 个 Spec P1** 与 **2 个 Standards P2**：Run 启动后的 progress-drain 取消仍可能错误释放 reservation；timeout cleanup 可以无界等待；所谓流式附件下载在 SDK 内仍先整包进内存；显式 owner 主机路径之后仍以 ambient user 获取并缓存沙箱；下载线程被取消时仍可能留下未准入文件。此外，README/CLAUDE 未同步，新增公共 API 的 docstring 契约也不完整。

**Ready to merge：No。**

---

## 2. 已确认关闭或前进的第三轮问题

- **共享 loop 的同步 endpoint 阻塞已关闭**：`_resolve_lark_endpoint()` 使用 `httpx.AsyncClient`，连接、读取和整体 connect 均有超时；一条 binding 的 endpoint 卡住不再冻结同一 loop 上的其他 binding。
- **附件下载的主准入顺序已前移**：当前为 mapping → Resolver → 轻量输入检查 → quota reserve → materialize → Run；未发布 Agent 与 quota 拒绝不会启动下载。
- **常规边界已补齐**：最多 10 个附件、单文件 50 MiB、基于实际落盘字节的聚合输入检查，并在普通异常路径删除已登记的主机文件。
- **慢 progress 与最终 stream 已解耦**：有界 latest-value queue 和独立 progress task 使慢卡片更新不再阻塞 `values` / artifact drain。
- **主机文件路径显式 owner 化**：入站 uploads 与出站 outputs 的路径解析不再直接依赖 `get_effective_user_id()`。
- **Protocol 重复与旧 docstring 缺口已关闭**：`EventDeduplicator` 已收敛到 `app/channels/contracts.py`，第三轮指出的 Protocol 方法已补充契约。

这些改进不等价于完整关闭第三轮 Gate；第 3、4 节仍有阻断问题。

---

## 3. Spec 轴：仍需修复的 P1 问题

### 3.1 [P1] Run 已完成后取消 progress drain，仍会按“Run 未启动”释放 reservation

**相关位置：**

- `backend/app/channels/published_runtime.py:380-389`
- `backend/app/channels/published_runtime.py:638-652`
- `backend/packages/harness/deerflow/persistence/agent_usage/sql.py:447-472`

**问题说明：**

`GatewayPublishedRunExecutor.execute()` 已等待 Run worker 和主 stream consumer 结束后，还会等待 `progress_queue.join()`。这里仅捕获 `TimeoutError`；如果 dispatcher 此时收到取消，`CancelledError` 会直接离开 executor。

外层 `PublishedChannelRuntime.run()` 只把 `PublishedRunDetachedError` 识别为“Run 已启动、reservation 必须留给恢复任务”，其余 `BaseException` 都调用 `release_unstarted()`。数据库实现只校验 reservation 仍为 pending 且绑定同一 `run_id`，并不会再次证明 Run 不存在。因此下列时序会错误释放一个已经执行甚至已经终态的 Run：

```text
Run 持久化并执行完成
  → stream/artifact 已 drain
  → progress callback 仍很慢
  → dispatcher 在 progress_queue.join() 处取消
  → CancelledError 逃逸
  → release_unstarted(reservation, run_id)
```

这会破坏 M2 §7.3 和设计 §10/§12.3 的 exactly-once 用量与 reservation 语义。

**建议修复：**

1. `start_run()` 成功返回后，executor 的所有取消/清理失败都必须转换为 `PublishedRunDetachedError`，或通过显式 started-state 契约告知外层绝不能释放 reservation。
2. progress drain 取消只能丢弃中间进度，不能把已启动 Run 降级成 unstarted。
3. 增加“Run success + slow progress + 在 queue drain 取消 dispatcher”的回归，断言 `release_unstarted()` 零调用，reservation 最终结算或保留给恢复任务。

---

### 3.2 [P1] timeout cleanup 无界等待取消后的 worker，`max_run_seconds` 不是硬上限

**相关位置：**

- `backend/app/channels/published_runtime.py:454-464`
- `backend/app/channels/published_runtime.py:589-609`

**问题说明：**

Run 超时后 `_cancel_started_run()` 先等待 `RunManager.cancel()`，随后直接 `await task`，两个 await 都没有 cleanup deadline。如果 worker 抑制 `CancelledError`、卡在 `finally`、token flush、持久化或外部 I/O 中，Feishu dispatcher 会在 `max_run_seconds` 之后继续永久挂起，最终响应、结算与 binding 可用性都无法恢复。

本轮新增的“cancel 抛异常时保留 reservation”测试只覆盖立即失败，没有覆盖 cancel 成功但 worker 永不 join 的情况。这违反设计 §12.1 的最大执行时长和 §14 的超时响应契约。

**建议修复：**

1. 为 `RunManager.cancel()` 与 worker join 设置独立、短且有界的 cleanup timeout。
2. cleanup 超时后抛 `PublishedRunDetachedError`，保留 Run-bound reservation 给 durable recovery；不能无限等待，也不能 release-unstarted。
3. 增加 worker 捕获取消并永久等待的测试，断言 executor 在 cleanup deadline 内退出且 reservation 未释放。

---

### 3.3 [P1] 64 KiB 落盘循环发生在 SDK 整包缓冲之后，附件内存硬限制仍未实现

**相关位置：**

- `backend/app/channels/feishu.py:877-918`
- `backend/uv.lock:2121-2132`（锁定 `lark-oapi 1.5.5`）
- 已安装 SDK：`lark_oapi/api/im/v1/resource/message_resource.py::MessageResource.get()`

**问题说明：**

新代码调用 `message_resource.get()`，等它返回后才从 `response.file` 每次读取 64 KiB。锁定的 `lark-oapi 1.5.5` 在 `get()` 内部先访问 `resp.content`，再执行 `io.BytesIO(resp.content)`；也就是说完整 HTTP 响应已经进入内存，之后的 64 KiB 读取只是在遍历一个内存缓冲区。

因此超大或恶意资源可在应用检查 50 MiB 上限前占用完整响应内存，设计 §12.1 的“最大输入体积与附件大小”仍不能作为硬性滥用防护。

**建议修复：**

1. 不要通过会读取 `resp.content` 的生成 SDK 下载方法；使用带鉴权的真正 streaming HTTP 路径或扩展 transport 暴露原始流。
2. 有可信 `Content-Length` 时先拒绝超限响应，同时仍对每个网络 chunk 累计校验，超过上限立即关闭响应并删除 partial file。
3. 测试应让 fake transport 分块产出且禁止读取完整 body，断言边界值成功、超限时网络流被中止、内存中未构造完整 payload。

---

### 3.4 [P1] owner 文件落盘后仍按 ambient user 获取沙箱，并按 thread_id 缓存错误映射

**相关位置：**

- `backend/app/channels/feishu.py:886-928`
- `backend/packages/harness/deerflow/sandbox/local/local_sandbox_provider.py:175-263`
- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:305-319,458-525`
- `backend/app/gateway/services.py:390-426`

**问题说明：**

主机 uploads 已正确写入 `owner_user_id` 目录，但紧接着仍调用 `sandbox_provider.acquire(thread_id)`。Local 与 AIO provider 都在内部通过 `get_effective_user_id()` 选择挂载路径，而 Feishu WebSocket / MessageBus dispatcher 没有 owner session ContextVar，此处通常得到 `default`。

两个 provider 又只按 `thread_id` 缓存沙箱。第一次 materialize 可把该 thread 的 sandbox 永久绑定到 `default` 挂载；后续 Published Run 即使在 config 中携带 owner，也会复用这个错误缓存。结果可能是 owner 文件被复制到 default sandbox、Run 看见错误 bucket、输出 artifact 又无法从 owner 路径解析，仍违反设计 §6.4/§13.1 的 tenant isolation。

现有 owner 测试把 provider mock 成恒定返回 `"local"`，没有覆盖真实 provider 的挂载与缓存语义。

**建议修复：**

1. Published 路径不要让沙箱 acquisition 依赖 ambient fallback；显式传递可信 `owner_user_id`，或在严格设置/恢复 owner context 的受控作用域中获取沙箱。
2. 若 provider 支持同一 thread 标识出现在不同 owner scope，缓存键必须包含 owner；至少要断言已经缓存的映射 owner 与当前请求一致，冲突时 fail closed。
3. Published Run worker 也应在生命周期内建立真实 owner user context，而不只把 user_id 写进 Runnable config。
4. 使用真实 Local/AIO provider 增加双 owner 回归，断言 uploads、sandbox virtual path、outputs 和最终 attachment 都落在相同 owner scope。

---

### 3.5 [P1] 取消 `to_thread()` 不会停止下载线程，未准入附件仍可能在取消后落盘

**相关位置：**

- `backend/app/channels/feishu.py:891-918`
- `backend/app/channels/feishu.py:821-853`
- `backend/app/channels/feishu.py:920-934`

**问题说明：**

`persist_stream()` 通过 `asyncio.to_thread()` 写文件。取消等待该 coroutine 只会取消 asyncio Future，不会停止工作线程。如果 dispatcher 在下载期间被取消：

1. `_materialize_published_file()` 尚未返回；
2. 外层 `created_paths.append()` 尚未执行；
3. 外层 cleanup 列表为空；
4. 后台线程仍可继续写完文件并正常返回，之后无人删除它。

沙箱同步的 `to_thread()` 也存在相同取消窗口。在 Windows 上同步线程持有文件时直接 `unlink()` 还可能失败并遮蔽原始取消。这样被取消、未通过最终 aggregate admission、也未启动 Run 的消息仍能向 thread workspace 留下文件，破坏第三轮要求的失败清理和设计 §12.1 的滥用防护。

**建议修复：**

1. 在启动阻塞工作前登记目标路径，并为 worker 提供 cooperative cancel flag，在每个 chunk 处终止。
2. coroutine 被取消后应有界等待工作线程停止，再执行 host 与非挂载 sandbox 的幂等 cleanup；如果无法确认清理完成，应记录可恢复的清理任务，而不是静默返回。
3. 增加阻塞 stream 回归：下载开始后取消 dispatcher，释放阻塞线程并等待其退出，最终断言 owner uploads 与 sandbox 中均无残留。

---

## 4. Standards 轴：P2 问题

### 4.1 [P2] README/CLAUDE 未同步，现有 M3 运行说明已经与实现不一致

**相关位置：**

- `backend/CLAUDE.md:71-77`
- `backend/CLAUDE.md:512-528`
- `backend/README.md:210-214`
- `docs/superpowers/plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md:866-870`

**问题说明：**

本提交修改了 WebSocket endpoint 获取、附件准入顺序、数量/聚合边界、owner 路径以及 progress drain，却没有更新 `backend/README.md` 或 `backend/CLAUDE.md`。现有说明仍称附件“在 resolver input-size checks 前”下载，只记录每个文件 50 MiB，没有记录 10 文件、实际字节聚合、endpoint timeout 与 progress queue 语义。

这既违反 `backend/CLAUDE.md` 的强制文档策略，也未满足 M3 Review Gate 的 IM Channels 文档同步要求。

**建议修复：**

- 修复代码问题后，同步更新 README 的部署/行为说明与 CLAUDE 的 Published Feishu flow、隔离、附件、超时和测试命令；确保描述的是最终真实顺序与硬限制，而不是本轮尚未成立的“流式下载”。

---

### 4.2 [P2] 新公共附件 API 与新增 user_id 参数缺少完整 docstring 契约

**相关位置：**

- `backend/app/channels/feishu.py:800-808`
- `backend/packages/harness/deerflow/uploads/manager.py:40-52`
- `backend/CONTRIBUTING.md:149-168`

**问题说明：**

`FeishuChannel.materialize_published_files()` 以及新增 `user_id` 参数的 `get_uploads_dir()` / `ensure_uploads_dir()` 只有一句摘要，没有说明：

- `owner_user_id` / `user_id` 必须来自可信 owner scope，何时允许 ambient fallback；
- 返回的 message、attachment byte count 与路径语义；
- 数量、大小、空文件、下载、沙箱同步和 cleanup 失败时抛出的异常；
- 取消时的清理保证。

这不符合 `backend/CONTRIBUTING.md` 对公共函数、类和方法的 docstring 要求。

**建议修复：**

- 补充 Google 风格 `Args` / `Returns` / `Raises`，并让文档契约与修复后的 owner、取消和清理语义一致。

---

## 5. 第三轮 finding 关闭状态

| 第三轮 finding | 第四轮状态 | 说明 |
|---|---|---|
| 3.1 共享 SDK loop 被同步 endpoint 请求阻塞 | **已关闭** | 异步 endpoint resolver 与 connect timeout 已落地，并有 stalled-binding 隔离测试 |
| 3.2 post-start cleanup 失败后错误释放 reservation | **部分关闭** | timeout cancel 立即失败会保留 reservation；progress drain 取消仍会 release-unstarted，cancel/join 也可能无界等待，见 3.1、3.2 |
| 3.3 Resolver/quota 前下载及附件硬限制缺失 | **部分关闭** | 主顺序、数量、单文件和聚合检查已补；SDK 仍整包缓冲，取消下载可留残余，见 3.3、3.5 |
| 3.4 慢 progress 阻塞最终 artifact | **直接问题已关闭** | consumer 与 sender 已解耦；但 Run 结束后的 progress 取消会破坏 reservation，见 3.1 |
| 3.5 Published 文件使用 default owner 路径 | **部分关闭** | host uploads/outputs 显式 owner 化；sandbox acquisition 与缓存仍依赖 ambient user，见 3.4 |
| 4.1 新 Protocol 方法缺 docstring | **原 finding 已关闭，出现新同类 P2** | 旧 Protocol 契约已补齐；新公共附件 API 仍缺完整契约，见 4.2 |
| 4.2 EventDeduplicator 重复定义 | **已关闭** | 已收敛到中立 contracts 模块 |

---

## 6. 验证记录

### 6.1 聚焦回归

执行 15 个 M3、legacy IM、quota、Agent public API 与 harness boundary 相关测试文件：

```text
232 passed, 2 failed, 6 warnings in 62.66s
```

两个失败均发生在用例 setup 阶段：当前 Windows 账号缺少创建符号链接权限（`WinError 1314`），对应：

```text
test_rejects_preexisting_symlink_destination
test_rejects_dangling_symlink_destination
```

本轮没有把这两个环境权限错误判定为代码回归，也没有据此声明相关安全用例通过。

### 6.2 静态、格式与迁移图

```text
ruff check --no-cache <11 changed Python files>: All checks passed!
ruff format --check --no-cache <11 changed Python files>: 11 files already formatted
git diff --check 5eb9a847...529b4d6f: passed
alembic heads: 2026_07_14_channel_mappings (head)
```

### 6.3 定向代码证据

- 锁定的 `lark-oapi 1.5.5` 在 binary resource `get()` 中使用 `io.BytesIO(resp.content)`，证实 64 KiB 循环不是网络流式读取。
- `release_unstarted_reservation()` 只按 pending + owner + reservation + run_id 更新，不查询 Run 是否已存在，证实 progress-drain 取消路径可以错误释放。
- Local/AIO provider 都从 `get_effective_user_id()` 构造 thread mount，并只按 `thread_id` 缓存，证实显式 host owner 尚未贯穿 sandbox scope。
- `asyncio.to_thread()` 取消不停止工作线程，且 `created_paths` 只在单文件 coroutine 返回后登记，证实取消窗口会绕过外层 cleanup。

### 6.4 尚未完成的 Gate

- 未重新执行全 backend `make test`；历史基线仍存在与本提交无关的失败。
- 未执行真实 PostgreSQL migration / 多进程 quota 并发门禁。
- 未使用两个真实 Feishu App 验证连接隔离、凭据轮换、流式卡片与双向附件。
- 未在具备 Windows symlink 权限或 Linux CI 的环境补跑两个符号链接安全用例。

---

## 7. 建议修复顺序

1. 先建立“Run 一旦启动，任何异常都不能 release-unstarted”的单一状态契约，并修复 progress-drain 取消路径。
2. 为 cancel 与 worker join 增加有界 cleanup deadline；超时转入 detached recovery。
3. 用真正的网络流式下载替换 SDK 的整包缓冲，并补 cooperative cancellation、host/sandbox 完整清理。
4. 将可信 owner 显式贯穿 sandbox acquisition、缓存和 Published Run worker context，补真实 provider 双 owner 测试。
5. 更新 README/CLAUDE 与公共 API docstring。
6. 重跑聚焦测试、全 backend、PostgreSQL Gate、双 Feishu App smoke 与 symlink 安全用例。

---

## 8. 最终判定

**Ready to merge：No。**

本轮修复使 endpoint 隔离、准入顺序、常规附件边界、慢 progress artifact 与显式主机 owner 路径显著前进，但 post-start reservation、执行时长硬上限、真正流式下载、取消清理和 sandbox owner scope 仍存在 P1 阻断。关闭第 3 节问题并补齐第 4 节文档后，建议进行第五轮复审。
