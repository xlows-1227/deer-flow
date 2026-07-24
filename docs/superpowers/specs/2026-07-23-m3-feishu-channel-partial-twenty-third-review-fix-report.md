# M3 Feishu Channel Partial — Twenty-Third Review Fix Report

**日期：** 2026-07-23
**基线：** `044fa17489b1d064286b97ea88dee65ed08060fe`
**对应 Review：** `2026-07-23-m3-feishu-channel-partial-code-twenty-third-review.md`

## 1. 结论

第二十三轮 Review 的三个可执行代码/测试问题已经修复：

1. Supervisor stop-failure 回归不再依赖 SQLite 写事务的毫秒级竞速；
2. discovery cursor 在 Windows 原子替换遇到瞬时 sharing violation 时会在 deadline 内有界重试；
3. Router 测试资源 teardown 在 Supervisor shutdown 与 engine disposal 同时失败时会保留两个异常。

Review 记录的统一登录 auth 首失败也已关闭。`/api/v1/auth/login` 是当前真实存在的统一首次登录路由，不是已移除的旧路径；测试已改为断言其 public/CSRF-exempt 契约，并与认证中间件现有行为对齐。

本轮 M3 Spec 轴没有新增 P1/P2，第二十三轮指出的聚合不确定性和双失败异常覆盖均已关闭。完整 backend suite 在当前 Windows 账户上仍不能判定全绿：auth 失败之后的首个失败是长期记录的符号链接权限限制 `WinError 1314`，发生在测试 setup，尚未进入业务代码；不使用 fail-fast 的运行还显示了其他失败标记，但在 10 分钟命令上限前没有生成可归因的最终列表。因此仓库级 Ready to merge 仍为 **No**，不能把本轮聚焦 Gate 全绿表述成所有仓库或发布 Gate 已关闭。

---

## 2. Review finding 处理结果

| Finding | 状态 | 修复 | 验证 |
|---|---|---|---|
| Standards Important：完整 suite 首先失败于 `test_csrf_does_not_exempt_old_login_path` | auth 首失败已关闭；完整仓库 Gate 仍受平台基线阻塞 | 核对真实 Router、CSRF middleware 与 Auth middleware 后，确认 `/api/v1/auth/login` 是统一首次登录入口。将陈旧的“旧路径不得豁免”测试改为统一登录必须免 CSRF。没有移除生产豁免。 | auth 两文件 `98 passed`；完整 fail-fast 越过 auth，达到 `409 passed, 3 skipped` 后才在 Windows symlink setup 失败。 |
| Standards Important：Supervisor/SQLite 聚合非确定性 | 已关闭 | stop-failure 用例用两个 `asyncio.Event` 明确阻塞 cleanup retry，并验证 runtime health callback 在 lifecycle lock 后等待；释放屏障后再断言 health 与 shutdown retry。测试不再靠 10ms heartbeat 与 50ms shutdown 自然碰撞。 | 修改后的用例连续 `3/3 passed`；四文件同进程聚合 `91 passed`。 |
| Standards Important：Windows discovery cursor replace 首轮偶发 `PermissionError` | 已关闭 | 新增 `_replace_cleanup_state_with_deadline()`；只对瞬时 `PermissionError` 重试，同时受调用方逻辑 deadline 与真实 monotonic wall deadline 双重限制，成功后仍保持临时文件到 cursor 的原子替换。 | 新回归先复现直接失败，再验证前两次 `PermissionError`、第三次成功；正式五文件 Gate `169 passed`。 |
| Standards Minor：Router teardown 双失败时 dispose 异常覆盖 shutdown ownership 异常 | 已关闭 | fixture teardown 始终尝试 shutdown 和 dispose；单失败保持原异常，双失败用 grouped exception 同时暴露 shutdown 与 dispose 两个 cause，并保持顺序。 | 新双失败回归与原单失败回归合计 `2 passed`；Router 随四文件聚合通过。 |
| Standards Minor：Repository/Supervisor 结构债务 | 已登记，未混改 | 本轮只处理 correctness 与测试确定性，没有在 fencing 收敛修复中引入 `BindingKey`、typed row/result 或 Repository 大拆分。 | 作为 M3 correctness 合并后的独立架构任务继续跟踪。 |

---

## 3. 关键不变量

### 3.1 Supervisor 测试必须控制并发顺序

- 第一次 `stop_binding()` 失败后，cleanup retry 进入 `channel.stop()` 并由 barrier 明确暂停。
- runtime health callback 在 cleanup retry 持有 lifecycle lock 时启动，测试先确认 callback 尚未完成。
- 释放 barrier 后 cleanup retry 与 callback 按受控顺序收敛。
- 首次 shutdown 仍验证 unresolved ownership 不会被伪装成成功；解除 stop 故障后，同一 Supervisor 可重试 shutdown 并完成。
- `finally` 始终释放 barrier、排空 health task 并恢复可清理状态，断言失败也不会遗留后台 owner。

### 3.2 Cursor 原子替换必须有界

- discovery progress 仍先写入唯一临时文件，再用 `Path.replace()` 原子发布到 `.discovery-cursor-global`。
- Windows antivirus/indexer/其他短暂文件句柄导致的 `PermissionError` 可以重试。
- 重试时间不能超过扫描逻辑 deadline；即使测试注入的逻辑 clock 不推进，也会受到真实 monotonic wall deadline 限制。
- deadline 耗尽继续抛出原始 `PermissionError`，不会把 cursor 未落盘误报成扫描成功。
- `finally` 继续删除未发布的临时文件。

### 3.3 Router teardown 不能丢失任一失败

- 只有 shutdown 失败时，engine 仍会 dispose，原 shutdown 异常继续传播。
- 只有 dispose 失败时，dispose 异常继续传播。
- 两者都失败时，pytest 同时看到 runtime ownership failure 与 database disposal failure；后一个异常不再覆盖前一个异常。

### 3.4 统一登录是首次请求入口

- `POST /api/v1/auth/login` 是当前 Router 的统一登录入口。
- `POST /api/v1/auth/login/local` 是显式 local-provider 入口。
- 登录前不存在认证会话或 CSRF token，因此两个入口及 trailing-slash 形式必须保持 public/CSRF-exempt。

---

## 4. 代码与文档变更

- `backend/app/channels/feishu.py`
  - 新增 deadline-bound cursor replace helper；
  - discovery cursor 发布改用有界 Windows sharing-violation retry。
- `backend/tests/test_feishu_parser.py`
  - 新增 transient Windows cursor replace contention 回归。
- `backend/tests/test_feishu_supervisor.py`
  - stop-failure/health callback 用例改用显式 cleanup retry barrier。
- `backend/tests/test_agent_channels_router.py`
  - Router fixture teardown 改为保留单失败、聚合双失败；
  - 新增 shutdown + dispose 双失败回归。
- `backend/tests/test_auth_type_system.py`
  - 删除与真实统一登录路由矛盾的旧断言，锁定统一入口免 CSRF 契约。
- `backend/README.md`
  - 记录统一登录、cursor replace deadline 与双失败 teardown 契约。
- `backend/CLAUDE.md`
  - 同步开发约束与聚合测试确定性要求。

---

## 5. 自动化验证

### 5.1 红绿证据

```text
Router 双失败（修复前）：
最终只暴露 OSError("database disposal failed")，shutdown ownership error 被覆盖

Router 单失败 + 双失败（修复后）：
2 passed, 1 warning in 3.58s

Cursor transient replace（修复前）：
Path.replace() 第一次 PermissionError 直接逃逸

Cursor transient replace + 既有 slow-tail / hung-path（修复后）：
3 passed, 1 warning in 3.80s

上述 cursor 三用例重复：
3/3 轮通过（每轮 3 passed）

Supervisor 显式屏障用例：
3/3 passed
```

### 5.2 Auth 契约

```text
tests/test_auth_type_system.py
tests/test_auth_middleware.py

98 passed, 5 warnings in 40.93s
```

### 5.3 Repository + Router + Supervisor + Gateway lifespan 聚合

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py
tests/test_gateway_lifespan_shutdown.py

91 passed, 1 warning in 83.76s
```

没有再次出现 `sqlite3.OperationalError: database is locked`，也没有 event-loop、Supervisor owner 或 engine teardown 挂起。

### 5.4 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

169 passed, 5 warnings in 67.04s
```

两个原先首轮偶发失败的 discovery cursor 用例与新增 transient replace 回归均通过。

### 5.5 完整 backend suite

不使用 fail-fast 的完整运行在 10 分钟命令上限内达到 96%，运行中出现了多组失败标记，但进程超时前没有生成最终 node id/traceback 汇总，因此既不作为“通过”证据，也不对未归因失败作已修复声明。随后使用 fail-fast 精确定位当前第一个失败：

```text
409 passed, 3 skipped, 1 failed, 10 warnings in 112.38s

failed:
tests/test_channel_file_attachments.py::
  TestInboundFileIngestion::
  test_rejects_preexisting_symlink_destination

setup error:
OSError: [WinError 1314] 客户端没有所需的特权
```

该失败发生在 `Path.symlink_to()` 创建测试夹具时，未执行到附件业务代码；同类限制已在既有 M1/M3 报告中持续记录。它证明 auth 首失败已经关闭，但也证明当前 Windows 账户不能为仓库完整 suite 提供全绿证据。96% 运行中的其他失败仍需在允许完整执行并输出最终 summary 的环境中分类，不能仅凭第一个 symlink 失败推断它们全部属于同一原因。

### 5.6 静态、格式与编译

```text
ruff check（5 个本轮 Python 文件）：
All checks passed!

ruff format --check（5 个本轮 Python 文件）：
5 files already formatted

compileall（5 个本轮 Python 文件）：
passed
```

---

## 6. 尚未关闭的环境 / 发布 Gate

1. 在具备 symlink 权限的 Windows 环境或 Linux CI 运行完整 backend suite，取得未超时的最终 failure summary，并逐项关闭或登记当前平台测试基线。
2. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim reconciliation、failure-health epoch、shutdown retry 和 row-lock 事务语义。
3. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
4. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
5. 将 Repository/Supervisor typed key/result 与职责拆分作为 M3 correctness 合并后的独立架构任务处理。

---

## 7. 最终判定

**第二十三轮 Review 的可执行问题已经全部修复；当前没有已知、仍未修复的第二十三轮 M3 P1/P2。**

Supervisor 与 cursor 聚合失败已有确定性机制和回归覆盖，Router teardown 不再丢失 shutdown ownership 错误，统一登录测试也已与真实 API 契约一致。聚焦 Gate、四文件聚合和正式五文件 Gate 均全绿。

**仓库级 Ready to merge：No。** 已确认本轮 auth 与 M3 聚合问题不再是首个阻塞；当前至少仍有 Windows symlink 权限、完整 suite 未归因失败列表，以及真实 PostgreSQL、双 Feishu App 等发布环境 Gate。在这些 Gate 完成前，不应声明整个 M3 已达到无条件生产发布状态。
