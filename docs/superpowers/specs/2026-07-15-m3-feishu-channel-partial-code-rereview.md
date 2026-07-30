# 多租户 Agent 发布平台 - M3 第二轮代码复审

> 复审日期：2026-07-15
> 复审状态：已复审，待修复
> 关联开发计划：`docs/superpowers/plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md`
> 关联设计规范：`docs/superpowers/specs/2026-07-12-multi-tenant-agent-publishing-design.md`
> 上游运行时规范：`docs/superpowers/specs/2026-07-14-m2-published-runtime-agent-api-impl-spec.md`
> 第一轮复审：`docs/superpowers/specs/2026-07-15-m3-feishu-channel-partial-code-review.md`
> 修复报告：`docs/superpowers/specs/2026-07-15-m3-feishu-channel-partial-review-fix-report.md`

---

## 1. 复审范围

- 复审分支：`codex/m3-feishu-supervisor`
- 固定比较点：`54e2fcd747aec84cfacbcee79b943e67afaccf6e`
- 本轮复审 HEAD：`720d9af94e4c79685391ea3618e338ed9e487126`
- 本轮修复提交：`720d9af9 fix(m3): address Feishu channel review findings`
- 复审方式：按 Spec 轴与 Standards 轴分别检查，不合并或重排两个轴的严重级别。
- 工作区中未提交的 `config.yaml`、前端文件、临时目录及图片不属于本轮修复提交，未纳入结论。

---

## 2. 复审结论

第一轮指出的主问题已经有实质修复：动态 binding 消息已进入 DB mapping、Published-Agent Resolver、quota reservation 与 Gateway Run 链路；Feishu 凭据已包含 verification token；Supervisor 已增加 ready/failure/stop 生命周期握手；mapping 读写已引入 system scope；README 与 CLAUDE 已同步。

但第二轮仍发现以下未关闭问题：

- **Spec 轴：3 个 P1**
  - 多 binding 的真实 `lark-oapi` 客户端仍共享并覆盖模块级 event loop。
  - dispatcher 被外部取消时，后台 Run 可继续执行，但其 quota reservation 已被释放。
  - 动态 binding 路径仍未实现 F3.4 要求的流式卡片与附件处理。
- **Standards 轴：1 个 P1、1 个 P2**
  - event dedup claim 仍是无 system scope、无 binding 校验的跨 owner 入口。
  - 新增公共 runtime Protocol 仍缺少规范要求的 docstring。

上述 P1 均位于已经启用的生产动态 binding 路径，不只是尚未开始的后续功能。因此本轮不能关闭 M3 Review Gate。

**结论：Ready to merge：No。**

---

## 3. 已确认修复

- **M3-S1 的正常文本主链路已接通**：消息按 `binding_id` 获取稳定 thread，经 Published-Agent Resolver 解析 release，执行输入校验、配额预占、Gateway Run 和终态结算。
- **M3-S2 的 verification token 已落入加密凭据 bundle**：创建、轮换与 Supervisor 解密启动路径已统一使用版本化凭据格式。
- **M3-S3 的单 binding 停止语义已增强**：WebSocket session 暴露 stop，Supervisor 通过 generation token、ready handshake 与 worker join 避免旧实例回写状态。
- **M3-S4 的过早 healthy 已修复**：绑定只有在连接 ready 后才进入 running，连接后失败会反馈 Supervisor。
- **M3-T1 的 mapping 主仓储已增加边界保护**：`get_or_create_thread()` 与跨 owner 列表入口要求不可伪造的 `SYSTEM_CHANNEL_MAPPING_SCOPE`，并验证 binding 与 Agent 的稳定归属。
- **M3-T2 文档同步已完成**：README、CLAUDE 与 `.env.example` 已补充动态 binding、SecretStore、兼容路径和运维说明。
- **M3-T3 大部分公共操作已补齐类型与说明**：本轮只剩第 5.2 节列出的新增 Protocol 文档缺口。

---

## 4. Spec 轴：仍需修复的问题

### 4.1 [P1] M3-RS1：多 binding 的真实 SDK event loop 会互相覆盖

**位置**

- `backend/app/channels/feishu.py:145-159`
- `backend/tests/test_feishu_websocket_lifecycle.py`
- `backend/tests/test_feishu_supervisor.py`

**问题**

`_LarkWebSocketSession.run()` 为每个 binding 在线程内创建独立 event loop，但随后把它写入 `lark_oapi.ws.client.loop`：

```python
loop = asyncio.new_event_loop()
ws_client_module.loop = loop
```

当前 lockfile 解析到的 `lark-oapi 1.5.5` 在连接、重连、ping、receive 和 message handler 创建任务时都会继续读取这个模块级全局 `loop`。第二个 binding 启动后会覆盖第一个 binding 写入的 loop；第一个客户端后续收到消息时，可能从自己的 worker 线程向第二个 binding 的 loop 创建任务，引发跨线程投递、事件丢失或连接异常。

现有生命周期测试均通过注入 fake session 验证 Supervisor 状态机，没有让两个真实 `_LarkWebSocketSession` 同时运行，因此 156 个聚焦测试通过不能证明该场景安全。

**违反规范**

- F3.2 要求按 binding 独立启动、停止、重启并做到故障隔离。
- M3 测试场景要求两个 binding 同时 running。
- M3 Gate 要求使用两个真实 Feishu 应用完成并行冒烟。

**建议修复**

1. 不要让每个 binding 的线程覆盖 SDK 模块级 loop。
2. 若 SDK 只能使用一个全局 loop，由 Supervisor 管理一个专用 SDK event-loop 线程，并把所有 Client 调度到该线程；或者封装/替换 SDK，使 loop 成为 client 实例状态。
3. 增加两个真实 `_LarkWebSocketSession` 并发启动、双向收消息、停止其中一个且另一个继续工作的适配器级测试。

---

### 4.2 [P1] M3-RS2：dispatcher 外部取消后，Run 继续执行但 quota reservation 被释放

**位置**

- `backend/app/channels/published_runtime.py:237-250`
- `backend/app/channels/published_runtime.py:363-387`
- `backend/packages/harness/deerflow/persistence/agent_usage/sql.py:427-444`

**问题**

Gateway executor 使用 `asyncio.shield(record.task)` 等待后台 Run。dispatcher 自身收到取消时，`record.task` 不会被取消，`execute()` 在确认当前 task 正在 cancelling 后直接重新抛出 `CancelledError`。外层 `PublishedChannelRuntime.run()` 捕获所有 `BaseException` 并调用 `quota.release()`。

这时 reservation 已携带预分配的 `run_id`，而 `release_reservation()` 只校验 `status == "pending"`，没有限制 `run_id IS NULL`。结果是：

1. Gateway Run 仍在后台执行并可能实际消耗 token；
2. reservation 立即变为 `released`，并发额度提前腾出；
3. durable recovery 只扫描 `pending` 且 Run-bound 的 reservation，无法再结算这条记录；
4. 最终形成未计费 Run，并破坏 usage/audit exactly-once 语义。

**违反规范**

- F3.4 要求取消、失败和成功都必须完成一致的用量结算。
- 设计规范的 quota/recovery 约束要求 reservation 与 Run 持久绑定，终态只能结算或在确认 Run 未启动时释放。

**建议修复**

1. 区分“Run 尚未创建”和“Run 已启动后的 dispatcher 取消”。
2. Run 已启动时，先请求 RunManager 取消并等待终态，再按真实终态结算；如果当前进程不能等待完成，应保留 Run-bound reservation 为 `pending`，交给 durable recovery。
3. repository 层禁止普通 release 把仍绑定有效 Run 的 pending reservation 改为 released，或提供带明确前置条件的 pre-start release 操作。
4. 增加测试：Run 启动后取消 dispatcher，断言 reservation 不会提前释放，后台 Run 最终只产生一次 settlement。

---

### 4.3 [P1] M3-RS3：动态 binding 只返回最终纯文本，流式卡片与附件仍被丢弃

**位置**

- `backend/app/channels/published_runtime.py:347-410`
- `backend/app/channels/manager.py:908-936`

**问题**

动态 runtime 虽然在 `RunCreateRequest` 中声明了 `stream_mode`，但实现只等待 `record.task` 完成，然后读取 `record.last_ai_message`。它没有消费 stream event，也没有从终态 values 中提取 `artifacts` / `attachments`。

`_handle_published_chat()` 随后只构造最终文本 OutboundMessage。入站执行同样只传递 `message.text`，没有下载或注入 `InboundMessage.files`。因此动态 binding 已被路由到新 runtime 后：

- 用户看不到 F3.4 要求的流式卡片更新；
- Agent 生成的附件不会交付到 Feishu；
- 用户发送给 Agent 的文件也不会进入 Run。

这不是 legacy 路径的能力缺口；legacy `_handle_chat()` 已有 stream、artifact 与 attachment 处理，而动态 binding 的新路径绕开了这些逻辑。

**违反规范**

- F3.4 要求“流式卡片 / 最终响应”以及附件处理。
- 设计规范运行链路要求发布最终响应或附件，并在 channel adapter 中处理流式/最终投递。

**建议修复**

1. 为 PublishedChannelRuntime 增加受控的 stream event 消费与 channel progress 回调，复用现有节流和卡片更新语义。
2. 从 Run values/terminal record 提取 artifact 与 attachment，填入动态路径的 OutboundMessage。
3. 明确定义入站 Feishu 文件的下载、大小限制、内容类型校验和 Run input 表达。
4. 增加流式增量、最终附件和入站文件三类端到端测试；在这些能力完成前，不应把动态路径标记为完成 F3.4。

---

## 5. Standards 轴：仍需修复的问题

### 5.1 [P1] M3-RT1：event claim 仍缺少 system scope 和 binding 校验

**位置**

- `backend/packages/harness/deerflow/persistence/channel_mapping/sql.py:184-215`
- `backend/app/channels/feishu.py:28`

**问题**

`ChannelMappingRepository` 的跨 owner mapping 操作已经要求 `SYSTEM_CHANNEL_MAPPING_SCOPE`，但同一模块导出的 `ChannelEventRepository.claim(binding_id, event_id)` 仍只接受两个可调用方提供的字符串。它既不要求不可伪造的 system scope，也不验证 binding 是否存在、是否属于预期动态入口。

因此任意内部调用方都可以替其他 binding 预占 event id，造成合法消息被当作重复事件丢弃；该入口也与本轮新增的 owner/system-scope 架构约束不一致。

**违反规范**

- `backend/CLAUDE.md` 要求所有 repository 查询 owner-scoped；确需跨 owner 的 Supervisor / inbound path 必须持有不可伪造的 system scope。
- F3.3 要求 event dedup 在多进程下保持一致，但不能把调用方提供的租户标识直接提升为可信边界。

**建议修复**

1. `claim()` 要求同一个 `SYSTEM_CHANNEL_MAPPING_SCOPE`（或专用的 event-claim sentinel）。
2. 在 repository 内确认 binding 是有效的持久化 channel binding，再写入 dedup 记录。
3. 增加无 scope、伪造 scope、未知 binding、跨 binding event id 和正常并发 claim 测试。

---

### 5.2 [P2] M3-RT2：新增公共 runtime Protocol 缺少 docstring

**位置**

- `backend/app/channels/published_runtime.py:52-112`

**问题**

新增并导出的 `MappingStoreLike`、`ResolverLike`、`QuotaLedgerLike`、`PublishedRunExecutor` 及其公共方法没有 docstring。它们承载稳定映射、owner context、reservation 生命周期和 Run 结算等关键契约，仅靠类型签名不足以说明调用顺序、副作用和异常语义。

**违反规范**

- `backend/CONTRIBUTING.md` 要求公共函数、类和方法使用 Google 风格 docstring，并完整提供类型注解。

**建议修复**

补充 class/method docstring，至少说明 owner/system scope、幂等键、reservation 的 acquire/release/settle 不变量、异常类型以及 executor 取消后的责任边界。

---

## 6. 验证记录

### 6.1 聚焦回归

执行：

```text
pytest tests/test_secret_store.py
       tests/test_agent_channel_repo.py
       tests/test_agent_channels_router.py
       tests/test_feishu_supervisor.py
       tests/test_channel_mapping_store.py
       tests/test_feishu_event_dedup.py
       tests/test_feishu_websocket_lifecycle.py
       tests/test_feishu_published_run_flow.py
       tests/test_channels.py
       tests/test_feishu_parser.py
       tests/test_harness_boundary.py
       tests/test_published_agents_app_wiring.py -q
```

结果：`156 passed, 5 warnings`。

### 6.2 静态检查

- `ruff check --no-cache`：通过。
- `ruff format --check --no-cache`：`19 files already formatted`。
- `git diff --check 54e2fcd7..HEAD`：通过。

### 6.3 迁移检查

- `alembic heads`：单一 head，`2026_07_14_channel_mappings`。
- 全新内存 SQLite `upgrade head`：通过。

### 6.4 尚未完成的环境门禁

- 未在本轮本地复审中执行真实 PostgreSQL 全新升级与历史升级。
- 未使用两个真实 Feishu 应用完成并行连接、收发、停启和故障隔离冒烟。
- 未执行覆盖动态 binding 的流式卡片、入站文件和出站附件端到端验证。

这些环境门禁与上述代码 findings 相互独立；即使环境门禁通过，也不能关闭第 4、5 节的问题。

---

## 7. 建议修复顺序

1. 修复 Run 外部取消后的 reservation / settlement 一致性，并补 durable recovery 回归。
2. 重构真实 `lark-oapi` 多 binding event-loop 所有权，补双实例适配器测试。
3. 完成动态 binding 的 stream、artifact、attachment 与入站文件链路。
4. 为 event claim 增加不可伪造 scope 与 binding 校验。
5. 补齐公共 Protocol docstring。
6. 重跑本轮 156 个聚焦测试、全量 backend 测试、真实 PostgreSQL 迁移和两个真实 Feishu 应用 Gate。

---

## 8. 最终复审结论

**Ready to merge：No**

本轮修复已经关闭第一轮多数直接问题，但动态 binding 的多实例隔离、取消结算和 F3.4 输出能力仍存在 P1 缺口，event claim 的 system-scope 边界也未闭合。修复以上问题并完成真实 PostgreSQL / 双 Feishu 应用门禁后，再进行下一轮复审。
