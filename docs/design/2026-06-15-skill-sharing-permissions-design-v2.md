# Skill 权限与分享 redesign 计划 v2

## 状态

- 状态：设计草案 v2，已按联合 review 修订，待实施评审
- 基于：`docs/design/2026-06-15-skill-sharing-permissions-design.md`
- 范围：custom skill 的 ownership、分享、运行授权、内容读取、运行投影、服务端脱敏与用户侧管理
- 不改变：内置 `public/` skill 的发布方式；现有 `SKILL.md` bundle 格式
- 关键修订：
  - 明确 local sandbox 不是内容保密边界，严格分享保护仅在具备宿主文件隔离的 sandbox 模式开放
  - 第一阶段继续保持 `skill_name` 全局唯一；权限、分享、运行授权、External API 和持久化配置统一迁移到稳定 `skill_id`
  - admin 普通运行不会自动获得所有用户 skill 内容，跨用户内容读取必须 break-glass
  - 明确覆盖既有 DB-managed skills 计划中的冲突决策，避免 DB、文件系统和 `extensions_config.json` 多真相源
  - run 投影只从 pinned immutable revision 物化，不从可变 active bundle 物化
  - 明确 redaction/checkpoint 双轨、memory backend fail-closed，以及 no-auth 降级语义

## 目标

1. A 用户创建的 custom skill 默认仅本人可见、可用、可编辑。
2. A 可以把 skill 分享给指定用户，或设为所有登录用户可用。
3. B 可以看到被分享 skill 的安全 metadata 并运行它，但不能通过管理 API、External API、SSE、run events、messages、checkpoint/state/history API、浏览器网络请求直接读取原始 skill bundle。
4. 在具备宿主文件系统隔离的 sandbox 模式下，每次运行只允许 agent 访问当前用户有权使用的 skill，不能通过 `read_file`、`ls`、`grep/glob`、`bash`、subagent 或 `skill_manage` 绕过授权。
5. owner 修改或取消分享后，对后续运行立即生效；已经开始的运行使用启动时固定的授权快照和内容投影。
6. 所有授权失败默认拒绝，文件系统与 DB 状态不一致时不能意外暴露 skill。
7. 保持 public skill 与现有 `SKILL.md` bundle 生态可迁移，不要求第一期重写 skill 执行模型。

## 非目标与安全边界

### 本次严格保证

在启用 **隔离 sandbox** 时，本方案保证：

- 用户无法通过 Gateway API、External API、SSE、run events、messages、checkpoint/state/history API 或前端页面直接获取无内容读取权限的 custom skill 原文。
- agent runtime 只能看到本次 run 授权的 skill bundle 投影。
- subagent 继承父 run 的授权快照，不能重新扫描全局 enabled skills。
- 分享、取消分享、visibility 修改、admin 内容读取、admin 修改、run grant 等敏感操作可审计。

这里的“隔离 sandbox”指 sandbox provider 能阻止运行命令读取未映射的宿主路径，例如 Docker/AioSandbox、Kubernetes/provisioner 或未来等价隔离实现。

### Local sandbox 限制

当前 `LocalSandbox` 不提供 chroot/container 隔离。默认配置会禁用 host `bash`；如果部署者显式启用 `sandbox.allow_host_bash=true`，命令将以 gateway 进程身份在宿主机执行，只做虚拟路径替换。因此 local sandbox 不能作为“可使用但不可读取内容”能力的严格保密边界。

因此：

- local sandbox 只能作为开发/单用户/可信环境模式。
- 在 local sandbox 下，服务端 API、`read_file`、`ls`、prompt 注入和前端输出仍按权限过滤；即使 host `bash` 默认禁用，也不能把 local provider 视为经过验证的宿主文件隔离边界。
- 默认不向普通用户开放“可使用但不可读内容”的 custom skill 分享功能，除非部署者显式启用 trusted-local override，并接受该模式下不提供严格内容保密。
- 测试计划必须分别覆盖：
  - 隔离 sandbox 下的硬性绕过测试。
  - local sandbox 下的能力降级与功能禁用测试。

推荐新增 provider 能力声明：

```python
class SandboxProvider:
    supports_host_filesystem_isolation: bool = False
    supports_readonly_skill_projection: bool = False
```

上述布尔值是能力接口，不是 provider 自报后即可信任的安全证明。分享能力开放前必须：

- provider 位于平台维护的可信 allowlist，或通过启动时隔离 conformance test。
- 验证未映射宿主路径不可读、custom 投影只读、全局 custom skill 根目录未挂载。
- 在 `share`、`visibility=authenticated`、run grant 和 run 真正执行前重复检查 capability；能力缺失或检查失败时 fail-closed。
- provider 配置从隔离模式降级后，已有 share 与 `authenticated` skill 也不能继续获得新 run grant。

### 本次不能严格保证

LLM 执行 skill 时必须读取原始指令，因此模型可能在最终回答中复述、改写或泄露已读取内容。服务端可以隐藏原始 tool call/result，并增加输出检测，但不能把“模型绝不复述”作为安全保证。

如果 skill 原文属于必须严格保密的商业机密，应将其实现为后端受控工具或工作流服务，只向 LLM 暴露结构化输入输出，而不是把原始 `SKILL.md` 注入模型上下文。

## 术语

- **metadata 可见**：用户可以看到 name、description、owner、visibility、capability 等安全字段。
- **content 可读**：用户可以读取整个 skill bundle，包括 `SKILL.md`、supporting files、history 和 versions。
- **可使用**：用户可以在 run 中调用 skill，但不等于 content 可读。
- **运行授权快照**：run 启动时计算并固定的 skill/version/content digest 授权集合。
- **运行投影**：仅包含本次 run 可使用 skill 的只读文件系统视图。
- **skill handle**：运行投影中用于避免重名冲突的稳定可读路径片段，例如 `report-writer-sk_abc123`；必须使用完整唯一 ID 或经过碰撞检测的编码生成，不能只依赖未经校验的短 ID 前缀。
- **break-glass**：admin 为排障、合规或用户支持而显式读取用户 skill 内容的高权限操作，必须提供 reason 并写审计。

## 权限模型

### Visibility

为避免与现有 `SkillCategory.PUBLIC` 混淆，custom skill 的 visibility 使用：

- `private`：仅 owner 可使用。
- `authenticated`：所有登录用户可使用。
- 指定用户分享通过 `skill_shares` 表表达，不新增 visibility 枚举。

`public/` 内置 skill 不进入此 visibility 枚举，继续属于平台内置资源。

### 权限矩阵

| 身份 | 查看 metadata | 使用 | 读取 bundle/history/version | 编辑/删除/分享 |
|---|---:|---:|---:|---:|
| owner | 是 | 是 | 是 | 是 |
| 被分享用户 | 是 | 是 | 否 | 否 |
| 登录用户，skill 为 `authenticated` | 是 | 是 | 否 | 否 |
| 无授权用户 | 否 | 否 | 否 | 否 |
| admin 普通运行 | 仅自己可见集合 | 仅自己可用集合 | 否 | 否 |
| admin 管理接口 | 是 | 可管理 | 仅显式 break-glass | 是 |

admin 读取用户 skill 内容必须使用显式 break-glass 接口、填写 reason 并写审计记录。admin 的普通对话/run 不会自动获得所有用户的 custom skill，也不会将其他用户 skill 原文注入模型。

### Enabled 语义

- custom skill 的 `is_enabled` 由 owner/admin 控制，是全局可运行状态。
- 被分享用户不能修改 `is_enabled`。
- 本期不实现“每用户单独禁用”；需要时后续增加 `skill_user_settings`。
- 内置 public skill 继续由现有 `extensions_config.json` 管理。
- custom skill 的 enabled 真相源迁移到 DB 后，`extensions_config.json` 不再作为 custom skill enabled 的权威来源；兼容镜像如保留，只能由 DB 单向生成。

### No-auth / 单用户模式

DeerFlow 仍支持 no-auth 单用户部署。该模式下：

- 授权主体固定为 `DEFAULT_USER_ID`（当前为 `"default"`），但只能用于 no-auth 单用户模式，不能用于多用户登录请求。
- 所有 legacy custom skill 可迁移为 `owner_id=DEFAULT_USER_ID`。
- 分享、visibility、break-glass 等多用户能力默认隐藏或退化为 no-op。
- 如果部署从 no-auth 升级到 auth 模式，必须先运行迁移脚本，将 `DEFAULT_USER_ID` 数据归属给明确用户或 admin 认领。

### 持久化依赖与 memory backend

- auth 多用户模式下，sharing、ownership、custom enabled 和 custom run grant 必须依赖可用的持久化 DB catalog。
- DB/ACL repository 不可用时，custom skill metadata、内容读取和运行授权全部 fail-closed；不得回退到 `LocalSkillStorage.load_skills()` 扫描全局 custom 目录。
- `database.backend=memory` 仅支持明确的 no-auth 单用户兼容模式，不开放多用户分享与 `authenticated` visibility。
- public skill 可继续使用现有本地加载与 `extensions_config.json`；custom skill 的 fallback 行为必须与 public skill 分开。

## 关键设计决策

### 1. 使用稳定 `skill_id` 作为唯一授权标识

权限关系不能以可复用的 `skill_name` 为主键。每次创建 skill 都生成新的不可变 `skill_id`，分享、审计、运行授权、External API 和持久化配置统一引用 `skill_id`。

这样删除后重新创建同名 skill，不会继承旧的分享关系。

### 2. 第一阶段保持 `skill_name` 全局唯一，完成全链路 ID 化后再开放同名

`skill_name` 是用户可见名称，不承担授权身份；但当前 runtime、agent config、forced skill、prompt cache、External API conversation 和 enabled 配置仍大量按 name 工作。为避免同名 skill 被静默覆盖或错误执行，第一阶段保持 public/custom 共用的全局 `UNIQUE(skill_name)`。

第一阶段要求：

- DB、runtime `Skill` 模型、agent config、forced skill、External API allowlist/conversation、tool policy 和 prompt cache 全部新增并优先使用 `skill_id`。
- legacy name 只允许在配置写入或迁移时解析一次，并持久化为 `skill_id`；run 阶段不能反复按可见集合动态解析 name。
- 新 API 以 `skill_id` 为主；name-based 路由只作为全局唯一名称的兼容入口。
- 创建时名称冲突统一返回安全的“name unavailable”，不返回 owner、visibility 或候选 metadata。

第二阶段只有在所有运行与持久化契约完成 ID 化、并验证不存在 name-keyed cache/map 后，才通过独立设计和 migration 将约束放宽为 `UNIQUE(owner_id, skill_name)`。该能力不属于本期 sharing 上线条件。

### 3. 保留文件系统 bundle，但使用独立 `storage_key`

数据库是 custom skill 的 metadata、授权、enabled、生命周期和 revision 索引真相源；不可变 revision storage 是完整 bundle 内容真相源；active bundle 只是可从当前 revision 重建的 working copy。

```text
custom/<storage_key>/SKILL.md
custom/<storage_key>/references/...
custom/.history/<storage_key>.jsonl
custom/.versions/<storage_key>/...
```

- 新建 skill 默认使用 `skill_id` 作为 `storage_key`。
- 迁移期允许旧 skill 暂时使用原目录名作为 `storage_key`。
- `storage_key` 不暴露给普通 API 调用者，不作为授权标识。
- name-based 兼容路由按全局唯一名称解析成 `skill_id`，但新写入的配置和关系必须持久化 `skill_id`。
- active bundle 不能作为 run snapshot 真相源；内容不一致时必须从 pinned revision 重建或进入 quarantine。

### 4. 归并 DB-managed skills 计划

仓库已有 `docs/execution/plans/2026-06-02-db-managed-skills.md` 提出 DB catalog、business classification、revision 等字段。本设计不应再引入一套完全独立 schema。

本设计明确覆盖前序计划中的冲突决策。统一方向：

- 以一个 skill catalog 表承载 metadata、ownership、visibility、enabled、lifecycle、business classification。
- 以 `skill_shares` 表承载分享关系。
- 以 `skill_revisions` 或 `skill_versions` 承载完整、不可变的 bundle 快照和 digest；不能只保存 `SKILL.md` 内容与 support-file hash。
- 以 `skill_revision_pins` 持久化 run 对 revision 的引用，保护 queued/running/interrupted/resumable run。
- 以 `skill_access_audit` 承载权限审计。
- custom enabled 仅以 DB 为真相源，不再镜像到 `extensions_config.json`；后者仅管理 public skill。
- custom active bundle 由 immutable revision 单向物化，不允许“DB 内容、active bundle、revision”互相回写。
- auth 多用户模式不得在 DB catalog 不可用时 fallback 到全局 `LocalSkillStorage` custom listing。

若前序计划中的 `skills` / `skill_files` / `skill_revisions` 已落地，本设计应通过 migration 扩展这些表，而不是创建语义重叠的 `skill_meta` 表。旧计划中 `unique(name)`、DB-first metadata、business classification 等兼容项可保留；与上述真相源、完整 revision、custom enabled 和 fallback 规则冲突的任务必须在实施前修订或废弃。下文使用推荐统一命名 `skills`，实现时可按已落地表名调整。

具体覆盖关系：

- 覆盖旧计划 Decision 3 中“custom 内容写入 DB 后再物化”的内容真相源定义：本设计改为 immutable revision storage 保存完整 bundle，DB 保存 catalog 与 revision 索引。
- 覆盖旧计划 Task 5 中“DB catalog 不可用时 custom listing fallback 到 LocalSkillStorage”的 auth 多用户行为。
- 覆盖旧计划 Task 7 中“custom enabled 镜像到 `extensions_config.json`”的行为；custom enabled 仅从 DB 读取。

### 5. 授权下沉到 skills domain 层

授权逻辑放在 `backend/packages/harness/deerflow/skills/access/`，Gateway、External API、runtime、sandbox、subagent 和 `skill_manage` 共用同一套策略。

Gateway router 只负责认证、参数校验与响应映射，不能成为唯一授权边界。

授权方法必须显式接收已认证的 `user_id` 和 `system_role`。多用户登录请求中，不能用 `"default"`、缺失 context 或 `None` 作为普通授权身份。

### 6. 每次 run 使用授权快照与只读投影

run 创建和真正执行前都计算当前用户可使用的 skill，生成不可变 `skill_grants`，并基于 grants 创建只读运行投影。

agent、subagent、`read_file`、`ls`、`grep/glob` 和隔离 sandbox 内的 `bash` 只能看到投影，不能看到全局 skills 根目录。

### 7. run 投影从 pinned immutable revision 物化

run 授权必须固定一个 immutable revision。投影从该 revision 的完整 bundle 物化，不能从运行期间可被修改的 active working bundle 物化。

流程：

1. 在授权事务中读取并固定 `active_version_seq`、revision location 与 `content_digest`，创建 run-to-revision pin/reference。
2. 从 pinned immutable revision 创建 per-run 投影。
3. 创建投影后重新计算 canonical digest，与 revision/DB `content_digest` 比对。
4. 不一致则拒绝 run grant；仅在排除正在进行的合法 mutation 并重复验证失败后，才将 skill 标记为 `quarantined`。
5. run 进入不可恢复的 terminal 状态并清理投影后，释放 revision pin。
6. revision prune 必须跳过所有被 active、queued、interrupted 或 resumable run pin 的 revision。

canonical digest 算法必须平台无关并形成独立规范：对归一化相对路径排序，使用统一 `/` 分隔符，对每个文件的路径、长度与原始 bytes 计算摘要；排除 history、versions、临时文件和文件系统 metadata；拒绝 symlink、路径穿越和重复归一化路径。

### 8. 服务端脱敏，前端仅负责展示

原始 skill tool call/result 不得进入任何用户可读输出。服务端在 SSE、event store、message API、checkpoint/state API 和 External API 响应边界统一返回脱敏版本。

更推荐的实现是：skill 内容读取不要作为普通 `ToolMessage` 持久化到用户可读消息历史，而是进入 run 内部上下文存储；用户消息流中只写入 `skill_execution` 占位事件。这样可以减少需要逐个出口 redaction 的风险。

前端 metadata 标记和路径检测只作为展示兼容，不能作为安全控制。

## 数据模型

### 1. `skills`

```python
class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # skill_id
    skill_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    runtime_category: Mapped[str] = mapped_column(String(16), nullable=False)  # public/custom
    storage_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    description_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_tools_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    frontmatter_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    business_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    active_version_seq: Mapped[int | None] = mapped_column(nullable=True)
    content_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    package_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="synced")
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

约束：

- `runtime_category IN ('public', 'custom')`
- custom `visibility IN ('private', 'authenticated')`
- public skill visibility 不参与 custom ACL
- `status IN ('creating', 'active', 'quarantined', 'deleting', 'deleted')`
- `sync_status IN ('synced', 'dirty', 'missing', 'failed')`
- 仅 `runtime_category='custom' AND status='active' AND is_enabled=true` 的 skill 可获得新运行授权。
- `owner_id IS NULL` 表示 legacy/system-owned custom skill，仅 admin 可管理或认领，不进入普通用户 run。
- 第一阶段保持全局 `UNIQUE(skill_name)`；per-owner 同名能力延期到全链路 ID 化完成后的独立 migration。
- `storage_key` 全局唯一且仅供内部存储定位使用。

### 2. `skill_shares`

```python
class SkillShareRow(Base):
    __tablename__ = "skill_shares"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shared_with_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    shared_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("skill_id", "shared_with_user_id", name="uq_skill_share"),
    )
```

分享前验证：

- 目标用户存在。
- 目标用户不是 owner 自己。
- actor 必须 `require_manage`。
- 当前 provider 未通过严格内容隔离 capability 检查时，不允许创建“可用但不可读”的分享关系或设置 `visibility=authenticated`，除非部署显式进入 trusted-local override。
- capability gate 还必须在每次 run grant 和 run 真正执行前重复执行，不能只在创建 share 时验证。

用户删除时必须清理其收到/发出的 shares 并写审计。该用户拥有的 custom skill 默认转为 `owner_id=NULL`、`visibility='private'`、`is_enabled=false`，仅 admin 可认领或删除；不能隐式转移给其他用户，也不能继续获得新 run grant。

### 3. `skill_revisions` / `skill_versions`

保留或扩展既有 version/revision 设计，至少记录：

- `skill_id`
- `seq`
- immutable bundle location / object key
- `content_digest`
- `action`
- `author_user_id`
- `message`
- `thread_id`
- `file_count`
- `size_bytes`
- `created_at`

revision 必须保存完整 bundle，且一旦发布不可原地修改。active bundle 每次成功修改后必须创建新的不可变 snapshot，并原子更新 `skills.active_version_seq` 与 `skills.content_digest`；active bundle 只是可重建 working copy。

历史 snapshot 可 prune，但 prune 前必须检查 active revision 和 run-to-revision pin。queued、running、interrupted、可恢复 run 引用的 revision 均不可删除。

### 4. `skill_revision_pins`

revision pin 必须持久化，不能只存在于进程内存或可被普通用户读取的 run metadata 中。至少记录：

- `run_id`
- `skill_id`
- `version_seq`
- `content_digest`
- `run_state`
- `created_at`
- `released_at`

约束与生命周期：

- `UNIQUE(run_id, skill_id)`。
- queued、running、interrupted、resumable 状态均视为 active pin。
- 只有 run 进入不可恢复 terminal 状态、投影已清理或确认可不再恢复时才能释放 pin。
- prune/reconciliation 必须查询持久化 pin；服务重启或 worker 崩溃不能导致仍可能恢复的 revision 被删除。
- 超时 pin 只能通过与 run store/checkpointer 对账后释放，不能仅按创建时间删除。

### 5. `skill_access_audit`

```python
class SkillAccessAuditRow(Base):
    __tablename__ = "skill_access_audit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

至少记录：`share`、`unshare`、`visibility_change`、`admin_content_read`、`admin_edit`、`admin_restore`、`claim_legacy`、`owner_removed`、`run_grant`、`delete`、`quarantine`、`reconcile_cleanup`。

审计表中的 `skill_id` 不设置级联删除外键，确保 skill 删除后审计记录仍被保留。

删除 skill 时必须先写 `delete` 审计；shares 删除前如需要逐条审计 unshare，必须先读取并批量写审计。存在 revision pin 时保留 `skills` tombstone，不能提前依赖级联硬删除清除 revision 索引。

## Domain 服务

新增或扩展 `backend/packages/harness/deerflow/skills/access/`：

```python
@dataclass(frozen=True)
class SkillGrant:
    skill_id: str
    skill_name: str
    skill_handle: str
    runtime_category: str
    storage_key: str
    owner_id: str | None
    version_seq: int | None
    content_digest: str
    can_view: bool
    can_use: bool
    can_read_content: bool
    can_manage: bool


class SkillAccessService:
    async def resolve_grant(self, *, skill_id: str, user_id: str, system_role: str) -> SkillGrant | None: ...
    async def list_visible(self, *, user_id: str, system_role: str, include_admin_managed: bool = False) -> list[SkillGrant]: ...
    async def list_usable(self, *, user_id: str, system_role: str, include_admin_managed: bool = False) -> list[SkillGrant]: ...
    async def require_run_use(self, *, skill_id: str, user_id: str, system_role: str, sandbox_capabilities: SandboxCapabilities) -> SkillGrant: ...
    async def require_content_read(self, *, skill_id: str, user_id: str, system_role: str, reason: str | None = None) -> SkillGrant: ...
    async def require_manage(self, *, skill_id: str, user_id: str, system_role: str) -> SkillGrant: ...
    async def share(self, *, skill_id: str, actor_user_id: str, target_user_id: str, sandbox_capabilities: SandboxCapabilities) -> None: ...
    async def unshare(self, *, skill_id: str, actor_user_id: str, target_user_id: str) -> None: ...
    async def set_visibility(self, *, skill_id: str, actor_user_id: str, visibility: str, sandbox_capabilities: SandboxCapabilities) -> None: ...
```

`SkillGrant` 是仅服务端可见的内部授权对象。包含 `storage_key`、owner ID、revision location 或内部 capability 判定的数据不得直接序列化到普通 API、run metadata 响应、checkpoint 或 Agent 可见文件。

规则：

- `system_role='admin'` 不等于普通 `list_usable` 自动包含所有用户 skill。
- `include_admin_managed=True` 只能由管理接口使用，且内容读取仍必须走 `require_content_read(reason=...)`。
- shared 或 `authenticated` skill 的 `require_run_use` 必须验证当前 sandbox capability；能力缺失、conformance check 失败或 DB/ACL repository 不可用时拒绝。
- runtime/subagent 只接收已解析好的 `SkillGrant` 列表，不在线程池或子任务中重新从 contextvar 解析用户身份。
- name-based helper 仅用于全局唯一 legacy name 的兼容解析；所有新关系、配置与运行快照必须保存 `skill_id`。

## 创建、修改与删除一致性

### 创建/安装

1. DB 事务中预留 `skill_id`、`owner_id`、`skill_name`、`storage_key`，状态为 `creating`。
2. 校验 `skill_name` 全局唯一性；冲突响应不得暴露已有 skill 的 owner、visibility 或 metadata。
3. 将 bundle 写入同一文件系统下的临时目录。
4. 完成安全扫描、frontmatter 校验、symlink 拒绝、文件数/单文件大小/总大小限制。
5. 创建包含完整 bundle 的初始 immutable revision，并计算 canonical digest。
6. 从该 revision 原子物化 active working copy 到正式 `storage_key` 目录。
7. 更新 `status='active'`、`active_version_seq` 和 `content_digest`。

任一步失败时保持 fail-closed：非 `active` skill 不可见、不可用，并由 reconciliation job 清理。

### 修改

- owner 可修改整个 bundle；修改成功后先创建完整不可变 revision，再原子更新 active version、digest 与 working copy。
- admin 对其他用户 skill 的内容读取、编辑、restore 和 rollback 都必须通过 break-glass，提供 reason 并写审计记录。
- published revision 不可原地修改；restore/rollback 通过创建一个新的 revision 指向恢复后的完整内容实现。
- agent 的 `skill_manage` 创建 skill 时 owner 为当前 runtime user；编辑、删除现有 skill 前必须 `require_manage`。
- skill evolution 默认只能修改 owner 自己明确选择的 skill，不能因为运行了某个被分享 skill 就自动修改它。

### 删除

1. 写 `delete` 审计。
2. 将状态更新为 `deleting`，立即拒绝新 run grant。
3. 删除 shares，并删除 active working copy；已经开始的 run 继续使用 pinned revision。
4. 仅删除没有 active/run pin 的 revisions；仍被运行引用的 revision 延迟清理。
5. 在所有 pin 释放前保留 `skills` tombstone 和 revision 索引；最终清理完成后再硬删除，或长期保留 `status='deleted'` tombstone。

文件删除失败时保留 `deleting` 状态并重试，不能恢复为可用状态。

## 运行时授权

### Run 启动

在 run 创建和真正执行前都执行一次授权检查，防止排队期间权限发生变化：

1. 获取当前 user 对 custom skill 的 `list_usable()`，不包含 admin 管理范围；DB/ACL repository 不可用时 custom 集合为空并拒绝显式 custom skill 请求。
2. 加载 public skill enabled 状态。
3. 与已经迁移并持久化为 ID 的 agent 配置允许集合、forced skill、External API key allowlist 取交集。
4. 对 shared/`authenticated` skill 执行 sandbox capability gate；检查失败时拒绝 run，不得仅隐藏 UI。interrupted/resumable run 恢复执行前同样重新检查 provider capability。
5. 为授权 custom skill 固定 `skill_id`、`skill_handle`、immutable revision、`content_digest`，创建 revision pin，并写入仅服务端可见的 internal run metadata。
6. 从 pinned immutable revision 生成 per-run 只读投影，生成后校验 canonical digest。
7. prompt 中只列出投影内的 skill 路径与安全 metadata。

示例 internal run metadata；该结构不得通过普通 run、message、event、state 或 debug API 返回：

```json
{
  "skill_grants": [
    {
      "skill_id": "sk_abc123",
      "skill_name": "report-writer",
      "skill_handle": "report-writer-sk_abc123",
      "version_seq": 3,
      "content_digest": "sha256:..."
    }
  ],
  "skill_projection_id": "proj_run_..."
}
```

### Sandbox 投影

建议路径：

```text
users/<user_id>/threads/<thread_id>/runtime-skills/<run_id>/
  public/
    <built-in-name>/...
  custom/
    <skill-handle>/...
```

该目录映射为本次 sandbox 的 `/mnt/skills`，并强制只读。

规则：

- custom 投影路径使用 collision-free 的 `custom/<skill-handle>`，为后续同名能力预留兼容路径。
- 完整 projection manifest 存放在 server-side internal run context，不映射进 `/mnt/skills`。Agent 不得读取 `storage_key`、owner ID、内部 revision location 或授权关系。
- 如运行时确需 Agent 可见 manifest，只能包含 `skill_handle` 和安全展示 metadata，且不能包含 `storage_key`、owner ID 或内部对象定位信息。
- public skill 可继续使用全局只读共享挂载；若 provider 不能同时挂载 public 和 per-run custom，可将 public 也投影进 run 目录。
- public skill 不要求每次复制。custom skill 禁止 hardlink 和 symlink；可使用经过验证不会回写源 revision 的 reflink/copy-on-write，否则使用 copy。物化完成后必须校验 canonical digest。
- 本地 sandbox 不再把全局 skills root 作为静态映射；但当 host `bash` 被显式启用时，这不能阻止其读取宿主绝对路径，见安全边界。
- AioSandbox/Kubernetes 不再挂载全局 custom skills root。
- `read_file`、`ls`、`grep/glob` 只解析本次 run 投影。
- 隔离 sandbox 内的 `bash` 只能访问映射进容器的投影。
- 投影生成时拒绝 symlink，并限制文件数、单文件大小与总大小。
- run 进入不可恢复的 terminal 状态后异步清理投影并释放 revision pin；queued、interrupted 或可恢复 run 必须保留投影，或能够从 pinned revision 验证后重建。异常退出由 reconciliation job 清理。

### Subagent 与 `skill_manage`

- subagent 必须继承父 run 的 `skill_grants` 和 `skill_projection_id`，不能重新加载全局 enabled skills。
- subagent 只加载其配置允许集合与父授权集合的交集。
- subagent 的授权数据通过显式参数传递，不能依赖 contextvar、`get_effective_user_id()` 或线程局部状态重新解析。
- `skill_manage` 不通过运行投影写文件，而是调用 domain service；共享用户不能编辑被分享 skill。
- owner 使用共享 skill 运行时，skill evolution 也不能在未显式选择的情况下自动修改该 skill。

## 服务端输出脱敏

新增 `deerflow.skills.privacy.SkillContentRedactor`，输入为 internal run context 中的 `skill_grants`、projection manifest 与原始事件，输出用户安全事件。

### 推荐持久化模型

优先采用“双轨”模型：

- **内部执行上下文**：保存 LLM 需要继续推理的 skill 原文或内部消息，不对普通用户 API 暴露。
- **用户可见消息/事件**：只保存脱敏后的 `skill_execution` 占位、工具状态和安全 metadata。

如果短期内仍需把原始 tool call/result 写入 checkpointer，则所有读取 checkpoint/state/history 的用户可见接口必须读时 redaction，且未知 stream mode / 未知消息类型默认 fail-closed。

### 识别范围

识别所有访问运行投影的工具调用及其配对结果，包括：

- `read_file`
- `ls`、`grep`、`glob`
- `bash` 中引用 `/mnt/skills`
- subagent 注入的 skill system/developer messages
- 未来新增的 skill bundle 读取工具

不能只判断 `/mnt/skills` 字符串；应结合 run projection manifest、tool call id、消息关联关系和工具类型。

### 用户可见边界

以下边界在返回或持久化前必须应用 redactor 或只写脱敏事件：

- StreamBridge 发布的全部模式：`messages-tuple`、`values`、`updates`、`debug`、`custom`
- RunEventStore 的 message、tool result 和 full event
- `GET /messages`、`GET /events`
- checkpoint/state/history 相关 API
- External API 的运行结果与错误信息
- 日志、trace 和错误响应中的 tool arguments/result
- 第三方 tracing/observability exporter 捕获的 prompt、system/developer message、tool call/result 和 checkpoint payload

受保护 skill run 默认禁止向第三方 tracing exporter 发送原始 prompt、tool payload 或内部 state。若 exporter 无法在采集前完成可靠脱敏，则该 run 必须禁用对应 tracing，而不是依赖导出后的清洗。

脱敏结果只保留：

```json
{
  "type": "skill_execution",
  "skill_id": "sk_abc123",
  "skill_name": "report-writer",
  "skill_handle": "report-writer-sk_abc123",
  "version_seq": 3,
  "summary": "Loaded skill instructions"
}
```

任何读取内部 state 的 admin/debug 接口都需要独立权限、reason 和审计。

前端只渲染后端提供的脱敏事件，不使用路径检测作为权限兜底。

## Gateway API

所有 custom skill bundle 相关接口统一按权限分类。

### Metadata 可见接口

- `GET /api/skills`
- `GET /api/skills/{skill_id}`

迁移期保留现有 name-based 路由作为兼容入口。第一阶段名称全局唯一，兼容入口解析为 `skill_id` 后执行相同授权逻辑：

- 无匹配：404。
- 有匹配但调用者不可见：404。
- 有且可见：映射成 `skill_id` 后执行相同授权逻辑。

兼容路由不能成为新的持久化契约；新创建的 agent config、API key policy、conversation 和 run metadata 必须存储 `skill_id`。

仅返回调用者可见 skill 的安全 metadata 和 capability 字段：

```json
{
  "skill_id": "sk_abc123",
  "name": "report-writer",
  "display_name": "Report Writer",
  "owner_id": "user_1",
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

shared/authenticated 用户默认没有 content 读取权限。

### Manage 接口

以下接口必须 `require_manage`：

- edit、write/delete file、upload、mkdir
- create version、restore、rollback
- enable/disable
- delete
- share/unshare、visibility

admin 管理其他用户 skill 时，metadata、enable/disable、share/unshare 等不读取 bundle 的操作可使用普通管理权限；edit、restore、rollback、download/export 或任何可能返回/读取 bundle 内容的操作必须额外通过 break-glass reason 与审计。

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
  ∩ API key allowed_skill_ids
  ∩ agent configured skill ids
```

新增 `allowed_skill_ids`，并保留 `allowed_skills` 仅作为输入兼容字段。legacy name 必须在 API key policy 写入或迁移时解析一次并持久化到 `allowed_skill_ids`，run 阶段不得动态重新解析。

兼容语义：

- `allowed_skills=[]` 保持 deny-all。
- `allowed_skill_ids=[]` 也表示 deny-all。
- 如未来需要“允许所有当前可用 skill”，新增明确字段，例如 `allow_all_visible_skills=true`，不能复用空列表。
- legacy name 解析仅在 API key 所属用户可见范围内进行；解析失败时拒绝写入策略。
- External conversation 新增并持久化 `default_skill_id`；迁移完成后 `default_skill_name` 仅用于展示或兼容读取。
- 创建 conversation、创建 run、run 真正开始前都重新检查授权。
- External API 启动的 subagent 同样继承运行授权快照。

## 前端变更

### Skill 管理页

- 展示 owner、visibility、shared、enabled、business category/tags 状态。
- 按 `can_read_content` 和 `can_manage` 控制入口。
- 对只可使用的 skill 仅显示安全 metadata 与“运行”入口。
- 分享弹窗仅 owner/admin 可见。
- local sandbox 不满足严格保护时，隐藏分享入口或展示禁用原因。
- admin break-glass 内容读取要求填写原因并明确提示会被审计。

### 对话与运行页

- 只消费后端脱敏事件。
- skill 读取步骤显示为统一占位步骤。
- “完整对话”也不能展示原始 skill tool call/result。
- 前端路径检测只用于兼容旧数据的视觉隐藏，不作为安全保证；旧数据应通过后端迁移或响应期 redaction 处理。

## Legacy 迁移

### DB 与目录迁移

1. 新增或扩展 DB 表、约束和 repositories，优先兼容已落地的 DB-managed skills schema。
2. 扫描现有 `custom/<name>` bundle，为每个 skill 创建稳定 `skill_id` 与 `storage_key=<legacy-name>`。
3. 为每个 legacy bundle 创建包含完整内容的初始 immutable revision，计算 canonical digest，并将 active working copy 绑定到该 revision。
4. 若部署配置提供明确 `legacy_skill_owner_id`，将 skill 归属给该用户。
5. no-auth 部署可将 legacy skill 归属给 `DEFAULT_USER_ID`。
6. auth 多用户部署未提供 owner 时，设置 `owner_id=NULL`、`visibility='private'`、`status='active'`，仅 admin 管理接口可见、可认领，不进入普通用户 runtime。
7. 不使用“当前 effective user”或“第一个 admin”进行隐式归属。
8. 将 agent config、API key policy、External conversation 等 legacy name 引用解析并持久化为 `skill_id`；无法唯一解析的记录标记为需人工处理，不能在 run 时动态猜测。
9. 后续可通过后台迁移将 legacy 目录重命名为 `skill_id`。

### Orphan reconciliation

启动时或管理任务中检查：

- 文件存在但无 DB row：标记为 orphan，仅 admin 管理接口可见，不加载到 runtime。
- DB row 为 active 但文件缺失或 digest 不匹配：改为 `quarantined`。
- `creating/deleting` 超时：按状态恢复或清理。
- shares 指向不存在用户：清理并写审计。
- active working copy 与 immutable revision 不一致：优先从 active revision 重建；重复失败后标记为 `quarantined`。
- revision prune 前发现 active run pin：跳过该 revision 并记录指标。

所有异常状态默认不可运行。

## 缓存与一致性

现有 skill prompt/enabled cache 是全局缓存，不能直接缓存用户授权结果。

- 全局缓存仅保存 public skill 和不敏感的 bundle metadata / 文件签名。
- custom `list_usable`、run grants 和 prompt skill 列表必须按 user/run 计算。
- cache key 至少包含 user id、授权集合、skill version/digest、agent 配置、sandbox capability。
- share、unshare、visibility、enabled、edit、delete 后发布失效事件。
- 多 worker 部署不能只依赖进程内 cache invalidation；需要 DB version、短 TTL 或共享失效通道。
- custom enabled 从 DB 读取后，`extensions_config.json` 仅作为 public skill enabled 真相源。

## 测试计划

### 权限矩阵

- owner、shared user、authenticated user、unauthorized user、admin 普通运行、admin 管理接口。
- metadata、use、content read、manage、share、delete。
- disabled、quarantined、creating、deleting、system-owned 状态。
- no-auth 单用户模式与 auth 多用户模式分别测试。

### 命名与兼容

- 两个用户并发创建同名 private skill，仅一个成功；失败响应为安全的 `name unavailable`，不包含已有 skill metadata。
- 删除后创建同名 skill，不继承旧 share。
- legacy name-based route：
  - 无匹配返回 404。
  - 可见匹配成功并解析到 `skill_id`。
  - 不可见匹配返回 404。
- API key policy、agent config、External conversation 的 legacy name 在写入/迁移时固化为 `skill_id`，run 阶段不动态解析。
- 所有 runtime map/cache、forced skill 和 tool policy 使用 `skill_id`，不存在 name-keyed 静默覆盖。

### 绕过测试

隔离 sandbox 下硬性覆盖：

- 直接读取 custom content、support files、history、versions。
- 通过 `read_file`、`ls`、`grep/glob`、`bash` 访问未授权 skill。
- subagent 默认加载和显式加载未授权 skill。
- `skill_manage` 编辑、删除或覆盖他人 skill。
- raw SSE、run events、messages、checkpoint/state、External API 中不出现 marker 原文。
- 模拟未知 stream mode / 未知事件类型，验证默认 fail-closed。
- DB/文件系统不一致时不可运行。
- share 在排队后、执行前被撤销时，run 被拒绝。
- share 在 run 启动后被撤销时，当前 run 使用固定快照，后续 run 被拒绝。
- API key `allowed_skills=[]` / `allowed_skill_ids=[]` 保持 deny-all。
- DB/ACL repository 不可用或 `database.backend=memory` 且处于 auth 模式时，custom listing、content API 和 run 全部 fail-closed，不回退到全局 custom 目录。
- provider 从隔离模式降级后，已有 share 与 `authenticated` skill 的新 run 被拒绝。
- 投影和 Agent 可见文件中不出现 `storage_key`、owner ID、内部 revision location 或完整 projection manifest。
- 受保护 skill run 的第三方 trace/export payload 不包含原始 prompt、tool result 或内部 state。

local sandbox 下覆盖：

- provider capability 不满足严格保护时，普通用户分享入口、分享 API、`visibility=authenticated` 和已有共享 skill 的新 run 被禁用。
- trusted-local override 开启时，响应和 API 仍脱敏，但测试明确不把宿主绝对路径 `bash` 绕过作为可阻止能力。

测试使用唯一 marker，例如 `SECRET_SKILL_MARKER_123`：

- 硬性断言：所有用户可见 tool call/result、stream、event、message、checkpoint/state 和 API 响应中都不存在来自受保护 bundle 的原始 marker。
- best-effort 断言：最终模型回答经过精确 marker/长片段检测；模型对内容的改写或复述不属于本方案能够严格保证的范围。

### 一致性与并发测试

- 两个用户并发创建同名 skill，验证全局唯一约束与安全冲突响应。
- edit/delete/run 并发。
- create/install 中途失败。
- 多 worker cache 失效。
- 投影创建后 digest 校验失败。
- 投影清理失败与服务重启后的 reconciliation。
- active/queued/interrupted/resumable run pin 的 revision 不会被 prune。
- terminal run 清理投影并释放 revision pin 后，过期 revision 可被 prune。
- interrupted/resumable run 的投影保留或可从 pinned revision 重建。

## 可观测性

至少提供：

- 授权拒绝计数，按入口、原因、sandbox capability 分类。
- run grant、projection 创建和清理耗时。
- projection 文件数和总大小。
- active revision pin 数量、被 pin 阻止的 prune 次数。
- orphan/quarantined skill 数量。
- redactor 命中、未知事件类型、fail-closed 次数。
- admin break-glass 内容读取审计。
- local sandbox 下分享能力被禁用的计数。
- 受保护 run 禁用第三方 tracing 或 tracing redaction 的计数。

日志中不得记录 skill 原文、support file 内容、未脱敏 tool arguments/result、内部 checkpoint 原文。

## 推荐实施顺序

1. 明确并验收安全边界、sandbox capability 与权限矩阵。
2. 修订或废弃 DB-managed skills 计划中的冲突任务，归并 schema、真相源、migration、repositories 与 reconciliation。
3. 将 custom enabled 真相源迁移到 DB，public skill 继续由 `extensions_config.json` 管理；auth 模式移除 custom LocalSkillStorage fallback。
4. 完成全链路 `skill_id` 化：runtime `Skill`、agent config、forced skill、prompt cache、External API policy/conversation 和 tool policy；本期继续保持全局 name 唯一。
5. 实现 domain `SkillAccessService`，覆盖完整 bundle、provider capability 和 DB fail-closed 权限测试。
6. create/edit/delete/install 状态机、完整 immutable revision、canonical digest 和 revision pin。
7. run grant、pinned revision/digest 绑定和 per-run 只读投影。
8. 改造 sandbox provider capability/conformance test、sandbox mount/path mapping、subagent、prompt、`skill_manage`，关闭 runtime 绕过。
9. 服务端 redactor 或内部上下文/用户事件双轨，覆盖 SSE、events、messages、checkpoint/state、External API 和第三方 tracing。
10. Gateway skill routers 与 External API policy，新增 `skill_id` 优先的 API。
11. 前端管理页、分享能力和脱敏步骤视图。
12. 绕过测试、并发测试、E2E、local sandbox 降级测试与灰度发布。

在第 7 至第 9 步完成前，不应向普通用户开放 custom skill 分享功能。local sandbox 不满足严格保护时，即使上述步骤完成，也不默认开放“可使用但不可读内容”的分享功能。

## 方案取舍

### 采用：DB ACL + per-run custom 只读投影 + sandbox capability gate

- 优点：保留现有 skill bundle 格式；隔离 sandbox 下 runtime 看不到未授权 custom skill；local sandbox 风险被明确降级而不是误承诺。
- 缺点：需要改造 sandbox mount/path mapping、prompt、subagent、cache 和 API，并承担 custom 投影创建和清理成本。

### 采用：`skill_id` 作为稳定身份，本期保持 `skill_name` 全局唯一

- 优点：删除重建不继承权限；在现有 name-keyed runtime 完成迁移前避免静默覆盖、配置歧义和错误执行。
- 缺点：名称可用性存在有限侧信道，且暂不支持多租户自然同名；创建冲突必须使用安全的通用错误响应。

### 后续可选：完成全链路 ID 化后开放 per-owner 同名

- 前置条件：runtime、cache、agent config、forced skill、External API conversation/policy、tool policy、路由与持久化关系均不再依赖 name 作为身份。
- 放宽约束需要独立 migration、兼容策略、同名展示和绕过测试，不作为本期 sharing 上线内容。

### 不采用：仅 Gateway ACL + UI 脱敏

- 改动较小，但 `read_file`、bash、subagent、history/version、raw SSE/events 均可绕过，不能作为权限方案。

### 不采用：local sandbox 下声称严格内容保密

- 当前 local sandbox 不提供经过验证的宿主文件系统隔离；默认 host `bash` 虽然禁用，但部署者可显式开启，其他未来宿主执行能力也可能扩大暴露面。继续承诺严格内容保密会造成错误安全感。

### 后续可选：受控 Skill Execution Service

- 将敏感 skill 实现为服务端工作流，LLM 只获得结构化接口。
- 能提供更强内容保密，但需要重构 skill 执行模型，不纳入本期。
