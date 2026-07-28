# 多租户 Agent 发布平台 - M3 飞书渠道部分第十六轮 Review 修复报告

**日期：** 2026-07-21

**关联 Review：** [2026-07-21-m3-feishu-channel-partial-code-sixteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-sixteenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十六轮列出的 1 项 P1、1 项 P2、1 项 Standards Important 和 1 项 Standards Minor 已完成代码修复、正式红绿回归、文档同步及本地自动化验证。第十六轮正式相关 5 文件结果为 `104 passed`；上一轮 7 文件广覆盖 Gate 拆分结果合计 `169 passed, 1 skipped`。真实 PostgreSQL、真实双 Feishu App 和全量 backend 仍属于外部/完整 Gate。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：startup deadline 可被无上界 start-task drain / `channel.stop()` 绕过 | 已修复 | `_RunningChannel` 显式跟踪 start、provisional renewal、stop 和 release task；timeout teardown 共用一个 2 秒绝对 deadline。未确认退出的 task 不再被当前 startup 无界 await，而是保留 token 并转入 quiescing cleanup retry；failure health projection也改为显式 task deadline | 双 binding 下分别令 start、stop、unhealthy projection永久吞取消；三条 load 均有界返回，失败 binding unhealthy、peer healthy、janitor 已启动；transport未退出时 DB token保留且 replacement被拒绝 |
| P2：scanner terminate/kill 后不复核 child 退出，成功 stop 会遗忘 live worker | 已修复 | `_terminate_slot()` 最后再次检查 `is_alive()` 并返回确认结果；stop、scan timeout、IPC/response failure、replenish rollback 和 partial spawn 均保留未退出 slot。stubborn child 使 stop 显式失败、保持 stop fence并拒绝 restart | terminate 无效但 kill 有效时 stop 正常返回且 child 已死；terminate/kill 均无效时 stop 抛错，slot 仍 tracked、restart 被拒绝，后续确认 child 退出后才能清空 |
| Important：scanner 文档保证强于实现和测试 | 已修复 | README/CLAUDE 与实现统一为“最终 liveness check 后才算成功”；补充 kill fallback 与 stubborn child 两个确定性回归 | scanner 专项、parser 全文件以及 Gateway shutdown Gate 均通过；文档不再把发出 kill 信号描述为已退出 |
| Minor：startup convergence timeout/失败投影双份实现 | 已修复 | 提取 `_converge_startup()`；显式 start/restart 与 startup reload 只传入 strict、reread、isolate 和 not-found 策略，共用同一 deadline、timeout、日志与 failure projection | initial DB read、hung start、non-cooperative start/stop、slow lease、explicit start 及完整 Supervisor 45 项均通过 |

---

## 2. 最终运行时不变量

### 2.1 有界 startup teardown 与 fencing ownership

- 正常 startup convergence 仍以 25 秒为上界；deadline 到达后，start、provisional renewal、transport stop 和 release task 使用同一个 2 秒 teardown deadline，而不是每个阶段重新获得一份无界等待。
- task cancellation 使用 `cancel + asyncio.wait(deadline)`；不会使用可能等待被取消协程自行结束的无界 `gather()` / `wait_for()` 作为退出证明。
- 若 start task 不响应取消，Supervisor 保留该 task、`_RunningChannel` generation 和数据库 lease token，不会调用可能被晚到 start 反向复活的 replacement transport。
- 若 stop task 不响应取消，Supervisor 保留同一个 stop task供后台重试，不会重复创建并发 stop，也不会释放 fencing token或从 `_running` 中移除 generation。
- runtime claim 成功后立即建立 process-local `_RunningChannel` owner，再执行最终 row re-read；因此 deletion 在 claim 后提交时，异常清理仍有可跟踪 owner，不会遗漏 token release。
- transport exit 确认后，release 写入也按 deadline 跟踪；未完成 release task由同一个 quiescing owner继续收敛。
- background cleanup 使用短周期、有界 stop/release 尝试；成功后才移除 generation。其间 `start_binding()` 只能得到 cleanup-pending，无法重叠创建 replacement。
- unhealthy failure projection 使用独立 task和硬 `asyncio.wait` deadline；投影不响应取消时立即采用内存 fallback health，迟到 task仅后台收尾，不会阻止 peer gather或 janitor创建。

### 2.2 单一 startup convergence 策略

- `_converge_startup()` 是 initial re-read、desired-state 检查、25 秒 timeout、超时日志、unhealthy projection 和 strict/not-found 策略的唯一实现。
- `_start_row()` 用于显式生命周期操作，保留原有 strict/domain exception 行为。
- `load_active_bindings()` 只负责逐 binding lifecycle lock，并以 `reread=True`、`isolate_failures=True`、`skip_not_found=True` 调用共享 helper。
- 除外层 `CancelledError` 外，一个 row 的读取、启动、确认或 cleanup failure 不会逃逸 startup gather；所有 row 收敛后 cleanup janitor必然获得创建机会。

### 2.3 scanner child 退出确认与 stop fence

- `_terminate_slot()` 执行 close、terminate、join、必要时 kill、再次 join，最后以 `not process.is_alive()` 作为唯一成功依据。
- terminate 无效但 kill 有效属于正常 fallback；kill 后仍 alive 属于明确 shutdown failure。
- published slot、scan 失败 slot、replenish 尚未发布 slot，以及 `Process.start()` 异常后实际存活的 child，都进入相同的退出确认和 tracking 语义。
- stubborn slot 保留在 scanner ownership 中，`_stopping=True` 且 maintenance stop event保持设置；request scan fail closed，Gateway scanner stop 抛错，同进程 restart被拒绝。
- child 后续真正退出后，下一次受锁保护的 lifecycle 检查才允许清理旧 slot并开启新 generation。

---

## 3. 主要代码变更

- `backend/app/channels/supervisor.py`
  - 新增共享 `_converge_startup()`，删除 load 内重复 timeout/failure 展开。
  - `_RunningChannel` 增加 start、startup lease、stop、release task tracking。
  - 新增有界 task cancellation、transport stop 和 runtime release helpers。
  - startup discard 使用单个绝对 teardown deadline；超时 task转入 quiescing cleanup retry。
  - runtime claim 后立即注册本地 fencing owner，关闭 claim→final re-read 的 tracking 空窗。
- `backend/app/channels/feishu.py`
  - `_terminate_slot()` 返回最终退出确认。
  - scan/replenish/partial spawn/stop 全路径保留 stubborn child tracking。
  - scanner 在未退出 child 存在时 fail closed并阻止同进程 restart。
- `backend/tests/test_feishu_supervisor.py`
  - 新增 non-cooperative start 和 permanent startup stop 双 binding 回归。
- `backend/tests/test_feishu_parser.py`
  - 扩展共享 fake，使 terminate/kill 是否生效可独立配置。
  - 新增 kill fallback success 与 stubborn child failure/restart fence 回归。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步共享 convergence helper、有界 quiescing cleanup、task ownership 和 scanner final liveness check。

---

## 4. 自动化验证

### 4.1 TDD 红绿证据

本轮把 Review 中的临时诊断转为正式回归：

- non-cooperative start 红测：旧实现 1 秒后 `load_active_bindings()` 仍 pending于 start-task drain；修复后有界返回并保留 token/retry owner。
- non-cooperative stop 红测：旧实现 1 秒后 load仍 pending于 `_discard_unpublished_runtime()` 的 `channel.stop()`；修复后 stop task被跟踪并移交后台。
- non-cooperative failure projection 红测：旧实现的 `asyncio.timeout()` 会等待吞取消的投影，0.6 秒后 load仍 pending；修复后硬 deadline返回内存 unhealthy fallback。
- stubborn scanner child 红测：旧实现 `scanner.stop()` 正常返回且 child仍 alive；修复后抛出 `child worker did not stop`，slot和 stop fence均保留。
- kill fallback 回归：terminate 无效、kill 有效时最终 `is_alive()` 为 false，stop才正常返回。

### 4.2 Supervisor 全文件

```text
46 passed, 1 warning in 22.91s
```

### 4.3 第十六轮正式相关 5 文件 Gate

```text
104 passed, 5 warnings in 59.71s
```

覆盖：

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_lifespan_shutdown.py
tests/test_harness_boundary.py
```

warnings 为 LangGraph、Lark 和 WebSocket 依赖 deprecation，不是本轮功能失败。

### 4.4 上一轮 7 文件广覆盖 Gate

单命令在本地 runner 的 120 秒命令上限被中止，只产生部分进度、没有失败汇总；随后按文件职责拆分完整复跑：

```text
Supervisor + parser:
95 passed, 1 warning in 30.97s

repository + router + secret + sandbox + migration:
74 passed, 1 skipped, 1 warning in 27.82s

合计：169 passed, 1 skipped
```

唯一 skip 是当前环境没有真实 PostgreSQL。

### 4.5 静态、格式、编译与差异检查

```text
ruff check --no-cache <4 个本轮 Python 文件>: All checks passed!
ruff format --check --no-cache <4 个本轮 Python 文件>: 4 files already formatted
python -m compileall <本轮生产代码与测试>: passed
git diff --check -- backend docs: passed
```

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，执行共享的 upgrade/downgrade/re-upgrade 严格 schema Gate。
2. 使用两个真实 Feishu App 验证 near-deadline ready、non-cooperative/failed stop、credential rotation、进程重启和 attachment recovery。
3. 在生产同构环境确认 M3 v1 仍只运行一个 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 本轮未执行完整 `pytest tests -q` / `make test`；正式合并仍需完整 CI 汇总。

---

## 6. 最终判定

**第十六轮 Review 指出的 1 项 P1、1 项 P2、1 项 Standards Important 和 1 项 Standards Minor 已在代码、正式测试和文档侧关闭；当前没有已知的第十六轮未修复 P1/P2。**

当前状态可以进入下一轮复审；但真实 PostgreSQL、双 Feishu App 与完整 backend suite 仍是合并/发布前 Gate，因此本报告不宣称最终生产发布完成。
