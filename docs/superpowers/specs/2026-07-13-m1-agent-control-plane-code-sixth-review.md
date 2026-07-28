# 多租户 Agent 发布平台 - M1 第六轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第五轮代码复审：[2026-07-12-m1-agent-control-plane-code-fifth-review.md](./2026-07-12-m1-agent-control-plane-code-fifth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第五轮复审头：`316fcfc9097db44e27ac50c1590b187af825f5c2`
- 第六轮复审头：`0d62ec59668ef08e48a31ee7224c3d357efd90f8`
- 本轮修复提交：`0d62ec59 fix(m1): PG version stamp, skill revision concurrency, connector registry, clear semantics`
- 本轮重点：逐项验证第五轮的 1 个 Critical、3 个 Important 和 5 个 Minor，并检查并发、迁移链、清空语义与生产适配器测试是否形成可防回归证据

---

## 1. 复审结论

本轮修复已经关闭第五轮唯一的 Critical，并关闭 3 个 Important 中的 2 个；`skills=[]`、全部无效 Skills、混合 Skills 的有效子集和空 description 清空语义也已经落地。专项并发验证确认，Skill revision 在两个真实 session 同时进入“先查后插”的竞争窗口时，SAVEPOINT 能隔离唯一键冲突，两个调用最终返回同一 canonical revision，外层事务不会被破坏。

当前仍未达到可合并标准。本轮剩余 **0 个 Critical、1 个 Important、6 个 Minor**。阻断项是 setup/update 对不可解析或不可选择的 Skill 仍然静默丢弃：文件系统保留调用方原始列表，数据库草稿只保存有效子集，但工具仍返回通用成功消息，调用方无法知道哪些 Skill 没有生效。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **PostgreSQL 长 revision stamp 路径已修复**：兼容 stub 在 Alembic 写入 36 字符 revision 前先把 `alembic_version.version_num` 扩为 `VARCHAR(64)`；短 ID 子迁移继续完成业务表修正。
- **Skill revision 并发去重已修复**：`session.begin_nested()` 将 INSERT/flush 放入 SAVEPOINT；唯一键冲突只回滚嵌套事务，随后重读 canonical row。
- **Connector type registry 校验已接入**：active、平台总开关、非空白名单、registry unknown/异常均按 fail closed 处理。
- **Skills 清空语义已修复**：调用方显式传入列表时，即使过滤结果为空也提交 `skills=[]`；`None` 才表示不修改。
- **description 清空语义已修复**：duplicate setup 不再使用 `description or None`，显式空字符串可清除旧值。
- **旧 SQLite revision stamp 兼容、普通 publish UOW 回滚与运行态文件清理保持有效**。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：unresolved Skills 仍被静默丢弃，成功消息与最终事实不一致

**相关文件：**

- [setup_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py)
- [update_agent_tool.py](../../../backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py)
- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)

**问题说明：**

`DraftService.filter_selectable_skills()` 只返回有效子集，没有同时返回 unresolved 项。`setup_agent` 和 `update_agent` 将过滤后的列表写入数据库草稿，但没有返回、结构化记录或提示被过滤的 Skill；异步镜像中的异常也被直接吞掉。工具最终仍返回通用成功消息。

例如调用方传入 `skills=["reporting", "missing-skill"]` 时：

- 文件系统 `config.yaml` 保存原始列表；
- 数据库草稿只保存 `reporting`；
- 返回消息没有指出 `missing-skill` 未生效。

全部 Skill 无效时，数据库会被正确清空为 `[]`，但调用方仍无法区分“主动禁用全部”与“全部解析失败”。这使两个事实来源产生可观察分歧，也不满足第五轮提出的“返回或至少结构化记录 unresolved Skills”要求。

**影响：**

- 用户可能在不知情的情况下发布缺少能力的 Agent；
- 文件系统 Agent 与发布草稿的 Skill 配置不一致；
- API/IM 上层无法向用户提供准确的配置结果和修复建议。

**建议修复：**

- 过滤时同时计算 `selectable` 与 `unresolved`；
- 在 ToolMessage 或结构化结果中明确列出 unresolved Skill；
- 镜像写入失败时返回警告或可观测状态，不要继续报告无条件成功；
- 增加混合列表、全部无效、镜像异常三类最终返回值和数据库状态测试。

---

## 5. Minor 问题

### 5.1 SQLite schema 声明仍与 ORM 漂移

[2026_07_12_widen_agent_ids.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_12_widen_agent_ids.py) 在 SQLite 上跳过多数旧 ID/FK 列的宽度变更。SQLite 不强制 `VARCHAR(n)` 长度，因此当前功能可用，但 schema 反射仍显示 `VARCHAR(32)`，与 ORM/新建表的 `String(64)` 不一致。

### 5.2 Skill revision 的“并发”回归测试实际上串行

[test_skill_revision_repo.py](../../../backend/tests/test_skill_revision_repo.py) 中 `test_concurrent_get_or_create_in_session_dedupes` 先让第一个 session 插入并提交，再启动第二个 session。第二次调用只覆盖“读取已存在行”，没有制造唯一键竞争窗口，无法防止本轮 SAVEPOINT 修复回归。

### 5.3 Draft CAS 测试仍不是真实并发

[test_published_agent_repo.py](../../../backend/tests/test_published_agent_repo.py) 中两个 CAS 测试依次执行 winner 和 loser。它们能验证 stale revision 被拒绝，但没有实现注释所述的“两事务同时读取 revision=N”，也没有覆盖真实调度竞争。

### 5.4 生产 Import adapter 测试函数边界与测试对象仍错误

[test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py) 的 Connector whitelist 测试末尾仍混入 Import adapter 的 docstring/断言；断言对象是 `_OwnerAwareSkillsIndex`，不是生产 `_OwnerAwareImportIndex`，也没有验证 private Skill 的 visibility/owner 传播。

### 5.5 发布回滚测试断言过宽，PostgreSQL 迁移门禁仍可跳过

[test_publish_service.py](../../../backend/tests/test_publish_service.py) 使用 `len(revs_after) <= 1`；即失败发布残留一个孤儿 revision，测试仍会通过。该不变量应严格断言 `== 0`。此外 PostgreSQL 迁移集成测试在本地无 PostgreSQL 时仍被 skip，建议在 CI 中提供强制运行的 PostgreSQL Review Gate。

### 5.6 实现规格章节编号重复

[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md) 已有第 19、20 节后，第五轮修复又使用第 18 节。本次同步将其修正为第 21 节，并把本轮复审追加为第 22 节。

---

## 6. 第五轮问题关闭状态

| 第五轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Critical-1：PostgreSQL 长 ID stamp | **已关闭（实现）** | stub 先扩宽 version_num，再由 Alembic stamp 长 ID；真实 PG 证据仍需 CI 固化 |
| Important-1：Skill revision 并发去重 | **已关闭** | SAVEPOINT + canonical reread；专项真实竞争验证通过 |
| Important-2：Connector registry 校验 | **已关闭** | registry unknown/异常 fail closed |
| Important-3：清空与 unresolved 语义 | **部分关闭** | 空列表、全部无效落库、空 description 已修复；unresolved 反馈未修复 |
| Minor：SQLite schema drift | **未关闭** | SQLite 仍跳过多数宽度声明修正 |
| Minor：真实并发 CAS | **未关闭** | 两个测试仍串行 |
| Minor：生产 Import adapter 测试 | **未关闭** | 函数边界和对象仍错误 |
| Minor：严格 UOW/生产路径测试 | **部分关闭** | 已查询 revision，但断言允许 1 条；PG 仍可 skip |
| Minor：迁移文档/运行态文件 | **已关闭** | 迁移头已修正，运行态文件保持清理 |

---

## 7. 验证记录

### 7.1 自动化测试

执行 M1、迁移、工具镜像、适配器和相关回归测试：

```text
181 passed, 1 skipped, 2 warnings in 39.80s
```

唯一 skip 为本地 PostgreSQL 不可用时跳过的集成迁移测试。

### 7.2 静态检查

```text
Ruff: All checks passed!
git diff --check: passed
```

### 7.3 迁移图

```text
2026_07_12_agent_releases
  -> 2026_07_12_widen_published_agent_ids
  -> 2026_07_12_widen_agent_ids (head)
```

### 7.4 专项行为验证

- 使用两个真实 `AsyncSession` 和屏障强制两个 Skill revision 调用同时完成首次 SELECT 后再 INSERT：两个调用均成功，并返回同一 revision ID。
- 使用 fake service 验证 `skills=[]` 镜像调用：第一次省略 skills，第二次明确提交 `[]`，清空语义生效。
- 普通失败 publish 的专项查询结果为 0 个孤儿 Skill revision；仓库测试断言仍需收紧以固化该行为。

---

## 8. 修复优先级

1. 返回或结构化记录 unresolved Skills，并让镜像失败对调用方可见。
2. 将 Skill revision 与 Draft CAS 测试改为真实双 session 并发。
3. 修正生产 Import adapter 测试函数边界和测试对象。
4. 将失败 publish 的 Skill revision 断言收紧为 0，并在 CI 强制执行 PostgreSQL 迁移测试。
5. 决定并统一 SQLite schema 宽度声明策略。

---

## 9. 最终判定

**Ready to merge：No。**

合并前至少必须关闭 Important-1，确保用户和上层系统能够知道哪些 Skill 未被接受，并避免文件系统与数据库草稿在无提示的情况下分叉。其余 Minor 建议在 M1 合并前一并清理，以确保并发、迁移和生产适配器修复都有准确的自动化防回归证据。
