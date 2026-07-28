# M3 Feishu Channel Partial — Twenty-Sixth Review Fix Report

**日期：** 2026-07-24
**分支：** `codex/m3-feishu-supervisor`
**基线 / 当前 HEAD：** `cc4c3eaeb6f0abc5d6c8a2314005edc32014fa9c`
**对应 Review：** `2026-07-24-m3-feishu-channel-partial-code-twenty-sixth-review.md`

## 1. 结论

第二十六轮新增的 Standards Important 已关闭：

- owner 未收敛时，测试 cleanup 不再调用 `shutdown()`；
- owner 未收敛时，禁止手工调用 `fence.release()`；
- cleanup 会抛出明确 `AssertionError`；
- 主体失败、ownership cleanup 失败与 Gateway engine dispose 失败会同时保留；
- leader fence 在 unresolved ownership 场景保持持有。

第二十六轮新增的 Duplicated Code Minor 也已同步处理。三个 shutdown/stop-failure 业务场景现在共享 barrier、durable token 等待、process-local owner 等待、条件 shutdown 和错误聚合实现，避免再次出现测试分支漂移。

**第二十六轮范围内没有未关闭的 M3 P1/P2 或 Standards Important。**

本轮没有修改生产实现。完整 backend suite、真实 PostgreSQL 与双 Feishu App 仍属于未关闭的仓库/发布环境 Gate。

---

## 2. Finding 处理结果

| Finding | 状态 | 修复 | 回归证据 |
|---|---|---|---|
| Standards Important：ownership 未收敛时 cleanup 手工释放 leader fence | 已关闭 | `finish_supervisor_cleanup()` 在 owner 未清除时 fail closed，抛出明确 `AssertionError`，不 shutdown、不 release fence。 | 新负向回归修复前稳定红、修复后连续 5 次通过。 |
| Standards Important：未收敛 ownership 未作为 cleanup error 保留 | 已关闭 | helper 异常进入 `cleanup_errors`；统一错误聚合保留主体与所有 cleanup cause。 | 新双错误回归通过；Gateway dispose 仍在独立 `try` 中执行。 |
| Standards Minor：并发测试支撑逻辑重复并发生规则漂移 | 已关闭 | 提取 `tests/support/feishu_shutdown.py`，统一 barrier、token/owner 等待、cleanup shutdown 和错误聚合。 | 三个业务回归共同通过；四文件聚合扩展为 93 项并连续两轮全绿。 |
| Important Gate：完整 backend suite 未全绿 | 未关闭，平台/环境 Gate | 本轮没有修改 symlink、LocalSandbox 或 live bash 环境。 | 沿用此前完整 suite 分类结果。 |
| Minor：Channel/Supervisor/Repository 结构债务 | 已登记，未混改 | 本轮只整理测试支撑，不拆分生产模块。 | 留作独立架构任务。 |

---

## 3. 红绿验证

### 3.1 行为保持的 seam 提取

原错误逻辑嵌在两个测试的 `finally` 内，无法独立驱动。首先把现有行为原样提取为 `finish_supervisor_cleanup()`，两个调用方切换到 helper 后，三个正常 shutdown 业务回归仍通过：

```text
3 passed, 1 warning in 13.13s
```

这一步只建立测试 seam，没有改变 fail-open 行为。

### 3.2 Red

新增负向回归构造永不清除 `owned_binding_ids` 的 Supervisor，并断言：

- cleanup 必须抛出 ownership convergence error；
- `shutdown_calls == 0`；
- leader fence 仍为 held。

修复前：

```text
FAILED test_shutdown_test_cleanup_preserves_fence_when_ownership_does_not_converge
Failed: DID NOT RAISE <class 'AssertionError'>
```

旧 helper 在该分支直接调用 `fence.release()` 并正常返回，精确复现第二十六轮 finding。

### 3.3 Green

修复后：

```text
1 passed, 1 warning in 1.02s
```

负向用例连续重复：

```text
5/5 passed
```

---

## 4. 共享测试支撑契约

新增 `backend/tests/support/feishu_shutdown.py`：

### 4.1 `CleanupRetryBarrier`

- 对第二次及后续 stop attempt 设置 `entered`；
- 只有测试显式设置 `release` 后才继续；
- delegate stop 成功后设置 `recovered`；
- 所有业务测试在 `finally` 无条件设置 `release`。

### 4.2 `wait_for_runtime_token_clear`

- 通过 owner-scoped repository read 等待精确 durable token 清除；
- binding 意外消失或超时会抛明确 `AssertionError`；
- 第二次 shutdown 不能先于 durable fencing 收敛。

### 4.3 `wait_for_supervisor_ownership`

- 等待 `owned_binding_ids == ()`；
- 超时必须抛出 `Supervisor ownership did not converge during test cleanup`；
- 不提供任何释放 leader fence 的能力。

### 4.4 `finish_supervisor_cleanup`

- 先等待 process-local ownership；
- owner 未收敛：不 shutdown、不 release fence，直接失败；
- owner 已收敛且 shutdown 未完成：才调用同一 Supervisor 的 shutdown retry；
- 传入 fence 时仅检查其状态，不具备 `release()` 接口。

### 4.5 `raise_test_cleanup_errors`

- 只有一个错误时保留原异常类型；
- 主体与 cleanup 同时失败时使用 `BaseExceptionGroup`；
- Gateway 用例会依次收集 ownership cleanup 和 engine dispose 错误。

---

## 5. 代码与文档变更

- `backend/tests/support/feishu_shutdown.py`
  - 新增共享 cleanup retry barrier；
  - 新增 durable token 与 local owner 收敛等待；
  - 新增 fail-closed cleanup shutdown；
  - 新增统一错误聚合。
- `backend/tests/test_feishu_supervisor.py`
  - 新增 unresolved ownership/fence 负向回归；
  - 新增主体与 cleanup 双错误回归；
  - 两个 stop/shutdown 场景改用共享 helper。
- `backend/tests/test_gateway_lifespan_shutdown.py`
  - Gateway lifespan 场景改用共享 helper；
  - engine disposal 继续独立执行并参与错误聚合。
- `backend/README.md`
  - 记录测试 cleanup 的 fail-closed fencing 契约。
- `backend/CLAUDE.md`
  - 固化共享 helper、禁止手工 release fence 和多错误保留要求。

本轮没有修改 `backend/app/channels/supervisor.py`。工作区原有的 M4 production/frontend、配置和验收改动均未纳入本轮修复。

---

## 6. 自动化验证

### 6.1 干净合成快照

测试对象为 `cc4c3eae` Git archive，仅叠加：

```text
backend/tests/support/feishu_shutdown.py
backend/tests/test_feishu_supervisor.py
backend/tests/test_gateway_lifespan_shutdown.py
```

工作区与快照 SHA-256 一致：

```text
test_feishu_supervisor.py:
19BB2FAF4A7C814EF62B8905D1B326E3CF70201E63932884554D8214430F9294

test_gateway_lifespan_shutdown.py:
C18BE39D95F78B8DE33187A9CDFE8C5DACA817E50B4D6D42B575000226BCA10D

support/feishu_shutdown.py:
962F16617E93CA4A76D573DCE0CCE58661F1FA1997F2AD244FCC80F6A5EDEC7F
```

### 6.2 关键定向回归

```text
unresolved owner must preserve fence
body + cleanup errors must both survive
quiescing transport shutdown retry
active runtime stop failure
Gateway lifespan quiescing owner

5 passed, 1 warning in 15.63s
```

### 6.3 Repository + Router + Supervisor + Gateway lifespan 聚合

新增两个负向回归后，聚合从 91 项增加到 93 项。

最终源码连续两轮：

```text
round 1: 93 passed, 1 warning in 63.01s
round 2: 93 passed, 1 warning in 82.84s
```

### 6.4 正式五文件 M3 Gate

```text
tests/test_feishu_supervisor.py
tests/test_feishu_parser.py
tests/test_feishu_websocket_lifecycle.py
tests/test_gateway_services.py
tests/test_harness_boundary.py

174 passed, 5 warnings in 50.71s
```

### 6.5 静态、格式与编译

```text
ruff check <三个测试文件>:
All checks passed!

ruff format --check <三个测试文件>:
3 files already formatted

compileall <三个测试文件>:
passed
```

---

## 7. 尚未关闭的发布 Gate

1. 在 Linux CI 或具备 Windows symlink、POSIX shell 与可写 live-test 目录的环境执行完整 backend suite。
2. 在真实 PostgreSQL CI 设置 `REQUIRE_POSTGRES_TESTS=1`，验证 runtime claim/release、shutdown retry 与 row-lock 事务语义。
3. 使用两个真实 Feishu App 验证 near-deadline ready、failed/non-cooperative stop、credential rotation、Gateway shutdown/restart、scanner child failure 与 attachment recovery。
4. 生产同构环境继续保持 M3 v1 单 Published Feishu Supervisor Gateway，并遵守 pre-fence 版本停机升级要求。
5. Channel/Supervisor/Repository 职责拆分作为 correctness 合并后的独立架构任务处理。

---

## 8. 最终判定

**第二十六轮 Standards Important：Pass。**
**第二十六轮 Duplicated Code Minor：Pass。**
**第二十六轮范围内已知 M3 P1/P2：0。**
**M3 聚合 Review Gate：Pass（连续两轮 93/93）。**
**正式五文件 M3 Gate：Pass（174/174）。**
**仓库级 Ready to merge / production release：No，仍受平台与真实环境 Gate 限制。**
