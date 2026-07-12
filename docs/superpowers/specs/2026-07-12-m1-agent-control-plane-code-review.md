# 多租户 Agent 发布平台 - M1 代码评审
**状态：** 已评审，待修复
**日期：** 2026-07-12

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)

**评审范围：**

- 分支：`feat/m1-agent-control-plane`
- 基线：`680507df45e1baccfe35e40177563fc612403033`
- 评审头：`262dc71aab38492e7a1e667f05a012bd68f48907`
- 范围：M1 控制平面与 Release 管理实现，包括持久化实体/仓储、草稿服务、发布服务、导入服务、Gateway 路由、对话式工具镜像写入，以及对应测试

---

## 1. 评审结论

本次 M1 实现的整体方向与设计文档一致，核心骨架已经搭建完成：

- 已引入 `published_agents` / `agent_drafts` / `agent_releases` / `skill_revisions` 等新实体；
- 已实现草稿编辑、发布校验、不可变 Release、回滚、导入旧 Agent 的主流程；
- 已补充较完整的单元测试与集成测试；
- 关键 M1 测试在本地抽样执行中通过。

但当前实现仍存在数项阻断性问题，尚不建议合并进入主干：

1. 存在会在 PostgreSQL 下直接失败的主键长度问题；
2. 真实 Gateway 启动链路未把新服务接通，控制平面接口在运行环境中不可用；
3. 草稿 PATCH 的并发冲突处理不是原子的，会出现“返回 409 但部分字段已经写入”的数据一致性问题。

除此之外，还存在若干重要但非立即阻断的问题，包括 Skill 可见性元数据不可信、并发发布下的唯一键竞争、以及对话式创建/更新与草稿事实来源未完全对齐。

**结论：当前 M1 不满足“Ready to merge”标准，建议先修复本文列出的 Critical 与 Important 问题后再进入下一里程碑。**

---

## 2. 做得好的部分

- 持久化模型、仓储、发布服务、导入服务和路由边界基本清晰，仍然遵守了 harness / app 分层约束。
- `agent_releases` 的“无 update API”设计与测试联动，较好地落实了 Release 不可变约束。
- `content_store` 抽象和 `LocalContentStore` 的原子写入思路合理，为后续对象存储替换预留了空间。
- 发布校验器单独拆出为纯函数，便于测试与后续扩展。
- M1 相关测试覆盖面较完整，尤其是 repo、draft service、publish service、import service 与 router 层都有专门用例。

---

## 3. 发现的问题

### 3.1 Critical-1：ID 列长度与实际生成值不匹配，PostgreSQL 下会直接插入失败

**相关文件：**

- [published_agent/model.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/published_agent/model.py#L41)
- [published_agent/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/published_agent/sql.py#L89)
- [agent_release/model.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/agent_release/model.py#L39)
- [agent_release/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/agent_release/sql.py#L67)
- [skill_revision/model.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/skill_revision/model.py#L36)
- [skill_revision/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/skill_revision/sql.py#L58)

**问题说明：**

多个主键 / 外键列声明为 `String(32)`，但实际生成的 ID 为：

- `pa_` + 32 位 hex，长度 35
- `rel_` + 32 位 hex，长度 36
- `skr_` + 32 位 hex，长度 36

例如：

- `PublishedAgentRow.id` 是 `String(32)`，但 `PublishedAgentRepository.create_agent()` 生成 `pa_{uuid4().hex}`
- `AgentReleaseRow.id` 是 `String(32)`，但 `AgentReleaseRepository.create()` 生成 `rel_{uuid4().hex}`
- `SkillRevisionRow.id` 是 `String(32)`，但 `SkillRevisionRepository.get_or_create()` 生成 `skr_{uuid4().hex}`

SQLite 对字符串长度基本不做强校验，因此测试可以通过；PostgreSQL 会按列宽严格校验，插入时会直接报错。

**影响：**

- 违反 M1 规格中“SQLite / PostgreSQL 均可执行”的要求；
- 真实生产环境如果使用 PostgreSQL，将在创建 Agent、创建 Release 或写 Skill Revision 时直接失败；
- 同类问题还会连带影响 `current_release_id`、`agent_drafts.agent_id`、Release 子表引用等字段。

**建议修复：**

- 将相关 ID / FK 列统一扩为足以容纳当前 ID 格式的长度，例如 `String(40)` 或 `String(64)`；
- 同步修正 Alembic 迁移与测试断言；
- 补一个 PostgreSQL 真实写入测试，而不只是 SQLite create_all / migration 测试。

---

### 3.2 Critical-2：真实 Gateway 启动链路没有把新服务挂到 `app.state`，控制平面接口实际不可用

**相关文件：**

- [published_agents.py](/D:/cursorcode/deer-flow/backend/app/gateway/routers/published_agents.py#L47)
- [app.py](/D:/cursorcode/deer-flow/backend/app/gateway/app.py#L176)
- [factory.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/factory.py#L17)
- [factory.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/factory.py#L56)

**问题说明：**

路由层明确依赖：

- `request.app.state.draft_service`
- `request.app.state.publish_service`
- `request.app.state.import_service`

但在 `app.gateway.app.lifespan()` 中并没有任何地方为这三个字段赋值。也就是说，测试里虽然通过 dependency override 可以跑通，但真实 Gateway 启动后，请求这些接口会直接走到：

- `503 Published-agent service not available`
- `503 Publish service not available`
- `503 Import service not available`

进一步看，`build_publish_service()` 本身也未完成 wiring：它只传了 repo 和 content_store，没有传 `PublishService.__init__()` 所需的 `skills_index`、`connector_repo`、`model_index`、`tool_group_whitelist` 等必需参数，因此就算接到启动链路里，也无法正确构造。

**影响：**

- M1 的核心交付“通过 Gateway API 完成创建 / 编辑 / 发布 / 回滚”在真实运行环境中并未成立；
- 当前 router 测试给出的是“伪接通”信号，无法代表生产可用性；
- 后续 M4 前端如果直接接这些接口，会立刻遇到运行时 503。

**建议修复：**

- 在 Gateway lifespan 或等价启动路径中，显式构建并挂载 `draft_service`、`publish_service`、`import_service`；
- 完成 `build_publish_service()` 依赖注入，包括：
  - 平台 Skill 索引
  - Connector repo 适配器
  - 可用模型索引
  - 平台 tool group 白名单
  - 平台 quota 默认值
- 增加一个真实 `create_app()` / lifespan 级测试，验证路由不依赖测试 override 也能获得 service。

---

### 3.3 Critical-3：`PATCH /draft` 的 revision 冲突不是原子的，返回 409 时子表改动可能已经落库

**相关文件：**

- [published_agents.py](/D:/cursorcode/deer-flow/backend/app/gateway/routers/published_agents.py#L170)
- [draft_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/draft_service.py#L132)

**问题说明：**

当前 `PATCH /api/published-agents/{agent_id}/draft` 的执行顺序是：

1. 如果请求包含 `skills`，先执行 `service.set_skills(...)`
2. 如果请求包含 `connector_grants`，先执行 `service.set_connector_grants(...)`
3. 最后才执行带 `revision` 检查的 `service.update_draft(...)`

这意味着：

- 当 `revision` 已过期时，主 draft 行会因为 optimistic concurrency 返回 409；
- 但 `skills` / `connector_grants` 子表已经先被替换成功。

本地复现结果如下：

- 第二次 PATCH 返回 `409`
- 随后 GET draft，`skills` 已经变成新的值

也就是接口表现为“失败”，但数据已经部分生效。

**影响：**

- 违反 draft 更新的原子性预期；
- 会导致并发编辑下出现难以排查的数据错乱；
- 破坏 M1 规格中 revision 冲突用于防止覆盖写的语义。

**建议修复：**

- 将 draft 主表和子表的更新合并到同一个事务、同一个 revision 检查之下；
- 最好把 `skills` / `connector_grants` 的替换下沉到一个统一的 `update_draft_bundle(...)` 服务方法中；
- 增加回归测试：带过期 `revision` 的 PATCH 返回 409 后，draft 主表与子表都必须保持不变。

---

### 3.4 Important-1：Skill 的 `source` / `visibility` 元数据目前信任调用方输入，导入流程还会误标私有 Skill

**相关文件：**

- [draft_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/draft_service.py#L132)
- [import_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/import_service.py#L152)
- [publish_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/publish_service.py#L168)
- [skills_index.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/skills_index.py#L16)

**问题说明：**

当前逻辑只验证“某个 skill 是否可被当前 owner 选择”，但不会重新推导该 skill 的真实来源分类：

- `DraftService.set_skills()` 直接接受请求里的 `source`
- `AgentImportService.import_agent()` 对所有可导入 skill 一律写成 `source="public"`
- `PublishService.publish()` 直接依据 `entry["source"]` 决定 `owner_user_id` 和 `visibility`

这会带来两个问题：

1. 客户端可以把本应为 public 的 skill 写成 private，或反之；
2. 导入旧 Agent 时，如果 skill 实际是当前用户的私有 skill，也会被误记成 public revision。

**影响：**

- 污染 `skill_revisions` 中的 ownership / visibility 元数据；
- 与设计文档中“平台自己记录 ownership/visibility 分类”的原则不一致；
- 为后续发布比对、权限审计、M2 resolver 及 M4 UI 带来隐性偏差。

**建议修复：**

- 不信任请求方传入的 `source`；
- 由 `SkillsIndex` 暴露更完整的元数据查询，例如 `resolve(name, owner_user_id) -> {visibility, owner}`；
- 导入流程和发布流程统一按平台解析结果写入，不从客户端或 legacy config 直接继承 `source`。

---

### 3.5 Important-2：并发发布的唯一键竞争未处理，`release_no` 与 `skill_revision` 都可能在竞争下报 500

**相关文件：**

- [agent_release/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/agent_release/sql.py#L53)
- [skill_revision/sql.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/persistence/skill_revision/sql.py#L38)
- [publish_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/publish_service.py#L179)

**问题说明：**

当前并发路径中存在两个典型的“先查后插”竞争窗口：

1. `next_release_no()` 用 `MAX(release_no)+1` 计算下一个号；
2. `SkillRevisionRepository.get_or_create()` 先查是否存在，再决定插入。

在并发 publish 场景下：

- 两个请求可能同时得到相同的 `release_no`；
- 两个请求也可能同时认为某个 skill revision 不存在，然后一起插入同一唯一键；
- 当前代码没有对 `IntegrityError` 做重试或转换，因此最终更可能变成 500。

**影响：**

- 不能稳定满足开发计划里“连续发布得到 1,2,3 且不重复”的要求；
- 生产环境中会出现偶发 publish 失败；
- 这类问题在单线程 SQLite 测试下不容易暴露。

**建议修复：**

- 为 `release_no` 分配引入数据库级并发保护，例如：
  - 在同一事务内锁定 agent 相关行后分配；
  - 或直接依赖唯一键冲突重试；
- 为 `skill_revisions.get_or_create()` 增加唯一键冲突后的重新读取逻辑；
- 补充并发测试，而不是只验证串行场景。

---

### 3.6 Important-3：对话式 `setup_agent` / `update_agent` 没有把所有关键字段同步进 draft 事实来源

**相关文件：**

- [setup_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py#L16)
- [setup_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/setup_agent_tool.py#L101)
- [update_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py#L36)
- [update_agent_tool.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/tools/builtins/update_agent_tool.py#L277)

**问题说明：**

当前“文件系统写入后 best-effort 镜像到 draft”只同步了部分字段：

- `setup_agent` 只创建了 draft identity，没有把 `soul_markdown`、`description`、`skills` 同步进去；
- `update_agent` 只镜像了 `soul`、`model`、`tool_groups`，没有同步 `description` 和 `skills`。

这意味着对话式创建 / 更新一个 Agent 后，Studio 侧看到的 draft 可能和真实文件系统状态不一致。

**影响：**

- 不满足 M1 规格中“结构化编辑与对话式编写落到同一个事实来源”的目标；
- M4 Studio 上线后，用户可能看到缺字段、旧字段或错误字段；
- 后续 publish 可能基于不完整 draft 生成错误 Release。

**建议修复：**

- 将 `setup_agent` / `update_agent` 的镜像逻辑补全到和文件系统写入同等语义；
- 至少同步：
  - `soul_markdown`
  - `description`
  - `skills`
  - `tool_groups`
  - `model_name`
- 为这两条工具补单元测试，验证文件系统状态与 draft 状态一致。

---

### 3.7 Important-4：部分路由的 HTTP 语义与实现规格不一致

**相关文件：**

- [publish_service.py](/D:/cursorcode/deer-flow/backend/packages/harness/deerflow/publishing/publish_service.py#L136)
- [published_agents.py](/D:/cursorcode/deer-flow/backend/app/gateway/routers/published_agents.py#L255)
- [published_agents.py](/D:/cursorcode/deer-flow/backend/app/gateway/routers/published_agents.py#L274)

**问题说明：**

当前有两处契约偏差：

1. `publish()` 找不到 agent 时抛 `PublishError(AGENT_NOT_FOUND)`，router 统一映射成 `422 publish_validation_failed`，而不是规格要求的 `404`；
2. `GET /{agent_id}/releases` 对跨 owner 或不存在的 agent 返回空数组，而不是 `404`。

**影响：**

- 前端很难区分“资源不存在”和“资源存在但当前没有历史”；
- 与 M1 实现规格中的 API 契约不一致；
- 会给 M4 前端和后续外部说明文档带来歧义。

**建议修复：**

- 将“找不到 agent”与“发布校验失败”分开建模；
- 对不存在或跨 owner 的 release history 查询返回明确 `404`；
- 补一组契约测试，锁定这些状态码行为。

---

## 4. 验证记录

本次评审执行了以下验证：

### 4.1 M1 相关测试抽样

执行：

```powershell
$env:PYTHONPATH='.'
cd backend
uv run pytest tests/test_published_agent_repo.py tests/test_agent_release_repo.py tests/test_draft_service.py tests/test_publish_service.py tests/test_published_agents_router.py tests/test_agent_import.py -q
```

结果：

- `57 passed`

执行：

```powershell
$env:PYTHONPATH='.'
cd backend
uv run pytest tests/test_publish_validation.py tests/test_publishing_content_store.py tests/test_publish_instructions.py -q
```

结果：

- `30 passed`

### 4.2 运行时接线验证

执行：

```python
from app.gateway.app import create_app
app = create_app()
print(hasattr(app.state, 'draft_service'), hasattr(app.state, 'publish_service'), hasattr(app.state, 'import_service'))
```

结果：

```text
False False False
```

说明真实 `create_app()` 结果中没有挂载这三个 service。

### 4.3 PATCH 原子性复现

执行了一个最小复现脚本：

1. 创建 agent
2. 用 `revision=1` 成功更新一次 draft
3. 再用过期 `revision=1` 提交一个只改 `skills` 的 PATCH
4. 读取当前 draft

结果：

- PATCH 返回 `409`
- 但 `draft.skills` 已经被改成新值

说明当前冲突处理存在“失败但部分生效”的一致性问题。

### 4.4 ID 长度验证

执行：

```python
print(len('pa_' + 'a'*32), PublishedAgentRow.__table__.c.id.type.length)
print(len('rel_' + 'a'*32), AgentReleaseRow.__table__.c.id.type.length)
print(len('skr_' + 'a'*32), SkillRevisionRow.__table__.c.id.type.length)
```

结果：

```text
35 32
36 32
36 32
```

说明列宽与实际生成值不一致。

---

## 5. 建议修复顺序

建议按以下顺序处理：

1. 修复所有 ID / FK 列宽问题，并同步更新 Alembic；
2. 打通真实 Gateway 启动链路，把 3 个 service 正确挂到 `app.state`，并补充非 override 的 app-level 测试；
3. 重构 draft PATCH 更新流程，保证主表与子表在 revision 约束下原子提交；
4. 收紧 Skill 来源元数据生成逻辑，不再信任客户端 `source`；
5. 为并发发布路径补唯一键冲突处理与测试；
6. 补齐 `setup_agent` / `update_agent` 与 draft 的字段对齐；
7. 收敛路由返回码，使其与 M1 实现规格一致。

---

## 6. 最终评审结论

**Ready to merge：No**

**原因：**

- 存在会在 PostgreSQL 下直接失败的 schema 问题；
- 真实运行环境中的 Gateway 路由尚未接通；
- 草稿 PATCH 存在明确的数据一致性缺陷。

在上述问题修复之前，不建议将本次 M1 作为可交付完成态推进到下一里程碑。
