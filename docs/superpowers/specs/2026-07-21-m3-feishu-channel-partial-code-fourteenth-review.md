# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十四轮代码复审

**状态：** 已复审，仍有阻塞问题
**日期：** 2026-07-21

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十三轮复审：[2026-07-20-m3-feishu-channel-partial-code-thirteenth-review.md](./2026-07-20-m3-feishu-channel-partial-code-thirteenth-review.md)
- 第十三轮修复报告：[2026-07-20-m3-feishu-channel-partial-thirteenth-review-fix-report.md](./2026-07-20-m3-feishu-channel-partial-thirteenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后的当前未提交 backend 工作区；排除无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- 重点：第十三轮 2 个 P1、1 个 P2、2 个 Standards Important 的关闭情况，以及本轮修复 diff 的 Spec/Standards 双轴符合性

---

## 1. 复审结论

第十三轮报告中的原问题本体大多已经实质关闭：新 leader 取得 OS fence 后可安全清理 hard-kill 遗留 token；stop 失败后 quiescing runtime 仍保留唯一 cleanup retry owner；DELETE 的完整文件系统扫描已进入预启动、可 kill 的子进程；AIO cancellation/adoption 用例也已改为确定性同步并在 `finally` 中释放 worker。7 个直接相关测试文件本轮复跑为 `145 passed, 1 skipped`。

但本轮仍发现 **Spec 轴 1 个 P1、2 个 P2**：

1. provisional runtime lease 与真实 Feishu ready timeout 同为 15 秒，慢但成功的启动会在 confirm 时过期；该 `BindingStartError` 还会中止整个 `load_active_bindings()`，使已经启动的其他 transport 继续运行，却从 Gateway 管理入口消失；
2. leader fence 的阻塞获取被取消时，后台线程仍可能稍后取得 OS 锁，但 `_held` 永远保持 false，锁无法由 Supervisor 释放；
3. 两个 DELETE scanner worker 先后超时后都会被永久移出池，运行期没有非请求路径补员，后续 DELETE 将持续返回 409，直至重启 Gateway。

Standards 轴另有 **1 个 Important**：migration Gate 虽已读取类型、默认值、主键和索引结构，但类型规范化会把 Boolean/Float 等所有非 string/int 类型都归为 datetime，默认值又只做子串判断，仍可误放行错误 schema。

**结论：Ready to merge：No。** P1 直接违反 F3.2“单绑定 start 失败不抛出、不影响其他绑定”的契约，并会形成活 transport 不可管理的状态；两个 P2 也会让无需重启的动态生命周期在取消或连续 worker 故障后失去活性。

---

## 2. 第十三轮问题关闭状态

| 第十三轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1：hard-kill 后 runtime token 永久阻塞 | **原问题已关闭，发现新边界** | 真实子进程持锁/kill 回归通过，新 leader 可恢复 orphan token；但 acquire 被取消时可能产生同进程未跟踪 fence，见 3.2 |
| P1：stop 先清 `is_running` 后失败会丢 retry owner | **已关闭** | `_RunningChannel.quiescing` 与独立 cleanup task 保留唯一 generation；transport stop 和 durable release 确认前 replacement 被拒绝 |
| P2：DELETE glob/process start 不在可终止边界内 | **原问题已关闭，发现新活性问题** | whole-scan 已完全位于预启动 worker；但 timeout 会永久消耗 slot，两个 slot 后扫描池无法自恢复，见 3.3 |
| Important：migration schema Gate 只验证名称 | **部分关闭** | 已加入 columns/PK/indexes 结构比较，但类型与 default 规范化仍可误放行错误契约，见 4.1 |
| Important：AIO cancellation/adoption 测试泄漏 worker | **已关闭** | 5 秒只作为 watchdog；`finally` 无条件释放阻塞 create、取消并 drain pending/cleanup tasks；聚合执行未再挂起 |
| 7 文件 focused regression | **已关闭** | 本轮复跑 `145 passed, 1 skipped, 1 warning in 60.03s` |
| M3 部署/完整 Review Gate | **未关闭** | 本机无真实 PostgreSQL；真实双 Feishu App 冒烟仍未执行；全量 backend 结果见第 5 节 |

---

## 3. Spec 轴

### 3.1 P1：慢但成功的 Feishu 启动会中止整个 Supervisor，并留下不可管理的 peer runtime

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L28)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L354)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L394)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1147)
- [feishu.py](../../../backend/app/channels/feishu.py#L1325)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L571)
- [app.py](../../../backend/app/gateway/app.py#L289)

`RUNTIME_LEASE_TTL_SECONDS` 是 15 秒。Supervisor claim provisional token 之后才调用 `channel.start()`；真实 `FeishuChannel.start()` 先投影本地 attachment cleanup health，然后最多等待 15 秒 WebSocket ready。也就是说，一次仍处于合法 ready 窗口内的慢启动，连同 cleanup 投影和数据库调度开销，完全可能在 `confirm_runtime()` 前超过 provisional TTL。

`confirm_runtime()` 对 `runtime_lease_expires_at <= now` 返回 `None`。此时 `_start_row()` 抛出专门的 `BindingStartError`，该异常不会进入普通 start-failure 的 unhealthy 降级分支，而会在清理后继续抛出；`load_active_bindings()` 的 `asyncio.gather()` 又没有逐绑定异常隔离，因此一个慢 binding 会中止整个 Supervisor 启动。

确定性短 TTL + 慢成功 fake 复现得到：slow binding 抛出 `BindingStartError: Feishu runtime lease was revoked before registration completed`，而 fast peer 已经注册并处于 running。Gateway lifespan 随后把 `app.state.feishu_supervisor` 置为 `None`，管理 API 返回 503，但 fast transport 仍由局部 Supervisor 对象持有并继续运行，且 cleanup janitor 因 gather 提前退出尚未启动。

这直接违反开发计划 F3.2 第 781、786、795 行：单绑定 start 失败应只记录 unhealthy，不抛出、不影响其他绑定，单绑定生命周期也不应让 peer 失去管理入口。

建议：

1. 为 provisional startup 使用独立且覆盖 `cleanup projection + ready timeout + DB margin` 的 TTL，或在 claim 后立即启动 provisional renewal，并在成功 confirm/失败收敛时终止；
2. `load_active_bindings()` 对每一行隔离除任务取消以外的 start 异常，持久化 unhealthy 后继续 peers，并保证 janitor 初始化不会因单行失败跳过；
3. 增加短 TTL + 慢成功 binding + 快 peer 回归，断言 load 不抛出、快 peer 仍可经管理 API 操作、慢 binding 最终 unhealthy 且 transport/token 收敛。

### 3.2 P2：leader fence 获取被取消后可能泄漏未跟踪的 OS 锁

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L71)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1120)

`_FileRuntimeLeaderFence.acquire()` 直接等待 `asyncio.to_thread(self._lock.acquire, timeout=2.0)`。取消 asyncio waiter 并不能停止已经运行的线程：如果外部 holder 在剩余窗口内释放，后台线程仍会取得 file lock，但协程已不会执行 `_held = True`。之后 `release()` 因 `_held == False` 直接返回，Supervisor 无法释放实际已经持有的 OS fence。

确定性 fake-lock 时序可得到 `underlying_acquired=True, tracked_held=False`；调用当前 `release()` 后 underlying lock 仍保持 acquired。只要该 Supervisor/fence 对象仍被引用，同进程后续 load/retry 就会被阻塞到进程退出。

建议把 acquisition 变成可 drain 的显式 future：收到取消后仍等待 worker 收敛，并在 worker 最终成功时立即 release，再重新传播 `CancelledError`；也可使用 `filelock` 的 `cancel_check` 配合线程事件，但仍需 drain worker。增加“阻塞 acquire → 取消 waiter → 原 holder 释放”的回归，断言没有遗留 lock、下一 Supervisor 可取得 fence。

### 3.3 P2：两个 DELETE scanner timeout 后池永久耗尽

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L290)
- [feishu.py](../../../backend/app/channels/feishu.py#L345)
- [feishu.py](../../../backend/app/channels/feishu.py#L398)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1099)

`scan()` 在 timeout、IPC error 或非法 response 时终止当前 slot 并直接返回 fail closed，却不会补回 slot。补员只发生在 `start()`，而生产路径仅在 Supervisor 构造时调用一次。现有回归只证明“第一个 worker 挂起后，第二个预启动 standby 可以成功一次”，没有覆盖第二个 worker 也出现瞬时/永久故障的情况。

因此运行期两个 slot 依次超时后 `_slots` 变为空；即使文件系统和后续 worker 条件已经恢复，所有 DELETE scan 仍从空池直接返回 `True`，owner 持续收到 retryable 409，只有重新构造 Supervisor/重启 Gateway 才会补池。这违反 F3.2 的动态 DELETE 与“不重启 Gateway”生命周期语义。

建议在非请求路径增加 scanner pool manager，以有界 backoff 补足失效 slot；请求线程仍只做 IPC deadline，不直接 `Process.start()`。增加“两个 slot 依次 hang → 后台补员 ready → 健康 scan 成功”的回归，并覆盖部分 prestart 失败回滚和 Gateway shutdown。

---

## 4. Standards 轴

### 4.1 Important：migration schema Gate 的类型与默认值断言仍可误放行

**相关文件：**

- [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py#L129)
- [2026_07_17_channel_deletion_state.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_17_channel_deletion_state.py#L19)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L669)

本轮 helper 已从“只比较名称”进步到返回 columns、PK 和 indexes，但仍没有精确证明 ORM/Alembic 契约：

- 类型规范化只区分“有 length 的 string”和类名包含 `int` 的 integer，其余一律标为 `datetime`；因此 Boolean、Float 等错误类型也会满足所有 datetime 字段断言；
- `state` 默认值只要求字符串包含 `reserved`，错误的 `unreserved` 也会通过；
- `writer_generation` 默认值只要求包含字符 `0`，错误的 `10` 也会通过。

当前 migration 实现本身未观察到上述 schema 错配，但第十三轮所称的“精确契约 Gate”仍未成立；这不满足 `backend/CLAUDE.md` 第 673–677 行“bug fix 必须有能证明修复的单测、完成前完整测试通过”的要求。

建议按 SQLAlchemy type affinity/真实类型类精确规范化，并把 server default 去除方言包装、引号和 cast 后做等值比较，而非子串判断。SQLite 与 PostgreSQL upgrade/re-upgrade 应继续复用同一严格断言。

### 4.2 非阻塞维护判断

- `supervisor.py` 同时协调 runtime、leader、secret ingest、删除 tombstone 与 attachment recovery，`feishu.py` 同时承载渠道、cleanup outbox 和 worker pool，存在 **Divergent Change**；后续可拆出 runtime lifecycle 与 cleanup/scanner coordinator。
- AIO sandbox 生命周期仍以 `dict[str, object]` 和字符串状态传播 owner/generation/lease，存在 **Primitive Obsession / Data Clumps**。两项均不单独阻塞本轮修复。

---

## 5. 验证记录

### 5.1 直接相关测试

```text
145 passed, 1 skipped, 1 warning in 60.03s
```

唯一 skip 是本机没有 `TEST_POSTGRES_URL`；warning 为 LangGraph `allowed_objects` pending deprecation，不是本轮失败。

Harness 分层边界：

```text
1 passed, 1 warning in 2.18s
```

### 5.2 静态检查

```text
ruff check --no-cache <17 个相关 Python 文件>
All checks passed!

ruff format --check --no-cache <17 个相关 Python 文件>
17 files already formatted

git diff --check <固定点> -- backend
通过
```

首次未带 `--no-cache` 的 Ruff 因工作区既有 `.ruff_cache` 目录权限被拒绝，改用官方无缓存模式后检查通过；不属于源码失败。

### 5.3 尚未关闭的 Gate

- 本机未配置 `TEST_POSTGRES_URL` / `DATABASE_URL` / `POSTGRES_URL`，真实 PostgreSQL migration contract 仍需在 `REQUIRE_POSTGRES_TESTS=1` 的 CI 中执行。
- 尚未执行两个真实 Feishu App 的 WebSocket ready、轮换、stop failure 和 attachment recovery 冒烟。
- 全量 backend `pytest tests -q` 运行 601 秒后到达命令上限，只推进到约 63%，没有生成最终汇总；超时前已出现多项失败，但安静模式没有给出失败归属。命令结束后确认没有残留 Python/pytest 进程。本报告不把未完成运行中的 failure 数量或归属作为稳定结论，也不声明全量 Gate 通过。

---

## 6. 最终结论

第十三轮的 hard-kill recovery、quiescing retry owner、whole-scan killable boundary 和 AIO 测试泄漏问题已经关闭；migration Gate 也有明显补强。但新的 startup lease/异常隔离组合问题可以让一个慢 binding 撤销整个 Supervisor 的管理入口，同时留下仍在运行的 peer transport，属于必须先修复的 P1。

建议优先顺序：

1. 修正 provisional startup lease 与逐 binding startup isolation，并补双 binding 确定性回归；
2. 让 leader acquire 对取消安全，确保后台锁获取必然被 drain/release；
3. 为 DELETE scanner 增加非请求路径的自动补员和完整生命周期测试；
4. 收紧 migration 类型/default 断言；
5. 最后完成真实 PostgreSQL、双 Feishu App 和全量 backend Gate。

**Ready to merge：No。**
