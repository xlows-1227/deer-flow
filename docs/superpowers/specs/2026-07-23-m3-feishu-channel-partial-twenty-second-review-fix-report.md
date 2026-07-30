# 多租户 Agent 发布平台 - M3 飞书渠道部分第二十二轮 Review 修复报告

**日期：** 2026-07-23

**关联 Review：** [2026-07-23-m3-feishu-channel-partial-code-twenty-second-review.md](./2026-07-23-m3-feishu-channel-partial-code-twenty-second-review.md)

**开发计划：** [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第二十二轮 Spec 轴没有新的 finding；本轮指出的 Router 测试资源清理 Minor 已修复并完成回归。当前没有已知、仍未修复的第二十二轮 M3 P1/P2。完整 backend suite 的范围外 auth 失败和 `AgentChannelRepository` 结构债务仍是未关闭 Gate。

---

## 1. 修复结论

| Finding | 结果 | 修复 | 回归证据 |
|---|---|---|---|
| Standards Minor：Router 中 6 个测试使用裸 `await supervisor.shutdown()` 后顺序执行 `await engine.dispose()`，断言或 shutdown 失败会跳过资源清理 | 已修复 | 新增 `_RouterTestResources` 与 `router_test_resources` async fixture。每个 engine 在创建时立即登记，每个 Supervisor 在实例化时立即登记；fixture teardown 覆盖完整测试主体，并用嵌套 `try/finally` 保证 shutdown 抛错时仍执行 engine disposal。6 个用例全部迁移并移除顺序清理。 | 新增 shutdown 失败仍 dispose 的直接回归；迁移用例 7 项通过，Router 文件 9 项全绿，四文件同进程聚合 90 项通过。多行静态检索确认不再存在不安全的裸顺序。 |
| Standards Important：完整 backend suite 未全绿 | 未关闭，范围外基线 | 未修改 auth 路径。 | fail-fast 为 `323 passed, 1 failed`；首个失败仍是 `test_csrf_does_not_exempt_old_login_path`。 |
| Standards Minor：`AgentChannelRepository` Divergent Change / Data Clumps / Primitive Obsession | 已登记，未混改 | 本轮没有生产 Spec finding，不在测试可靠性修复中引入仓储大迁移。`BindingKey`、typed row/result 和职责拆分继续作为 M3 合并后的独立任务。 | 避免扩大 fencing correctness 变更面。 |

---

## 2. 测试资源生命周期不变量

### 2.1 资源从创建时开始受 fixture 管理

- 数据库 engine 创建后立即通过 `own_engine()` 登记，后续 schema、session、route setup 或断言失败都不会绕过 fixture teardown。
- Supervisor 创建后立即通过 `own_supervisor()` 登记，测试主体不再负责尾部顺序清理。
- pytest-asyncio fixture 的 teardown 覆盖完整测试调用，因此 HTTP 断言、Repository 断言或辅助恢复步骤抛错后仍会进入资源回收。

### 2.2 shutdown 失败不能跳过数据库回收

- `_RouterTestResources.close()` 首先尝试 `FeishuSupervisor.shutdown()`。
- engine disposal 位于 `finally`，因此 unresolved runtime ownership 导致 shutdown 抛错时，数据库连接池仍会释放。
- shutdown 原始异常继续传播，不会把未收敛 ownership 伪装成成功。
- 直接回归记录调用顺序为 `shutdown → dispose`，并断言 shutdown 异常仍对测试可见。

### 2.3 同进程聚合保持确定性

- Router module 继续在模块边界停止 process-owned attachment scanner。
- slow-writer 用例原有的 barrier release、request task join 和嵌套清理保持不变。
- 其余 6 个 Router 用例不再依赖正常走到测试函数末尾，因此失败复现不会遗留 engine、Supervisor task 或跨 event-loop 状态。

---

## 3. 主要代码变更

- `backend/tests/test_agent_channels_router.py`
  - 新增统一 `_RouterTestResources` owner 和 pytest-asyncio fixture。
  - 新增 Supervisor shutdown 失败时 engine 仍 dispose 的回归测试。
  - 将第二十二轮指出的 6 个 Router 用例迁移到 fixture，并删除裸顺序清理。
- `backend/README.md`
  - 明确 Router engine/Supervisor 从创建时登记，并由 fixture 执行失败安全 teardown。
- `backend/CLAUDE.md`
  - 同步 Router 聚合测试的 async resource ownership 与嵌套清理契约。

本轮没有修改生产 runtime、Repository、Router endpoint 或数据库 schema。

---

## 4. 自动化验证

### 4.1 红绿证据

修复前静态审计在 Router 文件中确认 6 处不安全顺序：

```text
411-412
462-463
524-525
581-582
659-660
719-720
```

修复后：

```text
shutdown 失败仍 dispose + 6 个迁移用例：
7 passed, 1 warning in 12.91s

Router 文件全量：
9 passed, 1 warning in 13.76s

裸 shutdown/dispose 多行检索：0 处
```

### 4.2 Repository + Router + Supervisor + Gateway lifespan 聚合

```text
90 passed, 1 warning in 87.01s
```

命令正常退出，没有 scanner、event-loop、Supervisor task 或 engine teardown 挂起。

### 4.3 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

168 passed, 5 warnings in 67.95s
```

### 4.4 完整 backend suite（fail-fast）

```text
323 passed, 1 failed, 7 warnings in 91.96s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

新增 Router cleanup 回归在 auth 失败之前通过。首个失败与前轮 Review 基线一致，属于本轮未修改的 auth 行为；本报告不把仓库级全绿 Gate 误报为通过。

### 4.5 静态、格式、编译与差异检查

```text
ruff check --no-cache tests/test_agent_channels_router.py: All checks passed!
ruff format --check --no-cache tests/test_agent_channels_router.py: 1 file already formatted
PYTHONPYCACHEPREFIX=<workspace temp> python -m compileall tests/test_agent_channels_router.py: passed
```

默认 `tests/__pycache__` 在当前 Windows 账户下不可写，因此 compileall 使用工作区临时 pycache 路径；编译本身正常通过。

---

## 5. 尚未关闭的环境 / 发布 Gate

1. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`，使仓库完整 backend suite 全绿。
2. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，继续验证 claim reconciliation、failure-health epoch 和 shutdown retry 的真实事务语义。
3. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 和 attachment recovery。
4. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
5. `AgentChannelRepository` 的 `BindingKey`、typed row/result 和 runtime/secret/delete 职责拆分作为 M3 合并后的独立架构任务处理。

---

## 6. 最终判定

**第二十二轮指出的 Router 测试资源清理 Minor 已关闭；当前没有已知、仍未修复的第二十二轮 M3 P1/P2。**

6 个 Router 用例现在从 engine/Supervisor 创建时就由统一 async fixture 接管。测试主体断言失败会进入 teardown，Supervisor shutdown 抛出 unresolved ownership 时也不会跳过 engine disposal；原始 shutdown 错误仍会传播，避免掩盖真实 fencing 问题。

第二十二轮 M3 代码修复可以进入下一轮复审。仓库级 Ready to merge 仍为 **No**：必须先关闭范围外 auth 全量失败，并完成 PostgreSQL、真实双 Feishu App 等发布 Gate。
