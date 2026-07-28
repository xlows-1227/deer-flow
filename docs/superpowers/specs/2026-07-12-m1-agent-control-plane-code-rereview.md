# 多租户 Agent 发布平台 - M1 第二轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-12

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第一轮代码评审：[2026-07-12-m1-agent-control-plane-code-review.md](./2026-07-12-m1-agent-control-plane-code-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第一轮评审头：`262dc71aab38492e7a1e667f05a012bd68f48907`
- 第二轮复审头：`f0e4c3d367bcffdd1ae69726fb13c8d7d1a9c739`
- 本轮重点：验证第一轮 Critical / Important 修复是否真实满足 M1 规格、开发计划和生产可合并标准
- 修复提交：
  - `c06641f5 fix(m1): PostgreSQL-safe ID widths + wire services into Gateway lifespan`
  - `4131c521 fix(m1): atomic draft PATCH — main + sub-tables under one revision check`
  - `178403bf fix(m1): authoritative skill metadata, publish races, tool mirror, status codes`
  - `f0e4c3d3 docs(m1): update impl spec with code-review fixes`

---

## 1. 复审结论

第一轮指出的部分问题已经有实质性改善：

- Gateway lifespan 已经挂载 draft / publish / import services；
- 过期 revision 的串行 PATCH 不再先写子表后返回 409；
- 发布时 Skill 的 source / visibility 已改为从平台索引推导；
- 缺失 agent 的发布与 release history 的 404 语义已经收敛。

但当前修复仍未达到可合并标准。第二轮复审仍发现 **2 个 Critical** 与 **5 个 Important** 问题，其中 Critical 问题分别会影响 PostgreSQL 迁移可用性和 draft 乐观并发的真实原子性。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Gateway 接线已打通**：真实 app lifespan 会构建并挂载控制平面服务，且工厂方法已补齐主要依赖。
- **串行 stale revision PATCH 已修复**：过期 revision 请求返回 409 时，已覆盖第一轮复现的“子表先被替换”场景。
- **发布侧 Skill 元数据不再信任客户端 source**：发布服务会基于 `SkillsIndex.get()` 的权威元数据写入 release / skill revision。
- **HTTP 404 语义修复**：缺失 agent 的发布请求与 release history 查询已按资源不存在返回 404。

---

## 3. 仍需修复的问题

### 3.1 Critical-1：PostgreSQL ID 迁移仍不完整，且不具备升级安全性

**相关文件：**

- [2026_07_12_agent_releases.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_agent_releases.py#L35)
- [skill_revision/model.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/skill_revision/model.py#L36)

**问题说明：**

运行时 ORM 的 `SkillRevisionRow.id` 已扩为 `String(64)`，但 Alembic 迁移里 `skill_revisions.id` 仍是 `String(32)`。实际 ID 为 `skr_` + 32 位 hex，长度 36，PostgreSQL 会拒绝写入。

此外，修复是直接编辑既有迁移文件，而不是新增纠偏迁移。已经应用过第一版迁移的数据库仍会保留旧列宽，即使代码更新也不会自动变宽。

**影响：**

- 新库从迁移初始化时仍可能建出错误 schema；
- 已部署或已本地应用旧迁移的数据库无法被升级修复；
- 与 M1 “SQLite / PostgreSQL 均可执行”的验收目标不一致。

**建议修复：**

- 修正新建迁移中的 `skill_revisions.id` 列宽；
- 新增一个 Alembic revision，显式 widen 已存在库中的所有受影响 ID / FK 列；
- 增加 migrated-schema 写入测试，验证通过迁移链建库后可以插入 `skr_` 长 ID。

---

### 3.2 Critical-2：Draft 乐观并发仍不是数据库级原子 CAS

**相关文件：**

- [published_agent/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/published_agent/sql.py#L292)
- [published_agent/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/published_agent/sql.py#L328)

**问题说明：**

`update_bundle()` 当前仍是：

1. 先加载 draft 行；
2. 在 Python 中判断 `draft.revision != revision`；
3. 修改主表与子表；
4. `commit()`。

这修复了“串行 stale revision”场景，但没有修复两个事务同时读取同一 revision 的并发场景。两个 session 都可以读到 revision=N，通过 Python 检查后分别写入并提交，最终都把 revision 写成 N+1，造成 lost update。

**影响：**

- M1 规格中“revision 防止并发覆盖写”的语义仍不成立；
- 用户在 Studio 或 API 并发编辑时仍可能悄悄覆盖对方修改；
- 现有测试只覆盖顺序过期请求，不覆盖真正同时读取同一 revision 的竞争窗口。

**建议修复：**

- 使用条件更新：`UPDATE agent_drafts SET ... WHERE agent_id = ? AND owner_user_id = ? AND revision = ?`；
- 通过 rowcount 判断是否抢到 revision；
- 只有抢到后才在同一事务中替换 skills / connector grants；
- 增加双 session 并发测试，证明同一 revision 只能有一个提交成功。

---

### 3.3 Important-1：发布仍不是单一事务，可能产生孤儿 Release 或指针回退

**相关文件：**

- [publish_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/publish_service.py#L194)
- [publish_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/publish_service.py#L218)

**问题说明：**

`PublishService.publish()` 现在先通过 `AgentReleaseRepository.create()` 创建 release 并提交，再调用 `PublishedAgentRepository.set_current_release()` 更新当前指针。这两个动作不在同一个事务中。

并发 publish 下还存在顺序回退风险：较早分配的 release 可能较晚完成指针更新，从而把 `current_release_id` 指回更旧的 release。

**影响：**

- 指针更新失败会留下不可达的孤儿 release；
- 并发发布可能让当前指针不是最新 release；
- “发布即不可变快照 + 原子切换线上指针”的规格语义没有完全落地。

**建议修复：**

- 将 skill revision upsert、release row、release 子表、current pointer 切换放入同一 unit-of-work / session；
- 只针对 release_no 唯一键冲突重试整个事务；
- 增加发布过程中指针失败与并发发布乱序的回归测试。

---

### 3.4 Important-2：公开 Skill revision 的并发去重仍不受唯一约束保护

**相关文件：**

- [skill_revision/model.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/skill_revision/model.py#L46)
- [skill_revision/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/skill_revision/sql.py#L69)

**问题说明：**

`skill_revisions` 的唯一约束是 `(skill_name, owner_user_id, content_checksum)`，但公开 skill 的 `owner_user_id` 为 `NULL`。SQLite 与 PostgreSQL 默认都允许唯一约束列中的多个 `NULL`，因此两个公开 skill revision 可以拥有相同 `(skill_name, NULL, content_checksum)`。

当前 `IntegrityError` 重读逻辑对私有 skill 有帮助，但对公开 skill 不会触发，因为数据库不会认为它们冲突。

**本地验证：**

手工插入两个相同公开 skill revision 后，SQLite 返回：

```text
public_duplicate_rows=2
```

**影响：**

- 公开 skill 内容不变时可能产生多个 revision；
- Release 锁定的 skill revision 不再具备内容去重的不变量；
- 并发 publish 下会出现隐蔽的重复历史，而不是稳定复用。

**建议修复：**

- 将 scope 改为非空列，例如 `owner_scope='public'` 或 `owner_user_id_or_public`；
- 或使用兼容 PostgreSQL 的 `NULLS NOT DISTINCT` / coalesce 唯一索引方案，并明确 SQLite 测试策略；
- 增加 public/private 两类并发 get_or_create 测试。

---

### 3.5 Important-3：生产可用性校验仍不准确

**相关文件：**

- [skills_index.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/skills_index.py#L38)
- [skills_index.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/skills_index.py#L56)
- [skills_index.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/skills_index.py#L106)
- [factory.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/factory.py#L47)
- [factory.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/factory.py#L137)

**问题说明：**

当前生产索引仍会把不可用资源视为可发布：

- `StorageSkillsIndex` 使用 `load_skills(enabled_only=False)`，`is_selectable_by()` 不检查 enabled；
- `ConnectorServiceRepo.get_instance()` 直接返回 connector 数据，不检查 active / enabled 状态；
- `build_publish_service()` 使用启动时静态读取的 app config 模型集，无法感知 `EffectiveConfigMiddleware` 合并后的用户自定义模型，也不能感知热更新。

**影响：**

- 被禁用的 skill 仍可能发布成功；
- 已禁用或不可用 connector 仍可能通过校验；
- 用户自定义模型可能被误报 `MODEL_NOT_AVAILABLE`。

**建议修复：**

- Skill 索引只暴露 enabled skill，或在 `is_selectable_by()` 显式检查 enabled；
- Connector adapter 按实例状态过滤，只返回 owner 有效且 active 的实例；
- 发布模型校验改为 owner/request-aware 的有效模型解析器，而不是启动时静态 set。

---

### 3.6 Important-4：生产导入适配器仍会把私有 Skill 标成 public

**相关文件：**

- [factory.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/factory.py#L165)
- [import_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/import_service.py#L158)

**问题说明：**

`AgentImportService.import_agent()` 会在 `skills_index` 存在 `get()` 时读取 visibility；否则默认 public。生产工厂里的 `_OwnerAwareImportIndex` 只实现了 `is_selectable_by()`，没有实现 `get()`，因此实际生产导入路径仍会把 owner 私有 skill 写成 public。

**影响：**

- 第一轮 Important-1 在导入路径上仍未完全修复；
- 私有 skill 的 ownership / visibility 元数据会被污染；
- 后续权限审计和 Release 解析可能基于错误分类。

**建议修复：**

- 复用 `_OwnerAwareSkillsIndex`，或为 `_OwnerAwareImportIndex` 实现 `get()`；
- 增加真实 factory adapter 下的私有 skill 导入测试，而不是只测 fake index。

---

### 3.7 Important-5：对话式 draft 镜像仍不完整

**相关文件：**

- [update_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py#L289)
- [setup_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py#L51)
- [setup_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py#L63)

**问题说明：**

当前对话式工具镜像仍有三类缺口：

- `update_agent` 调用 `_persist_draft_update()` 时没有传 `description`；
- `setup_agent` 遇到重复 slug 会直接 return，导致重跑不会同步已有 draft；
- `setup_agent` 把 `soul_markdown` 与 `skills` 放在同一个 `update_draft_bundle()` 里，如果 legacy skill 不可解析，整个 bundle 被拒绝，soul 也不会落到 draft。代码注释说“identity + soul are still mirrored”，但实际没有保证。

**影响：**

- Studio 侧 draft 仍可能与文件系统 Agent 状态不一致；
- 用户重跑创建/更新工具时可能以为已同步，实际 draft 没变；
- 一个坏 skill 会阻断本应可以安全落库的 soul / identity 字段。

**建议修复：**

- 为 identity metadata 增加可更新路径，至少覆盖 description；
- duplicate slug 场景改为读取已有 agent 并同步草稿；
- 对不可解析 skill 做跳过和报告，不要阻断 soul / description / model 等有效字段写入；
- 测试不要只断言工具返回成功，还要断言 draft 最终状态。

---

## 4. 验证记录

### 4.1 M1 与回归测试

执行：

```powershell
cd backend
$env:PYTHONPATH='.'
uv run pytest tests/test_published_agent_models.py tests/test_published_agent_repo.py tests/test_agent_release_models.py tests/test_agent_release_repo.py tests/test_skill_revision_repo.py tests/test_publishing_content_store.py tests/test_publish_instructions.py tests/test_draft_service.py tests/test_published_agents_router.py tests/test_publish_validation.py tests/test_publish_service.py tests/test_agent_import.py tests/test_published_agents_app_wiring.py tests/test_setup_agent_tool.py tests/test_update_agent_tool.py tests/test_harness_boundary.py tests/test_user_model_capabilities_migration.py -q
```

结果：

- `145 passed, 1 skipped`

### 4.2 Ruff

对本分支变更的 Python 文件执行 Ruff，结果：

- `All checks passed!`

### 4.3 公开 Skill revision NULL 唯一约束验证

在 SQLite 中直接构造相同 `(skill_name, owner_user_id=NULL, content_checksum)` 的两条公开 skill revision，结果：

```text
public_duplicate_rows=2
```

说明当前唯一约束不能保护公开 skill revision 去重。

### 4.4 独立复核

第二 reviewer 独立执行重点测试，结果：

- `103 passed, 1 skipped`

独立复核结论与本轮主要发现一致。

---

## 5. 建议修复顺序

1. 先修复迁移链与 PostgreSQL schema 升级安全性；
2. 将 draft update bundle 改为数据库级 CAS，并补双 session 并发测试；
3. 重构 publish 为单一事务，覆盖 orphan release 与指针回退；
4. 修复 public skill revision 的 NULL 唯一约束问题；
5. 收紧生产可用性校验：enabled skill、active connector、owner-aware model；
6. 修复导入 factory adapter 的 `get()` 缺失；
7. 补齐 setup/update 工具镜像的 description、duplicate slug、unresolved skill 行为。

---

## 6. 最终复审结论

**Ready to merge：No**

本轮修复已经向正确方向推进，但仍有两个阻断项没有真正闭环：PostgreSQL 迁移仍可能建出错误 schema，draft 乐观并发仍不是数据库级原子写。建议完成上述 Critical 与 Important 修复后，再进行第三轮复审。
