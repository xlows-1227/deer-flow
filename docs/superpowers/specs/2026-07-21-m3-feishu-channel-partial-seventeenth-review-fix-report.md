# 多租户 Agent 发布平台 - M3 飞书渠道部分第十七轮 Review 修复报告

**日期：** 2026-07-21

**关联 Review：** [2026-07-21-m3-feishu-channel-partial-code-seventeenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-seventeenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十七轮列出的 1 项 P1、2 项 P2、3 项 Standards Important 和 2 项 Standards Minor 已完成代码、测试与文档修复。第十七轮 M3 正式 5 文件 Gate 为 `152 passed`，parser 全文件连续三次均为 `54 passed`。当前未发现仍属于本轮 M3 范围的 P1/P2。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：detached failure projection 可持全局 lock 阻塞 peers，并迟到覆盖新 generation | 已修复 | health persistence 不再持跨 binding lifecycle lock；所有 health 写携带 expected `runtime_generation + runtime_lease_token`，repository 在行锁内 CAS；只有 CAS 成功才更新内存 health。runtime release 返回真实持久化后的 generation，避免 stop/delete 已递增 generation 时用本地 `+1` 推算 | 真实进入 `repository.update_health()` 后吞取消：旧实现 peer load 0.6 秒仍 pending，修复后 peer healthy、janitor 已创建；旧 unhealthy projection 在新 generation healthy 后恢复时，DB 与内存均保持 healthy；repository generation/token 两类 stale 写均返回 `None` |
| P2：scanner process API 抛错时遗忘 live child | 已修复 | `_terminate_slot()` 对 `is_alive/terminate/join/kill` 全部 best-effort，异常只将退出状态视为 unknown；统一 `_retain_unconfirmed_slots()` 保留 slot、设置 stop fence 与 maintenance stop。scan、dead-slot cleanup、partial spawn rollback、replenish 和 stop 均使用同一 ownership 语义 | `terminate`、`join`、`kill`、最终 `is_alive` 分别抛错的 4 条回归均保留 slot 并拒绝 restart；partial prestart 后 termination 抛错仍保留未发布 child，后续确认退出后才能清空 |
| P2：quiescing fencing owner 被误报为 running | 已修复 | 新增统一 `_is_serving()`：`channel.is_running and not quiescing`；`running_binding_ids` 和 `test_binding().running` 只报告 serving。新增 `owned_binding_ids` 专用于 fencing/cleanup ownership 等待 | non-cooperative start 与 non-cooperative stop 两条回归中，失败 binding 仍被 `owned_binding_ids` 跟踪，但从 running 列表排除，`test_binding().running=False`；healthy peer 仍为 running |
| Important：scanner 文档强于异常路径实现 | 已修复 | README/CLAUDE 明确 process API exception 为 exit-unknown；实现保证所有异常路径继续 best-effort 并仅以最终确认退出为成功 | scanner 5 条新回归、parser 全文件和 Gateway shutdown 相关 Gate 通过 |
| Important：完整 `make test` 未执行 | 已执行并记录范围外阻塞 | Windows 无 `make`，已执行 Makefile 等价主体 `uv run pytest tests/`；仓库共有 4,544 条测试。详细 `-x` 运行在第 319 条遇到本轮未修改 auth 的稳定既有失败 | `318 passed` 后 `tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path` 失败；该用例单独复跑仍失败。M3 focused Gate 另有 5 个前几轮已记录的 Windows LocalSandbox 环境失败 |
| Important：parser focused Gate 时间敏感不稳定 | 已修复 | discovery cursor 使用可注入逻辑 clock；quarantine 测试通过职责 API 确定性填满 8 个 slot；真实 deadline 测试将 100ms 观察窗扩大为 500ms 并保留启动 event | parser 全文件连续三次：`54 passed`、`54 passed`、`54 passed` |
| Minor：convergence 五个布尔参数编码入口策略 | 已修复 | `_StartupPolicy` 将显式操作与 startup reload 建模为具名策略；`_converge_startup()` 不再暴露五个可任意组合的 bool | explicit start/restart、reload isolation、not-found 与 strict failure 全文件回归通过 |
| Minor：quiescing task ownership 状态转换分散 | 已修复 | `_enter_quiescing()`、`_drain_runtime_task()`、`_ensure_cleanup_retry()` 统一 serving→fence owner、cancel/drain/clear 与单 cleanup owner 迁移 | non-cooperative start/stop、stop failure、delete tombstone、renewal failure 与 cleanup retry 回归通过 |

---

## 2. 最终运行时不变量

### 2.1 Health projection 隔离与 CAS

- `_repository_lifecycle_lock` 只协调 runtime claim/final re-read，不包裹可被 deadline detach 的 health repository 写。
- `_record_health()` 从产生 health 的 durable row/fence 获取 expected `runtime_generation` 与 `runtime_lease_token`。
- `AgentChannelRepository.update_health()` 在 owner-scoped row lock 内同时校验 generation 与 token；任一不匹配均不修改数据库。
- 内存 `_health` 只在 durable CAS 成功后更新；stale CAS 抛出内部 `_StaleHealthProjectionError`，detached callback只消费结果，不写内存 fallback。
- startup attempt 显式捕获 claim、confirm 和 release 后的实际 generation/token。`release_runtime()` 返回提交后的 row，而不是让 Supervisor 猜测 generation 增量。
- 一个 cancellation-resistant projection 即使永久存在，也不能持跨 binding 应用锁；peer startup、peer health 和 janitor 创建不再依赖它退出。

### 2.2 Ownership 与 serving 分离

- `_running` 仍是 process-local fencing owner registry，包含 retained quiescing generation。
- `owned_binding_ids` 反映该 registry，用于 cleanup convergence、测试 teardown 和 replacement fencing。
- `running_binding_ids` 只返回 transport 已 running 且 `quiescing=False` 的 binding。
- `test_binding()` 的 credential health 与 transport service 状态分离；credential 可 healthy，但 quiescing owner 必须返回 `running=False`。
- 所有进入 stop/retry 的路径先调用 `_enter_quiescing()`，因此对外 running 会立即撤销，而 token/owner 在退出确认前继续保留。

### 2.3 Scanner 异常路径 ownership

- `_process_liveness()` 返回 `True/False/None`；任何 `is_alive()` 异常均为 unknown，不等价于 exited。
- `_terminate_slot()` 即使某一步抛错，也继续执行后续 terminate/join/kill/final liveness 步骤。
- 只有最终明确 `is_alive() is False` 才允许遗忘 slot。
- `_retain_unconfirmed_slots()` 是 scan、replenish rollback、partial spawn 和 stop 的统一失败出口；它保留 slot、设置 `_stopping=True` 并保持 maintenance stop event。
- restart 仅在后续 lifecycle check 明确确认旧 child 已退出后才允许清理旧 slot并创建新 generation。

### 2.4 确定性 parser Gate

- discovery cursor 测试通过注入的逻辑 clock 推进 deadline，不再依赖 `sleep(10ms)` 与 45ms wall-clock 预算。
- quarantine cap 测试先通过 `_read_cleanup_job_with_deadline()` 确定性建立 8 个 hung reader，再验证 isolated fallback 能到达正常 job。
- remote delete 总预算测试仍验证真实 deadline，但以 event 确认 delete thread 已开始，并使用适合受载 Windows runner 的 500ms/1.2s 观察预算。
- supervisor 测试模块显式停止自己启动的 process-owned scanner，避免 pytest 结束时依赖 multiprocessing interpreter finalizer而无汇总挂起。

---

## 3. 主要代码变更

- `backend/app/channels/supervisor.py`
  - health projection 与跨 binding lifecycle lock 分离。
  - 新增 runtime generation/token CAS 参数、stale projection 拒绝和真实 release generation 传播。
  - 新增 `_StartupPolicy` / `_StartupAttempt`。
  - 新增 `_is_serving()` 与 `owned_binding_ids`，拆分 serving 和 fencing ownership。
  - 新增统一 quiescing/task-drain/cleanup-retry transition helpers。
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - `update_health()` 增加 expected runtime generation/token CAS。
  - `release_runtime()` 返回提交后的 durable row，供后续 health fencing 使用。
- `backend/app/channels/feishu.py`
  - scanner process API 全部异常安全。
  - 新增统一 unconfirmed slot retention/stop fence。
  - `_scan_all_cleanup_jobs()` 支持测试注入逻辑 clock。
- `backend/tests/test_feishu_supervisor.py`
  - 新增持锁 projection 吞取消、stale generation、serving/ownership 回归。
  - quiescing 测试改用 serving 与 ownership 两套明确断言。
  - module teardown 显式停止 process-owned scanner；renewal tests join lease task。
- `backend/tests/test_feishu_parser.py`
  - 新增四类 process API exception 与 replenish rollback ownership 回归。
  - 三个 wall-clock-sensitive 用例改为逻辑 clock、职责状态或扩大真实观察窗。
- `backend/tests/test_agent_channel_repo.py`
  - 新增 generation/token health CAS repository 回归。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步 health CAS、serving/ownership、scanner exception ownership 与 deterministic Gate。

---

## 4. 自动化验证

### 4.1 TDD 红绿证据

```text
P1 projection/CAS 红测：3 failed
P1 projection/CAS 绿测：3 passed

Scanner exception/replenish 红测：5 failed
Scanner exception/replenish 绿测：5 passed

Quiescing serving-state 红测：2 failed
Quiescing serving-state 绿测：2 passed
```

### 4.2 Supervisor + repository

```text
59 passed, 1 warning in 27.28s
```

### 4.3 Parser 稳定性

```text
run 1: 54 passed in 9.84s
run 2: 54 passed in 9.17s
run 3: 54 passed in 8.75s
```

### 4.4 第十七轮正式 5 文件 Gate

```text
152 passed, 5 warnings in 53.92s
```

覆盖：

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py
```

warnings 均为 LangGraph/Lark/WebSocket 第三方 deprecation；最终运行不再出现 SQLAlchemy connection GC 或 aiosqlite thread exception warning。

### 4.5 M3 focused regression

```text
425 passed, 9 skipped, 5 failed in 141.32s
```

5 个失败与前几轮报告一致，均为当前 Windows runner 环境项：

1. 4 个 LocalSandbox Windows host path reverse mapping/roundtrip 断言；
2. 1 个测试强制使用本机不存在的 `/bin/sh`。

本轮修改涉及的 supervisor、parser、repository、router、Gateway lifecycle、secret、sandbox ownership 和 migration 相关用例均通过。9 个 skip 包含真实 PostgreSQL/环境条件用例。

### 4.6 完整 backend suite

Windows 环境没有 `make` 可执行文件，已执行 Makefile `test` target 的等价 pytest 主体。仓库当前收集 `4544 items`；详细 fail-fast 运行结果：

```text
318 passed, 1 failed, 7 warnings in 87.21s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

该 auth 用例不在本轮修改范围，且单独复跑仍稳定失败：

```text
1 failed in 8.06s
```

因此“第十七轮未执行完整 suite”的 Standards finding 已关闭，但仓库级全绿 Gate 仍被一个范围外既有 auth 失败阻塞；本报告不把它误报为通过。

### 4.7 静态、格式、编译与差异检查

```text
ruff check --no-cache <6 个本轮 Python 文件>: All checks passed!
ruff format --check --no-cache <同 6 个文件>: 6 files already formatted
python -m compileall <3 个本轮生产 Python 文件>: passed
git diff --check: passed（仅提示无关 config.yaml 后续可能发生 CRLF→LF）
```

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，执行 upgrade/downgrade/re-upgrade 以及 health generation/token CAS 并发验证。
2. 使用两个真实 Feishu App 验证 near-deadline ready、non-cooperative/failed stop、credential rotation、进程重启和 attachment recovery。
3. 在生产同构环境确认 M3 v1 只运行一个 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 仓库级完整 suite 需由 auth 所有者修复 `test_csrf_does_not_exempt_old_login_path` 后重新全量执行；Windows LocalSandbox 5 项需在对应平台契约下单独处理。

---

## 6. 最终判定

**第十七轮 Review 指出的 1 项 P1、2 项 P2、3 项 Standards Important 和 2 项 Standards Minor 已在代码、正式测试和文档侧完成修复；当前没有已知的第十七轮 M3 范围内未修复 P1/P2。**

当前状态可以进入第十八轮复审。由于真实 PostgreSQL、双 Feishu App、范围外 auth 全量失败以及 Windows LocalSandbox 平台项仍未关闭，本报告不宣称整个仓库已经满足最终生产发布/全量 merge Gate。
