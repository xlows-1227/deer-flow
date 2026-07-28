# 多租户 Agent 发布平台 — M3 第九轮 Review 修复报告

**日期：** 2026-07-20
**关联 Review：** [2026-07-20-m3-feishu-channel-partial-code-ninth-review.md](./2026-07-20-m3-feishu-channel-partial-code-ninth-review.md)
**状态：** 第九轮 Review 列出的 3 项 P1、4 项 P2 与 2 组 Standards 缺口已完成代码侧修复和本地自动化核验。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：DELETE stop 失败恢复出 active 半停止 runtime | 已修复 | stop 失败后立即移除不可信的本地 runtime/registry；只有新 runtime 再次 ready 且成功取得数据库 runtime claim，row 才能从 `deleting` 原子恢复为 `active`。恢复失败则保留 unhealthy、可重试 tombstone | 覆盖 stop 部分失败后重新 ready、恢复失败保留 tombstone，以及旧 runtime 不再留在 registry |
| P1：AIO destroy 中取消导致提前释放锁和重复 destroy | 已修复 | generation 检查、file-lock ownership、destroy 与 lifecycle 收敛全部移交给同一个同步 worker；取消方 shield 并等待该 worker，不再启动第二条 fallback destroy | 真实阻塞 destroy barrier 中取消 cleanup，断言 destroy 只调用一次，worker 返回前 lifecycle record/锁不被提前释放 |
| P1：最终 SELECT 后仍可跨 Gateway stale-start | 已修复 | ready runtime 注册前必须原子写入 `runtime_lease_token` 并推进 `runtime_generation`；DELETE tombstone 会撤销 token 并推进 generation，因此删除提交在最后 SELECT 之后也能使旧启动 claim 失败 | 精确 barrier 覆盖“最终读取已经返回、注册尚未发生”时另一 Gateway 写入 tombstone，旧 Gateway 未注册 runtime |
| P2：AIO cleanup handoff 无法跨进程恢复 | 已修复 | 取消意图先持久化为 `cleanup_pending`；provider startup 在 deterministic sandbox file lock 下扫描 lifecycle records，销毁仍匹配的遗留容量，成功后才删除 record | 覆盖新 provider 启动恢复 `cleanup_pending`，断言遗留 sandbox 被 destroy 而非 adopt；真实进程 kill/restart 保留为部署 Gate |
| P2：两个挂起 cleanup reader 永久饿死正常 job | 已修复 | global/per-binding discovery 统一走同一个有界扫描器；超时 reader 按 path 隔离到全局 quarantine，逻辑槽立即释放，同一路径在原线程结束前不重复提交；扫描继续推进到后续文件。health property 改为 cache-only，异步刷新负责 durable projection | 两个永久挂起文件加一个正常尾部 job，正常 job 在 6 轮内被发现，活跃 reader 始终有界；per-binding/health 回归通过 |
| P2：startup 只重试 tombstone 一次 | 已修复 | 公开单轮 `recover_cleanup_state()` 固定执行 attachment cleanup、secret cleanup、全部 deleting tombstone 收敛；janitor 每轮重复执行，不再只依赖 startup 或外部 DELETE | startup 时存在 backlog，单轮 janitor 清空 backlog 后自动删除 tombstone/secret/row，无需额外 API 调用 |
| P2：rotation secret erase 失败产生不可重试孤儿 | 已修复 | binding row 新增 durable secret-cleanup 状态；新 secret 在 row 切换前先 stage，失败回滚转为 `rotation_rollback`，成功切换转为旧 ref 的 `rotation_superseded`；matching acknowledgement 后才清空 cleanup 状态，未清完时拒绝下一次 rotation | 覆盖新 ref rollback erase 失败、旧 ref superseded erase 失败、janitor 重试，以及原始 502/成功业务语义不被 cleanup 异常覆盖 |
| Standards：新增测试签名类型不完整 | 已修复 | 新增/修改的 AIO、Supervisor、cleanup、migration 测试及 callbacks 补齐 fixture、参数和返回值类型 | Ruff/format 通过，书面签名要求已落实 |
| Standards：迁移未直接验证新增列和 downgrade | 已修复 | SQLite 迁移专项直接检查列名、类型、nullable/default，并执行 downgrade 验证列全部删除后重新 upgrade head；PostgreSQL 路径检查同一组列 | 迁移专项包含在直接回归；本地 PostgreSQL 不可用时 1 项 skip，CI 可用 `REQUIRE_POSTGRES_TESTS=1` 强制缺库即失败 |

---

## 2. 最终运行时不变量

### 2.1 Binding 删除与 runtime fencing

- `deleting` row 是删除流程的唯一 durable owner；stop、attachment cleanup、secret erase 或 physical delete 失败都不会丢失重试依据。
- stop 返回失败后，旧 runtime 一律视为不可信并从本地 registry 移除。只有新的 ready runtime 成功完成数据库 claim，才允许恢复 `active`。
- runtime claim 使用 token 与单调 generation。标记 `deleting`、activate/deactivate/restore 等状态迁移会撤销旧 token并推进 generation。
- ready 后的最后一次 row read 不是注册凭证；原子 claim 才是允许写入 `_running` 和 dynamic registry 的提交点。
- janitor 每轮先处理 cleanup backlog，再重试全部 tombstone，因此 backlog 清空后无需重启或重复 DELETE 即可继续收敛。

### 2.2 AIO cancellation 与 crash recovery

- backend create 返回后的 generation 检查、file lock、destroy 和 lifecycle record 清理由一个不可分叉的 worker拥有。
- event-loop cancellation 只取消调用方等待，不会提前释放 fencing lock，也不会并发发起第二次裸 ID destroy。
- 未完成补偿以 `cleanup_pending` durable record 表示。新 provider 启动时先恢复该状态，再进行 warm sandbox adoption。
- 只有 matching sandbox 已成功销毁或确认不存在时才删除 cleanup record；异常会保留 record 供下一次 startup 重试。

### 2.3 Cleanup liveness 与 health projection

- global recovery、per-binding recovery 和异步 health refresh 共享同一个 bounded discovery 实现，不再分别叠加不可取消的 `to_thread` reader。
- 单个挂起 path 只拥有一个 quarantine worker；其逻辑 scan slot 会立即释放，后续 pass 可以继续扫描其他文件。
- persisted cursor 在超时文件之后继续前进，正常尾部 job 不会被永久饥饿。
- `attachment_cleanup_healthy` 只读取缓存投影，不在 event loop 上同步取得 file lock 或遍历 outbox；异步 refresh 更新 durable generation 投影。

### 2.4 Secret cleanup durability

- rotation candidate 在数据库切换前先写入 durable cleanup ownership；进程在任意阶段退出后，startup 可根据 row 当前 `secret_ref` 判断应保留 candidate 还是回收 candidate。
- rollback candidate 和已被替换的旧 secret 都通过 `secret_cleanup_ref/reason/not_before` 重试，erase 成功且 matching ack 后才释放 durable ownership。
- 已有 cleanup pending 时，新的 credential rotation 返回冲突，避免单槽 cleanup 状态被覆盖。
- physical binding DELETE 会先处理额外 cleanup ref，再擦除 row 当前 secret，最后删除 tombstone。

---

## 3. 数据库变更

Alembic revision `2026_07_17_channel_deletion_state` 在 `agent_channels` 增加：

```text
delete_previous_status        VARCHAR(16)  NULL
runtime_lease_token           VARCHAR(64)  NULL
runtime_generation            INTEGER      NOT NULL DEFAULT 0
secret_cleanup_ref            VARCHAR(128) NULL
secret_cleanup_reason         VARCHAR(32)  NULL
secret_cleanup_not_before     TIMESTAMP    NULL
rotation_previous_secret_ref  VARCHAR(128) NULL
```

`rotation_previous_secret_ref` 只用于恢复进程中断的 candidate rotation；cleanup matching ack 后与其余 cleanup 字段一起清空。downgrade 会删除以上全部列。

---

## 4. 自动化验证

### 4.1 第九轮直接回归

执行 repository、Owner Channel API、Supervisor、AIO provider、Feishu parser/cleanup、WebSocket lifecycle、Gateway services 与迁移链：

```text
155 passed, 1 skipped, 5 warnings in 33.85s
```

唯一 skip 为本地未提供 PostgreSQL。迁移测试已支持在 CI 使用：

```bash
REQUIRE_POSTGRES_TESTS=1 pytest tests/test_user_model_capabilities_migration.py -q
```

此时 PostgreSQL 不可用会直接失败，不会静默跳过。

### 4.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 测试文件：

```text
347 passed, 8 skipped, 5 failed, 6 warnings in 61.70s
```

5 项失败与第五至第九轮已记录的 Windows LocalSandbox 基线完全一致，均位于本轮未修改的 `test_local_sandbox_provider_mounts.py`：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项是 Windows host path 反向映射/roundtrip，1 项依赖本机不存在的 `/bin/sh`。本轮新增或修改的 M3 测试没有失败。

### 4.3 静态、格式、编译与差异检查

```text
ruff check <14 changed Python files>: All checks passed!
ruff format --check <14 changed Python files>: 14 files already formatted
python -m compileall <changed source/migration targets>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

### 4.4 全量 backend Gate

本轮未重复执行此前在当前环境连续超过 300 秒且没有最终汇总的全量 `pytest tests -q`。本报告不宣称全量 backend 测试通过。

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 中以 `REQUIRE_POSTGRES_TESTS=1` 执行完整 migration upgrade/downgrade Gate。
2. 使用至少两个 Gateway replicas 验证 runtime generation claim、并发 start/delete 和 tombstone janitor 收敛。
3. 真实进程 kill/restart 验证 AIO `cleanup_pending` 恢复，并在远程 provisioner 上验证 conditional ownership 行为。
4. 两个真实 Feishu App 验证 credential rotation、WebSocket readiness、secret cleanup retry 与 attachment backlog 运维。
5. 在 Linux CI 或修复 Windows LocalSandbox 既有基线后取得完整 M3 全绿结果。
6. 在可完成的 runner 上取得全量 backend `pytest tests -q` 最终汇总。

---

## 6. 最终判定

**第九轮 Review 的 3 项 P1、4 项 P2 与 2 组 Standards 缺口已在代码侧关闭，直接回归全部通过。**

由于真实 PostgreSQL、多 Gateway、进程 kill/restart、远程 AIO/Feishu Gate 尚未执行，且 M3 聚焦集仍有 5 项既有 Windows LocalSandbox 失败，本报告不宣称最终 Ready to merge；这些剩余项属于环境和部署验收，不是本轮尚未修复的代码级 P1。
