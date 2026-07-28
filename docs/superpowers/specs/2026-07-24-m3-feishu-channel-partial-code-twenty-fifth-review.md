# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十五轮代码复审

**状态：** 已复审；第二十四轮 cursor deadline P2 已关闭，发现 1 个新的 Standards Important 聚合测试确定性问题；仓库完整测试与发布 Gate 未关闭
**日期：** 2026-07-24

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第二十四轮复审：[2026-07-23-m3-feishu-channel-partial-code-twenty-fourth-review.md](./2026-07-23-m3-feishu-channel-partial-code-twenty-fourth-review.md)
- 第二十四轮修复报告：[2026-07-24-m3-feishu-channel-partial-twenty-fourth-review-fix-report.md](./2026-07-24-m3-feishu-channel-partial-twenty-fourth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审提交：`cc4c3eaeb6f0abc5d6c8a2314005edc32014fa9c`（`fix(m3): finalize Feishu supervisor correctness gates`）
- 实际 diff：`git diff 044fa17489b1d064286b97ea88dee65ed08060fe...cc4c3eaeb6f0abc5d6c8a2314005edc32014fa9c -- backend`
- 提交范围：22 个 backend 文件，`9933 insertions(+), 644 deletions(-)`
- 测试方式：从复审提交生成独立 Git archive 快照；关键生产、测试和文档文件的 blob hash 与提交逐项一致
- Spec 来源：开发计划 M3/F3.1-F3.4/M3 Review Gate、设计文档及 `backend/CLAUDE.md` Published Feishu 不变量
- Standards 来源：`backend/AGENTS.md`、`backend/CLAUDE.md`、`backend/CONTRIBUTING.md` 与 code-review smell baseline
- 排除：当前工作区中尚未提交的 M4 backend/frontend、`config.yaml`、图片、验收草稿和历史测试临时目录

---

## 1. 复审结论

第二十四轮唯一的 Spec P2 已正确关闭：

- `_replace_cleanup_state_with_deadline()` 在每次 `Path.replace()` 前同时检查逻辑 deadline 与 monotonic wall deadline；
- 首次进入已过期时不执行 replace 并抛出 `TimeoutError`；
- transient `PermissionError` 后耗尽预算时不再 replace，并保留最后一个文件错误；
- frozen logical clock 仍由 wall deadline 终止；
- 三个负向边界和 transient success 均有直接回归，timeout 路径不发布 target。

本轮 **Spec 轴没有新的 finding**，未发现缺失需求、scope creep 或明确生产行为错误。

Standards 轴发现 **1 个新的 Important**：`test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner` 在解除 stop 故障后，未等待后台 cleanup retry、durable runtime token 与 process-local owner 收敛，就立即以 1 秒预算再次调用 `shutdown()`。该用例隔离重复 5 次均通过，但 91 项同进程聚合首轮出现 `1 failed, 90 passed`，原样重跑又 `91 passed`，证明 Review Gate 仍受真实调度时序影响。

此外继续保留：

1. **Important Gate（延续）：** 完整 backend suite 尚无全绿证据；
2. **Minor（延续）：** Channel/Supervisor/Repository 的 Divergent Change、Data Clumps 与 Primitive Obsession 结构债务。

**第二十四轮 cursor 修复：Pass。M3 聚合 Review Gate：Partial Pass。仓库级 Ready to merge：No。** 应先关闭本轮聚合测试确定性问题，再把 M3 标记为完成态。

---

## 2. 第二十四轮问题关闭状态

| 第二十四轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| Spec P2：helper 入口 deadline 已过仍执行 replace | **已关闭** | 首次 attempt 前检查逻辑预算；确定性回归验证 attempt count 为 0、source 保留、target 不存在 |
| Spec P2：retry sleep 耗尽后仍继续 replace | **已关闭** | 每次 attempt 前重算逻辑与 wall remaining；耗尽后重抛最后一个 `PermissionError` |
| Standards Minor：frozen logical clock 缺少 wall deadline 覆盖 | **已关闭** | wall clock 推进后固定在第二次 attempt 终止，不会形成无限 retry |
| Standards Important：Supervisor release-failure 测试依赖自然时序 | **已关闭** | 用例已使用显式 barrier，并等待 durable token 与 process-local owner 同时收敛 |
| Standards Important：完整 backend suite 未全绿 | **未关闭，平台/环境 Gate** | 本轮未重跑完整 suite；第二十四轮修复报告记录的 symlink、LocalSandbox 与 live bash 环境失败仍有效 |
| Standards Minor：Channel/Repository 结构债务 | **未关闭（已登记）** | correctness 提交未混入大规模职责拆分 |

---

## 3. Spec 轴

### 3.1 Cursor deadline P2 关闭复核

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L80)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1001)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L534)

当前 helper 的行为与文档契约一致：

1. 入口先用 `deadline - clock()` 建立逻辑预算，已过期时明确抛出 `TimeoutError`；
2. wall deadline 由初始 remaining 与 `time.monotonic()` 建立，不受注入 clock 冻结影响；
3. while loop 顶部在每次 replace 前取逻辑与 wall remaining 的较小值；
4. 首次过期与 contention 后过期保持不同异常语义；
5. 所有过期路径都不会开始新的 filesystem mutation。

定向测试验证了首次过期、sleep 到期、冻结逻辑时钟和 transient success；同时断言 attempt 数量、source/target 状态及异常类型。未发现第二十四轮 P2 的残留路径。

### 3.2 Supervisor release-failure 修复复核

[test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L989) 已通过 `cleanup_retry_started`、`release_cleanup_retry` 与 `release_recovered` 三个显式事件控制并发顺序；第二次 shutdown 前同时等待 durable token clear 与 `owned_binding_ids == ()`。`finally` 始终释放 barrier，并只在 owner 已收敛时补做 shutdown。

这组修复保留了生产契约：首次 shutdown 必须失败并保留 owner/fence；故障解除后，同一 Supervisor 可以完成重试。本轮直接回归通过，没有发现为了测试变绿而削弱生产语义的情况。

### 3.3 Spec 轴汇总

```text
缺失或部分实现：0
Scope creep：0
实现错误：0
最严重 Spec finding：无
```

完整 suite、真实 PostgreSQL 与双 Feishu App 属于 Review/发布 Gate，不能用聚焦测试替代；但现有证据不足本身不重复登记为生产 Spec finding。

---

## 4. Standards 轴

### 4.1 Important：Gateway lifespan stop-failure 回归仍依赖调度时序

**相关文件：**

- [test_gateway_lifespan_shutdown.py](../../../backend/tests/test_gateway_lifespan_shutdown.py#L365)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L1032)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L534)

`test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner` 先用 50ms shutdown deadline 制造 stop failure，验证 Supervisor 保留 owner 与 leader fence。随后测试只执行：

```python
channel.fail_stop = False
RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS = 1.0
await supervisor.shutdown()
```

这里没有确认后台 `_retry_local_runtime_stop()` 是否已经：

1. 进入或离开 binding lifecycle lock；
2. 完成成功的 channel stop；
3. 清除 durable runtime token；
4. 清除 process-local `owned_binding_ids`。

聚合负载下，cleanup retry 可能仍持有 lifecycle lock 或正在做 SQLite 持久化。第二次 shutdown 同样等待该锁，1 秒预算耗尽后抛出 `RuntimeError("...timed out...")`。测试的 `finally` 又在 owner 尚未收敛时无条件再次调用 shutdown，因此还可能用清理异常覆盖测试主体的首个失败。

本轮确定性证据：

```text
隔离重复：
5/5 passed

四文件同进程聚合首轮：
1 failed, 90 passed, 1 warning in 89.07s
failed:
tests/test_gateway_lifespan_shutdown.py::
  test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner

相同四文件聚合原样重跑：
91 passed, 1 warning in 71.02s
```

这不是稳定的生产错误复现，而是 Review Gate 测试同步不完整。仓库文档已经要求 Supervisor contention 使用显式 barrier，并要求 release-failure 在重试 shutdown 前等待 durable token 和 local owner 收敛；当前 Gateway lifespan 用例没有遵循同一标准。

建议参考已修复的 Supervisor release-failure 用例：

1. 给 stop recovery/cleanup retry 增加显式 entered/release/recovered barrier；
2. 故障解除后等待 durable runtime token clear；
3. 等待 `owned_binding_ids == ()` 后再调用第二次 shutdown；
4. `finally` 始终释放 barrier，但只在 ownership 已收敛时补做 shutdown；若主体和 cleanup 同时失败，应同时保留两个 cause；
5. 修复后重复运行该用例，并至少连续运行两轮 91 项同进程聚合。

该问题会让 M3 Review Gate 偶发红并削弱 shutdown retry 回归的可信度，定级为 **Standards Important**。

### 4.2 Important Gate（延续）：完整 backend suite 尚未全绿

`backend/CLAUDE.md` 的 TDD 规则要求变更前后运行完整 suite，`backend/CONTRIBUTING.md` 的 Before Submitting 也要求 `uv run pytest` 通过。

本轮为了严格排除未提交 M4 改动，只在 `cc4c3eae` 的干净快照执行定向与 M3 Gate，没有重新运行完整 backend suite。第二十四轮修复报告的最近证据仍是：

```text
1 failed, 785 passed, 15 skipped, 2 deselected

first remaining failure:
tests/test_client_live.py::TestLiveToolUse::test_agent_uses_bash_tool
WinError 5 on live bash working directory
```

此前两个 deselected 项是在当前 Windows 账户创建 symlink fixture 时失败；完整 M3 focused regression 另有 5 个未修改 LocalSandbox Windows/POSIX 兼容失败。这些不构成本轮 M3 生产 finding，但仓库级完成态仍不能声明全绿。

### 4.3 Minor（延续）：Channel/Supervisor/Repository 结构债务

- [feishu.py](../../../backend/app/channels/feishu.py#L1) 约 3334 行，混合 transport、消息/附件、cleanup outbox、scanner/process pool 与 cursor 状态。
- [supervisor.py](../../../backend/app/channels/supervisor.py#L293) 约 2069 行，混合 runtime lifecycle、health、secret/delete cleanup、janitor 与 shutdown ownership。
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L59) 约 1015 行，约 40 个 public async method，跨 secret、runtime lease/health、rotation、cleanup/delete 职责。
- Repository 继续以 `dict[str, Any]` 和 `agent_id/binding_id/owner_user_id/token/generation` 原语组表达多个生命周期操作。

该项属于 **Divergent Change / Data Clumps / Primitive Obsession** 判断项。建议在 M3 correctness Gate 关闭后独立引入 typed binding/runtime key/result，并拆分 cleanup scanner/store 与 runtime lifecycle；不要混入当前测试确定性修复。

### 4.4 Standards 轴汇总

```text
新增 Important：1（Gateway lifespan 聚合测试非确定性）
延续 Important Gate：1（完整 suite 未全绿）
延续 Minor：1（Channel/Supervisor/Repository 结构债务）
新增 Standards 生产代码 finding：0
```

README 与 CLAUDE 已同步第二十四轮 cursor deadline 行为；本轮未发现新增文档、类型或格式硬性违规。

---

## 5. 验证记录

### 5.1 干净提交快照

测试对象由 `git archive cc4c3eae` 生成。以下文件的快照 blob hash 与提交一致：

```text
backend/app/channels/feishu.py
backend/app/channels/supervisor.py
backend/tests/test_feishu_parser.py
backend/tests/test_feishu_supervisor.py
backend/CLAUDE.md
```

因此测试结果不包含当前工作区尚未提交的 M4 修改。

### 5.2 第二十四轮直接回归

```text
cursor transient success
cursor expired before first attempt
cursor retry sleep reaches deadline
cursor frozen logical clock
Supervisor release-failure shutdown retry

5 passed, 1 warning in 7.32s
```

### 5.3 Gateway lifespan 非确定性探针

```text
目标用例隔离重复 5 次：
5/5 passed

Repository + Router + Supervisor + Gateway lifespan 聚合首轮：
1 failed, 90 passed, 1 warning in 89.07s

相同聚合原样重跑：
91 passed, 1 warning in 71.02s
```

首轮失败发生在解除 stop 故障后的第二次 shutdown；等待 binding lifecycle lock 超过测试设置的 1 秒 deadline。首轮失败、隔离通过、原样聚合重跑通过的组合证明该 Gate 仍依赖调度时序。

### 5.4 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

172 passed, 5 warnings in 54.85s
```

正式五文件 Gate 不包含本轮失败的 `test_gateway_lifespan_shutdown.py`，因此其全绿不能关闭 4.1。

### 5.5 静态、格式、编译与差异检查

```text
ruff check <20 个 M3 Python 文件>
All checks passed!

ruff format --check <同 20 个文件>
20 files already formatted

python -m compileall <同 20 个文件>
passed

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe...cc4c3eae -- backend
passed
```

---

## 6. 尚未关闭的 Review / 发布 Gate

1. 为 Gateway lifespan stop-failure 回归增加显式 cleanup barrier，并在第二次 shutdown 前等待 durable token 与 process-local owner 收敛。
2. 修复后重复运行目标用例，并至少连续运行两轮 Repository + Router + Supervisor + Gateway lifespan 91 项同进程聚合。
3. 在 Linux CI 或具备 Windows symlink、POSIX shell 与可写 live-test 目录的环境执行完整 backend suite，取得全绿或完整分类结果。
4. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim reconciliation、failure-health epoch、shutdown retry 与 row-lock 事务语义。
5. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
6. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
7. Channel/Repository 职责拆分作为 correctness 合并后的独立架构任务处理。

---

## 7. 最终判定

第二十四轮 cursor deadline P2 已正确关闭；对应直接回归、正式五文件 M3 Gate、Ruff、格式、编译与差异检查均通过。本轮没有新的 Spec finding，也没有新的生产代码 Standards finding。

但 Gateway lifespan stop-failure 回归仍缺少与 Supervisor release-failure 用例等价的显式同步：隔离测试稳定通过，而同一 91 项聚合在两次运行间发生一次失败、一次全绿。该问题必须先修复，否则 M3 shutdown retry Review Gate 仍不可重复。

**第二十四轮 cursor 修复：Pass。**
**M3 聚合 Review Gate：Partial Pass。**
**仓库级 Ready to merge / production release：No。**

当前工作区尚未提交的 M4 改动不在本轮范围内，也不能用来覆盖或解释本轮 M3 结论。
