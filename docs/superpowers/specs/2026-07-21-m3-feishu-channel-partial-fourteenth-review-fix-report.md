# 多租户 Agent 发布平台 - M3 飞书渠道部分第十四轮 Review 修复报告

**日期：** 2026-07-21

**关联 Review：** [2026-07-21-m3-feishu-channel-partial-code-fourteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-fourteenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十四轮列出的 1 项 P1、2 项 P2 和 1 项 Standards Important 已完成代码修复及本地自动化回归。评审指定 7 文件聚合结果为 `161 passed, 1 skipped`；真实 PostgreSQL、真实双 Feishu App 和全量 backend 仍属于外部/完整 Gate。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：慢启动租约过期会中止整个 Supervisor | 已修复 | 将 provisional startup lease 独立为 30 秒，不再复用 15 秒运行期心跳租约；`load_active_bindings()` 对每个 binding 隔离除 cancellation 外的异常，失败行持久化 unhealthy，peer 与 cleanup janitor 保持可管理 | 短运行期 TTL + 慢成功启动可正常 confirm；强制 runtime confirm 失败时 load 不抛出，失败 token/transport 收敛，快 peer 保持 healthy/running，janitor 已启动 |
| P2：取消 leader fence acquire 会泄漏晚到 OS 锁 | 已修复 | acquire 使用显式 task + `shield`；收到取消后不可中断地 drain 阻塞 worker，若 worker 晚到成功则先完成跨线程 release，再传播 `CancelledError` | 阻塞 fake lock → 取消 waiter → 允许后台 acquire，断言 underlying lock 已释放、`_held=False` 且 release 恰好执行一次 |
| P2：两个 DELETE scanner timeout 后池永久耗尽 | 已修复 | scanner 新增进程级后台 pool manager；失效 slot 通过事件立即触发补员，并以 0.1–5 秒有界 backoff 重试；spawn/readiness 全部位于非请求线程，部分 spawn 失败只回滚新 slot；shutdown 先停止 manager 再清理 worker | 两个预启动 slot 依次 hang 后后台补回 2 个 healthy slot，后续 scan 成功；部分 prestart 失败无残留 slot；manager 与 worker 在 stop 后全部退出 |
| Important：migration 类型/default 断言可误放行 | 已修复 | 按 SQLAlchemy `_type_affinity` 精确规范化类型；未知 Boolean/Numeric 不再伪装成 datetime；server default 去除外层括号、引号和 PostgreSQL cast 后做精确相等比较 | `Boolean → boolean`、`Float → numeric`；`unreserved` 与 `10` 不再满足 `reserved`/`0`；SQLite upgrade → downgrade → re-upgrade 严格契约通过，PostgreSQL 复用同一断言 |

---

## 2. 最终运行时不变量

### 2.1 启动租约与逐绑定收敛

- `RUNTIME_STARTUP_LEASE_TTL_SECONDS=30` 只保护 claim 到 WebSocket ready、runtime confirm 和 health projection 的启动阶段；ready 后仍使用 15 秒运行期 lease 与 5 秒 heartbeat。
- 一个 binding 的 secret、claim、ready、confirm、registry 或 health 异常不会再使 `asyncio.gather()` 撤销整个 startup。除任务取消外，异常会被当前 binding 的收敛函数吸收并尝试持久化 unhealthy。
- 失败启动仍先停止未发布 transport、释放匹配 token；若 stop/release 本身失败，既有 quiescing retry-owner 语义继续保留唯一 owner，不会允许 replacement 重叠。
- 其他已 ready binding 继续保留在同一个 Supervisor 与动态 registry 中；`app.state.feishu_supervisor` 不会因单行失败变成 `None`，cleanup janitor 也会正常启动。

### 2.2 cancellation-safe leader fence

- `asyncio.to_thread(FileLock.acquire)` 的线程不会因外层 task 取消而停止，因此外层不再直接遗弃该 future。
- 首次 cancellation 到达后，Supervisor 持续 shield/drain acquire task；重复 cancellation 也不会打断清理。
- acquire 最终 timeout 时不做 release；最终成功时先完整 drain 跨线程 release，再重新传播原始 cancellation。
- `_held` 只在未取消的正常 acquire 成功后置为 true，因此追踪状态与底层 OS lock 不再分叉。

### 2.3 DELETE scanner pool 活性与生命周期

- `scan()` 仍只尝试非阻塞取得已有 slot、发送 IPC 并等待 deadline；请求线程不会调用 `Process.start()`。
- timeout、IPC error、非法 response 或 dead worker 会终止当前 slot，并通知后台 manager 补足目标容量。
- manager 每秒健康检查一次；补员失败从 0.1 秒开始指数退避，最大 5 秒。spawn 与 ready wait 不持有请求侧 scanner lock，健康 slot 可继续服务。
- 一次 replenish 只发布全部 ready 的新 slot；部分失败回滚本批新进程，不销毁原有健康 peer。
- Gateway 原有 shutdown hook 调用全局 scanner `stop()`；stop 先通知并 join manager，再发送 worker stop，必要时 terminate/kill/join，避免后台补员与关停相互复活。

### 2.4 migration 严格 Gate

- String、Integer、DateTime 通过 SQLAlchemy type affinity 映射；Boolean、Numeric 等其他 affinity 保留真实名称，不能误通过 datetime 断言。
- SQLite 的 `'reserved'`/`0` 与 PostgreSQL 的 `'reserved'::character varying`/`0` 被规范化为相同语义值，但 `unreserved`、`10` 仍保持不同。
- `state` 必须精确等于 `("string", false, "reserved", 16)`，`writer_generation` 必须精确等于 `("integer", false, "0", null)`；其他 nullable、length、PK 和 index 契约保持严格比较。
- SQLite 与 PostgreSQL upgrade/re-upgrade 继续复用 `_assert_channel_ingest_contract()`，避免不同方言 Gate 漂移。

---

## 3. 主要代码变更

- `backend/app/channels/supervisor.py`
  - 新增独立 startup lease TTL。
  - 修复 `_FileRuntimeLeaderFence.acquire()` cancellation drain/release。
  - `load_active_bindings()` 增加逐 binding 异常隔离与 unhealthy 投影。
- `backend/app/channels/feishu.py`
  - scanner 拆出 `_replenish()` 与 `_maintain()`，新增失效通知、有界 backoff、部分 spawn 回滚和 manager shutdown。
- `backend/tests/test_feishu_supervisor.py`
  - 新增慢 ready、confirm 失败 peer 隔离和 fence cancellation 回归。
- `backend/tests/test_feishu_parser.py`
  - 新增双 slot 连续失败后自愈、部分 prestart 回滚和 manager shutdown 回归。
- `backend/tests/test_user_model_capabilities_migration.py`
  - 新增 type affinity/default normalization 单测并收紧共享 schema contract。
- `backend/README.md`、`backend/CLAUDE.md`
  - 记录 startup lease、逐 binding 收敛、cancellation-safe fence 与 scanner manager 运维不变量。

---

## 4. 自动化验证

### 4.1 TDD 专项

本轮先建立失败用例，再完成实现：

- 取消 fence waiter 后旧实现得到 `underlying acquired=true`、`_held=false`，修复后晚到锁必然释放。
- 两个 scanner slot 依次 timeout 后旧实现永久返回 fail closed，修复后 manager 补池并恢复 healthy scan。
- runtime confirm 专用 `BindingStartError` 原本逸出 startup gather；修复后失败 binding unhealthy，快 peer 与 janitor 均保留。
- 旧 migration helper 没有可验证 Boolean/Numeric 与精确 default 的规范化入口；新增严格 helper 与反例用例后，SQLite 完整迁移契约通过。

### 4.2 核心三文件

```text
105 passed, 1 skipped, 1 warning in 27.62s
```

覆盖：

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_user_model_capabilities_migration.py
```

### 4.3 Review 指定 7 文件聚合 Gate

```text
161 passed, 1 skipped, 1 warning in 50.28s
```

覆盖：

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_secret_store.py
tests/test_aio_sandbox_provider.py
tests/test_user_model_capabilities_migration.py
```

唯一 skip 是当前环境没有真实 PostgreSQL；warning 是 LangGraph `allowed_objects` pending deprecation，不是本轮功能失败。

### 4.4 Gateway shutdown 与 harness 边界

```text
2 passed, 1 warning in 11.77s
```

覆盖 `test_gateway_lifespan_shutdown.py` 和 `test_harness_boundary.py`。

### 4.5 静态、格式、编译与差异检查

```text
ruff check --no-cache <5 个本轮 Python 文件>: All checks passed!
ruff format --check --no-cache <5 个本轮 Python 文件>: 5 files already formatted
python -m compileall <本轮生产代码与测试>: passed
git diff --check -- backend docs: passed
```

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，执行共享的 upgrade/downgrade/re-upgrade 严格 schema Gate。
2. 使用两个真实 Feishu App 验证接近 ready deadline 的 WebSocket 启动、credential rotation、stop failure、进程重启和 attachment recovery。
3. 在生产同构环境确认 M3 v1 仍只运行一个 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 第十四轮没有重新执行此前 601 秒仍未完成的全量 backend suite；进入合并前仍需在可完成的 CI runner 取得完整 `pytest tests -q` / `make test` 汇总。

---

## 6. 最终判定

**第十四轮 Review 指出的 1 项 P1、2 项 P2 与 1 项 Standards Important 已在代码侧关闭，当前没有已知的第十四轮未修复 P1/P2。**

当前状态可以进入复审；但真实 PostgreSQL、双 Feishu App 与完整 backend suite 仍是合并/发布前 Gate，因此本报告不宣称最终生产发布完成。
