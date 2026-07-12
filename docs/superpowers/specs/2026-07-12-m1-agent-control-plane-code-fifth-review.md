# 多租户 Agent 发布平台 - M1 第五轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-12

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第四轮代码复审：[2026-07-12-m1-agent-control-plane-code-fourth-review.md](./2026-07-12-m1-agent-control-plane-code-fourth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第四轮复审头：`af2228252700c2ccc589283eda3aaf2a43bab5fd`
- 第五轮复审头：`316fcfc9097db44e27ac50c1590b187af825f5c2`
- 本轮修复提交：
  - `7ab36606 fix(m1): restore old revision as no-op stub for SQLite upgrade compat`
  - `b13804b6 fix(m1): single-transaction publish, connector fail-closed, skill subset mirror`
  - `316fcfc9 docs(m1): record fourth-round review fixes in impl spec`
- 本轮重点：逐项验证第四轮 1 个 Critical、3 个 Important 与 5 个 Minor，并检查修复是否引入迁移链、并发去重和清空语义回归

---

## 1. 复审结论

本轮修复继续取得实质进展：上一版已经写入 36 字符 revision 的 SQLite 数据库现在可以升级到短 ID head；生产发布路径已用共享 `AsyncSession` 将 Skill revision、Release、Release 子表和线上指针纳入同一事务；Release 失败时专项验证不再产生孤儿 Skill revision；Connector 对平台总开关、非 active、type 白名单和配置异常已改为 fail closed；混合 Skills 中的有效子集也能写入草稿；`.superpowers` 运行态文件已经清理。

但当前版本仍未达到可合并标准。第五轮复审发现 **1 个 Critical、3 个 Important 与 5 个 Minor** 问题：36 字符兼容 stub 被放在 PostgreSQL 主迁移链上，使 PostgreSQL 仍无法到达短 revision；共享事务版 Skill revision upsert 引入并发唯一键回归；Connector type 仍未经过 registry 权威校验；`skills=[]`、全部无效 Skills 与空 description 的镜像语义仍不正确。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **旧 SQLite revision stamp 兼容已修复**：从 `2026_07_12_widen_published_agent_ids` 可以升级到当前短 ID head，SQLite 回归测试通过。
- **正常 publish UOW 已收敛**：真实仓储路径使用同一 session/transaction 完成 Skill revision、Release、子表和指针切换。
- **普通失败回滚不再产生孤儿 revision**：专项注入 Release 创建失败后结果为 `orphan_skill_revisions_after_failed_publish=0`。
- **release-number 冲突按整个 UOW 重试**：Release 唯一键冲突会回滚整个事务后重新执行。
- **Connector 部分 fail-closed 已修复**：平台关闭、状态非 active、type 不在非空白名单、配置读取异常均会拒绝。
- **混合 Skills 的有效子集可落库**：一个无效 Skill 不再阻断列表中的有效 Skills。
- **运行态文件已清理**：累计分支不再跟踪 `.superpowers` 端口、token、pid 与 server 状态文件。

---

## 3. Critical 问题

### 3.1 Critical-1：36 字符兼容 stub 位于 PostgreSQL 主迁移链，短 revision 仍不可达

**相关文件：**

- [2026_07_12_widen_published_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_published_agent_ids.py)
- [2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py)
- [test_user_model_capabilities_migration.py](../../../backend/tests/test_user_model_capabilities_migration.py)

**问题说明：**

当前迁移链为：

```text
2026_07_12_agent_releases
  → 2026_07_12_widen_published_agent_ids   # 36 字符 no-op stub
  → 2026_07_12_widen_agent_ids             # 26 字符 current head
```

兼容 stub 的 `upgrade()` 完全 no-op。新 PostgreSQL 数据库从 `agent_releases` 升级时，Alembic 必须先执行 stub，然后把 36 字符 revision 写入默认 `alembic_version.version_num VARCHAR(32)`。该 stamp 会失败，因而短 ID 子迁移仍不可达。

`alembic history` 已确认 36 字符 revision 位于唯一主链中。本轮 PostgreSQL 集成测试仍因本地 PostgreSQL 不可用而 skip，所以新增 SQLite 兼容测试没有覆盖这一阻断。

**影响：**

- PostgreSQL 无法完成 M1 迁移链；
- Gateway 的 PostgreSQL 启动/升级仍可能停留在 `agent_releases`；
- 第三轮 Critical-1 的 PostgreSQL 根因被重新引入。

**建议修复：**

- 兼容 stub 在 PostgreSQL 上必须先把 `alembic_version.version_num` 扩到至少 64，再允许 Alembic stamp 长 revision；或提供不要求 PostgreSQL stamp 长 ID 的受测试兼容映射方案；
- 使用真实 PostgreSQL 从 `2026_07_12_agent_releases` 执行到 head，并断言最终 version 为短 ID；
- 保留上一版 SQLite stamp → current head 的兼容测试。

---

## 4. Important 问题

### 4.1 Important-1：共享 UOW 引入 Skill revision 并发去重回归

**相关文件：**

- [skill_revision/sql.py](../../../backend/packages/harness/deerflow/persistence/skill_revision/sql.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)

**问题说明：**

`_get_or_create_in_session()` 仍采用“先 SELECT、再 INSERT、最后 flush”的模式。上一版 `get_or_create()` 捕获 `IntegrityError` 后回滚/重读 canonical row 的逻辑已经移除。

两个事务同时为相同 `(skill_name, owner_scope, checksum)` 创建 revision 时，本地双 session 复现结果为：

```text
a: ok
b: IntegrityError: UNIQUE constraint failed: skill_revisions.skill_name,
   skill_revisions.owner_scope, skill_revisions.content_checksum
```

`PublishService` 只重试包含 `release_no` 的 IntegrityError；Skill revision 唯一冲突会直接抛出，尚未进入 Release-number 竞争。

**影响：**

- 同时发布同一 public/private Skill 的请求会有一个失败；
- `SkillRevisionRepository.get_or_create()` 独立调用也失去原有并发恢复能力；
- public Skill revision 的数据库去重约束存在，但服务层并发幂等语义不成立。

**建议修复：**

- 使用数据库原子 upsert，例如 `INSERT ... ON CONFLICT DO NOTHING RETURNING` 后读取 canonical row；或识别 Skill revision 唯一冲突并重试整个 publish UOW；
- 增加真实双 session public/private Skill revision 与并发 publish 测试；
- 保持 Release + Skill revision 的单事务不变量。

---

### 4.2 Important-2：Connector type 仍未经过 registry 权威校验

**相关文件：**

- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py)
- [connectors/service.py](../../../backend/packages/harness/deerflow/connectors/service.py)

**问题说明：**

当前发布适配器只检查 type 非空以及 `enabled_types`。当 `enabled_types=[]`（平台语义为“不限制已注册类型”）时，任何非空字符串都会通过，没有调用 Connector registry 确认 type 是否真实存在。

专项验证：

```text
unknown_type_returned_when_whitelist_empty=True
```

测试 `test_active_connector_is_grantable` 也使用空白名单和不带 registry 的 fake service，实际上固定了这一 fail-open 行为。真实 ConnectorService 的运行路径会调用 registry 获取 type definition。

**影响：**

- 已从 registry 删除或从未注册的 active Connector 可以进入 Release；
- 发布通过后，运行时才因未知 type 失败。

**建议修复：**

- 复用 ConnectorService 的权威 type 校验，例如调用 `get_connector_type()` 或公开 `is_type_enabled()`；
- 未知、禁用或 registry 异常一律 fail closed；
- 增加空白名单 + unknown registry type 测试。

---

### 4.3 Important-3：Skills/description 清空及 unresolved 反馈仍未修复

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)

**问题说明：**

`filter_selectable_skills()` 已能返回有效子集，但两个工具都在 `selectable=[]` 时直接 return：

- 显式 `skills=[]` 本应表示禁用全部 Skills，却不会提交空列表，旧 draft Skills 被保留；
- 输入全部为无效 Skills 时同样保留旧值，而文件系统已保存新列表；
- unresolved Skills 被静默丢弃，没有返回或记录；
- duplicate setup 仍使用 `description or None`，显式空字符串被转换为“不更新”，无法清除旧 description。

专项 fake-service 验证 `skills=[]` 的镜像调用结果：

```text
skills_payloads=['<omitted>']
```

即没有发送 `skills=[]` 更新。

**影响：**

- 文件系统与控制平面继续在“清空 Skills/description”场景分叉；
- 工具契约中 `skills=[] = no skills` 没有落实；
- 用户不知道哪些 Skills 未同步。

**建议修复：**

- 仅以 `skills is None` 表示“不修改”；只要调用方传了列表，即使过滤结果为空也必须提交 `skills=[]`；
- duplicate setup 保留显式空 description 语义；
- 返回或至少结构化记录 unresolved Skills；
- 增加已有 Skills → `skills=[]`、全部无效、混合列表、空 description 的最终数据库状态测试。

---

## 5. Minor 问题

### 5.1 SQLite 旧库多数 ID 类型声明仍为 `VARCHAR(32)`

SQLite 路径仍跳过 `_WIDEN`，只有 `skill_revisions` 被手工重建。运行时不限制 VARCHAR 长度，因此不阻断功能，但 schema introspection 与 ORM `String(64)` 持续漂移。

### 5.2 Draft CAS 测试仍不是真实并发

两个名为 `concurrent_draft_*` 的测试仍先 await winner，再 await loser，没有两个同时活跃的事务。单 SQL CAS 实现方向正确，但回归测试没有覆盖真实竞争窗口。

### 5.3 生产 Import adapter 测试仍未修正，且测试代码被并入错误函数

`test_import_factory_index_has_get` 的函数定义已消失，其 docstring/断言被附在 Connector type 测试末尾；断言对象仍是 `_OwnerAwareSkillsIndex`，不是生产 `_OwnerAwareImportIndex`，也没有验证私有 Skill 最终写为 private。

### 5.4 新 UOW 测试没有断言标题所述的 Skill revision 不变量

`test_failed_publish_leaves_no_orphan_skill_revisions` 只断言 Release 列表为空，没有查询 `skill_revisions`。本轮专项验证确认普通失败回滚确实为 0，但仓库测试无法防止该不变量回归。owner-aware custom model 与并发 Skill upsert 也仍缺少生产路径测试。

### 5.5 迁移说明与实现规格章节编号不一致

短 ID 迁移文件头仍写 `Revises: 2026_07_12_agent_releases`，实际 `down_revision` 已改为长 ID stub。实现规格顺序此前为 §17、§18、再出现重复 §17；本次文档更新已将修复章节调整为 §19，并将第五轮复审置于 §20，但迁移文件头仍应同步修正。

---

## 6. 第四轮问题关闭情况

| 第四轮问题 | 第五轮状态 | 说明 |
|---|---|---|
| Critical-1：旧 SQLite revision 断链 | **SQLite 已关闭，但 PostgreSQL 仍 Critical** | 旧 stamp 可升级；主链必须 stamp 36 字符 stub，PG 仍失败 |
| Important-1：完整 publish UOW | **主路径已关闭，并发回归待修复** | 普通失败无孤儿；Skill revision 唯一冲突不重试 |
| Important-2：Connector fail-open | **部分关闭** | 平台总开关/配置/非空白名单已修复；registry unknown type 未修复 |
| Important-3：混合 Skills 镜像 | **部分关闭** | 有效子集可写；空列表、全部无效、description 清空与 unresolved 反馈未修复 |
| Minor：SQLite schema drift | **未关闭** | 多数旧列仍声明 VARCHAR(32) |
| Minor：真实并发 CAS | **未关闭** | 测试文件未改 |
| Minor：生产 Import adapter 测试 | **未关闭** | 测试目标仍错误，且函数边界损坏 |
| Minor：生产路径测试 | **部分关闭** | Connector fail-closed 有新增测试；PG、owner model、Skill 并发仍缺失 |
| Minor：运行态文件 | **已关闭** | `.superpowers` 运行状态已从累计分支移除并加入 gitignore |

---

## 7. 验证记录

### 7.1 M1、迁移与相关回归测试

```text
179 passed, 1 skipped, 2 warnings in 41.40s
```

跳过项仍是 PostgreSQL 集成迁移测试；当前最关键的长 ID stub 问题没有被 SQLite 测试覆盖。

### 7.2 独立 reviewer

```text
101 passed, 1 skipped
```

独立 reviewer 同样判定 **Ready to merge：No**，确认 PostgreSQL 迁移链、Skill revision 并发、Connector registry 与清空语义问题。

### 7.3 Ruff、Diff 与迁移图

```text
All checks passed!
git diff --check：通过
alembic heads：2026_07_12_widen_agent_ids (head)
```

迁移 history 明确显示 36 字符 stub 是短 ID head 的直接父节点。

### 7.4 专项验证

```text
orphan_skill_revisions_after_failed_publish=0
concurrent_skill_revision_results=[ok, IntegrityError]
unknown_type_returned_when_whitelist_empty=True
skills_payloads=['<omitted>']
```

---

## 8. 建议修复顺序

1. 先修复 PostgreSQL 对长 ID 兼容 stub 的 version stamp，跑真实 PostgreSQL 全迁移链。
2. 恢复 Skill revision 并发幂等，且保持完整 publish UOW。
3. 将 Connector type 校验接到 registry 权威接口。
4. 修复 `skills=[]`、全部无效 Skills、空 description 与 unresolved 反馈语义。
5. 补真实并发 CAS、生产 Import adapter、UOW revision 断言与 owner-aware model 测试。
6. 处理 SQLite schema 声明漂移并同步迁移文档头。

---

## 9. 最终判定

**Ready to merge：No。**

本轮已经完成旧 SQLite stamp 兼容、普通发布单事务、部分 Connector fail-closed 与混合 Skills 子集同步，但 PostgreSQL 迁移链仍被长 revision 阻断，并发 Skill revision 去重出现回归。完成上述 Critical / Important 后建议进行第六轮复审。
