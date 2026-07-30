# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十六轮代码复审

**状态：** 已复审；第二十五轮正常恢复路径的调度竞态已关闭，但两个异常 cleanup 分支仍会在 ownership 未收敛时手工释放 leader fence；仓库完整测试与发布 Gate 未关闭
**日期：** 2026-07-24

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第二十五轮复审：[2026-07-24-m3-feishu-channel-partial-code-twenty-fifth-review.md](./2026-07-24-m3-feishu-channel-partial-code-twenty-fifth-review.md)
- 第二十五轮修复报告：[2026-07-24-m3-feishu-channel-partial-twenty-fifth-review-fix-report.md](./2026-07-24-m3-feishu-channel-partial-twenty-fifth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`cc4c3eaeb6f0abc5d6c8a2314005edc32014fa9c`
- `HEAD` 之后无新增 commit；第二十五轮修复位于未提交工作区
- 本轮可执行 diff：`git diff HEAD -- backend/tests/test_gateway_lifespan_shutdown.py backend/tests/test_feishu_supervisor.py`
- 修复规模：两个测试文件，`185 insertions(+), 19 deletions(-)`
- 测试方式：从 `cc4c3eae` 生成 Git archive，并只叠加上述两个测试文件的 patch；快照文件 hash 与工作区逐项一致
- Spec 来源：开发计划 M3/F3.1-F3.4/M3 Review Gate、设计文档、第二十五轮 Review/修复报告及 `backend/CLAUDE.md`
- Standards 来源：`backend/AGENTS.md`、`backend/CLAUDE.md`、`backend/CONTRIBUTING.md` 与 code-review smell baseline
- 排除：工作区内所有 M4 变更，尤其 `backend/app/channels/supervisor.py`、Published Agent owner API、usage/quota/audit、frontend、`config.yaml` 和 M4 验收文件

---

## 1. 复审结论

第二十五轮指出的自然调度竞态在正常恢复路径上已经关闭：

- Gateway lifespan 用例显式控制 cleanup retry 的 entered/release/recovered 三阶段；
- 两个同根因 Supervisor 用例也增加了对应 barrier；
- 第二次 shutdown 前等待 transport stop、durable runtime token clear 与 process-local owner clear；
- 测试主体错误与实际 cleanup 异常可通过 `BaseExceptionGroup` 同时保留；
- 目标用例连续重复、91 项同进程聚合连续两轮及正式五文件 M3 Gate 全绿。

本轮 **Spec 轴无 finding**。修复没有改变生产实现、放宽 ownership/fencing 断言或引入范围外行为。

Standards 轴发现 **1 个新的 Important**：Gateway lifespan 用例和 quiescing transport Supervisor 用例在 `finally` 等待 owner 收敛失败时执行 `await fence.release()`，没有把未收敛 ownership 记录为 cleanup error。这样会在 Supervisor 仍持有 binding、`_shutdown_complete` 仍为 `False` 时人为解除 leader fence；Gateway 用例随后还会 dispose engine。该分支违反 `backend/CLAUDE.md` 刚固化的“未解决 ownership 不释放 fence”“cleanup 只在 ownership 收敛后 shutdown”“主体与 cleanup 双错误同时保留”契约。

另有 **1 个 Minor 判断项**：entered/release/recovered、token/owner 轮询和错误聚合在两个文件中大段重复，并已出现三个同根因用例中一个正确、两个错误的分支漂移。

**第二十五轮正常恢复路径：Pass。第二十五轮完整 Standards Important：Partial Pass。M3 聚合正常路径 Gate：Pass。仓库级 Ready to merge：No。**

---

## 2. 第二十五轮问题关闭状态

| 第二十五轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| Gateway lifespan stop-failure 回归依赖后台调度时序 | **正常路径已关闭** | entered/release/recovered barrier 生效；token 与 owner 收敛后才 retry shutdown；目标用例连续 5 次通过 |
| `finally` 可能覆盖主体失败 | **部分关闭** | 实际抛出的 cleanup 异常会参与 `BaseExceptionGroup`；但 owner 未收敛时只手工 release fence，不生成 cleanup error |
| Supervisor quiescing transport 用例立即 retry shutdown | **正常路径已关闭，异常 cleanup 未关闭** | barrier 与收敛等待正确；未收敛分支仍手工释放 fence |
| Active runtime stop-failure 用例立即 retry | **已关闭** | 正常路径等待 recovery/token/owner；cleanup 未收敛时明确抛 `AssertionError`，没有手工释放 fence |
| 完整 backend suite 未全绿 | **未关闭，平台/环境 Gate** | 本轮未修改 symlink、LocalSandbox 或 live bash 环境 |
| Channel/Supervisor/Repository 结构债务 | **未关闭（已登记）** | 测试同步修复未混入生产模块拆分 |

---

## 3. Spec 轴

### 3.1 第二十五轮行为要求复核

第二十五轮 Review 要求：

1. cleanup retry 使用 entered/release/recovered barrier；
2. 第二次 shutdown 前等待 durable runtime token 与 process-local owner 收敛；
3. 首次 shutdown 失败时仍保留 owner 与 leader fence；
4. `finally` 始终释放测试 barrier；
5. 主体与 cleanup 同时失败时不能互相覆盖。

Gateway 用例在 [test_gateway_lifespan_shutdown.py](../../../backend/tests/test_gateway_lifespan_shutdown.py#L287) 中完整实现前三项正常路径要求：首次 lifespan shutdown 后验证 `_shutdown_complete is False`、owner 仍存在、fence 仍持有；故障恢复后依次等待 recovered、durable token clear 与 owner clear，最后才调用同一 Supervisor 的 shutdown retry。

两个 Supervisor 用例在 [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L890) 和 [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L1839) 中保留相同生产不变量。额外修复这两个用例来自 91 项聚合暴露的同根因，不构成 scope creep。

### 3.2 Spec 轴汇总

```text
缺失或部分实现：0
Scope creep：0
实现错误：0
最严重 Spec finding：无
```

本轮 finding 位于测试 teardown 对仓库 fencing 标准的违反，不重复登记为生产 Spec finding。

---

## 4. Standards 轴

### 4.1 Important：ownership 未收敛时 cleanup 手工释放 leader fence

**相关文件：**

- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L977)
- [test_gateway_lifespan_shutdown.py](../../../backend/tests/test_gateway_lifespan_shutdown.py#L424)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L555)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L561)

两个用例的 cleanup 都采用以下结构：

```python
if supervisor.owned_binding_ids == () and not supervisor._shutdown_complete:
    await supervisor.shutdown()
elif fence.held:
    await fence.release()
```

当 100 次轮询后 owner 仍未清除时，第二个分支会：

1. 跳过 Supervisor shutdown；
2. 不把“ownership 未收敛”加入 `cleanup_errors`；
3. 在 owner 仍存在时直接释放 leader fence；
4. 使最终错误列表只包含测试主体错误，掩盖独立的 cleanup/fencing 失败。

Gateway 用例之后无条件尝试 `engine.dispose()`；如果后台 `_retry_local_runtime_stop()` 仍在访问 repository，还可能把活跃 cleanup owner 留给已 dispose 的数据库引擎。

这与生产及测试文档契约冲突：

- `backend/CLAUDE.md` 规定 unresolved ownership 必须抛错，不能标记 shutdown complete，也不能释放 leader fence；
- 同一文件又明确规定 stop/release-failure cleanup 只在 ownership 收敛后调用 shutdown，并在主体与 cleanup 同时失败时聚合两个错误；
- 第二十五轮修复报告 §3.3 也声明 ownership 未收敛必须作为 cleanup 失败保留。

同一提交范围内的 `test_stop_failure_preserves_active_runtime_and_status` 已采用正确模式：

```python
elif supervisor.owned_binding_ids != ():
    raise AssertionError(
        "Supervisor ownership did not converge during test cleanup"
    )
```

建议让另外两个用例使用相同 fail-closed 分支：owner 未收敛时抛出明确 `AssertionError` 并追加到 `cleanup_errors`，禁止手工 release fence。Gateway 仍应在独立 `try` 中尝试 dispose engine，以便主体、ownership cleanup 和 dispose 错误都可见。

该问题会让失败路径伪造“fence 已清理”状态、隐藏 cleanup 根因并可能污染后续聚合测试，定级为 **Standards Important**。

### 4.2 Minor：并发测试支撑逻辑出现 Duplicated Code

**相关文件：**

- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L919)
- [test_gateway_lifespan_shutdown.py](../../../backend/tests/test_gateway_lifespan_shutdown.py#L287)

两个文件重复了约 70 行同形逻辑：

- stop attempt 计数；
- entered/release/recovered 三事件；
- durable token 与 local owner 轮询；
- retry shutdown 条件；
- test/cleanup error 收集与 `BaseExceptionGroup` 构造。

重复已经产生可观察的规则漂移：active runtime 用例在 owner 未收敛时抛错，另外两个用例却手工 release fence。建议提取测试专用的小型 barrier/controller 与 cleanup helper，使三个业务场景继续保留独立断言，但共享同一 fencing-safe teardown。

该项属于 **Duplicated Code** 判断项，不是硬性违规；可与 Important 一起小范围整理，也可在正确性修复后单独处理。

### 4.3 Important Gate（延续）：完整 backend suite 尚未全绿

`backend/CLAUDE.md` 的 TDD 规则和 `backend/CONTRIBUTING.md` 的 Before Submitting 要求完整 `uv run pytest` 通过。

本轮为排除 M4 污染，只在“`cc4c3eae` + 两个 M3 测试 patch”的快照运行定向与 M3 Gate，没有重新执行完整 backend suite。最近完整分类仍来自第二十四轮修复报告：两个 Windows symlink fixture 被 deselect 后，suite 达到 `785 passed, 15 skipped`，随后在依赖真实模型、本机 bash 与目录权限的 live test 发生 `WinError 5`。完整 M3 focused regression 另有 5 个未修改 LocalSandbox Windows/POSIX 失败。

这些环境失败不构成本轮 M3 生产代码 finding，但仓库完成态仍不能声明全绿。

### 4.4 Minor（延续）：Channel/Supervisor/Repository 结构债务

`feishu.py`、`supervisor.py` 与 `AgentChannelRepository` 继续存在此前登记的 Divergent Change、Data Clumps 与 Primitive Obsession。该债务不应混入本轮测试 teardown 修复；在 M3 correctness Gate 关闭后独立拆分 cleanup scanner/store、runtime lifecycle 与 typed binding/runtime key/result。

### 4.5 Standards 轴汇总

```text
新增 Important：1（owner 未收敛时手工释放 fence）
新增 Minor：1（并发测试支撑逻辑重复）
延续 Important Gate：1（完整 suite 未全绿）
延续 Minor：1（Channel/Supervisor/Repository 结构债务）
新增生产代码 finding：0
```

---

## 5. 验证记录

### 5.1 干净合成快照

测试基线为 `cc4c3eae` 的 Git archive，仅叠加：

```text
backend/tests/test_feishu_supervisor.py
backend/tests/test_gateway_lifespan_shutdown.py
```

两个快照文件的 blob hash 分别与当前工作区一致：

```text
test_feishu_supervisor.py:
96a0954978538e47bb4ac49c9fa0b1ca3fb82a4d

test_gateway_lifespan_shutdown.py:
498333614c0149d3a8d3d3cbb56ffc1527e7a6c6
```

生产 `supervisor.py` 及其他 M4 工作区差异均未进入测试。

### 5.2 Shutdown 竞态定向回归

```text
test_shutdown_remains_retryable_while_quiescing_transport_cannot_stop
test_stop_failure_preserves_active_runtime_and_status
test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner

3 passed, 1 warning in 11.15s
```

Gateway 目标用例隔离重复：

```text
5/5 passed
```

这些测试覆盖正常 recovery 分支，不会进入 owner 永不收敛的错误 cleanup 分支，因此不能关闭 4.1。

### 5.3 Repository + Router + Supervisor + Gateway lifespan 聚合

```text
round 1: 91 passed, 1 warning in 65.77s
round 2: 91 passed, 1 warning in 67.70s
```

连续两轮证明第二十五轮的正常调度竞态已关闭。

### 5.4 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

172 passed, 5 warnings in 40.49s
```

### 5.5 静态、格式、编译与差异检查

```text
ruff check <两个修复测试文件>
All checks passed!

ruff format --check <两个修复测试文件>
2 files already formatted

python -m compileall <两个修复测试文件>
passed

git diff --check HEAD -- <两个修复测试文件>
passed

git diff --check -- <第二十五轮修复报告>
passed
```

---

## 6. 尚未关闭的 Review / 发布 Gate

1. 删除两个 cleanup 分支中的手工 `fence.release()`；owner 未收敛时抛出明确 cleanup error，并与主体错误一起保留。
2. 最好增加或抽取可直接验证“owner 未收敛时不得 release fence”的测试 helper/负向回归，避免仅靠正常 recovery 测试。
3. 修复后重跑三个 shutdown 竞态用例、连续两轮 91 项同进程聚合及正式五文件 M3 Gate。
4. 在 Linux CI 或具备 Windows symlink、POSIX shell 与可写 live-test 目录的环境执行完整 backend suite。
5. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 runtime claim/release、shutdown retry 与 row-lock 事务语义。
6. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
7. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
8. Channel/Supervisor/Repository 职责拆分作为 correctness 合并后的独立架构任务处理。

---

## 7. 最终判定

第二十五轮修复已经消除正常恢复路径的自然调度竞态：目标重复、连续两轮 91 项聚合和正式 M3 Gate 均全绿。本轮 Spec 轴没有 finding，生产实现也没有纳入新的修改。

但两个异常 cleanup 分支仍会在 Supervisor 保留 process-local owner 时手工释放 leader fence，并且不把 ownership 未收敛作为第二个 cleanup error。该行为违反 M3 fail-closed fencing 契约，也与第三个同根因测试的正确 cleanup 实现不一致。

**第二十五轮正常恢复路径：Pass。**
**第二十五轮完整 Standards Important：Partial Pass。**
**M3 聚合正常路径 Gate：Pass。**
**仓库级 Ready to merge / production release：No。**

当前 M4 工作区变更不在本轮范围内，不能用于覆盖或解释本轮 M3 结论。
