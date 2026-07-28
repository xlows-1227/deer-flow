# 多租户 Agent 发布平台 - M4 代码评审

**状态：** 已评审，待修复
**日期：** 2026-07-26

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- 参考格式：[2026-07-12-m1-agent-control-plane-code-review.md](./2026-07-12-m1-agent-control-plane-code-review.md)

**评审范围：**

- 分支：`codex/m3-feishu-supervisor`
- 固定点：`cc4c3eaeb6f0`
- 评审头：当前未提交工作区
- Diff：`git diff HEAD`，并纳入 M4 新增的前端 Published Agent 模块、E2E/单测、后端最终验收测试与 `docs/PUBLISHED_AGENTS.md`
- 排除项：M3 临时测试目录、本地 `config.yaml`、既有 M3 复审文档
- Spec：开发计划 F4.1–F4.7、最终验收清单，以及设计文档 §15、§16、§18.6、§19

---

## 1. 评审结论

M4 的主要页面骨架已经完成：

- Published Agent API 客户端、类型与 TanStack Query hooks 已建立；
- Gallery、Studio、发布/历史/回滚、API Key、飞书绑定、用量、配额与审计视图均已有实现；
- 中英文 i18n、前端单测、E2E 用例、后端最终验收测试和运维文档已补充；
- TypeScript、ESLint、M4 前端单测与后端验收测试的已执行部分可以通过。

但当前实现仍存在 3 类阻断问题：

1. Agent 生命周期允许 `draft → suspended → published`，可产生“没有 Release 却显示已发布”的非法状态；
2. 草稿沙箱没有实现 spec 要求的草稿运行端点，只跳转到旧普通聊天路径，无法保证读取数据库草稿和免计费；
3. “最终验收”中的若干测试只验证独立仓储或策略函数，没有覆盖 spec 声称的真实 HTTP / Run / 计费 / Supervisor 链路，因此 `12 passed` 不能证明 M4 Review Gate 通过。

此外，模型无法从显式选择清回平台默认、认证失败审计在 owner 视图中不可达、Channel 状态契约与后端不一致、运维指标不完整。

**结论：当前 M4 不满足 “Ready to merge / 第一版整体验收通过” 标准。**

---

## 2. 做得好的部分

- M4 组件按 API、hooks、类型与业务面板拆分，整体目录边界清楚。
- 草稿保存携带 revision，并为 409 提供了专属错误分支。
- 发布校验能够保留并逐条渲染 `PublishViolation`。
- API Key 明文只保存在创建/轮换响应弹窗状态中，关闭后会清空。
- Release 历史、草稿与当前 Release 对比、双 Release 对比和回滚确认已具备基本交互。
- 配额编辑明确区分 owner 覆盖与平台默认值，空值按继承语义处理。
- 新增 API 客户端的 TypeScript 类型检查、ESLint 与单元测试均通过。

---

## 3. Standards 轴

### 3.1 Critical-S1：模型无法从显式选择清回“继承平台默认”

**相关文件：**

- `frontend/src/components/workspace/published-agents/agent-studio.tsx:375`
- `frontend/src/components/workspace/published-agents/agent-studio.tsx:152`
- `backend/app/gateway/routers/published_agents.py:388`
- `backend/packages/harness/deerflow/persistence/published_agent/sql.py:624`

**问题说明：**

Studio 把“继承平台默认”映射为 `modelName: null`，保存时也会发送 `model_name: null`。但是后端从 Pydantic 到 Service、Repository 都用 `None` 同时表达“清空”和“字段未提供”，仓储最终只在 `model_name is not None` 时写列。

因此触发以下流程时：

1. 先为草稿选择具体模型并保存；
2. 再选择“继承平台默认”；
3. 点击保存；

UI 会提示保存成功、revision 也会增加，但数据库中的旧模型不会被清除，后续发布仍使用旧模型。

**影响：**

- Studio 展示状态与持久化事实来源不一致；
- 发布内容可能与 owner 明确选择相反；
- 违反 F4.1 “类型与后端契约一致”及 F4.3 模型选择要求。

**建议修复：**

- 使用显式 sentinel 或 `payload.model_fields_set` 区分“未提供”与“显式 null”；
- Repository 的动态更新允许 `model_name = NULL`；
- 增加“具体模型 → 继承默认 → 重新读取仍为 null”的路由与仓储回归测试。

---

### 3.2 Important-S1：owner 审计视图无法看到认证失败事件

**相关文件：**

- `backend/app/gateway/external/agent_auth.py:44`
- `backend/app/gateway/external/audit.py:91`
- `backend/app/gateway/routers/published_agents.py:319`
- `frontend/src/components/workspace/published-agents/usage-panel.tsx:127`

**问题说明：**

Agent Key 缺失、无效或绑定到其他 Agent 时，认证中间件会在设置以下状态前直接返回 401/404：

- `auth_method = "agent_api_key"`
- `owner_user_id`
- `agent_id`
- `agent_key_id`

审计中间件只有在 `auth_method == "agent_api_key"` 时才写入 owner / Agent / credential 作用域。owner 审计接口又按 `owner_user_id + agent_id` 查询。

结果是认证失败虽然会形成审计行，但没有 Agent 归属，M4 的单 Agent 运维面板永远查不到它；`_audit_category(401) == "authentication"` 在该视图中实际不可达。

**影响：**

- F4.6 明确要求的“认证失败”拒绝事件无法展示；
- owner 无法判断某个 Agent 是否正在遭受无效 Key 调用；
- UI 看似支持 authentication 分类，但生产数据路径不会提供该分类。

**建议修复：**

- 在验证 Key 前先从受控路径解析目标 `agent_id`，安全地写入目标资源标识；
- 如 Agent 存在，可写 owner 作用域但不得泄露给调用方；
- 增加“无 Key / 错 Key / 跨 Agent Key → owner 审计可见且外部仍不泄露存在性”的集成测试。

---

### 3.3 Important-S2：Channel TypeScript 契约遗漏真实 `deleting` 状态

**相关文件：**

- `frontend/src/core/published-agents/types.ts:9`
- `frontend/src/core/i18n/locales/en-US.ts:571`
- `frontend/src/core/i18n/locales/zh-CN.ts:535`
- `frontend/src/components/workspace/published-agents/feishu-binding-panel.tsx:328`
- `backend/packages/harness/deerflow/persistence/agent_channel/sql.py:945`

**问题说明：**

前端把 Channel 状态声明为：

```ts
"inactive" | "active" | "error" | "deleted"
```

后端真实持久化状态包含 `deleting`，删除清理期间会把该值原样返回。前端既没有类型和 i18n，也没有针对清理中状态禁用操作。

当绑定处于 `deleting` 时，UI 会把它走入“非 active”分支，并继续显示 start、restart、rotate credentials 等按钮。

**影响：**

- F4.1 所要求的前后端契约一一对应不成立；
- 清理中的绑定会显示未本地化的原始状态；
- owner 可以发起必然冲突或不安全的生命周期操作。

**建议修复：**

- 以后端状态机为唯一来源补齐 `deleting`，移除没有后端依据的状态；
- 清理中只允许查看/重试清理，不允许 start/restart/rotate；
- 加入 `deleting` 响应的 API 契约测试与组件/E2E 测试。

---

### 3.4 Minor-S1：动态路由页面不必要地声明为 Client Component

**相关文件：**

- `frontend/src/app/workspace/agents/[agent_name]/page.tsx:1`
- `frontend/CLAUDE.md` “Server Components by default”

页面本身只读取动态参数并把参数传给 `AgentStudio`，却整体声明 `"use client"`。这不影响核心功能，但违反仓库“Server Components 默认”的明确约定。

建议保留 `AgentStudio` 为 Client Component，让 page 作为 Server Component 解包 `params` 后传入 `agentId`。

---

### 3.5 判断项-S1：配额字段和解析逻辑形成重复 Data Clump

**相关文件：**

- `frontend/src/components/workspace/published-agents/api-keys-panel.tsx:43`
- `frontend/src/components/workspace/published-agents/quota-panel.tsx:27`

两个组件分别维护同一组 7 个配额字段、输入转换、正整数解析和标签逻辑。后续新增或修改配额字段时容易出现散弹式修改和契约漂移。

这是 Fowler `Duplicated Code / Data Clumps` 判断项，不是硬性违规。建议抽取共享的 quota field schema、解析器和输入组件。

---

## 4. Spec 轴

### 4.1 Critical-P1：生命周期允许把从未发布的草稿恢复成 `published`

**Spec：**

- 开发计划 F4.2（第 910–916 行）：卡片提供暂停/恢复，但暂停不得删除数据，状态必须准确。
- 设计 §16.1：Gallery 展示稳定的 Agent 状态。

**相关文件：**

- `frontend/src/components/workspace/published-agents/agent-card.tsx:228`
- `backend/packages/harness/deerflow/publishing/draft_service.py:414`
- `backend/app/gateway/routers/published_agents.py:419`

**问题说明：**

Gallery 对所有非 archived Agent 都显示 suspend，包括状态为 `draft`、`current_release_id == null` 的 Agent。后端 `suspend()` 无状态前置条件，`resume()` 又无条件把状态写成 `published`。

最小触发链路：

```text
draft（无 Release） → suspend → resume → published（仍无 Release）
```

**影响：**

- 产生违反领域模型的非法状态；
- Gallery、集成面板与外部运行时可能对“是否已发布”得出不同结论；
- M4 的稳定状态展示和发布后集成前置条件失真。

**建议修复：**

- 后端实现显式状态转换表并校验 `current_release_id`；
- 未发布草稿不提供 suspend，或 resume 时恢复到原状态而非固定 `published`；
- 用真实 Repository + Router 增加 draft/published/suspended/archived 全状态转换测试。

---

### 4.2 Critical-P2：草稿沙箱没有实现草稿运行路径

**Spec：**

- 开发计划 F4.3（第 926、935、939 行）：复用聊天组件对草稿配置运行；如 M1 无端点，M4 必须补 `POST /api/published-agents/{agent_id}/draft/sandbox-runs`；读取草稿且不产生用量账单。
- 设计 §16.2：Studio 第 4 区必须是草稿沙箱测试。

**相关文件：**

- `frontend/src/components/workspace/published-agents/draft-sandbox.tsx:10`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:42`
- `frontend/tests/e2e/agent-studio-draft.spec.ts:192`

**问题说明：**

当前 Sandbox 面板只把用户跳转到：

```text
/workspace/agents/{agentSlug}/chats/new
```

仓库中没有 `draft/sandbox-runs` 端点。目标页面使用普通 `useThreadStream` 聊天链路，未证明它读取数据库草稿，也没有免计费标识或结算隔离。

对应 E2E 只断言 “Not live” 徽章可见，没有点击入口或验证请求路径、草稿指令、Release 隔离与用量不变。

**影响：**

- F4.3 的核心功能实际缺失；
- owner 可能以为测试的是未发布草稿，实际运行的是旧文件 Agent 或其他普通聊天配置；
- 无法证明沙箱不会产生 Published 用量账单。

**建议修复：**

- 实现专用 draft sandbox run 后端端点和前端 hook；
- 明确注入草稿 revision，并禁用 Published 用量结算；
- E2E 验证“修改但未发布的指令生效、线上 Release 不变、usage 不增加”。

---

### 4.3 Critical-P3：最终验收测试没有覆盖其声明的真实链路

**Spec：**

- 开发计划 F4.7（第 1006–1011 行）：把设计 §19 的 14 条验收逐条转成自动化测试。
- 最终验收 #1、#5、#13、#14（第 1025、1029、1037–1038 行）分别要求 HTTP 隔离、Connector 实际授权、重试不重复 Run/计费和故障隔离。

**相关文件：**

- `backend/tests/test_acceptance_multi_tenant.py:384`
- `backend/tests/test_acceptance_multi_tenant.py:517`
- `backend/tests/test_acceptance_multi_tenant.py:828`
- `backend/tests/test_acceptance_multi_tenant.py:883`

**问题说明：**

- #1 直接调用 owner-scoped Repository 读取并断言 `None` / `[]`，没有通过 HTTP 验证跨租户读写的 403/404，也没有覆盖跨租户修改；
- #5 直接调用 `authorize_connector_action()`，没有执行真实工具/Connector 调用，`repr(context)` 不含 secret 也不能证明响应与日志不泄密；
- #13 分别测试 event claim、idempotency claim 和 usage 唯一写入，没有执行一次完整重试 → Run → 计费链路；
- #14 手工修改 Channel、Connector、Agent 状态后解析另一个 Agent，没有启动 Supervisor、运行 Connector 或触发真实故障边界。

测试使用真实 SQLite Repository 是优点，但这些断言仍不足以证明命名和文档宣称的端到端验收条件。

**影响：**

- `12 passed` 会给出错误的 M4 Review Gate 通过信号；
- 跨层 wiring、HTTP 语义、重试编排和 Supervisor 故障传播仍可能在生产失败；
- 开发计划把 M4 标为“待评审”可以，但不能据此标为“已完成”。

**建议修复：**

- #1 使用真实 FastAPI app/session auth 发起跨 owner GET/PATCH/POST；
- #5 运行真实 Connector adapter，并扫描响应、异常与结构化日志；
- #13 从飞书事件和 Agent API 重试入口执行到 Run/usage 终态；
- #14 同时运行两个绑定/Agent，注入一个真实运行时故障并确认另一个持续服务。

---

### 4.4 Important-P1：Channel 状态不满足 F4.1 契约一致性

**Spec：**

- 开发计划 F4.1（第 887、898 行）：Channel TS 类型必须与后端 Pydantic/响应契约一一对应。

前端遗漏后端真实 `deleting` 状态，并加入后端当前不会返回的 `error` / `deleted`。该问题在 Standards 轴体现为类型与状态机缺陷；在 Spec 轴则直接构成 F4.1 未完成。

修复要求见 3.3。

---

### 4.5 Important-P2：运维 Dashboard 指标只实现了子集

**Spec：**

- 设计 §15（第 541–550 行）：成本、配额拒绝与并发饱和、飞书事件延迟、Connector 失败/拒绝、按当前 Release 的错误率等。
- 开发计划 F4.6（第 989–995 行）：用量、配额与审计视图，对应设计 §15。

**相关文件：**

- `backend/packages/harness/deerflow/persistence/agent_usage/sql.py:401`
- `backend/app/gateway/routers/published_agents.py:237`
- `frontend/src/components/workspace/published-agents/usage-panel.tsx:127`

当前实现主要覆盖 Run、输入/输出 Token、按状态推导错误率、来源/Key 过滤及最近拒绝记录。以下设计项没有数据或 UI：

- 成本；
- 并发饱和；
- 飞书事件延迟；
- Connector 失败/授权拒绝的专门聚合；
- 按当前 Release 维度的错误率。

建议明确第一版是否缩减 §15；若不缩减，需要补指标采集、聚合 API 与对应 UI/E2E。

---

### 4.6 Minor-P1：全局品牌替换超出 M4 范围

**相关文件：**

- `frontend/src/components/workspace/workspace-header.tsx:19`
- `frontend/public/images/shisanxiang-icon.png`

F4.1–F4.7 没有品牌替换需求，但本次工作区把全局 Workspace 品牌改为“十三香”并新增图片资产。这会扩大回归面，也使 M4 diff 混入无法由当前 spec 验收的产品决策。

建议从 M4 变更中拆出，使用独立需求、评审与提交。

---

## 5. 验证记录

### 5.1 前端类型与静态检查

执行：

```powershell
cd frontend
.\node_modules\.bin\tsc.cmd --noEmit
.\node_modules\.bin\eslint.cmd . --ext .ts,.tsx
```

结果：

- TypeScript：通过；
- ESLint：0 errors，10 warnings；
- 10 个 warning 均位于既有非 M4 文件，本次新增 Published Agent 文件没有 ESLint warning。

标准入口 `pnpm check` 未进入项目脚本：本地 Codex pnpm 包装器在非交互终端尝试重建 `node_modules` 后以 `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY` 中止，因此改用现有本地可执行文件完成等价静态检查。

### 5.2 M4 前端单测

执行：

```powershell
cd frontend
.\node_modules\.bin\vitest.cmd run tests/unit/core/published-agents
```

结果：

```text
2 test files passed
7 tests passed
```

### 5.3 后端最终验收测试

执行：

```powershell
cd backend
uv run pytest tests/test_acceptance_multi_tenant.py -q
```

结果：

```text
12 passed, 1 warning
```

该结果证明当前测试代码本身全绿，但不能消除 4.3 所述的验收覆盖层级不足。

### 5.4 前端构建与 E2E

- `next build` 的编译阶段成功（46 秒），随后命令在完整构建结束前达到本次评审的工具超时；
- M4 Playwright E2E 未取得可信的完整运行结果：本地 Next 测试进程与 auth 环境启动冲突；
- 因此本报告不把全量 build / E2E 记为通过，也不以此增加代码问题结论。

---

## 6. 建议修复顺序

1. 收紧 Agent 生命周期状态机，禁止无 Release Agent 进入 `published`。
2. 实现真实 draft sandbox run 路径、免计费语义与端到端测试。
3. 修复 `model_name = null` 的显式清空契约。
4. 补齐 Channel `deleting` 类型、i18n 与操作禁用。
5. 让认证失败审计安全地归属到目标 Agent/owner。
6. 把最终验收 #1、#5、#13、#14 提升为真实跨层测试。
7. 补齐或显式裁剪设计 §15 的运维指标范围。
8. 将全局品牌替换拆出 M4，并处理 Server Component 与配额重复逻辑。

---

## 7. 最终评审结论

**Ready to merge：No**

**原因：**

- 可产生“无 Release 但 published”的非法 Agent 状态；
- 草稿沙箱核心路径未实现；
- 最终验收测试对关键条目的覆盖与其命名/文档声明不一致；
- 模型清空、认证失败审计和 Channel 状态契约仍存在生产行为缺口。

修复 Critical 与 Important 问题，并取得完整前端 build / E2E 结果前，不建议把 M4 标记为“已完成”或通过第一版整体验收。

**双轴汇总：** Standards 轴 5 项（最严重：显式清回默认模型被静默忽略）；Spec 轴 6 项（最严重：生命周期可生成无 Release 的 published Agent）。
