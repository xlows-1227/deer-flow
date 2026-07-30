# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十三轮代码复审

**状态：** 已复审；第二十二轮 Router 资源清理问题已关闭，Spec 轴无新 finding；仓库级完整测试 Gate、聚合测试稳定性及 2 个 Standards Minor 仍未关闭
**日期：** 2026-07-23

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第二十二轮复审：[2026-07-23-m3-feishu-channel-partial-code-twenty-second-review.md](./2026-07-23-m3-feishu-channel-partial-code-twenty-second-review.md)
- 第二十二轮修复报告：[2026-07-23-m3-feishu-channel-partial-twenty-second-review-fix-report.md](./2026-07-23-m3-feishu-channel-partial-twenty-second-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`044fa17489b1d064286b97ea88dee65ed08060fe`
- `HEAD` 相对固定点没有新增 commit；复审对象仍是固定点之后当前未提交的 backend 工作区
- 实际 diff：`git diff 044fa17489b1d064286b97ea88dee65ed08060fe -- backend`
- 本轮重点：第二十二轮指出的 6 处 Router 测试资源清理，以及修复中新引入的 `_RouterTestResources` / `router_test_resources`
- Spec 来源：开发计划 M3/F3.1-F3.2/M3 Review Gate、设计文档及 `backend/CLAUDE.md` Published Feishu 运行时不变量
- Standards 来源：`backend/AGENTS.md`、`backend/CLAUDE.md`、`backend/CONTRIBUTING.md` 与 code-review smell baseline
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和历史临时目录

---

## 1. 复审结论

第二十二轮指出的 6 处 Router 顺序清理已实质关闭：

- engine 在创建时立即交给 `router_test_resources.own_engine()`；
- Supervisor 在创建时立即交给 `router_test_resources.own_supervisor()`；
- fixture teardown 覆盖完整测试主体，断言或 route setup 失败不会绕过清理；
- `close()` 使用 `try/finally`，Supervisor shutdown 抛错时仍会执行 engine disposal；
- 旧的 6 处裸 `shutdown → dispose` 已全部移除；
- 新增直接回归锁定了 shutdown 失败时仍调用 dispose 的基本行为。

**Spec 轴：0 个 finding。** 未发现缺失/部分实现、scope creep 或看似实现但行为错误。

**Standards 轴：2 个 Important、2 个 Minor。**

1. 完整 backend suite 仍因范围外 auth 用例失败，仓库强制全绿 Gate 未关闭。
2. 本轮首次运行四文件聚合和正式五文件 Gate 时分别出现 SQLite 锁竞争与 Windows cursor replace 竞争；隔离及重跑均恢复全绿，说明 Gate 存在可观测的非确定性。
3. `_RouterTestResources.close()` 在 shutdown 与 dispose 同时失败时会由 dispose 异常覆盖更关键的 shutdown/ownership 异常，新增测试没有覆盖双失败。
4. `AgentChannelRepository` 与 `FeishuSupervisor` 继续承载过多职责，属于已登记的结构债务。

**第二十二轮针对性修复：Pass。仓库级 Ready to merge：No。** 原 Router finding 已关闭，但完整 suite 与聚合稳定性仍不满足仓库 Gate。

---

## 2. 第二十二轮问题关闭状态

| 第二十二轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| Standards Minor：Router 6 个测试的顺序清理不具备失败安全性 | **已关闭** | 6 个用例从资源创建起交给统一 async fixture；测试主体失败仍 teardown，shutdown 失败仍尝试 dispose |
| Standards Important：完整 backend suite 未全绿 | **未关闭（延续）** | fail-fast 仍为 `323 passed, 1 failed`，失败仍是 `test_csrf_does_not_exempt_old_login_path` |
| Standards Minor：`AgentChannelRepository` Divergent Change / Data Clumps / Primitive Obsession | **未关闭（已登记）** | 未在测试资源修复中混入生产仓储迁移，继续作为独立架构任务 |

---

## 3. Spec 轴

### 3.1 Router fixture 不改变 M3 生产契约

[test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py#L108) 的新增内容只管理测试侧 engine/Supervisor 生命周期，没有修改生产 runtime、Repository、owner API、SecretStore 或数据库 schema。

6 个迁移用例保留了原有业务断言，fixture 只取代测试尾部清理。Router 全文件、四文件聚合重跑和正式五文件重跑均通过，未发现资源 fixture 改变绑定创建、凭据探活、生命周期、crash recovery 或 delete fencing 行为。

### 3.2 M3 契约抽查

对开发计划 F3.1/F3.2 及 `backend/CLAUDE.md` Published Feishu 不变量抽查结果：

- 密钥仍只通过 SecretStore opaque ref 持久化；
- owner API 仍不回显明文凭据；
- 单 binding 生命周期继续与 peer 隔离；
- runtime claim、health epoch、quiescing ownership 和 shutdown fence 契约未被本轮测试修复改动；
- harness → app 依赖方向没有新增逆向导入。

### 3.3 Spec 轴汇总

```text
缺失或部分实现：0
Scope creep：0
实现错误：0
最严重 Spec finding：无
```

真实 PostgreSQL 与双 Feishu App 仍是发布 Gate，不能由 SQLite/fake transport 结果替代，但不计为本轮代码 finding。

---

## 4. Standards 轴

### 4.1 Important（延续）：完整 backend suite 仍未全绿

`backend/CLAUDE.md` 的 TDD 规则要求完整测试通过后功能才算完成；`backend/CONTRIBUTING.md` 的 Before Submitting 同样要求 `uv run pytest` 全绿。

本轮完整 backend fail-fast：

```text
FAILED tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
1 failed, 323 passed, 7 warnings in 95.24s
```

该失败不属于本轮 M3 diff，但它仍是仓库级硬 Gate。第二十二轮 Router 修复通过不能替代完整 suite 全绿。

### 4.2 Important：M3 聚合 Gate 存在可观测的非确定性

本轮首次执行四文件同进程聚合时：

```text
FAILED tests/test_feishu_supervisor.py::test_stop_failure_preserves_active_runtime_and_status
sqlite3.OperationalError: database is locked
1 failed, 89 passed, 1 warning in 101.88s
```

[test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L1747) 把 lease heartbeat 压到 `0.01s`、shutdown budget 压到 `0.05s`，随后在 quiescing cleanup retry 仍并发写库时直接调用 runtime health callback。此次 health commit 与后台 cleanup transaction 发生 SQLite 锁竞争。该用例隔离运行 3 次均通过，四文件聚合重跑为 `90 passed`，因此是时序相关 flake，而非稳定功能失败。

本轮首次执行正式五文件 Gate 时：

```text
FAILED test_global_cleanup_discovery_cursor_reaches_slow_directory_tail
FAILED test_cleanup_discovery_reaches_normal_job_after_ten_hung_paths
PermissionError: [WinError 5] ... .discovery-cursor-global.<uuid>.tmp
  -> .discovery-cursor-global
2 failed, 166 passed, 5 warnings in 66.96s
```

[feishu.py](../../../backend/app/channels/feishu.py#L681) 使用临时文件 `replace()` 推进 discovery cursor；本轮 Windows 聚合运行曾在该原子替换处失败。两个用例隔离重跑均通过，正式五文件重跑为 `168 passed`。

两组失败都不证明新的生产 Spec 缺陷，但它们证明当前 Gate 不是确定性全绿。建议：

- Supervisor stop-failure 回归改用明确 barrier 暂停 cleanup retry 的写事务，再触发 health projection，避免用 10ms/50ms 常量制造未受控竞争；
- cursor 写入抽取可单测的原子 helper，并在 Windows 对共享/扫描器短暂占用使用 deadline 内有界重试，或在测试中显式收敛 process-owned scanner 后再检查 cursor 公平性；
- 保留“首次失败 + 隔离结果 + 聚合重跑”记录，不能只报告最后一次绿跑。

### 4.3 Minor：shutdown 与 dispose 双失败时会覆盖原 ownership 异常

**相关文件：** [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py#L123)

当前实现：

```python
try:
    await self.supervisor.shutdown()
finally:
    await self.engine.dispose()
```

它正确保证了 dispose 一定被尝试；但如果 shutdown 与 dispose 都抛错，Python 最终向 pytest 暴露的是 dispose 异常，关键的 unresolved runtime ownership 只留在 exception context。第二十二轮修复报告声称“shutdown 原始异常继续传播”，而 [新增回归](../../../backend/tests/test_agent_channels_router.py#L145) 只覆盖 shutdown 失败、dispose 成功，没有锁定双失败语义。

建议捕获两个异常并使用 `ExceptionGroup` 或等价聚合，至少保证 shutdown/ownership 错误不会被数据库清理错误覆盖；增加 shutdown + dispose 同时失败的直接回归。这是测试诊断可靠性问题，不影响生产 M3 Spec 判定。

### 4.4 Minor（判断项）：Repository 与 Supervisor 继续存在 Divergent Change

- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L59) 约 1015 行，继续混合 secret ingest、runtime lease/health、credential rotation、cleanup 与 delete，并反复传播 binding/owner/token/generation 参数组，属于 **Divergent Change / Data Clumps / Primitive Obsession**。
- [supervisor.py](../../../backend/app/channels/supervisor.py#L293) 约 2069 行，同时承担 runtime、health、secret cleanup、janitor、delete 与 shutdown ownership，属于 **Divergent Change** 判断项。

建议在 M3 correctness 合并后独立引入 typed binding/runtime key/result，并按 runtime lifecycle、health projection、secret/delete cleanup 拆分；不要把大范围重构混入当前 fencing 修复。

### 4.5 Standards 轴汇总

```text
Important：2（完整 suite Gate；聚合测试非确定性）
Minor：2（双失败异常覆盖；Repository/Supervisor 结构债务）
最严重 Standards finding：仓库完整 suite 与聚合稳定性 Gate 均未关闭
```

---

## 5. 验证记录

### 5.1 Router 全文件

```text
9 passed, 1 warning in 14.86s
```

旧的 6 处裸 `await supervisor.shutdown()` 后紧跟 `await engine.dispose()` 的多行检索为 0。

### 5.2 Repository + Router + Supervisor + Gateway lifespan 聚合

首次：

```text
1 failed, 89 passed, 1 warning in 101.88s
failed: test_stop_failure_preserves_active_runtime_and_status
```

失败用例隔离重复：

```text
3/3 passed
```

聚合重跑：

```text
90 passed, 1 warning in 93.14s
```

### 5.3 正式五文件 M3 Gate

首次：

```text
2 failed, 166 passed, 5 warnings in 66.96s
failed: 2 个 cleanup discovery cursor Windows replace 用例
```

失败用例隔离重跑：

```text
2 passed, 1 warning in 3.20s
```

正式 Gate 重跑：

```text
168 passed, 5 warnings in 56.41s
```

### 5.4 完整 backend suite（fail-fast）

```text
1 failed, 323 passed, 7 warnings in 95.24s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

### 5.5 静态、格式、编译与差异检查

```text
ruff check --no-cache tests/test_agent_channels_router.py
All checks passed!

ruff format --check --no-cache tests/test_agent_channels_router.py
1 file already formatted

python -m compileall tests/test_agent_channels_router.py
passed

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe -- backend
passed
```

---

## 6. 尚未关闭的环境 / 发布 Gate

1. 关闭 `test_csrf_does_not_exempt_old_login_path`，使完整 backend suite 全绿。
2. 将 Supervisor SQLite 并发回归和 Windows discovery cursor 回归改成确定性 Gate，并在同进程聚合中重复验证。
3. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim reconciliation、failure-health epoch 与 shutdown retry 的真实事务语义。
4. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
5. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
6. Repository/Supervisor 职责拆分作为合并后的独立架构任务处理。

---

## 7. 最终判定

第二十二轮指出的 Router 资源清理问题已关闭：6 个用例从资源创建起由统一 async fixture 管理，测试主体失败会进入 teardown，Supervisor shutdown 失败不会跳过 engine disposal。Spec 轴没有新的 actionable finding。

Standards 轴仍有 2 个 Important 与 2 个 Minor。尤其是本轮在真实执行中分别观察到 SQLite lock 与 Windows cursor replace 的首次聚合失败，即使隔离及重跑恢复全绿，也不能把当前 Gate 描述为确定性通过。

**第二十二轮针对性修复：Pass。**
**仓库级 Ready to merge：No。** 在 auth 全量失败、M3 聚合非确定性及发布环境 Gate 关闭前，不应声明整个工作区已满足合并条件。
