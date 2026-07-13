# 多租户 Agent 发布平台 - M1 第十一轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第十轮代码复审：[2026-07-13-m1-agent-control-plane-code-tenth-review.md](./2026-07-13-m1-agent-control-plane-code-tenth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第十轮复审头：`6890dd194e10cbebe90f07655cbd239c693c488a`
- 第十一轮复审头：`cf84822c13b50c668877e55dfd535e56018a5fed`
- 本轮修复提交：
  - `332c7b6e fix(m1): DB-first write order, sync tool entry, no partial state`
  - `cf84822c docs(m1): record tenth-round review fixes in impl spec`
- 本轮重点：验证 DB-first 是否形成真正的事务一致性单元、验证同步 `.func` 是否在持久化启用时安全、复查文件写失败反馈，并核对第十轮遗留 Minor

---

## 1. 复审结论

本轮修复了两条 setup 测试未执行协程的问题；两个 LangChain tool 也已经同时具备 async coroutine 与 sync `.func`。setup 的 SOUL 与 Skills 已合并进同一次 `update_draft_bundle()`，比上一轮减少了一次 Draft commit。

但当前仍未达到可合并标准。本轮发现 **0 个 Critical、2 个 Important、8 个 Minor**：

1. DB-first 只改变了双写顺序，没有创建事务级 DraftService 用例。setup 的 identity 与 draft bundle、update 的 metadata 与两个 draft bundle 仍分别提交；任何后续失败都会保留前序数据库状态。数据库成功后的兼容文件写失败又被静默降级为成功，而当前 Agent 运行时仍读取这些文件，因此会稳定产生 DB-only、部分 DB、文件/DB 分叉和错误成功。
2. 新增同步桥 `_run_async()` 无条件在新事件循环执行 coroutine，并以“同步 Client 一定没有 DB engine”为安全前提；但 `build_draft_service()` 的真实判断只取决于全局 session factory 是否存在。只要进程已经初始化持久化，同步入口就会在新 loop/专用线程复用全局 `AsyncEngine`，重新引入第八至第十轮要求消除的跨 loop 风险。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **两条 setup 数据保护测试已真实执行**：`.coroutine(...)` 已由 `asyncio.run()` 包裹，完整回归不再出现未 await warning。
- **async Gateway 路径保持同 loop**：`setup_agent.coroutine` / `update_agent.coroutine` 继续直接 await DraftService。
- **同步 LangChain 入口已经存在**：`setup_agent.func` 与 `update_agent.func` 不再为 `None`，不会再直接触发 `StructuredTool does not support sync invocation`。
- **setup 的 SOUL 与 Skills 更新已合并**：同一 `update_draft_bundle()` 内由 Draft CAS 事务提交。
- **DB 失败发生在兼容文件提交前**：正式 `config.yaml` / `SOUL.md` 不会先于 DB 被替换。
- **Ruff 与 diff 检查通过，M1 相关 186 个测试通过**。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：DB-first 不是原子 UOW，数据库和兼容文件仍会产生部分状态及错误成功

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)
- [published_agent/sql.py](../../../backend/packages/harness/deerflow/persistence/published_agent/sql.py)
- [agents_config.py](../../../backend/packages/harness/deerflow/config/agents_config.py)
- [backend/CLAUDE.md](../../../backend/CLAUDE.md)

**问题说明：**

#### setup 仍有两个独立数据库提交

`_mirror_draft_identity()` 当前顺序为：

1. `create_agent()` 提交 identity + 空草稿；duplicate slug 时，`update_agent_meta()` 独立提交 metadata；
2. `update_draft_bundle()` 再提交 SOUL + Skills。

第二步失败不会回滚第一步。专项注入结果：

```text
setup_partial= {'succeeded': False, 'unresolved': [], 'error': 'draft write failed'}
identity_created= True
```

即工具返回创建失败，但数据库已经留下 Agent identity/空草稿，slug 也已经占用。所谓 DB-first 并没有消除第十轮发现的 DB-only 状态。

#### update 仍有最多三个独立数据库提交

`_persist_draft_update()` 依次执行：

1. `update_agent_meta()` 提交 description；
2. 第一个 `update_draft_bundle()` 提交 SOUL/model/tool groups；
3. 第二个 `update_draft_bundle()` 提交 Skills。

后续步骤失败时，前序变更保持提交。专项注入 Skills 写入失败：

```text
update_partial= {'succeeded': False, 'unresolved': [], 'error': 'skills write failed'}
metadata_updated= True
draft_fields_updated= True
```

因此 ToolMessage 报告失败时，metadata 与部分草稿已经改变，重试还会面对新的 revision。

#### DB 成功后的文件失败被报告为完整成功

setup 在数据库成功后直接写 `config.yaml`，再写 `SOUL.md`；任一文件失败只记录日志，最终仍返回 `created successfully`。专项注入 `yaml.dump()` 失败：

```text
setup_fs_failure_message= Agent 'bot' created successfully!
setup_fs_failure_dir_exists= True
```

update 在 DB 成功后逐个 `Path.replace()`；文件替换失败也只记录日志并继续返回完整成功。专项注入：

```text
update_fs_failure_message= Agent 'bot' updated successfully. Changed: soul. The new configuration takes effect on the next user turn.
update_fs_failure_soul= old soul
```

该消息明确承诺“next user turn”生效，但当前自定义 Agent 构建仍通过 `load_agent_config()` 与 `SOUL.md` 读取文件系统，新值实际上不会生效。若 config 已替换而 SOUL 替换失败，还会留下混合版本。

此外，在无 DB 的 CLI fallback 中，setup 对既有目录先覆盖 `config.yaml`、再写 `SOUL.md`；第二步失败时不会恢复已覆盖的 config。现有测试只断言原 SOUL 文件仍“存在”，没有断言其内容及 config 均未改变。

这与 [backend/CLAUDE.md](../../../backend/CLAUDE.md) 中“no DB-only, filesystem-only, or divergent state”的描述不符，也与开发计划 F1.4 中“写入走 DraftService，文件系统旧路径仅保留读取兼容”和设计 §16.3 的唯一事实来源要求不符。

**影响：**

- 创建失败仍可占用 slug 并留下空草稿；
- 更新失败可实际提交 description、SOUL/model/tool groups 中的一部分；
- 成功消息后对话运行仍可能读取旧配置、缺失文件或混合版本；
- 失败重试无法判断哪些字段已经提交，CAS revision 也可能已经递增；
- 文档声明、ToolMessage、数据库事实与运行时读取结果可能四者不一致。

**建议修复：**

- 在 repository/DraftService 增加真正的 setup UOW：一个 session/transaction 内完成 identity、空草稿、SOUL、Skills，duplicate metadata 更新也纳入同一事务；
- 增加 update UOW：metadata、草稿主行与 Skills 使用一个 session 和一次 commit，统一 CAS；
- 按开发计划让对话运行直接读取 DraftService；文件系统在迁移期只读，不再作为每次写入的第二提交目标；
- 如果短期必须双写，文件失败不得返回完整成功，且需要 staging、备份、补偿状态与可恢复重试，不应只写日志；
- 增加 create-success/draft-failure、metadata-success/skills-failure、DB-success/filesystem-failure、CLI 既有文件回滚测试。

### 4.2 Important-2：同步 `_run_async` 在持久化启用时重新跨 loop 使用全局 AsyncEngine

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [factory.py](../../../backend/packages/harness/deerflow/publishing/factory.py)
- [engine.py](../../../backend/packages/harness/deerflow/persistence/engine.py)
- [client.py](../../../backend/packages/harness/deerflow/client.py)

**问题说明：**

`_run_async()` 的安全说明建立在以下假设上：

```text
sync DeerFlowClient.stream() -> CLI -> build_draft_service() returns None
```

但生产实现没有根据调用方是不是 CLI 判断。`build_draft_service()` 仅执行：

```python
sf = get_session_factory()
if sf is None:
    return None
```

只要当前进程已经通过 Gateway lifespan、测试夹具或嵌入式调用初始化了持久化，全局 session factory 就非空，同步工具会取得真实 DraftService。

此时：

- 同步调用线程没有 running loop：`asyncio.run(coro)` 创建新 loop，并复用现有全局 `AsyncEngine`；
- 调用线程已有 running loop：代码显式创建专用线程，再在其中 `asyncio.run(coro)`，仍是新 loop + 同一个全局 engine；
- `result(timeout=15)` 不能把数据库 UOW安全取消；`ThreadPoolExecutor` 的 context manager 在退出时默认等待任务，timeout 也不是可靠的终止边界。

这重新引入 SQLAlchemy 默认池 AsyncEngine 跨事件循环共享风险，PostgreSQL/asyncpg 可出现 `Future attached to a different loop` 等运行时错误。参见 [SQLAlchemy：Using multiple asyncio event loops](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-multiple-asyncio-event-loops)。

当前目标测试只调用 `.coroutine`，没有任何测试调用 `setup_agent.func`、`update_agent.func` 或让 `DeerFlowClient.stream()` 真正执行工具，也没有在 live SQLite/PostgreSQL engine 下验证同步桥。

**影响：**

- `.func` 虽然存在，但持久化启用时仍可能在真实工具调用中失败；
- 同步 Client 在不同进程初始化顺序下行为不同，难以复现；
- 专用线程/15 秒 timeout 可能造成调用方收到异常时后台数据库操作仍继续或等待；
- 第十轮关闭的 async-only 回归被替换成新的跨 loop 回归。

**建议修复：**

- 不要让同步入口自行创建 event loop 并直接复用全局 async session factory；
- 由持久化生命周期拥有者提供线程安全的 portal/loop bridge，把 coroutine 提交回 AsyncEngine 所属 loop；或让 Embedded Client 内部统一使用受控 async 图执行，再向外桥接同步 generator；
- 如果 Embedded Client 明确只支持无 DB 模式，应在 API 层显式拒绝 live persistence，而不是依赖 `get_session_factory() is None` 的隐含条件；
- 增加 `.func` 真实调用、`DeerFlowClient.stream()` 工具调用，以及 live PostgreSQL 默认池门禁。

---

## 5. Minor 问题

### 5.1 update 的 DB 失败路径泄漏 staged 临时文件

[update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py) 在调用 DraftService 前已通过 `_stage_temp()` 写入新配置/SOUL；`mirror["succeeded"] == False` 时直接返回，没有调用 `_cleanup_temps(staged_temps)`。专项验证：

```text
message= Error: Failed to update agent 'bot' in the database: db failed.
leftover_tmp_count= 1
old_soul= old
```

临时文件可能保留完整的新 SOUL/config 内容。至少应在所有 DB 失败、取消和异常返回前清理。

### 5.2 对话式工具仍缺真实 DB、文件失败与同步入口测试

[test_setup_agent_tool.py](../../../backend/tests/test_setup_agent_tool.py) 与 [test_update_agent_tool.py](../../../backend/tests/test_update_agent_tool.py) 仍主要运行 `build_draft_service() is None` 的 fallback；没有真实 DraftService/SQLite、部分 DB commit、DB 后文件失败或 `.func`/Client stream 覆盖。setup 的“既有目录不丢失”用例只断言文件存在，没有断言原 SOUL 内容和 config 内容保持不变。

### 5.3 Skill revision 仓库并发测试仍缺确定性 barrier

[test_skill_revision_repo.py](../../../backend/tests/test_skill_revision_repo.py) 仍只是双 session + `asyncio.gather()`，没有在两个首次 SELECT-miss 后同步，也没有确定性证明 conflict-do-nothing 分支被执行。

### 5.4 生产 Import adapter 仍未被测试

[test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py) 仍直接测试 `StorageSkillsIndex`，没有经 `build_import_service()` / `_OwnerAwareImportIndex` 验证 private source 与跨 owner 拒绝。

### 5.5 Draft CAS 测试仍是串行 stale-revision 测试

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 的 CAS 用例仍在 winner 完成后才执行 loser，没有真实双事务竞争。

### 5.6 SQLite schema/ORM 宽度声明仍漂移

[2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py) 仍跳过 SQLite 多数旧 ID/FK 列的声明宽度修正，迁移后反射 schema 与 ORM 声明不完全一致。

### 5.7 PostgreSQL Review Gate 仍可跳过

[test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py) 在本地 PostgreSQL 不可用时继续 skip；事务级 setup/update 与同步 bridge 都没有必跑 asyncpg 门禁。

### 5.8 实现规格状态与章节编号再次错误

[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md) 顶部仍写“第九轮修复已提交；第十轮复审待修复”，第 30 节后又把第十轮修复编号为第 23 节。本次复审同步将其更正为第 31 节，并追加第 32 节。

---

## 6. 第十轮问题关闭状态

| 第十轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：DraftService 与兼容文件不是一致性单元 | **未关闭** | 只改为 DB-first；setup/update 仍有多个独立 DB commit，DB 后文件失败仍返回成功 |
| Important-2：async-only 工具破坏同步 Client | **部分关闭** | `.func` 已存在，但通过新 loop/线程复用 live AsyncEngine，重新引入跨 loop 风险 |
| Minor-1：两条 setup 测试未 await | **已关闭** | 已由 `asyncio.run()` 执行，相关 warning 消失 |
| Minor-2：真实 DB 镜像测试 | **未关闭** | 未增加真实服务、部分提交、文件失败或 sync Client 测试 |
| Minor-3：Skill revision barrier | **未关闭** | 测试未增加确定性 barrier |
| Minor-4：生产 Import adapter | **未关闭** | 仍未覆盖 `_OwnerAwareImportIndex` |
| Minor-5：Draft CAS 并发 | **未关闭** | 仍为串行 stale revision |
| Minor-6：SQLite schema drift | **未关闭** | 迁移文件未修改 |
| Minor-7：PostgreSQL Review Gate | **未关闭** | 本地无 PostgreSQL 时仍 skip |
| Minor-8：规格编号 | **未关闭** | 第 30 节后再次回退为第 23 节，顶部状态也未更新 |

---

## 7. 验证记录

### 7.1 完整 M1 回归

执行 M1 模型、仓库、Draft/Publish 服务、路由、导入、迁移、工具、适配器与边界测试：

```text
186 passed, 1 skipped, 2 warnings in 36.98s
```

其中 1 个 skip 为本地 PostgreSQL 不可用时跳过集成迁移测试；两个 warning 为 LangChain 待弃用提醒与 pytest cache 目录权限提醒。上轮两条未 await warning 已消失。

### 7.2 目标回归

执行 setup/update、Client 与 publish 测试：

```text
178 passed, 1 skipped, 2 warnings in 11.93s
```

目标测试未调用两个工具的 `.func`，也未模拟 live persistence 下的 `DeerFlowClient.stream()` 工具执行。

### 7.3 静态检查

```text
Ruff: All checks passed!
git diff --check: passed
```

### 7.4 专项行为验证

- setup identity 成功、draft bundle 失败：helper 返回失败，但 identity 已创建。
- update metadata/首个 draft bundle 成功、Skills bundle 失败：helper 返回失败，但 metadata 与草稿字段均已改变。
- DB 成功、setup 文件写失败：仍返回 created successfully，目录已产生。
- DB 成功、update 文件替换失败：仍返回 updated successfully，SOUL 仍为旧值。
- update DB 失败：正式文件未替换，但留下包含新内容的 `.tmp` 文件。
- `build_draft_service()` 只检查全局 session factory，不能保证同步 Client 场景一定返回 `None`。

---

## 8. 修复优先级

1. 为 setup/update 提供真正的单 session/单 commit DraftService UOW，关闭 DB 内部部分提交。
2. 移除写时文件系统双写，或实现明确的可恢复兼容事务；文件失败不得返回完整成功。
3. 让同步 Client 把工作提交回 AsyncEngine 所属 loop，禁止 `_run_async()` 新 loop 直接复用全局 engine。
4. 增加真实 DB、文件失败、`.func` 和 `DeerFlowClient.stream()` 工具调用回归；修复临时文件清理与弱断言。
5. 补 Skill barrier、生产 Import、真实 Draft CAS、SQLite schema 与 PostgreSQL CI 门禁。

---

## 9. 最终判定

**Ready to merge：No。**

合并前必须关闭两个 Important：DB-first 必须升级为真正的事务级唯一事实来源，不能留下 identity/metadata/draft/Skills 的部分提交或 DB 后文件失败的错误成功；同步入口必须在持久化启用时保持 AsyncEngine 单事件循环约束，而不是通过 `asyncio.run()` 或专用线程创建新 loop。
