# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十九轮代码复审

**状态：** 已复审；第十八轮多数问题已关闭，但发现 1 个 P2、1 个 P3，仓库级全绿 Gate 仍未关闭
**日期：** 2026-07-22

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十八轮复审：[2026-07-21-m3-feishu-channel-partial-code-eighteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-eighteenth-review.md)
- 第十八轮修复报告：[2026-07-22-m3-feishu-channel-partial-eighteenth-review-fix-report.md](./2026-07-22-m3-feishu-channel-partial-eighteenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点 / 当前 `HEAD`：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后当前未提交的 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- 重点：第十八轮 6 个 P3、聚合测试 Important 的关闭情况，以及本轮 claim 补偿、health ordering、scanner ownership、serving/ownership 和 shutdown 的对抗性复核

---

## 1. 复审结论

第十八轮的 A-1、A-4、B-1、C-1 已实质关闭；router + repository + supervisor 同进程聚合测试也已恢复为可确定结束。A-2 与 A-3 的主路径已经修复，但各自仍留下一个未被现有回归覆盖的并发边界：

1. **P2：deadline claim 没有形成可证明收敛的补偿 ownership。** `claim_task.cancel()` 后，只要数据库提交已经生效而协程最终以 `CancelledError` 结束，done callback 就直接返回，不会重读 token 或补偿释放。即使 callback 收到了成功行，补偿 release 也是一次性 best-effort；异常或 CAS miss 被吞掉后任务即被遗忘。`shutdown()` 也不会等待两个 late-task 集合，就可以标记完成并释放 leader fence。该路径能永久保留一个 runtime token，令单个 binding 的 start/stop/delete 一直失败，直至 Gateway 重启后的 leader recovery。
2. **P3：凭据轮换未切换 health fencing epoch。** `update_credentials()` 在 `runtime_generation` 和 `runtime_lease_token` 都不变时把 `health_revision` 重置为 0。并发的旧凭据 `test_binding()` 随后会分配 revision 1，并通过 generation/token/revision CAS，把新凭据对应的健康态写成旧凭据的探测结果。inactive binding 没有后续 restart health projection，因此错误状态可以持久保留。

Standards 轴另有 1 个延续 Important：完整 backend suite 仍在范围外 auth 用例首先失败，仓库级全绿 Gate 未满足；另记录 3 个不影响本轮 correctness 的结构性 Minor。

**Ready to merge：No。** P2 必须先关闭；P3 应与之一起补回归。真实 PostgreSQL、真实双 Feishu App 和完整 suite 全绿仍是最终发布 Gate。

---

## 2. 第十八轮问题关闭状态

| 第十八轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| A-1 P3：fallback 绕过 CAS 写共享 `_health` | **已关闭** | timeout / persistence failure fallback 只作为当次调用返回值；共享 `_health` 仍只在 durable CAS 成功且 revision process-locally latest 时更新 |
| A-2 P3：同 generation 迟到 health 覆盖较新结果 | **部分关闭** | 同一 credential/runtime epoch 内的 revision ordering 已正确；但 `update_credentials()` 清零 revision 而不切换 generation/token，使旧 credential probe 可进入新凭据健康态，见 3.2 |
| A-3 P3：吞取消 claim 阻塞 peers/janitor | **部分关闭** | claim 已移出 binding lifecycle lock，peer/janitor 隔离成立；但提交结果不明、补偿失败和 shutdown 三条路径仍可遗留 token，见 3.1 |
| A-4 P3：`test_binding` 与 restart 并发时 stale CAS 逃逸为 500 | **已关闭** | `_StaleHealthProjectionError` 后重读当前 durable row 并返回当前 health/serving；正式回归通过 |
| B-1 P3：scanner `recv()` 反序列化异常遗忘 live slot | **已关闭** | send/poll/recv/响应校验均收敛到 `_fail_scan_slot()`；只在最终确认退出后遗忘，否则 retain + stop fence |
| C-1 P3：quiescing health callback 重投 `running=True` | **已关闭** | callback 使用 `_is_serving()`，quiescing transport 只保留 ownership，不再报告 serving |
| Standards Important：router + supervisor 聚合挂起 | **已关闭** | repository + router + supervisor 同进程 `72 passed` 并正常退出；正式 5 文件 Gate `156 passed` |
| Standards Important：完整 suite 未全绿 | **未关闭（延续）** | 本轮 fail-fast 仍为 `320 passed, 1 failed`，首个失败仍是范围外 auth 用例，见 4.1 / 5.3 |

---

## 3. Spec 轴

### 3.1 P2：detached runtime claim 可在取消、补偿失败或 shutdown 边界遗留永久 token

**置信度：高**

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L531)：deadline claim、detach 和 late release
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1196)：`_release_runtime_claim()` 吞异常并以 `None` 表示未释放
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1897)：`shutdown()` 未处理 `_late_runtime_claim_tasks` / `_late_runtime_release_tasks`
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L541)：claim 在 `session.commit()` 后返回行
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L448)：现有用例只覆盖“吞取消后正常返回成功行”
- [backend/CLAUDE.md](../../../backend/CLAUDE.md#L526)：文档要求迟到提交按 exact token 补偿释放

当前实现有三条未收敛路径：

1. `_detach_runtime_claim()` 先登记 task，再调用 `task.cancel()`。done callback 对 `CancelledError` 直接 `return`。数据库 commit 是具有结果不确定窗口的外部写：如果服务端已经提交，但客户端在 commit acknowledgement 返回前收到取消，持久 token 已存在，task 却只暴露 `CancelledError`，于是没有任何 release。
2. 对正常返回的迟到 claim，callback 只创建一次 `_release_late_runtime_claim()`。其下层 `_release_runtime_claim()` 会把 DB 异常记录后转换成 `None`；同 token 的 generation 在“重读 current”和真正 release 之间再次推进时也会 CAS miss 返回 `None`。上层不检查结果、不重试，done callback 随后从集合移除该任务。
3. `shutdown()` 只停止 janitor 和 `_running` 中的 owner；当 `_running` 为空时，即使 late claim/release 集合仍非空，也会释放 leader fence并设置 `_shutdown_complete=True`。lifespan 后续可继续关闭其余资源并结束 event loop，late callback 不再有可靠执行窗口。

本轮最小复现得到：

```text
claim-cancel outcome: durable token retained, compensation calls=0
shutdown outcome: complete=True, leader fence released, late claim still pending
```

这不是普通 30 秒 lease 过期即可自愈：`claim_runtime()` 明确不允许其他 token 依据过期时间接管，只有 matching explicit release 或新进程取得 OS leader fence 后的 orphan recovery 能清除。因此一个普通 deadline 事件即可让该 binding 在当前 Gateway 生命周期内持续不可管理，违反 F3.2“单绑定动态启停，不重启 Gateway”的核心契约。

**建议修复：**

1. 不要把 `CancelledError` 当作“确定未提交”。对 outcome-ambiguous 的 claim 按 exact token 重读并 reconcile；或者用数据库级 statement/transaction deadline 保证 repository 返回前提交结果已确定，避免直接取消未决 writer。
2. late release 的 `None` / 异常必须表示 cleanup 未收敛，保留一个可重试 owner；每次重读同 token 的最新 generation 后重试，直到 token 消失、变成其他 token或行被删除。
3. `shutdown()` 必须显式 drain late claim/release ownership，未收敛时不得宣称 shutdown complete 或提前释放 leader fence；同时保持 gateway shutdown 的总 deadline。
4. 新增正式回归：commit 生效后返回前取消、首次 release 异常、同 token generation 再推进导致首次 CAS miss、claim 阻塞时直接 shutdown。

### 3.2 P3：凭据轮换清零 `health_revision`，但旧凭据 probe 仍拥有相同 generation/token

**置信度：高**

**相关文件：**

- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L693)：`update_credentials()` 清零 health/revision，但不推进 generation
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1429)：rotation 先更新 credentials，active binding 才 stop/start
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1851)：`test_binding()` 在 probe 前读取 row，probe 后才分配 health revision
- [supervisor.py](../../../backend/app/channels/supervisor.py#L367)：health CAS 只携带 generation/token/revision
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L853)：repository 不校验 secret/credential epoch

确定性时序如下：

1. inactive binding 的 `test_binding()` 读取 `secret_ref=old`、generation `N`、token `None`，随后用旧 secret 调用远端 probe。
2. 并发 PATCH 将 row 改为 `secret_ref=new`，但 generation/token 仍是 `N/None`，同时把 `health_revision` 重置为 0。
3. 旧 probe 返回 healthy；`_record_health()` 此时才分配 revision 1。
4. repository 看到 `N/None/1` 对当前 `N/None/0` 全部匹配，接受该写入，于是 new credential 被标成 old credential 的 healthy。

本轮最小复现输出：

```text
credential-race outcome: new-ref is reported healthy by old-secret probe
```

active rotation 通常会被后续 restart health 覆盖，但仍有错误可见窗口；inactive rotation 没有后续 restart，错误状态会一直保留。仅“不清零 revision”不足以解决，因为 revision 是 probe 完成后分配，旧 probe仍可能获得更大的序号。这里缺失的是 credential epoch fence。

**建议修复：** 在 credential update / rollback 时推进一个会被 health CAS 校验的 epoch。可以推进 `runtime_generation`，也可以新增独立 `credential_revision` / expected `secret_ref`；关键是旧 row 发起的 probe 必须在凭据切换后 CAS fail。补充 active 与 inactive 两条 rotation-vs-test 并发回归，并断言 DB 与 process-local health 都不发布旧凭据结果。

---

## 4. Standards 轴

### 4.1 Important（延续）：仓库级完整测试 Gate 仍未全绿

[backend/CLAUDE.md](../../../backend/CLAUDE.md#L673) 要求每次 feature / bug fix 运行完整 suite 且全部通过；[backend/CONTRIBUTING.md](../../../backend/CONTRIBUTING.md) 的提交前检查也要求 tests pass。

本轮重新执行 fail-fast，首个失败与第十八轮报告一致：

```text
FAILED tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
1 failed, 320 passed, 7 warnings in 122.72s
```

该失败不属于当前 M3 diff，但硬 Gate 仍然是未通过状态，不能声明整个仓库 standards-ready。

### 4.2 Standards Minor

| 类型 | 位置 | 说明与建议 |
|---|---|---|
| Duplicated Code | `supervisor.py:1866-1874`、`1887-1895` | stale probe 的两条分支重复重读 row 并构造 `BindingHealth`；抽出 `_current_binding_health(binding_id)` 可减少后续字段漂移 |
| Divergent Change / Data Clumps | `agent_channel/sql.py` 的 `AgentChannelRepository` | 单类混合 ingest、runtime、health、cleanup、delete 等约 44 个方法，`(agent_id, binding_id, owner_user_id)` 与 `dict[str, Any]` 反复传递；建议 M3 合并后以 `BindingKey`/typed row 和职责仓储分拆，避免与本轮 fencing 修复混改 |
| Repeated Switches / Primitive flag bundle | `supervisor.py:183-194`、`432-475` | enum 已封闭可选策略，但 value 仍是四布尔元组，行为分支散在 convergence 中；后续新增策略时可改为具名策略方法或按策略封装行为 |

这些 Minor 不单独阻塞本轮修复，但应登记为合并后维护项。

---

## 5. 验证记录

### 5.1 核心聚合 Gate

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py

72 passed, 1 warning in 75.42s
```

命令正常退出，未复现第十八轮之前的 cross-runner / event-loop teardown 挂起。

### 5.2 第十八轮正式 5 文件 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

156 passed, 5 warnings in 67.17s
```

### 5.3 完整 backend suite（fail-fast）

```text
1 failed, 320 passed, 7 warnings in 122.72s
failed: tests/test_auth_type_system.py::test_csrf_does_not_exempt_old_login_path
```

失败与当前 M3 修改无关，但仓库级 Gate 不能记为通过。

### 5.4 对抗性最小复现

```text
claim-cancel outcome: durable token retained, compensation calls=0
shutdown outcome: complete=True, leader fence released, late claim still pending
credential-race outcome: new-ref is reported healthy by old-secret probe
```

复现脚本仅用于本轮审计，运行后已删除，未写入产品代码或正式测试目录。

### 5.5 静态、格式与差异检查

```text
ruff check --no-cache <10 个第十八轮直接相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 10 个文件>
10 files already formatted

git diff --check 044fa17489b1d064286b97ea88dee65ed08060fe -- backend
passed
```

uv 在命令结束时提示无法打开既有 `.venv/.lock`，但上述专项、聚合、ruff 和 format 命令均已实际运行并以 exit code 0 完成；完整 suite 的 exit code 1 来自已列明的 auth 断言失败。

---

## 6. 尚未关闭的环境 / 部署 Gate

1. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 migration upgrade/downgrade/re-upgrade，以及 claim/release 与 health fence 的真实事务取消/并发语义。
2. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway 退出/重启、scanner child failure 与 attachment recovery。
3. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
4. 由 auth 所有者关闭 `test_csrf_does_not_exempt_old_login_path`；Windows LocalSandbox 既有平台差异继续按其独立契约处理。

---

## 7. 最终判定

第十八轮的 health fallback、restart stale probe、scanner response ownership、quiescing serving 语义和聚合测试挂起均已关闭；新 `health_revision` 也正确阻止了同一 runtime/credential epoch 内的乱序覆盖。

但是，deadline claim 的补偿仍没有覆盖提交结果不明、一次 release 失败和 supervisor shutdown 三个关键出口，能够留下需要 Gateway 重启才能恢复的 durable token，属于 **P2 阻塞项**。credential rotation 也尚未进入 health fence 的 epoch 模型，属于 **P3**。

**Ready to merge：No。** 建议先把 late claim 变成可重试、可 shutdown-drain 的显式 ownership，再让 credential update 推进 health 可校验的 epoch；新增上述四类 claim 回归和 active/inactive rotation-vs-test 回归后，重新执行三文件聚合 Gate、正式 5 文件 Gate、完整 suite 与 PostgreSQL CI Gate。
