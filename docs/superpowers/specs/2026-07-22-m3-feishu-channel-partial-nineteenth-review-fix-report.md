# 多租户 Agent 发布平台 - M3 飞书渠道部分第十九轮 Review 修复报告

**日期：** 2026-07-22

**关联 Review：** [2026-07-22-m3-feishu-channel-partial-code-nineteenth-review.md](./2026-07-22-m3-feishu-channel-partial-code-nineteenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十九轮列出的 1 项 P2、1 项 P3 已完成代码、测试和文档修复。核心三文件聚合为 `78 passed`，正式 5 文件 Gate 为 `162 passed`；16 文件 M3 focused regression 能正常汇总为 `437 passed / 9 skipped`，仅保留 5 个既有 Windows LocalSandbox 平台失败。当前未发现仍属于第十九轮 M3 范围的 P1/P2。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P2：detached runtime claim 在取消、release 失败/CAS miss、shutdown 边界遗留永久 token | 已修复 | deadline 后不再取消提交结果不明的 claim writer；claim task 始终保留在显式 ownership 集合。无论 task 返回成功、`None`、异常或取消态，完成回调都会启动 exact-token reconciliation。reconciler 重读同 token 的最新 durable generation，release 失败或 generation race 返回 `None` 时持续重试，直到 token 消失、改变或行删除 | 覆盖 commit 已生效但 acknowledgement 尚未返回、首次 release 异常、首次 release 前同 token generation 推进三条回归；均最终清除 token，且 transient/CAS race 至少发生两次 release attempt |
| P2：shutdown 未 drain late claim/release ownership | 已修复 | Supervisor shutdown 使用一个 20 秒总预算停止 janitor/runtime 并 drain late claim/release tasks。预算内不收敛时抛出明确错误，不设置 `_shutdown_complete`，也不释放 leader fence；后续 cleanup 收敛后可再次 shutdown | claim 阻塞时 shutdown 按缩短的测试预算失败且 fence 仍 held；放行 claim 后 token 被补偿清除，第二次 shutdown 成功释放 fence |
| P3：凭据轮换未切换 health fencing epoch | 已修复 | `AgentChannelRepository.update_credentials()` 在提交新 `app_id/secret_ref` 时推进 `runtime_generation`，随后清零 health/revision。旧 row 发起的 probe 即使在新 credential 提交后才分配 revision，也会因 generation 不匹配而 CAS fail | inactive rotation：旧 secret 的 healthy probe 不写 DB/内存，新 credential 保持 unknown；active rotation：在 credential commit 与 runtime stop/restart 之间放行旧 secret 的 unhealthy probe，DB/内存均不发布旧结果，restart 后恢复 healthy |
| Standards Minor：stale probe 两分支重复构造当前 health | 已修复 | 抽取 `_current_binding_health()`，统一 durable row 重读和 serving 状态构造 | concurrent restart 与 active/inactive rotation probe 回归通过 |
| Standards Minor：closed enum 仍以四布尔 tuple 编码 | 已修复 | `_StartupPolicy` value 改为具名字符串；行为通过只读 property 从明确枚举成员导出，不再存储 primitive flag bundle | explicit、strict restart、reload isolation 与完整 Supervisor 回归通过 |
| Standards Minor：Repository/Data Clumps 大范围结构债务 | 已登记，未混改 | `BindingKey`/typed row 与 ingest/runtime/health/cleanup repository 拆分属于跨模块迁移，不与本轮 fencing 修复混合 | 不影响本轮 correctness；建议 M3 合并后独立设计和回归 |

---

## 2. 最终运行时不变量

### 2.1 Late claim 是显式 cleanup ownership

- `claim_runtime()` 仍在独立 deadline-owned task 中执行，不持跨 binding 应用锁。
- request deadline 到期只停止等待，不取消可能正在提交的数据库 writer；因此不会人为制造“已提交但 task 只暴露 CancelledError”的结果歧义。
- detached claim task 始终登记在 `_late_runtime_claim_tasks`，直到完成回调接管结果。
- 完成回调不把异常、取消态或 `None` 当作“确定未提交”；它一律启动 exact-token reconciliation。
- reconciliation 每轮先通过 system-scoped row read 确认 exact token，取得其最新 generation 后再 release。数据库异常、release `None` 和 generation CAS race 都保留同一个 retry owner。
- 行删除、token 已清除或 token 已变化时，旧 claim ownership 才可视为收敛。

### 2.2 Shutdown 不提前宣称完成

- shutdown 关闭新 runtime admission 后，在一个 `RUNTIME_SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS=20` 的总 deadline 内停止 janitor、收敛 process-local runtime，并 drain late claim/release 集合。
- shutdown 等待的是原 task，不取消未决 claim/release writer。
- deadline 到期而 ownership 仍未收敛时，shutdown 抛出错误；`_shutdown_complete` 保持 false，leader fence 保持 held。
- cleanup 稍后收敛后允许再次调用 shutdown，并且只有 late ownership 为空且现有 leader release 条件满足时才释放 fence。

### 2.3 Credential replacement 是新的 health epoch

- `runtime_generation` 同时 fence runtime lifecycle 与生成 health 观察所使用的 credential authority。
- credential update 和 rotation rollback 每次都会推进 generation，再将 health/revision 重置为 unknown/0。
- probe 持有的旧 generation 无法跨 credential commit 写入，即使 runtime token 没变或 binding 处于 inactive。
- active rotation 的 stop/release 会读取同 token 的最新 durable generation，因此 credential epoch 推进不会阻断旧 transport 的精确退出确认。

---

## 3. 聚合 Gate 稳定性补强

第十九轮第一次 16 文件聚合在 router `slow_secret_write[patch]` 用例失败后未能清理结束。定位结果是测试自身仍保留两个负载敏感点：

1. 60ms writer lease 在受载 Windows runner 上可能在首次 20ms heartbeat 获得调度前失效；
2. timing assertion 失败会跳过函数末尾的 Supervisor shutdown 与 engine dispose，使 `-x` 停在异步 teardown。

修复后测试仍以真实 `writer_renewed` event 作为验收，不使用 sleep 推断；lease 调整为 3 秒，仅提供受载调度余量。整个 HTTP/断言区域置于外层 `try/finally`，任何异常都执行 Supervisor shutdown 和 engine dispose，请求 barrier 也始终释放并 join。router 全文件和后续 16 文件单进程聚合均正常结束。

---

## 4. 主要代码变更

- `backend/app/channels/supervisor.py`
  - deadline claim 不再主动取消未决数据库 writer。
  - late claim 完成回调对所有 outcome 启动 exact-token reconciliation。
  - late release 对数据库异常和 generation race 持续重试。
  - 新增 shutdown 总 deadline 与 late ownership drain；未收敛时保留 leader fence。
  - stale credential probe 统一经 `_current_binding_health()` 返回当前 durable state。
  - `_StartupPolicy` 去除四布尔 tuple value。
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - `update_credentials()` 推进 `runtime_generation`，将 credential replacement 纳入 health fence。
- `backend/tests/test_feishu_supervisor.py`
  - 新增 acknowledgement 未返回、release transient failure、generation CAS race、shutdown drain 四条 claim 回归。
  - 新增 inactive/active rotation-vs-test 两条 credential epoch 回归。
  - 公共 fixture helper 通过 owner-scoped repository/SecretStore seam 准备 credential ingest并观察 token 收敛。
- `backend/tests/test_agent_channel_repo.py`
  - credential update 合同增加 generation 递增与 health revision 重置断言。
- `backend/tests/test_agent_channels_router.py`
  - slow writer heartbeat 使用有余量的事件驱动租约，并保证所有失败路径执行请求、Supervisor 和 engine 清理。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步 late claim retry ownership、shutdown drain deadline、credential health epoch 与具名 startup policy。

---

## 5. 自动化验证

### 5.1 TDD 红绿证据

```text
第十九轮 P2/P3 对抗性红测：6 failed
实现 late ownership / credential epoch 后：6 passed
```

六条正式回归覆盖：

1. claim commit 已生效、acknowledgement 返回前 deadline；
2. late release 首次数据库异常；
3. latest-generation read 与 release 之间 generation 再推进；
4. late claim 未决时直接 shutdown；
5. inactive credential rotation 与旧 probe 并发；
6. active credential commit 和 runtime restart 之间的旧 probe 窗口。

### 5.2 Supervisor + repository

```text
70 passed, 1 warning in 29.13s
```

### 5.3 核心同进程聚合 Gate

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py

78 passed, 1 warning in 70.30s
```

命令正常退出，没有 event-loop teardown 挂起。

### 5.4 第十九轮正式 5 文件 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

162 passed, 5 warnings in 67.54s
```

### 5.5 Router 稳定性复跑

```text
tests/test_agent_channels_router.py
8 passed, 1 warning in 20.72s
```

### 5.6 M3 focused regression（16 文件、单进程）

```text
437 passed, 9 skipped, 5 failed, 6 warnings in 213.69s
```

命令正常结束。5 个失败与前几轮一致，均为 `test_local_sandbox_provider_mounts.py` 在当前 Windows runner 上的既有平台差异：4 个 POSIX container path reverse mapping/roundtrip 断言，以及 1 个调用本机不存在 `/bin/sh` 的用例。第十九轮新增 6 条、Supervisor、repository、router、parser、Gateway lifecycle、migration 与 secret/sandbox ownership 相关用例均通过。

### 5.7 完整 backend suite（fail-fast）

Windows 环境没有 `make` 可执行文件，执行 Makefile `test` target 的等价 pytest 主体：

```text
320 passed, 1 failed, 7 warnings in 124.47s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

失败点与第十八、十九轮 Review 记录一致，属于本轮未修改的 auth 行为；本报告不将仓库级全绿 Gate 误报为通过。

### 5.8 静态、格式、编译与差异检查

```text
ruff format --check <5 个本轮 Python 文件>: 5 files already formatted
ruff check --no-cache <同 5 个文件>: All checks passed!
python -m compileall <2 个本轮生产 Python 文件>: passed
git diff --check: passed（无关 config.yaml 仍有 CRLF→LF 提示）
```

---

## 6. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim commit cancellation、release retry、shutdown drain 和 credential epoch 的真实事务语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 仓库级完整 suite 仍需由 auth 所有者处理 `test_csrf_does_not_exempt_old_login_path`；Windows LocalSandbox 5 项按其独立平台契约处理。

---

## 7. 最终判定

**第十九轮 Review 的 1 项 P2 与 1 项 P3 已全部关闭；当前没有已知未修复的第十九轮 M3 P1/P2。**

late runtime claim 现在是可重试、可由 shutdown 有界 drain、不会提前释放 leader fence 的显式 ownership；credential update 也已成为 health CAS 可识别的新 epoch。就第十九轮代码修复与本地 M3 focused Gate 而言，可以进入下一轮复审/合并准备。

真实 PostgreSQL、双 Feishu App、范围外 auth 全量失败及 Windows LocalSandbox 平台项仍是最终生产发布 Gate，因此本报告不宣称整个仓库已经满足生产发布条件。
