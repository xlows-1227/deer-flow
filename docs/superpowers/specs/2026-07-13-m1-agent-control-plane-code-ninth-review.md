# 多租户 Agent 发布平台 - M1 第九轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第八轮代码复审：[2026-07-13-m1-agent-control-plane-code-eighth-review.md](./2026-07-13-m1-agent-control-plane-code-eighth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第八轮复审头：`be324d74e518d1e14fc2563766fcc0ed7c46a4e7`
- 第九轮复审头：`1ebea745ec42c8ed69289f393427b11bd69f6207`
- 本轮修复提交：
  - `7dd5c696 fix(m1): same-loop mirror, honest ToolMessage, fix unawaited test, sync docs`
  - `1ebea745 docs(m1): record eighth-round review fixes in impl spec`
- 本轮重点：确认同步 LangChain tool 的真实执行线程/事件循环、验证 DB mirror 失败后的 ToolMessage 与最终事实、复查不同内容 UOW 测试，并核对第八轮遗留 Minor

---

## 1. 复审结论

本轮修复了不同内容失败发布测试的未 `await` 问题，并正确注册第二个 Skill；CLAUDE 中 owner_scope、Alembic head 等旧描述也已同步。ON CONFLICT publish UOW 修复继续保持有效，M1 自动化回归没有新增失败。

但当前仍未达到可合并标准。本轮发现 **0 个 Critical、2 个 Important、7 个 Minor**。两个阻断项都位于对话式工具镜像：首先，setup/update 仍是没有 async coroutine 的同步 LangChain tool，异步运行时会被 `BaseTool._arun()` 放入 executor，因此 helper 内没有 running loop，实际走的是 `asyncio.run()` 新 loop，而不是代码和文档所称的 Gateway 主 loop；其次，本轮主动把镜像重新定义为 best-effort/fire-and-forget，数据库失败仍返回文件系统成功，并能创建开发计划明确禁止的“仅文件系统 Agent”。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **不同内容失败 publish 测试已修正**：`replace_skills()` 已 `await`，`public-tool` 已加入测试 SkillsIndex。
- **SQLite publish UOW 保持关闭**：失败发布严格不留下 Skill revision。
- **CLAUDE 的 owner_scope、Alembic head 描述已同步**。
- **跨线程 ThreadPoolExecutor 与无效 10 秒 timeout 已移除**。
- **Ruff 与 diff 检查通过，M1 相关 186 个测试通过**。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：同步 LangChain tool 实际运行于 executor，mirror 仍通过新 loop 使用全局 AsyncEngine

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [engine.py](../../../backend/packages/harness/deerflow/persistence/engine.py)

**问题说明：**

两个工具仍由同步 `def` 创建，运行态检查结果为：

```text
setup_agent.coroutine = None
update_agent.coroutine = None
```

当前安装的 LangChain `BaseTool._arun()` 对没有 coroutine 的同步工具执行：

```python
return await run_in_executor(None, self._run, *args, **kwargs)
```

因此在正常异步 Agent 调用中，工具函数运行在 executor worker 线程，而不是 Gateway 主事件循环。`_persist_draft_identity()` / `_persist_draft_update()` 中的 `asyncio.get_running_loop()` 会抛出 `RuntimeError`，代码随即执行 `asyncio.run(_run())`，在 worker 线程创建新的事件循环并使用 Gateway 全局 session factory。

这与代码注释、CLAUDE 和实现规格中的“在 SAME/main loop 上 `create_task`”描述相反，也仍违反 SQLAlchemy 默认池 `AsyncEngine` 不应跨事件循环共享的约束。参见 [SQLAlchemy：Using multiple asyncio event loops](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-multiple-asyncio-event-loops)。

**影响：**

- PostgreSQL/asyncpg 仍可能出现 `Future attached to a different loop`；
- `create_task()` 的所谓 same-loop 分支不是标准 `ainvoke()` 路径；
- 当前测试全部直接调用 `.func`，没有通过 `ainvoke()` 验证真实 LangChain 调度方式。

**建议修复：**

- 为 setup/update 提供真正的 async coroutine tool，在 Gateway loop 中直接 `await` DraftService；
- 文件系统同步写入可抽为 helper，并通过 `asyncio.to_thread()` 执行，避免阻塞主 loop；
- 增加经 `tool.ainvoke()` 的集成测试，断言 mirror 与 Gateway engine 位于同一 event loop；
- PostgreSQL CI 使用 asyncpg/default pool 执行一次真实 setup/update mirror。

### 4.2 Important-2：best-effort 文件系统优先违反 F1.4 验收条件，DB 失败仍返回成功

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- [2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)

**问题说明：**

开发计划 F1.4 明确要求：

- setup/update “写入走 DraftService”，文件系统旧路径仅保留读取兼容；
- DraftService 是 Studio 与对话式编写的唯一事实来源；
- `setup_agent` 创建后数据库形态必须与结构化 API 一致，不得存在“仅文件系统 Agent”。

设计 §16.3 同样要求聊天创建流程必须通过相同草稿服务与授权规则落盘，不能创建绕过控制平面的文件系统 Agent。

当前实现先提交文件系统，再以 best-effort 方式执行/调度 DB mirror；所有 DB 异常都只写 warning，ToolMessage 始终以文件系统成功为准。专项注入 `service.create_agent()` 失败后结果为：

```text
ToolMessage: Agent 'bot' created successfully!
filesystem_created: True
database_agent: absent
```

即使 unresolved 预计算成功，消息中的“were excluded from the draft”也不代表 draft 已经写入；mirror 失败时数据库可能仍是旧值或完全不存在。

**影响：**

- 可稳定产生 F1.4 明确禁止的“仅文件系统 Agent”；
- 对话式创建与 Studio/API 不再共享唯一事实来源；
- 用户可能在成功消息后找不到 Agent 草稿，或发布旧配置；
- CLAUDE/实现规格通过重新定义为 best-effort 掩盖了与开发计划和设计的偏差。

**建议修复：**

- 以 DraftService 成功作为 setup/update 成功的必要条件；
- 将数据库与文件系统兼容写入设计为明确的一致性流程：DB 失败则工具失败并清理/回滚新文件，或先 DB 后兼容文件并提供可恢复状态；
- ToolMessage 区分创建失败、部分失败与成功，不能只记录日志；
- 增加 DB create/update 失败、任务取消、duplicate metadata 失败与回滚的工具级验收测试。

---

## 5. Minor 问题

### 5.1 新 mirror 行为没有工具级测试

本轮没有修改 [test_setup_agent_tool.py](../../../backend/tests/test_setup_agent_tool.py) 或 [test_update_agent_tool.py](../../../backend/tests/test_update_agent_tool.py)。same-loop、`ainvoke()`、DB 失败日志、ToolMessage 与 unresolved 结果均没有自动化证据。

### 5.2 Skill revision 仓库并发测试仍缺确定性 barrier

[test_skill_revision_repo.py](../../../backend/tests/test_skill_revision_repo.py) 仍只是双 session + `asyncio.gather()`，没有在两个首次 SELECT-miss 后同步，也没有证明 conflict-do-nothing 分支被执行。

### 5.3 生产 Import adapter 仍未被测试

[test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py) 仍直接测试 `StorageSkillsIndex`，没有经 `build_import_service()` 验证 private source 与跨 owner 拒绝。

### 5.4 Draft CAS 测试仍是串行 stale-revision 测试

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 的两个 CAS 测试仍在 winner 完成后才执行 loser，没有真实双事务竞争。

### 5.5 SQLite schema/ORM 宽度声明仍漂移

[2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py) 仍跳过 SQLite 多数旧 ID/FK 列的声明宽度修正。

### 5.6 PostgreSQL Review Gate 仍可跳过

[test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 在本地 PostgreSQL 不可用时继续 skip；新的 mirror 调度也没有 asyncpg 集成门禁。

### 5.7 实现规格章节编号和“同 loop”描述不准确

[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md) 已有第 26 节后，第八轮修复又使用第 21 节；该节和 [backend/CLAUDE.md](../../../backend/CLAUDE.md) 都声称同步工具在主 loop 上 `create_task`，与 LangChain executor 调度不符。本次同步将修复章节改为第 27 节，并追加第 28 节。

---

## 6. 第八轮问题关闭状态

| 第八轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：跨 loop mirror / 失败反馈 | **未关闭** | 移除显式线程池，但同步 tool 的 `ainvoke()` 本身运行于 executor；DB 失败仍返回文件系统成功 |
| Minor-1：不同内容失败测试 | **已关闭** | 已补 await 并注册 public-tool |
| Minor-2：Skill revision barrier | **未关闭** | 测试文件未修改 |
| Minor-3：生产 Import adapter | **未关闭** | 测试文件未修改 |
| Minor-4：Draft CAS 并发 | **未关闭** | 测试文件未修改 |
| Minor-5：SQLite schema drift | **未关闭** | 迁移文件未修改 |
| Minor-6：PostgreSQL Review Gate | **未关闭** | 仍可 skip，且无 asyncpg mirror 测试 |
| Minor-7：规格编号 | **未关闭** | 第 26 节后回到第 21 节 |
| Minor-8：CLAUDE 基础事实 | **部分关闭** | owner_scope/head 已更新；same-loop 镜像描述不准确 |

---

## 7. 验证记录

### 7.1 自动化测试

执行 M1、迁移、工具、适配器和相关回归测试：

```text
186 passed, 1 skipped, 2 warnings in 31.39s
```

唯一 skip 为本地 PostgreSQL 不可用时跳过的集成迁移测试。

### 7.2 静态检查

```text
Ruff: All checks passed!
git diff --check: passed
```

### 7.3 专项行为验证

- `setup_agent.coroutine` 与 `update_agent.coroutine` 均为 `None`；本地 LangChain `BaseTool._arun()` 对同步工具使用 `run_in_executor()`。
- 注入 DraftService `create_agent()` 失败：ToolMessage 仍为成功，文件系统 SOUL.md 已创建。
- 修正后的双内容失败 publish 测试无未 await warning，完整 M1 回归通过。

---

## 8. 修复优先级

1. 将 setup/update 改为原生 async tool，在 Gateway loop 中直接 await DraftService。
2. 恢复 F1.4 的数据库唯一事实来源语义，DB 失败不得生成无提示的仅文件系统 Agent。
3. 增加 `ainvoke()` + SQLite/PostgreSQL 的真实工具镜像测试。
4. 补 Skill barrier、生产 Import、真实 Draft CAS 与 PostgreSQL CI 门禁。
5. 统一迁移声明与规格章节。

---

## 9. 最终判定

**Ready to merge：No。**

合并前必须关闭两个 Important：镜像必须在 AsyncEngine 所属事件循环中被真实 await；setup/update 必须以 DraftService 落盘成功作为成功条件，不能创建开发计划明确禁止的“仅文件系统 Agent”。
