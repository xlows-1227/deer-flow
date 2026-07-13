# 多租户 Agent 发布平台 - M1 第十五轮代码复审

**状态：** 已复审，待修复  
**日期：** 2026-07-14

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第十四轮复审：[2026-07-14-m1-agent-control-plane-code-fourteenth-review.md](./2026-07-14-m1-agent-control-plane-code-fourteenth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 复审头：`cf84822c13b50c668877e55dfd535e56018a5fed` + 当前未提交工作区
- 本轮重点：第十四轮 2 个 Important 与 2 个 Minor 的关闭情况，以及发布/对话式 authoring 锁序、Connector 能力授权有效性、控制平面请求边界和导入失败恢复

---

## 1. 复审结论

第十四轮的草稿单 SQL 发布快照、最终 revision 重检、Alembic logger 副作用和 slug 契约均已按正确方向修复。普通结构化 PATCH 在发布快照后提交时，发布会在写 Release 前返回 `DRAFT_REVISION_CONFLICT`；logger 顺序回归与大小写 slug 路由也已经通过。

Skill 发布修复完成了“解析后不再重读”和缺失内容 fail closed，但仍没有保证 `declared_connector_caps` 与最终固化的 `SKILL.md` bytes 来自同一内容版本。本轮另发现发布与对话式 authoring 的行锁顺序未统一，以及 Connector grant 只校验实例归属、不校验该实例类型是否支持被授予 capability。

本轮仍有 **0 个 Critical、3 个 Important、2 个 Minor**：

1. Skill 的 caps/enabled/owner 元数据先由 `load_skills()` 解析，文件 bytes 随后才从目录读取；并发改写可把旧 caps 与新 `SKILL.md` 固化到同一个 revision。
2. duplicate setup/update authoring 按“identity → draft”加锁，publish 的 joined `FOR UPDATE` 从 draft 查询进入且未限定 `OF`，会同时锁 draft/identity；两条路径存在反向锁序和 PostgreSQL deadlock/事务中止风险。
3. Connector grant 的 capability 不与 Connector type 的权威能力集合求交；任意 active owned Connector 上填写同名 capability 就能满足 Skill 要求并进入 Release。
4. `PatchDraftRequest` 使用无结构的 `dict[str, str]`，空对象、空 capability 和重复项可越过 422 边界，最终形成 `KeyError`/`IntegrityError` 500。
5. 旧 Agent 导入仍由三个独立提交组成；中途失败会留下部分 Agent，重试又被重复 slug 阻断。

**结论：Ready to merge：No。** 三个 Important 关闭前，不建议进入 M2。

---

## 2. 已确认修复

- **发布草稿混合快照已关闭**：`get_publish_snapshot()` 用单条 joined SQL 返回 draft、Skills 与 Connector grants；Skills/grants 直接替换方法会锁定并递增 draft revision。
- **普通 PATCH/发布竞态已关闭**：发布写事务会锁定 owner-scoped draft 并复核捕获的 revision；不一致时不写 Skill revision、Release 或 current pointer，并由 Gateway 映射为 409。
- **Skill 缺失与后续重读已关闭**：发布校验、checksum、content store 和 revision 写入都消费冻结的 `SkillPublishSnapshot`；缺目录、缺非空 `SKILL.md`、禁用或 private owner 不匹配均 fail closed。
- **Alembic logger 副作用已关闭**：`fileConfig(..., disable_existing_loggers=False)` 保持应用 logger 启用，原顺序依赖最小复现已通过。
- **slug 契约已关闭**：结构化创建、DraftService、旧导入、Gateway assistant 映射和 runtime loader 复用大小写保留的 `[A-Za-z0-9-]{1,64}` 校验。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：Skill 元数据与文件 bytes 仍可来自不同内容版本

**相关文件：**

- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [skill_revision/sql.py](../../../backend/packages/harness/deerflow/persistence/skill_revision/sql.py)
- [test_publishing_adapters.py](../../../backend/tests/test_publishing_adapters.py)

**问题说明：**

`StorageSkillsIndex._ensure_index()` 先调用 `storage.load_skills()`，把 `enabled`、visibility、owner 与 `skill.connector_requirements` 缓存到字典；`_resolve_publish_snapshot()` 随后才遍历 `skill_dir` 并读取文件 bytes。虽然二者使用同一个 `StorageSkillsIndex` 实例，但仍是两个不同时间点的读取。

确定性复现中，fake storage 在返回已解析的旧 Skill 对象前把磁盘 `SKILL.md` 从 `old.read` 改成 `new.write`。当前 snapshot 得到：

```text
declared_connector_caps = ('old.read',)
SKILL.md bytes           = requires: new.write
```

现有“先 resolve、再修改文件、snapshot 仍保存旧 bytes”测试只证明 snapshot 创建完成后不会重读，无法覆盖 `load_skills()` 与文件复制之间的窗口。

该问题还会被内容去重放大：`content_checksum` 只由 skill name + files 计算。同一份新 bytes 一旦首次以旧 caps 写入 revision，后续正确解析出的新 caps 会命中同一 checksum 并复用错误的 canonical row。

**影响：**

- Release 的 Skill 源码与声明能力不一致，审计和运行时最小权限输入失真。
- 新增 capability 可能未被要求授权；已删除 capability 可能继续阻断发布。
- 错误 caps 可能因内容寻址去重长期固化，不能靠下一次发布自动纠正。

**建议修复：**

- 先捕获完整文件 bytes，再从捕获到的 `SKILL.md` bytes 解析 connector requirements；revision 的 caps 必须由同一份 bytes 派生。
- enabled/visibility/owner 与文件树若没有原子版本接口，应使用 Skill storage 的 version/snapshot 能力或“读取前后版本指纹一致，否则重试/拒绝”的 fail-closed 协议。
- `SkillRevisionRepository` 在命中相同 checksum 时可增加不变量校验：visibility/owner/caps/content_ref 与派生值不一致应报错，不能静默复用。
- 新增确定性测试：在 `load_skills()` 返回与文件 capture 之间修改 `SKILL.md`，最终只能拒绝或得到同一 bytes 派生的 caps。

### 4.2 Important-2：publish 与对话式 authoring 的行锁顺序不统一

**相关文件：**

- [published_agent/sql.py](../../../backend/packages/harness/deerflow/persistence/published_agent/sql.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [agent_release/sql.py](../../../backend/packages/harness/deerflow/persistence/agent_release/sql.py)
- [test_authoring_postgres_concurrency.py](../../../backend/tests/test_authoring_postgres_concurrency.py)

**问题说明：**

`PublishedAgentRepository.setup_authoring_bundle()` 与 `update_authoring_bundle()` 明确先对 `published_agents` identity 执行 `SELECT ... FOR UPDATE`，再锁 `agent_drafts`。而 publish 的 `lock_revision_for_publish()` 以 `AgentDraftRow` 为查询起点，join `PublishedAgentRow` 后调用未指定 `of=` 的 `.with_for_update()`。

在 PostgreSQL 中，这个 joined `FOR UPDATE` 会尝试锁定查询涉及的两张表。于是并发路径可能形成：

```text
authoring: 已持有 identity，等待 draft
publish:   已持有 draft，等待 identity
```

随后 `create_and_point()` 还必须更新 identity 的 current pointer。PostgreSQL 会通过 deadlock detector 中止其中一个事务；当前 publish 只处理 release number 的 `IntegrityError`，不会把 deadlock/serialization failure 转换为可重试冲突，API 将出现 500。

现有 PostgreSQL 测试覆盖“duplicate setup 与 PATCH”和“publish snapshot 与 PATCH”，但没有覆盖 publish 与 duplicate setup/update 的锁交叉。

**影响：**

- owner 在对话式编辑与发布并发时可能收到偶发 500，即使两条业务操作各自合法。
- 数据库会安全回滚被中止事务，但发布可用性与调用方重试语义不稳定。
- 查询计划或数据库版本变化可能改变复现概率，使问题难以靠普通单测发现。

**建议修复：**

- 为 identity + draft 定义唯一锁顺序，建议统一为 `published_agents → agent_drafts`。
- publish 在最终事务内先锁 owner-scoped identity，再锁/复核 draft revision；`create_and_point()` 复用已锁 identity，不再隐式改变锁序。
- 如仍使用 joined lock，显式指定 `with_for_update(of=...)`，不要依赖查询计划决定多表锁顺序。
- 增加 PostgreSQL 双连接 barrier 测试：authoring 持有第一把锁时启动 publish，验证二者完成/返回 revision 冲突，而不是 deadlock 或 500；CI 继续用 `REQUIRE_POSTGRES_TESTS=1` 强制执行。

### 4.3 Important-3：Connector capability 未与 Connector type 能力集合校验

**相关文件：**

- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)
- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py)
- [validation.py](../../../backend/packages/harness/deerflow/publishing/validation.py)
- [schemas.py](../../../backend/packages/harness/deerflow/connectors/schemas.py)

**问题说明：**

`ConnectorServiceRepo.get_instance()` 会确认实例 active、type 已注册且平台启用，但丢弃 `ConnectorTypeDefinition.capabilities`。`DraftService` 对每条 grant 只检查实例是否可获取；`validate_draft_for_publish()` 又只检查 Skill 所需 capability 字符串是否出现在任意 grant 中，不检查该 grant 指向的 Connector type 是否支持该 capability。

专项复现中，Skill 要求 `database.query`，草稿却把 `database.query` 授给只支持 `mail.send` 的 Gmail Connector。即使同步 connector adapter 明确返回该实例的支持能力，当前 validator 仍返回空 violations：

```text
[]
```

这意味着任意 active、owner-owned Connector 都可以被用来“占位”满足不相关的 Skill 要求。

**影响：**

- 发布成功不再代表 Connector 要求可执行，M2 外部运行时会拿到不可用的 Release。
- 若后续 resolver 只信任 Release grant 字符串，可能扩大 capability 白名单，破坏设计要求的四方最小权限交集。
- UI 无法可靠展示“已满足/未满足”的 Connector 要求。

**建议修复：**

- `ConnectorServiceRepo` 从权威 `ConnectorTypeDefinition` 返回不可变的 `supported_capabilities`。
- Draft 保存和 publish 校验都要求 `grant.capability in instance.supported_capabilities`。
- Skill 要求只能由“属于 owner、active、type 已启用且确实支持该 capability”的具体 Connector grant 覆盖。
- 增加错误 capability、错误 Connector type、正确 type/capability 和 type 在校验后禁用的回归测试。

---

## 5. Minor 问题

### 5.1 PATCH draft 的嵌套请求体缺少结构化校验

[published_agents.py](../../../backend/app/gateway/routers/published_agents.py) 将 `skills` 与 `connector_grants` 定义为 `list[dict[str, str]]`。Pydantic 会接受 `skills=[{}]`、`connector_grants=[{}]` 以及空 capability；生产 `DraftService` 随后直接访问 `entry["skill_name"]` / `entry["connector_instance_id"]`，缺字段会抛出未映射的 `KeyError`。重复 skill/grant 还会在数据库复合主键处形成 `IntegrityError`。这些客户端输入错误会返回 500，而不是规格中的 422。

建议为 Skill selection 与 Connector grant 定义 `extra="forbid"` 的嵌套 Pydantic 模型，限制非空字段和长度，并在服务写入前做去重/重复项 422；同时补真实路由测试，确保所有畸形输入不进入仓储。

### 5.2 旧 Agent 导入不是原子操作，失败后不可安全重试

[import_service.py](../../../backend/packages/harness/deerflow/publishing/import_service.py) 依次调用 `create_agent()`、`update_with_revision()` 与 `replace_skills()`，三者分别提交。第二或第三步失败时，identity 和部分 draft 已永久存在；再次导入又会因 owner + slug 唯一约束返回 `ImportAlreadyExistsError`。`update_with_revision()` 的返回值也没有检查。

建议增加专用 import authoring UOW，在一个事务中创建 identity、映射 draft 和写 Skills；或实现可证明幂等的 resume/cleanup 语义。新增重复 skill、draft flush 失败与 skills 写失败测试，断言失败后无部分 Agent，或重试可以完成同一导入。

---

## 6. 第十四轮问题关闭状态

| 第十四轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：发布草稿读取/写入不在同一一致性边界 | **主体已关闭** | 单 SQL snapshot + revision 行锁复核已覆盖普通 PATCH；新增 identity/draft 锁序问题见 Important-2 |
| Important-2：Skill 校验与固化使用多个独立快照 | **部分关闭** | 后续不再重读、缺失 fail closed 已完成；caps 与文件 bytes 仍可混版，见 Important-1 |
| Minor-1：Alembic 禁用应用 logger | **已关闭** | `disable_existing_loggers=False` + 顺序回归通过 |
| Minor-2：slug 创建与运行时契约不一致 | **已关闭** | 统一大小写保留的严格校验，创建/assistant/runtime/import 测试通过 |

---

## 7. 验证记录

### 7.1 静态检查

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
652 files already formatted

git diff --check
通过（仅有两个既有 CRLF 提示，无 whitespace error）
```

### 7.2 第十四轮修复专项

```text
134 passed, 3 skipped, 2 warnings in 32.29s
```

覆盖 publish service/repository、Skill adapter、PostgreSQL 并发门禁、Gateway logger/slug、runtime loader、真实 HTTP 下一轮运行、Import 与迁移。跳过项包含本地无 PostgreSQL 的双连接用例。

Alembic logger 顺序最小复现：

```text
4 passed in 7.43s
```

### 7.3 当前 M1 改动测试回归

除 `test_uploads_router.py` 外的 21 个改动/新增测试文件：

```text
409 passed, 4 skipped in 43.28s
```

uploads 文件排除两个本机缺少 Windows symlink 特权、在测试准备阶段失败的用例后：

```text
30 passed, 2 deselected in 2.28s
```

因此与实现规格第 39 节口径一致，当前等价覆盖为：

```text
439 passed, 4 skipped, 2 deselected
```

未排除运行中的两个 `WinError 1314` 不进入业务代码，不作为本轮 M1 回归。

### 7.4 专项反例

Skill 元数据/bytes 混版复现：

```text
caps = ('old.read',)
captured SKILL.md = requires: new.write
```

错误 Connector type/capability 复现：

```text
Gmail supported capabilities = ['mail.send']
grant capability             = 'database.query'
publish violations           = []
```

请求模型边界复现：`skills=[{}]`、`connector_grants=[{}]` 与空 capability 均被 `PatchDraftRequest` 接受。

### 7.5 全量与 PostgreSQL

本地 `uv run pytest -q` 在 244 秒执行窗口内未完成，未取得全仓最终结果；这不是额外的断言失败，但 `make test` 完整门禁仍需以 CI 完成结果为准。

本地没有可用 PostgreSQL，两个双连接用例跳过；CI 已设置 `TEST_POSTGRES_URL` 与 `REQUIRE_POSTGRES_TESTS=1`。Important-2 需要新增 publish/authoring 交叉锁序测试后由 PostgreSQL job 给出证据。

---

## 8. 修复优先级

1. 让 Skill caps 从最终捕获的 `SKILL.md` bytes 派生，并对同 checksum revision 的元数据不变量 fail closed。
2. 统一 identity/draft 的行锁顺序，补 publish 与 duplicate setup/update 的 PostgreSQL 交叉测试。
3. 将 Connector grant capability 与权威 Connector type 能力集合求交。
4. 给 PATCH draft 的 Skills/grants 增加结构化请求模型、非空约束与去重。
5. 把旧 Agent 导入收敛到单事务或可安全重试的幂等流程。

---

## 9. 最终结论

第十四轮四项问题中，logger 与 slug 已完全关闭，发布 draft revision 一致性也已覆盖普通 PATCH；Skill 快照则完成了对象冻结，但尚未完成“元数据必须由同一份固化 bytes 派生”的最后一步。

当前 Release 仍可能携带与 Skill 源码不一致的 connector caps，也可能通过不支持该 capability 的 Connector grant 完成发布。再叠加 publish/authoring 的反向锁序风险，这三项会直接进入 M2 的 PublishedAgentResolver、外部 API 与 IM 执行边界，应在 M1 合并前关闭。

**Ready to merge：No。**
