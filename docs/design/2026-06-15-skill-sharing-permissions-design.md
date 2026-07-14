# Skill 权限与分享 redesign 计划

## 状态

- 状态：设计修订版，待评审
- 范围：custom skill 的 ownership、分享、运行授权、内容读取与用户侧脱敏
- 不改变：内置 `public/` skill 的发布方式；现有 `SKILL.md` bundle 格式

## 目标

1. A 用户创建的 custom skill 默认仅本人可见、可用、可编辑。
2. A 可以把 skill 分享给指定用户，或设为所有登录用户可用。
3. B 可以看到被分享 skill 的安全元数据并运行它，但不能通过管理 API、运行事件、历史记录或浏览器网络请求直接读取原始 skill bundle。
4. 每次运行只允许 agent 访问当前用户有权使用的 skill，不能通过 `read_file`、`bash`、subagent 或 `skill_manage` 绕过授权。
5. owner 修改或取消分享后，对后续运行立即生效；已经开始的运行使用启动时固定的 skill 快照。
6. 所有授权失败默认拒绝，文件系统与 DB 状态不一致时不能意外暴露 skill。

## 非目标与安全边界

### 本次保证

- 用户无法通过 Gateway API、External API、SSE、run events、messages、checkpoint/state API、history/version API 或前端页面直接获取无内容读取权限的 skill 原文。
- agent runtime 只能访问本次 run 被授权的 skill bundle。
- 分享、取消分享、visibility 修改、admin 内容读取等敏感操作可审计。

### 本次不能严格保证

LLM 执行 skill 时必须读取原始指令，因此模型可能在最终回答中复述、改写或泄露已读取内容。服务端可以隐藏原始 tool call/result，并增加输出检测，但不能把“模型绝不复述”作为安全保证。

如果 skill 原文属于必须严格保密的商业机密，应将其实现为后端受控工具或工作流服务，只向 LLM 暴露结构化输入输出，而不是把原始 `SKILL.md` 注入模型上下文。

## 术语

- **metadata 可见**：用户可以看到 name、description、owner、visibility 等安全字段。
- **content 可读**：用户可以读取整个 skill bundle，包括 `SKILL.md`、supporting files、history 和 versions。
- **可使用**：用户可以在 run 中调用 skill，但不等于 content 可读。
- **运行授权快照**：run 启动时计算并固定的 skill/version 授权集合。
- **运行投影**：仅包含本次 run 可使用 skill 的只读文件系统视图。

## 权限模型

### Visibility

为避免与现有 `SkillCategory.PUBLIC` 混淆，custom skill 的 visibility 使用：

- `private`：仅 owner 可使用。
- `authenticated`：所有登录用户可使用。
- 指定用户分享通过 `skill_shares` 表表达，不新增 visibility 枚举。

### 权限矩阵

| 身份 | 查看 metadata | 使用 | 读取 bundle/history/version | 编辑/删除/分享 |
|---|---:|---:|---:|---:|
| owner | 是 | 是 | 是 | 是 |
| 被分享用户 | 是 | 是 | 否 | 否 |
| 登录用户，skill 为 `authenticated` | 是 | 是 | 否 | 否 |
| 无授权用户 | 否 | 否 | 否 | 否 |
| admin | 是 | 是 | 仅显式 break-glass | 是 |

admin 读取用户 skill 内容必须使用显式接口、填写原因并写审计记录，不能因为普通列表或运行操作自动获得原文。

### Enabled 语义

- custom skill 的 `is_enabled` 由 owner/admin 控制，是全局可运行状态。
- 被分享用户不能修改 `is_enabled`。
- 本期不实现“每用户单独禁用”；需要时后续增加 `skill_user_settings`。
- 内置 public skill 继续由现有 `extensions_config.json` 管理。

## 关键设计决策

### 1. 使用稳定 `skill_id`

权限关系不能以可复用的 `skill_name` 为主键。每次创建 skill 都生成新的不可变 `skill_id`，分享、审计、运行授权全部引用 `skill_id`。

这样删除后重新创建同名 skill，不会继承旧的分享关系。

### 2. 保留文件系统 bundle，但使用独立 `storage_key`

数据库是 custom skill 的授权与生命周期真相源，文件系统继续存储 bundle。

```text
custom/<storage_key>/SKILL.md
custom/<storage_key>/references/...
custom/.history/<storage_key>.jsonl
custom/.versions/<storage_key>/...
```

- 新建 skill 默认使用 `skill_id` 作为 `storage_key`。
- 迁移期允许旧 skill 暂时使用原目录名作为 `storage_key`。
- `skill_name` 仅是用户可见名称，本期仍保持全局唯一。

### 3. 授权下沉到 skills domain 层

授权逻辑放在 `backend/packages/harness/deerflow/skills/access/`，Gateway、External API、runtime、sandbox、subagent 和 `skill_manage` 共用同一套策略。

Gateway router 只负责认证、参数校验与响应映射，不能成为唯一授权边界。

### 4. 每次 run 使用只读授权投影

run 启动前解析当前用户可使用的 skill，生成只包含授权 bundle 的只读运行投影，并将该投影映射到 `/mnt/skills`。

agent、subagent、`read_file`、`ls` 和 `bash` 都只能看到投影，不能看到全局 skills 根目录。

### 5. 服务端脱敏，前端仅负责展示

原始 skill tool call/result 不得进入任何用户可读输出。服务端在 SSE、event store、message API、checkpoint/state API 和 External API 响应边界统一生成脱敏版本。

前端 metadata 标记和路径检测只作为展示兼容，不能作为安全控制。

### 6. run 绑定启动时版本

run 启动时记录 `skill_id`、`version_seq`、`content_digest`，并从该版本生成运行投影。

- owner 在运行中修改 skill，只影响后续 run。
- 取消分享后禁止新 run；已开始 run 默认继续使用授权快照。
- 如需强制终止已运行任务，作为后续管理能力实现。

## 数据模型

### 1. `skill_meta`

```python
class SkillMetaRow(Base):
    __tablename__ = "skill_meta"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # skill_id
    skill_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active_version_seq: Mapped[int | None] = mapped_column(nullable=True)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

约束：

- `visibility IN ('private', 'authenticated')`
- `status IN ('creating', 'active', 'quarantined', 'deleting')`
- 仅 `status='active' AND is_enabled=true` 的 skill 可获得新运行授权。
- `owner_id IS NULL` 表示 legacy/system-owned skill，仅 admin 可管理或认领。

### 2. `skill_shares`

```python
class SkillShareRow(Base):
    __tablename__ = "skill_shares"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skill_meta.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shared_with_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    shared_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("skill_id", "shared_with_user_id", name="uq_skill_share"),
    )
```

分享前验证目标用户存在，禁止分享给 owner 自己。

### 3. `skill_access_audit`

```python
class SkillAccessAuditRow(Base):
    __tablename__ = "skill_access_audit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

至少记录：`share`、`unshare`、`visibility_change`、`admin_content_read`、`admin_edit`、`claim_legacy`、`run_grant`、`delete`。

审计表中的 `skill_id` 不设置级联删除外键，确保 skill 删除后审计记录仍被保留。admin 调用 `require_content_read` 时必须提供非空 reason。

## Domain 服务

新增 `backend/packages/harness/deerflow/skills/access/`：

```python
@dataclass(frozen=True)
class SkillGrant:
    skill_id: str
    skill_name: str
    storage_key: str
    version_seq: int
    content_digest: str
    can_view: bool
    can_use: bool
    can_read_content: bool
    can_manage: bool


class SkillAccessService:
    async def resolve_grant(self, *, skill_id: str, user_id: str, system_role: str) -> SkillGrant | None
    async def list_visible(self, *, user_id: str, system_role: str) -> list[SkillGrant]
    async def list_usable(self, *, user_id: str, system_role: str) -> list[SkillGrant]
    async def require_content_read(self, *, skill_id: str, user_id: str, system_role: str, reason: str | None = None) -> SkillGrant
    async def require_manage(self, *, skill_id: str, user_id: str, system_role: str) -> SkillGrant
    async def share(self, *, skill_id: str, actor_user_id: str, target_user_id: str) -> None
    async def unshare(self, *, skill_id: str, actor_user_id: str, target_user_id: str) -> None
    async def set_visibility(self, *, skill_id: str, actor_user_id: str, visibility: str) -> None
```

授权方法必须显式接收已认证的 `user_id` 和 `system_role`。不能用 `"default"`、缺失 context 或 `None` 作为普通请求的授权身份。

## 创建、修改与删除一致性

### 创建/安装

1. DB 事务中预留 `skill_id`、`skill_name`、`storage_key`，状态为 `creating`。
2. 将 bundle 写入同一文件系统下的临时目录。
3. 完成安全扫描、frontmatter 校验和初始 version snapshot。
4. 原子 rename 到正式 `storage_key` 目录。
5. 更新 `status='active'`、`active_version_seq` 和 `content_digest`。

任一步失败时保持 fail-closed：非 `active` skill 不可见、不可用，并由 reconciliation job 清理。

### 修改

- 仅 owner/admin 可修改整个 bundle、history、versions。
- 修改成功后创建不可变 version snapshot，再原子更新 active version 和 digest。
- admin 修改必须写审计记录。
- agent 的 `skill_manage` 创建 skill 时 owner 为当前 runtime user；编辑、删除现有 skill 前必须 `require_manage`。

### 删除

1. 将状态更新为 `deleting`，立即拒绝新 run grant。
2. 删除 bundle、history 和 versions。
3. 删除 `skill_meta`，依赖外键级联删除 shares。

文件删除失败时保留 `deleting` 状态并重试，不能恢复为可用状态。

## 运行时授权

### Run 启动

在 run 创建和真正执行前都执行一次授权检查，防止排队期间权限发生变化：

1. 获取当前 user 对所有 enabled custom skill 的 `list_usable()`。
2. 与 agent 配置允许集合、forced skill、External API key allowlist 取交集。
3. 为授权 skill 固定 active version、digest，并写入 run metadata。
4. 从固定版本生成 per-run 只读投影。
5. prompt 中只列出投影内的 skill。

示例 run metadata：

```json
{
  "skill_grants": [
    {
      "skill_id": "sk_...",
      "skill_name": "report-writer",
      "version_seq": 3,
      "content_digest": "sha256:..."
    }
  ]
}
```

### Sandbox 投影

建议路径：

```text
users/<user_id>/threads/<thread_id>/runtime-skills/<run_id>/
  public/<built-in-name>/...
  custom/<skill-name>/...
```

该目录映射为本次 sandbox 的 `/mnt/skills`，并强制只读。

- 投影中的虚拟路径继续使用 `custom/<skill-name>`，兼容当前 prompt 和工具调用；投影 manifest 负责将其关联到稳定 `skill_id` 和固定版本。
- 本地 sandbox 不再把全局 skills root 作为静态映射。
- AioSandbox 不再挂载全局 skills root。
- `read_file`、`ls`、`grep/glob`、`bash` 只解析本次 run 投影。
- 投影生成时拒绝 symlink，并限制文件数、单文件大小与总大小。
- run 完成后异步清理；异常退出由 reconciliation job 清理。

### Subagent 与 `skill_manage`

- subagent 必须继承父 run 的 `skill_grants`，不能重新加载全局 enabled skills。
- subagent 只加载其配置允许集合与父授权集合的交集。
- `skill_manage` 不通过运行投影写文件，而是调用 domain service；共享用户不能编辑被分享 skill。
- owner 使用共享 skill 运行时，skill evolution 也不能在未显式选择的情况下自动修改该 skill。

## 服务端输出脱敏

新增 `deerflow.skills.privacy.SkillContentRedactor`，输入为 run 的 `skill_grants` 与原始事件，输出用户安全事件。

### 识别范围

识别所有访问运行投影的工具调用及其配对结果，包括：

- `read_file`
- `ls`、`grep`、`glob`
- `bash` 中引用 `/mnt/skills`
- subagent 注入的 skill system/developer messages
- 未来新增的 skill bundle 读取工具

不能只判断 `/mnt/skills` 字符串；应结合 run 投影解析结果、tool call id 和消息关联关系。

### 用户可见边界

以下边界在返回或持久化前必须应用 redactor：

- StreamBridge 发布的全部模式：`messages-tuple`、`values`、`updates`、`debug`、`custom`
- RunEventStore 的 message、tool result 和 full event
- `GET /messages`、`GET /events`
- checkpoint/state/history 相关 API
- External API 的运行结果与错误信息
- 日志、trace 和错误响应中的 tool arguments/result

脱敏结果只保留：

```json
{
  "type": "skill_execution",
  "skill_id": "sk_...",
  "skill_name": "report-writer",
  "version_seq": 3,
  "summary": "Loaded skill instructions"
}
```

原始内部执行 state 可以继续服务 agent，但必须存放在用户不可直接访问的内部存储。任何读取内部 state 的 admin/debug 接口都需要独立权限和审计。

前端只渲染后端提供的脱敏事件，不使用路径检测作为权限兜底。

## Gateway API

所有 custom skill bundle 相关接口统一按权限分类：

### Metadata 可见接口

- `GET /api/skills`
- `GET /api/skills/{skill_id}`

迁移期保留现有 name-based 路由作为兼容入口。兼容入口必须先将 name 解析成唯一 `skill_id`，再执行相同授权逻辑，不能直接访问 storage。

仅返回调用者可见 skill 的安全 metadata 和 capability 字段：

```json
{
  "skill_id": "sk_...",
  "name": "report-writer",
  "owner_id": "...",
  "visibility": "private",
  "is_shared_to_me": true,
  "can_use": true,
  "can_read_content": false,
  "can_manage": false
}
```

### Content 读取接口

以下接口必须 `require_content_read`，保护对象是整个 bundle，而不只是 `SKILL.md`：

- custom skill content
- files 和单文件读取
- history
- versions、version files
- archive/download/export

### Manage 接口

以下接口必须 `require_manage`：

- edit、write/delete file、upload、mkdir
- create version、restore、rollback
- enable/disable
- delete
- share/unshare、visibility

分享与 visibility 使用独立接口，不复用同一个 body：

- `POST /api/skills/{skill_id}/shares`
- `DELETE /api/skills/{skill_id}/shares/{user_id}`
- `PUT /api/skills/{skill_id}/visibility`

无权访问的 skill 统一返回 404，避免泄露其是否存在；已看到 metadata 但无 content 权限的内容读取请求返回 403。

## External API

External API 使用 API key 所属用户作为授权主体：

```text
usable =
  enabled skills
  ∩ user can_use
  ∩ API key allowed_skills
  ∩ agent configured skills
```

保持现有兼容语义：

- `allowed_skills=[]` 表示 deny-all。
- 如未来需要“允许所有当前可用 skill”，新增明确字段，例如 `allow_all_visible_skills=true`，不能复用空列表。
- 创建 conversation、创建 run、run 真正开始前都重新检查授权。
- External API 启动的 subagent 同样继承运行授权快照。

## 前端变更

### Skill 管理页

- 展示 owner、visibility、shared、enabled 状态。
- 按 `can_read_content` 和 `can_manage` 控制入口。
- 对只可使用的 skill 仅显示安全 metadata 与“运行”入口。
- 分享弹窗仅 owner/admin 可见。
- admin break-glass 内容读取要求填写原因并明确提示会被审计。

### 对话与运行页

- 只消费后端脱敏事件。
- skill 读取步骤显示为统一占位步骤。
- “完整对话”也不能展示原始 skill tool call/result。
- 前端路径检测只用于兼容旧数据的视觉隐藏，不作为安全保证；旧数据应通过后端迁移或响应期 redaction 处理。

## Legacy 迁移

### DB 与目录迁移

1. 新增表、约束和 repositories。
2. 扫描现有 `custom/<name>` bundle，为每个 skill 创建稳定 `skill_id` 与 `storage_key=<legacy-name>`。
3. 若部署配置提供明确 `legacy_skill_owner_id`，将 skill 归属给该用户。
4. 未提供 owner 时，设置 `owner_id=NULL`、`visibility='private'`、`status='active'`，仅 admin 可见、可运行、可认领。
5. 不使用“当前 effective user”或“第一个 admin”进行隐式归属。
6. 后续可通过后台迁移将 legacy 目录重命名为 `skill_id`。

### Orphan reconciliation

启动时或管理任务中检查：

- 文件存在但无 `skill_meta`：标记为 orphan，仅 admin 可见，不加载到 runtime。
- `skill_meta` 为 active 但文件缺失或 digest 不匹配：改为 `quarantined`。
- `creating/deleting` 超时：按状态恢复或清理。
- shares 指向不存在用户：清理并写审计。

所有异常状态默认不可运行。

## 缓存与一致性

现有 skill prompt/enabled cache 是全局缓存，不能直接缓存用户授权结果。

- 全局缓存仅保存不敏感的 bundle metadata 与文件签名。
- `list_usable`、run grants 和 prompt skill 列表必须按 user/run 计算。
- cache key 至少包含 user id、授权集合、skill version/digest、agent 配置。
- share、unshare、visibility、enabled、edit、delete 后发布失效事件。
- 多 worker 部署不能只依赖进程内 cache invalidation；需要 DB version、短 TTL 或共享失效通道。

## 测试计划

### 权限矩阵

- owner、shared user、authenticated user、unauthorized user、admin。
- metadata、use、content read、manage、share、delete。
- disabled、quarantined、creating、deleting、system-owned 状态。

### 绕过测试

- 直接读取 custom content、support files、history、versions。
- 通过 `read_file`、`ls`、`grep/glob`、`bash` 访问未授权 skill。
- subagent 默认加载和显式加载未授权 skill。
- `skill_manage` 编辑、删除或覆盖他人 skill。
- raw SSE、run events、messages、checkpoint/state、External API 中不出现 marker 原文。
- 模拟未知 stream mode，验证默认 fail-closed。
- 删除后创建同名 skill，不继承旧 share。
- DB/文件系统不一致时不可运行。
- share 在排队后、执行前被撤销时，run 被拒绝。
- share 在 run 启动后被撤销时，当前 run 使用固定快照，后续 run 被拒绝。
- API key `allowed_skills=[]` 保持 deny-all。

测试使用唯一 marker，例如 `SECRET_SKILL_MARKER_123`：

- 硬性断言：所有用户可见 tool call/result、stream、event、message、checkpoint/state 和 API 响应中都不存在来自受保护 bundle 的原始 marker。
- best-effort 断言：最终模型回答经过精确 marker/长片段检测；模型对内容的改写或复述不属于本方案能够严格保证的范围。

### 一致性与并发测试

- 两个用户并发创建同名 skill。
- edit/delete/run 并发。
- create/install 中途失败。
- 多 worker cache 失效。
- 投影清理失败与服务重启后的 reconciliation。

## 可观测性

至少提供：

- 授权拒绝计数，按入口和原因分类。
- run grant、projection 创建和清理耗时。
- orphan/quarantined skill 数量。
- redactor 命中与未知事件类型计数。
- admin break-glass 内容读取审计。

日志中不得记录 skill 原文、support file 内容或未脱敏 tool result。

## 推荐实施顺序

1. 明确并验收安全边界与权限矩阵。
2. DB 模型、migration、repositories、legacy migration 与 reconciliation。
3. domain `SkillAccessService`，覆盖完整 bundle 的权限测试。
4. create/edit/delete/install 的状态机与一致性改造。
5. run grant、版本绑定和 per-run 只读投影。
6. 改造 sandbox、subagent、prompt、`skill_manage`，关闭 runtime 绕过。
7. 服务端 redactor，覆盖 SSE、events、messages、checkpoint/state 和 External API。
8. Gateway skill routers 与 External API policy。
9. 前端管理页、分享能力和脱敏步骤视图。
10. 绕过测试、并发测试、E2E 与灰度发布。

在第 5 至第 7 步完成前，不应向普通用户开放 custom skill 分享功能。

## 方案取舍

### 采用：DB ACL + per-run 只读投影

- 优点：保留现有 skill bundle 格式；runtime 看不到未授权 skill；适配本地和远程 sandbox。
- 缺点：需要改造 sandbox mount/path mapping，并承担投影创建和清理成本。

### 不采用：仅 Gateway ACL + UI 脱敏

- 改动较小，但 `read_file`、bash、subagent、history/version、raw SSE/events 均可绕过，不能作为权限方案。

### 后续可选：受控 Skill Execution Service

- 将敏感 skill 实现为服务端工作流，LLM 只获得结构化接口。
- 能提供更强内容保密，但需要重构 skill 执行模型，不纳入本期。
