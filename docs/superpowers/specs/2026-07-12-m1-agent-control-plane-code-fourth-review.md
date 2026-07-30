# 多租户 Agent 发布平台 - M1 第四轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-12

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第三轮代码复审：[2026-07-12-m1-agent-control-plane-code-third-review.md](./2026-07-12-m1-agent-control-plane-code-third-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第三轮复审头：`b8a4fcfd4be747272a1bb8542d1ddaa70d763fc5`
- 第四轮复审头：`af2228252700c2ccc589283eda3aaf2a43bab5fd`
- 本轮修复提交：
  - `05c1ffa4 fix(m1): short revision id + safe duplicate-revision upgrade + nullability`
  - `6aa4aa8f fix(m1): owner-aware models, strict connector, precise retry, tool mirror, cleanup`
  - `af222825 docs(m1): record third-round review fixes in impl spec`
- 本轮重点：逐项验证第三轮 2 个 Critical、5 个 Important 与 4 个 Minor 是否真实关闭，并复核从上一轮已应用数据库升级到当前 head 的兼容性

---

## 1. 复审结论

本轮修复关闭了第三轮中的多项核心问题：新 Alembic revision 已缩短到 26 字符；旧 schema 中重复 public Skill revisions 的 canonical 合并、Release 引用重写与去重已经实现；nullability 不再一律放宽；owner-aware 模型解析已接入发布路径；Connector 状态改为严格 active；非 release-number `IntegrityError` 不再被误重试；duplicate setup 的 identity metadata 也已开始同步。

但当前版本仍未达到可合并标准。第四轮复审发现 **1 个 Critical、3 个 Important 与 5 个 Minor** 问题：删除并改名已经存在的 Alembic revision 会让应用过上一版迁移的 SQLite 数据库无法继续升级；完整 publish unit-of-work 与混合有效/无效 Skills 镜像仍未完成；Connector 校验仍存在平台整体关闭、未知 type 和配置异常时的 fail-open 路径。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **Alembic revision 长度已修复**：`2026_07_12_widen_agent_ids` 长度为 26，满足默认 `VARCHAR(32)`。
- **旧 public revision 数据合并逻辑已落地**：迁移会选择 canonical revision，处理同一 Release 的 PK 冲突，重写引用并删除重复行；SQLite 回归测试通过。
- **新迁移不再统一设置 `nullable=True`**：PostgreSQL 路径按列记录目标可空性；SQLite 不再通过错误 batch rebuild 放宽列约束。
- **owner-aware 模型解析已接线**：发布时按 owner 调用 `build_effective_app_config()` 获取有效模型集合。
- **Connector 实例状态已收紧**：仅 `status == 'active'` 的实例继续进入发布校验。
- **release-number 冲突重试已收窄**：明显无关的 `IntegrityError` 会直接抛出。
- **duplicate setup identity 同步已补充**：已有 agent 会调用 `update_agent_meta()` 更新 display name/description。
- **不可达重复代码已删除**：`update_bundle()` 的重复 return 已清理。

---

## 3. Critical 问题

### 3.1 Critical-1：删除/改名已应用的 Alembic revision，导致上一版 SQLite 数据库迁移图断裂

**相关文件：**

- [2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py)
- [engine.py](../../../backend/packages/harness/deerflow/persistence/engine.py)

**问题说明：**

上一轮提交中存在 revision：

```text
2026_07_12_widen_published_agent_ids
```

该 revision 虽然在 PostgreSQL 上因长度问题无法安全写入 `VARCHAR(32)`，但 SQLite 不执行 VARCHAR 长度限制，因此上一版 Gateway 可以成功应用并把它写入 `alembic_version`。

本轮直接删除旧迁移文件，并新增：

```python
revision = "2026_07_12_widen_agent_ids"
down_revision = "2026_07_12_agent_releases"
```

当前 migration graph 已不认识旧 revision。对 `alembic_version='2026_07_12_widen_published_agent_ids'` 的 SQLite 数据库执行自动升级，专项复现结果为：

```text
version_after_upgrade=2026_07_12_widen_published_agent_ids
```

数据库仍停留在旧值，没有到达当前 head。`_run_pending_alembic_revisions()` 捕获迁移异常后继续启动，使问题不会在启动阶段明确阻断，而可能在后续 ORM 访问时才暴露。

**影响：**

- 从第三轮代码版本升级的 SQLite 开发库/单机部署库无法继续迁移；
- 后续新增迁移也无法越过未知 revision；
- 应用可能在迁移失败后继续运行，形成 ORM 与 schema 漂移。

**建议修复：**

- 不要删除已经可能被应用的 revision；
- 提供受测试支持的 revision 兼容/重映射路径，使旧 SQLite version stamp 能升级到当前短 revision；
- 增加从 `2026_07_12_widen_published_agent_ids` 起步升级到 current head 的测试；
- 自动迁移失败时至少应让生产启动显式失败，避免静默运行在旧 schema。

---

## 4. Important 问题

### 4.1 Important-1：完整 publish unit-of-work 仍未实现

**相关文件：**

- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [skill_revision/sql.py](../../../backend/packages/harness/deerflow/persistence/skill_revision/sql.py)
- [agent_release/sql.py](../../../backend/packages/harness/deerflow/persistence/agent_release/sql.py)

**问题说明：**

本轮只修复了 `IntegrityError` 的精确重试，没有收敛第三轮要求的共享事务。发布流程仍然是：

1. `SkillRevisionRepository.get_or_create()` 使用自己的 session 并立即 commit；
2. `PublishService` 随后调用 `AgentReleaseRepository.create_and_point()`，在另一个 session/事务创建 Release、子表并切换指针。

专项注入 Release 创建失败后，数据库结果为：

```text
orphan_skill_revisions_after_failed_publish=1
```

这证明 Release 事务失败时仍会留下本次发布创建、但没有任何 Release 引用的 Skill revision。

**影响：**

- 不满足开发计划与实现规格 §5.3 的完整单数据库事务；
- 发布失败会积累无引用 revision；
- 并发重试无法覆盖整个 publish unit-of-work。

**建议修复：**

- 让 Skill revision upsert、Release、Release 子表、状态与指针切换接受同一 `AsyncSession`；
- 以整个事务为单位重试 release-number 唯一冲突；
- 增加事务中途失败后无孤儿 revision/Release 的集成测试。

---

### 4.2 Important-2：Connector 校验仍存在 fail-open 路径

**相关文件：**

- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py)
- [connectors_config.py](../../../backend/packages/harness/deerflow/config/connectors_config.py)
- [connectors/service.py](../../../backend/packages/harness/deerflow/connectors/service.py)

**问题说明：**

当前适配器已经检查 `status == 'active'` 与 `enabled_types`，但仍有三类放行路径：

- 平台 `connectors.enabled=False` 时，active 实例仍会返回；
- `enabled_types` 为空时，代码将所有 type 视为可用，没有通过 Connector registry 验证 type 是否真实存在；
- 读取 app config 发生异常时直接 `pass`，按可用处理。

本地专项验证：

```text
connector_returned_when_platform_disabled=True
```

真实 Connector 运行路径还会通过 `_ensure_type_enabled()` 调用 registry 校验 type；当前发布适配器没有复用该权威语义。

**影响：**

- 平台已整体关闭 Connector 时仍可能发布带 Connector grant 的 Agent；
- active 但 type 已移除/未知的实例可通过发布，运行时才失败；
- 配置故障被静默转换为放行。

**建议修复：**

- 检查 `connectors.enabled`；
- 复用 ConnectorService 的权威 type 校验，或暴露 `is_type_enabled()`；
- 配置/registry 检查异常时 fail closed；
- 增加 platform disabled、unknown type、type disabled 与配置异常测试。

---

### 4.3 Important-3：混合有效/无效 Skills 的对话式镜像仍会分叉

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)

**问题说明：**

duplicate setup 的 identity metadata 已修复，但 setup/update 仍将完整 skills 列表一次提交给 `update_draft_bundle()`。只要其中一个 legacy Skill 不可选择，整个 skills 更新失败，有效 Skills 也不会写入 draft；文件系统则已经保存完整的新列表。

此外，`setup_agent` 使用 `description or None` 更新 duplicate identity。空 description 会被转换成 `None`（仓储语义为“不更新”），因此无法清除文件系统中已经删除的旧 description。

异常继续被吞掉，工具返回成功结果，也没有 unresolved skills 报告。

**影响：**

- 文件系统 Agent 与控制平面 draft/identity 继续分叉；
- 用户无法从工具结果得知哪些 Skills 没有同步；
- 第三轮 Important-5 仍只部分关闭。

**建议修复：**

- 逐项解析 Skills，写入可选子集并返回/记录 unresolved 项；
- duplicate setup 允许显式清空 description；
- 增加混合 Skills、空 description、duplicate setup 与 update 的最终 DB 状态测试。

---

## 5. Minor 问题

### 5.1 SQLite 旧库的多数 ID 列仍保留 `VARCHAR(32)` 声明

迁移在 SQLite 上完全跳过 `_WIDEN`，仅手工重建 `skill_revisions`。SQLite 运行时不限制 VARCHAR 长度，所以功能不受阻断，但其余表的 schema introspection 仍与 ORM `String(64)` 不一致，后续 autogenerate 会持续产生漂移。

### 5.2 CAS “并发测试”仍是串行 stale-revision 测试

`test_concurrent_draft_update_only_one_wins` 与 bundle 用例仍先 await winner、再 await loser，没有构造两个同时活跃的事务。实现的单 SQL CAS 方向正确，但测试没有锁定真实竞争窗口。

### 5.3 导入适配器测试仍未覆盖生产 `_OwnerAwareImportIndex`

`test_import_factory_index_has_get()` 仍构造 `_OwnerAwareSkillsIndex`，没有走 `build_import_service()`，也没有验证私有 Skill 导入后写入 `source='private'`。实现已补 `get()`，但测试目标仍未对齐生产路径。

### 5.4 owner-aware 模型与 Connector type 校验缺少生产路径回归测试

本轮没有新增 owner 自定义模型发布测试，也没有 active instance + disabled/unknown type 测试。`test_publishing_adapters.py` 只覆盖 status，不覆盖 type 与平台总开关。

### 5.5 分支仍包含运行态与无关格式化噪声

累计分支 diff 仍包含 `.last-port`、`.last-token`、`server.pid`、`server-stopped` 等 brainstorm 运行状态；本轮还格式化了 scheduled-task 与 connector repository 的无关代码。建议合并前清理运行态文件，并避免在修复提交中混入无关变更。

---

## 6. 第三轮问题关闭情况

| 第三轮问题 | 第四轮状态 | 说明 |
|---|---|---|
| Critical-1：revision ID 超长 | **原问题已关闭，但引入新 Critical** | 短 ID 正确；删除旧 revision 导致上一版 SQLite 数据库迁移图断裂 |
| Critical-2：旧库重复 public revisions | **已关闭** | canonical 合并、引用重写与去重逻辑已实现并通过 SQLite 测试 |
| Important-1：迁移 nullability | **已关闭** | 不再一律 `nullable=True`；PostgreSQL 实际执行仍需 CI 证据 |
| Important-2：owner-aware 模型 | **代码已关闭，测试待补** | resolver 已接线；缺少生产发布路径回归测试 |
| Important-3：Connector 有效性 | **部分关闭** | strict active 已修复；平台总开关、registry 与异常 fail-open 未修复 |
| Important-4：完整 publish UOW / 精确重试 | **部分关闭** | 精确重试已修复；Skill revision 仍提前独立 commit |
| Important-5：对话式镜像 | **部分关闭** | duplicate identity 已改善；混合 Skills 与清空 description 仍分叉 |
| Minor：真实并发/导入 adapter 测试 | **未关闭** | 对应测试文件没有完成生产路径与真实并发改造 |
| Minor：不可达代码 | **已关闭** | 重复 return 已删除 |
| Minor：运行态文件 | **未关闭** | 累计分支仍包含端口、token、pid、server 状态文件 |

---

## 7. 验证记录

### 7.1 M1、迁移与相关回归测试

```text
175 passed, 1 skipped, 2 warnings in 34.45s
```

跳过项仍是本地 PostgreSQL 不可用时的迁移测试，因此 PostgreSQL 实际 ALTER 路径尚无本轮执行证据。

### 7.2 独立 reviewer

```text
81 passed, 1 skipped
```

独立 reviewer 同样判定 **Ready to merge：No**，确认迁移图断裂、完整 publish UOW、Connector fail-open 与镜像问题。

### 7.3 Ruff 与 Diff

```text
All checks passed!
git diff --check：通过
alembic heads：2026_07_12_widen_agent_ids (head)
```

`alembic heads` 只能证明当前代码图有单一 head，不能证明数据库中旧 revision stamp 可被当前图识别。

### 7.4 专项复现

```text
revision_length=26
version_after_upgrade=2026_07_12_widen_published_agent_ids
orphan_skill_revisions_after_failed_publish=1
connector_returned_when_platform_disabled=True
```

---

## 8. 建议修复顺序

1. 先恢复/实现旧 revision 的兼容升级路径，并增加上一版 SQLite head → 当前 head 测试。
2. 将 Skill revision、Release、子表与指针收敛到同一 DB unit-of-work。
3. 将 Connector 校验改为 platform enabled + active + registry type enabled，并对异常 fail closed。
4. 对 setup/update 逐项解析 Skills，写入有效子集并报告 unresolved；补 description 清空语义。
5. 在真实 PostgreSQL CI 上执行完整迁移链。
6. 补真实并发 CAS、生产 import adapter、owner model 与 connector type 测试并清理运行态文件。

---

## 9. 最终判定

**Ready to merge：No。**

本轮已经关闭旧 public revision 去重、短 revision、nullability、owner-aware model 等关键问题，但迁移 revision 兼容性形成了新的升级阻断；完整 publish UOW 与对话式镜像也仍不满足规格。修复上述 Critical / Important 后建议进行第五轮复审。
