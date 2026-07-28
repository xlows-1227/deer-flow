# 多租户 Agent 发布平台 - M3 飞书渠道部分实现第十五轮代码复审

**状态：** 已复审，仍有阻塞问题
**日期：** 2026-07-21

**关联文档：**

- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 第十四轮复审：[2026-07-21-m3-feishu-channel-partial-code-fourteenth-review.md](./2026-07-21-m3-feishu-channel-partial-code-fourteenth-review.md)
- 第十四轮修复报告：[2026-07-21-m3-feishu-channel-partial-fourteenth-review-fix-report.md](./2026-07-21-m3-feishu-channel-partial-fourteenth-review-fix-report.md)

**复审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`044fa17489b1d064286b97ea88dee65ed08060fe`
- 复审对象：固定点之后的当前未提交 backend 工作区；排除无关的 `config.yaml`、frontend、图片、旧 review 文档和临时目录改动
- 重点：第十四轮 1 个 P1、2 个 P2、1 个 Standards Important 的关闭情况，以及本轮修复 diff 的 Spec/Standards 双轴符合性

---

## 1. 复审结论

第十四轮的 fence acquire cancellation 与 migration 精确 Gate 已实质关闭：取消中的阻塞 worker 会被 drain，晚到 OS lock 会在传播 `CancelledError` 前释放；migration helper 也能区分 Boolean/Numeric 与 DateTime，并对 `reserved`/`0` 做精确 default 比较。scanner manager 可以在两个 slot 连续失败后补足容量，`_start_row()` 内的 confirm/start 等异常也已进入逐 binding 收敛。7 个直接相关测试文件本轮复跑为 `161 passed, 1 skipped`。

但本轮仍发现 **Spec 轴 2 个 P1、1 个 P2**：

1. `load_active_bindings()` 的逐 binding 首次 DB 重读仍在异常隔离之外；一个 row 的读取异常会让整个 load 抛出，另一个 binding 已经 running，但 Gateway 会移除 Supervisor 管理入口且 janitor 不启动；
2. 30 秒 provisional startup lease 仍不是实际启动上界：claim 后的 cleanup 索引投影没有 deadline，随后 WebSocket ready 还可合法等待 15 秒；一个慢/挂起的投影仍可让合法启动过期或让整个 Gateway startup 无限等待；
3. scanner manager 可在 readiness `poll(15s)` 中阻塞，而 `stop()` 只 join 1 秒便返回，因此 Gateway shutdown 可能在 manager 和新 child 仍存活时完成。

Standards 轴发现 **1 个 Important、1 个 Minor**：scanner shutdown 的真实行为与 README/CLAUDE 的“先停止 manager，再终止全部 worker”不一致，也缺少阻塞 replenish 回归；三个 scanner 测试重复定义相同 process/connection fake，属于非阻塞的 Duplicated Code。

**结论：Ready to merge：No。** 两个 P1 都继续违反 F3.2“单个绑定失败不抛出、不影响其他绑定”和“不重启 Gateway”的生命周期契约；需把整个逐 binding startup convergence 纳入异常和 deadline 边界，而不是只包裹 `_start_row()` 的后半段。

---

## 2. 第十四轮问题关闭状态

| 第十四轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| P1：慢启动租约过期会中止整个 Supervisor | **部分关闭** | `_start_row()` 的异常已逐 binding 吸收，steady/startup TTL 已分离；但首次 row 重读在隔离外，30 秒也未覆盖无 deadline 的 cleanup 投影，见 3.1、3.2 |
| P2：取消 leader fence acquire 泄漏晚到 OS lock | **已关闭** | acquire task 使用 shield/drain；取消后成功取得的底层锁先跨线程 release，再传播 cancellation；直接回归通过 |
| P2：两个 DELETE scanner timeout 后池永久耗尽 | **部分关闭** | manager 可补足两个失败 slot；但 shutdown 只等 manager 1 秒，阻塞 readiness 时会带着 manager/child 返回，见 3.3 |
| Important：migration 类型/default Gate 可误放行 | **已关闭** | SQLAlchemy type affinity 保留 Boolean/Numeric；server default 去方言包装后精确比较；反例与 SQLite 双向迁移通过 |
| 7 文件 focused regression | **已关闭** | 本轮复跑 `161 passed, 1 skipped, 1 warning in 47.79s` |
| M3 部署/完整 Review Gate | **未关闭** | 本机无真实 PostgreSQL；真实双 Feishu App 与全量 backend 最终汇总仍未完成 |

---

## 3. Spec 轴

### 3.1 P1-1：逐 binding 的首次 DB 重读仍可撤销整个 Supervisor 管理入口

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L1150)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L1177)
- [app.py](../../../backend/app/gateway/app.py#L289)
- [test_feishu_supervisor.py](../../../backend/tests/test_feishu_supervisor.py#L240)

`start_row()` 只对 `_start_row(current)` 及其后的异常做隔离。进入该 `try` 之前，代码先执行 `current = await self._binding(binding_id)`；这里除了 `BindingNotFoundError` 外的数据库/仓储异常会直接逸出 `start_row()`，继而让 `asyncio.gather()` 和 `load_active_bindings()` 整体抛错。

确定性诊断令第一个 binding 的 `_binding()` 抛 `RuntimeError`、第二个保持正常，得到：

```text
load_outcome = RuntimeError: one row read failed
running = (<second binding>,)
janitor_started = False
```

FastAPI lifespan 会捕获该异常并把 `app.state.feishu_supervisor` 置为 `None`。于是第二个 transport 已经 running，却无法再通过管理 API 启停/删除，cleanup janitor 也没有启动。这与第十四轮 P1 的最终失管形态相同，只是触发点从 confirm 移到了被遗漏的首次 row 重读。

开发计划 F3.2 第 781、786、795 行要求单 binding start 失败只标记 unhealthy，不抛出、不影响 peers。建议把“重读 row → 检查状态 → `_start_row` → unhealthy 投影”的整个单行 convergence 放在同一异常边界内；除 task cancellation 外，任一行失败都只记录并返回。补一个仓储 read 对单 binding 失败的双 binding 回归，断言 load 返回、peer 仍可管理且 janitor 已启动。

### 3.2 P1-2：固定 30 秒 startup lease 仍未覆盖无 deadline 的 cleanup 投影

**相关文件：**

- [supervisor.py](../../../backend/app/channels/supervisor.py#L31)
- [supervisor.py](../../../backend/app/channels/supervisor.py#L384)
- [feishu.py](../../../backend/app/channels/feishu.py#L103)
- [feishu.py](../../../backend/app/channels/feishu.py#L1413)
- [feishu.py](../../../backend/app/channels/feishu.py#L2062)
- [sql.py](../../../backend/packages/harness/deerflow/persistence/agent_channel/sql.py#L571)

claim 现在使用 30 秒 startup TTL，修复了“cleanup + 15 秒 ready 在正常情况下略超 15 秒”的直接问题，但 30 秒不是由代码保证的启动上界。claim 后，真实 `FeishuChannel.start()` 会先调用 `_refresh_attachment_cleanup_health()`；其 `to_thread(_binding_cleanup_index_has_backlog)` 执行 `exists/glob/read_text/unlink`，没有 timeout 或可 kill 边界。投影完成后，WebSocket ready 还可合法等待 15 秒，最后才调用 `confirm_runtime()`；confirm 会拒绝已经过期的 provisional token。

因此有两种仍合法可达的失败形态：

1. cleanup 投影约 15 秒、ready 接近 15 秒，再加调度/数据库开销，ready transport 会被 confirm 判为 lease revoked 并停止；
2. 文件系统调用永久阻塞时，该 binding 的 `start_row()` 永不返回，外层 gather 没有 per-binding deadline，整个 Gateway lifespan 永远不能进入 request admission。

现有 `test_slow_ready_start_uses_provisional_lease_budget` 只把 steady-state TTL 缩短到 0.02 秒，启动仅 sleep 0.05 秒；它证明两个常量已分离，但没有越过 startup TTL，也没有覆盖 projection hang。

建议在 claim 后立即启动 provisional renewal，或为完整的 `projection + ready + confirm` 建立明确 deadline，并让投影进入可取消/可终止边界；无论选哪种方式，单 binding deadline 都必须收敛为 unhealthy 而不阻断 peers。补“短 startup TTL + 慢投影 + 慢但成功 ready”和“投影永久阻塞”两条确定性回归。

### 3.3 P2：scanner shutdown 会在 manager/child 仍存活时返回

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L323)
- [feishu.py](../../../backend/app/channels/feishu.py#L384)
- [feishu.py](../../../backend/app/channels/feishu.py#L463)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1172)

manager 补员时，单个新 worker 的 readiness `parent.poll()` 最长等待 15 秒；`stop()` 设置 stop event 后只执行 `maintenance_thread.join(timeout=1.0)`，无论 manager 是否退出都会继续清空当前已发布 slots 并返回。stop event 无法中断已经进行中的 `parent.poll()`。

确定性阻塞 replenish 诊断得到：

```text
stop_elapsed = 1.016s
manager_alive_after_stop = True
```

真实路径中，未发布的新 child 也会跟随 manager 继续存活，直至 readiness wait 完成后才因 `_stopping` 被回滚。若同一进程在此窗口重新启动 lifespan，`start()` 还会清除 stop event 并把 `_stopping` 复位，形成旧 manager/新生命周期交叉。

建议让 in-flight spawn/readiness 可被 stop event 唤醒，或在一个明确的总 shutdown deadline 内 drain 当前 replenish 并终止 unpublished children；`stop()` 返回前应验证 manager 已退出。补“manager 正阻塞 readiness → stop → manager/所有 child 均退出”的回归，并覆盖同进程 stop/start。

---

## 4. Standards 轴

### 4.1 Important：scanner shutdown 实现、文档与测试不一致

**相关文件：**

- [feishu.py](../../../backend/app/channels/feishu.py#L463)
- [README.md](../../../backend/README.md#L216)
- [CLAUDE.md](../../../backend/CLAUDE.md#L530)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1172)

README 和 CLAUDE 明确描述 Gateway shutdown 会先停止 manager，再终止所有剩余 workers；当前实现只等待 1 秒，3.3 已证明返回时 manager 仍可存活。文档因此没有与代码保持准确同步，违反 `backend/CLAUDE.md` 第 70–77 行 Documentation Update Policy。

新增 manager lifecycle 测试使用立即 ready 的 fake，只覆盖正常 stop，没有覆盖本次新增异步 manager 最关键的 blocked replenish teardown。按照 `backend/CLAUDE.md` 第 671–677 行 Mandatory TDD，新 bug fix 必须有能证明故障边界的单元测试；应增加阻塞 readiness 并在 `finally` 中释放 fake worker 的回归。

### 4.2 Minor：scanner 测试重复 process/connection fake

**相关文件：**

- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1099)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1172)
- [test_feishu_parser.py](../../../backend/tests/test_feishu_parser.py#L1265)

三个相邻 scanner 测试分别重复定义 `FakeProcess`、parent/child connection 及 start/terminate/kill/join 形状，属于 **Duplicated Code** 判断项。建议提取一个可配置的 process/pipe fixture，使 ready、hang、spawn failure 与 teardown 状态通过参数表达，避免后续某条 fake 忘记模拟 close/join 语义。

该项不改变运行时正确性，不单独阻塞合并。本轮未发现新增公开 API 的类型/docstring、migration 精确反例、harness→`app.*` 边界或 Ruff/format 违规。

---

## 5. 验证记录

### 5.1 直接相关测试

7 个 review 相关文件：

```text
161 passed, 1 skipped, 1 warning in 47.79s
```

Gateway shutdown 与 harness boundary：

```text
2 passed, 1 warning in 10.17s
```

唯一 skip 是本机没有 `TEST_POSTGRES_URL`；warning 为 LangGraph `allowed_objects` pending deprecation，不是本轮失败。

### 5.2 静态检查

```text
ruff check --no-cache <5 个第十四轮修复 Python 文件>
All checks passed!

ruff format --check --no-cache <同 5 个文件>
5 files already formatted

git diff --check <固定点> -- backend
通过
```

### 5.3 尚未关闭的 Gate

- 本机未配置 `TEST_POSTGRES_URL` / `DATABASE_URL` / `POSTGRES_URL`，真实 PostgreSQL migration contract 仍需在 `REQUIRE_POSTGRES_TESTS=1` 的 CI 中执行。
- 尚未执行两个真实 Feishu App 的接近 ready deadline 启动、轮换、stop failure、进程重启和 attachment recovery 冒烟。
- 本轮未重复执行第十四轮已运行 601 秒、到约 63% 仍无最终汇总的全量 backend suite；进入合并前仍需在可完成的 CI runner 取得完整 `pytest tests -q` / `make test` 结果。

---

## 6. 最终结论

第十四轮的 cancellation-safe leader fence、migration 严格 Gate，以及 scanner 运行期补员均已取得实质修复；`_start_row()` 内部异常也不再直接撤销 peers。但 startup convergence 的边界仍不完整：首次 DB 重读可逸出，cleanup 投影可无限等待，固定 30 秒 TTL 不能替代明确的完整启动 deadline 或 provisional renewal。

建议优先顺序：

1. 将逐 binding 的首次重读也纳入隔离，确保任何单行异常都不会让 Gateway 丢失 Supervisor；
2. 为完整 startup 建立可证明的 deadline/renewal 与可终止 projection，补慢投影和永久阻塞回归；
3. 让 scanner stop 确认 manager 与 unpublished/published workers 全部退出，并补阻塞 replenish teardown；
4. 整理重复 scanner fake；
5. 最后完成真实 PostgreSQL、双 Feishu App 和全量 backend Gate。

**Ready to merge：No。**
