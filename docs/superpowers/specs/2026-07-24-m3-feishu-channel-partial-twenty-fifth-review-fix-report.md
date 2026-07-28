# M3 Feishu Channel Partial — Twenty-Fifth Review Fix Report

**日期：** 2026-07-24
**分支：** `codex/m3-feishu-supervisor`
**Review 提交：** `cc4c3eaeb6f0abc5d6c8a2314005edc32014fa9c`
**对应 Review：** `2026-07-24-m3-feishu-channel-partial-code-twenty-fifth-review.md`

## 1. 结论

第二十五轮新增的 Standards Important 已关闭。

`test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner` 不再依赖“解除 stop 故障后立即再次 shutdown”的自然调度时序。测试现在显式控制 cleanup retry 的 entered/release/recovered 三个阶段，并在第二次 shutdown 前依次确认：

1. 后台 cleanup retry 已成功完成 transport stop；
2. durable `runtime_lease_token` 已清除；
3. process-local `owned_binding_ids` 已收敛为空；
4. 同一 Supervisor 的 shutdown retry 才被允许完成并释放 leader fence。

验证过程中，review 要求的 91 项聚合还暴露了两个使用相同旧模式的 Supervisor 回归。它们已按同一标准同步修复，避免只修 Gateway 用例、却让同根因竞态从另一条测试路径继续出现。

**第二十五轮没有 Spec P1/P2；本轮 Standards Important 已关闭，当前范围内没有已知未关闭的 M3 生产代码 P1/P2。**

仓库级 Ready to merge / production release 仍为 **No**：完整 backend suite 的平台失败、真实 PostgreSQL Gate 和双 Feishu App Gate 尚未关闭。

---

## 2. Finding 处理结果

| Finding | 状态 | 修复 | 回归证据 |
|---|---|---|---|
| Standards Important：Gateway lifespan stop-failure 回归依赖后台调度时序 | 已关闭 | 增加 cleanup entered/release/recovered barrier；等待 durable token 和 local owner 收敛后再 retry shutdown。 | 目标用例连续 5 次通过；最终 shutdown 竞态三用例共同通过。 |
| Standards Important：`finally` 可能用 cleanup shutdown 覆盖主体失败 | 已关闭 | `finally` 始终释放 barrier；只在 ownership 已收敛时补做 shutdown；主体与 cleanup 同时失败时使用 `BaseExceptionGroup` 保留两者。 | 最终定向回归及 91 项聚合通过。 |
| 同根因残留：Supervisor quiescing transport shutdown retry 仍立即重试 | 已关闭 | 使用同样的 stop barrier、token/owner 收敛等待和双错误保留。 | `test_shutdown_remains_retryable_while_quiescing_transport_cannot_stop` 通过。 |
| 同根因残留：active runtime stop-failure 用例仍立即重试 | 已关闭 | 增加 recovered 事件；故障解除后等待 stop、durable release、local owner 全部完成。 | `test_stop_failure_preserves_active_runtime_and_status` 通过。 |
| Standards Important Gate：完整 backend suite 未全绿 | 未关闭，平台/环境 Gate | 本轮不修改 symlink、LocalSandbox 或 live bash 环境。 | 沿用第二十四轮完整 suite 分类结果。 |
| Standards Minor：Channel/Supervisor/Repository 结构债务 | 已登记，未混改 | correctness 测试同步不引入大规模职责拆分。 | 留作独立架构任务。 |

---

## 3. 确定性同步契约

### 3.1 首轮 shutdown

测试先让 transport stop 明确失败，等待后台 cleanup retry 进入 barrier，再断言：

- `_shutdown_complete is False`；
- 对应 binding 仍属于 `owned_binding_ids`；
- Gateway lifespan 场景的 leader fence 仍被持有。

这保证测试确实覆盖“shutdown 失败但 ownership 不能丢失”的生产契约，而不是仅断言一个偶然异常。

### 3.2 故障恢复

故障解除后按固定顺序执行：

1. 释放 cleanup retry barrier；
2. 等待 stop recovery 事件；
3. 轮询数据库直到精确 runtime token 被清除；
4. 轮询 Supervisor 直到 process-local owner 被移除；
5. 执行同一 Supervisor 的 shutdown retry；
6. 断言 shutdown complete、owner 为空、leader fence 已释放。

第二次 shutdown 不再与后台 lifecycle lock 或 SQLite durable release 竞争。

### 3.3 失败清理

三个相关测试的 cleanup 遵循以下规则：

- 无论主体是否通过，都先释放 barrier 并解除注入故障；
- 只在 `owned_binding_ids == ()` 后调用补偿 shutdown；
- ownership 尚未收敛时不启动另一个会竞争 lifecycle lock 的 shutdown；
- 主体失败和 cleanup 失败同时发生时，使用 `BaseExceptionGroup` 同时保留两个 cause；
- Gateway lifespan 用例无论如何都会尝试 dispose database engine。

---

## 4. 代码与文档变更

- `backend/tests/test_gateway_lifespan_shutdown.py`
  - 为 Gateway lifespan stop-failure 增加显式 cleanup retry barrier；
  - 等待 durable token 与 local owner 收敛；
  - cleanup 只在 ownership 收敛后 shutdown，并保留双错误。
- `backend/tests/test_feishu_supervisor.py`
  - 同步修复两个同根因 shutdown/stop-failure 回归；
  - 复用已有 `_wait_for_runtime_token_clear()` 契约。
- `backend/README.md`
  - 记录 stop/release-failure 测试的 entered/release/recovered、token/owner 收敛和 cleanup 错误保留要求。
- `backend/CLAUDE.md`
  - 将上述确定性测试要求固化为后端 M3 维护规范。

本轮没有修改 M3 生产实现。工作区中原本存在的未提交 `backend/app/channels/supervisor.py` 差异没有被本轮覆盖或归入本修复。

---

## 5. 自动化验证

### 5.1 Review 目标用例

```text
test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner

首次修复：1 passed
连续重复：5/5 passed
```

### 5.2 最终 shutdown 竞态定向回归

```text
test_shutdown_remains_retryable_while_quiescing_transport_cannot_stop
test_stop_failure_preserves_active_runtime_and_status
test_lifespan_does_not_mark_supervisor_complete_with_quiescing_owner

3 passed, 1 warning in 12.60s
```

### 5.3 91 项同进程聚合

聚合文件：

```text
tests/test_agent_channel_repo.py
tests/test_agent_channels_router.py
tests/test_feishu_supervisor.py
tests/test_gateway_lifespan_shutdown.py
```

最终连续两轮：

```text
round 1: 91 passed, 1 warning in 80.69s
round 2: 91 passed, 1 warning in 73.53s
```

两轮均在 150 秒外层硬超时内完成。唯一 warning 是既有的 LangChain `allowed_objects` pending deprecation。

### 5.4 修复过程中的额外红信号

在只修 Gateway 用例后，第二轮聚合暴露：

```text
1 failed, 90 passed
failed:
test_shutdown_remains_retryable_while_quiescing_transport_cannot_stop
```

继续按同根因扫描后，另一轮聚合还暴露：

```text
2 failed, 89 passed
failed:
test_non_cooperative_startup_stop_cannot_block_peer_or_janitor
test_stop_failure_preserves_active_runtime_and_status
```

其中 `test_stop_failure_preserves_active_runtime_and_status` 确认存在相同的立即重试缺口并已修复；`test_non_cooperative_startup_stop_cannot_block_peer_or_janitor` 随后与该用例隔离同跑全绿，最终两轮 91 项聚合也均全绿，因此按聚合负载瞬态记录，没有为它改变生产语义或放宽断言。

### 5.5 静态、格式、编译与差异检查

```text
ruff check:
All checks passed!

ruff format --check:
2 files already formatted

compileall:
test_feishu_supervisor.py passed
test_gateway_lifespan_shutdown.py passed

git diff --check:
passed
```

---

## 6. 尚未关闭的发布 Gate

1. 在 Linux CI 或具备 Windows symlink、POSIX shell 与可写 live-test 目录的环境执行完整 backend suite。
2. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 runtime claim/release、shutdown retry 和 row-lock 事务语义。
3. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
4. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
5. 将 Channel/Supervisor/Repository 职责拆分作为 correctness 合并后的独立架构任务处理。

---

## 7. 最终判定

**第二十五轮 Standards Important：Pass。**
**第二十五轮范围内已知 M3 P1/P2：0。**
**M3 聚合 Review Gate：Pass（连续两轮 91/91）。**
**仓库级 Ready to merge / production release：No，仍受上述平台与真实环境 Gate 限制。**
