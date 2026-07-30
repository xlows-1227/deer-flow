# 多租户 Agent 发布平台 - M1 第三轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-12

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第一轮代码评审：[2026-07-12-m1-agent-control-plane-code-review.md](./2026-07-12-m1-agent-control-plane-code-review.md)
- 第二轮代码复审：[2026-07-12-m1-agent-control-plane-code-rereview.md](./2026-07-12-m1-agent-control-plane-code-rereview.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第二轮复审头：`f0e4c3d367bcffdd1ae69726fb13c8d7d1a9c739`
- 第三轮复审头：`b8a4fcfd4be747272a1bb8542d1ddaa70d763fc5`
- 本轮修复提交：
  - `03d82c16 fix(m1): PostgreSQL migration + DB-level CAS draft updates + public skill dedup`
  - `b8a4fcfd fix(m1): single-transaction publish, availability guards, adapter get(), tool mirror`
- 本轮重点：逐项验证第二轮的 2 个 Critical 与 5 个 Important 是否真实关闭，并复核 M1 规格、开发计划与 PostgreSQL 升级安全性

---

## 1. 复审结论

本轮修复有明显进展：draft 更新的数据库级 CAS 已落地；Release 与 `current_release_id` 已进入同一事务；干净数据库中的 public Skill revision 已使用非空 scope 去重；禁用 Skill、导入 visibility、`update_agent` description 以及 soul/skills 分步镜像均有实质修复。

但当前版本仍未达到可合并标准。第三轮复审发现 **2 个 Critical、5 个 Important 与 4 个 Minor** 问题。两个 Critical 均位于纠偏迁移：新的 Alembic revision ID 超过 PostgreSQL `alembic_version.version_num` 的 32 字符上限；旧库如果已经存在重复 public Skill revisions，迁移会在创建新唯一约束时失败。

此外，生产模型可用性仍不感知用户自定义模型，Connector 仍不是严格的 `active` 白名单，发布事务尚未覆盖 Skill revision upsert，对话式镜像在重复 setup 与部分无效 Skill 场景仍会分叉。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Draft CAS 核心语义已修复**：`update_with_revision()` 与 `update_bundle()` 均使用条件 `UPDATE ... WHERE revision = ?`，通过 `rowcount` 判定唯一胜者；bundle 子表与主表在同一事务提交。
- **Release 与线上指针已原子化**：`AgentReleaseRepository.create_and_point()` 在同一 session/commit 中创建 Release、子表并切换 `current_release_id`，修复了原先两次提交的孤儿 Release 窗口。
- **新库 public Skill revision 去重已修复**：`owner_scope` 为 public Skill 提供非 NULL 唯一键，顺序重复发布能复用 revision。
- **禁用 Skill 已被发布校验拒绝**：`StorageSkillsIndex.is_selectable_by()` 显式检查 `enabled`。
- **导入 visibility 适配器已补齐**：生产 `_OwnerAwareImportIndex` 已实现 `get()`，不再因缺方法而默认写成 public。
- **对话式镜像有实质改善**：`update_agent` 已同步 description；soul/model/tool_groups 与 skills 分步提交，坏 Skill 不再阻断 soul 等字段落库。

---

## 3. Critical 问题

### 3.1 Critical-1：Alembic revision ID 超过 PostgreSQL 版本表上限

**相关文件：**

- [2026_07_12_widen_published_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_published_agent_ids.py)
- [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py)

**问题说明：**

新迁移的 revision 为：

```text
2026_07_12_widen_published_agent_ids
```

该字符串长度为 **36**。仓库迁移测试与 Alembic 默认版本表均使用 `alembic_version.version_num VARCHAR(32)`。SQLite 不执行 `VARCHAR(n)` 长度约束，因此本地 SQLite 测试会通过；PostgreSQL 在 migration body 执行后写入版本号时会拒绝 36 字符值。

当前 PostgreSQL 测试依赖本地数据库，不可用时直接 skip；本轮验证中该项确实被跳过，所以现有绿灯没有覆盖这一阻断。

**影响：**

- PostgreSQL 无法升级到新的 head；
- Gateway 自动迁移捕获异常后继续启动，可能让应用运行在旧 schema 上；
- M1 “SQLite / PostgreSQL 均可执行”的 Review Gate 不成立。

**建议修复：**

- 将 revision ID 缩短到不超过 32 字符，例如 `2026_07_12_widen_agent_ids`；
- 使用真实 PostgreSQL 执行完整升级链并断言最终 version stamp；
- CI 中不要把 PostgreSQL 迁移测试作为可长期跳过项。

---

### 3.2 Critical-2：纠偏迁移无法升级已存在重复 public revisions 的旧库

**相关文件：**

- [2026_07_12_widen_published_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_published_agent_ids.py)
- [agent_release/model.py](../../../backend/packages/harness/deerflow/persistence/agent_release/model.py)

**问题说明：**

旧 schema 的唯一约束是 `(skill_name, owner_user_id, content_checksum)`；public Skill 的 `owner_user_id=NULL`，因此数据库允许重复行。纠偏迁移当前执行：

1. 回填 `owner_scope = COALESCE(owner_user_id, 'public')`；
2. 直接创建 `(skill_name, owner_scope, content_checksum)` 唯一约束。

如果旧库已经产生重复 public revisions，两行都会被回填为相同 `owner_scope='public'`，唯一约束创建会失败。迁移没有先选择 canonical revision、重写 `agent_release_skills.skill_revision_id` 引用并删除重复行。

**本地复现：**

构造一个处于 `2026_07_12_agent_releases` 的旧 SQLite schema，插入两条相同 public revision 后执行自动升级，结果为：

```text
version=2026_07_12_agent_releases owner_scope_present=False
```

即升级没有到达新 head；自动迁移入口吞掉异常后返回，旧 schema 被保留。

**影响：**

- 最需要纠偏迁移的已有数据库可能无法升级；
- Gateway 可能继续启动，但 ORM 已开始读取不存在的 `owner_scope` 列，后续请求会 500；
- 已有 Release 对重复 revision 的引用无法自动收敛。

**建议修复：**

- 创建新唯一约束前，按 `(skill_name, owner_scope, checksum)` 选择 canonical revision；
- 将 `agent_release_skills.skill_revision_id` 全部改指 canonical revision；
- 删除重复 revision 后再创建唯一约束；
- 增加“旧库含重复 public revision 且被多个 Release 引用”的升级回归测试。

---

## 4. Important 问题

### 4.1 Important-1：widen 迁移错误地放宽全部目标列的可空性

**相关文件：**

- [2026_07_12_widen_published_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_published_agent_ids.py)
- [agent_release/model.py](../../../backend/packages/harness/deerflow/persistence/agent_release/model.py)

**问题说明：**

迁移对 `_WIDEN` 中所有列统一调用：

```python
batch_op.alter_column(..., type_=sa.String(64), nullable=True)
```

该列表同时包含主键、必填关联列与唯一允许为空的 `published_agents.current_release_id`。完整 SQLite 迁移后的诊断结果为：

```text
agent_releases.agent_id_notnull=0 null_rows_inserted=1
```

也就是说，原本 `nullable=False` 的 `agent_releases.agent_id` 已被迁移放宽，且数据库实际接受无归属 Release。新库也会先创建正确 schema，再被纠偏迁移放宽。

**影响：**

- 数据库 schema 与 ORM 定义漂移；
- 数据库不再保护 Release/Revision 的必填归属关系；
- PostgreSQL 对主键执行 `nullable=True` 还可能导致迁移 DDL 失败。

**建议修复：**

- 在 `_WIDEN` 中记录每一列真实的 `existing_nullable` / 目标 nullable；
- 仅 `published_agents.current_release_id` 保持可空，其余 ID/FK 按原 schema 保持非空；
- 增加迁移后 nullability、PK、FK、unique constraint 的 schema 断言。

---

### 4.2 Important-2：发布模型校验仍不感知 owner 的有效配置

**相关文件：**

- [factory.py](../../../backend/packages/harness/deerflow/publishing/factory.py)
- [effective_config.py](../../../backend/packages/harness/deerflow/config/effective_config.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)

**问题说明：**

`build_publish_service()` 仍在服务构建时调用 `_resolve_available_model_names()`，把基础 `get_app_config().models` 固化为静态 `set[str]`。发布请求只把该静态集合传给校验器，没有根据 `owner_user_id` 调用 `build_effective_app_config()` 或用户模型服务。

代码注释称“用户模型由发布流程单独解析”，但实际发布流程没有对应实现。

**影响：**

- owner 已配置且可用的自定义模型仍会被误报 `MODEL_NOT_AVAILABLE`；
- 模型热更新或用户模型启停不能及时反映到发布校验；
- 第二轮 Important-3 中的模型子项仍未修复。

**建议修复：**

- 将 `model_index` 改为 owner-aware 异步 resolver；
- 发布时按 `owner_user_id` 读取 effective config，并传入当次请求的模型集合；
- 增加真实用户自定义模型发布成功、跨 owner 不可见、禁用后拒绝的测试。

---

### 4.3 Important-3：Connector 有效性仍不是严格的 active 语义

**相关文件：**

- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py)
- [connectors/service.py](../../../backend/packages/harness/deerflow/connectors/service.py)

**问题说明：**

`ConnectorServiceRepo.get_instance()` 仅拒绝 `disabled/deleted/inactive` 三个字符串，`pending`、`error`、空状态或未来新增的非 active 状态都会通过。真实 Connector 运行路径采用的是 `status != 'active'` 即拒绝，并额外检查 Connector type 是否仍在平台 enabled types 中。

当前发布适配器也没有验证实例所属 Connector type 是否仍被平台启用。

**影响：**

- 不健康、未激活或类型已被平台关闭的 Connector 仍可能进入 draft/Release grant；
- 发布校验通过，但运行时立即因 Connector 不可用失败。

**建议修复：**

- 使用白名单：仅 `status == 'active'` 返回实例；
- 同时检查 Connector type 的平台启用状态；
- 增加 `pending/error/unknown` 与“实例 active 但 type disabled”的测试。

---

### 4.4 Important-4：发布仍未达到完整的单数据库事务

**相关文件：**

- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [skill_revision/sql.py](../../../backend/packages/harness/deerflow/persistence/skill_revision/sql.py)
- [agent_release/sql.py](../../../backend/packages/harness/deerflow/persistence/agent_release/sql.py)

**问题说明：**

Release row、Release 子表与 `current_release_id` 已由 `create_and_point()` 原子提交，这一部分已修复。但每个 Skill revision 仍通过 `SkillRevisionRepository.get_or_create()` 的独立 session 提前 commit；随后才开启另一个事务创建 Release。

因此，M1 规格 §5.3 所述的完整 publish unit-of-work 尚未形成。Release 事务最终失败时，本次发布新建的 Skill revisions 会保留为无 Release 引用的孤儿记录。

同时，`PublishService.publish()` 捕获所有 `IntegrityError` 并一律当作 release-number race 重试；FK、子表唯一键或其他完整性错误也会被误报为 `RELEASE_RACE`。

**影响：**

- 发布失败后可能残留无引用 revision；
- 非 release_no 冲突会被错误重试并掩盖真实故障；
- 当前测试只验证正常发布，没有模拟 pointer/Release 事务失败与并发发布乱序。

**建议修复：**

- 让 Skill revision upsert 接受共享 session，把 revision、Release、Release 子表与指针切换纳入同一 DB unit-of-work；
- 仅识别并重试 `(agent_id, release_no)` 对应唯一约束冲突；
- 增加事务中途失败、无孤儿 revision/Release、并发 publish 最终指针指向最大 release_no 的测试。

---

### 4.5 Important-5：对话式镜像仍会在部分场景与文件系统分叉

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)

**问题说明：**

- `setup_agent` 遇到 duplicate slug 后会继续同步 soul，但没有调用 `update_agent_meta()` 更新本次写入文件系统的新 description/display_name；
- skills 仍作为完整列表一次提交。只要一个 legacy skill 不可解析，整个 skills 更新被拒绝，列表中的有效 skills 也不会同步；
- 镜像异常被吞掉后，工具仍向用户报告创建/更新成功，没有 unresolved skills 结果。

**影响：**

- 重复 setup 后，文件系统 description 与控制平面 identity 不一致；
- “有效 Skill + 无效 Skill”的混合列表会导致草稿保留旧 skills；
- 第二轮 Important-5 仅部分关闭。

**建议修复：**

- duplicate slug 路径同步 identity metadata；
- 逐项解析 skills，写入可选子集并记录 unresolved 项；
- 增加断言最终数据库状态的 setup/update 镜像测试。

---

## 5. Minor 问题

### 5.1 新增 CAS “并发测试”实际为串行 stale-revision 测试

`test_concurrent_draft_update_only_one_wins` 与 `test_concurrent_draft_bundle_only_one_wins` 先 await winner，再 await loser，没有构造两个 session 同时持有 revision=N 的竞争窗口。SQL CAS 的实现方向正确，但测试没有锁定真实并发行为。

建议使用两个 repository/session、barrier 与 `asyncio.gather()` 构造真正竞争。

### 5.2 导入 adapter 测试没有测试生产 `_OwnerAwareImportIndex`

`test_import_factory_index_has_get()` 实际构造的是模块级 `_OwnerAwareSkillsIndex`，并未构造 `build_import_service()` 内部的 `_OwnerAwareImportIndex`。当前生产实现已经补了 `get()`，但这条测试无法防止导入适配器回归。

建议把 import index 提升为可直接测试的模块级类，或通过 `build_import_service()` 验证私有 Skill 导入后的 visibility。

### 5.3 `update_bundle()` 留有不可达重复代码

`published_agent/sql.py` 在第一次 `return _draft_to_dict(...)` 后重复加载 skills/grants 并再次 return，后半段永远不可执行。

建议删除不可达代码。

### 5.4 修复 diff 混入 brainstorm 运行状态文件

本轮提交删除 `.superpowers/.../server-info` 并新增 `server-stopped`，与 M1 功能修复无关，属于运行态噪声。

建议在合并前从功能提交中移除这两项变更。

---

## 6. 第二轮问题关闭情况

| 第二轮问题 | 第三轮状态 | 说明 |
|---|---|---|
| Critical-1：迁移列宽与升级安全 | **未关闭** | 列宽本身已扩展，但新 revision 超长、nullability 漂移、旧库重复数据升级失败 |
| Critical-2：数据库级 CAS | **代码已关闭，测试待加强** | 条件 UPDATE + rowcount 正确；所谓并发测试仍是串行 |
| Important-1：发布单事务 | **部分关闭** | Release + pointer 已同事务；Skill revision 仍提前独立提交 |
| Important-2：public Skill revision 去重 | **部分关闭** | 新库约束正确；含历史重复数据的旧库无法升级 |
| Important-3：生产可用性校验 | **部分关闭** | disabled Skill 已修复；owner 模型与 Connector 严格有效性未修复 |
| Important-4：导入适配器 `get()` | **已关闭** | 生产实现已补齐；测试目标需修正 |
| Important-5：对话式镜像 | **部分关闭** | description/soul 分步有改善；duplicate identity 与混合 skills 仍分叉 |

---

## 7. 验证记录

### 7.1 M1 与回归测试

```text
157 passed, 1 skipped, 2 warnings in 21.61s
```

跳过项为本地 PostgreSQL 不可用时的迁移测试；该 skip 正好没有覆盖本轮最关键的 PostgreSQL version stamp 问题。

### 7.2 Ruff

对本轮变更的 Python 文件运行 Ruff：

```text
All checks passed!
```

### 7.3 Diff 检查

```text
git diff --check f0e4c3d3..b8a4fcfd
```

结果：通过，无 whitespace error。

### 7.4 迁移专项诊断

```text
revision_length=36
agent_releases.agent_id_notnull=0 null_rows_inserted=1
version=2026_07_12_agent_releases owner_scope_present=False
```

第三行来自含重复 public revision 的旧库升级复现，证明迁移未到达新 head。

### 7.5 独立复核

独立 reviewer 运行重点测试结果：

```text
67 passed, 1 skipped
```

独立复核同样判定 **Ready to merge：No**，并确认 revision ID、旧库重复数据、owner-aware model、Connector 状态、发布事务与镜像缺口。

---

## 8. 建议修复顺序

1. 缩短 Alembic revision ID，并修复 widen 迁移的 nullability。
2. 为旧库重复 public revision 实现 canonical 合并与 Release 引用重写。
3. 在真实 PostgreSQL 上跑完整升级链，并把该测试纳入必跑 CI。
4. 将发布模型校验改为 owner-aware effective config。
5. 将 Connector 校验收紧为 active + type enabled。
6. 收敛完整 publish unit-of-work 与精确 IntegrityError 重试。
7. 补齐 duplicate setup / 部分无效 skills 的镜像语义与真实并发测试。

---

## 9. 最终判定

**Ready to merge：No。**

本轮修复已经关闭了 CAS、导入 visibility 和 Release+pointer 原子切换等关键问题，但 PostgreSQL 迁移仍存在直接阻断，旧数据库升级也不安全。完成两个 Critical 与上述 Important 后，应进行第四轮复审；在此之前不建议进入 M2 或合并主分支。
