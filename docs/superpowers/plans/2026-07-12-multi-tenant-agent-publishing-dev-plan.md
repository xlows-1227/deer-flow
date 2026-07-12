# 多租户 Agent 发布平台 — 详细开发计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**关联设计文档：** [docs/superpowers/specs/2026-07-12-multi-tenant-agent-publishing-design.md](../specs/2026-07-12-multi-tenant-agent-publishing-design.md)

**目标：** 将 DeerFlow 从"单用户作用域的自定义 Agent 工作区"演进为"多租户 Agent 发布平台"：草稿/发布分离、不可变 Release、Agent 专属 API Key、独立飞书机器人绑定，复用现有 LangGraph 运行时。

**架构：** 新增控制平面（Studio / 发布服务 / 集成管理 / 运维）+ `PublishedAgentResolver` 接入层，全部落在现有 harness/app 分层内：新实体走 `deerflow.persistence`（SQLAlchemy + Alembic），Gateway 路由走 `app/gateway/routers`，渠道走 `app/channels`。不替换运行时。

**技术栈：** Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + LangGraph；前端 Next.js 16 + React 19 + TanStack Query。

**状态：** 待启动
**日期：** 2026-07-12

---

## 0. 阅读指南与执行方式

- 本计划按设计文档第 20 节的 4 个里程碑拆分，**必须按 M1 → M2 → M3 → M4 顺序交付**，每个里程碑结束有评审关口（Review Gate）。
- 每个功能项（编号 `F<里程碑>.<序号>`）包含：功能描述、涉及文件、开发任务（checkbox）、验收标准、测试要求。开发者按功能项逐个实现，**每个功能项完成即提交一次 commit 并跑通对应测试**。
- 遵循仓库强制规范：
  - **TDD 强制**：每个功能先写 `backend/tests/test_<feature>.py` 失败用例，再实现（见 backend/CLAUDE.md「Test-Driven Development — MANDATORY」）。
  - **harness/app 边界**：`packages/harness/deerflow/` 永远不 import `app.`*（CI 由 `tests/test_harness_boundary.py` 保证）。持久化实体、Resolver、策略引擎放 harness；HTTP 路由、渠道 Supervisor 放 app。
  - **文档同步**：每个里程碑收尾更新 `backend/CLAUDE.md` 与 `README.md` 的相关章节。
  - 代码风格：ruff、行宽 240、类型注解、双引号。
- 验证命令：
  - 后端全量测试：`cd backend && make test`
  - 单文件：`cd backend && PYTHONPATH=. uv run pytest tests/test_<feature>.py -v`
  - 前端：`cd frontend && pnpm check && pnpm test`

## 0.1 现有代码落点（开发前必读）


| 现有能力                  | 位置                                                                                                                                           | 本计划的复用方式                                          |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| ORM Base + to_dict    | `backend/packages/harness/deerflow/persistence/base.py`                                                                                      | 所有新表继承 `Base`                                     |
| 持久化实体模式               | `persistence/<entity>/{model.py, sql.py, __init__.py}`（参考 `persistence/api_key/`）                                                            | 新实体照此布局                                           |
| Alembic 迁移            | `persistence/migrations/versions/`，命名 `YYYY_MM_DD_<name>.py`                                                                                 | 每个新表组一个迁移文件                                       |
| 用户级 API Key           | `persistence/api_key/model.py`（`APIKeyRow`：`secret_hash`/`key_prefix`/`last_four`/`status`）、`app/gateway/routers/api_keys.py`                | Agent API Key 复用同样的哈希与前缀方案，**不复用同一张表**            |
| External API V1       | `app/gateway/routers/external.py`（前缀 `/api/v1/external`）、`app/gateway/external/{service,audit,errors,models,skill_policy}.py`                | Agent 对外 API 作为其平行外观层，复用 Conversation/Run 服务与幂等仓储 |
| 外部会话/幂等/审计表           | `persistence/external_conversation/`、`external_idempotency/`、`external_audit/`                                                               | Conversation 映射与幂等机制直接扩展                          |
| 自定义 Agent 文件存储        | `deerflow/config/agents_config.py`、`agents_api_config.py`、`config/paths.py`；目录 `users/{user_id}/agents/{name}/`（`SOUL.md` + `config.yaml`）   | M1 迁移导入的数据源                                       |
| setup/update_agent 工具 | `deerflow/tools/builtins/setup_agent_tool.py`、`update_agent_tool.py`                                                                         | M1 改造为写入草稿服务                                      |
| 现有 agents 路由          | `app/gateway/routers/agents.py`（GET/POST/PUT/DELETE `/agents`*）                                                                              | M1 扩展为草稿 CRUD 的入口                                 |
| Connector 持久化         | `persistence/connector/model.py`（`connector_instances`/`connector_grants`/`connector_audit_logs`）、`app/gateway/routers/connectors.py`        | Release 级授权白名单引用 `connector_instances.id`         |
| IM 渠道                 | `app/channels/service.py`（`ChannelService`、`start_channel_service`）、`feishu.py`（`FeishuChannel`）、`store.py`（JSON 映射）、`base.py`（`Channel` 抽象） | M3 演进为 DB 驱动 Supervisor                           |
| Run 与用量               | `persistence/run/`、`run_events`、`TokenUsageMiddleware`（harness middlewares）、`app/gateway/routers/thread_runs.py` token-usage 端点              | M2 用量记账在其上扩展                                      |
| 记忆系统开关                | `deerflow/agents/memory/`、`MemoryMiddleware`                                                                                                 | M2 无记忆策略需绕过/禁用这些组件                                |


---

# 里程碑 M1：Agent 控制平面与 Release 管理

**目标：** 数据库化 Agent 身份/草稿/Release，实现校验发布、历史回滚、旧 Agent 迁移导入。本里程碑结束时：可以通过 Gateway API 完成"创建 Agent → 编辑草稿 → 发布 → 回滚"，但尚无外部访问入口。

**交付边界：** 不包含外部 API、飞书、前端 UI（M2/M3/M4）。

## F1.1 领域实体与迁移：`agents` / `agent_drafts`

**功能描述：** 建立稳定 Agent 身份表与可变草稿表（设计文档 §7.1、§7.2）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/persistence/published_agent/model.py`
- 新建：`backend/packages/harness/deerflow/persistence/published_agent/sql.py`
- 新建：`backend/packages/harness/deerflow/persistence/published_agent/__init__.py`
- 新建：`backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_XX_published_agents.py`
- 测试：`backend/tests/test_published_agent_models.py`、`backend/tests/test_published_agent_repo.py`

**表结构要点（model.py）：**

```python
class PublishedAgentRow(Base):
    __tablename__ = "published_agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)          # 稳定 agent_id，外部可见
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)          # owner 范围内唯一
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    avatar_ref: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)  # draft|published|suspended|archived
    current_release_id: Mapped[str | None] = mapped_column(String(32))     # 内部指针，绝不对外
    created_at / updated_at: Mapped[datetime]

    __table_args__ = (UniqueConstraint("owner_user_id", "slug", name="uq_published_agents_owner_slug"),)


class AgentDraftRow(Base):
    __tablename__ = "agent_drafts"

    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)    # 1:1 于 published_agents
    agent_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")   # AGENT.md
    soul_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")    # SOUL.md
    model_name: Mapped[str | None] = mapped_column(String(128))
    tool_groups_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quota_overrides_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)       # 乐观并发
    updated_at: Mapped[datetime]
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)


class AgentDraftSkillRow(Base):
    __tablename__ = "agent_draft_skills"
    # (agent_id, skill_name) 复合主键；记录 skill 来源分类 public|private

class AgentDraftConnectorGrantRow(Base):
    __tablename__ = "agent_draft_connector_grants"
    # (agent_id, connector_instance_id, capability) 复合主键
```

**开发任务：**

- 写失败测试 `test_published_agent_models.py`：断言 4 张表在 `Base.metadata.tables` 中、owner+slug 唯一约束、status 枚举默认值（参考 `backend/tests/test_connectors_models.py` 的写法）
- 实现 `model.py`（上述 4 个 Row 类）
- 编写 Alembic 迁移 `2026_07_XX_published_agents.py`（`upgrade()` 建 4 表 + 索引，`downgrade()` 删表）
- 写失败测试 `test_published_agent_repo.py`：owner 范围 CRUD、跨 owner 读写被拒、slug 冲突报错、乐观并发（`revision` 不匹配时更新失败）
- 实现 `sql.py`：`PublishedAgentRepository`（`create/get/list_by_owner/update_meta/set_status/set_current_release`）与 `AgentDraftRepository`（`get/update_with_revision/replace_skills/replace_connector_grants`），全部方法带 `owner_user_id` 过滤
- 运行 `PYTHONPATH=. uv run pytest tests/test_published_agent_models.py tests/test_published_agent_repo.py -v` 全部通过
- Commit：`feat(m1): add published_agents & agent_drafts entities`

**验收标准：**

1. 两个不同 `owner_user_id` 各自创建同名 `slug` 的 Agent 均成功；同 owner 下重复 slug 抛唯一约束错误。
2. 仓储层任何方法在 `owner_user_id` 不匹配时返回 None/抛 NotFound，不返回他人数据。
3. 草稿更新必须携带当前 `revision`，过期 revision 更新被拒绝（防并发覆盖）。
4. Alembic `upgrade` / `downgrade` 在 SQLite 与 PostgreSQL 语法下均可执行。

## F1.2 领域实体与迁移：`agent_releases` / `skill_revisions` / Release 子表

**功能描述：** 不可变 Release 快照 + Skill revision 锁定 + Release 级 Connector 授权（设计文档 §7.3–§7.5）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/persistence/agent_release/model.py`、`sql.py`、`__init__.py`
- 新建：`backend/packages/harness/deerflow/persistence/skill_revision/model.py`、`sql.py`、`__init__.py`
- 新建：`persistence/migrations/versions/2026_07_XX_agent_releases.py`
- 测试：`backend/tests/test_agent_release_models.py`、`backend/tests/test_agent_release_repo.py`、`backend/tests/test_skill_revision_repo.py`

**表结构要点：**

```python
class AgentReleaseRow(Base):
    __tablename__ = "agent_releases"
    id: Mapped[str]                 # 内部 release_id，仅创建者控制平面可见
    agent_id: Mapped[str]           # index
    release_no: Mapped[int]         # 对创建者单调递增；UniqueConstraint(agent_id, release_no)
    agent_markdown / soul_markdown: Mapped[str]     # 完整快照
    model_name: Mapped[str]
    tool_groups_json: Mapped[list]
    quota_overrides_json: Mapped[dict]
    manifest_checksum: Mapped[str]  # 规范化清单 SHA-256
    created_by: Mapped[str]
    created_at: Mapped[datetime]
    # 无 updated_at —— 行创建后永不更新

class SkillRevisionRow(Base):
    __tablename__ = "skill_revisions"
    id: Mapped[str]
    skill_name: Mapped[str]
    owner_user_id: Mapped[str | None]   # None = 平台公开 skill
    visibility: Mapped[str]             # public|private
    content_checksum: Mapped[str]       # SKILL.md + 附属文件的规范化校验和
    content_ref: Mapped[str]            # 指向不可变内容存储（见 F1.3）
    declared_connector_caps_json: Mapped[list]
    created_at: Mapped[datetime]
    # UniqueConstraint(skill_name, owner_user_id, content_checksum) —— 内容不变则复用 revision

class AgentReleaseSkillRow(Base):
    __tablename__ = "agent_release_skills"
    # (release_id, skill_revision_id) 复合主键

class AgentReleaseConnectorGrantRow(Base):
    __tablename__ = "agent_release_connector_grants"
    # (release_id, connector_instance_id, capability) 复合主键；引用 connector_instances.id，不嵌入密钥
```

**开发任务：**

- 写失败测试：表存在性 + `agent_releases` 无 `updated_at`、`(agent_id, release_no)` 唯一
- 实现两组 `model.py`
- 写失败测试 `test_agent_release_repo.py`：`create_release()` 后调用任何 update 方法应不存在（仓储只提供 `create/get/list_by_agent/get_by_release_no`）；`release_no` 在并发创建时不重号（同事务内 `SELECT MAX(release_no)+1` 或数据库序列）
- 写失败测试 `test_skill_revision_repo.py`：同名同 checksum 复用已有 revision；checksum 变化产生新 revision；私有 skill 记录 `owner_user_id`
- 实现两组 `sql.py` 仓储
- 编写 Alembic 迁移
- 跑测试通过，Commit：`feat(m1): add immutable agent_releases & skill_revisions`

**验收标准：**

1. Release 行创建后无任何代码路径可修改（仓储不暴露 update；测试用 `dir(repo)` 断言）。
2. 同一 Agent 连续发布 3 次得到 `release_no` = 1,2,3。
3. Skill 内容未变时重复发布复用同一 `skill_revision_id`；内容变更后生成新 revision，旧 Release 的关联不受影响。
4. `agent_release_connector_grants` 中只有 `connector_instance_id` 引用，无任何密钥字段。

## F1.3 不可变内容存储抽象（Skill Revision 快照）

**功能描述：** 带版本的内容存储接口，保存 Skill revision 的文件快照；初期本地文件系统实现，接口预留对象存储（设计文档 §6.2）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/__init__.py`
- 新建：`backend/packages/harness/deerflow/publishing/content_store.py`
- 测试：`backend/tests/test_publishing_content_store.py`

**接口设计：**

```python
class ImmutableContentStore(Protocol):
    def put(self, namespace: str, checksum: str, files: dict[str, bytes]) -> str:
        """写入一组文件快照，返回 content_ref。相同 checksum 幂等复用。"""
    def get(self, content_ref: str) -> dict[str, bytes]: ...
    def exists(self, content_ref: str) -> bool: ...

class LocalContentStore:
    """存储在 {base_dir}/content-store/{namespace}/{checksum[:2]}/{checksum}/ 下，写入后只读。"""
```

**开发任务：**

- 写失败测试：put→get round-trip；同 checksum 重复 put 幂等；get 不存在的 ref 抛 KeyError；写入后目录内文件修改不影响已返回的 ref 语义（重新 put 校验 checksum 不匹配时报错）
- 实现 `LocalContentStore`（原子写：临时目录 + `os.rename`）
- 提供 `get_content_store()` 工厂（读取 `config.yaml` 可选 `publishing.content_store` 配置，缺省本地实现）
- 跑测试通过，Commit：`feat(m1): add immutable content store abstraction`

**验收标准：**

1. put 是原子的：写入中断不会留下可被 get 到的半成品。
2. 相同内容重复 put 不产生重复目录。
3. 接口不含任何本地路径语义（`content_ref` 为不透明字符串），可替换为对象存储实现。

## F1.4 草稿服务与 Gateway 草稿编辑 API

**功能描述：** 结构化草稿服务（创建 Agent、编辑 AGENT.md/SOUL.md、选择模型/工具组/Skill、授予 Connector），作为 Studio 与对话式编写的唯一事实来源（设计文档 §6.1、§8.1、§16.3）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/draft_service.py`
- 新建：`backend/app/gateway/routers/published_agents.py`
- 修改：`backend/app/gateway/app.py`（注册路由）
- 修改：`backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py`、`update_agent_tool.py`（改为调用 DraftService 落盘）
- 测试：`backend/tests/test_draft_service.py`、`backend/tests/test_published_agents_router.py`

**API 契约（全部要求浏览器会话认证 + CSRF，复用现有 `app/gateway/routers/agents.py` 的认证依赖）：**


| 方法    | 路径                                                    | 说明                                                                                                                                            |
| ----- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| POST  | `/api/published-agents`                               | 创建 Agent 身份 + 空草稿；body: `{slug, display_name, description?}`                                                                                  |
| GET   | `/api/published-agents`                               | 列出当前用户全部 Agent（含状态、current_release 摘要）                                                                                                        |
| GET   | `/api/published-agents/{agent_id}`                    | Agent 详情 + 草稿内容                                                                                                                               |
| PATCH | `/api/published-agents/{agent_id}/draft`              | 更新草稿；body 必须带 `revision`；支持部分字段：`agent_markdown`、`soul_markdown`、`model_name`、`tool_groups`、`skills[]`、`connector_grants[]`、`quota_overrides` |
| POST  | `/api/published-agents/{agent_id}/archive`            | 归档                                                                                                                                            |
| POST  | `/api/published-agents/{agent_id}/suspend` / `resume` | 暂停/恢复（不删任何数据）                                                                                                                                 |


**开发任务：**

- 写失败测试 `test_draft_service.py`：创建、部分更新、revision 冲突返回 409 语义错误、skill 选择校验（不存在/非本人私有 skill 被拒）、connector 授权校验（connector 不属于 owner 被拒）
- 实现 `DraftService`（组合 F1.1 仓储 + 现有 `deerflow.skills.load_skills()` 做 skill 归属校验 + `persistence/connector` 仓储做 connector 归属校验）
- 写失败测试 `test_published_agents_router.py`：用 FastAPI TestClient 覆盖上表 6 个端点的 200/403/404/409 路径；用户 A 不能读写用户 B 的 Agent
- 实现 `published_agents.py` 路由并注册
- 改造 `setup_agent_tool.py` / `update_agent_tool.py`：写入走 `DraftService`（保持工具对模型的 schema 不变；文件系统旧路径仅在迁移窗口内保留读取兼容）
- 跑测试 + `make lint`，Commit：`feat(m1): draft service and published-agents CRUD API`

**验收标准：**

1. 保存草稿对任何已发布行为零影响（此时还没有 Release 概念介入，仅断言草稿表更新不触碰 `current_release_id`）。
2. 两个并发 PATCH 只有一个成功，另一个收到 revision 冲突。
3. 选择他人私有 Skill、引用他人 Connector 实例均返回 403/422，且错误信息不暴露资源是否存在。
4. 对话式 `setup_agent` 创建的 Agent 与结构化 API 创建的 Agent 在数据库中形态一致，不存在"仅文件系统 Agent"。

## F1.5 发布服务：校验、快照、原子切换

**功能描述：** 发布校验 → Skill revision 锁定 → 创建不可变 Release → 原子设置 `current_release_id`（设计文档 §6.1 发布服务、§8.2）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/publish_service.py`
- 新建：`backend/packages/harness/deerflow/publishing/validation.py`
- 修改：`backend/app/gateway/routers/published_agents.py`（新增发布相关端点）
- 测试：`backend/tests/test_publish_validation.py`、`backend/tests/test_publish_service.py`

**API 契约：**


| 方法   | 路径                                                       | 说明                                       |
| ---- | -------------------------------------------------------- | ---------------------------------------- |
| POST | `/api/published-agents/{agent_id}/releases`              | 发布当前草稿；成功返回 `{release_no, published_at}` |
| GET  | `/api/published-agents/{agent_id}/releases`              | Release 历史（仅 owner）                      |
| GET  | `/api/published-agents/{agent_id}/releases/{release_no}` | 单个 Release 快照详情（用于对比）                    |
| POST | `/api/published-agents/{agent_id}/rollback`              | body: `{release_no}`，原子回滚指针              |


**校验规则（validation.py，逐条对应设计文档 §8.2，全部违规聚合返回而非首错即断）：**

```python
@dataclass
class PublishViolation:
    code: str        # e.g. "EMPTY_INSTRUCTIONS", "SKILL_NOT_FOUND", "CONNECTOR_NOT_GRANTED"
    message: str
    field: str | None

def validate_draft_for_publish(draft, *, owner_user_id, skills_index, connector_repo, model_index, platform_quota) -> list[PublishViolation]:
    # 1. agent_markdown 与 soul_markdown 至少一个非空（strip 后）
    # 2. 指令大小上限（默认单文件 <= 200KB，可配置 publishing.max_instruction_bytes）
    # 3. model_name 对该 owner 可用（复用 models 路由的可用模型解析）
    # 4. 每个选中 skill：存在、enabled、public 或 owner 私有
    # 5. skill 声明的 connector 能力 ⊆ 草稿 connector_grants
    # 6. 每个 connector_instance 仍属于 owner 且状态有效
    # 7. tool_groups 均在 config.yaml tool_groups 白名单内
    # 8. quota_overrides 每项 <= 平台硬上限
```

**开发任务：**

- 写失败测试 `test_publish_validation.py`：上面 8 条规则各至少 1 个正例 + 1 个反例；多条违规同时返回
- 实现 `validation.py`
- 写失败测试 `test_publish_service.py`：
  - 发布成功后 `agent_releases` 新行 + `current_release_id` 指向它 + status 变为 `published`
  - Skill 快照锁定：发布后修改 skill 文件内容，`agent_release_skills` 指向的 revision checksum 不变
  - 指针切换原子性：模拟 Release 创建成功但指针更新失败时整体回滚（单事务）
  - 回滚：指向历史 release_no，历史行未被修改（checksum 不变）
  - 回滚到不存在的 release_no 返回 404
- 实现 `PublishService.publish(agent_id, owner_user_id)`：单数据库事务内完成（读草稿 → 校验 → 为每个 skill 计算 checksum 并 upsert revision + content store 快照 → 插入 Release 行与子表 → 更新指针与状态）
- 实现 `PublishService.rollback(agent_id, owner_user_id, release_no)`
- 路由接线 + TestClient 测试
- 跑测试，Commit：`feat(m1): publish service with validation, immutable releases, rollback`

**验收标准：**

1. 违反 §8.2 任一条件时发布被拒，响应逐条列出违规项，且线上状态完全不变。
2. Agent 可在没有任何飞书绑定和 API Key 的情况下发布成功（对应总验收 #6 前半）。
3. 发布/回滚后再次发布，`release_no` 继续递增，不复用历史号。
4. Release 详情 API 仅 owner 可访问；他人访问返回不暴露存在性的 404。
5. "只有 AGENT.md"、"只有 SOUL.md"、"两者都有" 三种草稿均可发布（对应总验收 #2）。

## F1.6 指令拼接：AGENT.md + SOUL.md Prompt 区块

**功能描述：** 运行时按"先 AGENT.md 后 SOUL.md"顺序拼接，放入带标签的 Prompt 区块（设计文档 §3.3）。本功能先在 harness 落基础函数，M2 的 Resolver 直接复用。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/instructions.py`
- 测试：`backend/tests/test_publish_instructions.py`

**实现：**

```python
def compose_agent_instructions(agent_markdown: str, soul_markdown: str) -> str:
    """拼接顺序固定：AGENT.md 在前，SOUL.md 在后；空文件跳过对应区块。"""
    blocks = []
    if agent_markdown.strip():
        blocks.append(f"<agent_instructions>\n{agent_markdown.strip()}\n</agent_instructions>")
    if soul_markdown.strip():
        blocks.append(f"<agent_soul>\n{soul_markdown.strip()}\n</agent_soul>")
    if not blocks:
        raise ValueError("at least one of AGENT.md / SOUL.md must be non-empty")
    return "\n\n".join(blocks)
```

**开发任务：**

- 写失败测试：仅 AGENT / 仅 SOUL / 两者 / 全空抛错 / 顺序断言（AGENT 区块 index < SOUL 区块 index）
- 实现并跑通
- Commit：`feat(m1): instruction composition with labeled prompt blocks`

**验收标准：** 四种输入组合行为与设计一致；顺序有测试锁定。

## F1.7 存量自定义 Agent 迁移导入

**功能描述：** 把 `users/{user_id}/agents/{name}/`（`SOUL.md` + `config.yaml`）列为候选并导入为 `published_agents` + `agent_drafts`，不静默发布（设计文档 §17）。

**涉及文件：**

- 新建：`backend/scripts/migrate_published_agents.py`
- 新建：`backend/app/gateway/routers/published_agents.py` 内新增 `POST /api/published-agents/import` 与 `GET /api/published-agents/import/candidates`
- 测试：`backend/tests/test_agent_import.py`

**开发任务：**

- 写失败测试：给定临时目录中的旧版 agent（SOUL.md + config.yaml），candidates 列出它；import 后草稿字段映射正确（SOUL→soul_markdown、config 的 model/tools→草稿字段、AGENT.md 缺失时 agent_markdown 为空）；skill 名无法解析时出现在报告的 `unresolved_skills` 中；重复导入被拒
- 实现导入服务（读取 `deerflow/config/agents_config.py` 现有解析逻辑，勿重写解析器）
- 实现 CLI 脚本（支持 `--dry-run`、`--user-id`，风格对齐现有 `scripts/migrate_user_isolation.py`）
- 跑测试，Commit：`feat(m1): import legacy custom agents as drafts`

**验收标准：**

1. 导入产物一律是 `status=draft` 且 `current_release_id=NULL`；必须 owner 显式发布才上线。
2. 无法解析的 Skill 不阻断导入，但在响应/报告中明确列出。
3. 迁移窗口内旧文件系统 Agent 的对话运行不受影响（不删除旧目录）。

## M1 Review Gate

- `cd backend && make test` 全绿；`make lint` 无错误
- 手工冒烟：curl 走完 创建→编辑→发布→改草稿→确认线上指针不变→回滚 全流程
- 更新 `backend/CLAUDE.md`（新增 persistence 实体、publishing 模块、published-agents 路由说明）
- Code review 通过后进入 M2

---

# 里程碑 M2：已发布运行时与 Agent API

**目标：** 外部调用方通过 Agent 专属 API Key 访问已发布 Agent：`PublishedAgentResolver` + 无记忆运行时 + Agent API Key + 对外 API 外观层 + 配额/用量/幂等。

**依赖：** M1 全部完成。

## F2.1 `PublishedAgentContext` 与 `PublishedAgentResolver`

**功能描述：** 每个外部请求解析稳定 Agent → 读 `current_release_id` → 构造受信任上下文（设计文档 §6.4）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/context.py`
- 新建：`backend/packages/harness/deerflow/publishing/resolver.py`
- 测试：`backend/tests/test_published_agent_resolver.py`

**核心类型（context.py）：**

```python
@dataclass(frozen=True)
class PublishedAgentContext:
    owner_user_id: str
    agent_id: str
    release_id: str                      # 内部使用，绝不序列化到对外响应
    source: Literal["api", "feishu"]
    credential_id: str                   # api key id 或 channel binding id
    external_actor: str                  # 外部主体标识（key 主体 / 飞书 open_id 哈希）
    conversation_scope: str
    skill_revision_ids: tuple[str, ...]
    connector_capabilities: tuple[tuple[str, str], ...]   # (connector_instance_id, capability)
    tool_groups: tuple[str, ...]
    model_name: str
    instructions: str                    # compose_agent_instructions() 结果
    effective_quota: "EffectiveQuota"
    correlation_id: str
    idempotency_key: str | None
    memory_enabled: bool = False         # 常量 False，禁止构造为 True

    def __post_init__(self):
        if self.memory_enabled:
            raise ValueError("published agent runtime must be memory-free")
```

**Resolver 行为（resolver.py）：**

```python
class PublishedAgentResolver:
    def resolve(self, agent_id: str, *, source, credential_id, external_actor, ...) -> PublishedAgentContext:
        # 1. 加载 agent；status != "published" 或 current_release_id 为空 → AgentNotAvailableError
        # 2. status == "suspended"/"archived" → AgentSuspendedError（供 API 层映射 410）
        # 3. 加载 Release + skills + connector grants
        # 4. Connector 能力交集：release 授权 ∩ connector 当前状态有效（撤销立即生效，即使 Release 不可变）
        # 5. compose_agent_instructions(...)
        # 6. 组装 EffectiveQuota（F2.5）
```

**开发任务：**

- 写失败测试：published+有指针→成功；draft/无指针→AgentNotAvailable；suspended→AgentSuspended；connector 实例被 owner 撤销后能力从上下文中消失（Release 行不变）；`memory_enabled=True` 构造抛错；context 冻结不可变
- 实现 context.py 与 resolver.py
- 跑测试，Commit：`feat(m2): PublishedAgentResolver and trusted context`

**验收标准：**

1. 上下文的每个字段只能由服务端数据推导，构造函数不接受任何来自请求体的直接透传值（由 API 层测试双重保证）。
2. Connector 撤销即时生效（对应设计 §7.5"安全覆盖"与总验收 #5）。
3. 正在执行中的 Run 持有创建时的 context（release 固定），新 Run 解析新指针 —— 在 F2.3 集成测试中断言。

## F2.2 无记忆运行时策略与管理工具过滤

**功能描述：** 外部来源 Run 禁用记忆读写与注入、排除管理类工具（设计文档 §11、§13.3）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/runtime_policy.py`
- 修改：`backend/packages/harness/deerflow/agents/lead_agent/agent.py`（`_build_middlewares` 支持 published 模式：跳过 `MemoryMiddleware`；`apply_prompt_template` 不注入 `<memory>`）
- 修改：`backend/packages/harness/deerflow/tools/__init__.py` 的 `get_available_tools()`（新增 `published_context` 参数：排除 `setup_agent`/`update_agent`/skill 管理/connector 管理工具；工具组取 Release 策略交集）
- 测试：`backend/tests/test_memoryless_runtime_policy.py`

**开发任务：**

- 写失败测试：
  - published 模式下构建的中间件链不含 `MemoryMiddleware`
  - 系统 Prompt 不含 `<memory>` 区块、不含 owner `USER.md` 内容
  - 工具列表不含 `setup_agent`、`update_agent`、`task`（若 Release 工具组未授权）及任何 mcp/skill 管理工具
  - 工具列表 = 平台白名单 ∩ Release tool_groups ∩ skill 声明要求（多白名单求交）
  - 入站 `config.configurable` 里伪造 `memory_enabled/model_name/skills` 等字段全部被忽略
- 实现 `runtime_policy.py`（`build_published_run_config(context) -> RunnableConfig`，把 context 放入 `configurable["published_agent_context"]`，并显式覆写 memory/subagent/tool 相关开关）
- 改造 `_build_middlewares` 与 `get_available_tools`
- 跑全量测试（确认既有 Web UI 路径行为不变），Commit：`feat(m2): memory-free published runtime policy`

**验收标准：**

1. 外部 Run 全程零记忆读写：`MemoryMiddleware` 不在链上、`memory/queue` 不被 enqueue（测试用 mock 断言）。
2. Web UI 常规线程运行行为与改造前完全一致（回归：现有 `make test` 全绿）。
3. 外部字段无法覆盖模型、Skill、Connector、owner、Release、记忆策略（对应设计 §18.3 第 2 条）。

## F2.3 Agent API Key：实体、哈希、轮换、撤销

**功能描述：** 绑定到单个 Agent 的多具名 Key（设计文档 §7.7、§9.1）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/persistence/agent_api_key/model.py`、`sql.py`、`__init__.py`
- 新建：`persistence/migrations/versions/2026_07_XX_agent_api_keys.py`
- 新建：`backend/app/gateway/routers/published_agent_keys.py`
- 测试：`backend/tests/test_agent_api_key_repo.py`、`backend/tests/test_published_agent_keys_router.py`

**表结构要点（复用 `persistence/api_key/model.py` 的哈希/前缀方案，但独立成表，一个 Agent 允许多个 active Key）：**

```python
class AgentAPIKeyRow(Base):
    __tablename__ = "agent_api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)   # slow salted hash
    key_prefix: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # 用于识别，形如 dfa_xxxx
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|revoked|expired
    quota_overrides_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at / last_used_at / expires_at / revoked_at / rotation_of: ...
    # 注意：无 uq_active 唯一约束 —— 同一 Agent 可多个 active Key（区别于用户级 api_keys 表）
```

**API 契约（owner 会话认证）：**


| 方法    | 路径                                                      | 说明                                        |
| ----- | ------------------------------------------------------- | ----------------------------------------- |
| POST  | `/api/published-agents/{agent_id}/keys`                 | 创建；明文仅此响应返回一次                             |
| GET   | `/api/published-agents/{agent_id}/keys`                 | 列表（只含 prefix/last_four/状态/限额）             |
| POST  | `/api/published-agents/{agent_id}/keys/{key_id}/rotate` | 轮换：签发新 Key，旧 Key 进入重叠有效期（默认 24h，可配置）后自动过期 |
| POST  | `/api/published-agents/{agent_id}/keys/{key_id}/revoke` | 立即撤销                                      |
| PATCH | `/api/published-agents/{agent_id}/keys/{key_id}`        | 改名/限额                                     |


**开发任务：**

- 写失败测试（repo 层）：创建返回明文 + 落库只有哈希；`verify(plaintext) -> AgentAPIKeyRow` 按 prefix 定位后慢哈希比对；撤销后 verify 立即失败；轮换后新旧 Key 重叠期内均有效、重叠期后旧 Key 失效；一个 Agent 同时 3 个 active Key 共存
- 实现 model.py + sql.py + Alembic 迁移
- 写失败测试（router 层）：明文只出现一次；非 owner 访问 404；Key 永不能调用管理 API（用一个管理端点断言 Bearer Agent Key 被拒 401/403）
- 实现路由并注册
- 跑测试，Commit：`feat(m2): agent-scoped API keys with rotate/revoke`

**验收标准：**

1. 数据库中不存在任何明文 Key；明文创建后不可恢复（对应设计 §13.4）。
2. 多具名 Key 独立限额、轮换、撤销（对应总验收 #11）。
3. 撤销立即生效；轮换有重叠有效期（对应设计 §9.1）。

## F2.4 Agent 对外 API 外观层

**功能描述：** 面向单 Agent 的对外 API：安全元数据、Conversation、同步/SSE/异步 Run、取消、幂等（设计文档 §6.3、§9）。

**涉及文件：**

- 新建：`backend/app/gateway/routers/agent_public_api.py`
- 新建：`backend/app/gateway/external/agent_auth.py`（Bearer Agent Key 认证依赖）
- 新建：`backend/app/gateway/external/agent_serialization.py`（安全序列化：白名单字段输出）
- 修改：`backend/app/gateway/app.py`（注册路由）
- 修改：`persistence/external_conversation/model.py` 使用方式——外部会话按 `(agent_id, credential_id)` 作用域建 Conversation（新增迁移为该表加 `credential_id` 列，或复用 `source` 字段规范编码；实现时二选一，倾向加列）
- 测试：`backend/tests/test_agent_public_api_auth.py`、`backend/tests/test_agent_public_api_runs.py`、`backend/tests/test_agent_public_api_serialization.py`

**API 契约（前缀 `/api/v1/agents/{agent_id}`，认证 `Authorization: Bearer <agent-api-key>`）：**


| 方法   | 路径                                                   | 说明                                                                       |
| ---- | ---------------------------------------------------- | ------------------------------------------------------------------------ |
| GET  | `/api/v1/agents/{agent_id}`                          | 安全元数据：`{agent_id, display_name, description, avatar}`，无 Release/owner 信息 |
| POST | `/api/v1/agents/{agent_id}/conversations`            | 创建隔离 Conversation                                                        |
| GET  | `/api/v1/agents/{agent_id}/conversations/{cid}`      | Conversation 状态                                                          |
| POST | `/api/v1/agents/{agent_id}/conversations/{cid}/runs` | 异步创建 Run；支持 `Idempotency-Key` 头                                          |
| POST | `.../runs/wait`                                      | 同步等待                                                                     |
| POST | `.../runs/stream`                                    | SSE 流式                                                                   |
| GET  | `.../runs/{run_id}`                                  | 状态/结果                                                                    |
| POST | `.../runs/{run_id}/cancel`                           | 取消                                                                       |


**认证规则（agent_auth.py）：**

```python
async def require_agent_api_key(request, agent_id: str) -> AgentAPIKeyRow:
    # 1. 解析 Bearer；prefix 查表；慢哈希验证
    # 2. Key 状态必须 active（含轮换重叠期）
    # 3. Key.agent_id 必须 == 路径 agent_id；不匹配返回 404（不暴露资源存在性）
    # 4. 更新 last_used_at（节流写，比如 60s 一次）
```

**开发任务：**

- 写失败测试 `test_agent_public_api_auth.py`：无 Key 401；撤销 Key 401；A 的 Key 调 B 的路径 404；suspended Agent 410；未发布 Agent 404
- 实现 `agent_auth.py` 与元数据端点
- 写失败测试 `test_agent_public_api_runs.py`：创建 Conversation → 提交 Run（wait）→ 返回消息；同一 `Idempotency-Key` 重试返回同一 run_id 不新建（复用 `persistence/external_idempotency`）；多轮 Conversation 上下文延续；不同 credential 的 Conversation 相互不可见；cancel 生效
- 实现 Conversation/Run 端点：内部复用 `app/gateway/external/service.py` 的 Conversation→Thread 映射与 RunManager 调用链，Run 创建前经 F2.1 Resolver + F2.2 runtime_policy
- 写失败测试 `test_agent_public_api_serialization.py`：递归扫描全部响应 JSON，断言不含 `release`、`owner_user_id`、`soul_markdown`、`agent_markdown`、绝对路径、`secret` 等禁止字段（做一个通用的 `assert_safe_external_payload()` 测试助手，后续 M3 复用）
- 实现 `agent_serialization.py`（**白名单**输出而非黑名单过滤）
- SSE 流式端点 + 测试（对齐现有 `external.py` 的 SSE 实现方式）
- 跑测试，Commit：`feat(m2): agent public API facade (metadata/conversations/runs)`

**验收标准：**

1. 一个 Agent 的多个 Key 均可用；跨 Agent 使用严格 404（对应总验收 #10 相关与设计 §18.4）。
2. 响应永不含内部 Release 标识/版本号/owner/指令源码/私有 Skill 元数据/密钥/路径（对应设计 §9.3；由序列化测试助手机器保证）。
3. 同步、流式、异步、取消、幂等 5 条流程全部有测试覆盖。
4. 幂等重试不重复创建 Run（对应总验收 #13 API 部分）。

## F2.5 配额引擎：平台限额 + owner 覆盖 + 预留/结算

**功能描述：** 分层配额（平台硬上限 → owner 覆盖 → Key 覆盖），Run 创建前预留、终态结算/释放，超限 429（设计文档 §12）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/publishing/quota.py`
- 新建：`backend/packages/harness/deerflow/persistence/agent_usage/model.py`、`sql.py`、`__init__.py`（`agent_usage_records` + `agent_quota_reservations` 两张表）
- 新建：`persistence/migrations/versions/2026_07_XX_agent_usage_quota.py`
- 修改：`config.example.yaml`（新增 `publishing.platform_quota` 段：`max_concurrent_runs_per_agent`、`max_input_bytes`、`max_run_seconds`、`max_tokens_per_run`、`inbound_rps`、`daily_runs_default` 等；bump `config_version`）
- 测试：`backend/tests/test_quota_inheritance.py`、`backend/tests/test_quota_reservation.py`

**核心逻辑（quota.py）：**

```python
@dataclass(frozen=True)
class EffectiveQuota:
    max_concurrent_runs: int
    daily_runs: int
    daily_tokens: int
    max_run_seconds: int
    max_tokens_per_run: int

def resolve_effective_quota(platform: PlatformQuota, owner_overrides: dict, key_overrides: dict) -> EffectiveQuota:
    """每项取 min(平台硬上限, owner 覆盖 or 平台默认, key 覆盖 or 上一层)；未设置继承，绝不表示无限制。"""

class QuotaLedger:
    def reserve(self, context, *, request_key: str) -> Reservation:
        """检查并预留；超限抛 QuotaExceededError(retry_after)。request_key 幂等：同 key 重复 reserve 返回原预留。"""
    def settle(self, reservation_id: str, *, tokens_used: int, status: str) -> None: ...
    def release(self, reservation_id: str) -> None: ...
```

**开发任务：**

- 写失败测试 `test_quota_inheritance.py`：未设置继承平台默认；owner 覆盖更严格生效；owner 试图超过平台硬上限时取硬上限；Key 覆盖再收紧
- 实现 `resolve_effective_quota`
- 写失败测试 `test_quota_reservation.py`：并发预留不超过 `max_concurrent_runs`（线程池模拟）；日配额耗尽后 reserve 抛 429 语义错误且不产生 Run；同 `request_key` 重复 reserve 幂等；成功/取消/超时/失败四种终态都恰好结算一次（重复 settle 幂等）
- 实现两张表 + `QuotaLedger`（预留行带唯一 `request_key` 约束实现幂等）
- 在 F2.4 的 Run 创建路径接入：reserve → 创建 Run → 终态回调 settle/release；接入点写在 `agent_public_api.py` 的 run 创建函数与 run 完成回调
- API 层测试：超限返回 `429` + `Retry-After` 头
- 跑测试，Commit：`feat(m2): layered quota engine with idempotent reservation`

**验收标准：**

1. 被拒请求不创建 Run、不消耗模型配额（对应设计 §12.3、总验收 #12）。
2. 预留在四种终态都被结算或释放，无泄漏（测试断言 reservations 表终态无 `pending` 残留）。
3. 重试不重复计费（对应总验收 #13）。

## F2.6 用量记账与审计

**功能描述：** 每个外部 Run 恰好一条用量记录，审计含双主体（设计文档 §7.9、§13.1、§15）。

**涉及文件：**

- 修改：`backend/packages/harness/deerflow/persistence/agent_usage/model.py`（`agent_usage_records` 表：owner/agent/source/credential_id/external_actor_hash/conversation_id/run_id/model/tokens/latency_ms/status/error_class/idempotency_key/correlation_id/created_at；`UniqueConstraint(run_id)` 保证恰好一次）
- 修改：`backend/app/gateway/external/audit.py`（扩展支持 agent 来源审计事件）
- 新建：`backend/app/gateway/routers/published_agents.py` 内 `GET /api/published-agents/{agent_id}/usage`（owner 用量视图，按日聚合）
- 测试：`backend/tests/test_agent_usage_accounting.py`

**开发任务：**

- 写失败测试：Run 成功/失败/取消/超时均产生 1 条记录；同 run_id 重复写入被唯一约束拒绝且不报错（upsert-ignore）；记录含 external_actor 的**哈希**而非原文；owner 用量视图只见自己 Agent
- 实现记账写入（挂在 F2.5 settle 路径上）与 owner 查询端点
- 结构化日志：为外部 Run 的日志上下文注入 `agent_id`/`correlation_id`（`release_id` 仅入受信日志，不入对外响应——复用现有 logging 配置方式）
- 跑测试，Commit：`feat(m2): usage accounting and dual-principal audit`

**验收标准：**

1. 用量精确一次，重试不重复（联动 F2.5）。
2. owner principal 与 external actor principal 在审计记录中分离，且入站字段无法设置这两个身份（对应设计 §13.1）。
3. 用量归属 Agent 拥有者（对应设计 §3.14）。

## M2 Review Gate

- `make test` 全绿，重点回归：现有 Web UI 线程运行、External API V1 兼容不破坏
- 安全专项自查（对照设计 §13）：密钥脱敏、白名单序列化、双主体、跨 Agent 拒绝
- 手工冒烟：创建 Key → curl 走同步/SSE/异步/幂等重试/撤销后 401 全流程
- 更新 `backend/CLAUDE.md` 与 `backend/docs/API.md`
- Code review 通过后进入 M3

---

# 里程碑 M3：多 Agent 飞书 Supervisor

**目标：** DB 驱动的飞书绑定：每个已发布 Agent 可绑定独立飞书应用，动态启停不重启 Gateway，事件去重 + 会话映射持久化 + 配额路由。

**依赖：** M2 完成（Resolver、配额、用量均被本里程碑复用）。

## F3.1 `agent_channels` 实体与密钥引用

**功能描述：** 绑定到 Agent 的稳定渠道对象，密钥只存加密引用（设计文档 §7.6、§6.2）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/persistence/agent_channel/model.py`、`sql.py`、`__init__.py`
- 新建：`backend/packages/harness/deerflow/publishing/secret_store.py`（`SecretStore` 协议 + `LocalEncryptedSecretStore` 实现：Fernet 对称加密，密钥来自环境变量 `DEER_FLOW_SECRET_STORE_KEY`）
- 新建：`persistence/migrations/versions/2026_07_XX_agent_channels.py`
- 测试：`backend/tests/test_agent_channel_repo.py`、`backend/tests/test_secret_store.py`

**表结构要点：**

```python
class AgentChannelRow(Base):
    __tablename__ = "agent_channels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False, default="feishu")
    app_id: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(128), nullable=False)   # 不透明引用，绝非明文
    connection_mode: Mapped[str] = mapped_column(String(16), default="websocket")
    status: Mapped[str] = mapped_column(String(16), default="inactive", index=True)  # inactive|active|error
    health: Mapped[str] = mapped_column(String(16), default="unknown")     # unknown|healthy|unhealthy
    health_detail: Mapped[str | None] = mapped_column(String(512))
    created_at / updated_at / last_started_at: ...

    __table_args__ = (
        # 一个 Agent 至多一个 active 的 feishu 绑定
        Index("uq_agent_channels_active", "agent_id", "channel_type", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
    )
```

**开发任务：**

- 写失败测试 `test_secret_store.py`：put 返回 ref；get(ref) 还原明文；数据库/日志中查不到明文（存储介质检查）；错误 ref 抛 KeyError
- 实现 `secret_store.py`
- 写失败测试 `test_agent_channel_repo.py`：CRUD；同 Agent 第二个 active 绑定被唯一索引拒绝；停用后可再建
- 实现实体 + 仓储 + 迁移
- 跑测试，Commit：`feat(m3): agent_channels entity and encrypted secret store`

**验收标准：**

1. 数据库行内只有 `secret_ref`，任何查询都不能拿到明文（对应设计 §13.4）。
2. 一个 Agent 零或一个激活中的飞书绑定（对应设计 §7.6）。

## F3.2 Feishu Supervisor：动态生命周期

**功能描述：** 把配置文件驱动的 `ChannelService` 演进为 DB 驱动 Supervisor：按绑定启动/停止/重启 `FeishuChannel` 实例，互不影响，不重启 Gateway（设计文档 §6.3）。

**涉及文件：**

- 新建：`backend/app/channels/supervisor.py`
- 修改：`backend/app/channels/feishu.py`（`FeishuChannel` 构造参数化：接受 `app_id/app_secret/binding_id/agent_id`，移除对全局 config 的直接读取；保留旧构造路径以兼容存量 config.yaml 渠道）
- 修改：`backend/app/gateway/app.py`（lifespan 启动 Supervisor：加载全部 `status=active` 绑定）
- 新建：`backend/app/gateway/routers/published_agent_channels.py`（owner 管理 API）
- 测试：`backend/tests/test_feishu_supervisor.py`、`backend/tests/test_agent_channels_router.py`

**API 契约（owner 会话认证）：**


| 方法     | 路径                                                     | 说明                                                                  |
| ------ | ------------------------------------------------------ | ------------------------------------------------------------------- |
| POST   | `/api/published-agents/{agent_id}/channels`            | 创建绑定；body: `{app_id, app_secret}`（secret 即刻入 SecretStore，只回 ref 状态） |
| POST   | `.../channels/{binding_id}/test`                       | 连接测试（调飞书 tenant_access_token 探活），只返回健康状态不回显密钥                       |
| POST   | `.../channels/{binding_id}/start` / `stop` / `restart` | 生命周期                                                                |
| PATCH  | `.../channels/{binding_id}`                            | 轮换凭据（新 secret 入库 → 重启实例）                                            |
| DELETE | `.../channels/{binding_id}`                            | 删除（先 stop）                                                          |
| GET    | `.../channels`                                         | 绑定列表 + 健康状态                                                         |


**Supervisor 结构：**

```python
class FeishuSupervisor:
    """维护 binding_id -> RunningChannel 的注册表。"""
    async def start_binding(self, binding_id: str) -> None      # 从 DB 读绑定 + SecretStore 取 secret + 实例化 FeishuChannel.start()
    async def stop_binding(self, binding_id: str) -> None
    async def restart_binding(self, binding_id: str) -> None
    async def load_active_bindings(self) -> None                 # Gateway 启动时调用
    def health(self) -> dict[str, BindingHealth]                 # 每绑定健康态
    # 单个绑定 start 失败：记录 health=unhealthy，不抛出、不影响其他绑定
```

**开发任务：**

- 写失败测试 `test_feishu_supervisor.py`（用 fake Channel 类注入）：启动 2 个绑定→都 running；stop 其一→另一个不受影响；start 失败的绑定标记 unhealthy 且不阻断 `load_active_bindings`；restart 换新实例；凭据轮换后新实例拿到新 secret
- 实现 `supervisor.py`
- 改造 `feishu.py` 构造参数化（回归：存量 config.yaml 单飞书渠道路径 `test_channels`* 相关现有测试不破坏）
- 实现管理路由 + TestClient 测试（含：创建绑定后 secret 不出现在任何响应中）
- Gateway lifespan 接线
- 跑测试，Commit：`feat(m3): DB-driven feishu supervisor with per-binding lifecycle`

**验收标准：**

1. 新增/启停/重启单个绑定全程不重启 Gateway、不影响其他绑定（对应总验收 #14）。
2. 集成失败只标记不健康，不取消 Agent 发布（对应设计 §8.4）。
3. 凭据轮换走 SecretStore，全链路无明文外泄。

## F3.3 事件校验、去重与持久化会话映射

**功能描述：** 签名校验、重放拒绝、事件级去重；会话映射从全局 JSON 迁移到数据库（设计文档 §6.3、§7.8）。

**涉及文件：**

- 新建：`backend/packages/harness/deerflow/persistence/channel_mapping/model.py`、`sql.py`、`__init__.py`（`channel_conversation_mappings` 表 + `channel_event_dedup` 表）
- 新建：`persistence/migrations/versions/2026_07_XX_channel_mappings.py`
- 修改：`backend/app/channels/feishu.py`（事件入口接入去重）
- 修改：`backend/app/channels/store.py`（新增 DB 实现 `DbMappingStore`，与现有 JSON `store` 同接口；绑定驱动的渠道一律用 DB 实现）
- 测试：`backend/tests/test_channel_mapping_store.py`、`backend/tests/test_feishu_event_dedup.py`

**映射规则（表唯一键）：**

```
channel_conversation_mappings:
  binding_id + chat_id + feishu_user_id           -> thread_id   (私聊：按用户隔离)
  binding_id + chat_id [+ topic_id]               -> thread_id   (群聊：群共享；有话题则按话题)
  UniqueConstraint(binding_id, chat_id, actor_scope, topic_id)
  # 映射永不跨 binding / 跨 agent 复用

channel_event_dedup:
  (binding_id, event_id) 唯一；带 created_at，用于 TTL 清理（默认保留 72h）
```

**开发任务：**

- 写失败测试 `test_channel_mapping_store.py`：私聊两个用户各得独立 thread；群聊同群成员共享 thread；话题群按 topic 隔离；相同 chat 在两个 binding 下映射不同 thread；并发 get_or_create 同 key 只建一个（唯一约束 + 重读）
- 实现映射表 + `DbMappingStore`
- 写失败测试 `test_feishu_event_dedup.py`：同 event_id 第二次投递被丢弃（在配额预留之前）；无效签名拒绝；过期时间戳（重放）拒绝
- 在 `feishu.py` 事件入口实现 verify → dedup → resolve binding → mapping → F2.1 Resolver → F2.5 reserve → Run 的完整顺序（顺序有测试锁定：dedup 必须先于 reserve）
- 跑测试，Commit：`feat(m3): persistent conversation mappings and event dedup`

**验收标准：**

1. 私聊/群聊/话题三种映射隔离正确（对应总验收 #8 前半、设计 §18.5）。
2. 飞书事件重试不产生重复 Run、不重复计费（对应总验收 #13 飞书部分）。
3. 多进程/多副本下映射一致（DB 唯一约束兜底，JSON 文件不再是事实来源）。

## F3.4 飞书运行链路：配额路由与响应投递

**功能描述：** 飞书消息走 published 运行链路：限流 → Resolver → 无记忆 Run → 流式卡片/最终响应 → 用量记账（设计文档 §10）。

**涉及文件：**

- 修改：`backend/app/channels/feishu.py`（接 F2 组件；复用现有流式卡片 patch 机制）
- 修改：`backend/app/channels/manager.py`（新增 published-agent 分发路径：绑定消息不再走 config.yaml 渠道的 default agent，而是携带 `agent_id` + `binding_id` 的 context）
- 测试：`backend/tests/test_feishu_published_run_flow.py`

**开发任务：**

- 写失败测试（mock 运行时 + fake 飞书 API）：
  - 正常消息 → resolve → run → 最终响应回投
  - Agent 未发布 → 通用不可用提示（不含内部细节）
  - 配额超限 → 友好繁忙提示且无 Run 创建
  - 运行超时 → 请稍后重试提示 + 预留释放
  - Run 成功后 `agent_usage_records` 恰好 1 条、source=feishu
  - 全流程无记忆读写（mock MemoryQueue 断言零调用）
- 实现分发路径与错误消息映射（对照设计 §14 错误表逐行实现飞书列）
- 跑测试，Commit：`feat(m3): feishu published-agent run flow with quota & usage`

**验收标准：**

1. 飞书用户无需 DeerFlow 账号即可使用（对应设计 §10 末段）。
2. 错误提示永不含 owner ID、Release ID、内部路径、堆栈（对应设计 §14；用 F2.4 的 `assert_safe_external_payload()` 助手扫描出站文本）。
3. 重新发布/回滚不改变机器人身份与既有映射（对应总验收 #9；测试：回滚前后同 chat 映射的 thread_id 不变）。

## M3 Review Gate

- `make test` 全绿；存量 config.yaml 渠道（Slack/Telegram/DingTalk/旧版飞书）回归不破坏
- 手工冒烟（需真实飞书测试应用）：两个 Agent 各绑一个飞书 app → 私聊/群聊 → 停用其一验证另一个存活 → 凭据轮换
- 更新 `backend/CLAUDE.md` IM Channels 章节
- Code review 通过后进入 M4

---

# 里程碑 M4：Agent Studio 与运维（前端 + 收尾）

**目标：** owner 可视化完成全生命周期：Gallery 控制台、Studio 编辑器、发布/回滚 UI、发布后集成配置、用量/健康/审计视图。

**依赖：** M1–M3 API 全部就绪。

**前端通用要求：** 遵循 `frontend/CLAUDE.md`：Server Components 默认、TanStack Query 管理服务端状态、`@/` 别名、i18n 双语（`src/core/i18n` en-US + zh-CN 同步补齐）、单测放 `tests/unit/` 镜像目录、E2E 放 `tests/e2e/`（`page.route()` mock 后端）。

## F4.1 API 客户端层与类型

**涉及文件：**

- 新建：`frontend/src/core/published-agents/types.ts`（Agent/Draft/Release/Key/Channel/Usage 的 TS 类型，与后端 Pydantic 响应一一对应）
- 新建：`frontend/src/core/published-agents/api.ts`（fetch 封装，走 `NEXT_PUBLIC_BACKEND_BASE_URL`）
- 新建：`frontend/src/core/published-agents/hooks.ts`（`usePublishedAgents`、`useAgentDraft`、`usePublishAgent`、`useAgentKeys`、`useAgentChannels`、`useAgentUsage` 等 TanStack Query hooks）
- 测试：`frontend/tests/unit/core/published-agents/api.test.ts`

**开发任务：**

- 定义类型 + API 封装 + hooks（mutation 后 invalidate 相应 query key）
- 单测：API 封装的 URL/方法/错误处理（mock fetch）
- Commit：`feat(m4): published-agents API client and hooks`

**验收标准：** 类型与后端契约一致；409（revision 冲突）、429（配额）有专属错误类型供 UI 分支。

## F4.2 Agent Gallery（owner 控制台）

**涉及文件：**

- 修改：`frontend/src/app/workspace/agents/page.tsx` 及同目录组件（演进现有 Gallery）
- 新建：`frontend/src/components/workspace/published-agents/agent-card.tsx`
- 测试：`frontend/tests/e2e/published-agents-gallery.spec.ts`

**开发任务：**

- 卡片展示：状态徽章（draft/published/suspended/archived）、当前 Release 摘要（release_no + 发布时间，仅 owner 可见）、激活集成图标（API Key 数 / 飞书绑定）、近 7 日用量迷你图、健康状态
- 操作：新建 Agent、进入 Studio、暂停/恢复、归档
- 无公开市场动作（不出现分享/上架按钮）
- E2E：mock 列表 API → 断言卡片渲染与状态徽章；新建流程
- Commit：`feat(m4): agent gallery owner console`

**验收标准：** 对应设计 §16.1；暂停操作不删除任何数据且 UI 有明确提示。

## F4.3 Agent Studio：草稿编辑器与能力选择

**涉及文件：**

- 新建：`frontend/src/app/workspace/agents/[agent_id]/page.tsx`（Studio 布局：5 个分区 Tab）
- 新建：`frontend/src/components/workspace/published-agents/instruction-editor.tsx`（AGENT.md / SOUL.md 双编辑器）
- 新建：`frontend/src/components/workspace/published-agents/skill-picker.tsx`
- 新建：`frontend/src/components/workspace/published-agents/connector-grants.tsx`
- 新建：`frontend/src/components/workspace/published-agents/draft-sandbox.tsx`（草稿沙箱对话，标注"尚未上线"）
- 测试：`frontend/tests/e2e/agent-studio-draft.spec.ts`

**开发任务：**

- 概览分区：slug/名称/描述/头像/模型选择/工具组
- 指令分区：两个 Markdown 编辑器（AGENT.md 在上/SOUL.md 在下），保存走 PATCH draft 携带 revision；409 冲突时提示重新加载
- Skill 分区：公共/私有分组展示（私有仅 owner 自己的）；选中 skill 后展示其声明的 Connector 能力要求，未授予的以警示态显示
- Connector 分区：按能力粒度勾选授予（列出 owner 的 connector 实例），无授予则发布校验会失败——前端预校验提示
- 沙箱分区：复用现有聊天组件对草稿配置发起对话（后端复用草稿沙箱 run 路径；若 M1 未含沙箱 run 端点，则在本功能中补 `POST /api/published-agents/{agent_id}/draft/sandbox-runs`，行为同 published 运行但读草稿且明确标注不产生用量账单）
- E2E：编辑→保存→revision 冲突分支；skill 选择联动 connector 警示
- Commit：`feat(m4): agent studio draft editing experience`

**验收标准：** 对应设计 §16.2 前 4 区、§18.6 前 3 条；草稿沙箱明确标识"尚未上线"。

## F4.4 发布、历史、对比与回滚 UI

**涉及文件：**

- 新建：`frontend/src/components/workspace/published-agents/publish-panel.tsx`
- 新建：`frontend/src/components/workspace/published-agents/release-history.tsx`
- 测试：`frontend/tests/e2e/agent-publish-rollback.spec.ts`

**开发任务：**

- 发布面板：触发发布 → 校验失败时逐条渲染 `PublishViolation`（code→i18n 文案）；成功展示 release_no
- 变更摘要：发布前对比"草稿 vs 当前 Release"（指令 diff、skill 增删、connector 授权变化、模型/工具组变化）
- 历史列表：release_no、时间、创建人；两个 Release 间对比视图
- 回滚：选择历史 Release → 二次确认 → 原子回滚；UI 强调"绑定/Key/API 路径不变"
- E2E：发布校验失败展示、成功发布、回滚流程
- Commit：`feat(m4): publish/history/diff/rollback UI`

**验收标准：** 对应总验收 #3、#10（Release 信息只出现在 owner 控制台，任何对外面（如 API 示例、分享文案）不含 Release 字段）。

## F4.5 发布后集成：API Key 与飞书绑定 UI

**涉及文件：**

- 新建：`frontend/src/components/workspace/published-agents/api-keys-panel.tsx`
- 新建：`frontend/src/components/workspace/published-agents/feishu-binding-panel.tsx`
- 测试：`frontend/tests/e2e/agent-integrations.spec.ts`

**开发任务：**

- Key 面板：创建（明文一次性展示 + 复制按钮 + "不再显示"警告）、列表（prefix/last4/状态/最后使用）、改名、限额编辑、轮换（提示重叠期）、撤销（二次确认）
- API 示例：按当前 agent_id 生成 curl 同步/SSE/异步示例代码块（占位 Key）
- 飞书面板：填 app_id/app_secret → 创建绑定 → 连接测试 → 启动/停止/重启 → 凭据轮换；健康状态实时展示（轮询 hooks）
- 未发布 Agent 的集成区域禁用并提示"先发布"
- E2E：Key 创建一次性明文展示、撤销分支；飞书绑定测试失败展示不健康态
- Commit：`feat(m4): post-publish integrations UI (keys + feishu)`

**验收标准：** 对应总验收 #6、#7；明文 Key 关闭弹窗后无任何途径再次查看；集成失败不影响 Agent 发布状态展示。

## F4.6 用量、配额与运维视图

**涉及文件：**

- 新建：`frontend/src/components/workspace/published-agents/usage-panel.tsx`
- 新建：`frontend/src/components/workspace/published-agents/quota-panel.tsx`
- 测试：`frontend/tests/e2e/agent-ops.spec.ts`

**开发任务：**

- 用量：按日 Run 数/Token/错误率图表（复用现有图表组件），按来源（api/feishu）与 Key 维度过滤
- 配额：展示生效值（平台默认 vs owner 覆盖清晰区分）；owner 覆盖编辑器（留空 = 继承默认，UI 明确标注"继承平台默认值，绝不表示无限制"）；超平台上限时前端预校验
- 审计：最近拒绝事件列表（配额拒绝/认证失败/能力拒绝），不展示原始外部用户内容
- E2E：配额继承展示、覆盖保存
- Commit：`feat(m4): usage, quota and ops views`

**验收标准：** 对应设计 §18.6 最后一条、§15（owner 视图不暴露原始外部用户内容）。

## F4.7 端到端验收与运维文档

**涉及文件：**

- 新建：`frontend/tests/e2e/multi-tenant-acceptance.spec.ts`
- 新建：`backend/tests/test_acceptance_multi_tenant.py`（后端集成验收）
- 新建：`docs/PUBLISHED_AGENTS.md`（运维手册：部署配置、SecretStore 密钥管理、配额调参、飞书绑定排障、回滚 SOP）
- 修改：`backend/CLAUDE.md`、`README.md`

**开发任务（把设计 §19 的 14 条总验收逐条转成自动化测试）：**

- 后端集成验收测试逐条覆盖 §19 条目 1–5、8–14（详见下节"最终验收清单"，每条一个测试函数，命名 `test_acceptance_<n>_<slug>`）
- 前端 E2E 覆盖条目 2、3、6、7 的 UI 路径
- 编写 `docs/PUBLISHED_AGENTS.md`
- 全量回归：`cd backend && make test`、`cd frontend && pnpm check && pnpm test && pnpm test:e2e`
- Commit：`feat(m4): acceptance suite and ops documentation`

## M4 Review Gate（= 第一版整体验收）

见下节"最终验收清单"，全部勾选后第一版验收通过。

---

# 最终验收清单（对应设计文档 §19，逐条可测）


| #   | 验收条目                                       | 验证方式                                                                  |
| --- | ------------------------------------------ | --------------------------------------------------------------------- |
| 1   | 两个平台用户各自创建多个 Agent，互相无法读写对方任何数据            | `test_acceptance_1_tenant_isolation`：双用户全资源交叉访问断言 403/404             |
| 2   | 仅 AGENT.md / 仅 SOUL.md / 两者均可发布            | `test_acceptance_2_instruction_combinations` + Studio E2E             |
| 3   | 已发布 Agent 保存草稿不改线上行为                       | `test_acceptance_3_draft_isolation`：改草稿后外部 Run 仍用旧指令                  |
| 4   | 公共/私有 Skill 可选，线上严格锁定发布时 revision          | `test_acceptance_4_skill_revision_pinning`：发布后改 skill 文件，外部 Run 读到旧快照 |
| 5   | Connector 只用显式授予能力，绝不暴露密钥                  | `test_acceptance_5_connector_grants`：未授予能力被拒；响应/日志无密钥                 |
| 6   | 可先无飞书发布，创建 Key 后即可 API 服务                  | `test_acceptance_6_publish_then_key`                                  |
| 7   | 发布后再绑定飞书，无需重新发布                            | `test_acceptance_7_late_feishu_binding`：绑定前后 current_release_id 不变    |
| 8   | 飞书私聊/群聊隔离；外部 Run 零长期记忆                     | `test_acceptance_8_mapping_and_memoryless`                            |
| 9   | 重新发布与回滚不改变机器人身份/API 路径/Key/Conversation 标识 | `test_acceptance_9_stable_identity`：回滚前后各标识 diff 为空                   |
| 10  | 外部调用方无法观察或选择内部 Release                     | `test_acceptance_10_no_release_leak`：全响应扫描 + 请求注入 release 字段被忽略       |
| 11  | 多具名 Key 独立限额、轮换、撤销                         | `test_acceptance_11_multi_keys`                                       |
| 12  | owner 限额与平台限额都在 Run 创建前拒绝并幂等记账             | `test_acceptance_12_quota_prerun_rejection`                           |
| 13  | 飞书事件重试与 API 幂等重试不重复 Run/计费                 | `test_acceptance_13_idempotency`                                      |
| 14  | 单个绑定/Connector/Agent 失败不影响其他已发布 Agent      | `test_acceptance_14_failure_isolation`                                |


---

# 横切关注点（贯穿所有里程碑）

## 安全检查清单（每个里程碑 Review Gate 必查）

- 新增对外响应均经白名单序列化，接入 `assert_safe_external_payload()` 扫描测试
- 密钥仅存 `secret_ref` / 慢哈希；结构化日志、异常、审计负载脱敏
- 所有仓储方法带 owner 作用域过滤；跨 owner 访问返回不暴露存在性的 404
- 入站字段永不直接设置 owner principal / external actor principal
- `tests/test_harness_boundary.py` 保持通过（新代码不越界）

## 配置与迁移纪律

- 每次改 `config.example.yaml` 同步 bump `config_version`
- 每张新表一个 Alembic 迁移文件，`upgrade`/`downgrade` 双向可执行，SQLite 与 PostgreSQL 兼容
- 迁移窗口内旧文件系统 Agent 与 External API V1 行为不变（每个里程碑跑全量回归）

## 可观测性

- 外部 Run 日志统一携带 `agent_id`、`correlation_id`、`source`；`release_id` 仅入受信遥测
- Supervisor 暴露每绑定健康端点（并入现有 `/health` 或独立 `GET /api/published-agents/{agent_id}/channels` 健康字段）

---

# 风险与应对


| 风险                                                          | 影响  | 应对                                              |
| ----------------------------------------------------------- | --- | ----------------------------------------------- |
| `get_available_tools` / `_build_middlewares` 改造回归 Web UI 行为 | 高   | F2.2 显式回归测试现有链路；published 模式走独立参数分支，不改默认路径语义    |
| 配额预留在 SQLite 下的并发正确性                                        | 中   | 预留行唯一约束 + 重读模式；并发测试用线程池压测；生产建议 PostgreSQL       |
| 飞书 websocket 多实例在同进程内的资源竞争                                  | 中   | Supervisor 每绑定独立事件循环任务 + 隔离异常边界；M3 冒烟用真实双应用验证   |
| Skill 快照体积（含附属文件）膨胀                                         | 低   | ContentStore 按 checksum 去重；快照仅在发布时产生            |
| 旧 `setup_agent` 对话流程与草稿服务双写不一致                              | 中   | F1.4 改造后旧文件路径只读兼容；迁移窗口结束后移除写路径                  |
| 多副本部署时 Supervisor 重复启动同一绑定                                  | 中   | 第一版明确单实例运行 Supervisor（文档标注）；预留 binding 租约字段留待后续 |


---

# 进度跟踪


| 里程碑 | 功能项                     | 状态  |
| --- | ----------------------- | --- |
| M1  | F1.1–F1.7 + Review Gate | 未开始 |
| M2  | F2.1–F2.6 + Review Gate | 未开始 |
| M3  | F3.1–F3.4 + Review Gate | 未开始 |
| M4  | F4.1–F4.7 + 最终验收        | 未开始 |


> 状态取值：未开始 / 进行中 / 待评审 / 已完成。每个功能项完成后更新本表并在对应小节勾选 checkbox。

