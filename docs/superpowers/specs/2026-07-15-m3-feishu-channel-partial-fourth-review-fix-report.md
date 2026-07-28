# 多租户 Agent 发布平台 — M3 第四轮 Review 修复报告

**日期：** 2026-07-15
**关联 Review：** [2026-07-15-m3-feishu-channel-partial-code-fourth-review.md](./2026-07-15-m3-feishu-channel-partial-code-fourth-review.md)
**状态：** 第四轮 Review 列出的 5 项 P1 与 2 项 P2 已完成代码侧修复和本地自动化核验。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| Run 完成后取消 progress drain 会误释放 reservation | 已修复 | `run_starter` 成功后进入统一 started-state 契约；所有未处理异常/取消统一转换为 `PublishedRunDetachedError`。progress drain 取消和 progress cleanup 超时均保留 Run-bound reservation | Run success + slow progress + drain 阶段取消时，`release_unstarted()` 与 settlement 均为零调用，reservation 留给恢复任务 |
| timeout cleanup 可无限等待 | 已修复 | `RunManager.cancel()` 与 worker join 分别使用独立、短且有界的 cleanup deadline；超时转 detached recovery | worker 吞掉第一次 `CancelledError` 并永久等待时，dispatcher 在 deadline 内退出，reservation 未释放 |
| SDK binary resource API 仍整包缓冲 | 已修复 | Published 附件不再调用 `message_resource.get()`；改用携带 tenant token 的 `httpx` 真正流式 GET，先检查 `Content-Length`，再按 transport 原始 chunk 累计实际字节 | 精确边界成功；声明超限时零 chunk 读取；无长度头的响应在超限 chunk 立即停止，后续 payload 未生成 |
| sandbox acquisition/cache 未贯穿 owner | 已修复 | Feishu materialize 显式传入可信 `owner_user_id`；Local/AIO provider 绑定 thread 与 owner，冲突 fail-closed；AIO deterministic ID 包含 owner；Published worker 全生命周期建立真实 user ContextVar | 真实 Local/AIO provider owner 回归通过；同 thread 的 owner 冲突被拒绝；owner uploads、virtual path、Run context、outputs 与最终 attachment 使用同一 owner |
| 取消 `to_thread()` 后可能留下 host/sandbox 文件 | 已修复 | 网络下载改为 async transport，取消会关闭 response 并删除已登记 partial；非挂载 sandbox 同步延后到整批附件准入完成之后，取消时有界等待 worker，超时则登记受跟踪 cleanup task，最终同时删除 host 与 sandbox 残留 | 阻塞网络流取消后 owner uploads 为空；阻塞 sandbox sync 释放 worker 后 host 与 remote sandbox 均为空 |
| README/CLAUDE 与实现不一致 | 已修复 | 同步最终准入顺序、10 文件、50 MiB、实际聚合字节、5/10/60 秒网络边界、progress latest-value queue、owner 与 cleanup 语义 | 文档内容已与本报告所述实现一致 |
| 公共附件 API docstring 不完整 | 已修复 | 为 `materialize_published_files()`、`get_uploads_dir()`、`ensure_uploads_dir()` 补充 Google 风格 Args/Returns/Raises，并说明可信 owner、ambient fallback 和取消清理契约 | Ruff 与格式检查通过 |

---

## 2. 最终运行时不变量

### 2.1 Run 与 reservation

- `run_starter` 返回前的失败仍允许按精确 `run_id` 执行 `release_unstarted`。
- `run_starter` 返回后，executor 的任意未处理异常或取消都表示 Run 已启动，只能结算终态或进入 detached recovery，不能再降级成 unstarted。
- cancellation RPC、worker join 和 progress cleanup 均有独立 deadline；不合作的 worker 不再无限占用 Feishu dispatcher。
- progress callback 与主 stream consumer 解耦。容量为 1 的队列只保留最新中间进度，250 ms drain window 后可以丢弃中间进度，但最终 values/artifacts 不依赖 callback 完成。

### 2.2 附件准入与流式下载

- 顺序固定为：mapping → Published resolver → 廉价声明检查 → quota reserve → 实际附件 materialize → Run。
- 单次最多 10 个文件，单文件最多 50 MiB，且 `UTF-8 文本字节 + 实际附件字节` 不得超过 Release 的 effective `max_input_bytes`。
- tenant token 请求和 resource 下载均使用 async HTTP；resource 连接、单次读取和整体下载边界分别为 5 秒、10 秒和 60 秒。
- 有 `Content-Length` 时在读取 body 前拒绝超限；无论是否有长度头，都对每个原始网络 chunk 做累计检查。
- 只有整批文件全部通过实际字节准入后才同步到非挂载 sandbox，避免 aggregate rejection 留下 remote 文件。

### 2.3 owner 与取消清理

- `owner_user_id` 只能来自 Published resolver，不读取 Feishu 消息中的非可信 owner 字段。
- host uploads、sandbox acquisition、provider cache ownership、Run worker ContextVar、outputs 解析和最终附件发送使用同一 owner。
- Local/AIO provider 对已绑定 thread 的 owner 冲突 fail-closed；AIO 跨进程 deterministic sandbox ID 同时包含 owner 和 thread。
- async HTTP 取消会关闭 response、关闭文件句柄并删除 partial file。
- sandbox 同步线程在取消后先进行有界 join；若仍未退出，则由持有强引用的 cleanup task 等待 worker 结束，再删除可能写入的 sandbox 文件和 host 文件。

---

## 3. 自动化验证

### 3.1 第四轮关键回归

- post-start progress drain 取消：通过。
- worker 抑制取消的 bounded cleanup：通过。
- chunk-only transport 精确边界、`Content-Length` 预拒绝、运行中累计超限：通过。
- 网络流取消与阻塞 sandbox sync 的最终清理：通过。
- 真实 Local/AIO provider owner 绑定与冲突拒绝：通过。
- Published worker owner ContextVar 设置与恢复：通过。

### 3.2 M3 + attachment + sandbox 聚焦集

执行 13 个相关测试文件：

```text
228 passed, 2 failed, 6 warnings in 53.83s
```

两项失败均发生在测试 setup 创建 Windows symlink 时，错误为 `WinError 1314`：

```text
test_rejects_preexisting_symlink_destination
test_rejects_dangling_symlink_destination
```

当前 Windows 账户缺少创建符号链接权限，两个用例未执行到业务代码；该环境限制与第四轮修复前 Review 的记录一致。本报告不据此声明 symlink 安全用例已通过。

补充定向结果：

```text
Published flow + Feishu parser + user context + gateway services: 92 passed
AIO provider + sandbox middleware: 24 passed
AIO sandbox delete/streaming/serialization: 25 passed
Local provider explicit-owner regression: 1 passed
```

### 3.3 静态与编译检查

```text
ruff check --no-cache <17 changed Python files>: All checks passed!
ruff format --check/format <changed Python files>: passed
python -m compileall app packages/harness/deerflow: passed
git diff --check: passed
```

本轮没有新增或修改数据库 migration。

---

## 4. 仍需部署环境完成的 Gate

以下内容不由本地 fake/SQLite 回归替代：

1. 在具备 symlink 权限的 Windows 环境或 Linux CI 重跑两个上传目标安全用例。
2. 两个真实 Feishu App 的并行长连接、凭据轮换、流式卡片与双向大文件 smoke。
3. 真实 PostgreSQL fresh/history migration，以及多进程 mapping、event claim、quota settlement/recovery 并发验证。
4. 非挂载远程 AIO sandbox 的真实大文件流式上传、取消和清理 smoke。

---

## 5. 最终判定

**第四轮 Review 的代码 finding：已关闭。**

当前没有遗留第四轮 Review 所列的 P1 代码问题。M3 是否最终 Ready to merge，仍应在第 4 节的部署环境 Gate 完成后判定；本报告不把未执行的真实 Feishu/PostgreSQL/远程 sandbox 验证声明为已通过。
