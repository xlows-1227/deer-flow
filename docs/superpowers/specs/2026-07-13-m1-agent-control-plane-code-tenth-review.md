# 多租户 Agent 发布平台 - M1 第十轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第九轮代码复审：[2026-07-13-m1-agent-control-plane-code-ninth-review.md](./2026-07-13-m1-agent-control-plane-code-ninth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第九轮复审头：`1ebea745ec42c8ed69289f393427b11bd69f6207`
- 第十轮复审头：`6890dd194e10cbebe90f07655cbd239c693c488a`
- 本轮修复提交：
  - `fb244673 fix(m1): async tools, DB-required success, no filesystem-only agents`
  - `6890dd19 docs(m1): record ninth-round review fixes in impl spec`
- 本轮重点：验证原生 async tool 的 LangChain 调度方式、复查 DraftService 与兼容文件写入的一致性、验证同步 Embedded Client 兼容性，并核对第九轮遗留 Minor

---

## 1. 复审结论

本轮已经实质关闭第九轮的跨事件循环问题：`setup_agent` / `update_agent` 已改为原生 async LangChain tool，Gateway 的 `agent.astream()` 路径会在当前事件循环直接 `await` DraftService，不再通过 executor 创建新 loop。持久化已配置时，数据库失败也不再返回无警告的成功消息。

但当前仍未达到可合并标准。本轮发现 **0 个 Critical、2 个 Important、8 个 Minor**。两个阻断项分别是：

1. “数据库写入成功”目前由多个各自提交的 DraftService 调用拼接而成，文件系统又先于数据库提交；中途失败可留下 DB-only 身份、部分更新的 DB 草稿，或已更新文件系统但未更新数据库的分叉状态，仍未满足 F1.4 的唯一事实来源与一致性要求。
2. 两个工具改为 async-only 后 `StructuredTool.func` 为 `None`，而仓库公开支持的 `DeerFlowClient.stream()` 明确使用同步 `agent.stream()`；同步图执行到这两个工具时会抛出 `NotImplementedError: StructuredTool does not support sync invocation.`。

完整 M1 回归虽然为绿，但两条 setup 数据保护用例只是创建协程而未执行，测试输出已经给出 `coroutine 'setup_agent' was never awaited`，因此不能作为对应行为的验收证据。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Gateway 跨 loop 风险已关闭**：两个工具均有 async coroutine，标准异步工具调用可直接在 Gateway loop 上 await DraftService。
- **DB 失败反馈已改善**：持久化已配置且镜像失败时，ToolMessage 返回错误，不再把数据库失败报告为完整成功。
- **新建目录的文件系统清理已实现**：setup 对本次调用新建的 Agent 目录在 DB 失败后执行清理。
- **unresolved Skills 反馈保持有效**：成功写入时继续在 ToolMessage 中列出被排除的 Skills。
- **ON CONFLICT publish UOW 保持关闭**：本轮相关回归未出现孤儿 Skill revision。
- **Ruff 与 diff 检查通过，M1 相关 186 个测试通过**。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：DraftService 与文件系统兼容写入仍不是一个一致性单元，中途失败会留下部分状态

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)
- [published_agent/sql.py](../../../backend/packages/harness/deerflow/persistence/published_agent/sql.py)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md)
- [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**问题说明：**

当前 setup 的提交顺序是：

1. 直接覆盖 `config.yaml` 与 `SOUL.md`；
2. `create_agent()` 创建身份和初始草稿并提交；
3. 重新查询 Agent 与草稿；
4. `update_draft_bundle()` 提交 SOUL；
5. 若指定 Skills，再次查询 revision 并用第二个 `update_draft_bundle()` 提交 Skills。

上述 DraftService 操作不是一个事务。`PublishedAgentRepository.create_agent()`、`update_meta()` 与 Draft repository 的 `update_bundle()` 均在各自调用中提交。因此，“最后返回失败”并不等于前面的 DB 写入被回滚。

专项注入“身份创建成功、后续草稿写入失败”得到：

```text
setup_message= Error: failed to create agent 'test-agent' in the database: draft write failed
setup_db_identity_created= True
setup_filesystem_exists= False
```

这会留下数据库中的 Agent 身份/空草稿，同时删除新建文件目录；slug 也可能已被占用。对于调用前已存在的 Agent 目录，`is_new_dir=False` 会阻止清理，但代码没有备份并恢复被覆盖的 `config.yaml` / `SOUL.md`，因此 DB 失败后会留下“文件系统新值 + 数据库旧值”。

update 也先执行文件替换，再调用 DraftService；数据库失败时仅返回提示“Filesystem was updated; the draft may be out of sync”，不进行补偿。专项结果为：

```text
message= Error: Failed to update agent 'test-agent' in the database: draft write failed. Filesystem was updated; the draft may be out of sync.
filesystem_soul= new soul
```

此外，两个 helper 在 `skills is not None` 且第二次 `get_draft()` 返回 `None` 时会跳过 Skills 更新，随后仍设置 `succeeded=True`，仍存在错误成功分支。

这也使 [backend/CLAUDE.md](../../../backend/CLAUDE.md) 中“DB 失败时工具清理文件系统”的描述只对 setup 的新目录成立，对 setup 的既有目录和 update 均不成立。

**影响：**

- setup 可留下 DB-only Agent/空草稿，与第九轮的 filesystem-only 问题方向相反但同样破坏唯一事实来源；
- duplicate setup 或 update 可让文件系统与结构化 API/Studio 读取到不同配置；
- 一个 helper 内的 metadata、SOUL 和 Skills 也可能分别处于新旧状态；
- 失败重试可能遇到 slug 已占用、revision 已变化或部分字段已应用，无法可靠恢复；
- F1.4“写入走 DraftService、数据库形态与结构化 API 一致”的验收条件仍未闭环。

**建议修复：**

- 在 DraftService 增加面向对话式 setup/update 的事务级用例，一次提交 identity metadata、SOUL、model、tool groups 与 Skills；任一步骤失败时整体回滚；
- 明确数据库是唯一事实来源：先完成单事务 DB 写入，再生成/刷新兼容文件；兼容文件失败应进入可重试的明确状态，不能反向让 DB 处于未知部分状态；
- 如果迁移期必须双写，为已有文件保存原内容并提供补偿恢复，同时处理补偿自身失败；
- `skills is not None` 时若 refreshed draft 不存在，应返回失败，不得跳过后报告成功；
- 增加 create 成功后 SOUL 失败、SOUL 成功后 Skills 失败、duplicate metadata 失败、既有文件回滚和 update DB 失败的真实服务测试。

### 4.2 Important-2：async-only 工具破坏同步 `DeerFlowClient.stream()` 支持

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [client.py](../../../backend/packages/harness/deerflow/client.py)
- [test_client.py](../../../backend/tests/test_client.py)

**问题说明：**

本轮修改后的运行态为：

```text
setup_agent.coroutine: async
update_agent.coroutine: async
setup_agent.func: None
update_agent.func: None
```

当前安装的 LangChain `StructuredTool._run()` 在 `self.func` 不存在时直接执行：

```python
msg = "StructuredTool does not support sync invocation."
raise NotImplementedError(msg)
```

仓库的公开 Embedded Client 并非只支持异步 Gateway。`DeerFlowClient.stream()` 的文档明确说明它是同步 generator，并在 `client.py` 中调用：

```python
for item in self._agent.stream(...):
    ...
```

因此 bootstrap Agent 通过同步 Client 调用 `setup_agent`，或自定义 Agent 通过同步 Client 调用 `update_agent` 时，图会进入工具的同步 `_run()`，随后因没有 `func` 抛出 `NotImplementedError`。现有 `test_client.py` 只验证 stream event 行为，没有让同步图真正执行这两个工具，所以 178 条目标回归与完整 M1 回归都未发现该回归。

**影响：**

- `DeerFlowClient.stream()` / `chat()` 的 bootstrap 创建流程会在真实工具调用时崩溃；
- 自定义 Agent 的对话式自更新在同步 Embedded Client 下不可用；
- Gateway async 路径与 Embedded sync 路径能力不再对齐，违反 `client.py` 自身声明的同一 Agent factory 兼容目标。

**建议修复：**

- 为工具保留可复用的 async 核心，同时提供受支持的同步入口；同步入口必须通过明确的事件循环桥接执行，且不能重新引入共享默认池 AsyncEngine 的跨 loop 问题；或
- 将 Embedded Client 内部迁移为受控的 async 执行桥，并保持对外同步 generator API；
- 增加 `DeerFlowClient.stream()` 的端到端工具调用测试，分别覆盖 bootstrap `setup_agent` 与 custom-agent `update_agent`；
- 在 Gateway `astream()` 与 Client `stream()` 两条路径同时设置回归门禁。

---

## 5. Minor 问题

### 5.1 两条 setup 数据保护测试创建协程后未执行

[test_setup_agent_tool.py](../../../backend/tests/test_setup_agent_tool.py) 的 `test_existing_agent_dir_preserved_on_failure` 与 `test_new_agent_dir_cleaned_up_on_failure` 把 `.func(...)` 改成了 `.coroutine(...)`，但没有 `await` 或 `asyncio.run()`。pytest 明确输出两条 `RuntimeWarning: coroutine 'setup_agent' was never awaited`。两条测试的断言只观察调用前已经成立的目录状态，因此即使生产逻辑完全没有执行也会通过。

### 5.2 新 DB-required 镜像仍没有真实工具级成功/失败测试

[test_setup_agent_tool.py](../../../backend/tests/test_setup_agent_tool.py) 与 [test_update_agent_tool.py](../../../backend/tests/test_update_agent_tool.py) 主要依赖未配置持久化时的 CLI fallback。没有通过真实 DraftService/SQLite 验证 identity、SOUL、Skills 和 revision，也没有覆盖 DB 中途失败、补偿、`ainvoke()` 与错误 ToolMessage。此次发现的部分提交问题因此未被测试捕获。

### 5.3 Skill revision 仓库并发测试仍缺确定性 barrier

[test_skill_revision_repo.py](../../../backend/tests/test_skill_revision_repo.py) 仍只是双 session + `asyncio.gather()`，没有在两个首次 SELECT-miss 后同步，也没有确定性证明 conflict-do-nothing 分支被执行。

### 5.4 生产 Import adapter 仍未被测试

[test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py) 仍直接测试 `StorageSkillsIndex`，没有经 `build_import_service()` / `_OwnerAwareImportIndex` 验证 private source 与跨 owner 拒绝。

### 5.5 Draft CAS 测试仍是串行 stale-revision 测试

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 的 CAS 用例仍在 winner 完成后才执行 loser，没有真实双事务竞争。

### 5.6 SQLite schema/ORM 宽度声明仍漂移

[2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py) 仍跳过 SQLite 多数旧 ID/FK 列的声明宽度修正，迁移后反射 schema 与 ORM 声明不完全一致。

### 5.7 PostgreSQL Review Gate 仍可跳过

[test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 在本地 PostgreSQL 不可用时继续 skip；真实 asyncpg setup/update 镜像和迁移仍没有必跑 CI 门禁。

### 5.8 实现规格章节编号错误

[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md) 已有第 28 节后，第九轮修复又使用第 22 节。本次复审文档同步将该节更正为第 29 节，并追加第 30 节记录本轮结论。

---

## 6. 第九轮问题关闭状态

| 第九轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：同步 tool 经 executor/new loop 使用 AsyncEngine | **已关闭** | setup/update 已是原生 async tool，Gateway 异步路径直接 await DraftService |
| Important-2：DB 失败仍报告成功并留下 filesystem-only Agent | **部分关闭** | DB 失败已返回错误，新建文件目录会清理；但多次独立 DB commit 与先文件后 DB 仍会留下 DB-only/部分 DB/文件与 DB 分叉状态 |
| Minor-1：真实工具镜像测试缺失 | **未关闭** | 只机械转换为 coroutine 调用，未增加真实 DraftService 成功/失败覆盖；另有两条协程未执行 |
| Minor-2：Skill revision barrier | **未关闭** | 测试未增加确定性 barrier |
| Minor-3：生产 Import adapter | **未关闭** | 仍未覆盖 `_OwnerAwareImportIndex` |
| Minor-4：Draft CAS 并发 | **未关闭** | 仍为串行 stale revision |
| Minor-5：SQLite schema drift | **未关闭** | 迁移文件未修改 |
| Minor-6：PostgreSQL Review Gate | **未关闭** | 本地无 PostgreSQL 时仍 skip，且无 asyncpg mirror 门禁 |
| Minor-7：规格编号 | **本次文档关闭** | 本次把第九轮修复更正为第 29 节并新增第 30 节 |

---

## 7. 验证记录

### 7.1 完整 M1 回归

执行 M1 模型、仓库、Draft/Publish 服务、路由、导入、迁移、工具、适配器与边界测试：

```text
186 passed, 1 skipped, 4 warnings in 31.86s
```

其中：

- 1 个 skip：本地 PostgreSQL 不可用时跳过集成迁移测试；
- 2 个业务相关 warning：两条 setup 测试未 await；
- 其余 2 个 warning：LangChain 待弃用提醒与 pytest cache 目录权限提醒。

### 7.2 目标回归

执行 setup/update、publish、client 目标测试：

```text
178 passed, 1 skipped, 4 warnings
```

两条未 await warning同样存在；Client 测试没有执行 setup/update 工具。

### 7.3 静态检查

```text
Ruff: All checks passed!
git diff --check: passed
```

### 7.4 专项行为验证

- `setup_agent.coroutine` / `update_agent.coroutine` 均为 async，`func` 均为 `None`；Gateway async 路径的第九轮跨 loop 问题已关闭。
- 本地安装的 `StructuredTool._run()` 对 async-only 工具明确抛出 `NotImplementedError`；`DeerFlowClient.stream()` 明确调用同步 `agent.stream()`。
- 注入 setup 的 create-success / draft-failure：文件目录被清理，但 DB identity 保留。
- 注入 update 的 draft failure：ToolMessage 返回错误，但 `SOUL.md` 已更新为新值。
- 完整回归捕获两条 `coroutine 'setup_agent' was never awaited`，对应测试没有执行生产逻辑。

---

## 8. 修复优先级

1. 把对话式 setup/update 的数据库变更收敛为 DraftService 单事务 UOW，并明确兼容文件的提交/补偿顺序。
2. 恢复同步 Embedded Client 对 setup/update 的支持，同时保持 Gateway 的同 loop async 安全性。
3. 增加真实 DraftService 的工具级成功、部分失败、补偿与 `ainvoke()`/`stream()` 双路径测试。
4. 修复两条未执行的 setup 数据保护测试，并让 warning 作为测试失败处理。
5. 补 Skill barrier、生产 Import、真实 Draft CAS、SQLite schema 与 PostgreSQL CI 门禁。

---

## 9. 最终判定

**Ready to merge：No。**

合并前必须关闭两个 Important：setup/update 的数据库与兼容文件写入必须形成可证明的一致性流程，任一失败不能留下 DB-only、部分 DB 或文件/DB 分叉状态；async 工具改造必须保持公开同步 `DeerFlowClient.stream()` / `chat()` 的工具调用能力。
