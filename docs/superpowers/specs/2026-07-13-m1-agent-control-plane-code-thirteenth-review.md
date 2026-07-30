# 多租户 Agent 发布平台 - M1 第十三轮代码复审

**状态：** 已复审，待修复

**日期：** 2026-07-13

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第十二轮代码复审：[2026-07-13-m1-agent-control-plane-code-twelfth-review.md](./2026-07-13-m1-agent-control-plane-code-twelfth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 第十二轮复审头：`cf84822c13b50c668877e55dfd535e56018a5fed` + 当时未提交工作区
- 第十三轮复审头：`cf84822c13b50c668877e55dfd535e56018a5fed` + 当前未提交工作区
- 本轮重点：
  - 持久化模式停止写入旧 `SOUL.md` / `config.yaml`；
  - 新增数据库草稿运行时 hydration；
  - 新增 `skill_selection_mode` 及迁移；
  - duplicate setup 的 draft row lock 与 PostgreSQL 交叉并发测试；
  - 第十二轮 4 个 Minor 的测试与返回值修复。

---

## 1. 复审结论

第十二轮提出的两个 Important 已按正确方向关闭：持久化模式不再执行文件/数据库双写，duplicate setup 也会锁定 draft 行并与结构化 PATCH 串行化。上一轮的 Ruff 格式、失效的文件回滚测试、串行 partial-CAS 测试和 authoring UOW Connector grants 返回值问题也均已修复。

但新加入的数据库草稿运行时链路仍不满足 M1 的兼容性和权威配置要求。本轮发现 **0 个 Critical、4 个 Important、3 个 Minor**：

1. 开启数据库后，尚未导入的旧文件系统 Agent 会直接报 `Database draft not found`，违反 F1.7“迁移窗口内旧 Agent 对话运行不受影响”的验收标准。
2. 请求体 `config.context` 可以注入 `__agent_*` 内部字段，并在 `_get_runtime_config()` 合并时覆盖服务端刚完成的数据库 hydration，从而改写 SOUL、Skill、工具组和 revision。
3. `skill_selection_mode='inherit'` 只在草稿运行时生效，发布服务仍只遍历技能子表；默认 `skills=None` 的对话式 Agent 在草稿中使用全部 Skills，发布后却得到零个 Skill revision。
4. 数据库运行时只注入 `soul_markdown`，完全忽略 `agent_markdown`；只填写 AGENT.md 的 Agent 在当前草稿运行时不会获得自己的指令。

此外，仓库级 `make lint` 仍被 9 个当前分支上的 Ruff 错误阻断；这些错误不属于本轮 M1 工作区改动，但 M1 Review Gate 仍未满足。

**结论：Ready to merge：No。**

---

## 2. 已确认修复

- **持久化模式文件/数据库双写已关闭**：setup/update 在数据库模式下只提交 authoring UOW，不再产生跨介质 commit 窗口或孤立 `.bak`。
- **duplicate setup lost update 已关闭**：既有 slug 分支使用 `SELECT ... FOR UPDATE` 锁定 draft；PostgreSQL 双连接测试覆盖 setup 持锁时结构化 PATCH 到达 CAS 的交叉时序。
- **变更文件 Ruff 门禁已恢复**：22 个已跟踪 Python 改动和 5 个未跟踪 Python 新文件的 `ruff check` / `ruff format --check` 均通过。
- **setup 文件故障测试已恢复有效**：测试在第二个正式文件替换时注入故障，并断言原内容与 `.tmp` / `.bak` 清理结果。
- **partial update CAS 测试已改为真实竞争**：barrier + `asyncio.gather()` 保证两个事务同时基于 revision=N 到达 CAS。
- **authoring UOW 返回值已补齐 Connector grants**：setup/update 返回的 draft 与数据库实际 grants 一致。
- **真实 HTTP bootstrap 已验证数据库单写**：E2E 通过 owner-scoped control-plane API 读取草稿，并断言持久化模式不创建兼容文件。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：数据库启用后，未导入的旧文件 Agent 无法继续运行

**相关文件：**

- [runtime_loader.py](../../../backend/packages/harness/deerflow/publishing/runtime_loader.py)
- [agents_config.py](../../../backend/packages/harness/deerflow/config/agents_config.py)
- [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**问题说明：**

`hydrate_runtime_agent_config()` 当前只在“完全没有 session factory”时选择文件系统：

```python
if get_session_factory() is None:
    configurable["__agent_config_source"] = "filesystem"
    return
```

只要数据库已经启用，owner + slug 查不到草稿就直接抛出：

```python
if state is None:
    raise FileNotFoundError(f"Database draft not found for agent '{agent_name}'")
```

专项验证结果：

```text
legacy_with_db= FileNotFoundError Database draft not found for agent 'legacy-only'
```

这会让部署在开启数据库持久化后，所有尚未执行 `migrate_published_agents.py` 的旧 `users/{user_id}/agents/{name}/` Agent 立即失效。开发计划 F1.7 和实现规格 §8.2 均要求迁移窗口内旧文件系统 Agent 的对话运行不受影响；“文件只读”并不等于“文件不可作为未导入 Agent 的兼容读取来源”。

**影响：**

- 数据库开关成为破坏性迁移开关，无法渐进导入存量 Agent；
- 升级后旧 Agent 的历史文件仍存在，但聊天入口直接失败；
- import candidate API 仍能列出 Agent，却无法在导入前继续提供原有服务。

**建议修复：**

- 数据库明确返回 owner + slug 不存在时，检查 owner-scoped 旧文件 Agent；存在则标记 `filesystem` 并只读运行，不执行任何文件写回；
- 仅对“确定不存在”的结果兼容回退，数据库连接错误、查询错误或数据损坏必须继续失败，不能被文件回退掩盖；
- 新增“数据库已启用 + DB 无草稿 + owner 旧文件存在”的 worker/HTTP 回归测试，并验证跨 owner 文件不可回退读取。

### 4.2 Important-2：请求 `config.context` 可覆盖服务端数据库 hydration

**相关文件：**

- [services.py](../../../backend/app/gateway/services.py)
- [thread_runs.py](../../../backend/app/gateway/routers/thread_runs.py)
- [runtime_loader.py](../../../backend/packages/harness/deerflow/publishing/runtime_loader.py)
- [agent.py](../../../backend/packages/harness/deerflow/agents/lead_agent/agent.py)

**问题说明：**

`RunCreateRequest.config` 是任意字典。`build_run_config()` 在其中出现 `context` 时会原样复制整个 mapping，而不是只接收 `_CONTEXT_CONFIGURABLE_KEYS`：

```python
context = dict(request_config["context"])
config["context"] = context
```

hydrator 随后把服务端解析出的 `__agent_config`、`__agent_soul` 和 revision 写入 `configurable`，但没有清除 `context` 中同名内部字段。最后 `_get_runtime_config()` 的顺序是：

```python
cfg = dict(config.get("configurable", {}) or {})
cfg.update(config.get("context", {}) or {})
```

因此请求 context 后写覆盖服务端 configurable。专项验证中，数据库返回 `DB soul` / `db-tools`，最终合并结果却是：

```text
hydrated_soul= DB soul
merged_soul= CALLER SOUL
merged_tools= ['caller-tools']
```

真实 `build_run_config()` 也会保留调用方提交的 `__agent_config_source`、`__agent_config` 和 `__agent_soul`。这使所谓“owner-scoped authoritative DB draft”仍可被单次运行请求绕过。

**影响：**

- 调用方无需更新草稿即可替换系统 SOUL；
- 可绕过草稿选定的 Skill 与工具组策略，构造与控制平面记录不一致的运行；
- 可伪造 draft revision，污染 graph cache key 与追踪元数据；
- 同一套内部字段若在后续 M2 被复用，会直接违反“外部字段不能覆盖运行时策略”的安全边界。

**建议修复：**

- 将 `__agent_*` 定义为服务端保留字段，在所有入站 `configurable` / `context` 容器中先清除或拒绝；
- hydration 结果应通过不可由请求构造的可信运行时对象传递，或在合并后由服务端值最终覆盖；
- 对 `RunCreateRequest.config` 中的嵌套 `context` 使用与 `body.context` 相同的 allowlist，而不是原样复制；
- 增加真实 HTTP 测试：请求伪造 SOUL、Skills、tool_groups、revision 后，实际 graph/prompt 仍只能使用数据库值。

### 4.3 Important-3：`inherit` Skill 语义未进入发布快照

**相关文件：**

- [model.py](../../../backend/packages/harness/deerflow/persistence/published_agent/model.py)
- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py)
- [runtime_loader.py](../../../backend/packages/harness/deerflow/publishing/runtime_loader.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [2026_07_13_draft_skill_mode.py](../../../backend/packages/harness/deerflow/persistence/migrations/versions/2026_07_13_draft_skill_mode.py)

**问题说明：**

新增字段把 `inherit` 定义为“继承全部可用 Skills”。对话式 setup 未传 `skills` 时会写入 `skill_selection_mode='inherit'` 和空技能子表；`runtime_loader` 也会把它解析成 `AgentConfig.skills=None`，所以 owner 草稿运行时确实使用全部启用 Skills。

但发布服务完全不读取 `skill_selection_mode`：

```python
prepared_skills = []
for entry in draft.get("skills") or []:
    ...
```

因此最常见的 `setup_agent(..., skills=None)` 路径会表现为：

```text
草稿运行时：全部可用 Skills
发布快照：0 个 agent_release_skills / 0 个 Skill revisions
```

此外，结构化 `POST /published-agents` 创建的空草稿也默认是 `inherit`，而 API 响应中的 `skills` 是空列表；这使“尚未选择 Skill”的 Studio 草稿运行时自动拥有全部 Skills。迁移又把“有子表行”回填为 explicit、“无子表行”保留 inherit，无法区分历史上的显式空集合与省略配置，可能把原本禁用全部 Skills 的草稿扩大为继承全部。

**影响：**

- 草稿测试行为与发布后的不可变 Release 行为不一致；
- owner 以为 Agent 当前能用的 Skills 会随发布全部消失；
- 新建 Studio 草稿在用户尚未选择前可能获得超出界面展示的 Skills；
- 迁移可能产生权限/能力扩大语义。

**建议修复：**

- 明确两个入口的初始语义：结构化 Studio 创建建议默认 `explicit + []`；仅 legacy/对话式省略 Skills 时使用 `inherit`；
- 发布 `inherit` 草稿时必须在 owner 权限下解析当前全部可选择 Skills，并逐个锁定不可变 revision，不能继续按空子表发布；或者在保存时就把继承集合物化为显式选择；
- 为历史无 skill rows 的数据制定保守迁移策略，不能无条件解释为 inherit；
- 新增 `skills=None`、`skills=[]`、显式列表三条从 setup/API → draft runtime → publish release 的端到端测试。

### 4.4 Important-4：数据库草稿运行时忽略 `AGENT.md`

**相关文件：**

- [runtime_loader.py](../../../backend/packages/harness/deerflow/publishing/runtime_loader.py)
- [prompt.py](../../../backend/packages/harness/deerflow/agents/lead_agent/prompt.py)
- [instructions.py](../../../backend/packages/harness/deerflow/publishing/instructions.py)
- [worker.py](../../../backend/packages/harness/deerflow/runtime/runs/worker.py)

**问题说明：**

数据库草稿包含 `agent_markdown` 与 `soul_markdown`，设计要求运行时按 AGENT.md → SOUL.md 顺序拼接。仓库已经实现 `compose_agent_instructions()`，但当前没有任何生产调用方。

新 hydrator 只写入：

```python
configurable["__agent_soul"] = draft.get("soul_markdown") or ""
```

没有保存 `agent_markdown` 或组合后的 instruction block。`agent.py` / flash direct 路径也只把 `agent_soul` 交给 prompt。因此只填写 AGENT.md 的数据库 Agent 会被成功保存、校验并发布，但 owner 侧当前数据库草稿运行时看不到任何自定义指令；两者都填写时也只使用 SOUL.md。

专项检查结果：

```text
agent_markdown_hydrated= False
compose_agent_instructions production usages= 0
```

**影响：**

- “填写 AGENT.md 或 SOUL.md”只在数据模型/发布校验成立，当前草稿运行行为不成立；
- owner 的草稿测试可能认为 Agent 行为错误，却无法从控制平面状态判断原因；
- full graph 与 flash direct 都复用同一缺失字段，两个运行模式一致地丢失 AGENT.md。

**建议修复：**

- hydration 时调用 `compose_agent_instructions(agent_markdown, soul_markdown)`，注入单个服务端可信 instruction block；
- prompt 不应再次用旧 `<soul>` 标签包装已经组合的内容，保持设计规定的 `<agent_instructions>` / `<agent_soul>` 顺序与标签；
- 增加 only-AGENT、only-SOUL、both 三条 worker → graph factory / flash direct 运行测试，断言实际 system prompt，而不只测试纯函数。

---

## 5. Minor 问题

### 5.1 `get_authoring_state()` 不是单快照读取

`DraftService.get_authoring_state()` 先调用 `list_agents(owner)`，再在另一个 session 调用 `get_draft(agent_id)`。如果 authoring UOW 恰好在两次查询之间提交，运行时会组合“旧 identity description + 新 draft”；同时按 slug 查一个 Agent 需要先加载该 owner 的全部 Agent。

建议在 repository 增加 owner + slug 的单 session 查询，并在同一数据库快照内加载 identity、draft、skills 与 grants。

### 5.2 仓库级 `make lint` 仍未通过

本轮 27 个 Python 改动文件的 Ruff check/format 均通过，但对完整 `backend/` 执行仓库门禁仍有 9 个错误：

- `app/gateway/external/service.py`：2 个未定义 `logger`；
- `app/gateway/routers/shares.py`：2 个未使用 import；
- `deerflow/connectors/resources.py`、`deerflow/models/factory.py`、`tests/test_connectors_router.py`：import 排序；
- `deerflow/connectors/service.py`：2 个超长行。

这些文件不在当前 M1 未提交差异中，不能归因于本轮修复；但开发计划的 M1 Review Gate 明确要求 `make lint` 无错误，因此合并前仍需在目标基线或本分支上关闭。

### 5.3 新运行时测试仍停留在 helper/happy path

`test_publishing_runtime_loader.py` 只覆盖“DB explicit 草稿成功 hydration”和“完全无 DB 时标记 filesystem”。真实 HTTP bootstrap E2E 在确认数据库草稿写入后结束，没有发起下一轮 custom-agent run 并检查实际 graph/prompt/tool policy。

缺失的集成场景正好对应本轮四个 Important：DB 已启用时 legacy fallback、入站内部字段伪造、inherit 发布快照、only-AGENT 指令。建议补一条从 HTTP run request → worker hydration → graph/prompt 的最小真实链路，而不是继续只测试 helper 返回字典。

---

## 6. 第十二轮问题关闭状态

| 第十二轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：文件事务无法在进程崩溃后恢复 | **已关闭** | 数据库模式已取消兼容文件双写；文件事务仅用于无数据库的单一文件事实来源 |
| Important-2：duplicate setup 可覆盖结构化 PATCH | **已关闭** | draft row lock + PostgreSQL 交叉并发测试已落地 |
| Minor-1：Ruff format 失败 | **已关闭（M1 改动范围）** | 27 个改动 Python 文件 check/format 通过；全仓仍有独立门禁问题，见 Minor-2 |
| Minor-2：setup 文件故障测试失效 | **已关闭** | 第二个 replace 真实故障注入，原内容与临时文件均有断言 |
| Minor-3：partial CAS 测试串行 | **已关闭** | barrier + gather 的双事务竞争已覆盖 |
| Minor-4：authoring UOW 返回值遗漏 grants | **已关闭** | 同 session 加载并返回 Connector grants |

---

## 7. 验证记录

### 7.1 M1 专项回归

按文件名稳定顺序执行模型、仓储、Draft/Publish、路由、迁移、工具、运行时 loader、prompt/client 与边界测试：

```text
342 passed, 3 skipped, 2 warnings in 36.56s
```

本地没有可用 PostgreSQL，相关并发/迁移测试被跳过；CI 已设置 `REQUIRE_POSTGRES_TESTS=1`，对应测试在 PostgreSQL job 中不得跳过。

### 7.2 真实 HTTP bootstrap

```text
1 passed, 2 warnings in 12.39s
```

该测试证明持久化 setup 只写数据库，但尚未覆盖创建后的下一轮 DB-backed custom-agent 运行。

### 7.3 静态检查

```text
M1 已跟踪 Python 改动（22 个）：ruff check / format --check 通过
M1 未跟踪 Python 新文件（5 个）：ruff check / format --check 通过
完整 backend：ruff check 失败（9 个错误）
git diff --check：通过
```

### 7.4 专项行为验证

```text
legacy_with_db= FileNotFoundError Database draft not found for agent 'legacy-only'
hydrated_soul= DB soul
merged_soul= CALLER SOUL
merged_tools= ['caller-tools']
agent_markdown_hydrated= False
```

---

## 8. 修复优先级

1. 封闭 `__agent_*` 服务端内部字段，确保任何请求容器都不能覆盖数据库 hydration。
2. 恢复数据库迁移窗口中的 owner-scoped 旧文件只读运行兼容。
3. 统一 `inherit` 在草稿运行与不可变发布快照中的语义，并修正结构化创建/历史迁移默认值。
4. 将 AGENT.md + SOUL.md 的组合指令真正接入数据库草稿运行时。
5. 将 authoring state 改为单快照查询，补真实 worker/HTTP 集成测试，并恢复完整仓库 lint 门禁。

---

## 9. 最终判定

**Ready to merge：No。**

第十二轮的原问题已经关闭，但数据库草稿运行时目前会中断存量 Agent、允许请求覆盖服务端权威配置、丢失 `inherit` 发布语义，并忽略 AGENT.md。以上四个 Important 关闭前不应进入 M2；完整仓库 lint 门禁也必须恢复。
