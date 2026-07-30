# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十六轮代码复审

**状态：** 已复审，仍有阻塞问题
**日期：** 2026-07-21

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十五轮复审：[2026-07-21-m3-feishu-channel-partial-code-fifteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-fifteenth-review.md)
- 第十五轮修复报告：[2026-07-21-m3-feishu-channel-partial-fifteenth-review-fix-report.md](./2026-07-21-m3-feishu-channel-partial-fifteenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后的当前未提交 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- 重点：第十五轮 2 个 P1、1 个 P2、1 个 Standards Important、1 个 Standards Minor 的关闭情况，以及本轮修复 diff 的 Spec/Standards 双轴符合性

---

## 1. 复审结论

第十五轮修复已经实质关闭了首次 binding DB 重读逃逸、provisional lease 在 ready 前不续期、startup 直接执行无上界 binding-index 投影、scanner manager readiness 长时间不响应 stop，以及 scanner 测试 fake 重复等问题。正式相关回归本轮复跑为 `99 passed`，Ruff 与格式检查通过。

但本轮仍发现 **Spec 轴 1 个 P1、1 个 P2**：

1. 25 秒 startup convergence timeout 触发后，取消路径会无上界等待 start task drain 和 `channel.stop()`；一个 transport 的 teardown 仍能永久阻塞 `load_active_bindings()`，使 cleanup janitor 无法启动；
2. scanner 对 child 执行 `terminate → kill → join` 后不复核其是否已经退出，`stop()` 会在 child 仍 alive 时正常返回并丢失对该 slot 的跟踪。

Standards 轴发现 **1 个 Important、1 个 Minor**：README/CLAUDE 声明“成功 shutdown 前全部 child 已退出”，但实现和测试尚不能保证该契约；startup convergence 的 timeout/失败投影在两个入口重复展开，存在后续语义漂移风险。

**结论：Ready to merge：No。** P1 仍直接违反 F3.2“单个 binding 启动失败不影响其他 binding”和 startup 不阻断 Gateway 的生命周期目标。P2 会把不完整的 scanner drain 错报为成功，并允许后续生命周期遗忘仍存活的旧 worker。

---

## 2. 第十五轮问题关闭状态

| 第十五轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1-1：首次 binding DB 重读可撤销整个 Supervisor 管理入口 | **已关闭** | 初始 `_binding()` 重读、状态判断、启动及失败投影已进入逐 binding 的异常和 timeout 边界；单行读取失败不再逃逸 `gather()` |
| P1-2：固定 startup lease 不是完整启动上界 | **部分关闭** | provisional token 已续期，startup cleanup-health 已改用 scanner；但 timeout 取消后的 start drain/`channel.stop()` 仍无上界，见 3.1 |
| P2：scanner stop 可在 manager/unpublished child 存活时返回 | **部分关闭** | readiness 已 stop-aware，manager 超时会显式抛错；但 child 强制终止后未验证退出，见 3.2 |
| Important：scanner shutdown 行为、文档与测试不一致 | **部分关闭** | 文档和 blocked-readiness 测试已补充；stubborn child 反例仍能推翻成功退出保证，见 4.1 |
| Minor：scanner 测试重复 process/connection fake | **已关闭** | 相邻 scanner 回归已共用可配置的 `_ScannerFakeProcess`、connection 与 context helpers |
| M3 部署/完整 Review Gate | **未关闭** | 真实 PostgreSQL、双 Feishu App 和全量 backend 最终汇总仍未在本轮完成 |

---

## 3. Spec 轴

### 3.1 P1：startup deadline 可被无上界 teardown 绕过

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L330)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L524)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L643)
- [feishu.py](../../../backend/app/channels/feishu.py#L1530)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L383)
- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L746)

`_start_row()` 与 `load_active_bindings()` 的逐行任务都使用 `asyncio.timeout(25s)`。deadline 到达时，timeout 先向当前任务注入 `CancelledError`；它只有在退出 timeout context 后才会转换成可由外层处理的 `TimeoutError`。

当前取消会先进入 `_start_channel_with_provisional_lease()` 的 `finally`，取消 `start_task` 后无界 `gather()`；随后 `_start_row_once()` 又捕获 `CancelledError` 并等待 `_discard_unpublished_runtime()`。后者直接 `await channel.stop()`，没有独立 deadline。真实 `FeishuChannel.stop()` 也会无界等待已取消的 `_background_tasks` 汇总。因此，只要 start task 不响应取消，或 transport 的 stop/背景任务 drain 不返回，timeout context 就永远无法退出，25 秒并不是完整 convergence 上界。

本轮增加并运行了一个临时诊断：把 startup timeout 缩短为 `0.1s`，令第一个 binding 的 `start()` 可取消、但 `stop()` 等待未释放事件，第二个 binding 正常。`0.6s` 后 `load_active_bindings()` 仍 pending 于最终 `gather()`；只有释放 `stop()` 后才能返回。由于 janitor 仅在该 `gather()` 之后创建，该 binding 仍能阻止全局 janitor 启动。临时探针已在验证后移除，现有正式测试未被改动。

这继续违反开发计划 F3.2 的以下要求：单 binding start 失败只标记自身 unhealthy、不抛出、不影响 peers；动态 binding 生命周期不得要求或阻塞 Gateway 重启/启动。现有 `test_hung_binding_start_times_out_without_blocking_peer_or_janitor` 只模拟了挂起的 `start()`，其 fake `stop()` 立即返回，因此没有覆盖真正的取消清理边界。

建议把 teardown 也纳入可证明的总 deadline。若 transport 无法在预算内确认退出，不应丢弃 fencing ownership：把对应 `_RunningChannel` 转为 quiescing、保留 lease/token 并交给后台 cleanup retry，同时让当前 binding 先收敛为 unhealthy，使 peer startup 和 janitor 可以继续。`_start_channel_with_provisional_lease()` 的 start-task drain 也应采用相同的有界、保留所有权语义。补充至少两条双 binding 回归：不响应取消的 start task，以及可取消 start + 永久阻塞 stop；两者都应断言 load 有界返回、peer healthy、janitor 已启动、失败 generation 在确认退出前不能被替换。

### 3.2 P2：scanner 会在 child 仍存活时把 shutdown 报告为成功

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L436)
- [feishu.py](../../../backend/app/channels/feishu.py#L488)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1099)
- [README.md](../../../backend/README.md#L216)
- [CLAUDE.md](../../../backend/CLAUDE.md#L530)

`_terminate_slot()` 在 `terminate()`、`join(0.5)`、`kill()`、再次 `join(0.5)` 后直接返回，没有最后一次检查 `process.is_alive()`。`stop()` 只验证 maintenance manager 是否退出，并且已经把 published slots 从 `self._slots` 中清空；所以 child 即使仍 alive，shutdown 也会正常返回，而且 scanner 不再保存可供后续 retry/拒绝 restart 的 slot ownership。

本轮使用共享 fake 派生 stubborn process，使 `terminate()` 和 `kill()` 只记录调用但保持 `alive=True`。诊断结果为：`scanner.stop()` 正常返回，随后 `process.is_alive() is True`；期望的“成功 stop 后 child 已退出”断言失败。临时探针已移除。真实 OS 上 `kill()` 通常有效，但实现既然声明的是确认退出契约，就不能把发出 kill 信号等同于已经退出。

这会让成功 Gateway shutdown 遗忘旧 worker；同进程再次 start 时可建立新池，与仍存活的旧 child generation 重叠。建议让 `_terminate_slot()` 在最后一次 join 后复核退出状态并返回/抛出明确失败；`stop()` 在任何 child 未退出时必须保留可重试的 tracking/stop fence 并显式失败，不能正常返回后清空所有权。回归应覆盖 terminate 无效但 kill 有效，以及 terminate/kill 后仍 alive 两种情况。

---

## 4. Standards 轴

### 4.1 Important：scanner 的文档、实现和 teardown 测试仍不一致

**相关文件：**

- [CLAUDE.md](../../../backend/CLAUDE.md#L530)
- [README.md](../../../backend/README.md#L216)
- [feishu.py](../../../backend/app/channels/feishu.py#L436)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1115)

README 与 CLAUDE 明确写明，scanner shutdown 正常返回只发生在 manager 和所有 published/unpublished child 均退出之后。这是比“已发送 terminate/kill”更强的可观察保证，而 3.2 的确定性反例证明当前实现尚未满足。

共享 `_ScannerFakeProcess.terminate()` 当前总会把 `alive` 置为 false，导致 kill fallback 本身以及 kill 后仍存活的失败路径都不可测试。按仓库 Documentation Update Policy 和 Mandatory TDD，修复应先加入 stubborn-child 红测，再让实现与文档契约一致；若产品只承诺 best-effort kill，则必须同步收窄 README/CLAUDE 描述并明确后续 restart fence，但这会弱化第十五轮已声明的不变量，不建议采用。

### 4.2 Minor：startup convergence 的 deadline/失败投影存在双份实现

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L330)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1303)

显式 start/restart 使用 `_start_row()` 包装 timeout 和 `_record_startup_failure()`；startup load 又在内嵌 `start_row()` 中重新展开一次“重读 → timeout → start → 失败投影”。两条入口本来就需要不同的 strict/not-found 策略，但完整 convergence 边界和失败语义应只有一个实现。本轮 P1 正好同时影响这两个入口，后续若只修一处会再次产生行为漂移。

建议提取单一的 per-binding convergence helper，让显式操作与 startup load 只传入 strict、是否重读以及 not-found 策略。该项属于 **Duplicated Code**，不单独决定合并结论。

本轮未发现新增公开 API 的类型/docstring、harness→`app.*` 反向依赖、Ruff/format 或新的 scope creep 问题。

---

## 5. 验证记录

### 5.1 确定性诊断

两个临时红测均在验证后移除，未写入业务代码或正式测试集：

```text
startup teardown probe:
0.1s convergence timeout；0.6s 后 load_task 仍 pending
期望的有界返回断言失败

scanner stubborn-child probe:
stop_returned = True
child_alive_after_stop = True
期望的 child 已退出断言失败
```

### 5.2 正式相关测试

```text
pytest tests/test_feishu_supervisor.py \
       tests/test_feishu_parser.py \
       tests/test_feishu_websocket_lifecycle.py \
       tests/test_gateway_lifespan_shutdown.py \
       tests/test_harness_boundary.py -q

99 passed, 6 warnings in 46.46s
```

warning 为 LangGraph/Lark/WebSocket deprecation 及当前环境 pytest cache 写入警告，不是功能失败。

### 5.3 静态、格式与差异检查

```text
ruff check --no-cache <6 个第十五轮修复相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 6 个文件>
6 files already formatted

git diff --check
通过（仅有无关 config.yaml 的 CRLF 提示）
```

### 5.4 尚未关闭的 Gate

- 当前环境未配置真实 PostgreSQL；migration upgrade/downgrade/re-upgrade 严格 Gate 仍需在 `REQUIRE_POSTGRES_TESTS=1` 的 CI 中执行。
- 尚未执行两个真实 Feishu App 的 near-deadline ready、rotation、stop failure、process restart 和 attachment recovery 冒烟。
- 本轮未执行完整 `pytest tests -q` / `make test`；正式合并仍需完整 CI 汇总。

---

## 6. 最终结论

第十五轮已经补齐 startup 首次重读隔离、provisional renewal、scanner-backed projection、stop-aware manager drain 和共享测试 fake；这些修复方向正确且正式回归通过。但 25 秒 deadline 仍会在取消 teardown 中失效，scanner 也仍会把未确认 child 退出的 drain 报告为成功。

建议优先顺序：

1. 将 start-task drain 与 `channel.stop()` 纳入有界且保留 fencing ownership 的 quiescing cleanup；
2. scanner 强制终止后复核 child 退出，失败时保留 tracking/stop fence 并抛错；
3. 补齐 hung-stop/non-cooperative-start 与 stubborn-child 红测；
4. 收敛两套 startup convergence 包装；
5. 最后完成真实 PostgreSQL、双 Feishu App 与全量 backend Gate。

**Ready to merge：No。**
