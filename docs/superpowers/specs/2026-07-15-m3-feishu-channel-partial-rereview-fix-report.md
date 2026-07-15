# 多租户 Agent 发布平台 — M3 第二轮复审修复报告

**日期：** 2026-07-15
**关联复审：** [2026-07-15-m3-feishu-channel-partial-code-rereview.md](./2026-07-15-m3-feishu-channel-partial-code-rereview.md)
**状态：** 第二轮复审列出的 4 项 P1 与 1 项 P2 已完成代码侧修复，M3 聚焦回归、静态检查和全新 SQLite 迁移门禁通过。全 backend 测试仍有与本轮聚焦路径无直接关系的既有/环境失败，真实 PostgreSQL 与双 Feishu App 验证仍属于部署环境门禁，因此本报告不宣称最终 M3 Review Gate 已关闭。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| M3-RS1 多 binding 覆盖 SDK 全局 loop | 已修复 | 增加进程级 `_LarkSdkRuntime`，只设置一次 `lark_oapi.ws.client.loop`；所有 Client 调度到同一专用线程，每个 session 仅跟踪和停止自己的连接、receive/ping task | 两个真实 `_LarkWebSocketSession` 同时启动、双向收消息、停止一个后另一个继续工作 |
| M3-RS2 dispatcher 取消提前释放 Run-bound reservation | 已修复 | started Run 收到 dispatcher 取消后先调用 RunManager cancel 并 join，再按真实终态结算；无法确认 join 时抛出 `PublishedRunDetachedError` 并保留 pending；普通 release 仅允许 `run_id IS NULL`，新增精确 `release_unstarted(reservation, owner, run_id)` | started Run 取消后无 release、只写一次 cancelled usage；普通 release 无法释放 Run-bound reservation |
| M3-RS3 动态 binding 丢失流式与附件能力 | 已修复 | Gateway executor 消费 `StreamBridge` 的 `messages-tuple` / `values`，按既有 350ms 节流回调非终态文本；从最后一轮 values 提取 artifacts；Manager 复用附件解析/投递，并在 Run 前调用动态 Feishu Channel 下载入站文件 | 流式增量、最终附件、入站文件三条聚焦测试通过 |
| M3-RT1 event claim 无 system scope / binding 校验 | 已修复 | `claim()` 强制 `SYSTEM_CHANNEL_MAPPING_SCOPE`，写 dedup 前 join 校验持久化 Feishu binding；无 scope、伪造 scope、未知 binding 均拒绝 | 同 binding 并发仅一个 winner；相同 event id 的不同合法 binding 互不影响 |
| M3-RT2 公共 Protocol 无 docstring | 已修复 | 为 `MappingStoreLike`、`ResolverLike`、`QuotaLedgerLike`、`PublishedRunExecutor` 及其方法补充作用域、生命周期、异常和取消责任说明 | Ruff / format / compileall 静态门禁 |

---

## 2. 关键运行时不变量

### 2.1 WebSocket 所有权

- `lark-oapi` 的模块级 loop 只指向一个进程拥有的专用 event loop。
- binding 拥有自己的 Client、连接、receive/ping task 与停止信号；单 binding 停止不会调用共享 loop 的 `stop()`。
- ChannelService 在所有静态与动态 Feishu Channel 退出后关闭共享 SDK runtime。

### 2.2 Run 与配额结算

- 未绑定 Run 的 reservation 才能走普通 `release()`。
- 已预绑定但确认 Run 未创建时，只能使用 owner + 精确 run_id 的 `release_unstarted()`。
- Run 已启动后，dispatcher 负责 cancel/join 并返回真实终态，由 Runtime 写一次 settlement。
- 无法确认 Run 终止时保持 pending，让既有 durable recovery 接管，禁止提前返还并发和 token 额度。

### 2.3 动态 Feishu 消息能力

- 入站文件在 DB conversation mapping 得到 thread_id 后、Resolver/输入大小校验/Run 之前下载并注入虚拟路径；SDK 入口只接受 image/file 资源，单文件最多读取 50 MiB，超限不落盘。
- 非终态文本通过 MessageBus 发布 `is_final=False`，复用 Feishu 同卡片更新语义。
- 最终 values 只提取最后一次用户消息之后的 `present_files`，继续执行 outputs 目录约束与附件解析，最终通过 `OutboundMessage.attachments` 交给 Feishu 上传。

### 2.4 事件归属

- 验证 token / timestamp 后，dynamic ingress 使用不可伪造 system scope claim 事件。
- repository 在 dedup 插入前确认 binding 存在且 `channel_type == "feishu"`，未知 binding 不能抢占合法 event id。
- 唯一键仍为 `(binding_id, event_id)`；同一 binding 并发只有一个 winner，不同 binding 相互隔离。

---

## 3. 自动化验证

### 3.1 通过的门禁

- M3 + legacy IM 完整聚焦集：`164 passed, 7 warnings`。
- 第二轮关键子集（事件归属、双 SDK session、取消结算、动态 Run）：`58 passed, 6 warnings`；入站大小限制、取消和双 session 追加定向验证：`3 passed, 6 warnings`。
- 全 backend Ruff：`All checks passed`（扫描无权限临时目录时输出 access warning，不影响检查结论）。
- 本轮 15 个 Python 文件：`ruff format --check --no-cache`，`15 files already formatted`。
- `python -m compileall app packages/harness/deerflow`：通过。
- `alembic heads`：单 head `2026_07_14_channel_mappings`。
- 第二个全新 SQLite 从空库 `upgrade head` 并查询 `alembic_version`：`2026_07_14_channel_mappings`。
- `git diff --check`：通过。

### 3.2 全 backend 测试记录

执行 `pytest tests -q` 得到：

```text
4303 passed, 37 skipped, 64 failed, 22 warnings
```

因此全量门禁仍为非绿。本次失败包含 Windows symlink 权限、POSIX `dev-entrypoint.sh`、live 模型/本机 bash、旧迁移 head 断言，以及只在全套顺序中出现的全局状态串扰等；本轮复审列出的 164 项聚焦集单独执行全部通过。本报告保留该非绿事实，不把聚焦通过等同于全仓库通过，也没有扩大范围修改这些非本轮 finding。

---

## 4. 部署环境仍需执行的 Gate

以下验证需要真实凭据或外部数据库，不能由本地 fake/SQLite 回归替代：

1. 两个真实 Feishu App 并行连接、双向收发、停止/重启其中一个及另一绑定存活验证。
2. 动态 binding 的真实流式卡片、入站文件下载、出站图片/文件上传验证。
3. PostgreSQL fresh/history migration，以及跨连接并发 mapping/event claim/quota settlement 验证。

这些是部署门禁，不改变本报告对第二轮 review 代码 finding 的关闭结论；最终进入 M3 下一阶段前仍应完成并保存 smoke 证据。
