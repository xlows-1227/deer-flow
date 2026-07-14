# 多租户 Agent 发布平台 — M2 第二轮 Review Gate 复审

**状态：** 代码侧 P1 已全部关闭；剩余 3 项 Important 验证门禁与 1 项 Minor 文档同步项待完成

**日期：** 2026-07-14

**关联文档：**

- M2 实现规格：[2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)
- 第一轮及修复复审：[2026-07-14-m2-published-runtime-agent-api-code-review.md](./2026-07-14-m2-published-runtime-agent-api-code-review.md)
- 总开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- Published Agent API 冒烟示例：[backend/docs/API.md](../../../backend/docs/API.md#published-agent-api-m2)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 固定点：`a3666db2706d72e29a1f635e5048f9013d908d3b`
- 复审对象：更新版 M2 Review 修复提交及其验证记录
- 重点：已识别 P1 的关闭状态，以及 M2 Review Gate 中尚未取得环境证据的项目

---

## 1. 复审结论

更新版 Review 中的 **7 项 Spec P1、1 项 Standards P1、1 项 Standards P2 均已关闭**。最终双轴代码复审结果为：

- Spec：0 个剩余 blocker；
- Standards：0 个剩余 finding；
- 当前没有新增或未关闭的已知代码 P1。

M2 focused regression、Gateway lifespan 接线、Ruff、格式、编译及 Alembic single-head 检查均已通过。不过，开发计划的完整 M2 Review Gate 还要求全量回归与真实链路验证；目前仍有以下待完成项：

1. PostgreSQL 上尚未取得 M2 quota/usage 并发与恢复语义的独立测试证据。
2. 全量 `make test` 尚未在干净环境跑到结束；本地执行被既有 Windows ACL 问题阻断。
3. 尚未使用真实模型完成 Published Agent API 的同步、SSE、异步、幂等与撤销 Key 全链路冒烟。
4. M2 实现规格页首状态与最终复审结论不一致，开发计划状态表也尚未反映代码侧 Gate 已关闭。

**Ready to enter M3：Yes。** M3 可以开始开发并复用 M2 Resolver、quota 与 usage 能力。

**Ready to merge for production：No。** 必须先关闭本文件的 3 项 Important；Minor 文档项最迟随 Gate 关闭记录一并同步。

---

## 2. Spec 轴

### 2.1 更新版 Review 问题关闭状态

| 上一轮问题组 | 本轮状态 | 复核结果 |
|---|---|---|
| 7 项 Spec P1 | **已关闭** | 冻结 Skill 正文、精确 Connector capability、Published 辅助模型、最终输入 token cap、强制 usage、持久化结算恢复及取消孤儿窗口均已修复并补回归 |
| Standards P1：Skill revision 缺少 owner/public scope | **已关闭** | 协议与 SQL 查询要求 `owner_user_id`，跨 owner private revision fail closed |
| Standards P2：`record_usage()` 冲突读取未校验 owner | **已关闭** | 入口要求显式 owner，冲突读取联合过滤 owner，不再返回其他 owner 的 usage |

本轮未发现新的需求缺失、scope creep、租户隔离回归或可复现的代码 P1。下面的 Important 均是 **Review Gate 证据缺口**，不表示已经证明存在新的生产代码缺陷。

---

## 3. Review Gate 轴

### 3.1 Important-1：PostgreSQL 上的 M2 quota/usage 并发门禁缺少直接覆盖

**状态：** 待完成，阻塞生产合并

**相关文件：**

- [test_quota_reservation.py](../../../backend/tests/test_quota_reservation.py#L39)
- [test_agent_usage_accounting.py](../../../backend/tests/test_agent_usage_accounting.py#L22)
- [agent_usage/sql.py](../../../backend/packages/harness/deerflow/persistence/agent_usage/sql.py#L129)
- [backend-unit-tests.yml](../../../.github/workflows/backend-unit-tests.yml#L21)

当前 CI 已启动 PostgreSQL 16，并设置 `TEST_POSTGRES_URL` 与 `REQUIRE_POSTGRES_TESTS=1`；但 M2 的 quota reservation 与 usage accounting fixture 仍显式创建 SQLite 数据库。现有 PostgreSQL service 可以执行 M1 authoring 并发测试，却不能单独证明 M2 使用的 advisory transaction lock、`ON CONFLICT`、终态幂等结算和恢复扫描在 PostgreSQL 上行为正确。

**需要补充：**

1. 新增 PostgreSQL 专项测试，例如 `tests/test_agent_usage_postgres_concurrency.py`；从 `TEST_POSTGRES_URL` 连接，且在 `REQUIRE_POSTGRES_TESTS=1` 时缺少数据库必须失败而不是 skip。
2. 在 PostgreSQL 16 上从 Alembic base/当前生产基线升级到 head，确认 M2 quota、usage、idempotency 与 audit 表结构可用。
3. 并发预留用例必须验证：`max_concurrent_runs=2` 时并发 6 个不同请求，恰好 2 个 reservation 成功，其余请求得到 `max_concurrent_runs_exceeded`，数据库中最多只有 2 个 pending。
4. 同一 `request_key` 的并发 reserve 必须返回同一 reservation；同一 `run_id` 的重复 settle 最终只产生一条 `agent_usage_records`。
5. 覆盖至少一个持久化恢复场景：bound pending reservation 在新进程/新 repository 实例中恢复后恰好结算一次。
6. 将上述测试接入现有 PostgreSQL CI job，并保留可审计的通过日志。

**关闭标准：**

- PostgreSQL migration upgrade 通过；
- 新增 M2 PostgreSQL 专项测试全部通过且无条件 skip；
- CI 中 `REQUIRE_POSTGRES_TESTS=1` 生效；
- 并发上限、幂等 reserve、exactly-once settle 与 restart recovery 四类断言均有直接证据。

### 3.2 Important-2：全量 backend 回归尚未取得完整绿灯

**状态：** 待完成，阻塞严格 M2 Review Gate

**相关文件：**

- [开发计划 M2 Review Gate](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L683)
- [test_aio_sandbox_provider.py](../../../backend/tests/test_aio_sandbox_provider.py)
- [backend-unit-tests.yml](../../../.github/workflows/backend-unit-tests.yml#L55)

本轮执行完整 `pytest tests -q -x` 时，在 `118 passed` 后停止于既有 Windows ACL 失败：测试对 `.deer-flow/.../workspace` 执行 `chmod(0o777)` 时收到 `PermissionError [WinError 5]`。该路径与 M2 diff 无关，但因为测试没有执行到结尾，仍不能把本地结果声明为“全仓 `make test` 全绿”。

**需要补充：**

1. 在干净 Linux CI 或权限正常的等价环境执行 `cd backend && make test`，保留完整测试摘要和 job 链接。
2. 确认现有 Web UI thread/run 路径、External API V1、普通非 Published Run 与 Channel 基线没有回归。
3. 如果 CI 仍失败，先按固定点区分基线失败与 M2 引入失败；不能通过新增无条件 skip、删除断言或缩小默认 `make test` 范围来关闭 Gate。
4. 若 Windows 是正式支持的开发/运行平台，应另行修复或隔离 `chmod` 的平台兼容问题；若不是，则在贡献文档中明确支持边界，但仍以 Linux CI 全绿作为本项关闭证据。

**关闭标准：**

- 干净 CI 的完整 `make test` 退出码为 0；
- 输出中没有 setup error、unexpected skip 或被截断的测试阶段；
- Web UI runtime 与 External API V1 兼容测试有明确通过记录。

### 3.3 Important-3：真实模型 Published Agent API 全链路 smoke 尚未执行

**状态：** 待完成，阻塞生产合并

**相关文件：**

- [Published Agent API 文档](../../../backend/docs/API.md#published-agent-api-m2)
- [agent_public_api.py](../../../backend/app/gateway/routers/agent_public_api.py)
- [开发计划 M2 Review Gate](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L683)

ASGI 路由测试已经覆盖认证、wait、SSE、异步、get、cancel、幂等重放、撤销 Key、跨 Agent/credential 拒绝与 429；但本轮没有连接真实外部模型，也没有在实际 Gateway 进程中执行 curl 全流程。因此，模型 provider、SSE 传输、运行时 middleware、真实 token usage 与数据库结算的组合行为尚缺部署环境证据。

**需要执行的 smoke：**

| 步骤 | 操作 | 必须验证 |
|---|---|---|
| 1 | 发布一个绑定真实模型的 Agent 并创建 Agent Key | Key 明文只返回一次，日志和后续响应不泄漏 secret |
| 2 | 读取 metadata，创建 Conversation | 仅返回外部白名单字段；Conversation 受 credential 隔离 |
| 3 | 调用同步 `/runs/wait` | Run 到达终态并返回模型消息；响应不含 owner、Release、内部路径或 secret |
| 4 | 使用同一 `Idempotency-Key` 重放相同请求 | 返回同一 `run_id`，不新增 Run、reservation 或 usage |
| 5 | 调用 SSE `/runs/stream` | 流正常结束，事件中没有内部 Release/owner 字段 |
| 6 | 创建异步 Run，再执行 get/cancel | 状态转换正确；取消后 reservation 恰好结算一次 |
| 7 | 查询数据库 usage/quota | token 为真实非零值；reservation 为 settled；每个外部 Run 恰好一条 usage |
| 8 | 撤销 Agent Key 后再次请求 | 返回 401，且不会新建 Conversation、Run 或 reservation |

**关闭标准：**

- 上表 8 个步骤全部通过；
- 保存脱敏后的请求/响应、Gateway 日志、数据库核对结果与测试环境说明；
- 至少覆盖一个真实模型成功 Run、一个取消 Run、一次幂等重放和撤销后 401；
- smoke 失败时先形成可复现用例并补自动化回归，再重新执行完整 smoke。

---

## 4. Standards 轴

### 4.1 Minor-1：M2 状态文档存在相互矛盾的历史结论

**状态：** 待同步，不阻塞 M3 开发

**相关文件：**

- [M2 实现规格](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md#L3)
- [M2 代码复审](./2026-07-14-m2-published-runtime-agent-api-code-review.md#L1)
- [开发计划状态表](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md#L1083)

M2 代码复审 §8 与实现规格 §11.7 已记录“Spec 0 blocker、Standards 0 finding、代码侧 Gate 关闭”，但实现规格第 3 行仍保留“7 项 P1 阻塞、Gate 重新打开”的旧状态；开发计划状态表仍写“待评审（专项通过，全量 CI 待确认）”。读者可能因此无法判断 P1 是否已关闭，以及 M3 是否允许启动。

**建议修复：**

1. 立即把实现规格页首改为“代码侧 Review Gate 已关闭；生产合并验证待完成”。
2. 开始 M3 时把开发计划状态表改为“M2 代码复审通过，生产验证待完成；M3 进行中”。
3. 3 项 Important 全部关闭后，再把 M2 状态更新为“Review Gate 通过/生产合并就绪”，并在本文件附上 CI 与 smoke 证据。

**关闭标准：** 三份文档对“代码 P1”“进入 M3”“生产合并”三个维度使用一致且不互相替代的状态描述。

---

## 5. 当前验证基线

```text
13 个 M2/修复相关测试文件：204 passed
Gateway lifespan / Published service wiring：4 passed
ruff check --no-cache --exclude .tmp-review .：All checks passed
ruff format --no-cache --check --exclude .tmp-review .：679 files already formatted
python -m compileall -q app packages/harness/deerflow：通过
git diff --check：通过
Alembic heads：2026_07_14_agent_audit_principals (head)

完整 pytest tests -q -x：118 passed 后被既有 Windows ACL 问题阻断
M2 PostgreSQL quota/usage 专项并发门禁：未执行
真实模型 Published Agent API smoke：未执行
```

---

## 6. 后续关闭清单

- [ ] Important-1：补充并通过 PostgreSQL M2 quota/usage 并发与恢复门禁。
- [ ] Important-2：在干净 CI 中取得完整 backend `make test` 绿灯。
- [ ] Important-3：完成真实模型 Published Agent API 八步 smoke，并保存脱敏证据。
- [ ] Minor-1：同步实现规格页首、M2 Review 文档与开发计划状态表。
- [ ] 在本文件追加每项的执行日期、commit、CI/job 链接或本地日志摘要。
- [ ] 3 项 Important 全部关闭后，将 `Ready to merge for production` 更新为 `Yes`。

建议关闭顺序：**PostgreSQL 专项门禁 → 全量 CI → 真实模型 smoke → 文档状态同步**。前两项如发现代码问题，应先补失败测试并修复；真实 smoke 如暴露 provider/部署差异，也必须沉淀为自动化回归后再关闭。

---

## 7. 最终结论

当前没有未关闭的已知 M2 代码 P1，M3 可以开始开发。剩余 3 项 Important 均属于生产合并前必须补齐的验证证据：PostgreSQL 并发/恢复、全量 backend 回归、真实模型 API smoke。它们未通过前，不应把“代码复审通过”解释为“M2 已完成生产验收”。

**Ready to enter M3：Yes。**

**Ready to merge for production：No（等待 3 项 Important Gate 关闭）。**
