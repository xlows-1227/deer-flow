# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十三轮代码复审

**状态：** 已复审，仍有阻塞问题
**日期：** 2026-07-20

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十二轮复审：[2026-07-20-m3-feishu-channel-partial-code-twelfth-review.md](./2026-07-20-m3-feishu-channel-partial-code-twelfth-review.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后的当前未提交 backend 工作区；排除无关的 `config.yaml`、frontend、图片和临时目录改动
- 重点：第十二轮 2 个 P1、2 个 P2、迁移/测试 Gate 的关闭情况，以及修复 diff 的 Spec/Standards 双轴符合性

---

## 1. 复审结论

第十二轮的 AIO live-owner 抢占和 secret writer/janitor 竞争已经实质关闭；Feishu runtime 也已改为先 claim 再打开 transport，lost-token/续租异常会 fail closed，本地 stop 未确认时不再用时间戳冒充退出确认。DELETE 的单文件 JSON reader 已进入可 kill 的子进程，迁移与 focused regression 文档也有明显补强。

但本轮仍发现 **Spec 轴 2 个 P1、1 个 P2**：

1. runtime token 只允许原 owner 显式释放，导致 Gateway 硬崩溃后 active binding 永久无法恢复、停止或删除；
2. 真实 `FeishuChannel.stop()` 会先把 `is_running` 置为 false，再可能因 SDK session/thread 未退出而抛错；下一次 start 会弹出唯一重试句柄，使活 transport 失去 Supervisor 管理；
3. DELETE 虽隔离了文件读取，但目录枚举和 `multiprocessing.Process.start()` 仍在可终止边界之外，挂死后会永久持有全局扫描锁。

Standards 轴另有 **2 个 Important**：migration Gate 对 ingest 表只比较列名/索引名集合，无法捕获主键、类型、nullable/default、索引列顺序或 unique 属性错误；AIO cancellation/adoption 测试还以 1 秒墙钟作为并发正确性断言，超时时未释放阻塞 worker，使聚合测试出现 1 个失败并额外挂住约 300 秒。

**结论：Ready to merge：No。** 两个 P1 都破坏 F3.2 的动态生命周期完成语义；需先补齐 crash recovery authority、stop retry ownership 和确定性回归，再进入 M3 Review Gate。

---

## 2. 第十二轮问题关闭状态

| 第十二轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1：runtime lease 接管/释放后旧 transport 继续运行 | **部分关闭** | claim 已前移，lost-token/续租异常会停止本地 transport，stop 未确认也不再按 expiry 放行；但硬崩溃 token 永久占用，且 `is_running=False` 的 stop 失败会丢唯一重试句柄 |
| P1：AIO explicit discovery 抢占未过期 live owner | **已关闭** | 同步/异步 discovery 均在 sandbox lock 内拒绝其他 provider 的未过期 live lease；过期 owner 才可推进 generation |
| P2：secret writer 与 janitor 竞争产生孤儿密文 | **已关闭** | `reserved → writing(token,generation,lease) → ready` 已落库并续租；过期 writer 在密文尚未出现时保留 DB cleanup owner，迟到密文可在后续 janitor pass 被擦除 |
| P2：DELETE 使用无界、不可终止 JSON reader | **部分关闭** | 单文件 `stat/read_text/json` 已置于 killable child；但目录枚举和 child `start()` 仍可在父线程永久阻塞并占住全局锁 |
| Important：迁移 schema Gate 不完整 | **部分关闭** | 已覆盖列名、三个索引名、downgrade 删除和 re-upgrade 恢复；尚未验证完整 schema 语义 |
| focused regression 文档漏测 | **已关闭** | README/CLAUDE 已加入 secret store、migration 和 `REQUIRE_POSTGRES_TESTS=1` 说明 |
| M3 Review Gate | **未关闭** | 9 个直接修复回归、SQLite migration 与 harness boundary 已通过；7 文件聚合集合为 `1 failed, 140 passed, 1 skipped`，且真实 PostgreSQL、多 Gateway/process-kill、双飞书 App 和全量 `make test` 仍未闭环 |

---

## 3. Spec 轴

### 3.1 P1-1：Gateway 硬崩溃后 active binding 的 runtime token 永久不可恢复

**相关文件：**

- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L538)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L623)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1048)
- [test_agent_channel_repo.py](../../../backend/tests/test_agent_channel_repo.py#L257)

`claim_runtime()` 现在把 lease 时间戳明确降级为诊断信息：只要 row 中存在另一个 token，即使 `runtime_lease_expires_at` 已过期也拒绝新 owner。`_await_runtime_quiesced()` 同样只接受匹配 owner 的显式 `release_runtime()`。这关闭了“旧 transport 还活着时仅凭超时接管”的双实例风险，却没有提供能证明旧进程已经死亡的恢复 authority。

确定时序如下：

1. Gateway A claim runtime，并已打开或即将打开 WebSocket；
2. A 被硬 kill，transport 随进程退出，但来不及执行 `release_runtime()`；
3. 数据库保留 active row 和旧 token；
4. Gateway B 启动执行 F3.2 要求的 `load_active_bindings()`，新 token 永远 claim 失败；
5. STOP/DELETE 在 B 上也没有本地 `_running` owner 可以显式 release，只能持续返回 cleanup pending。

现有 `test_expired_runtime_timestamp_does_not_authorize_takeover()` 只锁定了“不能按时间戳直接抢占”，没有覆盖进程死亡后的安全恢复。F3.2 明确要求 Gateway 启动加载全部 active binding；当前状态需要手工改库，active binding 在正常 crash/restart 后不可用。

建议不要退回“仅凭 expiry 覆盖 token”。开发计划第一版若坚持单实例 Supervisor，应先取得数据库 advisory lock/单实例 leader fence，再由新 leader 清理已确认无旧进程的 token；若保留多 Gateway 支持，则需要持久化 supervisor instance identity、存活 membership/epoch，并只回收已被可靠判死的 owner generation。增加真实子进程 `claim → hard kill → new Gateway load_active/STOP/DELETE` 回归，验证既不双开，也能最终恢复。

### 3.2 P1-2：stop 失败后 `is_running=False` 会让下一次 start 丢掉唯一 transport 句柄

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L1271)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L250)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L439)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L793)

真实 `FeishuChannel.stop()` 在调用 `session.stop()` 和等待 worker thread 退出之前，先执行 `_running = False`。随后 `session.stop()` 返回 false，或 thread 在 5 秒后仍存活，方法会抛错。Supervisor 的 unpublished cleanup 会正确保留 `_RunningChannel`、数据库 token，并安排 `_retry_local_runtime_stop()`；问题出在下一次 start：`_start_row()` 看到 `existing.channel.is_running == False` 就立即从 `_running` 弹出并 unregister，既不等待/取消原 retry task，也不再次确认底层 session/thread 已退出。

之后新 claim 被旧 token 拒绝，而旧 retry task 醒来后因 `_running` entry 已消失直接返回。结果是仍可能存活的 SDK session/thread 已无任何可达句柄，数据库却永久保留旧 token；既不能继续 stop，也不能安全 replacement。

本轮 fake 回归通过 `fail_stop_running_app_ids` 刻意让 stop 抛错时 `is_running` 仍为 true，没有覆盖真实 channel 的“先 false、后抛错”语义。

建议把“连接是否 ready/running”与“transport 是否已确认退出”分成两个状态。只要 stop/release retry 仍持有 generation，`_start_row()` 就不得 pop 该 entry；应返回 retryable cleanup pending，并让唯一 quiescing owner 持续重试。增加使用真实 stop 顺序的 fake：先置 `is_running=False`、保留 live session/thread、再抛错；并发/后续 start 后必须仍只有一个可达 retry owner，最终退出后才 release token。

### 3.3 P2-1：DELETE 的目录枚举和 child 启动仍在可终止边界之外

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L244)
- [feishu.py](../../../backend/app/channels/feishu.py#L409)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L966)

`has_published_attachment_cleanup_backlog()` 已把每个候选文件的 `stat/read_text/json.loads` 放到可 kill 的 spawn child，这关闭了第十二轮最直接的无界 reader。但“整个扫描 2 秒有界”仍不成立：

- `outbox_dir.glob("*.json")` 的目录枚举在父 `to_thread` 中执行；网络盘、故障挂载或文件系统驱动异常时不可终止；
- `_read_attachment_cleanup_job_isolated()` 的 `process.start()` 在 `poll(timeout)` 之前执行，spawn/OS 创建进程若卡住，同样没有 deadline；
- 外层 `asyncio.wait_for(asyncio.to_thread(...))` 只取消等待，不能终止线程；该线程会永久持有 `_ATTACHMENT_BACKLOG_SCAN_LOCK`，后续所有 binding DELETE 都 fail closed 为 409，直至 Gateway 重启。

建议把“枚举 + 大小检查 + 读取 + 解析”作为一次完整的 killable worker operation，父进程只负责 deadline、terminate/kill/join；不要在受保护线程内再 spawn child。至少补两个确定性回归，分别令目录枚举和 worker start 永不返回，断言首个 DELETE 在 deadline 后结束、执行资源被回收，后续健康扫描仍可成功。

本项不泄漏或误删数据，因此定为 P2；但它仍阻断 owner 删除和 M3 的生命周期 liveness。

---

## 4. Standards 轴

### 4.1 Important：migration schema Gate 仍只验证名称，不验证契约

**相关文件：**

- [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py#L121)
- [2026_07_17_channel_deletion_state.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_17_channel_deletion_state.py#L19)
- [model.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/model.py#L57)

`_channel_ingest_schema()` 只返回 table name、column name set 和 index name set。于是以下错误仍会通过 SQLite/PostgreSQL migration Gate：

- `secret_ref` 不是主键，或字符串长度错误；
- 必填字段被迁移为 nullable；
- `state` / `writer_generation` 默认值错误或缺失；
- due index 的列顺序错误，或单列索引被错误建成 unique；
- `runtime_stop_requested` 缺少 false server default（当前测试只断言 boolean + non-null）。

第十二轮要求的表存在、列、三个索引、downgrade/re-upgrade 已补齐，但测试尚不能证明 ORM 与 Alembic schema 一致。仓库要求 bug fix 具备可证明修复的单元测试，且 feature 完成前测试必须通过。

建议让 helper 返回 `get_columns()`、`get_pk_constraint()`、`get_indexes()` 的结构化结果；在 SQLite 和 PostgreSQL upgrade/re-upgrade 都断言类型/长度、nullable/default、PK、索引列顺序和 `unique=False`，downgrade 继续断言表/新增列消失。

### 4.2 Important：AIO cancellation/adoption 回归用墙钟判正确性，失败时泄漏阻塞 worker

**相关文件：**

- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py#L730)

`test_cancelled_create_does_not_destroy_sandbox_adopted_by_second_provider` 在 backend create worker 被 `release_create` 阻塞时，要求 provider B 的完整 acquire/file-lock release 必须在 1 秒内完成；只有该断言成功后才会 `release_create.set()`。Windows/聚合负载下 B 的同一路径可能略超 1 秒，`wait_for()` 随即取消并抛错，测试跳过 release，导致 provider A 的默认-executor worker 永久阻塞。事件循环关闭会继续等待 executor 约 300 秒，测试失败因此同时退化为 suite hang。

本轮聚合集合确定得到：

```text
FAILED tests/test_aio_sandbox_provider.py::test_cancelled_create_does_not_destroy_sandbox_adopted_by_second_provider[asyncio]
1 failed, 140 passed, 1 skipped, 2 warnings in 349.49s
RuntimeWarning: The executor did not finishing joining its threads within 300 seconds.
```

单独重跑该 node 也在 121 秒外层上限内无法退出。使用同一时序但在 `finally` 中无条件 release worker、把观测窗口放宽到 5 秒的诊断脚本可完成，B acquire 实测约 0.75 秒，说明原来的 1 秒阈值把机器负载误当成语义失败。

建议用事件/状态断言 ownership handoff，而不是把 1 秒当 correctness contract；`release_create.set()` 和 cleanup-task drain 必须放在 `finally`，确保任何 assertion/cancellation 分支都不会泄漏 executor worker。若仍需防死锁，外层 timeout 可保留为较宽的测试保险，但不应替代确定性同步。

### 4.3 判断性维护项

- `supervisor.py` 已超过 1200 行，同时协调 runtime、secret ingest、删除 tombstone 和 attachment recovery，存在 **Divergent Change**；建议后续拆出 runtime lifecycle coordinator 与 secret cleanup coordinator。
- AIO lifecycle 仍以 `dict[str, object]` 和重复字符串状态传递 owner/generation/lease，属于 **Primitive Obsession / Data Clumps**；建议引入 typed lifecycle record 和状态枚举。

这两项不单独阻塞本轮合并；本轮未发现新增的类型标注、公开 docstring、harness→`app.*` 边界或格式违规。

---

## 5. 验证记录

### 5.1 第十二轮直接修复回归

runtime fencing、AIO live owner、secret writer/janitor 与 DELETE isolated reader 的 9 个测试实例：

```text
9 passed, 1 warning in 9.77s
```

warning 为 LangGraph `allowed_objects` 待弃用提示，不是本轮失败。

SQLite migration 双向测试与 harness boundary：

```text
2 passed, 1 warning in 4.33s
```

### 5.2 静态检查

```text
ruff check --no-cache <本轮 16 组相关 Python 路径>
All checks passed!

ruff format --no-cache --check <本轮 16 组相关 Python 路径>
16 files already formatted

git diff --check HEAD -- backend
通过
```

### 5.3 未关闭 Gate

- 本机未配置/未强制执行真实 PostgreSQL Gate；合并前需以 `REQUIRE_POSTGRES_TESTS=1` 运行。
- 未执行多 Gateway + process hard-kill 集成 Gate，P1-1 当前也没有相应测试。
- 未执行真实双飞书 App 冒烟。
- 7 个相关测试文件的聚合集合生成了失败汇总后，因上述阻塞 worker 未退出而被 601 秒外层上限终止；pytest 自身汇总为 `1 failed, 140 passed, 1 skipped, 2 warnings in 349.49s`。因此 focused Gate 当前明确不绿，M3 要求的全量 `make test` 也未在本轮独立复现。

---

## 6. 最终结论

第十二轮四个核心修复方向都取得了实质进展，其中 AIO live-owner 和 secret writer/janitor 已关闭；但 runtime 当前在“绝不错误接管”与“崩溃后可恢复”之间缺少可靠的 owner-death fence，且 stop retry ownership 会被 `is_running` 这个 readiness 标志错误驱逐。这两个 P1 使 active binding 在正常故障恢复或 stop 异常后永久不可管理。

建议优先顺序：

1. 引入可证明旧 owner 死亡的 runtime recovery authority，并补 process-kill/restart 测试；
2. 将 quiescing/retry ownership 与 `is_running` 解耦，任何 replacement 前必须确认底层 transport 已退出；
3. 把 DELETE 整体扫描收进单一可 kill worker；
4. 修正 AIO cancellation/adoption 测试的确定性同步和失败清理；
5. 补完整 migration schema 断言，最后执行 PostgreSQL、多 Gateway/process-kill、双飞书 App 和全量 `make test` Gate。

**Ready to merge：No。**
