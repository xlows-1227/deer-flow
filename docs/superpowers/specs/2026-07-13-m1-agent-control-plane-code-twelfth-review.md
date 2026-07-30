# 多租户 Agent 发布平台 - M1 第十二轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第十一轮代码复审：[2026-07-13-m1-agent-control-plane-code-eleventh-review.md](./2026-07-13-m1-agent-control-plane-code-eleventh-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第十一轮复审头：`cf84822c13b50c668877e55dfd535e56018a5fed`
- 第十二轮复审头：`cf84822c13b50c668877e55dfd535e56018a5fed` + 当前未提交工作区
- 本轮工作区重点：
  - 新增 setup/update 单 session/单 commit authoring UOW；
  - 新增 `AgentFileTransaction` 兼容文件补偿；
  - live persistence 下同步 `.func` 显式拒绝；
  - 补 Skill revision barrier、bundle CAS、生产 Import adapter、SQLite schema 与 PostgreSQL CI 门禁；
  - 同步 README、CLAUDE 与实现规格。

---

## 1. 复审结论

本轮已经关闭第十一轮发现的大部分正常异常路径问题：setup/update 的数据库字段现在由同一 SQLAlchemy session 一次 commit；文件替换或 commit 抛出普通异常时会尝试恢复备份并 rollback；live persistence 下同步入口不再创建新 loop；Skill revision barrier、生产 Import adapter、bundle CAS、SQLite 反射宽度和 PostgreSQL CI service 均已有实际改动。

但当前仍未达到可合并标准。本轮发现 **0 个 Critical、2 个 Important、4 个 Minor**：

1. `AgentFileTransaction` 只是进程内补偿对象，不是可恢复事务。正式文件在数据库 commit 前已经替换，进程在两者之间退出时不会执行 rollback，也没有 journal/启动恢复；下一进程只会看到新文件与随机 `.bak`，数据库仍可能是旧值。开发计划要求迁移期文件路径只读，当前双写仍不能成为真正的一致性单元。
2. duplicate setup 的 UOW 只锁 `published_agents` identity 行，读取 `agent_drafts` 时没有 `FOR UPDATE` 或 CAS。它可与结构化 PATCH 的 revision CAS 同时基于 revision=N 成功，随后 setup 的普通 ORM UPDATE 按主键覆盖 PATCH 内容并同样写成 revision=N+1，形成已确认设计上可能发生的 lost update。

此外，正式 `make lint` 的 Ruff format 门禁会因 8 个文件未格式化而失败。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **数据库内部部分提交已关闭（正常异常路径）**：setup identity/draft/Skills 与 update metadata/draft/Skills 各使用一个 session、一次 commit。
- **文件失败与 DB 异常补偿已实现**：staging、backup、apply、rollback、finish 已集中到 `AgentFileTransaction`。
- **同步跨 loop 风险已关闭**：live session factory 存在时 `.func` 明确返回错误，不再启动线程或新 event loop；README 已说明该限制。
- **update 的 DB 失败临时文件泄漏已关闭**：普通 DB 失败会触发 `files.rollback()` 清理 `.tmp` / `.bak`。
- **Skill revision 并发 barrier 已补**：两个事务在首次 SELECT-miss 后确定性同步。
- **生产 Import adapter 已覆盖**：测试经 `build_import_service()` 验证 `_OwnerAwareImportIndex` 的 public/private 与跨 owner 规则。
- **Draft bundle CAS 已补真实 barrier**：两个 bundle 更新同时到达 CAS，只允许一个成功。
- **SQLite schema drift 已修正**：迁移在 SQLite 实际 batch rebuild，并断言反射宽度。
- **PostgreSQL CI migration gate 已建立**：GitHub Actions 启动 PostgreSQL 16，设置 `REQUIRE_POSTGRES_TESTS=1`，安装 postgres extra。
- **规格章节已恢复连续编号**：第十轮修复为 31、第十一轮复审为 32、第十一轮修复为 33。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：兼容文件“事务”没有持久化恢复能力，commit 前崩溃仍会造成文件/DB 分叉

**相关文件：**

- [agent_file_transaction.py](../../../backend/packages/harness/deerflow/tools/builtins/agent_file_transaction.py)
- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [published_agent/sql.py](../../../backend/packages/harness/deerflow/persistence/published_agent/sql.py)
- [agents_config.py](../../../backend/packages/harness/deerflow/config/agents_config.py)
- [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**问题说明：**

新 UOW 的实际顺序是：

1. SQL 变更 `flush()`，但尚未 commit；
2. repository 调用同步 `before_commit()`；
3. `AgentFileTransaction.apply()` 把旧正式文件移动为随机 `.bak`，把 staged 文件替换成正式文件；
4. `await session.commit()`；
5. commit 成功后 `finish()` 删除备份；异常时 `rollback()` 尝试恢复。

对于被 Python 捕获的普通文件异常或 DB commit 异常，这个补偿流程有效。但 `AgentFileTransaction` 的所有状态都只保存在内存中的 `_staged` / `_backups`；没有事务 ID、持久化 journal、固定备份映射或启动扫描恢复。

专项模拟在 `apply()` 后丢弃对象并“重启”得到：

```text
official_config_after_restart= new config
official_soul_after_restart= new soul
orphan_backups= 2
restart_knows_backups= 0
```

如果进程在第 3、4 步之间退出，数据库事务会由连接关闭回滚，但正式文件已经是新值；旧文件只存在随机 `.bak` 中，新进程没有任何方式识别应恢复哪一组。commit 的 await 期间，其他协程也可能通过当前文件系统 loader 暂时读到未提交的新配置。

这与 `backend/CLAUDE.md` / README 所称的“单个可回滚一致性单元”不符。类自身 docstring 实际也只承诺 “best-effort unit”。更重要的是，开发计划 F1.4 明确要求写入走 DraftService，文件系统旧路径仅在迁移窗口保留读取兼容；风险表也要求结束 setup 双写，而不是在 SQL transaction 中嵌入文件替换。

**影响：**

- Gateway 崩溃、进程被终止、主机掉电时仍会留下数据库旧值 + 文件新值；
- 新进程无法自动恢复随机备份，长期遗留 `.bak`；
- commit 完成前，当前运行时可能读取未提交配置；
- “正常异常测试通过”不能证明跨资源原子性，运维恢复也没有确定步骤；
- Studio/API 只更新数据库、对话式工具双写文件，唯一事实来源仍不统一。

**建议修复：**

- 按开发计划让自定义 Agent authoring/runtime 直接读取 DraftService，迁移窗口的旧文件路径只读，移除每次写入的兼容文件双写；这是唯一能真正消除跨资源提交窗口的方案；
- 如果短期必须保留双写，至少引入持久化 transaction journal：固定 transaction id、目标/backup 映射、DB 状态、启动恢复和幂等清理，并在 ToolMessage/运维状态中暴露 unresolved recovery；
- 不要在 async DB transaction 内执行阻塞文件 I/O；将兼容投影建模为 commit 后可重试的派生投影，并让运行时不依赖其即时一致性；
- 增加进程中断/重启恢复测试，而不仅是 callback 抛异常测试。

### 4.2 Important-2：duplicate setup 未锁 draft/CAS，可覆盖成功的结构化 PATCH

**相关文件：**

- [published_agent/sql.py](../../../backend/packages/harness/deerflow/persistence/published_agent/sql.py)
- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)
- [test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py)

**问题说明：**

`setup_authoring_bundle()` 查找 identity 时使用：

```python
select(PublishedAgentRow)...with_for_update()
```

但 slug 已存在的分支读取 draft 时只是：

```python
draft = await session.get(AgentDraftRow, agent.id)
```

随后直接修改 ORM 对象并执行：

```python
draft.revision += 1
await session.flush()
```

SQLAlchemy 对这个 ORM 更新只按主键生成 UPDATE，没有 `WHERE revision = :expected`。锁住 identity 行并不会阻止结构化 PATCH 的 `AgentDraftRepository.update_bundle()` 直接更新 draft 行。

一个 PostgreSQL 时序可以让两边都报告成功：

1. duplicate setup 锁 identity，读取 draft revision=N；
2. Studio PATCH 执行 `UPDATE agent_drafts ... WHERE revision=N`，成功提交 revision=N+1；
3. setup flush 普通主键 UPDATE，覆盖 PATCH 内容，并把自己内存中的 N+1 写回；
4. setup commit 成功。

最终 revision 仍是 N+1，但 PATCH 已返回成功的内容被静默丢失。`update_authoring_bundle()` 已对 draft 使用 `with_for_update=True`，而 duplicate setup 分支没有；新增并发测试也只覆盖 `AgentDraftRepository.update_bundle()` 自身，没有覆盖 authoring UOW 与结构化 PATCH 的交叉竞争。

**影响：**

- 聊天 bootstrap 重试/duplicate setup 可覆盖网页 Studio 已成功保存的草稿；
- 两个调用都返回成功，revision 只增加一次，客户端无法发现 lost update；
- PostgreSQL CI 已存在但没有覆盖这一真实 row-lock/CAS 组合。

**建议修复：**

- duplicate setup 至少对 draft 使用 `session.get(..., with_for_update=True)`；更稳妥的是复用同一 DB-level CAS 更新语句并检查 rowcount；
- 明确 setup 对已存在 slug 的语义：若它是“创建”，应返回冲突而不是静默转为更新；若允许覆盖，必须要求/获取 revision 并遵守同一乐观并发契约；
- 增加 PostgreSQL 集成测试：authoring setup/update 与结构化 `update_bundle()` 在同一 revision 上竞争，保证最多一个基于旧 revision 的写入成功。

---

## 5. Minor 问题

### 5.1 `make lint` 的 Ruff format 门禁失败

`ruff check` 通过，但仓库 Makefile 的 lint 同时执行 `ruff format --check .`。当前检查结果：

```text
Would reformat: published_agent/sql.py
Would reformat: draft_service.py
Would reformat: setup_agent_tool.py
Would reformat: update_agent_tool.py
Would reformat: test_published_agent_repo.py
Would reformat: test_setup_agent_tool.py
Would reformat: test_update_agent_tool.py
Would reformat: test_user_model_capabilities_migration.py
8 files would be reformatted
```

因此 M1 Review Gate 的 `make lint` 尚未满足。

### 5.2 setup 的“既有目录不丢失”测试已经失效

[test_setup_agent_tool.py](../../../backend/tests/test_setup_agent_tool.py) 的 `test_existing_agent_dir_preserved_on_failure` 仍 patch `Path.write_text`；新实现通过 `NamedTemporaryFile` + `Path.replace` 写文件，该 patch 不再触发失败。测试随后只断言原 `SOUL.md` “存在”，不检查原内容与 config 内容，因此 setup 实际成功覆盖文件时测试仍会通过。

应改为在 `AgentFileTransaction.apply()` 的第二个 replace 或 DB commit 处注入失败，并断言 config/SOUL 原始内容、数据库 revision 和 `.tmp`/`.bak` 均保持原状。

### 5.3 `update_with_revision` 的所谓并发测试仍是串行 stale-revision

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 已为 `update_bundle()` 增加 `_before_cas` barrier，但 `test_concurrent_draft_update_only_one_wins` 仍先 await winner、再 await loser。普通 partial-update CAS 路径的真实双事务竞争仍未覆盖，测试名称与 docstring 继续声称 concurrent。

### 5.4 authoring UOW 返回的 draft 会错误清空 Connector grants

`setup_authoring_bundle()` / `update_authoring_bundle()` 构造返回值时只传入 Skills：

```python
_draft_to_dict(draft, skills=await _load_skill_dicts(...))
```

`connector_grants` 使用默认 `None`，序列化为 `[]`。若既有草稿拥有 grants，只更新 description 后，数据库仍保留 grants，但方法返回值声称为空。专项验证：

```text
returned_grants= []
stored_grants= [{'connector_instance_id': 'c1', 'capability': 'read'}]
```

当前工具只用 `saved is None`，所以尚未影响 ToolMessage；但 DraftService 新方法的返回契约不完整，未来复用会向调用方提供错误状态。应在同一 session 加载 grants，或只返回明确声明的最小结果类型。

---

## 6. 第十一轮问题关闭状态

| 第十一轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：DB/file 部分状态与错误成功 | **部分关闭** | 单 SQL commit 与普通异常补偿已实现；commit 前进程退出仍不可恢复，文件双写仍违背只读兼容方向 |
| Important-2：同步 bridge 跨 loop | **已关闭** | live persistence 下同步写入口显式拒绝并已文档化；无新线程/新 loop 复用 engine |
| Minor-1：DB 失败残留 staged 文件 | **已关闭** | 普通 DB 失败统一 rollback/cleanup |
| Minor-2：真实工具/失败测试 | **部分关闭** | 新增 SQLite UOW 测试；缺 commit-failure、进程重启与交叉并发，且 setup 旧测试已失效 |
| Minor-3：Skill revision barrier | **已关闭** | 首次 SELECT-miss 后已有确定性 barrier |
| Minor-4：生产 Import adapter | **已关闭** | 已经由 factory 构建并验证 owner-aware index |
| Minor-5：Draft CAS 并发 | **部分关闭** | bundle 路径已有真实 barrier；partial update 路径仍串行 |
| Minor-6：SQLite schema drift | **已关闭** | SQLite 实际重建并有反射宽度断言 |
| Minor-7：PostgreSQL Review Gate | **已关闭（迁移范围）** | CI service + REQUIRED gate 已配置；authoring row-lock 仍缺 PG 集成测试 |
| Minor-8：规格编号 | **已关闭** | 31/32/33 连续，顶部状态已同步 |

---

## 7. 验证记录

### 7.1 M1 专项回归

执行 M1 模型、仓库、Draft/Publish 服务、路由、导入、迁移、工具、适配器与边界测试：

```text
192 passed, 1 skipped, 2 warnings in 33.31s
```

1 个 skip 为本地 PostgreSQL 不可用；CI 中 `REQUIRE_POSTGRES_TESTS=1` 后该测试不可跳过。

### 7.2 全量 backend 测试

执行：

```text
uv run pytest tests -q
```

当前工作区全量测试未通过，主要失败/错误源于本地 `config.yaml` 引用了未设置的 `KIMI_API_KEY`，并连带影响 live Client/Gateway 等环境依赖测试。该结果不能归因于本轮 M1 修改，也不能作为有效全量回归证据；应在与 CI 一致的干净配置/环境中重新执行 `make test`。

### 7.3 静态检查

```text
Ruff check: All checks passed!
Ruff format --check: failed (8 files would be reformatted)
git diff --check: passed
```

### 7.4 专项行为验证

- 丢弃已 `apply()` 的文件事务对象模拟进程退出：正式文件为新值，留下 2 个 `.bak`，新实例不知道备份映射。
- authoring update 只改 description：返回 draft 的 Connector grants 为 `[]`，重新读取数据库仍有原 grant。
- live persistence 下 setup/update sync `.func` 返回明确错误，不创建新 loop。
- Skill barrier、bundle CAS、生产 Import、SQLite reflected width 与 M1 相关回归均通过。

---

## 8. 修复优先级

1. 移除 authoring 写时文件双写，让 DraftService 成为运行时唯一事实来源；或实现有持久化 journal 与启动恢复的真正可恢复投影。
2. 为 duplicate setup 的 draft 获取 row lock/CAS，并增加与结构化 PATCH 的 PostgreSQL 交叉并发测试。
3. 运行 Ruff format，恢复 `make lint` 门禁。
4. 修复失效的 setup 故障测试与串行 partial CAS 测试；补 commit/restart 恢复覆盖。
5. 修正 authoring UOW 返回值中的 Connector grants。

---

## 9. 最终判定

**Ready to merge：No。**

合并前必须关闭两个 Important：兼容文件投影必须在进程中断后仍可恢复，或从 authoring 写路径中移除；duplicate setup 必须与结构化 PATCH 共享 draft row lock/CAS，不能让两个成功请求发生 lost update。同时必须恢复仓库要求的 Ruff format 门禁。
