# 多租户 Agent 发布平台 - M3 飞书渠道部分第十五轮 Review 修复报告

**日期：** 2026-07-21

**关联 Review：** [2026-07-21-m3-feishu-channel-partial-code-fifteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-fifteenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十五轮列出的 2 项 P1、1 项 P2、1 项 Standards Important 和 1 项 Standards Minor 已完成代码修复、文档同步及本地自动化回归。评审指定 7 文件聚合结果为 `164 passed, 1 skipped`；真实 PostgreSQL、真实双 Feishu App 和全量 backend 仍属于外部/完整 Gate。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1-1：首轮 binding DB 重读可撤销整个 Supervisor 管理入口 | 已修复 | 将“初始重读 → 状态检查 → 启动 → 失败健康投影”纳入同一个逐 binding 异常与 deadline 边界；除 cancellation 外，单行读取或启动失败只收敛当前 binding | 首个 binding 的 `get_for_supervisor()` 强制抛错后 `load_active_bindings()` 正常返回，第二个 binding 仍 healthy/running，失败 binding 为 unhealthy，cleanup janitor 已启动 |
| P1-2：固定 startup lease 不是完整启动上界，无 deadline cleanup 投影可挂死 startup | 已修复 | provisional token 在 transport ready 前持续续期；正常逐 binding convergence 具有 25 秒 deadline，超时后的 unhealthy 投影另有 2 秒上限；真实 Feishu startup cleanup-health 改用预启动、可 kill、2 秒请求预算的 scanner，不再直接执行无上界 binding-index 文件系统投影 | `0.1s` startup TTL 下模拟慢 projection + 慢 ready，总时长超过 TTL 仍成功 confirm；永久阻塞旧 binding-index 投影不再阻止 WebSocket ready；永久阻塞单 binding start 超时收敛，peer 与 janitor 不受影响 |
| P2：scanner stop 可在 manager/unpublished child 仍存活时返回 | 已修复 | readiness 以 50ms stop-aware 分片等待；stop 唤醒并在明确 shutdown budget 内 join manager，成功返回前清理全部 published/unpublished child，manager 未退出则抛错；旧 manager 活着且处于 stopping 时禁止新 lifecycle 清除 stop fence | manager 正阻塞于新 worker readiness 时调用 stop，耗时小于 0.75 秒，manager 与所有 fake child 均退出；同一 scanner 实例随后可重新 start/stop，旧/新 lifecycle 无重叠 |
| Important：scanner 行为、文档与关键 teardown 测试不一致 | 已修复 | README/CLAUDE 明确 startup projection、stop-aware readiness、成功 shutdown 的退出保证及失败显式上报；新增 blocked replenish teardown + same-process restart 回归 | scanner teardown 回归及 Gateway lifespan shutdown 测试通过；文档描述与实际成功/失败语义一致 |
| Minor：scanner 测试重复 process/connection fake | 已修复 | 抽取可配置 `_ScannerFakeProcess`、`_ScannerFakeConnection`、`_ScannerFakeChildConnection`、`_ScannerFakeContext`，统一 ready、hang、spawn failure、terminate/kill/join/close 语义 | 相邻 hung scan、双 slot 补员、部分 prestart 回滚和 blocked readiness teardown 共用同一套 fake；parser 全文件回归通过 |

---

## 2. 最终运行时不变量

### 2.1 完整逐 binding startup convergence

- `load_active_bindings()` 的每个 row 在独立 lifecycle lock 中执行；首轮 `_binding()` 重读本身也位于逐行 `try` 和 timeout 内，不再成为 `asyncio.gather()` 的遗漏异常出口。
- 正常路径从初始重读、secret decode、runtime claim、cleanup-health projection、WebSocket ready、runtime confirm 到最终 health projection，共用 25 秒启动 deadline。
- deadline 到达后，取消未发布 transport、释放匹配 fencing token，并用独立的 2 秒预算尝试持久化 unhealthy；若该投影本身失败，仍在 Supervisor 内存 health 中保留 fail-closed 状态，不无限等待数据库。
- 除 `CancelledError` 外，一个 binding 的读取、claim、transport、confirm 或 health 失败只影响当前 binding；其他 ready peer 继续受同一 Supervisor 管理，所有 startup row 收敛后 cleanup janitor 仍会启动。

### 2.2 provisional lease 与可终止 startup projection

- claim 后立即并行运行 transport start 与 provisional lease renewal；续期间隔不超过 startup TTL 的三分之一，默认仍受 steady heartbeat 上限约束。
- transport ready 前的任一次续期异常、返回 false 或 token revocation 都 fail closed：取消 start、停止未发布 transport并释放当前 generation，而不是等待最终 confirm 才发现过期。
- ready 后仍由 `confirm_runtime()` 原子校验 token、expiry、desired state 和 deletion fence；确认成功后才注册动态 channel 并切换到 steady-state lease monitor。
- Dynamic Feishu startup 不再直接调用 `_binding_cleanup_index_has_backlog()`。它通过 Gateway 已预启动的 scanner 做完整目录/索引扫描；scanner 不可用、超时、非法响应或无空闲 worker 时统一返回 backlog=true，使 binding 启动但保持 cleanup unhealthy，等待后台恢复。
- 即便旧的直接 binding-index 投影永久阻塞，也不会占用 startup request admission 或阻止 WebSocket ready。

### 2.3 scanner shutdown 与同进程重启

- background manager 的 worker readiness wait 每次最多阻塞 50ms，然后重新检查 shutdown event；stop 可以唤醒正在 replenishing 的 manager。
- replenish 期间创建但尚未发布的 children 由 manager 在失败/停止 unwind 中统一 terminate/kill/join；已发布 slots 在 manager 退出后收到 stop，未退出者同样强制终止。
- `stop()` 只有在 manager 已退出并清空 worker pool 时正常返回；超过 manager shutdown budget 会抛出 `RuntimeError`，不会把仍存活 manager 错报为已关闭。
- 同一实例只有在上一生命周期成功停止后才能清除 stop event；若旧 manager 仍 alive 且 scanner 处于 stopping，`start()` 明确拒绝，避免两个 manager/child generation 交叉。

### 2.4 测试与文档单一语义

- scanner fake 的 process 生命周期、pipe readiness、scan response、close、terminate、kill 和 join 语义集中在共享 helper，所有相邻 scanner 回归使用同一实现。
- `backend/README.md` 与 `backend/CLAUDE.md` 明确记录 25 秒正常 startup deadline、2 秒失败健康投影、provisional renewal、scanner-backed startup projection，以及成功 scanner shutdown 的 manager/child 退出保证。

---

## 3. 主要代码变更

- `backend/app/channels/supervisor.py`
  - 新增逐 binding startup convergence deadline 与失败健康投影 deadline。
  - 将 initial DB re-read 纳入 `load_active_bindings()` 的逐行隔离。
  - 新增 provisional lease renewal 与 start/renewal 竞争收敛。
- `backend/app/channels/feishu.py`
  - startup cleanup-health 切换到 deadline-bound scanner。
  - scanner readiness 改为 stop-aware 分片等待；stop 验证 manager 退出并清理 in-flight child；start 增加跨生命周期保护。
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - 明确 `renew_runtime()` 同时服务 active provisional/confirmed runtime claim 的文档语义。
- `backend/tests/test_feishu_supervisor.py`
  - 新增 initial repository read 隔离、跨 startup TTL 续期和永久阻塞单 binding deadline 回归；SQLite fixture 改为文件数据库以覆盖并发 renewal session。
- `backend/tests/test_feishu_websocket_lifecycle.py`
  - 新增永久阻塞旧 binding-index projection 不影响 ready 的真实 `FeishuChannel.start()` 回归。
- `backend/tests/test_feishu_parser.py`
  - 抽取共享 scanner fakes；新增 blocked readiness stop/drain 与同实例 restart 回归。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步 startup 与 scanner 的真实 deadline、fail-closed 和 shutdown 不变量。

---

## 4. 自动化验证

### 4.1 TDD 专项

本轮先复现评审边界，再完成实现：

- initial repository read 旧实现直接向上抛出 `RuntimeError`；修复后只标记对应 binding unhealthy，peer 与 janitor 保持可用。
- `0.1s` startup TTL 下，慢 projection + 慢 ready 的总耗时超过 token TTL；修复后 provisional renewal 保持 fencing ownership并成功 confirm。
- 永久阻塞 startup 旧实现无法完成 load；修复后逐 binding deadline 取消并清理失败 runtime，另一个 binding 正常启动。
- 旧的直接 binding-index projection 会阻止 WebSocket session start；修复后 startup 只使用 bounded scanner，阻塞函数不再位于启动路径。
- scanner manager 阻塞于 readiness 时，旧实现约 1 秒返回但 manager 仍 alive；修复后 stop-aware wait 在 0.75 秒内完成 manager 与全部 child drain，并可在同实例重新启动。

### 4.2 Supervisor 全文件

```text
43 passed, 1 warning in 19.95s
```

### 4.3 Feishu parser 与 WebSocket lifecycle

```text
54 passed, 5 warnings in 27.70s
```

永久阻塞 projection 最终单测复跑：

```text
1 passed, 6 deselected, 5 warnings in 18.52s
```

### 4.4 Review 指定 7 文件聚合 Gate

```text
164 passed, 1 skipped, 1 warning in 70.59s
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

### 4.5 Gateway shutdown 与 harness 边界

```text
2 passed, 1 warning in 10.97s
```

覆盖 `test_gateway_lifespan_shutdown.py` 和 `test_harness_boundary.py`。

### 4.6 静态、格式、编译与差异检查

```text
ruff check --no-cache <6 个本轮 Python 文件>: All checks passed!
ruff format --check --no-cache <6 个本轮 Python 文件>: 6 files already formatted
python -m compileall <本轮生产代码与测试>: passed
git diff --check -- backend docs: passed
```

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，执行共享的 upgrade/downgrade/re-upgrade 严格 schema Gate。
2. 使用两个真实 Feishu App 验证接近 startup deadline 的 WebSocket ready、provisional renewal、credential rotation、stop failure、进程重启和 attachment recovery。
3. 在生产同构环境确认 M3 v1 仍只运行一个 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 本轮未重新执行此前长时间未完成的全量 backend suite；进入合并前仍需在可完成的 CI runner 取得完整 `pytest tests -q` / `make test` 汇总。

---

## 6. 最终判定

**第十五轮 Review 指出的 2 项 P1、1 项 P2、1 项 Standards Important 和 1 项 Standards Minor 已在代码、测试和文档侧关闭；当前没有已知的第十五轮未修复 P1/P2。**

当前状态可以进入下一轮复审；但真实 PostgreSQL、双 Feishu App 与完整 backend suite 仍是合并/发布前 Gate，因此本报告不宣称最终生产发布完成。
