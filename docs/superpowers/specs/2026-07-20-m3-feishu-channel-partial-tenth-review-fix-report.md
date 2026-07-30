# 多租户 Agent 发布平台 — M3 第十轮 Review 修复报告

**日期：** 2026-07-20

**关联 Review：** [2026-07-20-m3-feishu-channel-partial-code-tenth-review.md](./2026-07-20-m3-feishu-channel-partial-code-tenth-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十轮列出的 3 项 P1、4 项 P2 和 3 组 Standards 缺口已完成代码侧修复与本地分层验证；真实 PostgreSQL、多 Gateway、远程 AIO、双 Feishu App、进程 kill/restart 和全量 backend Gate 仍属于环境/部署验收项。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：runtime claim 返回后仍可被 DELETE 穿透并注册旧 runtime | 已修复 | runtime claim 改为带过期时间的互斥 provisional lease；本地 registry/health 完成后必须再次以 matching token CAS confirm。DELETE 保留并撤销 lease，等待远端 release/expiry，物理删除拒绝任何残留 token；已注册 runtime 持续 heartbeat，观察到撤销后自行 stop | claim 返回后暂停，另一 Gateway 完整 DELETE，再恢复启动；断言旧 channel 已 stop，且不进入 `_running`/registry |
| P1：stop 失败恢复的 post-claim 异常留下 `active + 无 runtime` | 已修复 | `restore_deleting` 的 provisional claim 不再提前切换 row；只有 registry、health 和最终 confirm 全部成功后才由 CAS 恢复 `active`。registry/health/cancellation 失败都会释放 lease、清理本地 runtime，并保留 durable tombstone | 覆盖 registry failure、health projection failure 和 task cancellation；三条路径均保留 `deleting`，无本地 runtime/registry |
| P1：PATCH 预清理误删当前 rotation candidate | 已修复 | 所有 secret cleanup 入口统一先执行 `rotation_candidate` crash recovery；通用 cleanup 禁止删除非 deleting row 当前引用的 `secret_ref` | 覆盖“credential row 已切到 candidate 后进程退出，再由 owner PATCH”的恢复路径；当前 candidate 始终存在，旧 ref 才进入 superseded cleanup |
| P2：AIO destroy 失败或 materialize 前快照导致 cleanup intent 丢失 | 已修复 | destroy 失败不再 finish/ack operation；`cleanup_pending` record 与 operation 保留供重启恢复。缺席单次或过期的 `list_running()` 快照不会删除 durable intent；即使 `idle_timeout=0` 也会周期 reconcile，目标出现后才 destroy 并清记录 | transient destroy failure 后重启 provider 可重试；Gateway B 先看不到目标时 record 保留，Gateway A 随后物化后下一轮完成 destroy |
| P2：quarantine 满 8 后正常尾部 job 永久饥饿 | 已修复 | 每轮全局 prune 已完成 future；8 个 released-slot quarantine 之外，最多两个 saturated reader 继续持有真实 slot，仍可为正常 tail 保留前进容量；实际 reader 总数上限为 10 | 8 个永久挂起 path 加正常尾部 job，断言正常 job 可发现、quarantine 不超过 8、实际 active readers 不超过 10 |
| P2：SecretStore.put 到 DB stage 之间无 durable owner | 已修复 | `SecretStore.put_pending()` 在写密文前先原子写 pending-ingest record；DB stage 后 matching ack。startup/周期 janitor 对 row-owned ref 只 ack，对超时无主 ref erase 后 ack；pre-stage 404/409 直接擦除本次 ref并正确映射响应 | 覆盖 pending record 生命周期、PATCH 与 DELETE 竞态返回 409 且不泄漏新 ref，以及未入库 pending 的 janitor 回收 |
| P2：global invalid/timed-out discovery 污染 clean binding health | 已修复 | 每个有效 cleanup job 维护独立 binding index；per-binding health 只读取自己的索引。无法归属的损坏/挂起 global record只影响 global store recovery，不再把所有租户标记 unhealthy | 其他 binding 的 invalid JSON 存在时，clean binding health 仍保持 healthy |
| Standards：新增/修改测试签名类型缺口 | 已修复 | review 指出的 repository、parser、supervisor fixtures/callbacks，以及本轮新增 AIO、router、migration、SecretStore 测试均补齐参数和返回值类型 | Ruff/format 全绿，并对 review 指定测试文件执行 AST 签名复核 |
| Standards：migration upgrade/downgrade 与 schema 断言不足 | 代码侧已修复 | SQLite/PostgreSQL 共用完整列集合断言，补齐 nullable、default、字符串长度、`runtime_lease_expires_at` 和 downgrade 后重新 upgrade；PostgreSQL 可用 `REQUIRE_POSTGRES_TESTS=1` 强制缺库失败 | 本地 SQLite 双向路径通过；本地无 PostgreSQL，集成项为 1 skip，真实 PG Gate 尚待 CI |
| Standards：reader 上限文档与实现不一致 | 已修复 | README/CLAUDE 明确区分 2 个 logical active slots、8 个 released-slot quarantine readers、2 个 saturated readers和最多 10 个实际 daemon readers；同步 provisional lease、pending ingest、binding index 与 AIO durable intent 语义 | 文档与实现常量/测试一致 |

---

## 2. 最终运行时不变量

### 2.1 Runtime lease、删除与失败恢复

- `claim_runtime()` 只创建带 `runtime_lease_expires_at` 的 provisional ownership；未过期的其他 token 不可被覆盖，没有 expiry 的遗留非空 token也按 fail-closed 处理。
- channel ready 后先取得 claim，再写入本地 `_running`、dynamic registry 与 health projection；最后的 `confirm_runtime()` matching-token CAS 才是发布完成点。
- `restore_deleting=True` 不会在 provisional 阶段清除 tombstone；只有最终 confirm 才将 `deleting` 切回 `active`。
- 注册后的 runtime 周期 heartbeat；DELETE 写入 tombstone 后使 renew 失败，远端 runtime观察撤销并 stop/release。DELETE 等待 release 或 expiry，不能在远端 runtime 仍持有 lease 时擦除 secret/row。
- 任意 post-claim 异常都会 stop 未发布 channel、移除 registry/map 并释放 matching lease；stop 失败恢复只能收敛为“完整 ready active runtime”或“durable retryable tombstone”。
- Supervisor shutdown 即使 channel stop 报错，也会取消 lease heartbeat、移除 registry/map 并释放数据库 claim，避免测试或进程关闭后留下后台任务。

### 2.2 Secret rotation 与 pending ingest

- PATCH 写入新凭据前先建立独立于 binding row 的 pending-ingest owner；进程在 DB stage 前退出后仍可枚举和回收该 ref。
- DB stage 成功后 ack pending record，并由 row 的 `secret_cleanup_*` 字段继续拥有 candidate/rollback/superseded cleanup。
- 所有 cleanup 入口先恢复 `rotation_candidate` 状态机，再决定回收 candidate 还是 previous ref；非 deleting row 当前引用的 ref不能由通用 cleanup 删除。
- pre-stage `BindingNotFoundError` 和 `BindingCleanupPendingError` 分别映射 404/409，并直接回收本次尚未进入 row outbox 的 ref。

### 2.3 AIO cleanup durability

- backend create cancellation 的 file lock、generation fencing、destroy 和 durable acknowledgement仍由唯一 ownership worker 串行拥有。
- transient destroy failure 会同时保留进程内 operation 和 `cleanup_pending` record，不会在 `finally` 中错误确认。
- 尚未 materialize 的目标不会因一次 absent backend snapshot 或本地时间阈值被当作已清理；record 保留到目标实际出现并成功 destroy。
- lifecycle reconciliation 与 idle eviction 解耦；`idle_timeout=0` 只关闭 idle cleanup，不关闭 durable cleanup recovery。

### 2.4 Attachment cleanup liveness 与 health isolation

- global discovery 最多运行 2 个逻辑 active reads、8 个 released-slot quarantine reads 和 2 个持真实 slot的 saturated reads，实际 daemon readers 上限为 10。
- quarantine 满后仍有正常 path 的 admission；done future 会在后续读取前被全局 prune，不永久占表。
- 有效 job 写入 binding-specific index；per-binding health 不再依赖无法归属的 global `invalid/timed_out` 状态。

---

## 3. 数据库与持久化变更

Alembic revision `2026_07_17_channel_deletion_state` 在既有删除/cleanup 字段之外新增：

```text
runtime_lease_expires_at  TIMESTAMP WITH TIME ZONE  NULL
```

runtime 相关字段现在共同表达：

```text
runtime_lease_token       VARCHAR(64)  NULL
runtime_lease_expires_at  TIMESTAMP    NULL
runtime_generation        INTEGER      NOT NULL DEFAULT 0
```

`downgrade()` 会删除包括 expiry 在内的全部本 revision 字段；SQLite 和 PostgreSQL 测试均断言完整列语义，随后重新升级到 head。

SecretStore 在 `${DEER_FLOW_HOME:-.deer-flow}/secret-store/feishu/.pending/` 下维护不含明文的 pending-ingest metadata；它只保存 ref、owner/binding identity 与回收时间，用于在 binding row stage 之前提供 durable ownership。

有效 attachment cleanup job 同时维护 `${DEER_FLOW_HOME:-.deer-flow}/published-attachment-cleanup/.binding-index/<binding-hash>/` 索引；job 完成后索引与主 outbox 一起删除。

---

## 4. 自动化验证

### 4.1 TDD 专项证据

第十轮修复先以故障注入/barrier 用例复现：

```text
P1 初始专项：3 failed
P1 修复后：3 passed
P2 初始专项：4 failed（quarantine 用例随后加强为超过上限的确定性场景）
P2 修复后：5 passed
补充 recovery 专项：3 passed
```

### 4.2 第十轮直接回归

覆盖 repository、Owner Channel API、Supervisor、AIO provider、Feishu parser/cleanup、WebSocket lifecycle、Gateway services、SecretStore 与 migration：

```text
171 passed, 1 skipped, 5 warnings in 41.07s
```

唯一 skip 为本地未提供 PostgreSQL。可在 CI 强制执行：

```bash
REQUIRE_POSTGRES_TESTS=1 pytest tests/test_user_model_capabilities_migration.py -q
```

### 4.3 M3 聚焦回归

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 测试文件：

```text
358 passed, 8 skipped, 5 failed, 6 warnings in 68.40s
```

5 项失败与此前 review 记录的 Windows LocalSandbox 基线完全一致，均位于本轮未修改的 `test_local_sandbox_provider_mounts.py`：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项为 Windows host path 反向映射/roundtrip，1 项依赖本机不存在的 `/bin/sh`。本轮新增或修改的 M3 测试没有失败。

### 4.4 静态、边界、编译与差异检查

```text
ruff check <16 changed Python files>: All checks passed!
ruff format --check <16 changed Python files>: 16 files already formatted
tests/test_harness_boundary.py: 1 passed
python -m compileall <changed source/migration targets>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

### 4.5 全量 backend Gate

已执行：

```bash
pytest tests -q
```

该命令在本地 Windows runner 的 300 秒门限内运行到约 52%，已经出现多项仓库/环境基线失败，但超时前没有生成最终失败摘要：

```text
command timed out after 300.7s
progress: 52%
```

因此本报告不宣称全量 backend Gate 通过，也不把未完成运行中的 failure 数量当作稳定结论。第十轮受影响路径由 4.2 的直接集合全绿以及 4.3 的完整 M3 聚焦集合覆盖。

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 中以 `REQUIRE_POSTGRES_TESTS=1` 强制执行完整 upgrade/downgrade 与 schema Gate。
2. 使用至少两个真实 Gateway replicas 验证 lease heartbeat、并发 start/delete、远端撤销和 expiry 接管。
3. 以真实进程 kill/restart 验证 provisional runtime、pending secret ingest 与 AIO `cleanup_pending` 的跨进程恢复。
4. 在远程 AIO/provisioner 环境验证 `list_running()` 一致性、materialization 延迟和 transient destroy retry。
5. 使用两个真实 Feishu App 验证 credential rotation、WebSocket readiness、attachment recovery 与跨 binding health isolation。
6. 在 Linux CI 或修复既有 Windows LocalSandbox 基线后取得 M3 全绿结果。
7. 在可完成的 runner 上取得全量 backend `pytest tests -q` 最终汇总。

---

## 6. 最终判定

**第十轮 Review 指出的 3 项 P1、4 项 P2 与 3 组 Standards 缺口已在代码侧关闭；当前没有已知的第十轮未修复 P1。**

由于真实 PostgreSQL、多 Gateway、进程 kill/restart、远程 AIO/Feishu Gate 尚未执行，M3 聚焦集仍有 5 项既有 Windows LocalSandbox 基线失败，且本地全量 backend 在 300 秒内未完成，本报告不宣称最终 `Ready to merge`。这些剩余项属于环境、部署和仓库全量 Gate，不是第十轮仍未修复的代码级 P1/P2。
