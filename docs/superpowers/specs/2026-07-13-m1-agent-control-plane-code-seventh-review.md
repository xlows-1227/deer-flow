# 多租户 Agent 发布平台 - M1 第七轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第六轮代码复审：[2026-07-13-m1-agent-control-plane-code-sixth-review.md](./2026-07-13-m1-agent-control-plane-code-sixth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第六轮复审头：`0d62ec59668ef08e48a31ee7224c3d357efd90f8`
- 第七轮复审头：`f1e94b01d924f80de1d629855721980c58dbbe1b`
- 本轮修复提交：
  - `966b7088 fix(m1): unresolved skills feedback, concurrent tests, adapter test cleanup`
  - `f1e94b01 docs(m1): record sixth-round review fixes in impl spec`
- 本轮重点：验证 unresolved Skills 反馈是否与实际镜像结果一致、失败 publish 是否保持完整 UOW、并发测试是否确定性进入竞争窗口，以及第六轮 Minor 是否形成生产路径回归证据

---

## 1. 复审结论

本轮对 unresolved Skills 的直接反馈已有实质改进：`filter_selectable_skills()` 现在返回 `(selectable, unresolved)`，普通 setup/update 场景会在 ToolMessage 中列出不可用 Skill；Import 测试原先损坏的函数边界也已拆开。Skill revision 的生产 SAVEPOINT 实现通过强制双 SELECT-miss 专项验证，两个事务最终返回同一 canonical revision。

但当前仍未达到可合并标准。本轮发现 **0 个 Critical、2 个 Important、6 个 Minor**。其中最重要的数据一致性问题是 SQLite 上失败 publish 会提交 Skill revision 的 SAVEPOINT、随后回滚 Release 外层事务，最终留下孤儿 revision；不同 Skill 内容版本的连续失败会持续累积。另一个阻断项是 setup/update 在事件循环中以 fire-and-forget 方式启动数据库镜像，ToolMessage 在镜像执行前就报告成功，镜像失败仍然不可见。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **普通 unresolved Skills 名单已可计算并反馈**：`DraftService.filter_selectable_skills()` 同时返回有效子集与 unresolved 子集。
- **setup/update 的正常路径会提示 unresolved 名称**：ToolMessage 附加不可用 Skill 警告，后台镜像也写 warning 日志。
- **Skill revision SAVEPOINT 的生产去重逻辑可处理真实唯一键竞争**：强制两个 session 同时 SELECT-miss 后，两次调用均成功并返回同一 revision ID。
- **Import/Connector 测试函数边界已修复**：Import 相关断言不再嵌套在 Connector whitelist 测试函数中。
- **StorageSkillsIndex 的 private owner 元数据读取 fake 已修正**。
- **第五轮已关闭的 PostgreSQL 长 stamp、Connector registry 和清空语义未发现新回归**。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：SQLite 失败 publish 会留下并累积孤儿 Skill Revision

**相关文件：**

- [skill_revision/sql.py](../../../backend/packages/harness/deerflow/persistence/skill_revision/sql.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [test_publish_service.py](../../../backend/tests/test_publish_service.py)

**问题说明：**

`_get_or_create_in_session()` 在共享 publish session 内使用 `session.begin_nested()` 创建 SAVEPOINT。当前 SQLite/aiosqlite 路径上，Skill revision INSERT 的 SAVEPOINT 会先形成独立可见提交；后续 `create_and_point()` 失败、外层 publish 事务回滚后，该 revision 没有被撤销。

本轮故障注入结果：

```text
orphan_skill_revisions=1
releases=0
```

进一步使用两个不同 Skill 内容版本连续执行失败 publish：

```text
after_failed_publish_v1: revisions=1
after_failed_publish_v2: revisions=2
```

因此当前测试注释所称“内容去重保证失败发布不会累积”只对完全相同 checksum 成立；Skill 内容变化时孤儿记录会持续增长。`test_failed_publish_leaves_no_orphan_skill_revisions` 使用 `len(revs_after) <= 1`，恰好掩盖了该 UOW 破坏。

**影响：**

- SQLite 生产/开发环境的 publish 不是完整原子事务；
- 每次不同 Skill 内容的失败发布都可能留下不可达 revision；
- 测试名称和实现规格声称“失败回滚无孤儿”，但当前证据与之相反。

**建议修复：**

- 确保 SQLite 在 SAVEPOINT 前已经真实开始外层事务，或改用不需要嵌套事务的原子 upsert/冲突忽略方案；
- 对 SQLite 与 PostgreSQL 都断言失败 publish 后 revision 数严格为 `0`；
- 增加两个不同内容版本连续失败的回归测试，防止以 checksum 去重掩盖事务泄漏。

### 4.2 Important-2：ToolMessage 未等待数据库镜像，失败时仍报告成功

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)

**问题说明：**

`_persist_draft_identity()` 与 `_persist_draft_update()` 在已有事件循环时调用 `loop.create_task(_run())` 后立即返回。setup/update 随即重新构建 service、独立计算 unresolved 名单，并构造成功 ToolMessage。该消息既没有等待草稿写入，也没有消费镜像任务的最终结果。

镜像内部多个异常分支仍直接 `return`，外层只在少数初始化异常时写 debug 日志。专项时序检查确认 helper 返回时后台镜像尚未开始，直到下一个 event-loop tick 才执行。

因此 `Warning: ... were excluded` 只表示预计算认为这些 Skill 不可选，不表示数据库草稿已经成功写入有效子集。任务失败或会话结束导致 task 取消时，文件系统已经更新，数据库草稿可能仍保留旧值，但调用方收到无条件成功。

**影响：**

- 文件系统与数据库事实源分叉仍不可见；
- 用户可能基于错误的成功消息继续发布旧草稿；
- 当前新增测试只覆盖纯过滤函数，没有覆盖 ToolMessage、镜像失败或任务取消。

**建议修复：**

- 让镜像 helper 可等待并返回结构化结果，例如 `MirrorResult(unresolved, succeeded, error)`；
- 构造 ToolMessage 前等待镜像完成，明确区分文件系统成功、草稿成功和草稿失败；
- 如果必须 fire-and-forget，消息应标记为 pending，并提供可查询的持久化状态；
- 增加 unresolved、镜像异常和 task cancel 的工具级测试。

---

## 5. Minor 问题

### 5.1 新 Skill revision “并发”测试没有确定性屏障

[test_skill_revision_repo.py](../../../backend/tests/test_skill_revision_repo.py) 虽然改用两个 session 和 `asyncio.gather()`，但没有在两个首次 SELECT-miss 后设置 barrier。调度器仍可能让第一个事务提交后，第二个事务才完成首次 SELECT；未使用的 `session_id` 也没有参与同步。测试应同时断言确实发生过一次唯一键冲突恢复。

### 5.2 Import 测试仍未覆盖生产 `_OwnerAwareImportIndex`

[test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py) 新增测试直接构造 `StorageSkillsIndex`，没有经过 [factory.py](../../../backend/packages/harness/deerflow/publishing/factory.py) 中 `build_import_service()` 的生产 wiring，也没有执行一次 private Skill 导入并验证最终 draft 的 `source == "private"` 与跨 owner 拒绝。

### 5.3 Draft CAS 测试仍是串行 stale-revision 测试

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 的两个 CAS 测试仍在 winner 完成后才调用 loser。它们能证明 stale revision 被拒绝，但不能证明两个独立事务真实竞争时只允许一个获胜。

### 5.4 SQLite schema/ORM 宽度声明仍漂移

[2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py) 仍跳过 SQLite 多数旧 ID/FK 列扩宽。SQLite 功能上不强制 `VARCHAR(n)`，但 schema reflection 与 ORM 声明持续不一致；应完成 batch rebuild 或明确记录为接受的永久差异。

### 5.5 PostgreSQL 迁移 Review Gate 仍可跳过

[test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 在本地 PostgreSQL 不可用时继续 skip。当前实现逻辑未发现新问题，但 PostgreSQL 长 revision stamp 仍缺少本轮可重复的强制 CI 证据。

### 5.6 实现规格编号和事实描述仍不准确

[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md) 已有第 21、22 节后，第六轮修复又使用第 19 节；同时声称 `asyncio.gather` “迫使两个协程同时 SELECT-miss”，但测试没有 barrier。本次同步将章节修正为第 23 节，并在第 24 节记录实际状态。

---

## 6. 第六轮问题关闭状态

| 第六轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：unresolved Skills 静默丢弃 | **部分关闭** | 正常路径能列出 unresolved；镜像完成/失败仍不反馈，成功消息与实际落库未绑定 |
| Minor-1：SQLite schema drift | **未关闭** | 仍跳过多数旧列宽度声明修正 |
| Minor-2：Skill revision 真实并发测试 | **部分关闭** | 已有双 session + gather，但没有确定性 barrier |
| Minor-3：Draft CAS 真实并发 | **未关闭** | 测试文件未修改，仍串行 |
| Minor-4：生产 Import adapter 测试 | **部分关闭** | 函数边界已修复，但只测 StorageSkillsIndex |
| Minor-5：严格 UOW/PG 门禁 | **未关闭，并确认实现缺陷** | `<=1` 掩盖 SQLite 孤儿 revision；PG 仍可 skip |
| Minor-6：规格章节编号 | **未关闭** | 第 22 节后回到第 19 节，且并发证据描述过度 |

---

## 7. 验证记录

### 7.1 自动化测试

执行 M1、迁移、工具、适配器和相关回归测试：

```text
185 passed, 1 skipped, 2 warnings in 28.96s
```

唯一 skip 为本地 PostgreSQL 不可用时跳过的集成迁移测试。

### 7.2 静态检查

```text
Ruff: All checks passed!
git diff --check: passed
```

### 7.3 专项行为验证

- 强制两个 session 同时完成首次 SELECT-miss 后再 INSERT：两个调用成功返回同一 revision，最终只有 1 行 canonical revision。
- 在 Release 创建阶段注入失败：SQLite 最终 `releases=0`，但 `skill_revisions=1`。
- 修改 Skill 内容后再次执行失败 publish：revision 数由 1 增至 2，确认会按不同 checksum 累积。
- 事件循环时序检查：镜像 helper 在 `create_task()` 后立即返回，ToolMessage 生成早于后台 `_run()` 执行。

---

## 8. 修复优先级

1. 修复 SQLite publish UOW，严格保证失败后 Release 与 Skill revision 都为 0。
2. 将 setup/update ToolMessage 与真实数据库镜像结果绑定，返回结构化成功/失败状态。
3. 给 Skill revision 与 Draft CAS 测试增加双 session barrier。
4. 通过生产 `build_import_service()` 验证 private Skill 导入。
5. 固化 PostgreSQL CI Review Gate，并决定 SQLite schema 声明策略。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭两个 Important：失败 publish 不能留下或累积孤儿 Skill revision；setup/update 不能在数据库草稿尚未写入或已经失败时继续向调用方报告无条件成功。
