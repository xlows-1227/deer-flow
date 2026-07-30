# 多租户 Agent 发布平台 — M3 第十一轮 Review 修复报告

**日期：** 2026-07-20

**关联 Review：** [2026-07-20-m3-feishu-channel-partial-code-eleventh-review.md](./2026-07-20-m3-feishu-channel-partial-code-eleventh-review.md)

**分支：** `codex/m3-feishu-supervisor`

**固定点：** `044fa17489b1d064286b97ea88dee65ed08060fe`

**状态：** 第十一轮列出的 3 项 P1、3 项 P2 已完成代码修复和本地自动化回归。真实 PostgreSQL、多 Gateway/进程 kill、远程 AIO、双 Feishu App 和全量 backend 最终汇总仍属于环境/部署 Gate。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：lease release/expiry 不能证明 Feishu transport 已退出 | 已修复 | 新增 durable `runtime_stop_requested`。STOP/DELETE 先写撤销状态；只有原 channel 的 `stop()` 实际返回后才释放 matching lease。stop 抛错时保留原 runtime、token 和重试 ownership，不启动 replacement。远端 STOP 等待 owner acknowledgement，未完成时返回 retryable `409` | 覆盖 stop 抛错且 `is_running=True`、DELETE 不产生第二实例、远端 STOP 等待、owner 后续成功退出后再 deactivate |
| P1：pending-secret janitor 快照竞态误删当前 ref | 已修复 | 新增数据库 `agent_channel_secret_ingests` outbox。janitor 必须先取得 matching claim；create/stage 在同一数据库事务中删除 ingest 并转移 owner。旧快照在 transfer 后无法 claim，因此不能 erase current ref | barrier 用例在 janitor 取快照后完成 PATCH owner transfer，再恢复 janitor；当前 ref 和密文均保留 |
| P1：AIO 周期 reconcile 销毁其他 Gateway 的 active sandbox | 已修复 | AIO lifecycle 记录增加 provider instance owner、generation 和 lease。普通 `list_running()` 发现只观察、不放入本地 warm pool。idle/shutdown/destroy 在 sandbox file lock 内复核 owner；显式接管后旧 provider 也不能销毁 | 双 provider 共享 backend：B 多轮 reconcile 不 adopt/destroy A；ownership transfer 后 A 的 idle cleanup 只移除旧本地状态 |
| P2：10 个 hung cleanup reader 永久饿死正常尾部 | 已修复 | 常驻 daemon quarantine 上限固定为 8。达到上限后不再创建 saturated daemon thread，改用受两个逻辑槽限制的可终止子进程；超时执行 terminate/kill，继续推进 cursor | 10 个 hung path 后正常第 11 个 job 可发现；daemon reader 不超过 8；Windows spawn 子进程有效 job round-trip 通过 |
| P2：进程在 AIO `creating` 阶段被 kill 后无人恢复 | 已修复 | `creating` 在 backend create 前持久化 owner lease；周期任务续租。新 provider 只在 lease 过期后于 file lock 内把 matching generation 转为 `cleanup_pending`，目标 materialize 后 destroy 并确认 | stale `creating` + 已出现 backend target 被新 provider 销毁；未过期 live owner 不受影响；`idle_timeout=0` 仍执行 lifecycle reconcile |
| P2：POST create 的 `SecretStore.put → DB create` 无 owner 窗口 | 已修复 | POST 预生成 binding/ref，先提交 DB ingest owner，再写密文，最后由 `create_from_secret_ingest()` 原子创建 row 并转移 owner。route compensation 也必须取得 matching DB claim | “密文写入后、binding commit 前进程退出”由重启 janitor 回收；“binding commit 后、route 返回前退出”保留 row 和当前密文 |
| Standards：reader 注释与资源上限不一致 | 已修复 | 代码注释、README、CLAUDE 统一为“2 个逻辑槽、最多 8 个 daemon quarantine、随后使用可 kill 子进程”，删除旧的 10 个 saturated daemon reader 描述 | Ruff/format、聚焦集合和 10 hung 顺序回归通过 |
| Standards：migration/schema 覆盖不足 | 代码侧已修复 | Alembic head 增加 `runtime_stop_requested` 和 `agent_channel_secret_ingests` 表/索引；upgrade/downgrade/re-upgrade 的 SQLite schema 断言已更新 | migration 集成测试本地通过；真实 PostgreSQL 因无连接配置而 skip |
| Standards：全量 backend / PostgreSQL Gate | 未闭环 | 已重新执行全量 backend 并记录实际超时位置；未把未完成运行声明为通过 | `pytest tests -q` 300 秒到 36% 超时；`DATABASE_URL`/`POSTGRES_URL` 均未配置 |

---

## 2. 最终运行时不变量

### 2.1 Runtime stop/delete

- `runtime_stop_requested` 是 active STOP 的 durable fence；`status=deleting` 是 DELETE fence。
- live owner 观察到 fence 后进入 quiescing 模式：普通 heartbeat 停止，但 matching token 的 quiescing lease 会续期，直到同一个 transport 的 `stop()` 真正返回。
- stop 失败不会 pop `_running`、不会释放 token、不会恢复 `active`、不会创建 replacement；后续 heartbeat/janitor 在同一 ownership 下重试。
- 非 owner Gateway 的 STOP/DELETE 只能等待 matching token 被真实 owner 释放；超时返回可重试冲突。只有 owner 进程消失、lease 确实过期时才允许清除 fencing token，因为进程退出同时终止了该 transport。
- physical delete 仍拒绝任何残留 runtime token，因此 secret/row 不会早于 transport quiescence 被删除。

### 2.2 Secret ingest owner transfer

- POST/PATCH 都先生成 `secret_ref`，再写入 `agent_channel_secret_ingests`，最后调用 `put_reserved()` 写密文。
- create/stage 与 ingest row 删除处于同一个数据库事务；数据库 row/outbox 始终有且只有一个 durable owner。
- janitor 和 route compensation 都先以 ref + identity + token 执行 claim CAS，删除密文后再 matching ack；transfer 已获胜时旧清理方无法 claim。
- 旧 `.pending` 文件协议仅保留向后兼容恢复，新 POST/PATCH 不再写入该协议。

### 2.3 AIO lifecycle ownership

- `creating`/active lifecycle record 包含 `owner_instance_id`、`operation_token`、`generation` 和 `lease_expires_at`。
- 周期 reconcile 不再无条件 adopt backend target；未识别 target 和其他 live owner 的 target 均不会进入本地 warm/idle eviction。
- stale `creating`/active 只能在 sandbox file lock 内转为 `cleanup_pending`，随后才允许 destroy。
- 显式 discovery/adoption 会转移 durable owner；旧 provider 的 idle、capacity eviction、explicit destroy 和 shutdown 都必须在 destroy 前重新核验 owner。
- absent backend snapshot 不会确认 cleanup；record 保留到 matching target 出现并成功销毁。

### 2.4 Attachment cleanup reader liveness

- 两个逻辑 read slots 控制同时进行的读取；前 8 个永久阻塞线程可进入 path-keyed daemon quarantine，并立即释放逻辑 slot。
- quarantine 达到 8 后，后续路径使用 spawn 子进程读取；每次超时都会 terminate/kill 子进程，不产生第 9、10 个永久 daemon reader。
- persisted discovery cursor 在超时路径后继续前进，因此多个坏路径不能永久遮蔽正常尾部 job。
- 完成的 quarantine future 会被清理；per-binding health 仍只读取本 binding 的 durable index。

---

## 3. 数据库与持久化变更

Alembic revision `2026_07_17_channel_deletion_state` 新增：

```text
agent_channels.runtime_stop_requested  BOOLEAN NOT NULL DEFAULT FALSE

agent_channel_secret_ingests
  secret_ref        VARCHAR(128) PRIMARY KEY
  agent_id          VARCHAR(64)  NOT NULL
  binding_id        VARCHAR(64)  NOT NULL
  owner_user_id     VARCHAR(128) NOT NULL
  state             VARCHAR(16)  NOT NULL DEFAULT 'pending'
  claim_token       VARCHAR(64)  NULL
  claim_expires_at  TIMESTAMP    NULL
  not_before        TIMESTAMP    NOT NULL
  created_at        TIMESTAMP    NOT NULL
  updated_at        TIMESTAMP    NOT NULL
```

并创建 agent、binding 和 `(state, not_before, claim_expires_at)` due 索引。`downgrade()` 删除 ingest 表并移除本 revision 的 runtime/cleanup/deletion 列；SQLite downgrade 后重新 upgrade 到 head 的测试已通过。

---

## 4. 自动化验证

### 4.1 TDD 专项

本轮先构造失败用例，再实现修复：

- transport stop 失败仍保持 live：初始 DELETE 传播错误/产生错误恢复语义；修复后保留单一 fenced runtime。
- 远端 STOP + owner stop failure：修复后等待 acknowledgement，不把 expiry 当作 live owner 已退出。
- janitor stale snapshot + PATCH transfer：初始可误删；修复后 claim CAS 失败且 current ciphertext 保留。
- 双 AIO provider periodic reconcile：初始 B adopt/warm；修复后 B 不接管、不销毁。
- stale AIO `creating`：初始不恢复；修复后 expired owner 被 fenced/cleanup。
- 10 hung cleanup paths：初始出现第 9 个 daemon/saturated reader；修复后正常尾部可达且 daemon 上限为 8。
- POST 两个 crash 注入点：pre-commit ciphertext 可恢复，post-commit current ref 不被补偿误删。

### 4.2 第十一轮直接回归

覆盖 repository、Owner Channel API、Supervisor、AIO provider、Feishu parser/cleanup、SecretStore 和 migration：

```text
128 passed, 1 skipped, 1 warning in 31.96s
```

唯一 skip 是本地没有 PostgreSQL。当前环境同时确认：

```text
DATABASE_URL_NOT_SET
POSTGRES_URL_NOT_SET
```

### 4.3 M3 聚焦回归

按后端文档执行 M3、legacy channels、attachment、sandbox、user-context、Gateway service、SecretStore 和 migration 集合：

```text
376 passed, 9 skipped, 5 failed, 6 warnings in 81.15s
```

5 个失败与此前记录的 Windows LocalSandbox 基线相同，均位于本轮未修改的 `test_local_sandbox_provider_mounts.py`：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项是 Windows host path reverse mapping/round-trip，1 项依赖本机不存在的 `/bin/sh`。第十一轮原有的 cleanup quarantine 聚合集顺序失败已不再出现。

### 4.4 静态、格式、编译与差异检查

```text
ruff check <16 changed Python targets>: All checks passed!
ruff format --check <16 changed Python targets>: 16 files already formatted
python -m compileall <changed source/migration targets>: passed
git diff --check -- backend: passed
```

### 4.5 全量 backend Gate

已执行：

```bash
pytest tests -q
```

本地 Windows runner 在 300 秒时运行到 36% 并超时，期间已出现多项失败，但 pytest 尚未生成最终失败摘要：

```text
command timed out after 300.5s
progress: 36%
```

因此本报告不声明全量 backend Gate 通过，也不把未完成运行中的 failure 数量作为稳定结论。

---

## 5. 尚未关闭的环境/部署 Gate

1. 在真实 PostgreSQL CI 中设置 `REQUIRE_POSTGRES_TESTS=1`，执行 upgrade/downgrade/re-upgrade 与完整 schema Gate。
2. 使用至少两个真实 Gateway replicas 验证 remote STOP、owner heartbeat/quiescing retry、进程退出后的 lease expiry 接管，以及不会双开 Feishu transport。
3. 用真实进程 kill/restart 验证 DB secret-ingest、AIO stale `creating` 和 runtime stop fence 的跨进程恢复。
4. 在远程 AIO/provisioner 环境验证 lifecycle owner lease、materialization 延迟和 destroy retry。
5. 使用两个真实 Feishu App 验证 WebSocket readiness、credential rotation、stop failure 和 attachment recovery。
6. 在 Linux CI 或修复既存 Windows LocalSandbox 基线后取得 M3 全绿结果。
7. 在可完成的 runner 上取得全量 backend `pytest tests -q` 最终汇总。

---

## 6. 最终判定

**第十一轮 Review 指出的 3 项 P1 和 3 项 P2 已在代码侧关闭；当前没有已知的第十一轮未修复 P1/P2。**

由于真实 PostgreSQL、多 Gateway、进程 kill、远程 AIO/Feishu 部署 Gate 尚未执行，M3 聚焦集仍有 5 项既存 Windows LocalSandbox 失败，且全量 backend 在 300 秒内未完成，本报告不宣称最终 `Ready to merge`。这些剩余项是环境、部署与仓库全量 Gate，不是第十一轮仍未实现的代码修复。
