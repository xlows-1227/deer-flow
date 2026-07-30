# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十七轮代码复审

**状态：** 已复审，仍有阻塞问题
**日期：** 2026-07-21

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十六轮复审：[2026-07-21-m3-feishu-channel-partial-code-sixteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-sixteenth-review.md)
- 第十六轮修复报告：[2026-07-21-m3-feishu-channel-partial-sixteenth-review-fix-report.md](./2026-07-21-m3-feishu-channel-partial-sixteenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后的当前未提交 backend 工作区；`HEAD` 相对固定点仍无新增 commit
- 排除：无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- 重点：第十六轮 1 个 P1、1 个 P2、1 个 Standards Important、1 个 Standards Minor 的关闭情况，以及本轮修复 diff 的 Spec/Standards 双轴符合性

---

## 1. 复审结论

第十六轮针对 non-cooperative start/stop 的核心修复方向正确：start、startup renewal、stop 和 release task 现在有显式 tracking；transport 未确认退出时会保留 quiescing generation 与 fencing token；scanner 在 terminate/kill 返回但 child 仍 alive 时也会保留 slot、设置 stop fence并拒绝 restart。共享 `_converge_startup()` 已消除上一轮两套 startup deadline 包装。

但本轮仍发现 **Spec 轴 1 个 P1、2 个 P2**：

1. timeout 后 detached 的 health projection 如果已经持有全局 `_repository_projection_lock` 并吞掉取消，会继续永久阻塞其他 binding；迟到投影还缺少 generation/CAS 防护；
2. scanner 只处理“terminate/kill 正常返回但 child 仍 alive”，当 `terminate()`、`kill()`、`join()` 或 `is_alive()` 自身抛错时，scan/replenish 路径仍可遗忘已经 pop 的 live slot；
3. 为 fencing 而保留的 quiescing generation 仍被 `running_binding_ids` 与 `test_binding()` 对外报告为 running，与其 unhealthy/non-ready 状态矛盾。

Standards 轴发现 **3 个 Important、2 个 Minor**：scanner 文档保证仍强于异常路径实现；仓库强制要求的完整 `make test` 没有执行；本轮正式 parser Gate 在两个全文件运行中出现三个不同的时间敏感失败；此外 convergence 的五个布尔策略参数和分散的 quiescing 状态迁移分别属于 Primitive Obsession 与 Duplicated Code 判断项。

**结论：Ready to merge：No。**

- Spec 轴：共 3 项，最严重为 P1——单 binding 的 detached projection 仍能持全局锁阻塞 peers 与 janitor。
- Standards 轴：共 5 项，最严重级别为 Important——scanner shutdown 文档契约和测试 Gate 均未稳定满足。

---

## 2. 第十六轮问题关闭状态

| 第十六轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1：startup deadline 可被无上界 teardown 绕过 | **部分关闭** | non-cooperative start/stop 已有界转入 quiescing retry；但 non-cooperative failure projection 可在持全局 lock 时被 detach，继续阻塞 peers，见 3.1 |
| P2：scanner stop 会遗忘仍 alive 的 child | **部分关闭** | terminate/kill 正常返回后的最终 liveness check 已补齐；process API 抛异常时 scan/replenish 仍可丢失 slot，见 3.2 |
| Important：scanner 文档保证强于实现/测试 | **部分关闭** | stubborn-child false-return 路径已与文档一致；termination exception 路径仍不满足“每条路径最终检查并保留”的描述，见 4.1 |
| Minor：startup convergence timeout/失败投影双份实现 | **已关闭** | `_converge_startup()` 已成为显式操作与 startup reload 的共享实现；本轮剩余的是策略参数建模问题，不是原重复包装 |
| M3 部署/完整 Review Gate | **未关闭** | 真实 PostgreSQL、双 Feishu App、完整 backend suite 以及稳定的 focused parser Gate 仍未完成 |

---

## 3. Spec 轴

### 3.1 P1：detached failure projection 可持全局 lock 继续阻塞 peers

**置信度：** 高（确定性复现）

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L298)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L398)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L565)
- [第十六轮修复报告](./2026-07-21-m3-feishu-channel-partial-sixteenth-review-fix-report.md#L28)
- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L746)

`_record_startup_failure()` 现在用 `asyncio.wait(2s)` 建立硬返回边界；projection 超时后会 `cancel()` 并通过 done callback 脱离。这只能保证父任务不再 await projection，不能释放 projection 自己已经持有的资源。

真实 `_record_health()` 在调用 repository 前先持有全局 `_repository_projection_lock`。如果 `repository.update_health()` 已进入该锁并吞掉 `CancelledError`，父 startup 虽然得到内存 fallback，但 detached task 会继续永久占锁。其他 binding 随后在 claim 或 health projection 时也需要同一把锁，最终 `load_active_bindings()` 仍卡在 `gather()`，janitor 无法创建。

本轮确定性诊断让第一个 binding 的 repository read 失败，再令其 `update_health()` 在已经拿到全局锁后吞掉取消；第二个 binding 等到该投影进入后再继续。projection timeout 为 `0.05s`，`0.6s` 后结果为：

```text
projection_cancelled = True
load_task_done = False
load_task pending at load_active_bindings():gather
```

释放 projection 后 load 才能完成。临时探针已移除。现有正式测试在调用原始 `_record_health()` 之前阻塞，因此从未持有全局锁，不能证明修复报告第 37 行“迟到 task 不会阻止 peer gather 或 janitor”的不变量。

此外，同一个 detached task 可能先在锁外吞取消；如果该 binding 随后成功启动并写入 healthy，迟到 projection 再恢复调用原始 `_record_health()`，会把新 generation 的健康状态覆写为旧 unhealthy。当前 health update 没有 expected generation/token 的 CAS。

建议不要让可脱离、不可取消的 repository task跨 deadline 持有全局应用锁：改为 per-binding serialization 或让父层只在有界协调阶段持锁；持久化 health 时携带 expected runtime generation/token 做 CAS，迟到任务不得覆盖新 generation。补两条正式回归：持锁 projection 吞取消时 peer/janitor仍有界完成；旧 projection 在新 generation healthy 后恢复时不能改写 DB 或内存 health。

### 3.2 P2：scanner process API 抛错时仍会遗忘 live child

**置信度：** 高（确定性复现）

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L335)
- [feishu.py](../../../backend/app/channels/feishu.py#L443)
- [feishu.py](../../../backend/app/channels/feishu.py#L458)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1099)
- [第十六轮修复报告](./2026-07-21-m3-feishu-channel-partial-sixteenth-review-fix-report.md#L46)

`_terminate_slot()` 只捕获 connection close 的 `OSError`；`process.terminate()`、`join()`、`kill()` 和最后的 `is_alive()` 都可以向外抛错。scan 已在调用该 helper 前从 `_slots` pop 当前 slot，异常逃逸后 finally 只设置 replenish event，不会重新插入 slot、设置 `_stopping` 或 stop event。manager 随后可以补充新 child，而旧 live child 已失去 tracking。

replenish rollback 也通过列表推导调用 `_terminate_slot()`；任何一次 termination API 异常都会中断整个推导，使尚未发布的 `new_slots` 随栈一起丢失。这与修复报告第 50～51 行“scan/replenish/partial spawn 全路径保留未退出 child”的声明不符。

本轮临时探针令 hung scan 的 fake process 在 `terminate()` 抛出 `RuntimeError("terminate failed")`。期望是 request fail closed并保留 slot；实际异常从 `scanner.scan()` 直接逃逸于 `_terminate_slot()`。临时探针已移除。

建议让 `_terminate_slot()` 无论 process API 成功或抛错都执行 best-effort 后续步骤和最终 liveness 判定；无法确认退出统一返回失败结果。所有调用方必须在 `finally`/统一 ownership helper 中把失败 slot放回 tracked 集合、设置 stop fence并 fail closed。补 terminate、join、kill、final `is_alive` 分别抛错的回归，并断言 restart始终被拒绝直到确认旧 child退出。

### 3.3 P2：quiescing fencing owner 被对外误报为 running

**置信度：** 高（确定性复现）

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L280)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L398)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1630)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L429)
- [开发计划 F3.2](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L771)

第十六轮有意把未退出 generation 留在 `_running`，用于 fencing ownership 和 cleanup retry；此时 `quiescing=True`，fallback health 也正确计算为 `running=False`。但 `running_binding_ids` 仍直接返回 `_running` 的所有 key，docstring 却声称返回“ready handshake completed”的 binding。`test_binding()` 也只检查 `binding_id in self._running`，会把 quiescing owner重新投影为 `running=True`。

在正式 non-cooperative start 场景中，失败 binding 已是 unhealthy、start task 已收到取消、cleanup janitor 已启动且 token被保留；本轮临时断言得到：

```text
running_binding_ids = (<failed quiescing binding>, <healthy peer>)
expected = (<healthy peer>,)
```

这不会丢失 fencing，但会让 Gateway startup 日志、内部运行态判断和 owner `test` 响应把“保留清理所有权”误当成“transport 正在服务”。建议统一一个 `is_serving` 判定：ready/`channel.is_running` 且 `not quiescing`；registry ownership 和 serving/runtime health 必须使用不同接口。补 non-cooperative start/stop 期间的 `running_binding_ids` 与 `test_binding().running` 回归。

本轮未发现超出 M3 范围的实质 scope creep。

---

## 4. Standards 轴

### 4.1 Important-1：scanner 文档声明的“每条 termination 路径”仍未实现

**相关文件：**

- [CLAUDE.md](../../../backend/CLAUDE.md#L530)
- [README.md](../../../backend/README.md#L216)
- [feishu.py](../../../backend/app/channels/feishu.py#L443)

README/CLAUDE 声明每条 terminate/kill 路径都执行最终 `is_alive()` 检查，stubborn child 会保持 tracked/stop fence。3.2 证明 process API 抛异常时既没有最终检查，也没有统一 tracking；这是文档与实现的硬性不一致，也违反 Documentation Update Policy 的准确同步要求。

### 4.2 Important-2：强制完整测试 Gate 未执行

**相关规范：**

- [CLAUDE.md](../../../backend/CLAUDE.md#L673)
- [第十六轮修复报告](./2026-07-21-m3-feishu-channel-partial-sixteenth-review-fix-report.md#L141)

仓库 Mandatory TDD 明确要求每个 bug fix 前后执行完整 `make test`，且测试通过后功能才算完成。第十六轮修复报告第 146 行明确说明未执行完整 `pytest tests -q` / `make test`，因此该硬 Gate 仍未满足。本轮复审也没有获得可替代的完整 backend 汇总。

### 4.3 Important-3：focused parser Gate 存在时间敏感不稳定

**相关文件：**

- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L708)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L889)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L991)

本轮相同工作区的 parser 全文件连续运行两次：第一次为 `47 passed, 2 failed`，第二次为 `48 passed, 1 failed`；三个失败用例各自单独复跑全部通过。失败分别是 45ms 目录扫描预算没有到达尾部、quarantine 峰值为 7 而断言精确等于 8，以及 100ms recovery 预算耗尽前 remote delete thread未启动。

这些测试使用极短 wall-clock deadline、真实 `sleep()` 和精确并发峰值断言，在受载 runner 上无法稳定充当合并 Gate。建议通过可控 clock、事件/barrier 或职责级状态断言替代调度时序猜测；若必须验证真实 deadline，应扩大预算与观察窗口并避免断言精确线程峰值。按 Mandatory TDD，“偶尔通过”不能作为功能完成证据。

### 4.4 Minor-1：convergence 用五个布尔参数编码入口策略

**判断项：** Primitive Obsession / Speculative Generality（judgement call）

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L353)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L336)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1542)

`_converge_startup()` 接收 `strict`、`restore_deleting`、`reread`、`isolate_failures`、`skip_not_found` 五个布尔值，但当前只有“显式操作”和“startup reload”两组固定组合。无效组合可以被任意调用方构造，参数名也不能表达完整策略。建议使用具名策略对象/枚举，或保留两个薄入口调用一个不暴露布尔组合的内部 primitive。

### 4.5 Minor-2：quiescing task ownership 状态转换仍分散重复

**判断项：** Duplicated Code（judgement call）

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L784)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L854)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L914)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1029)

cancel/drain task、清空 task slot、转 quiescing、启动 cleanup retry 的状态迁移分别散落在 discard、replace、retry 和 normal stop 四处。它们虽然服务不同入口，但共享“task 未确认退出就保留 generation”的核心不变量；继续复制容易出现某条路径忘记保留 task或错误清空 ownership。建议收敛为一个返回明确状态的 transition helper。

本轮未发现新的公开 API type/docstring、harness→`app.*` 反向依赖或 Ruff/format 问题。

---

## 5. 验证记录

### 5.1 确定性诊断

三个临时红测/断言均在验证后移除，未写入正式测试集：

```text
projection-lock probe:
projection_cancelled=True；0.6s 后 load_task 仍 pending

scanner termination-exception probe:
terminate() 的 RuntimeError 从 scanner.scan() 逃逸

quiescing serving-state probe:
running_binding_ids 同时包含 failed quiescing binding 与 healthy peer
```

### 5.2 正式测试

第十六轮声明的 5 文件聚合命令：

```text
command timed out after 184.1s
无最终 passed/failed 汇总
```

拆分结果：

```text
tests/test_feishu_supervisor.py
46 passed, 3 warnings in 23.96s

tests/test_feishu_parser.py（第一次）
47 passed, 2 failed, 3 warnings in 10.57s

两个失败单独复跑
2 passed, 2 warnings in 3.40s

tests/test_feishu_parser.py（第二次）
48 passed, 1 failed, 3 warnings in 8.83s

该失败单独复跑
1 passed, 2 warnings in 1.56s

WebSocket lifecycle + Gateway shutdown + harness boundary
9 passed, 6 warnings in 35.19s
```

Supervisor 运行还出现一次 SQLAlchemy `non-checked-in connection` GC warning；其余 warning 为 LangGraph/Lark/WebSocket deprecation 与当前环境 pytest cache 写入警告。本轮未把 warning 计为功能失败，但建议在修复 cancellation ownership 时确认数据库 session 能稳定归还 pool。

### 5.3 静态、格式与差异检查

```text
ruff check --no-cache <4 个第十六轮修复相关 Python 文件>
All checks passed!

ruff format --check --no-cache <同 4 个文件>
4 files already formatted

git diff --check
通过（仅有无关 config.yaml 的 CRLF 提示）
```

### 5.4 尚未关闭的 Gate

- 当前环境未配置真实 PostgreSQL；migration upgrade/downgrade/re-upgrade 严格 Gate 仍需在 `REQUIRE_POSTGRES_TESTS=1` 的 CI 中执行。
- 尚未执行两个真实 Feishu App 的 near-deadline ready、non-cooperative/failed stop、rotation、process restart 和 attachment recovery 冒烟。
- 完整 `pytest tests -q` / `make test` 尚无最终汇总。
- focused parser 文件必须先消除时间敏感不稳定，再作为可信合并 Gate。

---

## 6. 最终结论

第十六轮已经实质修复了 non-cooperative start/stop 无界等待和 stubborn child 正常返回后仍 alive 的主路径，也正确保留了 quiescing fencing owner。但 hard deadline 不能自动隔离 detached task 已持有的全局资源；scanner 的异常路径尚未进入统一 ownership；registry ownership 与 serving 状态也仍混用。

建议优先顺序：

1. 让 detached health projection 无法跨 deadline 持有全局 lock，并用 runtime generation/token CAS拒绝迟到写；
2. scanner process API 的所有异常路径统一保留 live slot与 stop fence；
3. 将 quiescing ownership 与 serving/running 状态拆开；
4. 消除 parser 时间敏感 flaky tests并完成完整 `make test`；
5. 再整理 convergence 策略参数和 quiescing 状态迁移重复；
6. 最后完成真实 PostgreSQL、双 Feishu App 与生产同构 Gate。

**Ready to merge：No。**
