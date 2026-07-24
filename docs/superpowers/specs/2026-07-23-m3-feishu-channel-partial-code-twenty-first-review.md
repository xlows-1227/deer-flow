# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十一轮代码复审

**状态：** 已复审；第二十轮 2 个原 P2 主路径已关闭，但本轮确认 2 个新的 P2 状态收敛缺口，仓库级全绿 Gate 继续未关闭
**日期：** 2026-07-23

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第二十轮复审：[2026-07-22-m3-feishu-channel-partial-code-twentieth-review.md](./2026-07-22-m3-feishu-channel-partial-code-twentieth-review.md)
- 第二十轮修复报告：[2026-07-22-m3-feishu-channel-partial-twentieth-review-fix-report.md](./2026-07-22-m3-feishu-channel-partial-twentieth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后当前未提交的 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 实际 diff 命令：`git diff 044fa17489b1d064286b97ea88dee65ed08060fe -- backend`
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和历史临时目录
- Spec 来源：开发计划 M3/F3.1-F3.2/M3 Review Gate、第二十轮 review/fix，以及 `backend/CLAUDE.md` 的 Published Feishu 运行时不变量；提交消息无 issue 引用

---

## 1. 复审结论

第二十轮两项修复的主体实现正确：

- Gateway 不再用普通 hook 的 5 秒预算提前取消 Supervisor 的 20 秒 shutdown drain；生产外层预算现在为 `20 + 5 = 25` 秒，并有真实 lifespan 回归覆盖 5–20 秒之间的 late claim；
- request deadline 前异常或自取消的 claim 也会转交 explicit reconciliation owner；
- Repository 的 `reconcile_runtime_claim()` 使用 system scope 和 `SELECT ... FOR UPDATE` 与潜在 claim transaction 串行，只清除 exact token；瞬态失败保留 retry owner；
- router slow-writer 测试的 Supervisor shutdown 与 engine disposal 已改为嵌套清理；
- repository/router/Supervisor/lifespan 聚合和正式五文件 Gate 均通过。

但围绕新 reconciliation 与既有 shutdown 状态机，本轮确认 **2 个高置信 P2**：

1. **claim commit 后 acknowledgement 丢失时，exact token 虽能清除，但启动失败 health 会使用旧 generation 做 CAS，最终既没有持久化 `unhealthy`，也没有写入进程内 health。** `load_active_bindings()` 看似隔离了失败，Owner 看到的却仍可能是 `unknown`；这不满足 F3.2 对单绑定启动失败可观测性的要求。
2. **shutdown 会吞掉 quiescing runtime 的 stop/release 失败，只 drain late claim 集合，随后在 `_running` 仍有 owner 时仍设置 `_shutdown_complete=True` 并正常返回。** leader fence 虽被保留，但 Gateway 会继续 teardown，第二次 shutdown 又直接 no-op，违反已声明的“unresolved ownership 必须抛错且不得完成 shutdown”契约。

Standards 轴另有 1 个延续 Important（完整 backend suite 未全绿）和 3 个非阻塞 Minor，其中 Repository 结构债务为前轮延续项。

**Ready to merge：No。** 第二十轮两个原 P2 的直接路径已经关闭，但 health 与 shutdown completion 仍会把未收敛状态误报为可观测成功/完成，需补正式回归并修复后再合并。

---

## 2. 第二十轮问题关闭状态

| 第二十轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P2：Gateway 5 秒 timeout 提前取消 Supervisor 20 秒 drain | **已关闭** | `app.py` 为 Supervisor 使用内部 20 秒预算加普通 hook 5 秒余量；真实 lifespan 回归覆盖 late claim 在两者之间收敛 |
| P2：deadline 前异常/自取消 claim 未进入 reconciliation，普通空读误判延迟可见 commit | **主问题已关闭，存在派生 P2** | 所有 ambiguous task 均转交 owner，Repository 用行锁 exact-token 事务收敛；但收敛推进 generation 后失败 health 未切换到新 epoch，见 3.1 |
| Standards Minor：router cleanup 可跳过 engine disposal | **已关闭** | 指出的 slow-writer fixture 已使用嵌套 `try/finally` |
| Standards Minor：AgentChannelRepository Divergent Change / Data Clumps | **未关闭（已登记）** | 本轮继续控制修改范围，建议 M3 合并后独立重构 |
| Standards Important：完整 suite 未全绿 | **未关闭（延续）** | 本轮 fail-fast 为 `321 passed, 1 failed`，首个失败仍是范围外 auth 用例 |

---

## 3. Spec 轴

### 3.1 P2：ambiguous claim 收敛后启动失败 health 丢失

**置信度：高**

**Spec 依据：**

- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L781)：单个绑定 start 失败必须记录 `health=unhealthy`，不影响其他绑定
- [开发计划 F3.2 测试要求](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L786)：`load_active_bindings` 中失败 binding 必须标记 unhealthy
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L526)：timeout、数据库失败、renewal 失败或 token mismatch 只应使该 binding unhealthy，ready peer 继续受管

**相关代码：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L543)：deadline 前异常 task 被登记为 ambiguous owner
- [supervisor.py](../../../backend/app/channels/supervisor.py#L720)：`attempt.capture_row(claimed)` 只会在 claim 正常返回后执行
- [supervisor.py](../../../backend/app/channels/supervisor.py#L849)：启动异常 health 使用旧 `attempt.runtime_generation` / token 做 CAS
- [supervisor.py](../../../backend/app/channels/supervisor.py#L489)：reload fallback 再次使用同一旧 epoch；持久化失败时只返回 fallback，不写 `_health`
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L670)：reconciliation 清 token并再次推进 generation，但不投影失败 health
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1816)：reload caller 忽略 `_converge_startup()` 返回的 fallback

确定性时序如下：

1. `attempt` 从 claim 前的 row 捕获 generation G、token `None`。
2. `claim_runtime()` 提交 exact token并把 generation 推进到 G+1，但 acknowledgement 丢失，task 在 deadline 前抛异常。
3. `_start_row_once()` 的 `lease_claimed` 仍为 false，`attempt.capture_row()` 未执行；异常路径用 G/`None` 写 `unhealthy`，CAS 必然 stale。
4. detached reconciler 用行锁清除 token并把 generation 推进到 G+2。
5. reload fallback 仍使用 G/`None`，第二次 CAS 同样 stale；fallback 只作为返回值存在，而 `load_active_bindings()` 不保存它。

本轮使用真实 SQLite Repository 和生产 Supervisor seam 的期望行为回归稳定得到：

```text
health=unknown local={}
FAILED: expected durable and local health to be unhealthy
```

这说明第二十轮确实解决了 durable token orphan，但没有让“cleanup ownership”与“失败状态 ownership”一起收敛。Owner API/诊断可能继续看到 `unknown`，进程内 `health()` 甚至没有该 binding；显式 start 还可能传播内部 stale-projection 异常，而不是稳定的生命周期结果。

**建议修复：** 让 exact-token reconciliation 返回具名结果与最新 durable epoch，或在同一个行锁事务中仅当本次 exact token 被清除且 binding 仍属于该失败 attempt 时原子投影 `unhealthy`。不同 token/新 owner 已存在时不得覆盖 successor health。随后只在 durable 写成功且本地 revision 仍最新时更新 `_health`。新增 commit-after-ack-loss + reload 回归，断言 token 清除、DB/local 均为 unhealthy、peer 正常运行；同时覆盖 foreign token 不被旧失败写覆盖。

### 3.2 P2：shutdown 吞掉 quiescing stop/release 失败并误设 complete

**置信度：高**

**Spec 依据：**

- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L526)：unresolved ownership 必须抛错，不得标记 shutdown complete 或释放 leader fence；stop/release 失败的 transport 必须保留 quiescing retry owner
- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L748)：单 binding 生命周期失败不能破坏 Supervisor 的动态生命周期隔离

**相关代码：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L1293)：`_stop_runtime()` 在 stop/release 未收敛时保留 retry owner 并抛错
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1960)：shutdown 的 `stop_one()` 捕获并吞掉所有 stop 异常
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1969)：20 秒 drain 只观察 `_late_runtime_claim_tasks` / `_late_runtime_release_tasks`，不观察 `_running` 中的 quiescing cleanup owner
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1983)：`_running` 非空时只是不释放 fence
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1986)：即使仍有 owner，也无条件设置 `_shutdown_complete=True`
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1948)：后续 shutdown 因 complete 直接返回，无法再次收敛或释放 fence

本轮让一个已 ready binding 的 `channel.stop()` 稳定失败，期望 shutdown 报告未收敛。实际输出为：

```text
shutdown_complete=True owned=('ach_…',) fence=True
FAILED: shutdown did not raise RuntimeError
```

这不是单纯的日志问题：Gateway 认为 Supervisor hook 已成功，继续停止 scanner/channel service 并结束 lifespan；quiescing cleanup task 可能在数据库/event loop teardown 时失去执行机会，durable token 只能留给下次 startup orphan recovery。fence 保留避免了同进程错误接管，但“正常返回 + complete”错误声明了 graceful shutdown 已结束，也永久禁用了同一对象上的 retry。

**建议修复：** 在同一个 20 秒 deadline 内同时 drain late claim/reconciliation 与 `_running` 中的 quiescing cleanup ownership。`stop_one()` 可收集异常，但只有 `_running`、late claim 和 late release 全部清空后才能设置 complete 并释放 fence；deadline 到期或 owner 仍存在时必须抛出明确错误，保留 `_shutdown_complete=False` 和 fence，使后续调用可重试。新增 stubborn stop、release transient/permanent failure 以及真实 lifespan 回归，断言未收敛时 Gateway 收到失败、complete 为 false、fence 保留；放开 barrier 后第二次 shutdown 能完成并释放 fence。

---

## 4. Standards 轴

### 4.1 Important（延续）：仓库级完整测试 Gate 仍未全绿

[backend/CLAUDE.md](../../../backend/CLAUDE.md#L675) 要求每项 bug fix 带单测、运行完整 suite 且测试通过；[backend/CONTRIBUTING.md](../../../backend/CONTRIBUTING.md#L240) 的 Before Submitting 同样要求 `uv run pytest` 全绿。

本轮完整 backend fail-fast：

```text
FAILED tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
1 failed, 321 passed, 9 warnings in 168.53s
```

失败仍不属于当前 M3 diff，但硬 Gate 没有关闭，不能声明整个仓库 standards-ready。

### 4.2 Minor：Gateway lifespan 新回归的补偿 shutdown 仍可跳过 engine disposal

**相关文件：** [test_gateway_lifespan_shutdown.py](../../../backend/tests/test_gateway_lifespan_shutdown.py#L213)

新测试的 `finally` 中，补偿 `await supervisor.shutdown()` 与 `await engine.dispose()` 仍是顺序执行。如果断言提前失败且补偿 shutdown 正好报告 unresolved ownership，engine disposal 会被跳过。第二十轮已在 router slow-writer 测试中采用嵌套 `try/finally`，这里应使用同一模式。该项只影响失败诊断与测试资源清理，不改变生产 finding 定级。

### 4.3 Minor / Duplicated Code：runtime lease 清除字段更新重复

[sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L662)、[sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L684) 与 [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L704) 重复更新 token、expiry、generation、health revision 与 timestamp。建议抽取只操作已加锁 row 的私有 helper，调用方继续保留各自的 owner/token 条件与事务边界，降低后续 fencing 字段扩展时漏改某一出口的风险。这是判断性 smell，不是硬规范违例。

### 4.4 Minor（延续）：AgentChannelRepository 仍有 Divergent Change / Data Clumps

Repository 继续混合 ingest、runtime、health、credential、cleanup 和 delete 职责，并反复传递 binding/owner 标识与动态 row dict。第二十轮只加入最小原子 seam、避免在 P2 中扩大重构是合理选择；建议 M3 合并后独立引入 `BindingKey`、typed row 和职责仓储。

README/CLAUDE 已同步本轮 runtime 不变量，未发现新的 harness → app 反向依赖或其他 actionable smell。

---

## 5. 验证记录

### 5.1 Repository + router + Supervisor + Gateway lifespan 聚合

```text
83 passed, 2 warnings in 80.48s
```

命令正常退出；第二十轮新增的 Gateway 预算、pre-deadline exception/self-cancel、row-lock exact-token 和 router cleanup 回归均通过。

### 5.2 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

164 passed, 6 warnings in 69.05s
```

### 5.3 对抗性期望行为回归

使用真实 SQLite Repository、生产 Supervisor seam 与 fake transport 补了两个仅用于本轮审计的临时测试：

```text
commit acknowledgement loss:
health=unknown local={}
FAILED: expected health=unhealthy

quiescing stop failure:
shutdown_complete=True owned=('ach_…',) fence=True
FAILED: expected shutdown RuntimeError
```

两个测试均按正确契约失败，分别验证 3.1 和 3.2。临时测试文件随后已删除，未在产品代码或正式测试目录中留下改动。

### 5.4 完整 backend suite（fail-fast）

```text
1 failed, 321 passed, 9 warnings in 168.53s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

### 5.5 静态、格式、编译与差异检查

```text
ruff check --no-cache <7 个第二十轮直接相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 7 个文件>
7 files already formatted

python -m compileall <3 个生产 Python 文件>
passed

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe -- backend
passed
```

pytest 仍提示当前 Windows 账户不能写既有 `.pytest_cache`，但专项、正式 Gate 和完整 fail-fast 均实际执行；完整 suite 的 exit code 1 来自已列明的 auth 断言。

---

## 6. 尚未关闭的环境 / 发布 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 commit acknowledgement 丢失、行锁 exact-token reconciliation、失败 health epoch 和 shutdown retry 的真实事务语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 和 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`；Windows symlink 与 LocalSandbox 平台项继续按独立平台契约处理。
5. `AgentChannelRepository` 的 `BindingKey`、typed row 和职责拆分作为 M3 合并后的独立架构任务处理。

---

## 7. 最终判定

第二十轮的 Gateway shutdown 外层预算和全 outcome-ambiguous claim reconciliation 已正确落地；聚合、正式五文件和静态 Gate 均通过，未发现 scope creep 或分层倒置。

但 exact-token 清理完成后失败 health 仍可能停在 `unknown`，quiescing stop/release 未收敛时 shutdown 又会误设 complete 并正常返回。这两个问题分别破坏 F3.2 的失败可观测性和 CLAUDE 已声明的 ownership shutdown 契约，属于 **2 个 P2 阻塞项**。

**Ready to merge：No。** 建议把 failure-health projection 纳入 exact-token 收敛结果，并让 shutdown 在同一 20 秒预算内 drain 所有 quiescing runtime owner；补对应正式回归后，重新执行聚合、正式五文件、完整 suite 与 PostgreSQL Gate。
