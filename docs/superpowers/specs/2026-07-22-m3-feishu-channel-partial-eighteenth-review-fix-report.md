# 多租户 Agent 发布平台 - M3 飞书渠道部分第十八轮 Review 修复报告

**日期：** 2026-07-22

**关联 Review：** [2026-07-21-m3-feishu-channel-partial-code-eighteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-eighteenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十八轮列出的 6 项 P3 与 1 项新增 Standards Important 已完成代码、测试和文档修复。正式 5 文件 Gate 为 `156 passed`；16 文件 M3 focused regression 能稳定产出汇总，其中 `431 passed / 9 skipped`，仅保留 5 个既有 Windows LocalSandbox 平台失败。当前未发现仍属于本轮 M3 范围的 P1/P2。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| A-1 P3：stale/timeout fallback 绕过 CAS 写共享 `_health` | 已修复 | `_record_startup_failure()` 的超时、异常和 stale fallback 只作为本次调用返回值，不再写共享 health；共享快照仍只允许 durable CAS 成功后更新 | cancellation-resistant health writer 超时后，调用方收到 unhealthy fallback，但 `supervisor.health()` 不发布未持久化值；peer 与 janitor 不受影响 |
| A-2 P3：同 generation 的迟到 health 覆盖较新结果 | 已修复 | 新增持久化 `health_revision`；Supervisor 为每次观察分配单调 revision，repository 在 generation、token、revision 三重 fence 下只接受严格更新的观察；内存写还需保持 process-local latest | repository 先写 revision 2 再写 revision 1，旧写返回 `None`；runtime 回调和公开 `test_binding()` 并发时，DB/内存均保留较新 healthy |
| A-3 P3：吞取消的 runtime claim 可阻塞 peers/janitor | 已修复 | 删除跨 binding repository lifecycle lock；claim 成为 deadline-owned 独立 task。超时/取消时 detach，迟到成功的 claim 按精确 token 与最新 durable generation 补偿释放 | non-cooperative claim 吞取消时，peer 正常 ready、janitor 正常创建；释放阻塞后迟到 claim 最终被清除，没有残留 token |
| A-4 P3：`test_binding` 与 restart 并发时 stale CAS 逃逸为 500 | 已修复 | credential probe 遇 `_StaleHealthProjectionError` 后重读当前 durable row，返回当前 health/serving 状态，不再用旧 row 二次写入 | restart 推进 generation 后释放 probe，公开 `test_binding()` 正常返回当前 healthy，不抛 stale/500 |
| B-1 P3：scanner `recv()` 反序列化异常遗忘已 pop live slot | 已修复 | scanner 请求 I/O 捕获完整异常边界；send/poll/recv、非法响应和 dead-slot 全部进入 `_fail_scan_slot()`，仅最终确认退出才遗忘，否则统一 retain + stop fence | `recv()` 抛反序列化错误时 live child 仍保留在 tracked slots，scanner fail closed，确认退出后方可重启 |
| C-1 P3：quiescing runtime health callback 重投 `running=True` | 已修复 | `_handle_runtime_health()` 与查询/列表统一使用 `_is_serving()`，quiescing owner 只保留 fencing ownership，不再对外报告 serving | stop 失败且 transport 仍存活时触发 runtime health callback，`health().running` 仍为 `False` |
| Standards Important：router + supervisor 聚合运行挂起 | 已修复 | router 测试从 AnyIO runner 统一到 pytest-asyncio；所有 barrier 在 `finally` 释放并 join 请求 task；Supervisor 与 process-owned scanner 在模块边界确定性 shutdown | repository + router + supervisor 同进程 `72 passed` 并正常退出；正式 5 文件 `156 passed`；16 文件聚合正常产出 `431 passed / 9 skipped / 5 known Windows failures` |

聚合回归还暴露并关闭了一个释放确认边界：远端 STOP/DELETE 会在保留同一 runtime token 时推进 generation。owner 现在先读取该 token 对应的最新 durable generation 再精确释放；release deadline 超时后保留唯一数据库写 task，而不是取消并猜测提交是否成功。这样既不会误释放其他 runtime，也不会在“提交成功、返回被取消”时永久保留本地 cleanup owner。

---

## 2. 最终运行时不变量

### 2.1 Health 投影顺序与可见性

- `agent_channels.health_revision` 是每个 runtime generation 内的单调观察序号；所有 generation/token 生命周期切换都将它重置为 0。
- `AgentChannelRepository.update_health()` 在 owner-scoped 行锁事务中同时校验 `runtime_generation`、`runtime_lease_token` 和严格递增的 `health_revision`。
- Supervisor 只有在 durable CAS 成功且该 revision 仍是进程内最新观察时才更新共享 `_health`。
- 超时、持久化异常和 stale CAS 的 fallback 仅返回当前调用方，不污染共享快照。
- runtime health callback、startup projection 与 `test_binding()` 共享同一顺序规则；同代迟到结果和旧代结果都无法覆盖新结果。

### 2.2 Claim、release 与跨 binding 隔离

- runtime claim 不再持有跨 binding 应用锁；每个 claim task 有独立 deadline 和 ownership。
- deadline 到期后，不合作的 claim task 被 detach；如果它稍后提交，done callback 创建精确 token/generation 的补偿释放 task。
- STOP/DELETE 在保留 token 的情况下可推进 generation；实际 transport owner 在释放前读取同 token 的最新 durable generation。
- release writer 超时后保留为该 `_RunningChannel` 的唯一 task，cleanup retry 继续观察同一结果，不重复写、不取消提交中的事务。
- transport `stop()` 与 durable release 都确认后才删除 process-local owner；任何不确定状态都保持 quiescing fence。

### 2.3 Scanner slot ownership

- scan 已 pop 的 slot 在请求 transport、反序列化、响应校验或 liveness 任一异常时都经 `_fail_scan_slot()` 收敛。
- `is_alive/terminate/join/kill` 异常仍表示 exit unknown；只有最终明确 exited 才允许丢弃 slot。
- exit unknown 的 child 保持 tracked，设置 stop fence 并拒绝新 scanner generation，避免 live subprocess 逃逸管理。

### 2.4 Serving 与 ownership 分离

- `owned_binding_ids` 表示仍持有 fencing/cleanup 责任的 generation。
- `running_binding_ids`、`test_binding().running` 和 runtime health callback 都使用 `_is_serving()`。
- 一个 quiescing transport 即使底层 `is_running=True`，也只能是 owner，不能被报告为 serving。

---

## 3. Standards Minor 收口

本轮同步处理了复审中与当前修复直接相关的 Minor：

1. scanner 重复异常出口收敛为 `_fail_scan_slot()`；
2. `_StartupPolicy` 从可任意实例化的 frozen dataclass 改为封闭 enum，调用方只能选择 explicit、explicit-strict 或 reload；
3. slow-ready 测试改为等待真实 lease renewal event，不再依赖两段 80ms sleep；
4. router slow-writer 测试使用 event/barrier、`finally` 释放、请求 task join；
5. README/CLAUDE 的 deterministic Gate、fallback 可见性、scanner 反序列化和 `health_revision` 迁移描述已与实现对齐。

以下两项属于非阻塞结构性债务，本轮未做高风险的大范围拆分：

- 将反复出现的 `(agent_id, binding_id, owner_user_id[, secret_ref])` 引入 `BindingKey` 值对象；
- 将约 44 个方法的 `AgentChannelRepository` 按 ingest/runtime/cleanup/health 职责拆分。

它们不改变当前 runtime 正确性，也不是 P1/P2；建议在 M3 合并后单独设计、迁移和回归，避免与并发/fencing 修复混合。

---

## 4. 主要代码变更

- `backend/app/channels/supervisor.py`
  - 新增 health revision 分配、三重 CAS 与 call-local fallback。
  - claim 改为 deadline-owned task，并补齐迟到 claim 的精确补偿释放。
  - `test_binding()` stale probe 改为返回当前 durable state。
  - runtime callback 统一 serving 判定。
  - release 使用同 token 最新 generation，并保留 deadline 后未决 writer。
  - `_StartupPolicy` 改为封闭 enum。
- `backend/packages/harness/deerflow/persistence/agent_channel/model.py`
  - 新增 `health_revision` 持久化字段。
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py`
  - health 更新增加 revision fence；生命周期转换重置 revision。
  - release 增加 expected generation 和精确幂等确认。
- `backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_17_channel_deletion_state.py`
  - upgrade/downgrade 纳入 `agent_channels.health_revision`。
- `backend/app/channels/feishu.py`
  - scanner response deserialization 纳入统一 fail-closed slot retention。
- `backend/tests/test_agent_channel_repo.py`
  - 新增同代 health 顺序和 release acknowledgement 回归。
- `backend/tests/test_feishu_supervisor.py`
  - 新增/加强 claim 隔离、fallback 可见性、同代 health 顺序、restart stale probe、quiescing callback、远端 generation release 回归。
- `backend/tests/test_feishu_parser.py`
  - 新增损坏 scanner response 的 live-slot retention 回归。
- `backend/tests/test_agent_channels_router.py`
  - 统一 pytest-asyncio runner，补齐 Supervisor/scanner/task teardown。
- `backend/tests/test_user_model_capabilities_migration.py`
  - SQLite/PostgreSQL migration 合同加入 `health_revision`。
- `backend/README.md`、`backend/CLAUDE.md`
  - 同步新的 health、claim/release、scanner、serving 和测试 Gate 不变量。

---

## 5. 自动化验证

### 5.1 TDD 红绿证据

```text
第十八轮 6 个 P3 定向红测：7 failed
对应实现后定向绿测：7 passed

聚合回归额外暴露 remote generation / release acknowledgement：2 failed
修复后定向复跑：2 passed
release 幂等 + slow-ready + non-cooperative startup：3 passed
```

### 5.2 核心同进程聚合 Gate

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py

72 passed, 1 warning in 81.87s
```

该命令正常退出并产出汇总，未再出现 AnyIO/pytest-asyncio runner teardown 的无限等待。

### 5.3 第十八轮正式 5 文件 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

156 passed, 5 warnings in 76.97s
```

warnings 均来自 LangGraph/Lark/WebSocket 第三方 deprecation。

### 5.4 M3 focused regression（16 文件、单进程）

```text
431 passed, 9 skipped, 5 failed, 6 warnings in 188.38s
```

聚合命令稳定结束。5 个失败与前几轮一致，均来自 `test_local_sandbox_provider_mounts.py` 的 Windows 平台差异：4 个 POSIX 容器路径反向映射/roundtrip 断言，以及 1 个强制调用本机不存在 `/bin/sh` 的用例。本轮修改涉及的 Supervisor、scanner、repository、router、Gateway lifecycle、migration 与 secret/sandbox ownership 用例均通过。

### 5.5 完整 backend suite（fail-fast）

Windows 环境没有 `make` 可执行文件，执行 Makefile `test` target 的等价 pytest 主体：

```text
320 passed, 1 failed, 7 warnings in 122.76s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

失败点与第十七、十八轮 Review 记录一致，属于本轮未修改的 auth 行为；本报告不将仓库级全绿 Gate 误报为通过。

### 5.6 静态、格式、编译与差异检查

```text
ruff format --check <10 个本轮 Python 文件>: 10 files already formatted
ruff check --no-cache <同 10 个文件>: All checks passed!
python -m compileall <5 个本轮生产/迁移 Python 文件>: passed
git diff --check: passed（仅提示无关 config.yaml 后续可能 CRLF→LF）
```

---

## 6. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，执行 upgrade/downgrade/re-upgrade，并验证 generation/token/revision 三重 health fence。
2. 使用两个真实 Feishu App 验证 near-deadline ready、non-cooperative/failed stop、credential rotation、进程重启、scanner child failure 和 attachment recovery。
3. 在生产同构环境确认 M3 v1 只运行一个 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 仓库级完整 suite 仍需由 auth 所有者处理 `test_csrf_does_not_exempt_old_login_path`；Windows LocalSandbox 5 项需按其平台契约单独处理。

---

## 7. 最终判定

**第十八轮 Review 的 6 项 P3 和新增聚合测试 Important 已全部关闭；第十八轮没有发现、当前实现也没有已知未修复的 M3 P1/P2。**

就第十八轮代码修复与本地 M3 focused Gate 而言，可以进入下一轮复审/合并准备。真实 PostgreSQL、双 Feishu App、范围外 auth 全量失败及 Windows LocalSandbox 平台项仍是最终生产发布 Gate，因此本报告不宣称整个仓库已经满足生产发布条件。
