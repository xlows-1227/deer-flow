# 多租户 Agent 发布平台 — M3 第三轮复审修复报告

**日期：** 2026-07-15

**关联复审：** [2026-07-15-m3-feishu-channel-partial-code-third-review.md](./2026-07-15-m3-feishu-channel-partial-code-third-review.md)

**状态：** 第三轮复审列出的 5 项 P1、1 项 P2 和 1 项 P3 判断性 smell 已完成代码侧修复，并通过定向、聚焦、静态和全量回归核验。真实 PostgreSQL 与双 Feishu App smoke 仍属于部署环境 Gate，本报告不将其声明为已完成。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| 共享 SDK loop 被同步 endpoint 请求阻塞 | 已修复 | 使用带 connect/read timeout 的异步 HTTP 获取 endpoint；SDK `_connect()` 只消费缓存 URL；每个 binding 有独立连接 deadline | 第二个 endpoint 永久阻塞时，第一个 session 仍能 ready、ping、收消息；阻塞 session 独立超时退出 |
| timeout cleanup 失败误释放 started Run reservation | 已修复 | `run_starter` 返回后，timeout/cancel 清理失败统一转换为 `PublishedRunDetachedError`；RunManager 未确认取消且 worker 仍存活时同样保持 detached/pending | cancel 抛出持久化错误时不调用 `release_unstarted`、不提前 settlement；既有 recovery exactly-once 测试继续通过 |
| 附件在 Resolver/quota 前下载且未计入有效输入额度 | 已修复 | 调整为 mapping → Resolver → 轻量准入 → reserve → 有界下载 → Run；限制文件数、单文件和实际总字节；64 KiB 分块写盘；失败清理本次全部文件 | 未发布与 quota 拒绝时零下载；聚合超限无残留；精确边界通过；materialization 失败只释放尚未启动的 reservation |
| 慢 progress 阻塞 stream drain 并丢失最终 artifacts | 已修复 | stream consumer 与外部 progress I/O 解耦；使用容量为 1 的 latest-value queue 和独立 sender；慢 progress 仅允许丢弃中间快照 | 2 秒 progress callback 下执行快速返回，最终文本和 `present_files` artifacts 完整 |
| Published 文件 I/O 落入 ambient `default` owner | 已修复 | Resolver 得到的 `owner_user_id` 显式传入入站 uploads、通用 ingest、最终 outputs 解析和 attachment delivery；Execution 携带可信 owner | 使用真实 `Paths` 对相同 thread 的两个 owner 做物理路径隔离；Manager 最终 artifact 解析断言使用 `owner-a` |
| 公共 Protocol 方法缺少方法级 docstring | 已修复 | 为 reservation release 与 event claim 补充 Args、Returns、Raises 和安全前置条件 | Ruff、format、compileall 通过 |
| EventDeduplicator Protocol 重复定义 | 已修复 | 收敛到中立的 `app.channels.contracts.EventDeduplicator`，Feishu adapter 与 Supervisor 共同引用 | Feishu lifecycle、event dedup 与 Supervisor 回归通过 |

---

## 2. 关键运行时不变量

### 2.1 多 binding 连接隔离

- `lark-oapi` 的模块级 loop 仍只由进程级 `_LarkSdkRuntime` 设置一次。
- endpoint 请求由 `httpx.AsyncClient` 执行，connect/read timeout 分别为 5 秒和 10 秒，不再在共享 SDK loop 中执行 `requests.post()`。
- endpoint 获取与 WebSocket 建连共同受单 binding 15 秒 deadline 约束；一个 binding 卡住只会让该 binding 启动失败，不会冻结其他连接的 receive/ping。

### 2.2 Run 与 reservation 生命周期

- quota reserve 之后、Run 启动之前的失败才允许 `release_unstarted(owner, reservation, run_id)`。
- `run_starter` 返回后，timeout/cancel 清理无法确认完成时抛出 `PublishedRunDetachedError`，reservation 保持 pending，由 durable recovery 接管。
- RunManager 返回未取消且 worker 仍存活时不会无限 join，也不会将 reservation 误标为 `unstarted/released`。

### 2.3 附件准入与资源上限

- 可信顺序为：mapping → Resolver → 文件数量/声明大小/文本准入 → quota reserve → 实际下载 → Run。
- Published Feishu 单次最多 10 个文件，单文件最多 50 MiB，且 `文本 UTF-8 字节 + 实际附件字节` 不得超过 effective `max_input_bytes`。
- provider 流按 64 KiB 分块直接写入安全文件句柄，不再一次性把 50 MiB 内容读入内存。
- 单文件失败、聚合超限、sandbox 同步失败或任务取消时，当前部分文件及本次已完成文件都会被清理。

### 2.4 流式最终态可靠性

- StreamBridge consumer 只负责持续消费 `messages-tuple`、`values` 和 `__end__`，不再同步等待 Feishu/MessageBus 网络 I/O。
- progress queue 只保留最新中间快照；慢回调在 250 ms drain 窗口后可以被丢弃。
- 最终文本与 artifacts 来自已完整 drain 的最终 values，不依赖“1 秒内恰好发完 progress”。

### 2.5 owner 文件隔离

- Published inbound materialization 显式使用 `context.owner_user_id` 写入 owner uploads。
- 通用 published ingest helper 支持显式 owner，不再只能读取 ambient ContextVar。
- Runtime 返回的 `PublishedChannelExecution` 携带可信 owner；Manager 使用它解析 outputs 与构造附件。
- legacy IM 调用仍保留 ambient fallback，兼容既有非 Published 路径。

---

## 3. 自动化验证

### 3.1 第三轮关键定向回归

覆盖 timeout cleanup、慢 progress、materialization 失败释放：

```text
3 passed, 2 warnings
```

覆盖 SDK loop 隔离、附件 admission/边界/清理、双 owner 真实路径：

```text
9 passed（分组执行）
```

最终 Feishu、Published Run、event dedup、Supervisor 与 quota 核心回归：

```text
72 passed, 6 warnings in 32.79s
```

### 3.2 M3 + legacy IM 聚焦集

14 个相关测试文件执行结果：

```text
215 passed, 2 failed, 8 warnings in 68.06s
```

两项失败均在测试准备阶段创建 Windows symlink 时触发 `WinError 1314`，属于当前账户缺少符号链接权限；没有执行到本轮业务代码。其余 M3、legacy Channel、quota、attachment、mapping 与 wiring 用例全部通过。

### 3.3 全 backend 回归

```text
4312 passed, 37 skipped, 64 failed, 22 warnings in 540.16s
```

失败数量与第二轮报告一致。本轮新增 9 项回归全部通过，且失败列表中没有第三轮新增用例；现有 64 项仍包括 Windows symlink 权限、POSIX `dev-entrypoint.sh`、live model/bash、本机路径/编码、全局状态串扰和旧 migration head 断言等既有环境或仓库问题。因此全 backend Gate 仍非绿色，本轮没有扩大范围修改这些无关问题。

### 3.4 静态、格式与迁移检查

```text
ruff check --no-cache <11 changed Python files>: All checks passed!
ruff format --check --no-cache <11 changed Python files>: 11 files already formatted
python -m compileall app packages/harness/deerflow: passed
alembic heads: 2026_07_14_channel_mappings (head)
git diff --check <本轮文件>: passed
```

本轮没有新增或修改数据库 migration。

---

## 4. 仍需部署环境完成的 Gate

以下验证不能由本地 fake/SQLite 回归替代：

1. 两个真实 Feishu App 并行长连接，其中一个 endpoint/网络异常时验证另一 binding 的实际消息接收与心跳存活。
2. 真实动态 binding 的流式卡片更新、重试、最终文本，以及双向图片/文件传输。
3. PostgreSQL fresh/history migration 与跨连接并发 mapping、event claim、quota settlement/recovery。

---

## 5. 最终判定

**第三轮 review 的代码 finding：已关闭。**

从代码和本地自动化证据看，第三轮列出的 5 个 P1 已完成修复，P2/P3 standards 项也已关闭。由于全 backend 仍有 64 项既有失败，且真实 PostgreSQL 与双 Feishu App Gate 尚未执行，最终 M3 部署验收仍应在这些环境验证完成后再判定为 Ready to merge。
