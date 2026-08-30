# DeerFlow Skill 权限与所有权设计

> 状态:**评审已通过,决策已固化(见第八节),待实施**
> 日期:2026-06-16(2026-06-16 根据评审反馈更新)
> 作者:基于 Mini-Agent skill 机制对比与 deer-flow 现状调研得出
> 关联代码:`backend/packages/harness/deerflow/skills/`、`backend/app/gateway/routers/skills.py`、`backend/app/gateway/authz.py`

---

## 目录

- [一、背景与目标](#一背景与目标)
- [二、Mini-Agent 的 skill 机制(对比参考)](#二mini-agent-的-skill-机制对比参考)
- [三、DeerFlow 现状](#三deerflow-现状)
  - [3.1 数据库里没有 skill 元信息](#31-数据库里没有-skill-元信息)
  - [3.2 skill 元信息实际存哪](#32-skill-元信息实际存哪)
  - [3.3 运行时可用集合的现有过滤逻辑](#33-运行时可用集合的现有过滤逻辑)
  - [3.4 现有的(微弱)权限手段](#34-现有的微弱权限手段)
  - [3.5 五个核心缺口](#35-五个核心缺口)
- [四、设计方案:所有权隔离 + 薄 DB 元信息层](#四设计方案所有权隔离--薄-db-元信息层)
  - [4.1 核心思路](#41-核心思路)
  - [4.2 三层可见性模型](#42-三层可见性模型)
  - [4.3 新增 `skills` 元信息表 schema](#43-新增-skills-元信息表-schema)
  - [4.4 存储层:per-user 目录布局](#44-存储层per-user-目录布局)
  - [4.5 运行时可用集合的新公式](#45-运行时可用集合的新公式)
  - [4.6 authz 扩展](#46-authz-扩展)
  - [4.7 路由层鉴权补全](#47-路由层鉴权补全)
- [五、迁移与兼容](#五迁移与兼容)
- [六、分阶段落地计划](#六分阶段落地计划)
- [七、已验证 / 未验证事项](#七已验证--未验证事项)
- [八、待评审的开放问题](#八待评审的开放问题)

---

## 一、背景与目标

DeerFlow 2.0 把 skill 作为一等能力(随仓库分发 22 个 public skill,用户可自建 custom skill,可在运行时增删改)。但 skill 当前的权限模型几乎是空白的:

- 没有所有权概念(任意登录用户可增删改任意 custom skill)
- 没有可见性隔离(所有用户共享同一池 custom skill)
- 多数写操作路由**没有任何鉴权装饰器**

本设计的目标:**在不破坏现有"内容存文件、运行时解析"加载链路的前提下,补一层薄数据库元信息,实现 skill 的所有权隔离与可见性控制**,并与 deer-flow 现有的 `users/{uid}/` 资源隔离模式(agents/memory/documents/threads 已采用)对齐。

明确**不做**的事:
- 不把 skill 内容搬进数据库(skill 常是多文件目录,带 scripts/references/资源,DB 化不现实)
- 不引入"使用前显式授权"动作(可用即可用,降低使用门槛)
- 不引入细粒度 RBAC 矩阵(先用 owner + admin 两档)

---

## 二、Mini-Agent 的 skill 机制(对比参考)

调研 [MiniMax-AI/Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) 后的核心结论:**它几乎没有真正的权限管理,参考价值有限。**

### 2.1 `allowed-tools` 字段:解析了但不强制

SKILL.md frontmatter 支持 `allowed-tools` 字段(Agent Skills 规范):

```yaml
---
name: webapp-testing
description: ...
allowed-tools:
  - Read
  - Bash
---
```

但看 `skill_tool.py` 和 `skill_loader.py`,这个字段被解析进 `Skill.allowed_tools` 存起来,**运行时没有任何地方根据它限制工具调用**。`GetSkillTool.execute()` 只是把整个 skill 内容塞进 prompt。这是 Claude Code 的规范字段,Mini-Agent 没实现 enforcement。

### 2.2 全量共享,无隔离

```
所有 skills/ 下的 SKILL.md → discover_skills() 全部加载 → 注进 system prompt
```

- 没有 user 概念
- 没有权限校验
- 没有 enable/disable 开关

**结论:DeerFlow 现有的权限手段(见下)已经比 Mini-Agent 强,不应以 Mini-Agent 为参照基准。** DeerFlow 真正可参考的是它自己的 `api_key.allowed_skills` 交集机制和 connector 的"内容在文件、状态在 DB"分层。

---

## 三、DeerFlow 现状

### 3.1 数据库里没有 skill 元信息

已查证:`backend/packages/harness/deerflow/persistence/` 下 16 张 ORM 表,**没有一张是 skill 的**:

```
users · api_keys · threads_meta · thread_shares · runs · run_events
feedback · connector_instances · connector_grants · connector_metadata_cache
connector_audit_logs · external_conversations · external_api_audit_logs
external_idempotency_keys · scheduled_tasks · scheduled_task_runs
```

`migrations/versions/` 搜 `skill`,只在**别的表**上出现(都是"引用 skill 名字",不是 skill 本身的元信息):

| 出现位置 | 字段 | 含义 |
|---|---|---|
| `api_keys` | `allowed_skills_json` | External API key 的 skill 名字白名单(JSON 数组) |
| `external_conversations` | `default_skill_name` | 会话默认 skill 名 |
| `external_api_audit_logs` | `skill_name` | 审计日志记录用 |

### 3.2 skill 元信息实际存哪

skill 的所有信息都来自 `SKILL.md` 的 YAML frontmatter,运行时解析,**不落库**。`Skill` 是纯内存 dataclass(`backend/.../skills/types.py:26`):

```python
@dataclass
class Skill:
    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path
    category: SkillCategory        # 'public' | 'custom'
    allowed_tools: list[str] | None = None
    enabled: bool = False
    display_name: str | None = None
    description_zh: str | None = None
    connector_requirements: list[ConnectorRequirement] | None = None
    # ↑ 注意:没有 owner_id / user_id / visibility
```

唯一的"状态"(enable/disable)存在 `extensions_config.json`(**进程级 JSON 文件,不是 DB**):

```json
{
  "skills": {
    "webapp-testing": { "enabled": true },
    "mcp-builder":    { "enabled": false }
  }
}
```

`SkillStateConfig`(`extensions_config.py:51`)只有 `enabled: bool` 一个字段。`is_skill_enabled(name, category)`(`extensions_config.py:264`)**不接 user 参数**,是进程级全局开关。`get_extensions_config()`(`extensions_config.py:284`)是进程单例,改一次影响所有人。

### 3.3 运行时可用集合的现有过滤逻辑

lead agent 在 `_resolve_available_skill_names`(`agent.py:483-502`)计算本次运行可用的 skill 集合:

```
可用 = agent.skills白名单 ∩ api_key.allowed_skills(若有)
       (然后再和"全局 enabled"的集合取交集做校验)
```

关键代码(已核对):

```python
def _resolve_available_skill_names(agent_config, is_bootstrap, forced_skill, *,
                                   app_config, external_allowed_skills=None):
    available = _available_skill_names(agent_config, is_bootstrap)  # agent.skills
    if external_allowed_skills is not None:                          # api_key.allowed_skills
        external_allowed = set(external_allowed_skills)
        available = external_allowed if available is None else available & external_allowed
    ...
```

**这里完全没有 user 维度的过滤。** `_load_enabled_skills_for_tool_policy` 加载的是全局 enabled skill,不带 user_id。

### 3.4 现有的(微弱)权限手段

deer-flow 已有的、和 skill 权限沾边的三层:

| 层 | 存哪 | 谁生效 | 局限 |
|---|---|---|---|
| 全局 `enabled` 开关 | `extensions_config.json`(进程级文件) | 所有人共享 | 一人改影响所有人;无 user 维度 |
| API key 的 `allowed_skills` 白名单 | `api_keys.allowed_skills_json`(DB) | **仅 External API 调用方** | 浏览器 session 用户零限制 |
| agent 配置的 `skills` 白名单 | agent YAML `skills` 字段 | 该 agent 的运行 | 只是"选哪些",不解决"谁能动" |

### 3.5 五个核心缺口

1. **没有 skill 所有权** —— `Skill` 数据结构和 `extensions_config.json` 都没有 `owner`/`user_id`。任意登录用户能增删改任意 custom skill。
2. **没有 per-user skill 命名空间** —— agents/memory/documents/threads 都有 `users/{user_id}/` 目录,**唯独 skills 没有**。`LocalSkillStorage` 不接 user 上下文。所有用户共享同一个 `custom/` 池。
3. **全局 enabled 是进程级** —— `extensions_config.json` 的 enabled 是进程全局,无 per-user/per-agent 维度。
4. **没有 `skills:*` 权限** —— `authz.py:Permissions` 只有 `threads:*`/`runs:*`。RBAC 层是 stub(所有登录用户拿全部权限)。
5. **skill 变更不记真实操作者** —— `author` 字段写死字符串 `"human"`,不记 `request.state.user.id`。
6. **多数写操作路由裸奔** —— skills 路由的 `create/update/delete_custom_skill`、install/upload、enable/disable 等连 auth 装饰器都没有;只有 `get_public_skill` 有 `@require_admin`。

---

## 四、设计方案:所有权隔离 + 薄 DB 元信息层

### 4.1 核心思路

**文件继续管"内容",数据库只补"权限和归属"这层薄元信息。**

这正是 deer-flow 对 connector 的做法:connector 的内容是 MCP 配置,但它的实例、授权、元数据缓存单独建了 4 张表。skill 走同样的"内容在文件、状态在 DB"分层。

**为什么不全 DB 化:** skill 经常是多文件目录(带 `scripts/`、`references/`、字体资源等,`skills/public/` 下就有大量这种),DB 存内容不现实。

### 4.2 三层可见性模型

```
┌─────────────────────────────────────────────────────┐
│  public/  (内置 skill,随仓库分发)                    │
│  → 全员可见、可用;只有 admin 能改                      │
│  → owner_id = NULL (系统所有)                         │
├─────────────────────────────────────────────────────┤
│  users/{uid}/skills/custom/  (用户私有 skill)         │
│  → 只有 owner 自己可见/可用/可改                       │
│  → owner_id = uid                                     │
├─────────────────────────────────────────────────────┤
│  (可选未来 P3) skill_grants 表                        │
│  → owner 显式分享给其他用户/agent                      │
└─────────────────────────────────────────────────────┘
```

与现状的关键差异:custom skill 从"全局共享池"改成"per-user 命名空间",与 agents/memory/documents/threads 的 `users/{uid}/` 模式统一。

### 4.3 新增 `skills` 元信息表 schema

新建一张 `skills` 表,**只存文件不擅长管的东西**(归属、可见性、per-user 开关)。skill 内容/描述仍从 SKILL.md 读。

```python
# backend/packages/harness/deerflow/persistence/skill/model.py

class SkillRow(Base):
    __tablename__ = "skills"

    # 主键:用 (owner_id, name) 业务唯一;表主键用自增 id
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # —— 归属 ——
    # NULL  => 系统所有(public skill)
    # 非 NULL => 该用户私有的 custom skill
    owner_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # —— 标识 ——
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 'public' = 内置只读; 'custom' = 用户可编辑
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="custom")

    # (owner_id, name) 业务唯一:同一用户下 skill 名不能重复;
    # public skill 的 owner_id 为 NULL, (NULL, name) 唯一。
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_skills_owner_name"),
    )

    # —— per-user 启用状态(替代全局 extensions_config.json 的 enabled) ——
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # —— 可见性(为未来分享预留;P1 阶段只用 private) ——
    # 'private' => 仅 owner; 'shared' => 走 skill_grants(未来)
    visibility: Mapped[str] = mapped_column(String(20), default="private", nullable=False)

    # —— 来源追踪 ——
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    # 'manual' | 'ai_draft' | 'install' | 'upload' | 'migrated'

    # —— 文件位置缓存(冗余,方便不扫盘也能 list) ——
    # 相对 skills root 的路径,如 "users/abc/custom/my-skill"
    skill_dir: Mapped[str] = mapped_column(String(512), nullable=False)

    # —— 冗余的展示字段(从 SKILL.md 同步,避免 list 时解析所有文件) ——
    display_name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
```

**设计要点说明:**

| 决策 | 理由 |
|---|---|
| `owner_id` 可空(NULL=系统所有) | public skill 没有具体 owner,与 custom skill 用同一张表,避免拆两表 |
| `(owner_id, name)` 唯一约束 | 同一用户下 skill 名不重复;public skill 用 `(NULL, name)` |
| `enabled` 进 DB | 替代进程级 `extensions_config.json`,实现 per-user 开关 |
| `display_name`/`description` 冗余存储 | list 接口不必再解析所有 SKILL.md,性能好;写入时从 frontmatter 同步 |
| `visibility` 字段预留 | P1 只用 `private`,但字段先在,避免 P3 加分享时改表 |
| 不存 skill 内容 | 多文件目录 DB 存不下;继续走文件 |

#### 4.3.1 新增 `skill_audit_logs` 表(评审决策 Q5)

参照 connector 的 `connector_audit_logs` 模式,为 skill 的增删改加独立审计表。当前代码只改 `author="human"` 是不够的——它不记操作类型、不记变更前后、不可查。

```python
# backend/packages/harness/deerflow/persistence/skill/audit_model.py

class SkillAuditLogRow(Base):
    __tablename__ = "skill_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # —— 被操作的 skill ——
    skill_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skills.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 冗余存名字,避免 skill 删除后审计记录失去上下文
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)

    # —— 操作者 ——
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 'user' = 浏览器登录用户; 'api_key' = External API 调用; 'system' = 迁移/后台
    actor_type: Mapped[str] = mapped_column(String(20), default="user", nullable=False)

    # —— 动作 ——
    # 'create' | 'update' | 'delete' | 'enable' | 'disable' | 'install' | 'upload' | 'rollback'
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # —— 变更内容(可选,update 时存 before/after 摘要) ——
    # 建议结构:{"before": {...}, "after": {...}},只存关键字段(enabled/visibility/display_name)
    # 完整文件内容变更走现有的 skill 版本历史(custom/.versions/),不在这里重复
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # —— 关联上下文(可选) ——
    # 如果是通过某个 thread/agent 的操作触发的,记下来便于追溯
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
```

**写入时机:** 所有 skill 写操作路由(create/update/delete/enable/disable/install/upload/rollback)在事务内同步插一条审计记录。`actor_user_id` 从 `request.state.user.id` 取(External API 从 api_key 的 user 取)。

**与现有版本历史的分工:**
- `custom/.versions/`(文件):存 skill **内容**的完整版本快照(已有)
- `skill_audit_logs`(DB):存**谁在何时做了什么操作**(新增),只记操作不记内容

**查询场景:** "谁改了 X skill"、"用户 Y 最近创建了哪些 skill"、"skill Z 的操作时间线"。

**与现有数据的迁移关系(评审决策已固化):**

| 现有 | 迁移到 | 决策依据 |
|---|---|---|
| `custom/<name>/`(文件,全局共享) | 文件搬到对应**已在线上运行的用户**目录 `users/{uid}/custom/<name>/`;`skills` 表插一行 `owner_id=<该用户>` | 评审 Q2:线上跑的都有明确用户,按文件实际归属的用户迁移,不存在 owner 未知的 skill |
| `extensions_config.json` 的 `skills.<name>.enabled` | 搬进 `skills.enabled`(per-user);该文件后续只保留 mcp/image_generation 配置 | |
| `api_keys.allowed_skills_json` | **保留机制,但收紧校验**:白名单里的 skill 名必须是该 key 所属用户**可见**的(public 或 owner=该用户),否则拒绝 | 评审 Q3:随 owner 隔离收紧,防止 API key 引用他人私有 skill |

### 4.4 存储层:per-user 目录布局

`LocalSkillStorage` 改造为接受可选 `user_id`,解析两个扫描根:

```
skills/
├── public/<name>/SKILL.md          # 公共(不变),owner_id=NULL
└── users/
    └── {user_id}/
        └── custom/<name>/SKILL.md   # 该用户私有(新增)
```

伪代码:

```python
class LocalSkillStorage:
    def __init__(self, root: Path, user_id: str | None = None):
        self.public_root = root / "public"
        self.user_custom_root = root / "users" / user_id / "custom" if user_id else None

    def _iter_skill_files(self):
        yield from self._walk(self.public_root, category="public", owner_id=None)
        if self.user_custom_root:
            yield from self._walk(self.user_custom_root, category="custom", owner_id=self.user_id)
```

`Skill` dataclass 补两个字段:

```python
@dataclass
class Skill:
    # ...现有字段...
    owner_id: str | None = None    # None = 系统所有(public)
    visibility: str = "private"
```

**owner 不写进 SKILL.md frontmatter**,而是从存储位置(`users/{uid}/` 路径)推导,避免 frontmatter 被手工篡改绕过所有权。

### 4.5 运行时可用集合的新公式

`_resolve_available_skill_names` 改造,加入 user 维度,并**收紧 api_key 白名单的归属校验**(评审决策 Q3):

```
当前用户可见的 skill 集合 =
    public(全部 enabled 的) ∪ 当前用户私有的(enabled 的)

最终可用 =
    当前用户可见的 ∩ agent.skills白名单 ∩ api_key.allowed_skills(若有)

⚠️ api_key.allowed_skills 的归属校验(Q3 决策):
   设置/更新 API key 白名单时,校验每个 skill 名必须是该 key 所属用户可见的
   (public 或 owner=该用户),否则拒绝写入。这样运行时交集天然不会出现
   "API key 引用了他人私有 skill"的情况。
```

`_load_enabled_skills_for_tool_policy` 改成带 `user_id` 加载:

```python
def _load_enabled_skills_for_tool_policy(available_skills, *, app_config, user_id):
    skills = get_enabled_skills_for_config(app_config, user_id=user_id)  # 新增 user_id
    if available_skills is None:
        return skills
    return [s for s in skills if s.name in available_skills]
```

run context 里已有 user_id(threads/runs 都按 user 隔离),需要把它接到 agent factory 这一层。

**External API 的 `skill_policy.py` 同步调整:** `available_external_skills()` 现在是 `enabled ∩ allowed_skills ∩ agent.skills`,改成基于"该 key 所属用户可见的 enabled skills"计算,而不是全局 enabled。

### 4.6 authz 扩展

`backend/app/gateway/authz.py` 的 `Permissions` 增加 skill 权限:

```python
class Permissions:
    # 现有: threads:read/write/delete, runs:create/read/cancel
    skills_read   = "skills:read"     # 查看自己可见的 skill
    skills_write  = "skills:write"    # 创建/改自己的 custom skill
    skills_delete = "skills:delete"   # 删除自己的 custom skill
    skills_admin  = "skills:admin"    # 改 public skill / 改别人的 / 管理 public skill 开关
```

`require_permission("skills", "write", owner_check=True)` 的 `owner_check` 扩展到 skills:校验 `skill.owner_id == request.state.user.id`(或 admin 豁免)。参考现有 threads 的 owner_check 实现(`authz.py:310-327`)。

**public skill 的开关权限(评审决策 Q1):** public skill 的 enable/disable **只归 admin**(`skills_admin`),普通用户不能禁用任何 public skill(包括"对自己生效"的局部禁用)。局部禁用能力留到后续阶段再评估。对应到路由:`enable/disable` 端点对 public skill 强制 `@require_admin`;对 custom skill 则走 `owner_check`。

### 4.7 路由层鉴权补全 + 审计写入

`backend/app/gateway/routers/skills.py` 的端点鉴权现状与改造对照:

| 端点 | 现状 | 改造后 | 审计(Q5) |
|---|---|---|---|
| `list_skills` | 无 auth | `@require_permission("skills","read")` + 只返回 `public ∩ owner自己的` | 只读,不记审计 |
| `create_custom_skill` | **无 auth** | `@require_permission("skills","write")` + 写进 `users/{uid}/custom/` | 记 `create` |
| `update_custom_skill` | **无 auth** | `@require_permission("skills","write",owner_check=True)` | 记 `update`(before/after) |
| `delete_custom_skill` | **无 auth** | `@require_permission("skills","delete",owner_check=True)` | 记 `delete` |
| `get_public_skill` | `@require_admin` | 保持(admin 才能看 public 源码) | 只读 |
| `enable/disable`(`PUT /skills/{name}`) | 无 auth | **public skill**:`@require_admin`;**custom skill**:`owner_check` | 记 `enable`/`disable` |
| install/upload | **无 auth** | `@require_permission("skills","write")` + 落到用户自己的目录 | 记 `install`/`upload` |
| rollback | 无 auth | `@require_permission("skills","write",owner_check=True)` | 记 `rollback` |

`author` 字段从写死的 `"human"` 改成 `request.state.user.id`;同时每个写操作在事务内插一条 `skill_audit_logs`(见 4.3.1)。

---

## 五、迁移与兼容

### 5.1 数据迁移(Alembic migration + 一次性脚本)

1. **建表:** Alembic 新增 `skills` 表和 `skill_audit_logs` 表(含外键、唯一约束、索引)。
2. **搬文件并归属(评审决策 Q2):** 现有 `custom/<name>/` 按文件实际归属的用户迁移到 `users/{uid}/custom/<name>/`。线上跑的 skill 都有明确用户,不存在 owner 未知的情况。脚本需建立"现有 custom skill → 归属用户"的映射(依据:创建该 skill 的操作来源/历史记录,或运维确认)。每个迁移的 skill 插一行 `skills` 记录,`source='migrated'`。
3. **搬 enabled 状态:** 读 `extensions_config.json` 的 `skills.*.enabled`,按归属用户写进对应 `skills.enabled`(per-user)。
4. **收紧 api_key 白名单(评审决策 Q3):** 迁移时扫描所有 `api_keys.allowed_skills_json`,校验每个引用的 skill 对该 key 所属用户可见。引用了他人私有 skill 的,记录到迁移报告由运维人工处理(不自动删除,避免误删)。
5. **保留旧文件:** `extensions_config.json` 不删,后续只保留 mcp/image_generation 配置。
6. **审计(评审决策 Q5):** 迁移脚本本身以 `actor_type='system'` 写入 `skill_audit_logs`,记录每个迁移动作,保证迁移可追溯。

### 5.2 向后兼容

- **External API 的 `api_key.allowed_skills_json` 机制保留**,但加归属校验(Q3):设置/更新白名单时校验每个 skill 名对该用户可见。
- 过渡期可保留一个 `legacy_shared/` 目录可见,给 deprecation warning(可选)。
- DB 里没有记录、但文件系统存在的 skill(比如手动放的),加载时自动补一条 `skills` 记录(owner 按"目录是否在 users/ 下"推断)。

---

## 六、分阶段落地计划

> 评审决策 Q4:P0 与 P1 拆成**两个独立 PR**,P0 先行快速止血。

| 阶段 | 内容 | 改动规模 | 风险 | PR |
|---|---|---|---|---|
| **P0 安全堵漏** | skills 路由写操作加 authz 装饰器;`author` 记真实 `request.state.user.id`;路由层显式拒绝越权(改/删别人的 skill) | 小(改路由装饰器 + author) | 低 | **PR #1 独立上线** |
| **P1 所有权隔离** | 新增 `skills` + `skill_audit_logs` 表;`Skill` 加 owner_id;custom 改 per-user 目录;list/get 加 owner 过滤;运行时可用集合加 user 维度;**所有写操作插审计记录**(Q5);迁移存量数据 | 中(新表 + 存储层 + agent factory + 迁移) | 中(涉及存储迁移) | PR #2 |
| **P2 开关私有化** | `enabled` 从全局 `extensions_config.json` 迁到 `skills` 表(per-user);public skill 开关只归 admin(Q1) | 中(迁移 + 现有开关端点改语义) | 中(行为变更) | PR #3 |
| **P3 分享协作** | `skill_grants` 表,owner 可分享给指定用户/agent | 中 | 低 | PR #4 |

**P0 是独立可上线的最小改动**——它不依赖新表,只是给现有路由补 authz 和真实 author,能立即堵住"任意用户改任意 skill"的洞。P1 再做所有权隔离(含审计表和迁移)。

> 注:P0 阶段由于还没有 owner_id,owner_check 只能做到"非 admin 不能改/删 custom skill"这种粗粒度(所有 custom skill 视作共享可写,但只限登录用户)。精确的 owner 校验要到 P1 表建好后才能生效。这个过渡期语义要在 PR 描述里写清楚。

---

## 七、已验证 / 未验证事项

### 已验证(读源码确认)

- [x] 数据库无 skill 表(persistence 16 张表无 skill;migrations 无 skill 表 DDL)
- [x] `Skill` dataclass 无 owner/user_id/visibility 字段(`types.py:26`)
- [x] `extensions_config.json` 的 enabled 是进程级单例(`extensions_config.py:281-296`),`is_skill_enabled` 不接 user 参数
- [x] `_resolve_available_skill_names` 无 user 维度过滤(`agent.py:483-502`)
- [x] skills 路由多数写操作无 auth 装饰器
- [x] `author` 字段写死 `"human"`
- [x] Mini-Agent 的 `allowed-tools` 解析了但不强制执行
- [x] connector 采用"内容在文件、状态在 DB"的分层(4 张表),可作参考;`connector_audit_logs` 是审计表的现成范例

### 已决策(评审 2026-06-16)

- [x] public skill 开关只归 admin,暂不支持用户局部禁用(Q1)
- [x] 存量 custom skill 按实际归属用户迁移,不存在 owner 未知(Q2)
- [x] `api_keys.allowed_skills_json` 随 owner 隔离收紧,设置时校验可见性(Q3)
- [x] P0 与 P1 拆两个独立 PR,P0 先行(Q4)
- [x] 新增 `skill_audit_logs` 表记录所有写操作(Q5)

### 未验证(需在实现阶段确认)

- [ ] run context 的 user_id 接到 agent factory 的具体路径(threads 已按 user 隔离,但 agent factory `_make_lead_agent` 当前从 `RunnableConfig` 取 `app_config`,需确认 user_id 如何传到这里)
- [ ] 前端 skill 管理页面对 per-user 的适配工作量
- [ ] 现有 `custom/` 数据量与迁移耗时(影响 P1 的迁移脚本设计)
- [ ] External API 的 `skill_policy.py` 与新 owner 过滤的交互细节(4.5 已给出方向,实现时需核对 `available_external_skills` 改造点)
- [ ] "现有 custom skill → 归属用户"映射的确定依据(Q2 假设有明确来源,但具体取自哪里需运维确认:创建历史/skill 内的标记/人工指定)

---

## 八、评审决策记录(2026-06-16)

原开放问题已全部决策,固化如下,作为实施的输入:

| # | 问题 | 决策 | 落到设计的哪里 |
|---|---|---|---|
| Q1 | public skill 的 enable/disable 归谁管 | **只归 admin**,暂不支持用户局部禁用某 public skill,后续再说 | 4.6 / 4.7 / P2 |
| Q2 | owner 未知的存量 custom skill 怎么归属 | **按实际归属用户迁移**。线上跑的都有明确用户,不存在 owner 未知的 skill | 5.1 / 第七节新增待确认项 |
| Q3 | `api_keys.allowed_skills_json` 是否随 owner 收紧 | **要收紧**。设置/更新白名单时校验每个 skill 名对该 key 所属用户可见(public 或 owner=该用户) | 4.5 / 4.3 迁移表 / 5.1 / 5.2 |
| Q4 | P0 是否单独一个 PR | **单独 PR**。P0(authz+author)独立上线快速止血,P1(所有权+审计)另起 PR | 第六节 |
| Q5 | 是否需要审计日志 | **需要**。新增 `skill_audit_logs` 表,所有写操作(create/update/delete/enable/disable/install/upload/rollback)记录,迁移脚本也记 | 4.3.1 / 4.7 / 5.1 / P1 |

---

*所有开放问题已闭环。下一步:按第六节推进 P0(独立 PR),实现时核对第七节"未验证"项。*
