# M3 Feishu Channel Partial — Twenty-Fourth Review Fix Report

**日期：** 2026-07-24
**基线：** `044fa17489b1d064286b97ea88dee65ed08060fe`
**对应 Review：** `2026-07-23-m3-feishu-channel-partial-code-twenty-fourth-review.md`

## 1. 结论

第二十四轮唯一 Spec P2 已关闭。Discovery cursor helper 现在会在每一次 `Path.replace()` 前同时检查调用方逻辑 deadline 与真实 monotonic wall deadline：

- 入口时预算已经耗尽：不执行 replace，抛出明确 `TimeoutError`；
- transient `PermissionError` 后预算耗尽：不再发起下一次 replace，并保留最后一个文件错误；
- 注入的逻辑 clock 冻结：真实 wall deadline 仍会终止永久 contention；
- 所有 timeout 路径都保留 source 临时文件且不发布 target，调用方 `finally` 负责清除 source。

验证过程中，四文件聚合首轮还暴露了一个既有 Supervisor release-failure 测试竞速：空载单测 5/5 通过，但聚合负载下后台 cleanup retry 可能在第二次 shutdown 时仍持有 lifecycle lock。该用例已改为显式 barrier，并等待 durable runtime token 与 process-local owner 都收敛后再重试 shutdown；原聚合重跑全绿。

**第二十四轮 Review 的 P2 已修复；当前没有已知、仍未关闭的第二十四轮 M3 P1/P2。**

仓库级 Ready to merge 仍为 **No**：当前 Windows 环境仍存在 symlink privilege、LocalSandbox POSIX/path 与 live bash 目录权限等范围外平台 Gate；真实 PostgreSQL 与双 Feishu App 发布验证也尚未完成。

---

## 2. Finding 处理结果

| Finding | 状态 | 修复 | 回归证据 |
|---|---|---|---|
| Spec P2：helper 入口 deadline 已过仍执行一次 replace | 已关闭 | 首次 attempt 前检查逻辑预算；已过期直接 `TimeoutError`。 | 新回归断言 attempt count 为 0、target 不存在。 |
| Spec P2：retry sleep 耗尽预算后仍进行下一次 replace | 已关闭 | while loop 顶部在每次 replace 前重新检查逻辑与 wall deadline；记录最后一个 `PermissionError`。 | 新回归断言仅一次 replace、一次 sleep，并重新抛出原文件错误。 |
| Standards Minor：frozen logical clock 缺少 wall deadline 负向覆盖 | 已关闭 | wall deadline 独立使用 `time.monotonic()`，不依赖测试注入的逻辑 clock 推进。 | 新确定性回归模拟 wall clock 两次 10ms 推进，attempt count 固定为 2，第三次不得执行。 |
| Standards Important：完整 backend suite 未全绿 | 未关闭，平台/环境 Gate | 本轮未修改 symlink、LocalSandbox 或 live client。 | symlink 两项 deselect 后，完整 fail-fast 达到 `785 passed, 15 skipped`，首失败为 live bash 工作目录 `WinError 5`。 |
| Standards Minor：Channel/Repository 结构债务 | 已登记，未混改 | 本轮没有在 deadline correctness 修复中拆分大模块。 | cleanup store/scanner、runtime lifecycle 与 typed key/result 留作独立架构任务。 |

---

## 3. Deadline 契约

### 3.1 首次尝试

Helper 进入时先计算 `deadline - clock()`。结果小于等于 0 时：

1. 不调用 `Path.replace()`；
2. 不修改 durable cursor target；
3. 抛出 `TimeoutError("Cleanup cursor replacement deadline expired")`。

### 3.2 contention retry

第一次 replace 在预算内遇到 `PermissionError` 时：

1. 保存该异常；
2. 按逻辑与 wall 两个 remaining 的较小值决定 sleep；
3. 下一轮在 replace 前再次检查两个 deadline；
4. 任一 deadline 耗尽就重新抛出最后一个 `PermissionError`，不再启动新的 filesystem mutation。

### 3.3 wall deadline

真实 wall deadline 在 helper 入口按初始逻辑 remaining 建立。即使注入的逻辑 clock 永远返回相同值，sleep 和后续 attempt 仍受 `time.monotonic()` 约束，永久 Windows sharing violation 不会形成无限 retry。

---

## 4. 聚合测试确定性补强

`test_shutdown_remains_retryable_while_runtime_release_keeps_failing` 原先只把 repository release 从永久失败直接切回成功，没有确认后台 quiescing cleanup retry 是否已经离开 binding lifecycle lock。聚合负载下，第二次 shutdown 偶发在 1 秒内拿不到该锁，`finally` 又可能用另一次 shutdown 覆盖原始失败。

修复后的用例：

1. 第二次 failed release 进入后设置 `cleanup_retry_started` 并由 barrier 暂停；
2. 首次 shutdown 按契约失败，保留 owner 与 leader fence；
3. repository 切换为 recovering release 后才释放 barrier；
4. 等待 recovering release、durable token clear 与 `owned_binding_ids == ()`；
5. 调用同一 Supervisor 的第二次 shutdown，验证完成态与 leader fence 释放；
6. `finally` 始终释放 barrier，并在 owner 已收敛时执行有界 shutdown，避免清理异常覆盖断言。

---

## 5. 代码与文档变更

- `backend/app/channels/feishu.py`
  - 每次 cursor replace 前执行双 deadline 检查；
  - 区分首次超时与已有 contention 后耗尽；
  - retry 耗尽时保留最后一个 `PermissionError`。
- `backend/tests/test_feishu_parser.py`
  - 新增 expired-before-first-attempt；
  - 新增 retry-sleep reaches deadline；
  - 新增 frozen logical clock + wall deadline；
  - 保留 transient contention 成功路径。
- `backend/tests/test_feishu_supervisor.py`
  - release-failure shutdown 回归加入显式 cleanup barrier 与 owner/token 收敛等待。
- `backend/README.md`
  - 记录 cursor late-publication 防线及 shutdown release-failure 测试契约。
- `backend/CLAUDE.md`
  - 同步双 deadline、异常语义和聚合测试确定性要求。

---

## 6. 自动化验证

### 6.1 红绿证据

```text
expired-before-first-attempt（修复前）：
Failed: DID NOT RAISE TimeoutError
replace attempts = 1

retry sleep reaches deadline（仅首次前置检查后）：
assert 2 == 1

三个负向边界 + transient success（最终）：
4 passed, 1 warning in 1.26s

上述四项重复：
3/3 轮通过（每轮 4 passed）
```

### 6.2 Supervisor release-failure

```text
聚合首轮：
1 failed, 90 passed
failed: test_shutdown_remains_retryable_while_runtime_release_keeps_failing

显式 barrier 单测：
3/3 passed

四文件聚合重跑：
91 passed, 1 warning in 68.65s
```

### 6.3 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

172 passed, 5 warnings in 51.76s
```

### 6.4 完整 M3 focused regression

```text
451 passed, 9 skipped, 5 failed in 150.98s
```

5 个失败全部位于未修改的 `tests/test_local_sandbox_provider_mounts.py`，表现为 Windows 路径反向映射预期 POSIX separator，或在 Windows 上强制 `/bin/sh`。本轮 M3生产/Review Gate 文件均通过；该结果不被误报为 focused suite 全绿。

### 6.5 完整 backend fail-fast

先 deselect 两个当前账户无法创建 symlink 的 fixture 后：

```text
1 failed, 785 passed, 15 skipped, 2 deselected, 11 warnings in 229.19s

failed:
tests/test_client_live.py::TestLiveToolUse::test_agent_uses_bash_tool

environment:
WinError 5 on backend/.deer-flow/users/test-user-autouse/threads/<id>
```

该失败依赖真实模型与本机 bash/目录权限，不在 M3 diff 中。它证明完整仓库 Gate 仍未关闭，也证明 auth、cursor 与正式 M3 Gate 已越过。

### 6.6 静态、格式与编译

```text
ruff check（20 个 M3 Python 文件）：All checks passed!
ruff format --check（同 20 个文件）：20 files already formatted
compileall（M3 production/tests）：passed
```

---

## 7. 尚未关闭的发布 Gate

1. 在 Linux CI 或具备 Windows symlink、POSIX shell 与可写 live-test 目录的环境执行完整 backend suite，取得全绿或完整分类结果。
2. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 claim reconciliation、failure-health epoch、shutdown retry 与 row-lock 事务语义。
3. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
4. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
5. Channel/Repository 职责拆分作为 correctness 合并后的独立架构任务处理。

---

## 8. 最终判定

**第二十四轮 cursor deadline P2：Pass。**
**第二十四轮 M3 代码可以本地提交。**
**仓库级 Ready to merge / production release：No。**

本地提交只应包含 M3 backend、M3 测试、迁移及 M3 Review 文档；当前工作区中的 `config.yaml`、M1/M4 backend/frontend、图片和历史测试临时目录不属于本次提交。
