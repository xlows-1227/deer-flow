# 多租户 Agent 发布平台 - M3 第三轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-15

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M2 运行时规范：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第一轮代码复审：[2026-07-15-m3-feishu-channel-partial-code-review.md](./2026-07-15-m3-feishu-channel-partial-code-review.md)
- 第二轮代码复审：[2026-07-15-m3-feishu-channel-partial-code-rereview.md](./2026-07-15-m3-feishu-channel-partial-code-rereview.md)
- 第二轮修复报告：[2026-07-15-m3-feishu-channel-partial-rereview-fix-report.md](./2026-07-15-m3-feishu-channel-partial-rereview-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 第二轮复审头：`720d9af94e4c79685391ea3618e338ed9e487126`
- 第三轮复审头：`5eb9a84751dc04010316ad4f12d6811210b012e2`
- 本轮修复提交：`5eb9a847 fix(m3): address Feishu rereview findings`
- 固定差异：`git diff 720d9af94e4c79685391ea3618e338ed9e487126...HEAD`
- 工作区中未提交的配置、前端文件、图片与既有临时目录不属于本轮提交，未纳入结论。
- 复审方式：Spec 轴与 Standards 轴分别检查，不合并或跨轴重排严重级别。

---

## 1. 复审结论

本轮修复已关闭第二轮 finding 的主要正常路径：event claim 已要求 system scope 并校验持久化 binding；公共 Published runtime Protocol 已补 docstring；两个 SDK session 不再互相覆盖模块级 loop；dispatcher 常规取消会 cancel/join Run 并结算；动态路径也已经具备基本的 stream、入站文件与最终 artifact 处理。

但是，第三轮复审仍发现：

- **Spec 轴：5 个 P1**
  - 共享 SDK loop 内执行同步且无 timeout 的连接请求，一个 binding 可阻塞全部 binding。
  - timeout cleanup 失败仍会把已启动 Run 当成 unstarted 释放。
  - 附件在 Resolver、入站限流与 quota reserve 前下载，且实际字节未计入有效输入限额。
  - stream consumer 同步等待 Feishu progress 网络投递，慢投递会丢失最终 values/artifacts。
  - 动态文件读写使用 ambient `default` 用户目录，而 Published Run 使用 Agent owner 目录。
- **Standards 轴：1 个 P2、1 个 P3 判断性 smell**
  - 新增的 repository/event Protocol 方法仍缺方法级 docstring。
  - event dedup Protocol 在两个 App 模块重复定义，形成 Duplicated Code / Shotgun Surgery 风险。

5 个 Spec P1 都位于已启用的动态 binding 路径，并有静态证据或定向复现，不能由现有 202 个聚焦测试通过抵消。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **第二轮 M3-RS1 的“每 binding 覆盖全局 loop”已关闭**：`_LarkSdkRuntime` 只设置一次 `lark_oapi.ws.client.loop`，两个 session 的连接与任务位于同一个进程级 loop。
- **第二轮 M3-RS2 的常规 dispatcher 取消已关闭**：已启动 Run 收到外层取消后会调用 RunManager cancel、等待 worker 并按 cancelled 终态结算；普通 `release()` 也已限制为 `run_id IS NULL`。
- **第二轮 M3-RS3 的基础能力已落地**：Gateway executor 会订阅 StreamBridge，Manager 会发布非终态消息、解析最后一轮 artifact，并调用动态 Feishu Channel 处理入站文件。
- **第二轮 M3-RT1 已关闭**：`ChannelEventRepository.claim()` 要求不可伪造的 `SYSTEM_CHANNEL_MAPPING_SCOPE`，并在插入 dedup 前验证持久化 Feishu binding。
- **第二轮 M3-RT2 已关闭**：`MappingStoreLike`、`ResolverLike`、`QuotaLedgerLike`、`PublishedRunExecutor` 及其关键方法已补充契约说明。
- **文档与测试同步有实质改善**：README、CLAUDE 和 M3 聚焦测试已覆盖本轮宣称的主要正常路径。

---

## 3. Spec 轴：仍需修复的问题

### 3.1 [P1] 共享 SDK loop 内的同步连接请求会破坏 binding 故障隔离

**相关位置：**

- `backend/app/channels/feishu.py:248-287`
- `backend/tests/test_feishu_websocket_lifecycle.py`
- `backend/uv.lock:2121-2123`

**问题说明：**

`_LarkSdkRuntime` 把全部 Feishu Client 调度到同一个专用 event loop；`_LarkWebSocketSession._run_client()` 随后在该 loop 中直接执行：

```python
await client._connect()
```

当前 lockfile 使用 `lark-oapi 1.5.5`。该版本的 `_connect()` 会同步调用 `_get_conn_url()`，而 `_get_conn_url()` 内部使用没有 timeout 的 `requests.post(...)`。所以任一 binding 在启动、重启或网络异常时卡在 endpoint 请求，都会阻塞共享 loop；其他已连接 binding 的 receive、message handler 与 ping 也无法继续执行。

新增双 session 测试把 `_SdkClient._connect()` 实现成纯异步、立即返回的 fake，因此没有覆盖这个真实 SDK 行为。

**违反规范：**

- F3.2 要求单个 binding 的启动、停止、重启和失败互不影响。
- 设计 §6.3 与验收 #14 要求一个不健康 binding 不得中断其他已发布 Agent。

**建议修复：**

1. 不要在共享 loop 上执行 SDK 内部的同步 endpoint 请求。
2. 将 endpoint 获取放到有严格 connect/read timeout 的线程或异步 HTTP 调用中，再在共享 loop 上建立 WebSocket；如果无法安全封装该私有 SDK 流程，应使用进程级隔离，而不是共享一个可被同步调用冻结的 loop。
3. 为每个 binding 增加有界的连接 deadline，并确保超时只标记该 binding unhealthy。
4. 增加真实适配器测试：让第二个 Client 的 endpoint 请求阻塞，断言第一个 Client 仍可收消息和执行 ping。

---

### 3.2 [P1] timeout cleanup 失败会把已启动 Run 误释放为 unstarted

**相关位置：**

- `backend/app/channels/published_runtime.py:342-351`
- `backend/app/channels/published_runtime.py:519-548`
- `backend/packages/harness/deerflow/persistence/agent_usage/sql.py:447-474`

**问题说明：**

Run timeout 后，executor 调用 `run_manager.cancel(record.run_id)`。如果取消或状态持久化抛出普通异常，该异常会离开 executor；外层 `PublishedChannelRuntime.run()` 只对 `PublishedRunDetachedError` 保留 pending，对其他所有 `BaseException` 都调用 `release_unstarted()`。

repository 的 `release_unstarted_reservation()` 只验证 reservation、owner 与精确 `run_id` 相等，并不能证明 Run 从未启动。因此一个已经持久化、已经执行甚至已经消耗 token 的 Run 仍会被标记为 `unstarted/released`，随后也不会被 pending settlement recovery 扫描。

本轮定向复现结果：

```text
error= persistence unavailable
run_started= True
release_unstarted_calls= ['reservation-1']
```

**违反规范：**

- F3.4 与 M2 运行时规范要求 success/cancelled/timeout/failed 恰好结算一次。
- 设计 §12.3 要求预留在成功、取消、超时和失败时最终结算或在确认 Run 未启动时释放。

**建议修复：**

1. `run_starter` 成功返回后，executor 的任何清理失败都必须转换为 `PublishedRunDetachedError`，保留 Run-bound pending reservation。
2. 不要把“调用方知道精确 run_id”等同于“Run 不存在”。`release_unstarted` 应只由已经通过 RunStore/RunManager 确认不存在的恢复或启动补偿路径调用，必要时增加专用 system scope。
3. 增加 timeout cancel 抛异常、worker 仍存活的测试，断言 reservation 保持 pending，后续 recovery 只写一次 terminal usage。

---

### 3.3 [P1] 附件在授权与限流前下载，且可以绕过有效输入限额

**相关位置：**

- `backend/app/channels/published_runtime.py:289-324`
- `backend/app/channels/manager.py:920-930`
- `backend/app/channels/feishu.py:26-36`

**问题说明：**

当前顺序是：DB mapping → `prepare_inbound()` 下载并落盘文件 → Resolver → 只对 `message.text` 执行 `max_input_bytes` 检查 → quota reserve。

这意味着：

- 未发布、暂停或归档的 Agent 也会先下载附件；
- quota/RPS/并发已超限的请求仍会先消耗 Feishu API、内存和磁盘；
- 单文件允许读取 50 MiB，文件数量与总字节数没有聚合上限；
- `max_input_bytes` 只计算插入虚拟路径后的文本字节，不计算已下载附件的真实字节，因此 owner/platform 输入限额可以被附件绕过。

**违反规范：**

- F3.3 锁定了 `verify → dedup → binding → mapping → Resolver → reserve → Run` 的可信顺序。
- 设计 §12.1 要求最大输入体积、附件大小、入站速率与过载保护由平台硬限制覆盖。
- 被拒绝请求不得在进入 Run 前无界消耗后端资源。

**建议修复：**

1. mapping 后先执行 Resolver 与轻量入站 admission，再处理附件。
2. 使用 provider metadata 或有界流式读取，同时限制单文件、文件数量和总字节，并以生效 quota 为上限；不要一次把 50 MiB 全部读入内存。
3. 把附件真实总字节纳入 Published 输入限额；部分下载失败时清理已落盘文件。
4. 增加未发布 Agent、quota 已满、多附件总量超限和单文件接近上限的回归测试，断言拒绝发生时没有下载和文件残留。

---

### 3.4 [P1] 慢速 progress 投递会阻塞 stream drain 并丢失最终附件

**相关位置：**

- `backend/app/channels/published_runtime.py:479-509`
- `backend/app/channels/published_runtime.py:550-566`
- `backend/app/channels/message_bus.py:142-157`
- `backend/app/channels/feishu.py:615-646`

**问题说明：**

stream consumer 在读到文本增量后同步 `await on_progress(latest_text)`。该 callback 会同步经过 MessageBus listener，最终等待 Feishu card patch；Feishu 发送还可能进行 1 秒、2 秒退避重试。

在 callback 等待期间，consumer 不再读取后续 `values`。Run 完成后 executor 只等待 stream task 1 秒，超时便取消它，所以排在慢 progress 后面的最终 values 不会赋给 `last_values`，最终 `artifacts` 变为空。

本轮使用现有 MemoryStreamBridge 做定向复现，progress callback 延迟 2 秒，结果为：

```text
progress_started= True
artifacts= ()
Published Feishu stream did not terminate after Run completion
```

**违反规范：**

- F3.4 与设计 §10 要求在支持时推送流式更新，同时可靠发布最终响应或附件。
- 设计 §18.5 要求流式/最终响应与附件处理具备回归覆盖。

**建议修复：**

1. 把 stream ingestion 与外部 progress I/O 解耦：consumer 必须持续 drain values/messages，卡片更新通过有界 latest-value queue 或独立发送任务完成。
2. progress 失败或变慢只能丢弃中间快照，不能阻止最终 state/artifact 收集。
3. 最终 artifact 应来自可靠终态 state/checkpoint，不能依赖“1 秒内刚好消费完 stream”。
4. 增加 progress 延迟、Feishu patch 重试和 callback 永久失败的测试，断言最终文本与附件仍完整投递。

---

### 3.5 [P1] 动态文件 I/O 使用 `default` 用户目录，与 Published owner 运行目录不一致

**相关位置：**

- `backend/app/channels/feishu.py:783-787`
- `backend/app/channels/manager.py:378-400`
- `backend/app/channels/manager.py:455-533`
- `backend/app/gateway/services.py:390-418`

**问题说明：**

Feishu WebSocket/MessageBus dispatcher 没有浏览器 owner ContextVar，所以 `get_effective_user_id()` 返回 `default`。入站 `receive_file()`、`_ingest_inbound_files()` 以及最终 `_resolve_attachments()` 都依赖这个 ambient user；而真正的 Published Run 在 Gateway 中明确使用 `published_context.owner_user_id`。

路径诊断确认同一 thread 会解析到两个不同 bucket：

```text
ambient_user= default
ambient_uploads= .../users/default/threads/thread-review/user-data/uploads
owner_uploads=   .../users/owner-a/threads/thread-review/user-data/uploads
ambient_outputs= .../users/default/threads/thread-review/user-data/outputs
owner_outputs=   .../users/owner-a/threads/thread-review/user-data/outputs
```

结果是：入站文件写进 default bucket 后，owner-scoped Run 看不到它；Run 在 owner bucket 生成的 artifact，Manager 又会去 default bucket 查找并跳过上传。现有测试用 mock path 忽略 `user_id`，因此没有发现这个错误。

**违反规范：**

- 设计 §6.4/§13.1 要求 Published Run 在 owner principal 下访问授权资源，并保持 external actor 与 owner 分离。
- F3.4 与设计 §18.5 要求入站和出站附件在真实运行链路可用。
- 仓库 CLAUDE 的线程目录约束要求所有 thread 文件位于正确的 per-user isolation scope。

**建议修复：**

1. Resolver 得到可信 owner 后，把 `owner_user_id` 显式传给入站落盘、artifact 解析和 attachment delivery；不要让 Published 路径依赖 ambient fallback。
2. 将相关 helper 改成显式 `(thread_id, owner_user_id, ...)` 契约，或在受控范围内设置并恢复可信 user context。
3. 增加真实 `get_paths()` 测试：两个 owner 各执行动态 Feishu 入站/出站附件，断言物理路径、Run 可见性和上传解析均严格 owner-scoped。

---

## 4. Standards 轴：仍需修复的问题

### 4.1 [P2] 新增公共 Protocol 方法缺少方法级 docstring

**相关位置：**

- `backend/packages/harness/deerflow/publishing/quota.py:164-170`
- `backend/app/channels/feishu.py:39-42`
- `backend/app/channels/supervisor.py:24-33`

**问题说明：**

本轮新增的 `UsageRepoLike.release_unstarted_reservation()`，以及两处承载新 `system_scope` 安全契约的 `claim()` Protocol 方法仍只有省略号，没有方法级 docstring。它们需要说明不可伪造 sentinel、binding 校验、异常语义，以及“仅在确认 Run 未启动时释放”的前置条件。

**违反标准：**

- `backend/CONTRIBUTING.md` 要求公共函数、类和方法提供 docstring，并完整描述参数、返回值和异常。

**建议修复：**

补充 Google 风格方法 docstring，并让 Protocol 说明与实现的安全前置条件一致。

---

### 4.2 [P3 判断性 smell] EventDeduplicator Protocol 重复定义

**相关位置：**

- `backend/app/channels/feishu.py:39-42`
- `backend/app/channels/supervisor.py:24-33`

**问题说明：**

`FeishuEventDeduplicator` 与 `EventDeduplicator` 表达同一 `claim(binding_id, event_id, system_scope)` 契约。本次安全签名变更需要同步修改两处，已经表现出 Duplicated Code / Shotgun Surgery 风险。

**建议修复：**

把 ingress event-claim Protocol 收敛到一个中立的 App contracts 模块，由 FeishuChannel 与 Supervisor 同时引用，避免后续 scope/异常契约漂移。

---

## 5. 第二轮问题关闭情况

| 第二轮 finding | 第三轮状态 | 说明 |
|---|---|---|
| M3-RS1：多 binding 覆盖 SDK 全局 loop | **直接问题已关闭，隔离仍未关闭** | loop 不再被覆盖，但共享 loop 可被 SDK 同步连接请求整体阻塞，见 3.1 |
| M3-RS2：dispatcher 取消提前释放 Run-bound reservation | **常规路径已关闭，异常路径未关闭** | 常规取消会结算；timeout cancel 失败仍走 `release_unstarted`，见 3.2 |
| M3-RS3：动态 binding 缺流式与附件 | **部分关闭** | happy path 已实现；授权/限流顺序、慢 progress 终态丢失与 owner 文件 scope 仍有缺口，见 3.3–3.5 |
| M3-RT1：event claim 无 system scope / binding 校验 | **已关闭** | repository 与调用链均携带 sentinel，并验证持久化 Feishu binding |
| M3-RT2：Published runtime Protocol 无 docstring | **已关闭，新增契约有尾项** | 原四个 Protocol 已补齐；本轮新方法仍有第 4.1 节缺口 |

---

## 6. 验证记录

### 6.1 聚焦回归

本轮执行 M3、legacy IM、quota、Agent public API 与 boundary 相关 14 个测试文件：

```text
202 passed, 6 warnings in 51.11s
```

聚焦测试全部通过，但没有覆盖第 3 节的真实 SDK 阻塞、异常 cleanup、慢 progress 与实际 owner 路径。

### 6.2 静态与格式检查

```text
ruff check --no-cache <15 changed Python files>: All checks passed!
ruff format --check --no-cache <15 changed Python files>: 15 files already formatted
git diff --check 720d9af9...HEAD: passed
```

### 6.3 迁移检查

```text
alembic heads: 2026_07_14_channel_mappings (head)
fresh in-memory SQLite upgrade head: passed
```

### 6.4 定向诊断

- timeout cancel 抛普通异常：已启动 Run 的 reservation 仍调用 `release_unstarted`。
- progress callback 延迟 2 秒：最终 stream values 未被消费，`artifacts=()`。
- 无认证 channel dispatcher 的 ambient user 为 `default`，与 Published owner 的 uploads/outputs 路径不同。
- 锁定的 `lark-oapi 1.5.5` 在 `_connect()` 内执行同步、无 timeout 的 `requests.post`。

### 6.5 尚未完成的 Gate

- 本轮没有重新执行全 backend 测试；修复报告记录的最近一次全量结果仍是 `4303 passed, 37 skipped, 64 failed`，因此 `make test` Gate 未关闭。
- 未执行真实 PostgreSQL fresh/history migration 与跨连接并发门禁。
- 未使用两个真实 Feishu App 验证连接阻塞隔离、动态流式卡片与双向附件。

---

## 7. 建议修复顺序

1. 先修复 timeout/post-start 任意异常下的 Run-bound reservation 保留与 recovery 语义。
2. 消除共享 SDK loop 上的同步 endpoint 请求，并补真实阻塞隔离测试。
3. 把 Resolver/admission 前置到附件下载之前，实施单文件、总大小、数量和流式读取上限。
4. 将 Published 文件 I/O 改为显式 owner scope，补真实物理路径隔离测试。
5. 解耦 stream drain 与 Feishu progress I/O，确保最终 state/artifact 不依赖中间投递速度。
6. 补齐新 Protocol docstring，并收敛重复的 event claim 契约。
7. 重跑本轮聚焦测试、全 backend 测试、PostgreSQL Gate 与两个真实 Feishu App smoke。

---

## 8. 最终判定

**Ready to merge：No。**

本轮修复已让第二轮 finding 的正常路径明显前进，但多 binding 故障隔离、Run/配额 exactly-once、附件 admission、终态 artifact 可靠性与 owner 文件隔离仍存在 P1 问题。完成第 3 节问题并补齐真实 PostgreSQL/双 Feishu App Gate 后，应进行第四轮复审。
