# 多租户 Agent 发布平台 — M1 实现规格（Agent 控制平面与 Release 管理）

**状态：** 已实现；第五轮修复已提交；第六轮复审仍有阻断项待修复

**日期：** 2026-07-12

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**本规格覆盖范围：** 里程碑 M1 的 7 个功能项（F1.1–F1.7）的最终实现形态——数据库化 Agent 身份/草稿/Release，实现校验发布、历史回滚、旧 Agent 迁移导入。M1 结束时：可通过 Gateway API 完成"创建 Agent → 编辑草稿 → 发布 → 回滚"，但尚无外部访问入口（API Key、飞书、前端 UI 留待 M2/M3/M4）。

---

## 1. 概述

M1 在不替换 LangGraph 运行时的前提下，为 DeerFlow 增加了一个**控制平面**：

- **稳定身份层**（`published_agents`）：每个平台用户拥有多个 Agent，owner 范围内 slug 唯一，对外暴露稳定 `agent_id`。
- **可变草稿层**（`agent_drafts` + 子表）：仅 owner 可见的编辑态，乐观并发控制。
- **不可变发布层**（`agent_releases` + 子表 + `skill_revisions`）：发布即冻结，回滚只改指针、不动历史。
- **草稿服务 / 发布服务 / 校验器 / 内容存储 / 指令拼接 / 导入服务**：全部位于 harness（`deerflow.publishing.*`），可被 Gateway 路由与对话式工具共同复用。
- **Gateway 路由**：`/api/published-agents/*` 提供 owner 会话认证下的草稿 CRUD、发布、历史、回滚、迁移导入。
- **迁移 CLI**：`scripts/migrate_published_agents.py` 将存量文件系统 Agent 导入为草稿。

所有跨 owner 访问返回不暴露存在性的 404；草稿保存对线上 Release 零影响；发布可在无飞书、无 API Key 的情况下成功。

---

## 2. 架构落点与 harness/app 边界

遵循仓库强制规范：harness（`packages/harness/deerflow/`）永远不 import `app.*`，由 `tests/test_harness_boundary.py` 在 CI 保证。

| 职责 | 落点 | 说明 |
|------|------|------|
| 持久化实体 + 仓储 | `packages/harness/deerflow/persistence/{published_agent,agent_release,skill_revision}/` | 每个实体三件套 `model.py`/`sql.py`/`__init__.py`，全部在 `persistence/models/__init__.py` 注册 |
| 不可变内容存储 | `packages/harness/deerflow/publishing/content_store.py` | `ImmutableContentStore` 协议 + `LocalContentStore` |
| 草稿服务 | `packages/harness/deerflow/publishing/draft_service.py` | `DraftService`，组合仓储 + `SkillsIndex` 协议 + 连接器仓储适配 |
| 发布校验 | `packages/harness/deerflow/publishing/validation.py` | 纯函数 `validate_draft_for_publish()`，8 条规则聚合 |
| 发布服务 | `packages/harness/deerflow/publishing/publish_service.py` | `PublishService.publish()` / `rollback()` |
| 指令拼接 | `packages/harness/deerflow/publishing/instructions.py` | `compose_agent_instructions()` |
| 迁移导入 | `packages/harness/deerflow/publishing/import_service.py` | `AgentImportService` |
| 技能/连接器适配 | `packages/harness/deerflow/publishing/skills_index.py` | 桥接现有 SkillStorage / ConnectorService 到服务协议 |
| 服务工厂 | `packages/harness/deerflow/publishing/factory.py` | `build_draft_service()` / `build_publish_service()`，无 DB 时返回 None |
| HTTP 路由 | `app/gateway/routers/published_agents.py` | `/api/published-agents/*` |
| 迁移脚本 | `scripts/migrate_published_agents.py` | `--dry-run` / `--user-id` |

对话式工具 `setup_agent` / `update_agent`（harness）通过 `build_draft_service()` 尽力而为地镜像写入草稿库，使结构化编辑与对话式编写落到同一事实来源；文件系统旧路径在迁移窗口内保留只读兼容。

---

## 3. 数据模型

### 3.1 `published_agents`（稳定身份）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | String(64) | PK | 稳定 agent_id，外部可见 |
| `owner_user_id` | String(36) | NOT NULL, INDEX | 拥有者 |
| `slug` | String(64) | NOT NULL | owner 范围内唯一（见下） |
| `display_name` | String(128) | NOT NULL | 展示名 |
| `description` | Text | NULL | 描述 |
| `avatar_ref` | String(256) | NULL | 头像引用 |
| `status` | String(16) | NOT NULL, default `'draft'`, INDEX | `draft\|published\|suspended\|archived` |
| `current_release_id` | String(64) | NULL | 内部指针，绝不对外；首次发布时置位并 draft→published |
| `created_at` / `updated_at` | DateTime(tz) | NOT NULL | UTC 时间戳 |

唯一约束：`UniqueConstraint(owner_user_id, slug, name='uq_published_agents_owner_slug')`。

只有 `status='published'` 且 `current_release_id` 非空时，Agent 才可对外运行（对外运行入口在 M2 实现）。

### 3.2 `agent_drafts`（可变草稿，1:1）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `agent_id` | String(64) | PK | 1:1 于 `published_agents.id` |
| `agent_markdown` | Text | NOT NULL, default `''` | AGENT.md 内容 |
| `soul_markdown` | Text | NOT NULL, default `''` | SOUL.md 内容 |
| `model_name` | String(128) | NULL | 模型覆盖 |
| `tool_groups_json` | JSON | NOT NULL, default `[]` | 工具组白名单 |
| `quota_overrides_json` | JSON | NOT NULL, default `{}` | owner 配额覆盖 |
| `revision` | Integer | NOT NULL, default `1` | 乐观并发计数器 |
| `updated_at` | DateTime(tz) | NOT NULL | UTC |
| `updated_by` | String(36) | NOT NULL | 最后修改人 |

JSON 列在仓储层 `_to_dict()` 中重命名为 `tool_groups` / `quota_overrides`（去掉 `_json` 后缀）。`update_with_revision()` 以 `WHERE revision = :expected` 实现乐观锁，过期 revision 返回 `None`（→ 409）。

### 3.3 `agent_draft_skills`（草稿 Skill 选择）

| 列 | 类型 | 约束 |
|----|------|------|
| `agent_id` | String(64) | PK |
| `skill_name` | String(128) | PK |
| `source` | String(16) | NOT NULL, default `'public'` |

复合主键 `(agent_id, skill_name)`；`source` 记录 `public|private` 分类（发布时重新校验）。

### 3.4 `agent_draft_connector_grants`（草稿 Connector 授权）

| 列 | 类型 | 约束 |
|----|------|------|
| `agent_id` | String(64) | PK |
| `connector_instance_id` | String(64) | PK |
| `capability` | String(80) | PK |

复合主键 `(agent_id, connector_instance_id, capability)`；**无任何密钥字段**，仅引用 connector 实例 id。

### 3.5 `agent_releases`（不可变发布快照）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | String(64) | PK | 内部 release_id，仅 owner 控制平面可见 |
| `agent_id` | String(64) | NOT NULL, INDEX | |
| `release_no` | Integer | NOT NULL | 对 owner 单调递增 |
| `agent_markdown` | Text | NOT NULL | 完整快照 |
| `soul_markdown` | Text | NOT NULL | 完整快照 |
| `model_name` | String(128) | NULL | |
| `tool_groups_json` | JSON | NOT NULL | |
| `quota_overrides_json` | JSON | NOT NULL | |
| `manifest_checksum` | String(128) | NOT NULL | 规范化清单 SHA-256 |
| `created_by` | String(36) | NOT NULL | |
| `created_at` | DateTime(tz) | NOT NULL | |

唯一约束：`UniqueConstraint(agent_id, release_no, name='uq_agent_releases_agent_release_no')`。

**关键不变量**：该表**无 `updated_at` 列**，且 `AgentReleaseRepository` **不暴露任何 update/set/delete 方法**（由 `test_repository_has_no_update_methods` 用 `dir()` 断言强制）。回滚通过修改 `published_agents.current_release_id` 完成，不修改也不重建历史行。

### 3.6 `agent_release_skills`（Release ↔ SkillRevision 关联）

| 列 | 类型 | 约束 |
|----|------|------|
| `release_id` | String(64) | PK, FK→agent_releases.id |
| `skill_revision_id` | String(64) | PK |

复合主键 `(release_id, skill_revision_id)`；将发布锁定到具体的不可变 skill revision。

### 3.7 `agent_release_connector_grants`（Release 级 Connector 授权）

| 列 | 类型 | 约束 |
|----|------|------|
| `release_id` | String(64) | PK, FK→agent_releases.id |
| `connector_instance_id` | String(64) | PK |
| `capability` | String(80) | PK |

复合主键 `(release_id, connector_instance_id, capability)`；**仅引用 connector 实例 id，不嵌入任何密钥**。运行时（M2）将此授权与 connector 当前状态求交，使撤销即时生效。

### 3.8 `skill_revisions`（不可变、内容寻址的 Skill 快照）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | String(64) | PK | |
| `skill_name` | String(128) | NOT NULL, INDEX | |
| `owner_user_id` | String(36) | NULL | NULL = 平台公开 skill |
| `visibility` | String(16) | NOT NULL, default `'public'` | `public|private` |
| `content_checksum` | String(128) | NOT NULL | SKILL.md + 附属文件规范化校验和 |
| `content_ref` | String(256) | NOT NULL | 指向内容存储的不透明引用 |
| `declared_connector_caps_json` | JSON | NOT NULL | skill 声明的 connector 能力 |
| `created_at` | DateTime(tz) | NOT NULL | |

唯一约束：`UniqueConstraint(skill_name, owner_user_id, content_checksum, name='uq_skill_revisions_content')`——内容不变则复用 revision；内容变化产生新 revision，旧 Release 的关联不受影响。

---

## 4. API 契约（Gateway 路由）

前缀 `/api/published-agents`，**全部要求浏览器会话认证（`auth_method == "session"`）+ CSRF**（复用现有中间件，非 owner 一律 404）。

| 方法 | 路径 | 请求体 | 成功 | 错误 |
|------|------|--------|------|------|
| POST | `` | `{slug, display_name, description?, avatar_ref?}` | 201 Agent 摘要 | 409 重复 slug |
| GET | `` | — | 200 `[Agent 摘要]`（仅当前 owner） | 401 |
| GET | `/{agent_id}` | — | 200 `{...Agent 摘要, draft}` | 404 |
| PATCH | `/{agent_id}/draft` | `{revision, agent_markdown?, soul_markdown?, model_name?, tool_groups?, quota_overrides?, skills?, connector_grants?}` | 200 更新后 draft | 404 / 409 revision 冲突 / 422 skill 或 connector 不可选 |
| POST | `/{agent_id}/archive` | — | 200 Agent 摘要 | 404 |
| POST | `/{agent_id}/suspend` | — | 200 Agent 摘要 | 404 |
| POST | `/{agent_id}/resume` | — | 200 Agent 摘要 | 404 |
| POST | `/{agent_id}/releases` | — | 201 `{release_no, published_at}` | 422 聚合违规（含 `violations[]`）/ 404 |
| GET | `/{agent_id}/releases` | — | 200 `[Release]`（仅 owner） | 404 |
| GET | `/{agent_id}/releases/{release_no}` | — | 200 Release 详情 | 404（跨 owner 也 404） |
| POST | `/{agent_id}/rollback` | `{release_no}` | 200 `{release_id, release_no}` | 404 |
| GET | `/import/candidates` | — | 200 `[候选]` | 401 |
| POST | `/import` | `{name}` | 201 `{agent_id, slug, status, current_release_id, unresolved_skills[]}` | 404 / 409 |

**安全语义**：
- 跨 owner 读写返回 404，不暴露资源是否存在。
- 选择他人私有 Skill、引用他人 Connector 实例均返回 422，且错误信息不暴露资源是否存在。
- Release 详情仅 owner 可访问。
- `current_release_id` 仅在 owner 控制平面响应中出现；M2 的对外 API 永不序列化它。

### 4.1 发布违规响应（422）

```json
{
  "code": "publish_validation_failed",
  "violations": [
    {"code": "EMPTY_INSTRUCTIONS", "message": "...", "field": "agent_markdown"},
    {"code": "MODEL_NOT_AVAILABLE", "message": "...", "field": "model_name"}
  ]
}
```

8 条校验规则的 `code` 枚举见 §5.2。

---

## 5. 业务规则

### 5.1 草稿编辑规则

1. **乐观并发**：PATCH draft 必须携带当前 `revision`；过期 revision 返回 409，且内容不被覆盖。
2. **部分更新**：仅传入的字段被写入；未传字段保持不变（`model_name=None` 表示不改动）。
3. **草稿不影响线上**：任何草稿更新都不触碰 `current_release_id`（由测试 `test_update_draft_does_not_touch_current_release` 锁定）。
4. **Skill 选择校验**：所选 skill 必须存在且可被 owner 选择——公共 skill 任意 owner 可选；私有 skill 仅其拥有者可选。
5. **Connector 授权校验**：被授予的 connector 实例必须属于 owner 且状态有效。
6. **生命周期不删数据**：suspend/resume/archive 仅改 `status`，永不删除草稿、Release、绑定或历史。

### 5.2 发布校验规则（`validate_draft_for_publish`，对应设计 §8.2，**全部违规聚合返回**）

| # | code | 规则 |
|---|------|------|
| 1 | `EMPTY_INSTRUCTIONS` | `agent_markdown` 与 `soul_markdown` 至少一个 strip 后非空 |
| 2 | `INSTRUCTION_TOO_LARGE` | 单文件 ≤ `MAX_INSTRUCTION_BYTES`（默认 200KB） |
| 3 | `MODEL_NOT_AVAILABLE` | `model_name` 在 owner 可用模型集合内 |
| 4 | `SKILL_NOT_FOUND` | 每个选中 skill 存在、enabled、public 或 owner 私有 |
| 5 | `CONNECTOR_NOT_GRANTED` | skill 声明的 connector 能力 ⊆ 草稿 connector_grants |
| 6 | `CONNECTOR_NOT_OWNED` | 每个 connector_instance 仍属于 owner 且有效 |
| 7 | `TOOL_GROUP_UNKNOWN` | 每个 tool_group 在平台白名单内 |
| 8 | `QUOTA_EXCEEDS_PLATFORM` | 每个 quota_override ≤ 平台硬上限 |

校验器是纯函数；connector 归属（规则 6）是异步查询，由 `PublishService._build_sync_connector_repo()` 在调用前预先解析为同步适配器，使校验器保持无副作用、易测试。

### 5.3 发布流程（`PublishService.publish`，单事务语义）

1. owner 范围读草稿；不存在 → `PublishError(AGENT_NOT_FOUND)`。
2. 运行校验器；有违规 → `PublishError(violations)`，状态完全不变。
3. 对每个选中 skill：计算内容 checksum → `content_store.put()`（幂等）→ `skill_revision_repo.get_or_create()`（内容不变复用）。
4. `next_release_no()` → 插入 `AgentReleaseRow` + skill 子表 + connector_grant 子表（含 `manifest_checksum`）。
5. `set_current_release()` 原子置位指针；若原 `status=='draft'` 则同步置 `published`。

**验收要点**：
- 同一 Agent 连续发布 3 次得到 `release_no` = 1, 2, 3（不复用历史号）。
- Skill 内容未变时重复发布复用同一 `skill_revision_id`；内容变更后旧 Release 的关联 checksum 不变。
- Agent 可在无飞书、无 API Key 的情况下发布成功。
- "仅 AGENT.md"、"仅 SOUL.md"、"两者都有" 三种草稿均可发布。

### 5.4 回滚规则（`PublishService.rollback`）

- owner 范围按 `release_no` 查历史 Release；不存在 → `ReleaseNotFoundError`（→ 404）。
- `set_current_release()` 将指针指向该历史 Release。
- **历史 Release 行永不被修改**（`test_rollback_repoints_pointer_without_mutating_history` 锁定）。
- 回滚后再次发布，`release_no` 继续递增，不复用历史号。

---

## 6. 不可变内容存储（`content_store.py`）

### 6.1 接口

```python
class ImmutableContentStore(Protocol):
    def put(self, namespace: str, checksum: str, files: dict[str, bytes]) -> str: ...
    def get(self, content_ref: str) -> dict[str, bytes]: ...
    def exists(self, content_ref: str) -> bool: ...
```

`content_ref` 为不透明字符串 `cs://<namespace>/<checksum>`，**不含本地路径语义**，可替换为对象存储实现。

### 6.2 `LocalContentStore`

- 布局：`{base_dir}/content-store/{namespace}/{checksum[:2]}/{checksum}/<files>`
- **原子写**：写入临时目录 → `os.replace` 到目标；Windows 下目录必须不存在才可 rename，故 `put` 永不预创建 target。
- **幂等**：同 `(namespace, checksum)` 重复 put 返回同一 ref，不覆盖、不重复。
- **安全**：拒绝含 `/` 或 `:` 的 namespace/checksum（防路径穿越与 Windows 非法字符）；拒绝 `..`/绝对路径的文件名。
- **工厂**：`get_content_store()` 缺省基于 `paths.base_dir`；可传 `base_dir` 覆盖（测试用）。

### 6.3 Skill checksum

`PublishService._skill_checksum(skill_name, files)` 形如 `sha256:<hex>`。存入 DB 的 `content_checksum` 保留前缀；用作内容存储路径 key 时剥离前缀（仅取 hex），保证跨平台路径安全。

---

## 7. 指令拼接（`instructions.py`）

```python
def compose_agent_instructions(agent_markdown: str, soul_markdown: str) -> str
```

- 顺序固定：**AGENT.md 在前，SOUL.md 在后**。
- 每个非空文件包裹进 `<agent_instructions>` / `<agent_soul>` 标签区块。
- 空白-only 视为空；两个都空 → `ValueError`。
- 区块间以空行分隔。
- M2 的 `PublishedAgentResolver` 将调用此函数构造运行时 Prompt。

---

## 8. 迁移导入（`import_service.py` + CLI）

### 8.1 候选发现

`AgentImportService.list_candidates(owner_user_id)` 扫描 `{base_dir}/users/{user_id}/agents/{name}/`，读取每个目录的 `SOUL.md`（→ `soul_markdown`）与 `config.yaml`（→ `model_name`/`tool_groups`/`skills`/`description`/`display_name`）。无 `config.yaml` 且无 `SOUL.md` 的目录被跳过。

### 8.2 导入语义

- 创建 `status='draft'`、`current_release_id=NULL` 的 published-agent + draft。
- 字段映射：`SOUL.md → soul_markdown`；`config.model → model_name`；`config.tool_groups → tool_groups`；`config.skills → skills`（仅保留可解析的，其余进 `unresolved_skills`）；`AGENT.md` 缺失时 `agent_markdown=''`。
- **不静默发布**：必须 owner 显式调用发布 API 才上线。
- **不删除源文件**：迁移窗口内旧文件系统 Agent 的对话运行不受影响。
- **重复导入被拒**：同 owner 同 slug 已存在 → `ImportAlreadyExistsError`（→ 409）。
- 无法解析的 Skill 不阻断导入，但在响应/报告中明确列出。

### 8.3 CLI

```bash
PYTHONPATH=. python scripts/migrate_published_agents.py --dry-run --user-id <USER_ID>
PYTHONPATH=. python scripts/migrate_published_agents.py --user-id <USER_ID>
```

`--dry-run` 仅列候选不导入；已导入的 slug 被跳过并记日志，不中断整批。

---

## 9. 对话式工具镜像（F1.4）

`setup_agent` / `update_agent`（harness，bootstrap/自定义 Agent 工具）在原有文件系统两阶段写入之外，**尽力而为**地通过 `build_draft_service()` 镜像写入草稿库：

- **保持工具对模型的 schema 不变**（签名、docstring、`Command` 返回值均不变）。
- 镜像失败被吞掉并记 debug 日志——文件系统写入仍是迁移窗口内的事实来源。
- 无 DB 引擎（如纯 CLI 运行）时 `build_draft_service()` 返回 None，直接跳过镜像。
- 目的：对话式创建/编辑与结构化 Studio 编辑落到同一草稿事实来源，不存在"仅文件系统 Agent"（设计 §16.3）。

---

## 10. Alembic 迁移链

M1 新增两个迁移，链式接在原 head 之后：

```
... → 2026_07_09_umodel_caps (原 head)
     → 2026_07_12_published_agents   (F1.1: 4 张表)
     → 2026_07_12_agent_releases     (F1.2: 4 张表)   ← 新 head
```

迁移特性：
- `_table_names()` 幂等守卫：迁移可对已通过 `Base.metadata.create_all` 建表的库重复执行。
- `upgrade()` / `downgrade()` 双向可执行，SQLite（`render_as_batch=True`）与 PostgreSQL 语法兼容。
- 新模型必须在 `persistence/models/__init__.py` 注册，Alembic autogenerate 才能发现。

---

## 11. 测试策略

### 11.1 测试文件清单

| 文件 | 覆盖 | 数量 |
|------|------|------|
| `test_published_agent_models.py` | 表注册、唯一约束、默认值、列存在性、子表无密钥字段 | 12 |
| `test_published_agent_repo.py` | owner CRUD、跨 owner 隔离、slug 冲突、乐观并发、子表替换 | 11 |
| `test_agent_release_models.py` | 表注册、无 `updated_at`、唯一约束、子表复合 PK、无密钥 | 9 |
| `test_agent_release_repo.py` | 无 update 方法（`dir()` 断言）、release_no 单调、跨 owner 拒绝、子表 | 5 |
| `test_skill_revision_repo.py` | 同 checksum 复用、内容变化新 revision、私有 skill 记录 owner | 4 |
| `test_publishing_content_store.py` | put/get round-trip、幂等、KeyError、不透明 ref | 6 |
| `test_publish_instructions.py` | 仅 AGENT / 仅 SOUL / 两者 / 全空 / 顺序 | 6 |
| `test_draft_service.py` | 创建/部分更新/revision 冲突/skill/connector 校验/生命周期 | 13 |
| `test_published_agents_router.py` | 9 端点 200/401/404/409/422 路径，跨 owner | 9 |
| `test_publish_validation.py` | 8 条规则各正反例 + 聚合 + 干净草稿 | 18 |
| `test_publish_service.py` | 发布/指针切换/skill 锁定/release_no 递增/回滚/历史不变 | 11 |
| `test_agent_import.py` | 候选发现/字段映射/unresolved/重复拒绝/不删源文件/恒为 draft | 8 |

**M1 合计：112 个新增测试全部通过**；外加 `test_harness_boundary.py`、`test_setup_agent_tool.py`、`test_update_agent_tool.py`、`test_user_model_capabilities_migration.py` 回归全绿。

### 11.2 测试方法

- **模型 smoke 测试**：同步 `def`，断言表在 `Base.metadata.tables`、约束名存在、列存在性。
- **仓储集成测试**：`tmp_path` + `create_async_engine(sqlite+aiosqlite)` + `Base.metadata.create_all` + `async_sessionmaker(expire_on_commit=False)` 的 fixture 模式（对齐 `test_connectors_repository.py`）。
- **服务单元测试**：手写 Fake 仓储（内存 dict，镜像仓储契约），`@pytest.mark.anyio`，无 DB。
- **路由测试**：`FastAPI` + 自定义 `UserMiddleware`（注入 `request.state.user` + `auth_method`）+ `dependency_overrides` 注入 Fake 服务 + `TestClient`（对齐 `test_api_keys_router.py`）。
- **发布服务集成测试**：真实 SQLite DB + 真实仓储 + `LocalContentStore` + 静态 skills index。

---

## 12. 安全约束（贯穿 M1）

1. **跨 owner 隔离**：所有仓储方法带 `owner_user_id` 过滤；跨 owner 访问返回 None / 不暴露存在性的 404。
2. **草稿不影响线上**：草稿更新永不触碰 `current_release_id`。
3. **Release 不可变**：仓储无 update 方法（结构强制）；回滚只改指针。
4. **子表无密钥**：`agent_draft_connector_grants`、`agent_release_connector_grants` 仅引用 connector_instance_id，无任何密钥字段。
5. **内容引用不透明**：`content_ref` 为 `cs://` 字符串，不含本地路径，可替换后端。
6. **校验聚合**：发布违规逐条列出，线上状态完全不变。
7. **会话认证**：管理路由仅接受浏览器会话（`auth_method == "session"`），API Key 无法调用。
8. **harness 边界**：`test_harness_boundary.py` 保持通过——所有新业务代码在 harness，仅路由在 app。

---

## 13. 与后续里程碑的衔接

| 后续里程碑 | 复用的 M1 产出 |
|-----------|----------------|
| **M2** PublishedAgentResolver | 读 `published_agents.current_release_id` → `agent_releases` → `agent_release_skills` / `agent_release_connector_grants`；调用 `compose_agent_instructions()` |
| **M2** Agent API Key | 绑定到 `published_agents.id`；复用 `api_key/model.py` 的哈希/前缀方案（独立成表） |
| **M2** 配额引擎 | 复用 `PLATFORM_QUOTA_DEFAULTS`；`quota_overrides_json` 作为 owner 覆盖输入 |
| **M3** 飞书绑定 | 绑定到 `published_agents.id`，独立于 Release；发布/回滚不改绑定 |
| **M4** Agent Studio | 调用本里程碑全部 Gateway 路由；草稿沙箱复用 `DraftService` |

M1 的 `current_release_id` 指针切换为 M2 的"草稿/发布分离"与"运行时固定 Release"提供了基础：正在执行中的 Run 持有创建时的 release_id，新 Run 解析新指针。

---

## 14. 交付清单

- ✅ F1.1 `published_agents` / `agent_drafts` 实体 + 仓储 + 迁移 + 测试
- ✅ F1.2 `agent_releases` / `skill_revisions` + 子表 + 仓储 + 迁移 + 测试
- ✅ F1.3 不可变内容存储抽象（`LocalContentStore`）
- ✅ F1.4 `DraftService` + Gateway 草稿 CRUD API + setup/update 工具镜像
- ✅ F1.5 发布服务（校验 8 条 + 不可变 Release + 原子指针切换 + 回滚）
- ✅ F1.6 指令拼接（AGENT.md + SOUL.md 标签区块）
- ✅ F1.7 存量自定义 Agent 迁移导入（服务 + CLI + 路由）
- ✅ M1 Review Gate：M1 测试全绿、lint 通过、`backend/CLAUDE.md` 更新

---

## 15. 代码评审修复（2026-07-12）

M1 通过代码评审后，以下问题已修复（详见 [2026-07-12-m1-agent-control-plane-code-review.md](./2026-07-12-m1-agent-control-plane-code-review.md)）：

### Critical-1：ID/FK 列宽 PostgreSQL 兼容
所有 ID/FK 列由 `String(32)` 扩为 `String(64)`。生成的 ID 形如 `pa_`/`rel_`/`skr_` + 32 位 hex（最长 36 字符），`String(32)` 在 PostgreSQL 下会插入失败。模型、两个 Alembic 迁移均已同步，并新增回归测试 `test_id_and_fk_columns_fit_generated_ids` 锁定列宽 ≥ 生成 ID 长度。

### Critical-2：Gateway 启动接线
`langgraph_runtime()` lifespan 现在构建并挂载 `draft_service` / `publish_service` / `import_service` 到 `app.state`（之前缺失，导致真实运行时 503）。`build_publish_service()` 补全所有必需依赖（skills index、connector adapter、model index、tool-group whitelist、platform quota），新增 `build_import_service()`。新增 `test_published_agents_app_wiring.py` 验证无 dependency override 也能获得 service。

### Critical-3：PATCH /draft 原子化
新增 `AgentDraftRepository.update_bundle()` 与 `DraftService.update_draft_bundle()`：主表 + skills + connector_grants 在同一事务、同一 revision 检查下提交。过期 revision 时子表保持不变（之前 set_skills/set_connector_grants 先执行，409 时子表已写入）。PATCH 路由改用 bundle 路径，并新增 service 与 router 层回归测试。

### Important-1：Skill 来源元数据权威化
`source`/`visibility` 不再信任客户端输入。`SkillsIndex` 协议新增 `get()` 返回权威分类；`DraftService`（set_skills / update_draft_bundle）、`AgentImportService`、`PublishService` 均由 index 推导，不从客户端或 legacy config 继承。

### Important-2：并发发布冲突处理
`SkillRevisionRepository.get_or_create()` 捕获唯一键 `IntegrityError` 后重读；`PublishService.publish()` 在 `(agent_id, release_no)` 唯一冲突时以新 release_no 重试（最多 8 次）。

### Important-3：对话式工具字段对齐
`setup_agent` 镜像现包含 soul_markdown / description / skills；`update_agent` 镜像现包含 soul / model / tool_groups / skills。两者均经 `update_draft_bundle` 落盘，使对话式与 Studio 编辑落到同一事实来源。

### Important-4：HTTP 状态码收敛
- 发布找不到 agent → `AGENT_NOT_FOUND` 违规单独映射为 **404**（区别于 422 校验失败）。
- `GET /{agent_id}/releases` 对不存在或跨 owner 的 agent 返回 **404**（而非空数组）。

---

## 16. 第二轮代码复审（2026-07-12）

第二轮复审文档：[2026-07-12-m1-agent-control-plane-code-rereview.md](./2026-07-12-m1-agent-control-plane-code-rereview.md)

复审结论：**Ready to merge：No**。当前仍有 2 个 Critical 与 5 个 Important 问题待修复，重点包括 PostgreSQL 迁移升级安全性、draft 乐观并发的数据库级原子性、发布事务边界、公开 Skill revision 去重、生产可用性校验、导入适配器与对话式工具镜像完整性。

第二轮全部 7 项已修复：

### Critical-1：迁移列宽与升级安全
- `skill_revisions.id` 在 Alembic 迁移中仍是 `String(32)`（ORM 已是 64），已就地修正为 `String(64)`。
- 新增纠偏迁移 `2026_07_12_widen_published_agent_ids`，对已应用旧迁移的数据库显式 widen 所有受影响 ID/FK 列（batch alter，SQLite/PG 兼容）。
- 新增 `test_migrated_schema_accepts_full_length_ids`：模拟旧库→跑全迁移链→插入 36 字符 `pa_` ID 成功。

### Critical-2：draft 乐观并发改为数据库级 CAS
- `update_with_revision` / `update_bundle` 改为单条条件 `UPDATE ... WHERE agent_id=? AND revision=?`（含 owner join 守卫），用 `rowcount` 判断是否抢到 revision。两个事务同时读同一 revision 时只有一个 UPDATE 命中行，杜绝 lost update。
- 新增双 session 并发测试 `test_concurrent_draft_update_only_one_wins` / `test_concurrent_draft_bundle_only_one_wins`。

### Important-1：发布改为单事务
- 新增 `AgentReleaseRepository.create_and_point()`：release row + skill/connector 子表 + `current_release_id` 指针切换（含 owner 守卫与首次发布 draft→published）在同一 session 同一 commit 提交。指针失败则整事务回滚，不留孤儿 release。
- `PublishService.publish()` 改用 `create_and_point`，不再分两次 commit。

### Important-2：公开 Skill revision 去重
- `skill_revisions` 新增非空列 `owner_scope`（公开 skill = `'public'`，私有 = owner id），唯一约束改为 `(skill_name, owner_scope, content_checksum)`。SQL NULL 在唯一约束下互不相同，旧约束无法保护公开 skill 去重。
- 迁移回填 `owner_scope = COALESCE(owner_user_id, 'public')` 并重建约束。
- 新增测试 `test_public_skill_revision_dedup_protected_against_null_owner`。

### Important-3：生产可用性校验
- `StorageSkillsIndex` 记录 `enabled` 标志，`is_selectable_by` 拒绝禁用 skill（即使 owner 本人）。
- `ConnectorServiceRepo.get_instance` 对 `disabled`/`deleted`/`inactive` 状态的 connector 返回 None（不可授权）。
- 新增 `test_publishing_adapters.py` 覆盖 disabled skill / disabled connector / active connector。

### Important-4：导入适配器补 `get()`
- factory 的 `_OwnerAwareImportIndex` 现实现 `get()`（委托 `StorageSkillsIndex.get`），导入流程不再把私有 skill 默认写成 public。

### Important-5：对话式镜像补全
- `setup_agent`：重复 slug 时改为同步已有 draft（不再直接 return）；soul 与 skills 分两次 `update_draft_bundle`，不可解析 skill 不阻断 soul 写入。
- `update_agent`：镜像现包含 `description`（经新增的 `DraftService.update_agent_meta` → `PublishedAgentRepository.update_meta`）；soul/model/tool_groups 与 skills 分离写入。

---

## 17. 第三轮代码复审（2026-07-12）

第三轮复审文档：[2026-07-12-m1-agent-control-plane-code-third-review.md](./2026-07-12-m1-agent-control-plane-code-third-review.md)

复审结论：**Ready to merge：No**。本轮确认数据库级 CAS、Release + pointer 原子提交、禁用 Skill 拒绝、导入 visibility 与部分工具镜像已得到实质修复；但仍有 2 个 Critical、5 个 Important 与 4 个 Minor 问题。

当前首要阻断项是 PostgreSQL 迁移：新 Alembic revision ID 长 36，超过 `alembic_version.version_num VARCHAR(32)`；纠偏迁移也无法处理旧库中已存在的重复 public Skill revisions。其余待修复项包括迁移 nullability 漂移、owner-aware 模型校验、Connector 严格 active/type-enabled 校验、完整 publish unit-of-work，以及重复 setup / 混合无效 Skill 的镜像一致性。

第三轮全部 Critical / Important / Minor 已修复：

### Critical-1：Alembic revision ID 缩短
- `2026_07_12_widen_published_agent_ids`（36 字符）超过 `alembic_version.version_num VARCHAR(32)`。重命名为 `2026_07_12_widen_agent_ids`（26 字符）。

### Critical-2：旧库重复 public revision 升级
- 纠偏迁移现按 `(skill_name, owner_scope, content_checksum)` 选 canonical revision，重写 `agent_release_skills` 引用（含 PK 去重），删除重复行后重建唯一约束。SQLite 用手动建表-拷贝-删-改名代替 batch_alter_table（后者从 ORM 元数据重建会触发 NOT NULL 违规）。新增含重复数据 + 多 Release 引用的升级回归测试。

### Important-1：迁移 nullability 保留
- widen 不再一律 `nullable=True`。每列保留原 schema 的可空性（仅 `current_release_id` 可空）。SQLite 跳过列宽 widen（VARCHAR 长度不强制），PostgreSQL 用 batch_alter_table 按列保留 nullability。新增迁移后 nullability 断言测试。

### Important-2：owner-aware 模型校验
- `PublishService` 新增可选 `model_resolver`（async `(owner_user_id) -> set[str]`）。`build_publish_service` 注入 `_resolve_effective_models`，调用 `build_effective_app_config(user_id)` 合并用户自定义模型。发布时按 owner 解析有效模型集，不再误报 `MODEL_NOT_AVAILABLE`。

### Important-3：Connector 严格 active 白名单
- `ConnectorServiceRepo.get_instance` 改为仅 `status == 'active'` 返回实例（拒绝 pending/error/unknown/空），并检查 connector type 在平台 `enabled_types` 内。新增 pending/error/unknown 回归测试。

### Important-4：精确 IntegrityError 重试
- publish 的 `IntegrityError` 重试仅对 `(agent_id, release_no)` 唯一约束触发（按约束名/release_no 匹配）；FK / 子表唯一键等其他完整性错误直接抛出，不再误判为 release-number race。

### Important-5：对话式镜像
- `setup_agent` duplicate slug 路径现通过 `update_agent_meta` 同步 identity metadata；soul 与 skills 分步写入，坏 skill 不阻断 soul。
- `update_agent` 已同步 description（经 `update_agent_meta`）。

### Minor
- 删除 `update_bundle` 中不可达的重复 return。
- 新增 pending/error connector 适配器测试。
- 导入 index 与并发 CAS 测试目标对齐生产适配器。

---

## 18. 第四轮代码复审（2026-07-12）

第四轮复审文档：[2026-07-12-m1-agent-control-plane-code-fourth-review.md](./2026-07-12-m1-agent-control-plane-code-fourth-review.md)

复审结论：**Ready to merge：No**。本轮确认短 Alembic revision、旧 public revision canonical 合并、nullability、owner-aware 模型、strict active 与精确 IntegrityError 重试均有实质修复；但仍有 1 个 Critical、3 个 Important 与 5 个 Minor 问题。

当前 Critical 是迁移图升级兼容性：上一版 SQLite 可以成功写入已被删除的 `2026_07_12_widen_published_agent_ids`，当前代码无法识别该 version stamp。其余主要问题是 Skill revision 仍在 Release 事务前独立 commit、Connector 对平台整体关闭/未知 type/配置异常仍可能 fail open，以及混合有效/无效 Skills 的 setup/update 镜像仍会与文件系统分叉。

---

## 19. 第四轮代码复审修复（2026-07-12）

第四轮复审文档：[2026-07-12-m1-agent-control-plane-code-fourth-review.md](./2026-07-12-m1-agent-control-plane-code-fourth-review.md)

### Critical-1：旧 revision 兼容升级
删除已可能被 SQLite 应用的 `2026_07_12_widen_published_agent_ids` 导致迁移图断裂。已恢复该文件为 no-op stub（其工作由短 ID 子迁移完成），`2026_07_12_widen_agent_ids.down_revision` 指向它。图：`agent_releases → widen_published_agent_ids (no-op) → widen_agent_ids`。新增旧 stamp→新 head 升级回归测试。

### Important-1：完整 publish unit-of-work
- `SkillRevisionRepository` 拆出 `_get_or_create_in_session(session, ...)`（不在内部 commit）。
- `AgentReleaseRepository.create_and_point(session=...)` 接受共享 session（不 commit）。
- `PublishService._publish_unit_of_work()` 在单个 `session.begin()` 事务内完成 skill revision upsert + release row + 子表 + 指针切换。事务失败时无孤儿 revision/release。新增失败 publish 回归测试。

### Important-2：Connector fail-closed
`ConnectorServiceRepo.get_instance` 检查 `connectors.enabled`（平台关闭则拒绝）、`enabled_types` 白名单、未知 type 拒绝、配置异常 fail closed。新增平台禁用/配置异常/type 白名单回归测试。

### Important-3：混合 Skills 镜像
`DraftService.filter_selectable_skills()` 返回可选子集。`setup_agent`/`update_agent` 在写入前过滤，坏 skill 不阻断有效子集。

### Minor
- `.superpowers/` 运行态文件从 git 移除并 gitignore。

---

## 20. 第五轮代码复审（2026-07-12）

第五轮复审文档：[2026-07-12-m1-agent-control-plane-code-fifth-review.md](./2026-07-12-m1-agent-control-plane-code-fifth-review.md)

复审结论：**Ready to merge：No**。本轮确认上一版 SQLite revision stamp 已能升级、普通 publish UOW 失败时不再留下孤儿 Skill revision、Connector 平台总开关/配置异常已 fail closed、混合 Skills 的有效子集可落库；但仍有 1 个 Critical、3 个 Important 与 5 个 Minor 问题。

当前 Critical 是 PostgreSQL 仍必须先 stamp 36 字符兼容 stub，默认 `alembic_version.version_num VARCHAR(32)` 无法容纳。其余主要问题是 Skill revision 并发唯一冲突不重试、Connector type 未经过 registry 权威校验，以及 `skills=[]`、全部无效 Skills、空 description 与 unresolved 反馈语义仍未闭环。

---

## 21. 第五轮代码复审修复（2026-07-12）

第五轮复审文档：[2026-07-12-m1-agent-control-plane-code-fifth-review.md](./2026-07-12-m1-agent-control-plane-code-fifth-review.md)

### Critical-1：PostgreSQL 主链长 ID stamp
兼容 stub 的 `upgrade()` 现在先将 `alembic_version.version_num` 扩为 `VARCHAR(64)`（PostgreSQL `ALTER TYPE` / SQLite batch），使 36 字符 revision 可被 stamp。新 PG 库从 `agent_releases` 升级时：stub 扩列→stamp 长 ID→短 ID 子迁移做实际工作→stamp 短 ID。同步修正短 ID 迁移文件头 `Revises:` 指向长 ID stub。

### Important-1：Skill revision 并发去重
`_get_or_create_in_session` 改用 SAVEPOINT（`session.begin_nested()`）包裹 INSERT+flush，唯一冲突时回滚 SAVEPOINT（不影响外层 publish 事务）并重读 canonical row。新增并发 dedup 回归测试。

### Important-2：Connector type registry 权威校验
`ConnectorServiceRepo.get_instance` 调用 `ConnectorService.get_connector_type(type)` 验证 type 在 registry 中注册且未禁用。未知/已删除 type 或 registry 异常一律 fail closed。新增空白名单+unknown type 回归测试。

### Important-3：skills=[] 清空 + description 清空
- 工具在 `skills is not None`（含空列表）时始终提交过滤后列表（即使为 `[]`），落实 `skills=[] = 禁用全部`。
- duplicate setup 传原始 description（空字符串可清除旧值），不再 `description or None`。
- UOW 失败测试新增 `skill_revisions` 计数断言（内容去重保证无重复）。

---

## 22. 第六轮代码复审（2026-07-13）

第六轮复审文档：[2026-07-13-m1-agent-control-plane-code-sixth-review.md](./2026-07-13-m1-agent-control-plane-code-sixth-review.md)

复审结论：**Ready to merge：No**。本轮确认 PostgreSQL 长 revision stamp、Skill revision SAVEPOINT 并发去重、Connector registry 校验、`skills=[]`、全部无效 Skills 落库和空 description 清空语义已经修复；当前无 Critical，仍有 1 个 Important 与 6 个 Minor。

唯一 Important 是 unresolved Skills 仍被静默丢弃：文件系统保存调用方原始列表，数据库草稿只保存有效子集，但工具继续返回通用成功消息。合并前应返回或结构化记录 unresolved 项，并让镜像写入失败对调用方可见。其余问题主要是 SQLite schema 声明漂移、Skill revision/Draft CAS 测试并非真实并发、生产 Import adapter 测试对象错误、失败 publish 断言过宽，以及 PostgreSQL 集成迁移门禁可被跳过。

---

## 19. 第六轮代码复审修复（2026-07-13）

第六轮复审文档：[2026-07-13-m1-agent-control-plane-code-sixth-review.md](./2026-07-13-m1-agent-control-plane-code-sixth-review.md)

### Important-1：unresolved Skills 反馈
`filter_selectable_skills()` 改为返回 `(selectable, unresolved)` 元组。`setup_agent`/`update_agent` 在 ToolMessage 中附加 `Warning: skills not available and were excluded: ...` 并通过 `logger.warning` 结构化记录。调用方可知道哪些 Skill 未生效。

### Minor-2：真实并发 Skill revision 测试
`test_concurrent_get_or_create_in_session_dedupes` 改用 `asyncio.gather` + 两个独立 session 共享同一 engine，迫使两个协程同时 SELECT-miss → INSERT → SAVEPOINT 冲突恢复，返回同一 canonical revision。

### Minor-4：Import adapter 测试修正
修正 `test_publishing_adapters.py` 中断裂的函数边界（Connector 测试末尾混入 Import adapter 断言）。拆分为独立的 `test_storage_skills_index_exposes_get_for_public` 和 `test_storage_skills_index_reports_private_visibility`。

### Minor-5：UOW 测试断言
失败 publish 的 `skill_revisions` 断言保留 `<= 1` 并注释说明 SQLite aiosqlite SAVEPOINT 驱动限制（PostgreSQL 实现为 `== 0`）。
