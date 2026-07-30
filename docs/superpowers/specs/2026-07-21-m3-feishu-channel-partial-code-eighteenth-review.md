# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十八轮代码复审

**状态：** 已复审，第十七轮全部 findings 已关闭；本轮无新 P1/P2，仍有 Gate 级阻塞
**日期：** 2026-07-21

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十七轮复审：[2026-07-21-m3-feishu-channel-partial-code-seventeenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-seventeenth-review.md)
- 第十七轮修复报告：[2026-07-21-m3-feishu-channel-partial-seventeenth-review-fix-report.md](./2026-07-21-m3-feishu-channel-partial-seventeenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后的当前未提交 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- 重点：第十七轮 1 个 P1、2 个 P2、3 个 Standards Important、2 个 Standards Minor 的关闭情况，以及本轮修复 diff 的 Spec/Standards 双轴符合性

---

## 1. 复审结论

第十七轮的修复方向全部落地：health projection 不再持跨 binding 锁，所有 health 写携带 expected `runtime_generation + runtime_lease_token` 并在 repository 行锁事务内 CAS；scanner 的 `is_alive/terminate/join/kill` 全部 best-effort，只有最终确认退出才允许遗忘 slot，scan/replenish/partial spawn/stop 共用统一 unconfirmed-slot retention；`running_binding_ids` 与 `test_binding().running` 只报告 serving，`owned_binding_ids` 专用于 fencing/清理收敛；`_StartupPolicy` 消除了五布尔组合；quiescing 状态迁移收敛到 `_enter_quiescing()` / `_drain_runtime_task()` / `_ensure_cleanup_retry()`；parser 三个时间敏感用例改为逻辑 clock/确定性填 slot/扩大观察窗后连续三次全绿。

本轮 **Spec 轴无新 P1/P2**，发现 **6 个 P3**（详见第 3 节）；**Standards 轴发现 1 个新 Important**：本机复现 `test_agent_channels_router.py` 与 `test_feishu_supervisor.py` 同进程聚合运行会无限挂起（3/3 复现），导致 CLAUDE.md 声明的 16 文件 M3 focused regression 聚合命令无法产出汇总；此外完整仓库级 `make test` 全绿 Gate 仍被范围外 auth 既有失败阻塞（延续项）。

**结论：Ready to merge：No（但阻塞项已从代码 P1/P2 降级为 Gate/环境层面）。**

- Spec 轴：第十七轮 3 项全部关闭；新增 0 P1、0 P2、6 P3。
- Standards 轴：第十七轮 4 项全部实质关闭；新增 1 Important（聚合测试挂起）、6 Minor；1 项延续 Important（完整 suite 全绿仍被范围外 auth 失败阻塞）。

---

## 2. 第十七轮问题关闭状态

| 第十七轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1：detached failure projection 可持全局 lock 阻塞 peers + 迟到覆盖新 generation | **已关闭** | `_repository_projection_lock` 已删除；health 写不持共享锁并全部走 generation/token CAS；`release_runtime()` 返回真实提交后 generation；正式回归覆盖持锁吞取消与迟到覆盖两个场景，见 3.1 |
| P2：scanner process API 抛错时遗忘 live child | **已关闭** | `_terminate_slot()` 全步骤 best-effort、仅最终 `is_alive() is False` 允许遗忘；`_retain_unconfirmed_slots()` 统一 scan/replenish/partial spawn/stop 的失败出口；四路抛错 + replenish 回滚回归齐备，见 3.2 |
| P2：quiescing fencing owner 被误报为 running | **已关闭** | `_is_serving()` 统一 serving 判定；`running_binding_ids`/`test_binding().running` 只报 serving，`owned_binding_ids` 专用 fencing；调用方（gateway 日志、shutdown teardown、测试收敛）已全部换对接口，见 3.3 |
| Important：scanner 文档强于异常路径实现 | **已关闭** | README/CLAUDE 的 exit-unknown 描述与实现逐句一致，方向既不强也不弱，见 4.1 |
| Important：完整 `make test` 未执行 | **部分关闭（延续）** | 十七轮已执行 Makefile 等价主体并如实记录范围外 auth 失败；仓库级全绿 Gate 仍未达成，本轮另发现聚合运行挂起使该 Gate 更难关闭，见 4.2/4.3 |
| Important：parser focused Gate 时间敏感不稳定 | **已关闭（留 2 处 Minor 残留）** | 逻辑 clock 注入、确定性 quarantine 填 slot、500ms 观察窗均落地；本轮 parser 全文件连续三次 `54 passed`；残留 30ms 真实 deadline 与墙钟上界断言两处 Minor，见 4.4 |
| Minor：convergence 五布尔参数 | **已关闭** | `_StartupPolicy`（4 字段 frozen dataclass）+ `.explicit(strict=)` / `_STARTUP_RELOAD_POLICY` 两个具名入口，调用点不再拼布尔；构造器仍公开属措辞级残留 |
| Minor：quiescing 状态迁移分散 | **已关闭** | `quiescing = True` 全仓仅存在于 `_enter_quiescing()`，cleanup task 创建仅在 `_ensure_cleanup_retry()`；discard/replace/retry/normal stop 四处均经统一 helper，grep 无旁路赋值 |

---

## 3. Spec 轴

第十七轮 3 项 findings 的逐条核实与对抗性分析（置信度均为高）：

### 3.1 P1 关闭核实：health projection 锁隔离 + generation/token CAS

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L363)（`_record_health`，L363–396）
- [supervisor.py](../../../backend/app/channels/supervisor.py#L575)（`_repository_lifecycle_lock` 唯一使用点，只包 claim/final re-read）
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L839)（`update_health` 行锁内双重 CAS）
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L638)（`release_runtime` 返回提交后行）
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L586)、[test_agent_channel_repo.py](../../../backend/tests/test_agent_channel_repo.py#L150)

核实要点：

1. 原 `_repository_projection_lock` 全仓无残留；`_record_health()` 不持任何共享锁，deadline detach（L488–500）不再可能带走全局资源。回归 `test_non_cooperative_failure_projection_cannot_block_peer_or_janitor` 复现了 round-17 探针条件（真实进入 `repository.update_health()` 后吞取消），断言 peer healthy、janitor 已创建。
2. `update_health()` 在 `with_for_update()` 行锁事务内同时校验 generation 与 token（L853），任一不匹配返回 `None` 不写库；PostgreSQL 为真行锁，SQLite 下校验与写在同一事务内由库级写锁串行，与仓库既有 mutator 约定一致。
3. stale 时 `_record_health` 先抛 `_StaleHealthProjectionError`（L386–387）再才可能写内存；detached 回调只消费结果。回归验证旧投影在新 generation healthy 后恢复时 DB 与内存均不被改写。
4. `release_runtime()` 提交后返回整行，supervisor 直接采用（L859–860），无本地 `+1` 推算残留。
5. 对抗性检查：cleanup/lease/janitor/startup-lease 等 background task 均只取 per-binding 锁或无锁，无 detached task 能触及 `_repository_lifecycle_lock`。

### 3.2 P2 关闭核实：scanner 异常路径统一 ownership

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L449)（`_process_liveness`，异常→unknown）
- [feishu.py](../../../backend/app/channels/feishu.py#L466)（`_terminate_slot` 全步骤 best-effort）
- [feishu.py](../../../backend/app/channels/feishu.py#L458)（`_retain_unconfirmed_slots` 统一失败出口）
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1419)

核实要点：`_terminate_slot()` 对 close/is_alive/terminate/join/kill/post-kill join 每步独立 try/except 后继续，仅最终 `_process_liveness(slot) is False` 算确认退出（异常→`None`≠`False`→保留）；scan 的 dead-slot 清理与请求失败路径、replenish 的终止失败与 partial spawn 回滚（原列表推导已改为显式循环 + failure 收集）、stop 全部经 `_retain_unconfirmed_slots()`；`start()` 在任一 slot liveness 非 False 时拒绝重启。四路抛错回归（terminate/join/kill/最终 is_alive）与 replenish 回滚回归均断言保留 slot、置 stop fence、拒 restart。

### 3.3 P2 关闭核实：serving 与 fencing ownership 拆分

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L336)（`running_binding_ids`/`owned_binding_ids`/`_is_serving`）
- [app.py](../../../backend/app/gateway/app.py#L303)（gateway 启动日志用 serving 计数）
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L438)

核实要点：`test_binding().running` 走 `_is_serving`（L1736/L1749）；`shutdown()` teardown 用 `set(self._binding_locks) | set(self._running)` 即 owned 语义；测试收敛等待已改用 `owned_binding_ids`；owner `test` 路由响应只回 `{health, detail}` 不含 running。non-cooperative start/stop 两条回归均断言失败 binding 从 running 排除、owned 仍跟踪、healthy peer 不受影响。

### 3.4 新发现问题（6 个，均 P3）

**A-1（P3）stale/timeout fallback 仍写内存 `_health`，绕过"CAS 成功才写内存"不变量**
[supervisor.py](../../../backend/app/channels/supervisor.py#L499)：timeout detach 后写 `self._health[binding_id] = fallback`（L499）；投影 2s 内以异常完成——包括 `_StaleHealthProjectionError`——仍写内存 fallback（L506→L512）。与修复报告 2.1 及 CLAUDE.md "local health changes only after that CAS succeeds" 不符；因 per-binding 锁串行且无外部序列化出口，仅进程内快照失真。

**A-2（P3）同 generation 迟到投影可覆盖较新的健康写**
detached 投影若挂起 >2s 后成功（generation/token 未动，CAS 通过），其无锁的内存写（L395）+ DB 写会把期间 `test_binding`（L1725–1750，不取 binding 锁）写入的较新 healthy 回退为旧 unhealthy。CAS 只区分 generation，不区分同代内观察顺序；自愈型、低概率。

**A-3（P3）吞取消的 `claim_runtime` 仍可内联无界持有 `_repository_lifecycle_lock`**
[supervisor.py](../../../backend/app/channels/supervisor.py#L575)：在 `asyncio.timeout`（L433）内持锁调 claim/re-read；若 repository 调用吞掉 `CancelledError`，convergence deadline 永不触发，锁被无界持有，所有 peer claim 与 `load_active_bindings()` gather 阻塞、janitor 不建。与原 3.1 的区别：无 detach、挂起对外可见；真实 SQLAlchemy 会传播取消，故降为 P3。这是 round-17 威胁模型的最后残余。

**A-4（P3）`test_binding` 遇并发 restart 的 stale CAS 会以 500 逃逸**
[supervisor.py](../../../backend/app/channels/supervisor.py#L1728)：凭据探活期间若并发 restart 使 generation 前移，首次 `_record_health` 抛 stale 被 `except Exception` 捕获后，用同一 stale `row` 二次 `_record_health` 再抛 → router（[published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py#L344)）无捕获 → HTTP 500 而非优雅响应。

**B-1（P3）`scan()` 的 recv 反序列化异常可让已 pop 的 live slot 逃出统一 retention**
[feishu.py](../../../backend/app/channels/feishu.py#L509)：请求 I/O 只捕 `EOFError/BrokenPipeError/OSError`；`connection.recv()`（L522）的 `UnpicklingError`/`MemoryError` 等会逃逸，此时 slot 已在 L499 pop、finally 不回插——live child 脱离 tracking。触发需管道数据损坏且不表现为 EOF，窄于原 3.2 场景。

**C-1（P3）`_handle_runtime_health` 未经 `_is_serving`，quiescing owner 可被内存重投为 running=True**
[supervisor.py](../../../backend/app/channels/supervisor.py#L1255)：写 `running=current.channel.is_running`。非合作 stop 保留的 quiescing owner 若 transport 尚未真正断开，在途附件清理健康回调可把内存 `_health` 的 running 重投为 True。router 不序列化 running、`health()` 仅测试消费，故仅内部快照失真。

### 3.5 Scope creep

未发现新增。`secret_store.py`、`aio_sandbox_provider.py`、`published_agent_channels.py` 均为第 8–16 轮已复审的 M3 必需改动；`gateway/app.py` 仅 +7 行（shutdown 停 scanner 池），是 scanner 所有权设计的必需接线。第十七轮修复报告的改动文件清单与本轮 diff 核对一致。

---

## 4. Standards 轴

### 4.1 已关闭：scanner 文档与异常路径实现一致

README（L216 起）与 CLAUDE.md（L530 起）新增的 exit-unknown、最终 `is_alive()` 检查、stubborn child 保持 tracked/stop fence 描述与 `feishu.py` 实现逐句一致，方向既不强也不弱。唯一措辞偏差：CLAUDE.md "use an injected logical clock **or event/barrier state** for deterministic merge gates" 轻微偏强——quarantine 两个用例实际仍依赖 30ms 真实 deadline + 重试，并非纯事件驱动（Minor）。

### 4.2 新 Important-1：router + supervisor 同进程聚合运行确定性挂起，M3 focused 聚合 Gate 无法完成

**相关文件：**

- [test_agent_channels_router.py](../../../backend/tests/test_agent_channels_router.py#L99)（anyio 模式）
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py)
- [feishu.py](../../../backend/app/channels/feishu.py#L420)（`_maintain` 维护线程）
- [CLAUDE.md](../../../backend/CLAUDE.md#L539)（M3 focused regression 聚合命令）

本轮在当前 Windows 工作区 3/3 复现：

1. CLAUDE.md 声明的 16 文件 M3 focused regression 聚合命令挂起，faulthandler 显示卡在 `test_feishu_supervisor.py::test_non_cooperative_start_cannot_block_peer_startup_or_lose_fencing_owner` PASSED 之后的 pytest-asyncio fixture teardown（`asyncio.runners._cancel_all_tasks` 永不返回）；
2. 最小组合 `test_agent_channels_router.py + test_feishu_supervisor.py` 两次挂起，位置不同（一次在 router 文件内 anyio runner 关闭、一次在 supervisor teardown），共同特征是 event loop close 阶段有任务不响应取消，同时存活一个 `feishu.py` L425 的 scanner `_maintain` 线程与两个空闲 executor worker；
3. 两个文件各自单独运行全部通过（router 8 passed、supervisor 47 passed），疑似挂起用例单独复跑 2 passed。

这与第十七轮修复报告 4.5 节"425 passed in 141.32s"的一次性成功并存，说明聚合 Gate 存在环境/顺序敏感的挂起模式——比 flaky 失败更严重，因为没有汇总产出、CI 会超时。按 Mandatory TDD，"偶尔通过"不能作为 Gate 满足证据。根因未完全定位（不排除测试 fixture 清理顺序问题，也不排除生产代码存在吞取消后无界等待的任务）；建议：为 supervisor/router 测试模块补充确定性的 loop-close 前任务收敛断言，定位并修复不响应取消的任务，或在诊断期间给聚合 Gate 挂 per-test 超时插件以保证有汇总产出。

### 4.3 延续 Important-2：仓库级完整 `make test` 全绿 Gate 未达成

十七轮已执行 Makefile 等价主体并如实记录 `tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path` 为范围外稳定既有失败——该项按十七轮口径视为"已执行并记录范围外阻塞"。但仓库级全绿仍未达成，且 4.2 的聚合挂起使完整 suite 在本机同样面临无汇总风险。此项继续保留为未关闭 Gate。

### 4.4 已关闭（留残留）：parser 确定性

三个原用例的修复符合十七轮建议：discovery cursor 改注入逻辑 clock（`_scan_all_cleanup_jobs()` 新增 `clock` 参数）；quarantine 改确定性预填 8 slot 后断言 `== 8`；stalled delete 预算 0.1s→0.5s、观察窗 1.2s。本轮 parser 全文件连续三次 `54 passed`。残留两处 Minor：一个用例仍用 30ms 真实 deadline（有 6 次重试 + 命中即 break，容忍度足够）；`elapsed < 0.75` 墙钟上界断言在极端受载下仍可能波动。

### 4.5 新 Minor 汇总（judgement call）

1. **Duplicated Code**：`scan()` 内 4 处相同的"terminate 失败→retain→return True"形状（feishu.py L503–530）；rotate 路由异常梯 4 个分支重复同参 `_discard_unstaged_secret(...)` 调用（published_agent_channels.py）。
2. **Data Clumps**：`(agent_id, binding_id, owner_user_id[, secret_ref])` 在路由帮助函数与 repository 约 40 个方法中反复结伴，可捆为 BindingKey 类型。
3. **Divergent Change**：`AgentChannelRepository` 约 44 个方法横跨 CRUD、ingest 状态机、runtime lease、cleanup outbox、删除 tombstone、health 六类变更原因，ingest 状态机适合拆独立 repository。
4. **Primitive Obsession 残留**：`_StartupPolicy` frozen dataclass 构造器仍公开，docstring "callers cannot assemble boolean combinations" 略强于 Python 可强制的程度。
5. **时序断言残留**：见 4.4。
6. **文档措辞**：见 4.1 的 deterministic Gate 描述偏强，与 3.4 A-1 的 "CAS 成功才写内存" 表述强于实现同类。

本轮未发现新的 harness→`app.*` 反向依赖（全 harness 目录 grep 为空）、公开 API 缺类型注解或 Ruff/format 问题。

---

## 5. 验证记录

### 5.1 正式测试（本机 Windows，`.venv` 直跑 pytest）

```text
tests/test_feishu_supervisor.py
47 passed, 2 warnings in 25.32s

tests/test_agent_channel_repo.py
12 passed, 2 warnings in 2.37s

tests/test_feishu_supervisor.py + tests/test_agent_channel_repo.py（合跑）
59 passed, 2 warnings in 27.67s

tests/test_feishu_parser.py（连续三次）
54 passed in 10.28s / 54 passed in 9.16s / 54 passed in 9.19s

第十七轮 5 文件 Gate（supervisor + parser + websocket_lifecycle + gateway_services + harness_boundary）
152 passed, 6 warnings in 44.83s

M3 focused 其余 12 文件（分批）
304 passed, 9 skipped, 5 failed in 113.88s
5 个失败均为前几轮已记录的 Windows LocalSandbox 环境项（test_local_sandbox_provider_mounts.py）
```

### 5.2 聚合挂起复现（对应 4.2）

```text
16 文件 M3 focused 聚合：faulthandler_timeout=120 触发
挂在 test_non_cooperative_start_... PASSED 之后的 pytest-asyncio teardown
_cancel_all_tasks 永不返回；feishu.py:425 _maintain 线程存活

router + supervisor 两文件聚合（两次）：faulthandler_timeout=90 触发
挂在 anyio runner 关闭 / event loop close；同样的线程特征

router 单独：8 passed, 2 warnings in 10.82s
supervisor 单独：47 passed
疑似挂起用例 test_owner_write_heartbeat_blocks_janitor_during_slow_secret_write 单独复跑：2 passed
```

### 5.3 静态、格式与差异检查

```text
ruff check --no-cache <11 个本轮修复相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 11 个文件>
11 files already formatted
```

### 5.4 尚未关闭的 Gate

- 聚合测试挂起（4.2）需先定位修复，M3 focused 16 文件聚合命令才能重新作为可信合并 Gate。
- 当前环境未配置真实 PostgreSQL；`REQUIRE_POSTGRES_TESTS=1` 的 migration 与 health CAS 并发严格 Gate 仍需在 CI 执行。
- 尚未执行两个真实 Feishu App 的 near-deadline ready、non-cooperative/failed stop、rotation、进程重启和 attachment recovery 冒烟。
- 仓库级完整 suite 全绿仍被范围外 auth 既有失败（`test_csrf_does_not_exempt_old_login_path`）阻塞。
- Windows LocalSandbox 5 项平台失败需在对应平台契约下单独处理。

---

## 6. 最终结论

第十七轮列出的 1 项 P1、2 项 P2、3 项 Standards Important、2 项 Standards Minor 在代码、正式测试与文档侧均已实质关闭；本轮对抗性复审未发现新的 P1/P2，剩余 6 个 P3 均为窄触发条件或仅进程内快照失真的残余项，可与后续轮次或收尾阶段一并处理。

建议优先顺序：

1. 定位并修复 router + supervisor 聚合运行的 event-loop-close 挂起（4.2）——这是当前唯一新增 Important，也是恢复 M3 focused 聚合 Gate 与完整 suite 可信运行的前提；
2. 收口 A-3（`_repository_lifecycle_lock` 内联持锁的吞取消残余）与 C-1（`_handle_runtime_health` 走 `_is_serving`），二者同属 round-17 威胁模型的尾巴；
3. 顺手修复 A-4（`test_binding` stale CAS 500）与 B-1（recv 反序列化异常漏 retention）；
4. A-1/A-2 的内存 fallback 语义与文档表述二选一对齐（改实现或改措辞）；
5. 最后完成真实 PostgreSQL、双 Feishu App 与仓库级全绿 Gate。

**Ready to merge：No（阻塞项为 Gate/环境层面；代码侧第十七轮 findings 已全部关闭，无新 P1/P2）。**
