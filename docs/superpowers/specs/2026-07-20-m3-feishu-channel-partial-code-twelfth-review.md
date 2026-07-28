# 多租户 Agent 发布平台 - M3 飞书渠道部分第十二轮代码复审

**状态：** 已复审，仍有阻塞问题
**日期：** 2026-07-20

**关联文档：**

- M3 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十一轮复审：[2026-07-20-m3-feishu-channel-partial-code-eleventh-review.md](./2026-07-20-m3-feishu-channel-partial-code-eleventh-review.md)
- 第十一轮修复报告：[2026-07-20-m3-feishu-channel-partial-eleventh-review-fix-report.md](./2026-07-20-m3-feishu-channel-partial-eleventh-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后当前未提交工作区中与第十一轮修复相关的 backend 代码、迁移、测试和文档
- 排除项：`config.yaml`、frontend 修改、图片以及既有临时目录等与本轮 M3 修复无关的工作区内容

---

## 1. 复审结论

第十一轮的以下修复已经实质生效：

- 新 POST/PATCH 路径使用数据库 secret-ingest owner；janitor 的旧枚举快照不能再删除已经由 binding 原子接管的 ref。
- AIO 周期性 `list_running()` 只观察未知实例，不再直接把其他 Gateway 的 sandbox 收入本地 warm pool。
- 过期的 `creating` lifecycle record 可以被新 provider fencing 为 `cleanup_pending` 并回收已 materialize 的 sandbox。
- cleanup 全局扫描在 8 个 daemon quarantine 后改用可终止子进程，10 个 hung path 后仍能推进到正常尾部。

但本轮仍发现 **2 个 P1、2 个 P2**：

1. Feishu runtime lease 被接管或清理后，旧 transport 可以继续运行，形成同一 binding 双实例或无租约幽灵实例。
2. AIO 普通 acquire/discovery 会无条件抢占未过期 live owner，随后可以销毁旧 Gateway 正在使用的 sandbox。
3. secret ingest 的数据库 grace 与进行中的文件写之间仍有竞争，可产生数据库不可枚举的永久孤儿密文。
4. DELETE 的 attachment backlog 安全扫描未使用可终止 reader，单个挂死文件读取可令请求永久不返回并耗尽线程池。

Standards 轴另有 **2 个 Important、1 个 Minor**：新增迁移状态缺少完整 schema 断言；M3/全量 backend Gate 仍未闭环；focused regression 文档漏掉 SecretStore 和 migration 测试。

**Ready to merge：No。** 两个 P1 直接破坏 F3.2 的单 binding 生命周期和跨 Gateway failure isolation；修复并补齐确定性并发回归前不应进入 M3 Review Gate。

---

## 2. 第十一轮问题关闭状态

| 第十一轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1：租约释放/过期不能证明 Feishu transport 已停止 | **部分关闭** | stop 失败时本地 running entry/token 通常会保留，但 lease expiry/takeover、lost-token monitor 和 unpublished cleanup 仍能留下活 transport |
| P1：pending-secret janitor 的 owner-check/delete TOCTOU | **原时序已关闭，出现新窗口** | DB claim/transfer 关闭了“枚举快照后 binding 接管”竞态；但 janitor 仍可在 ciphertext 写入完成前删除 ingest owner，写者随后落出孤儿密文 |
| P1：AIO 周期 reconcile 收养并销毁其他 Gateway sandbox | **部分关闭** | passive reconcile 已关闭；普通 acquire 的 explicit discovery/adoption 仍会抢占未过期 live owner |
| P2：10 个 hung reader 后正常尾部永久饥饿 | **主扫描路径已关闭** | 第 9 个以后使用 killable child process；10-hung 与 Windows spawn 回归通过 |
| P2：进程 kill 于 `creating` 后不恢复 | **已关闭** | lease 过期后可在 file lock 内转为 `cleanup_pending`，且 `idle_timeout=0` 仍执行 reconcile |
| P2：POST `SecretStore.put → DB create` 无 durable owner | **部分关闭** | DB owner 已先于 ciphertext；但 owner 的固定 grace 没有覆盖仍在进行的文件写，详见 P2-1 |

---

## 3. Spec / 正确性发现

### 3.1 P1-1：runtime lease 被接管后旧 transport 不会自我停止

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L456)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L444)
- [README.md](../../../backend/README.md#L210)
- [CLAUDE.md](../../../backend/CLAUDE.md#L526)

`_monitor_runtime_lease()` 在数据库异常时只记录日志并继续使用本地 runtime。此时数据库 lease 会自然过期，另一 Gateway 的 `claim_runtime()` 可以覆盖旧 token 并启动同一 binding。旧 Gateway 恢复数据库连接后，`renew_runtime()` 因 token 不匹配返回 false，随后 `renew_quiescing_runtime()` 又因 row 不是 `deleting/runtime_stop_requested` 返回 false；当前代码在该分支直接退出 monitor，没有调用 `_stop_runtime()`。

因此确定性时序为：

1. Gateway A 已持有 binding runtime，随后到数据库的连接暂时失败，但 Feishu WebSocket 线程仍正常收事件。
2. A 的数据库 lease 过期；Gateway B 在启动新 transport 后 claim 过期 token 成功。
3. A 恢复数据库连接，发现 token 已变化，却直接退出 heartbeat monitor。
4. A、B 的 transport 均继续运行，且 A 再无 stop retry owner。

独立复现结果为：

```text
old_still_running=True
new_running=True
old_stop_count=0
```

另有两个同根路径：

- `_await_runtime_quiesced()` 会把时间戳到期当作 stop acknowledgement，清 token 并允许 STOP/DELETE 继续；本轮临时回归确定性得到“DB 已为 `inactive` 且 token 为空，但 owner channel 仍为 running”。
- `_discard_unpublished_runtime()` 即使 `channel.stop()` 抛错，仍无条件 release provisional claim；启动/确认竞争失败后可留下不再受 Supervisor 管理的无租约 transport。

这与 README/CLAUDE 当前写出的“lease expiry alone cannot authorize replacement or physical delete”相矛盾，也违反 F3.2 的 stop/restart/DELETE 生命周期语义。

**建议修复：**

- 把“丢失 matching token”本身视为本地 transport 必须停止的 fence，不要要求先取得 `renew_quiescing_runtime()` 才执行 local stop。
- stop 未确认时保留独立的 process-local retry owner；不能因 DB token 已被接管而放弃停止旧 transport。
- 不要在打开 WebSocket 后才 claim provisional lease；先取得 durable generation/lease，再启动 transport，并在 readiness 后 CAS confirm。
- unpublished cleanup 只有在 transport 实际退出后才 release；stop 失败必须进入可恢复状态。
- 用多 Supervisor 测试覆盖 DB partition → lease expiry → 新 owner claim → 旧 owner恢复，以及 remote STOP 等待期间 owner event loop/stop 卡住的时序。

### 3.2 P1-2：AIO explicit discovery 会抢占未过期 live owner 并销毁其 sandbox

**相关文件：**

- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py#L995)
- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py#L1042)
- [aio_sandbox_provider.py](../../../backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py#L1338)

本轮给 periodic reconcile 增加了 owner lease 检查，但同步和异步普通 acquire 路径在 `backend.discover(sandbox_id)` 命中后都会直接调用 `_mark_durable_sandbox_adopted()`。该方法不检查 lifecycle record 是否为另一个 provider 的 `active/adopted + 未过期 lease`，而是在 file lock 内无条件把 owner 改成当前 provider。

于是 Gateway B 请求与 Gateway A 相同的 thread 时，可以取得 A 的 live sandbox owner；B 后续 `destroy()`、warm idle eviction 或 shutdown 会以新 owner 身份销毁 backend target，而 A 仍在 `_sandboxes` 中把它当作活跃实例使用。确定性复现结果为：

```text
a_still_tracking_active=True
backend_destroyed=[sandbox_id]
```

这违反多租户 failure isolation，也说明第十一轮 AIO P1 只关闭了 passive reconcile，未关闭正常流量路径。

**建议修复：**

- explicit discovery 必须在 sandbox file lock 内读取 lifecycle record；对其他 provider 的未过期 live lease拒绝 takeover，或建立有完成语义的共享 use lease/交接协议。
- 只有 lease 已过期，或旧 owner 明确完成 handoff/release，才允许 CAS 增加 generation 并转移 owner。
- 增加双 provider 回归：A active 时 B acquire 不得改 owner；B 的 release/idle/shutdown 不得 destroy A 的 target；过期或明确 handoff 后才允许 B 接管。

### 3.3 P2-1：janitor 可在 ciphertext 写入中删除 ingest owner，留下永久孤儿密文

**相关文件：**

- [published_agent_channels.py](../../../backend/app/gateway/routers/published_agent_channels.py#L192)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L85)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L143)
- [secret_store.py](../../../backend/packages/harness/deerflow/publishing/secret_store.py#L140)

`_put_database_owned_secret()` 先创建 `pending` ingest，再调用 `put_reserved()`，这是正确方向；但 ingest 只有固定 120 秒 `not_before`，没有 `writing` claim 或 heartbeat。慢文件系统、线程池拥塞或进程长暂停可产生如下时序：

1. route reserve ingest 后开始写密文，但在 atomic replace 前暂停超过 grace。
2. janitor claim 到期 ingest；此时目标密文尚不存在，`delete()` 返回 false，janitor 仍删除 ingest row。
3. route 恢复并完成 atomic replace，密文文件现在存在。
4. `create_from_secret_ingest()` 因 ingest row 已消失而失败；route compensation 也无法再 claim 已删除的 row。

最终 `binding_created=False`、`due_ingests=[]`，但 ciphertext 仍可读取，形成数据库和 `.pending` 都无法枚举的永久孤儿文件。固定 grace 只能降低概率，不能提供所有权不变量。

**建议修复：**

- reserve 后先 CAS 为带 lease 的 `writing` 状态，并在可能超过 grace 的写入期间续租；janitor 只能 claim 明确过期且无活跃 writer 的 generation。
- 写入完成后用 matching token 将状态转为 `ready_to_transfer`；transfer/compensation/janitor 都比较同一 token/generation。
- 增加确定性测试：写者停在 atomic replace 前、janitor 到期执行完成、写者恢复；最终必须由 binding 或 cleanup owner 二者之一持有 ref，不能留下不可枚举密文。

### 3.4 P2-2：DELETE 的 attachment backlog 检查仍可无限阻塞

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L242)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L909)

全局 cleanup janitor 已把不可信 job 读取放入带 deadline 的 thread quarantine/killable child process，但 `has_published_attachment_cleanup_backlog()` 仍对每个 outbox path 直接执行无大小上限、无 deadline 的 `Path.read_text()` 和 `json.loads()`。`delete_binding()` 只用 `asyncio.to_thread()` 调用它，没有 `wait_for`，因此不会终止挂死的底层文件读取。

单个阻塞 path 会让 DELETE 永久不返回；重复 DELETE 还能持续占用默认 executor worker，最终影响同进程其他异步 offload。该问题绕过了第十一轮新增的可终止 reader boundary。

**建议修复：**

- DELETE 的安全扫描复用同一个有文件大小限制、deadline 和 killable process fallback 的 reader。
- 给整个 backlog scan 设置总预算；超时应 fail closed 为 retryable `409`，同时不能留下永久占用线程。
- 增加 DELETE + hung path 回归，并断言多次请求后正常 offload 仍可执行。

### 3.5 条件性迁移风险：旧 `.pending` 协议在滚动升级期间仍有原 TOCTOU

新版本自身不再创建 `.pending` 文件，因此不把此项计入正式 P2 数量。但若部署允许新旧 Gateway 滚动共存，旧版本 PATCH 仍可能在新版本 janitor 完成 owner 快照后接管 ref；兼容恢复路径随后会依据旧快照直接删除 ciphertext。若生产发布不是 stop-the-world，应把 legacy `.pending` 恢复也纳入 DB claim/CAS，或在升级窗口暂停该 janitor。

---

## 4. Standards 轴

### 4.1 Important-1：新增 migration 状态没有完整 schema Gate

**相关文件：**

- [2026_07_17_channel_deletion_state.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_17_channel_deletion_state.py#L19)
- [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py#L120)

SQLite/PostgreSQL upgrade/downgrade 测试仍未断言本轮新增的 `runtime_stop_requested`，也没有检查 `agent_channel_secret_ingests` 的列、三个索引、downgrade 后表消失和 re-upgrade 后恢复。测试当前可以通过，但不能证明第十一轮修复报告所称的完整 schema Gate。

建议补齐 SQLite 双向断言；PostgreSQL Gate 同样检查 boolean default、时间列类型、字符串长度、索引和 table lifecycle，并在 CI 使用 `REQUIRE_POSTGRES_TESTS=1`。

### 4.2 Important-2：M3 与全量 backend Review Gate 仍未闭环

第十一轮修复报告记录 M3 聚焦集合为 `376 passed, 9 skipped, 5 failed`，全量 `pytest tests -q` 在 300 秒、36% 时超时，真实 PostgreSQL、多 Gateway、进程 kill、remote AIO 和双 Feishu App Gate 均未执行。本轮 7 个直接相关测试文件同样在 304 秒上限内未生成最终汇总。

这不满足开发计划 M3 Review Gate 的 `make test` 全绿要求。已通过的拆分回归不能替代完整 Gate，最终合并结论仍应以 Linux/CI、真实 PostgreSQL 和多 Gateway 集成结果为准。

### 4.3 Minor-1：focused regression 文档漏掉本轮关键测试

**相关文件：**

- [README.md](../../../backend/README.md#L220)
- [CLAUDE.md](../../../backend/CLAUDE.md#L535)

两处 M3 focused regression 命令都漏掉 `tests/test_secret_store.py` 和 `tests/test_user_model_capabilities_migration.py`，因此照文档执行无法复现本轮 secret-ingest 与 migration Gate。应把两文件加入命令，并在 PostgreSQL CI 说明 `REQUIRE_POSTGRES_TESTS=1`。

---

## 5. 验证记录

### 5.1 拆分专项回归

runtime stop/lease 与 DB secret snapshot 五个定向用例：

```text
5 passed, 2 warnings in 3.39s
```

AIO lifecycle、10-hung/spawn reader、secret-ingest route 与 migration 定向集合：

```text
15 passed, 1 skipped, 2 warnings in 19.47s
```

唯一 skip 为本地 PostgreSQL 不可用。

### 5.2 确定性时序复现

- remote STOP 在 owner heartbeat 未运行时等待 lease expiry：复现测试通过，观测到 DB 为 `inactive`、token 已清，但 owner channel 和 Supervisor registration 均仍为 running。
- DB partition → lease takeover → owner恢复：观测到 old/new transport 同时 running，old stop count 为 0。
- AIO live-owner explicit adoption：观测到旧 owner 仍登记 active，而 backend 已被新 owner destroy。
- secret writer 与 janitor claim：观测到 binding 未创建、ingest row 已无、ciphertext 仍可读取。

临时复现测试未保留在工作区。

### 5.3 静态与差异检查

```text
ruff check <16 个相关 Python target>: All checks passed!
ruff format --check <16 个相关 Python target>: 16 files already formatted
git diff --check HEAD -- backend: passed
```

### 5.4 未完成 Gate

7 个直接相关测试文件的整组执行在 304 秒达到命令上限，pytest 未输出最终汇总；因此不把该次运行声明为通过或失败。第十一轮报告所列全量 backend、真实 PostgreSQL 和部署 Gate 仍保持未闭环状态。

---

## 6. 最终判定

第十一轮六项问题中，DB owner-transfer、被动 AIO reconcile、stale `creating` recovery 和 cleanup 主扫描 liveness 已有实质进展；但 runtime lease fencing 与 AIO active ownership 仍存在可确定性复现的 P1，secret 写入所有权及 DELETE cleanup scan 仍存在 P2。

**Ready to merge：No。**

建议修复顺序：

1. 先关闭 runtime lost-token/expiry 的旧 transport 自我 fencing，并把 provisional claim 移到 transport start 之前。
2. 再关闭 AIO live-owner 的无条件 explicit adoption。
3. 为 secret writer 引入 generation/lease，并统一 DELETE 与 janitor 的可终止 reader。
4. 补齐 migration schema 断言和 focused regression 文档，最后在 PostgreSQL、多 Gateway 与 Linux 全量 CI 中关闭 Review Gate。
