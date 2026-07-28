# 多租户 Agent 发布平台 - M1 第八轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第七轮代码复审：[2026-07-13-m1-agent-control-plane-code-seventh-review.md](./2026-07-13-m1-agent-control-plane-code-seventh-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第七轮复审头：`f1e94b01d924f80de1d629855721980c58dbbe1b`
- 第八轮复审头：`be324d74e518d1e14fc2563766fcc0ed7c46a4e7`
- 本轮修复提交：
  - `d0e7d2f8 fix(m1): atomic publish UOW (ON CONFLICT), synchronous tool mirror`
  - `be324d74 docs(m1): record seventh-round review fixes in impl spec`
- 本轮重点：复现 SQLite publish UOW、验证 ON CONFLICT 并发去重、检查同步 Tool mirror 的事件循环/连接池安全性与失败反馈，以及核对新增回归测试是否真正覆盖描述场景

---

## 1. 复审结论

本轮已经实质关闭 SQLite publish UOW 问题。Skill revision 改为方言级 `ON CONFLICT DO NOTHING` 后，不再依赖 SAVEPOINT；本轮使用两个不同 Skill 内容连续注入 Release 创建失败，两个 revision 计数都严格为 0。强制两个 session 同时 SELECT-miss 的并发验证也返回同一 canonical revision，最终只有一行。

但当前仍未达到可合并标准。本轮发现 **0 个 Critical、1 个 Important、8 个 Minor**。唯一阻断项是同步镜像实现：`_run_mirror_sync()` 把 Gateway 已初始化的默认池 `AsyncEngine`/session factory 带到专用线程的新事件循环中执行，违反 SQLAlchemy 的多事件循环约束；超时或线程执行失败又返回 `None`，setup/update 会把它当成“无镜像结果”并继续报告无警告成功，因此第七轮的“失败对调用方可见”仍未闭环。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **SQLite publish UOW 已修复**：失败 publish 后 Release 与 Skill revision 都为 0。
- **不同内容失败不会再累积孤儿 revision**：专项验证 `reporting` 与 `public-tool` 两个不同内容均保持 0。
- **Skill revision 并发去重保持有效**：强制双 SELECT-miss 后两个事务返回同一 revision ID，数据库最终一行。
- **正常完成的 mirror 可以返回 `{succeeded, unresolved}`**：ToolMessage 不再单独重新计算 unresolved。
- **第五至第七轮已关闭的迁移、Connector registry、清空语义和 unresolved 过滤未发现新回归**。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：同步 mirror 跨事件循环共享 AsyncEngine，失败/超时仍被误报为成功

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [engine.py](../../../backend/packages/harness/deerflow/persistence/engine.py)

**问题说明：**

`build_draft_service()` 使用 Gateway 启动时创建的全局 `async_sessionmaker`，其底层 PostgreSQL `AsyncEngine` 使用默认连接池。`_run_mirror_sync()` 每次创建 `ThreadPoolExecutor`，再在线程内通过 `asyncio.run()` 创建新的事件循环并使用该 session factory。

SQLAlchemy 官方文档明确说明：使用默认池时，同一个 `AsyncEngine` 不应在不同 asyncio event loop 间共享；若确需跨 loop，必须先 dispose，或使用 `NullPool`，否则可能出现 `Future attached to a different loop`。参见 [SQLAlchemy：Using multiple asyncio event loops](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-multiple-asyncio-event-loops)。当前生产 engine 既没有在每次 mirror 前 dispose，也不是 `NullPool`。

该实现还有两个可复现的失败反馈问题：

1. `_run_mirror_sync()` 捕获线程、数据库或 timeout 异常后返回 `None`；setup/update 仅在 `mirror_result is not None` 时追加警告，所以真实失败会继续返回无警告成功。
2. `future.result(timeout=10)` 被放在 `with ThreadPoolExecutor(...)` 中。发生 timeout 后，退出 context manager 会执行 `shutdown(wait=True)`，仍等待 worker 完成。专项调用一个 11.5 秒 coroutine 的结果为：

```text
result=None
elapsed_seconds=11.5
```

因此 10 秒并不是有效的工具延迟上限。

此外，setup duplicate-agent 路径中 `update_agent_meta()` 异常仍被 `pass`，没有把 `succeeded` 设为 `False`。专项 fake-service 结果：

```text
update_agent_meta: RuntimeError("meta write failed")
mirror_result: {"succeeded": true, "unresolved": []}
```

**影响：**

- PostgreSQL 生产环境的 setup/update mirror 可能直接发生跨 loop RuntimeError；
- mirror 超时、数据库异常或 metadata 更新失败时，用户仍可能收到无警告成功；
- 在同步工具运行于事件循环线程时，阻塞等待还会冻结该 loop；
- 新实现没有工具级测试覆盖成功、失败、timeout、跨 loop 或 duplicate metadata 分支。

**建议修复：**

- 将 setup/update 工具改为原生 async tool，在 Gateway 所属事件循环中直接 `await` mirror，不要把全局 AsyncEngine 移到新 loop；
- 或通过受支持的主 loop 调度桥接，但必须避免在同一 loop 线程内阻塞等待；
- 明确区分 `unavailable`、`failed`、`timed_out` 与 `succeeded`，任何真实失败都必须进入 ToolMessage；
- duplicate setup 的所有失败分支都更新 `succeeded=False` 并携带 error；
- 增加 SQLite 与 PostgreSQL/asyncpg 的工具级集成测试，以及 timeout/metadata failure 回归测试。

---

## 5. Minor 问题

### 5.1 “不同内容失败”测试漏掉 `await`，第二个场景没有执行

[test_publish_service.py](../../../backend/tests/test_publish_service.py) 调用 `AgentDraftRepository.replace_skills(...)` 时没有 `await`。测试输出明确产生：

```text
RuntimeWarning: coroutine 'AgentDraftRepository.replace_skills' was never awaited
```

因此 draft 仍引用 `reporting`，随后对 `public-tool` 的零行断言只是查询一个从未发布过的 Skill。fixture 的 `_StaticSkillsIndex` 也只注册了 `reporting`，与“already knows public-tool”的注释不一致。本轮独立专项验证补齐注册与 `await` 后确认生产实现正确，但仓库回归测试仍是假覆盖。

### 5.2 Skill revision 仓库并发测试仍缺确定性 barrier

[test_skill_revision_repo.py](../../../backend/tests/test_skill_revision_repo.py) 仍只是双 session + `asyncio.gather()`，没有在首次 SELECT-miss 后同步两个事务，也没有断言确实进入 conflict-do-nothing 路径。

### 5.3 生产 Import adapter 仍未被测试

[test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py) 仍直接测试 `StorageSkillsIndex`，没有通过 `build_import_service()` 验证 `_OwnerAwareImportIndex`、private source 与跨 owner 拒绝。

### 5.4 Draft CAS 测试仍是串行 stale-revision 测试

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 的两个 CAS 测试仍在 winner 完成后才执行 loser，没有真实双事务竞争。

### 5.5 SQLite schema/ORM 宽度声明仍漂移

[2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py) 仍跳过 SQLite 多数旧 ID/FK 列的声明宽度修正。

### 5.6 PostgreSQL 迁移 Review Gate 仍可跳过

[test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 在本地 PostgreSQL 不可用时继续 skip。ON CONFLICT PostgreSQL 路径与新 mirror 的 asyncpg 行为也没有强制 CI 证据。

### 5.7 实现规格章节编号再次重复

[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md) 已有第 24 节后，第七轮修复又使用第 20 节。本次同步将其修正为第 25 节，并追加第 26 节。

### 5.8 README/CLAUDE 未按仓库规则同步，且现有描述已过期

[backend/CLAUDE.md](../../../backend/CLAUDE.md) 明确要求每次代码变更同步 README/CLAUDE，但本轮没有修改两者。当前 CLAUDE 仍描述：

- Skill revision 唯一键为 `(skill_name, owner_user_id, content_checksum)`，实际已改为 `owner_scope`；
- setup/update 是 `best-effort mirror`，与当前“阻塞等待结果”的实现不一致；
- M1 Alembic head 仍是 `2026_07_12_agent_releases`，实际 head 已是 `2026_07_12_widen_agent_ids`。

---

## 6. 第七轮问题关闭状态

| 第七轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：SQLite publish UOW | **已关闭（实现）** | ON CONFLICT 路径专项验证两个不同内容失败后均为 0；仓库第二场景测试需修正 |
| Important-2：mirror 完成/失败反馈 | **未关闭** | 不再 fire-and-forget，但跨 loop 共享 engine；失败/timeout 返回 None 后仍无警告成功 |
| Minor-1：Skill revision barrier | **未关闭** | 测试文件未修改 |
| Minor-2：生产 Import adapter | **未关闭** | 测试文件未修改 |
| Minor-3：Draft CAS 并发 | **未关闭** | 测试文件未修改 |
| Minor-4：SQLite schema drift | **未关闭** | 迁移文件未修改 |
| Minor-5：PostgreSQL Review Gate | **未关闭** | 本地/CI 路径仍可 skip |
| Minor-6：规格编号 | **未关闭** | 第 24 节后回到第 20 节 |

---

## 7. 验证记录

### 7.1 自动化测试

执行 M1、迁移、工具、适配器和相关回归测试：

```text
186 passed, 1 skipped, 3 warnings in 35.13s
```

新增的第三个 warning 是未 await coroutine；唯一 skip 为本地 PostgreSQL 不可用时跳过的集成迁移测试。

### 7.2 静态检查

```text
Ruff: All checks passed!
git diff --check: passed
```

### 7.3 专项行为验证

- 修正测试数据后连续失败发布 `reporting` 与 `public-tool`：两者 revision 数均为 0。
- 强制两个 session 同时 SELECT-miss：两个调用返回同一 revision ID，最终一行。
- 11.5 秒 coroutine 通过 `_run_mirror_sync()` 执行：返回 `None`，实际耗时 11.5 秒，证明 10 秒 timeout 不截断等待。
- duplicate setup 的 metadata 写入注入异常：返回 `succeeded=True`，证明失败状态仍可误报。

---

## 8. 修复优先级

1. 移除跨线程新 event loop 对全局 AsyncEngine 的使用，让 mirror 在所属事件循环中被真实 await。
2. 让 mirror 的 unavailable/failed/timeout 都成为明确 ToolMessage 状态，并修正所有 `succeeded` 分支。
3. 修复未 await 的双内容失败测试，并给工具 mirror 增加真实集成测试。
4. 补 barrier、生产 Import、真实 Draft CAS 与 PostgreSQL CI 门禁。
5. 同步 README/CLAUDE 与规格章节。

---

## 9. 最终判定

**Ready to merge：No。**

合并前必须修复 Important-1：数据库 mirror 不能通过新的事件循环共享默认池 AsyncEngine，且任何数据库失败或 timeout 都不能继续向调用方报告无警告成功。
