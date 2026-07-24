# 多租户 Agent 发布平台 — M3 第十三轮 Review 修复报告

**日期：** 2026-07-20

**关联 Review：** [2026-07-20-m3-feishu-channel-partial-code-thirteenth-review.md](./2026-07-20-m3-feishu-channel-partial-code-thirteenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十三轮列出的 2 项 P1、1 项 P2、2 项 Standards Important 已完成代码修复和本地自动化回归。Review 指定的 7 文件聚合集已由上一轮的失败/挂起恢复为 `145 passed, 1 skipped`。真实 PostgreSQL、真实双 Feishu App 和全量 backend 仍属于部署/环境 Gate。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：Gateway 硬退出后 runtime token 永久阻塞 | 已修复 | 按开发计划的 v1 单实例约束新增 OS leader fence。Supervisor 只有取得 `${DEER_FLOW_HOME}/published-feishu-supervisor.lock` 后才能执行启动恢复；新 leader 取得锁即证明旧进程已退出，此时才以 system scope 清理 crash-orphaned token 并加载 active binding。普通 lease expiry 仍不授权接管 | 真实子进程持有 fence + 数据库残留 token；活进程期间新 Supervisor 被拒绝，hard terminate 后新 Supervisor 成功恢复并只启动一个 runtime |
| P1：stop 先清 `is_running` 后失败会丢失唯一 retry owner | 已修复 | `_RunningChannel` 分离 ready 与 `quiescing`/confirmed-exit 状态。stop/release 未确认时不再 pop entry；heartbeat owner 转为唯一 cleanup retry task，所有 start/restart replacement 返回 cleanup pending。只有 transport stop 返回且 durable release 完成后才移除 generation | fake 按真实顺序先令 `is_running=False` 再抛错；后续 start 未创建第二实例，原 retry owner 保留 token，stop 成功后才 release，随后可正常新建一个 runtime |
| P2：DELETE 的 glob 与 `Process.start()` 仍在可终止边界外 | 已修复 | Gateway 请求 admission 前预启动两个 disposable scanner。目录枚举、stat、大小检查、读取和 JSON 解析作为一个 worker operation 执行；DELETE 只做 IPC deadline、terminate/kill/join，请求内不再 spawn 或扫描文件系统。首个 worker 卡死后被回收，standby 继续服务后续扫描 | 未启动 scanner 时 DELETE fail closed 且不会调用 `Process.start()`；whole-scan hang 到 deadline 后 worker 被 terminate，第二个预启动 worker 的健康扫描成功 |
| Important：migration Gate 只比较名称 | 已修复 | `_channel_ingest_schema()` 现在结构化读取 columns、PK 和 indexes；SQLite/PostgreSQL upgrade/re-upgrade 均验证类型/长度、nullable/default、`secret_ref` PK、due index 列顺序及所有索引 `unique=False`，并验证 `runtime_stop_requested` 的 false server default | SQLite upgrade → downgrade → re-upgrade 精确契约通过；PostgreSQL 使用同一断言，当前环境无 PostgreSQL 因而按 Gate 规则 skip |
| Important：AIO cancellation/adoption 测试以 1 秒作正确性判定并泄漏 worker | 已修复 | 以 create-published 状态和 provider B adoption 结果作为确定性同步；5 秒仅作为外层死锁保险。`release_create`、pending task cancel/drain 和 late cleanup drain 全部进入 `finally` | 单项回归通过；7 文件聚合不再出现 300 秒 executor join hang |

---

## 2. 最终运行时不变量

### 2.1 单实例 crash recovery authority

- M3 v1 只支持一个 Gateway 进程运行 Published Feishu Supervisor，这是开发计划中已明确的第一版部署约束。
- Supervisor 在 `load_active_bindings()` 的任何恢复动作之前取得进程级文件锁；第二个活进程不能进入 runtime recovery，也不能清理第一个进程的 token。
- runtime timestamp 只用于 heartbeat 诊断，expiry 本身仍不能证明 transport 已退出，也不能授权 replacement、STOP 或 DELETE。
- 进程被 hard kill 后 OS 自动释放 fence；新进程取得 fence 后，才允许通过 system-only repository API 清理旧进程遗留的 runtime token。
- graceful shutdown 只有在所有本地 runtime 都确认退出后才主动释放 fence；若 stop 失败，fence 保留到进程实际退出，避免新旧 transport 重叠。
- 从没有 leader fence 的旧版本滚动升级时，必须先停止所有旧 Gateway，再启动本版本。

### 2.2 Stop/release ownership

- `channel.is_running` 只表示 ready/running 可见状态，不再被当作底层 SDK session/thread 已退出的证据。
- 任一 generation 一旦进入 stop，就标记为 `quiescing` 并从动态发送 registry 注销；replacement 一律返回 retryable cleanup pending。
- stop 抛错、stop 超时或 durable release 异常都会保留同一个 `_RunningChannel` 和 token，并确保至多一个 cleanup retry task。
- 只有 `channel.stop()` 返回、matching token release 不再处于未知状态后，Supervisor 才会移除本地 generation。

### 2.3 DELETE scan liveness

- 两个 scanner 都在 Supervisor 构造/请求 admission 前完成 spawn 和 ready handshake；`scan()` 本身不会调用 `Process.start()`。
- `exists/glob/stat/read/parse` 全部位于 scanner 子进程；Gateway 请求线程只传入 base path 和 binding ID，并等待带 request ID 的结果。
- 2 秒无响应即 fail closed，关闭 IPC 并 terminate/kill/join 当前 worker；不会留下持有全局扫描锁的不可终止线程。
- 预启动 standby 允许一个永久挂起 worker 被回收后，下一次健康扫描仍能成功；后续 Supervisor 初始化会补足失效 worker。

### 2.4 Migration 与 AIO 测试 Gate

- ingest schema Gate 不再使用 column/index 名称集合替代契约；主键、类型、长度、nullable、server default、索引顺序和 unique 属性均被验证。
- SQLite 与 PostgreSQL 共享同一规范化 ingest schema 断言，且 downgrade/re-upgrade 后必须与首次 upgrade 完全相等。
- AIO cancellation 测试的阻塞 backend worker 无论断言、timeout 或 cancellation 如何结束都会在 `finally` 中释放，测试失败不会退化为 suite hang。

---

## 3. 主要代码变更

- `backend/app/channels/supervisor.py`
  - 新增 `_FileRuntimeLeaderFence`、leader acquire/release 生命周期和 startup orphan-token recovery。
  - `_RunningChannel` 新增 `quiescing` 与独立 `cleanup_task`；统一 stop retry owner 的建立与 confirmed-exit 移除。
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - 新增 system-scope-only `recover_orphaned_runtime_leases()`，清理 token 时推进 runtime generation。
- `backend/app/channels/feishu.py`
  - 新增预启动双 worker backlog scanner；whole-scan 操作完全位于可 kill 子进程。
- `backend/app/gateway/app.py`
  - Gateway shutdown 有界停止全局 attachment backlog scanner。
- `backend/tests/test_feishu_supervisor.py`
  - 新增真实 hard-kill/fence recovery 和真实 stop 顺序的唯一 retry-owner 回归。
- `backend/tests/test_feishu_parser.py`
  - 新增请求内禁止 worker start、whole-scan hang 回收与 standby 后续成功回归。
- `backend/tests/test_user_model_capabilities_migration.py`
  - 新增 SQLite/PostgreSQL 完整 schema contract 与 re-upgrade 等价断言。
- `backend/tests/test_aio_sandbox_provider.py`
  - 修正 cancellation/adoption 测试的确定性同步和无条件资源释放。
- `backend/README.md`、`backend/CLAUDE.md`
  - 记录 v1 单实例部署、leader fence/升级要求、quiescing ownership 和 DELETE 预启动扫描池。

---

## 4. 自动化验证

### 4.1 TDD 专项

本轮新增用例先证明旧行为失败，再完成实现：

- stop 已令 `is_running=False` 后抛错：旧实现会丢 entry 并尝试新 claim；修复后保留唯一 cleanup owner。
- stale token + 活进程 fence：第二个 Supervisor 被拒绝；真实 hard kill 后 fence 自动释放并恢复 active binding。
- DELETE scanner 未预启动：fail closed，且请求路径不会尝试 `Process.start()`。
- whole-scan 永久挂起：deadline 后 worker 被终止，standby 的下一次扫描成功。
- migration 精确 schema 与 AIO finally cleanup 均已加入直接回归。

### 4.2 Review 指定 7 文件聚合 Gate

```text
145 passed, 1 skipped, 2 warnings in 58.15s
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

唯一 skip 是当前环境没有真实 PostgreSQL。warnings 为 LangGraph pending deprecation 与本地 pytest cache 权限提示，不是功能失败。

### 4.3 受影响核心模块聚合

在修正 worker readiness 和跨线程 file-lock release 后，repository、Supervisor、parser、AIO 和 migration 的顺序聚合结果：

```text
131 passed, 1 skipped, 2 warnings in 46.23s
```

### 4.4 静态、格式、编译与差异检查

```text
ruff check --no-cache <17 related Python targets>: All checks passed!
ruff format --check <17 related Python targets>: passed after formatting 2 files
python -m compileall app packages/harness/deerflow <7 review test files>: passed
git diff --check -- backend docs: passed
```

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 中设置 `REQUIRE_POSTGRES_TESTS=1`，执行 upgrade/downgrade/re-upgrade 精确 schema Gate。
2. 使用两个真实 Feishu App 验证 WebSocket ready、credential rotation、stop failure、进程重启和 attachment recovery。
3. 在生产同构环境确认 Published Feishu Supervisor 使用单 Gateway 进程，并在从旧版本升级时先停止所有旧 Gateway。
4. 在 Linux CI 或修复既存 Windows LocalSandbox 基线后执行完整 M3 focused Gate。
5. 在可完成的 runner 上取得全量 backend `pytest tests -q` / `make test` 最终汇总。

---

## 6. 最终判定

**第十三轮 Review 指出的 2 项 P1、1 项 P2 和 2 项 Standards Important 已在代码侧关闭，当前没有已知的第十三轮未修复 P1/P2。**

Review 中失败并额外挂起约 300 秒的 7 文件聚合集已经全绿（PostgreSQL 环境项除外）。M3 可以进入真实 PostgreSQL、真实 Feishu 和完整仓库 Gate；在这些外部 Gate 完成前，本报告不宣称最终生产发布完成。
