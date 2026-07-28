# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十轮代码复审

**状态：** 已复审；第十九轮 P3 已关闭，P2 主路径已修复但仍有 2 个 P2 边界，仓库级全绿 Gate 继续未关闭
**日期：** 2026-07-22

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十九轮复审：[2026-07-22-m3-feishu-channel-partial-code-nineteenth-review.md](./2026-07-22-m3-feishu-channel-partial-code-nineteenth-review.md)
- 第十九轮修复报告：[2026-07-22-m3-feishu-channel-partial-nineteenth-review-fix-report.md](./2026-07-22-m3-feishu-channel-partial-nineteenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后当前未提交的 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 实际 diff 命令：`git diff 044fa17489b1d064286b97ea88dee65ed08060fe -- backend`
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- Spec 来源：开发计划 M3/F3.1-F3.2、第十九轮 review/fix，以及 `backend/CLAUDE.md` 的已声明运行时不变量；提交消息无 issue 引用

---

## 1. 复审结论

第十九轮修复的主体方向正确：

- deadline 后不再取消未决 claim writer；detached task 会转交 exact-token reconciliation，release 异常和同 token generation race 均会保留 retry owner；
- Supervisor 自身能在 20 秒总预算内 drain late claim/release，超时不会设置 `_shutdown_complete` 或释放 leader fence；
- credential update 推进 `runtime_generation`，active/inactive binding 的旧 secret probe 都无法跨 epoch 写入；
- stale health helper 与具名 `_StartupPolicy` 已消除第十九轮两个局部 Standards Minor；
- 三文件聚合和正式五文件 Gate 均正常结束。

但本轮发现 **2 个 P2**：

1. **Gateway 生产 lifespan 用 5 秒外层 `wait_for()` 提前取消 Supervisor 声明的 20 秒 drain。** 5–20 秒内本可收敛的 claim/release 会被生产 wrapper 截断，lifespan 仅记录 warning 后继续 teardown；新增 Supervisor 单测没有覆盖真实 wrapper。
2. **claim 在 request deadline 之前以异常/自取消结束时不会进入 reconciliation。** `asyncio.wait()` 把 task 放入 done 集合后，代码直接 `return await claim_task`；如果 DB commit 已生效但 acknowledgement 以异常结束，`lease_claimed` 仍为 false，late ownership 集合也为空，token 可一直阻塞该 binding。即使是已 detached 的异常结果，reconciler 也以一次非锁定“token 当前不存在”读取作为终态，仍未严格排除服务端 commit 稍后可见的结果不确定窗口。

Standards 轴另有 1 个延续 Important（完整 backend suite 的 auth 失败）和 2 个非阻塞 Minor。

**Ready to merge：No。** 两个 P2 均属于第十九轮 claim/shutdown ownership 契约的剩余出口，应补正式回归并关闭后再合并。

---

## 2. 第十九轮问题关闭状态

| 第十九轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P2：detached claim 在取消、release 失败/CAS miss 时遗留 token | **部分关闭** | deadline 后的正常迟到、release transient failure 和 generation race 已可重试收敛；但 deadline 前异常/自取消不会 detach，且异常后的单次非锁定空读不足以证明 commit 不会稍后可见，见 3.2 |
| P2：shutdown 未 drain late ownership | **部分关闭** | Supervisor 直接调用时的 20 秒 drain 与 leader fence 保留成立；Gateway 生产 wrapper 5 秒即取消该 drain，见 3.1 |
| P3：credential rotation 未切换 health epoch | **已关闭** | `update_credentials()` 推进 generation 后清零 health/revision；active/inactive 并发 probe 使用真实 repository seam 验证，DB 与内存均不发布旧 secret 结果 |
| Standards Minor：stale probe 重复构造 health | **已关闭** | `_current_binding_health()` 统一当前 durable health 与 serving 构造 |
| Standards Minor：startup enum 四布尔 tuple | **已关闭** | enum value 已改为具名字符串，行为从明确成员导出 |
| Standards Minor：Repository / Data Clumps | **未关闭（已登记）** | 属于跨模块结构债务，不阻塞本轮 correctness，建议 M3 合并后独立拆分 |
| Standards Important：完整 suite 未全绿 | **未关闭（延续）** | 本轮 fail-fast 仍为 `320 passed, 1 failed`，首个失败仍是范围外 auth 用例 |

---

## 3. Spec 轴

### 3.1 P2：Gateway 的 5 秒 hook timeout 会取消 Supervisor 的 20 秒 ownership drain

**置信度：高**

**相关文件：**

- [app.py](../../../backend/app/gateway/app.py#L64)：所有 shutdown hook 共用 5 秒上限
- [app.py](../../../backend/app/gateway/app.py#L314)：生产 lifespan 以 `asyncio.wait_for(..., 5s)` 包装 Supervisor shutdown
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1942)：Supervisor 内部声明并执行 20 秒总预算
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L703)：新增 shutdown 回归只直接调用 Supervisor，并把内部预算缩到 50ms
- [test_gateway_lifespan_shutdown.py](../../../backend/tests/test_gateway_lifespan_shutdown.py)：现有 lifespan 测试只验证普通 channel service 的统一 5 秒上限
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L526)：已声明 shutdown 在一个 20 秒 Supervisor budget 内 drain late ownership

`FeishuSupervisor.shutdown()` 在 20 秒 deadline 内等待 runtime 与 late claim/release；只有内部 deadline 到期才抛错并保留 fence。生产接线却在 5 秒调用 `wait_for()`：外层 deadline 先到时，shutdown 收到外部 `CancelledError`，内部的“未收敛则明确失败”路径甚至不会执行。Gateway 捕获外层 `TimeoutError` 后只写 warning，继续停止 scanner、channel service 和其余 lifespan 资源。

本轮缩短比例后的最小复现为：

```text
outer shutdown timeout: complete=False fence_held=True late_claims=1
```

leader fence 在进程退出前仍能避免并发接管，但 graceful cleanup 已不再拥有 20 秒窗口；reconciler 可能随 event loop teardown 被取消，持久 token 只能依赖下次 Gateway 启动的 orphan recovery。该行为直接弱化了第十九轮修复报告 §2.2 和 CLAUDE 的 shutdown 契约。

**建议修复：** 为 Feishu Supervisor 使用独立的外层 timeout，至少大于内部 20 秒预算并留少量调度余量，或移除这一层更短的重复 deadline，保留其他普通 hook 的 5 秒上限。新增真实 lifespan 回归：让 late claim 在 5 秒之后、20 秒之前收敛，断言 reconciliation 完成、token 清除、shutdown complete 且 fence 正常释放；另保留超过内部预算时的有界退出测试。

### 3.2 P2：deadline 前结束的 outcome-ambiguous claim 未转交 reconciliation

**置信度：高**

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L543)：`_claim_runtime_before_deadline()`
- [supervisor.py](../../../backend/app/channels/supervisor.py#L562)：只有 caller cancellation 或 task 未在 deadline 内完成时调用 `_detach_runtime_claim()`
- [supervisor.py](../../../backend/app/channels/supervisor.py#L569)：task 已 done 时直接 `await` 并传播异常
- [supervisor.py](../../../backend/app/channels/supervisor.py#L606)：reconciliation 只服务已经 detach 的 task
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L465)：reconciler 的首次检查是普通 system-scoped SELECT，不持行锁
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L541)：claim 在 `session.commit()` acknowledgement 后才返回行
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L572)：现有 acknowledgement 用例刻意让 task 超过 deadline，未覆盖 deadline 前异常完成

当前 ownership 转移由“是否超时”决定，而不是由“claim commit outcome 是否确定”决定。确定性时序如下：

1. repository 发出 claim commit；数据库已持久化 token，但客户端在 request deadline 前丢失 commit acknowledgement并抛异常。
2. `asyncio.wait()` 返回时 task 已在 done 集合，故不会调用 `_detach_runtime_claim()`。
3. `return await claim_task` 抛出；`_start_row_once()` 中 `lease_claimed` 仍为 false，discard 路径不会 release。
4. claim/release ownership 两个集合均为空，shutdown 也没有对象可 drain；该 binding 的 token 只能在 Gateway 重启 recovery 时清除。

本轮最小复现输出：

```text
predeadline failure: token=predeadline-token release_calls=0 late_claims=0 late_releases=0
```

此外，detached claim 若以网络异常结束，服务端 transaction 仍可能在 task 完成后短暂处于结果不确定状态。当前 reconciler 第一次普通 SELECT 看不到 exact token 就立即返回；该读取不与原 claim transaction 串行，不能严格证明 token 不会稍后变为可见。第十九轮测试是在原 repository commit 已完全返回后才释放 barrier，因此没有覆盖这个窗口。

**建议修复：**

1. task 在 deadline 前以异常或取消态完成时，也必须在向调用方传播前转交 exact-token reconciliation；不要把“done”误当成“确定未提交”。
2. 将 reconciliation 下沉为 repository 的原子 exact-token 操作，通过行锁或条件写与可能仍在完成的 claim transaction 串行；不要仅凭一次非锁定空读宣称收敛。至少应在结果不确定时要求稳定观测/有界重试。
3. 新增正式回归：commit 生效后立即在 deadline 前抛异常、task 自取消，以及异常返回时 token 延迟可见；断言最终 release、late ownership 可 drain，且 peer 不受影响。

---

## 4. Standards 轴

### 4.1 Important（延续）：仓库级完整测试 Gate 仍未全绿

[backend/CLAUDE.md](../../../backend/CLAUDE.md#L673) 要求每次 feature / bug fix 运行完整 suite 且全部通过；[backend/CONTRIBUTING.md](../../../backend/CONTRIBUTING.md#L240) 的 Before Submitting 也要求 `uv run pytest` 全绿。

本轮重新执行 fail-fast：

```text
FAILED tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
1 failed, 320 passed, 7 warnings in 93.28s
```

失败不属于当前 M3 diff，但硬 Gate 仍未满足，不能声明整个仓库 standards-ready。

### 4.2 Minor：router 清理仍可能在 shutdown 抛错时跳过 engine dispose

**相关文件：** [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py#L223)

第十九轮修复报告称 slow-writer 测试的任意异常都会执行 Supervisor shutdown 和 engine dispose；但 `finally` 中仍是两个顺序 await：

```python
await supervisor.shutdown()
await engine.dispose()
```

本轮恰好让 `shutdown()` 在 late ownership 未收敛时主动抛错，因此第一行异常会跳过第二行，仍可能在失败复现时遗留数据库资源并干扰 event-loop teardown。建议用嵌套 `try/finally` 保证 dispose 独立执行。该项属于测试清理可靠性，不影响生产路径定级。

### 4.3 Minor（延续）：AgentChannelRepository 仍有 Divergent Change / Data Clumps

`AgentChannelRepository` 继续在单类中混合 ingest、runtime、health、credential、cleanup 和 delete 职责，并反复传递 `(agent_id, binding_id, owner_user_id)` 与 `dict[str, Any]`。第十九轮选择不在 fencing 修复中做大范围结构迁移是合理的；建议 M3 合并后用 `BindingKey`、typed row 及职责仓储独立重构。

其余 smell baseline 未发现新的 actionable finding。

---

## 5. 验证记录

### 5.1 Supervisor + repository

```text
70 passed, 1 warning in 31.26s
```

### 5.2 核心三文件聚合 Gate

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py

78 passed, 1 warning in 71.45s
```

命令正常退出，未出现 router / event-loop teardown 挂起。

### 5.3 正式 5 文件 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

162 passed, 5 warnings in 64.47s
```

### 5.4 Gateway lifespan 既有回归

```text
tests/test_gateway_lifespan_shutdown.py
1 passed, 1 warning in 9.88s
```

该测试确认普通 channel service 的 5 秒 shutdown hook 仍有界，但未构造 Feishu Supervisor 的 20 秒 late-ownership drain，故不能关闭 3.1。

### 5.5 完整 backend suite（fail-fast）

```text
1 failed, 320 passed, 7 warnings in 93.28s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

### 5.6 对抗性最小复现

```text
predeadline failure: token=predeadline-token release_calls=0 late_claims=0 late_releases=0
outer shutdown timeout: complete=False fence_held=True late_claims=1
```

复现脚本仅用于本轮审计，运行后已删除，未写入产品代码或正式测试目录。

### 5.7 静态、格式、编译与差异检查

```text
ruff check --no-cache <5 个第十九轮直接相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 5 个文件>
5 files already formatted

python -m compileall <2 个本轮生产 Python 文件>
passed

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe -- backend
passed
```

uv 在命令结束时仍提示无法打开既有 `.venv/.lock`，但专项、聚合、ruff、format 和 compileall 均实际执行并按上述 exit code 完成；完整 suite 的 exit code 1 来自已列明的 auth 断言。

---

## 6. 尚未关闭的环境 / 部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 commit acknowledgement 丢失、异常返回、延迟可见、exact-token release retry 和 shutdown drain 的真实事务语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`；Windows LocalSandbox 5 项继续按其独立平台契约处理。

---

## 7. 最终判定

第十九轮修复已经关闭 credential health epoch，并使 deadline 后正常迟到的 claim、release transient failure、same-token generation race 和 Supervisor 直接 shutdown 主路径可重试收敛。新增回归、三文件聚合和正式五文件 Gate 均通过。

但生产 Gateway 会在 5 秒取消内部 20 秒 drain；deadline 前异常完成的 claim 也不会进入 reconciliation。这两条路径都可能让 durable token 脱离当前 Supervisor 的显式 ownership，属于 **2 个 P2 阻塞项**。

**Ready to merge：No。** 建议统一 production shutdown deadline 契约，并让所有 outcome-ambiguous claim（不只超时 task）通过事务串行的 exact-token reconciliation 收敛；补 lifespan、pre-deadline exception/self-cancel 和 delayed-visibility 回归后，重新执行三文件聚合、正式五文件、完整 suite 与 PostgreSQL CI Gate。
