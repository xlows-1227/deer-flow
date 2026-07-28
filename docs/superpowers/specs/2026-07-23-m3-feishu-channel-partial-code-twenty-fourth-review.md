# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第二十四轮代码复审

**状态：** 已复审；第二十三轮 Supervisor、Router 与 auth 修复已关闭，cursor retry 仍有 1 个 P2 deadline 边界错误；仓库完整测试与发布 Gate 未关闭
**日期：** 2026-07-23

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第二十三轮复审：[2026-07-23-m3-feishu-channel-partial-code-twenty-third-review.md](./2026-07-23-m3-feishu-channel-partial-code-twenty-third-review.md)
- 第二十三轮修复报告：[2026-07-23-m3-feishu-channel-partial-twenty-third-review-fix-report.md](./2026-07-23-m3-feishu-channel-partial-twenty-third-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`044fa17489b1d064286b97ea88dee65ed08060fe`
- `HEAD` 相对固定点仍无新增 commit；复审对象是固定点之后当前未提交的 backend 工作区
- 实际 diff：`git diff 044fa17489b1d064286b97ea88dee65ed08060fe -- backend`
- 本轮重点：第二十三轮指出的 Supervisor/SQLite race、Windows cursor replace、Router teardown 双失败与 auth 首失败
- Spec 来源：开发计划 M3/F3.1-F3.2/M3 Review Gate、设计文档及 `backend/CLAUDE.md` Published Feishu 不变量
- Standards 来源：`backend/AGENTS.md`、`backend/CLAUDE.md`、`backend/CONTRIBUTING.md` 与 code-review smell baseline
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和历史临时目录

---

## 1. 复审结论

第二十三轮的四组针对性处理结果：

- **Supervisor/SQLite 聚合不稳定：已关闭。** stop-failure 回归使用明确 barrier 控制 cleanup retry 与 health callback 顺序，`finally` 释放屏障并重试 shutdown；四文件聚合本轮一次通过。
- **Router teardown 双失败：已关闭。** shutdown 与 dispose 都失败时通过 grouped exception 同时保留两个 cause，单失败仍原样传播。
- **auth 首失败：已关闭。** `/api/v1/auth/login` 是生产代码中真实存在的统一首次登录入口，AuthMiddleware 与 CSRFMiddleware 本来就把它列为 exact public/exempt path；修改测试是在纠正陈旧断言，不是删除安全边界。
- **Windows cursor replace：部分关闭。** transient `PermissionError` 已有有界 retry 和成功路径回归，但 helper 在第一次或下一次 `replace()` 前不重新检查 deadline，仍可在预算耗尽后发布 durable cursor。

**Spec 轴：1 个 P2。** `_replace_cleanup_state_with_deadline()` 允许 deadline 之后的 late cursor publication。

**Standards 轴：1 个 Important、2 个 Minor。**

1. 完整 backend suite 仍未全绿，当前本机首先被 Windows symlink privilege 阻塞。
2. cursor helper 缺少 deadline 已过及 wall-deadline 耗尽的负向回归。
3. `feishu.py`、`supervisor.py` 与 `AgentChannelRepository` 的结构债务继续登记。

**第二十三轮修复：Partial Pass。仓库级 Ready to merge：No。** 必须先关闭 cursor deadline P2；完整 suite、PostgreSQL 与真实双 Feishu App Gate 仍需完成。

---

## 2. 第二十三轮问题关闭状态

| 第二十三轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| Important：Supervisor stop-failure 回归出现 SQLite lock race | **已关闭** | 显式 barrier 控制 cleanup retry/health callback 顺序；直接回归及 91 项聚合通过 |
| Important：Windows discovery cursor replace 偶发 `PermissionError` | **部分关闭** | transient retry 成功路径通过，但 deadline 前置/重试边界仍错误，详见 Spec P2 |
| Minor：Router shutdown + dispose 双失败时覆盖 ownership 异常 | **已关闭** | `BaseExceptionGroup` 同时保留两个异常；单失败保持原类型 |
| Important：完整 suite 首先失败于陈旧 auth 断言 | **auth 首失败已关闭；完整 Gate 未关闭** | 生产统一登录路由与 middleware 契约证明测试原断言错误；auth 两文件 98 项通过；完整 suite 继续运行到 symlink 权限失败 |
| Minor：Repository/Supervisor 结构债务 | **未关闭（已登记）** | 未在 correctness 修复中混入职责拆分 |

---

## 3. Spec 轴

### 3.1 P2：cursor helper 仍可能在 deadline 到期后成功发布

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L80)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L948)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L534)

`backend/CLAUDE.md` 要求 discovery cursor replacement 同时受调用方逻辑 deadline 与 monotonic wall deadline 约束。当前 helper：

```python
while True:
    try:
        source.replace(target)
        return
    except PermissionError:
        remaining = min(
            deadline - clock(),
            wall_deadline - time.monotonic(),
        )
        if remaining <= 0:
            raise
        time.sleep(min(RETRY_SECONDS, remaining))
```

存在两个越界路径：

1. `_scan_all_cleanup_jobs()` 写临时文件后才调用 helper；如果临时文件写入已耗尽预算，helper 仍会先执行一次 `replace()`，成功时完全不检查 deadline。
2. `PermissionError` 后若 sleep 恰好消耗全部 remaining，下一轮仍直接执行 `replace()`，没有在 retry 前重新检查两个 deadline。

本轮使用确定性探针把 `deadline=0.5`、`clock()=1.0`，即 helper 入口时预算已经过期；结果仍为：

```text
replace_after_expired_deadline_attempts=1
```

这不只是测试计时问题。生产调用在 `asyncio.to_thread()` 中运行，外层 `wait_for()` 超时不能终止 worker thread；因此调用方已经返回 timeout 后，旧 worker 仍可能晚到修改 `.discovery-cursor-global`，影响下一轮扫描的 durable 起点。

现有回归只覆盖“两次 `PermissionError`，第三次在预算内成功”，没有覆盖：

- helper 入口时 deadline 已过；
- sleep 推进到 deadline 后不得再次 `replace()`；
- frozen logical clock 下由 wall deadline 终止永久 `PermissionError`。

建议在每次 `replace()` 前同时检查逻辑与 wall deadline；首次尝试前已超时应抛明确 `TimeoutError`，已有 `PermissionError` 的 retry 耗尽时应保留最后一个文件错误或按统一契约转换。新增确定性负向测试，断言 deadline 后 attempt count 不再增加、target 不被发布。

### 3.2 其余 Spec 抽查

- Supervisor barrier 仍验证 stop failure 保留 active desired status、quiescing ownership、首次 shutdown 失败及同一 Supervisor 后续重试成功，没有通过移除并发行为换绿。
- Router teardown 始终尝试 shutdown 与 dispose，双失败均可见。
- `/api/v1/auth/login` 在 [auth.py](../../../backend/app/gateway/routers/auth.py#L448) 中是现行统一登录端点，并被 Auth/CSRF middleware 精确豁免；测试修改符合真实生产契约。
- 本轮未发现其他缺失/部分实现、scope creep 或实现错误。

### 3.3 Spec 轴汇总

```text
缺失或部分实现：0
Scope creep：0
实现错误：1（P2 cursor deadline late publication）
最严重 Spec finding：P2
```

---

## 4. Standards 轴

### 4.1 Important（延续）：完整 backend suite 仍未全绿

`backend/CLAUDE.md` 的 TDD 规则与 `backend/CONTRIBUTING.md` 的 Before Submitting 均要求完整 `uv run pytest` 全绿。

本轮完整 fail-fast 已越过原 auth 失败：

```text
1 failed, 409 passed, 3 skipped, 10 warnings in 130.67s

failed:
tests/test_channel_file_attachments.py::
  TestInboundFileIngestion::
  test_rejects_preexisting_symlink_destination

setup:
OSError: [WinError 1314] 客户端没有所需的特权
```

临时 deselect 该用例后，下一个失败仍是同类平台问题：

```text
1 failed, 409 passed, 3 skipped, 1 deselected, 10 warnings in 129.50s

failed:
test_rejects_dangling_symlink_destination

setup:
OSError: [WinError 1314] 客户端没有所需的特权
```

两项都在创建测试 symlink fixture 时失败，尚未进入业务代码；它们不构成本轮 M3 生产 finding，但当前 Windows 账户不能提供仓库全绿证据。第一个失败被排除后仍未执行到 suite 尾部，因此也不能据此声明其他未归因失败已全部消失。

### 4.2 Minor：cursor deadline 的耗尽分支缺少负向测试

[test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L948) 只验证 transient `PermissionError` 后成功，没有锁定 helper 声明的双 deadline 语义。该缺口直接允许 3.1 的 P2 通过聚焦 Gate。

建议至少增加：

- expired-before-first-attempt；
- retry sleep reaches deadline；
- frozen logical clock + advancing wall clock；
- target 在 timeout 后保持未发布。

这是测试完整性判断项；生产行为错误仍归入独立 Spec 轴，不在 Standards 轴重复定级。

### 4.3 Minor（延续）：Channel/Repository 模块继续存在 Divergent Change

- [feishu.py](../../../backend/app/channels/feishu.py#L1) 约 3321 行，同时承担 transport、消息/附件处理、cleanup outbox、scanner/process pool 与 cursor 状态。
- [supervisor.py](../../../backend/app/channels/supervisor.py#L293) 约 2069 行，同时承担 runtime lifecycle、health、secret/delete cleanup、janitor 与 shutdown ownership。
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L59) 约 1015 行，继续混合 secret ingest、runtime lease/health、credential rotation、cleanup/delete；binding/owner/token/generation 参数组仍属于 Data Clumps / Primitive Obsession。

建议在 M3 correctness 合并后独立拆分 cleanup store/scanner、runtime lifecycle 与 typed binding/runtime key/result，不要混入当前 deadline 修复。

### 4.4 Standards 轴汇总

```text
Important：1（完整 suite Gate）
Minor：2（deadline 负向测试缺口；结构债务）
最严重 Standards finding：完整 backend suite 未全绿
```

README 与 CLAUDE 已同步第 23 轮行为；Ruff 等自动化检查未发现新的文档标准违规。

---

## 5. 验证记录

### 5.1 本轮直接回归

```text
Router 单失败 + 双失败
cursor transient retry + slow tail + hung paths
Supervisor barrier
unified login CSRF

7 passed, 1 warning in 12.70s
```

### 5.2 Repository + Router + Supervisor + Gateway lifespan 聚合

```text
91 passed, 1 warning in 85.60s
```

没有再次出现 SQLite `database is locked`、event-loop teardown 或 Supervisor ownership 挂起。

### 5.3 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

169 passed, 5 warnings in 54.53s
```

### 5.4 Auth 契约

```text
tests/test_auth_type_system.py
tests/test_auth_middleware.py

98 passed, 5 warnings in 41.48s
```

### 5.5 完整 backend suite

```text
fail-fast:
1 failed, 409 passed, 3 skipped, 10 warnings in 130.67s
first failure: preexisting symlink fixture WinError 1314

deselect first symlink test, fail-fast:
1 failed, 409 passed, 3 skipped, 1 deselected, 10 warnings in 129.50s
next failure: dangling symlink fixture WinError 1314
```

### 5.6 Deadline 确定性探针

```text
deadline=0.5
clock()=1.0

replace_after_expired_deadline_attempts=1
```

期望为 0；该结果直接复现 3.1 的 deadline 前置检查缺失。

### 5.7 静态、格式、编译与差异检查

```text
ruff check --no-cache <5 个本轮相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 5 个文件>
5 files already formatted

python -m compileall <同 5 个文件>
passed

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe -- backend
passed
```

---

## 6. 尚未关闭的环境 / 发布 Gate

1. 修复 cursor helper 的 pre-attempt deadline 检查并增加耗尽边界回归，确保 timeout 后没有 late durable publication。
2. 在具备 symlink 权限的 Windows 环境或 Linux CI 运行完整 backend suite，取得未截断的最终 summary。
3. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim reconciliation、failure-health epoch、shutdown retry 与 row-lock 事务语义。
4. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
5. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
6. Channel/Repository 职责拆分作为 correctness 合并后的独立架构任务处理。

---

## 7. 最终判定

第二十三轮中的 Supervisor barrier、Router 双失败和 auth 修正均已正确关闭；cursor transient contention 的成功路径也已实现并通过回归。但 `_replace_cleanup_state_with_deadline()` 仍允许 deadline 后执行并成功发布 cursor，属于新的 P2 实现边界错误。

**第二十三轮修复：Partial Pass。**
**仓库级 Ready to merge：No。** 在 cursor deadline P2、完整 suite、PostgreSQL 与真实双 Feishu App Gate 关闭前，不应开始依赖 M3 完成态的正式 M4 实现或声明 M3 已完成验收。
