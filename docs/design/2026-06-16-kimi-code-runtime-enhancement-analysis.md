# Kimi Code Runtime 机制对 DeerFlow 的增强分析

> 状态:**调研结论,待产品/架构评审**
> 日期:2026-06-16
> 作者:基于 MoonshotAI/kimi-code 与 DeerFlow 现状对比调研得出
> 参考版本:`MoonshotAI/kimi-code@66b4d65`
> 关联代码:`backend/packages/harness/deerflow/agents/`、`backend/packages/harness/deerflow/subagents/`、`backend/packages/harness/deerflow/runtime/`、`frontend/src/core/threads/`

---

## 目录

- [一、结论摘要](#一结论摘要)
- [二、Kimi Code 与 DeerFlow 的内核定位差异](#二kimi-code-与-deerflow-的内核定位差异)
- [三、Kimi Code 可借鉴的核心 runtime 机制](#三kimi-code-可借鉴的核心-runtime-机制)
  - [3.1 AgentSwarm:批量 subagent 协作](#31-agentswarm批量-subagent-协作)
  - [3.2 Permission Policy Chain:统一权限策略链](#32-permission-policy-chain统一权限策略链)
  - [3.3 BackgroundManager:后台任务统一模型](#33-backgroundmanager后台任务统一模型)
  - [3.4 Plan Mode:计划文件、权限保护与退出评审](#34-plan-mode计划文件权限保护与退出评审)
  - [3.5 Goal Mode:长期目标与多轮自动推进](#35-goal-mode长期目标与多轮自动推进)
  - [3.6 Hook Engine:本地自动化与企业集成点](#36-hook-engine本地自动化与企业集成点)
  - [3.7 Session SDK / Protocol / ACP:多客户端共享协议](#37-session-sdk--protocol--acp多客户端共享协议)
  - [3.8 Plugin Marketplace:skills、MCP 与 datasource 的能力包](#38-plugin-marketplaceskillsmcp-与-datasource-的能力包)
- [四、DeerFlow 的推荐增强路线](#四deerflow-的推荐增强路线)
- [五、不建议直接照搬的部分](#五不建议直接照搬的部分)
- [六、建议优先级](#六建议优先级)
- [七、开放问题](#七开放问题)

---

## 一、结论摘要

Kimi Code 的内核更适合**本地 coding agent 产品**:它围绕 `Session` 构建本地模型循环、工具执行、权限审批、subagent、后台任务、TUI、SDK、ACP 和插件生态,整体形态接近 Cursor/Codex/Claude Code 这类本地开发助手。

DeerFlow 当前基于 LangGraph 的内核更适合**服务端 agent 平台**:它已经具备 Gateway、LangGraph runtime、线程、runs、memory、skills、MCP、sandbox、external API、IM channels、Web workspace 等平台能力,更适合多渠道接入、持久化服务和业务系统集成。

因此,更合理的方向不是用 Kimi Code 替换 DeerFlow 的 LangGraph 内核,而是:

> **保留 DeerFlow 的 LangGraph 服务端平台底座,逐步吸收 Kimi Code 的本地 coding-agent runtime 与产品机制。**

短期最值得优先落地的是:

1. `AgentSwarm`:把现有单次 subagent delegation 升级为批量并行协作。
2. 权限策略链:把 Guardrail、SandboxAudit、审批、session approval、路径/命令规则统一。
3. 后台任务统一模型:统一 shell、subagent、long-running run、ACP agent 的状态/输出/停止。
4. Plan Mode 升级:从 todo 变成 plan artifact + 写入保护 + 用户确认。
5. Goal Mode:支持长期目标、多轮自动推进、预算、暂停/恢复/阻塞。

---

## 二、Kimi Code 与 DeerFlow 的内核定位差异

### 2.1 Kimi Code:本地 coding-agent runtime

Kimi Code 的核心特点:

- 以 `Session` 为用户工作单元。
- 以本地模型-工具 loop 为中心,工具执行、权限、hooks、subagent 都围绕 turn 生命周期组织。
- 提供 CLI/TUI/Node SDK/ACP 多种客户端入口。
- 内置本地开发体验所需机制:slash command、后台任务、approval panel、插件市场、session export、doctor、migration、telemetry。

可代表其方向的代码区域:

- `packages/agent-core/src/agent/`
- `packages/agent-core/src/loop/`
- `packages/node-sdk/src/session.ts`
- `apps/kimi-code/src/tui/commands/`
- `packages/acp-adapter/src/server.ts`

这类架构的优势是本地响应快、权限控制细、开发工作流强、容易接入 IDE。但它默认假设 agent runtime 和用户工作区在同一台机器上,天然更偏单用户本地会话。

### 2.2 DeerFlow:LangGraph 服务端 agent 平台

DeerFlow 当前的核心特点:

- Gateway 内嵌 LangGraph-compatible runtime。
- 通过 FastAPI 暴露 threads、runs、artifacts、skills、memory、MCP、external API 等服务。
- 前端以 Web workspace 形式消费 SSE/stream events。
- 后端已经有 per-user isolation、run store、run events、memory、skills、sandbox、IM channels 等平台能力。

这类架构的优势是可部署、可服务化、容易接多端和业务系统。代价是本地 coding-agent 体验中常见的细粒度权限、IDE 协议、后台任务面板、subagent swarm 等机制还需要继续补齐。

### 2.3 方向判断

| 维度 | Kimi Code 更强 | DeerFlow 更强 |
|---|---|---|
| 本地 coding agent 体验 | 是 | 目前较弱 |
| Web agent workspace | 较弱 | 是 |
| 服务端部署与 API | 较弱 | 是 |
| subagent 批量协作 | 是 | 目前偏单任务 delegation |
| 权限审批体验 | 是 | 有 guardrail/audit,但体系未统一 |
| 长期平台能力 | 较弱 | 是 |
| IDE/ACP 接入 | 是 | 已有 ACP 工具调用能力,但缺 agent-side adapter |
| 插件产品化 | 是 | skills/MCP 已有,marketplace/能力包不足 |

结论:**DeerFlow 不应放弃 LangGraph 平台化方向,但应向 Kimi Code 学习本地 runtime 的产品闭环。**

---

## 三、Kimi Code 可借鉴的核心 runtime 机制

### 3.1 AgentSwarm:批量 subagent 协作

Kimi Code 相关实现:

- `packages/agent-core/src/tools/builtin/collaboration/agent-swarm.ts`
- `packages/agent-core/src/session/subagent-batch.ts`
- `packages/agent-core/src/session/subagent-host.ts`
- `packages/agent-core/src/agent/swarm/enter-reminder.md`
- `apps/kimi-code/src/tui/commands/swarm.ts`

其核心设计:

- `AgentSwarm` 是一个工具,由主 agent 调用。
- 输入包括 `description`、`subagent_type`、`prompt_template`、`items`、`resume_agent_ids`。
- `prompt_template` 必须包含 `{{item}}`;每个 item 生成一个 subagent prompt。
- 最多支持 128 个 subagents。
- `SubagentBatch` 负责排队、启动、结果顺序归并、取消、超时、rate limit 退避重试。
- 初始最多启动 5 个任务,后续按固定间隔继续启动,遇到 provider rate limit 后进入限流模式。
- 返回 `<agent_swarm_result>` XML 风格汇总,包含每个 subagent 的 outcome 和 resume hint。

对 DeerFlow 的价值:

- DeerFlow 已有 subagent executor 和 `task` 工具,但更像单次委派。
- 可以在现有基础上新增 `agent_swarm` 工具,不需要推翻 LangGraph。
- 适合代码审查、资料调研、多文件分析、并行方案探索、批量修复等场景。
- 前端可以展示每个 subagent 的运行状态、输出摘要、失败原因和恢复入口。

建议设计:

- 新增内置 subagent profile:`explore`、`coder`、`plan`、`review`。
- `agent_swarm` 入参保持简洁:`description`、`subagent_type`、`prompt_template`、`items`、`max_concurrency`、`resume_task_ids`。
- 后端输出结构化 custom events:`swarm_started`、`swarm_child_started`、`swarm_child_completed`、`swarm_child_failed`、`swarm_completed`。
- 结果汇总既写入 ToolMessage,也写入 run events,便于前端恢复。
- 与现有 `MAX_CONCURRENT_SUBAGENTS` 合并或替换为队列式调度。

### 3.2 Permission Policy Chain:统一权限策略链

Kimi Code 相关实现:

- `packages/agent-core/src/agent/permission/`
- `packages/agent-core/src/agent/permission/policies/`
- `packages/agent-core/src/session/hooks/`
- `packages/protocol/src/approval.ts`
- `apps/kimi-code/src/tui/components/dialogs/approval-panel.ts`

其核心设计:

- 权限决策是 ordered policy chain,首个命中结果生效。
- 支持 permission mode:`manual`、`auto`、`yolo`。
- policy 可以返回 allow、deny、ask/approval。
- approval 支持 session scope,避免重复确认。
- 支持用户配置规则,按工具名、命令前缀、路径 glob、输入等匹配。
- `PreToolUse` hook 可以阻断工具调用。
- `AgentSwarm` 有 exclusive policy:一次响应里只能调用一个 `AgentSwarm`,且不能与其他工具混用。

DeerFlow 现状:

- 已有 `GuardrailMiddleware`、`SandboxAuditMiddleware`、`ToolErrorHandlingMiddleware`。
- 但 guardrail、audit、审批、plan mode 限制、session approval 尚未形成统一策略链。

建议设计:

- 在 lead-agent 工具执行前引入 `ToolAuthorizationMiddleware`。
- 抽象 `PermissionPolicy` 协议:
  - `Allow`
  - `Deny`
  - `AskUser`
  - `AllowForSession`
- 默认 policy 顺序建议:
  1. System hard deny:危险路径、危险工具组合、AgentSwarm exclusive。
  2. Plan mode guard:计划模式禁止写代码/执行高风险命令。
  3. User/session rules:用户配置的工具、路径、命令规则。
  4. Guardrail provider:现有 OAP/allowlist/custom provider。
  5. Session approval history:本会话已批准操作。
  6. Permission mode fallback:manual/auto/yolo。
- 审批请求应进入 run events,前端显示可恢复的 approval UI。
- 审批结果写入审计日志,可用于企业合规。

### 3.3 BackgroundManager:后台任务统一模型

Kimi Code 相关实现:

- `packages/agent-core/src/agent/background/index.ts`
- `packages/agent-core/src/agent/background/agent-task.ts`
- `packages/agent-core/src/agent/background/process-task.ts`
- `packages/agent-core/src/tools/background/task-list.ts`
- `packages/agent-core/src/tools/background/task-output.ts`
- `packages/agent-core/src/tools/background/task-stop.ts`
- `packages/protocol/src/task.ts`

其核心设计:

- 后台任务有统一 `task_id`、kind、status、startedAt、endedAt、output。
- 支持后台 bash、后台 subagent、question task。
- 输出写入 ring buffer + 持久化日志。
- 支持 list/output/stop。
- 进程重启后可把失联任务标记为 lost。
- 任务结束后发通知事件。

对 DeerFlow 的价值:

- DeerFlow 现在有 runs、subagent executor、sandbox bash、ACP agent 调用,但缺少一个面向用户的统一后台任务视图。
- 本地客户端尤其需要知道“后台正在跑什么、输出是什么、能否停止”。

建议设计:

- 新增 `BackgroundTaskManager` 或在 RunManager 上扩展统一 task 子系统。
- kind 至少包括:
  - `run`
  - `subagent`
  - `shell`
  - `tool`
  - `acp_agent`
- 提供 API:
  - `GET /api/threads/{thread_id}/tasks`
  - `GET /api/threads/{thread_id}/tasks/{task_id}`
  - `GET /api/threads/{thread_id}/tasks/{task_id}/output`
  - `POST /api/threads/{thread_id}/tasks/{task_id}/cancel`
- 前端增加后台任务面板,支持 tail output、停止、跳转到产生任务的消息。
- 复用 run event store 记录任务生命周期,避免另起一套不可查询日志。

### 3.4 Plan Mode:计划文件、权限保护与退出评审

Kimi Code 相关实现:

- `packages/agent-core/src/agent/plan/`
- `packages/agent-core/src/tools/builtin/planning/enter-plan-mode.ts`
- `packages/agent-core/src/tools/builtin/planning/exit-plan-mode.ts`
- `packages/agent-core/src/agent/injection/plan-mode.ts`

其核心设计:

- `EnterPlanMode` 用于非平凡实现任务前进入计划模式。
- 计划模式要求先探索、再写 plan 文件。
- `ExitPlanMode` 将计划展示给用户并等待确认。
- plan mode 下权限收紧,防止模型绕过计划阶段直接修改代码。

DeerFlow 现状:

- 已有 `TodoListMiddleware` 和 plan mode 开关。
- 但计划更像任务列表,缺少稳定计划文件、用户确认、写入保护和执行阶段切换。

建议设计:

- 将 plan mode 升级为三段式:
  1. `planning`:只允许读、搜索、写计划 artifact。
  2. `review`:等待用户确认或修改计划。
  3. `execution`:按计划执行并更新 todos。
- 每个复杂任务生成 `plan.md` artifact,绑定 thread/run。
- plan mode 的 ToolAuthorizationPolicy 禁止非计划文件写入。
- 前端把 plan、todos、执行进度合并为一个“任务计划面板”。

### 3.5 Goal Mode:长期目标与多轮自动推进

Kimi Code 相关实现:

- `packages/agent-core/src/agent/goal/`
- `packages/agent-core/src/tools/builtin/goal/create-goal.ts`
- `packages/agent-core/src/tools/builtin/goal/update-goal.ts`
- `packages/agent-core/src/tools/builtin/goal/set-goal-budget.ts`
- `packages/agent-core/src/agent/injection/goal.ts`

其核心设计:

- Goal 是 durable 结构化目标,有 objective、completion criterion、status、budget。
- 状态包括 active、paused、blocked、complete。
- 支持 token、turn、wall-clock budget。
- runtime 可以围绕 active goal 自动推进多个 turn。
- 模型通过 `UpdateGoal` 标记进展、阻塞或完成。

对 DeerFlow 的价值:

- DeerFlow 已有 thread、memory、runs、run events,天然适合承载长期目标。
- Goal 能把 DeerFlow 从“聊天式 agent”推进到“可持续执行任务的 agent workspace”。

建议设计:

- 新增 thread-level `GoalState`:
  - `objective`
  - `completion_criterion`
  - `status`
  - `budget`
  - `progress_summary`
  - `blockers`
- 新增工具:
  - `create_goal`
  - `get_goal`
  - `update_goal`
  - `set_goal_budget`
- Goal mode 不应默认无限自动运行,必须有预算和用户可见状态。
- 与 memory 集成:goal 关键进展写入用户长期上下文。

### 3.6 Hook Engine:本地自动化与企业集成点

Kimi Code 相关实现:

- `packages/agent-core/src/session/hooks/types.ts`
- `packages/agent-core/src/session/hooks/engine.ts`
- `packages/agent-core/src/session/hooks/runner.ts`
- `packages/agent-core/src/agent/permission/policies/pre-tool-call-hook.ts`

其 hook 事件包括:

- `PreToolUse`
- `PostToolUse`
- `PostToolUseFailure`
- `PermissionRequest`
- `PermissionResult`
- `UserPromptSubmit`
- `Stop`
- `StopFailure`
- `Interrupt`
- `SessionStart`
- `SessionEnd`
- `SubagentStart`
- `SubagentStop`
- `PreCompact`
- `PostCompact`
- `Notification`

对 DeerFlow 的价值:

- 本地客户端可用 hooks 触发通知、审计、自动格式化、企业安全扫描。
- 服务端部署可把 hook 抽象为 webhook/event subscription,避免直接执行任意本地命令。

建议设计:

- 本地模式:支持命令型 hooks。
- 服务端模式:优先支持 webhook / internal event handler,谨慎开放 shell hooks。
- `PreToolUse` 与权限策略链打通,可阻断工具。
- hook 输入统一 snake_case JSON,输出统一 allow/block/message/reason。

### 3.7 Session SDK / Protocol / ACP:多客户端共享协议

Kimi Code 相关实现:

- `packages/node-sdk/src/session.ts`
- `packages/protocol/src/`
- `packages/acp-adapter/src/server.ts`
- `apps/kimi-code/src/cli/commands.ts`
- `apps/kimi-code/src/tui/commands/`

其核心设计:

- 独立 protocol 包定义事件、REST schema、approval、task、session、message 等类型。
- Node SDK 暴露完整 Session 能力:
  - prompt/steer/swarm/cancel
  - setModel/setPermission/setPlanMode
  - compact/undo/usage/status
  - skills/MCP/plugins
  - background tasks
  - goal lifecycle
- ACP adapter 让 IDE 通过 Agent Client Protocol 驱动同一套 session。

对 DeerFlow 的价值:

- DeerFlow 当前 Web 前端和 Python embedded client 已有能力,但缺一个跨 Web/CLI/desktop/IDE 的统一 session SDK。
- 若未来做本地客户端,Session SDK 和 protocol 包会显著降低重复实现。

建议设计:

- 抽取 `deerflow-protocol`:
  - 前端 TypeScript 类型
  - REST/SSE event schema
  - approval/task/goal/swarm schema
- 扩展 `DeerFlowClient` 或新增 `deerflow-sdk`:
  - `session.prompt()`
  - `session.swarm()`
  - `session.createGoal()`
  - `session.listTasks()`
  - `session.approve()`
- 中期实现 ACP agent-side adapter,让 Zed/JetBrains/Cursor 类客户端能直接接入 DeerFlow agent。

### 3.8 Plugin Marketplace:skills、MCP 与 datasource 的能力包

Kimi Code 相关实现:

- `packages/agent-core/src/plugin/`
- `apps/kimi-code/src/tui/commands/plugins.ts`
- `apps/kimi-code/src/utils/plugin-marketplace.ts`
- `plugins/marketplace.json`
- `plugins/official/kimi-datasource/kimi.plugin.json`

其核心设计:

- plugin manifest 可声明:
  - skills
  - MCP servers
  - sessionStart injection
  - interface metadata
- marketplace 展示 official/curated plugin。
- 用户可以安装、启用/禁用、查看详情、管理 plugin MCP server。

DeerFlow 现状:

- 已有 skills 和 MCP 配置。
- 但 skills、MCP servers、datasource、session prompt injection 仍是分散概念。

建议设计:

- 引入 DeerFlow plugin manifest:
  - `name`
  - `version`
  - `description`
  - `skills`
  - `mcpServers`
  - `sessionStart`
  - `permissions`
  - `author/license`
- 允许一个 plugin 同时安装 skill + MCP + datasource。
- Plugin 安装路径与 per-user isolation 对齐。
- 前端增加 marketplace/插件管理页面。

---

## 四、DeerFlow 的推荐增强路线

### 阶段 1:补齐 subagent swarm

目标:把 DeerFlow 现有单 subagent 委派升级为批量协作。

建议任务:

- 新增 `agent_swarm` 工具。
- 新增 `SubagentBatchExecutor`。
- 增加内置 subagent profile。
- 设计 swarm run events。
- 前端增加 swarm 子任务状态展示。

成功标准:

- 用户可以要求“并行审查这 10 个文件”,主 agent 自动拆分并启动多个子 agent。
- 每个子 agent 的状态、输出、失败原因可见。
- 主 agent 能收到结构化汇总并继续综合判断。

### 阶段 2:统一权限策略链

目标:把工具授权从分散 middleware 升级为可组合 policy chain。

建议任务:

- 定义 `PermissionPolicy` 协议。
- 接入现有 GuardrailProvider。
- 增加 session approval cache。
- 增加 path/command/tool rule。
- 增加前端 approval UI。
- 将审批结果写入 run events 和 audit log。

成功标准:

- 高风险工具调用可以被拦截、请求确认、按会话记住。
- plan mode、swarm exclusive、sandbox audit 不再是孤立逻辑。

### 阶段 3:后台任务面板

目标:统一 long-running run、shell、subagent、ACP agent 的可见性和控制。

建议任务:

- 定义 `BackgroundTask` schema。
- 新增 task list/output/cancel API。
- 把 subagent executor 和 sandbox bash 接入 task manager。
- 前端增加后台任务面板。

成功标准:

- 用户能看到所有后台运行中的任务。
- 用户可以查看 tail output、停止任务、追溯任务来源消息。

### 阶段 4:Plan Mode 升级

目标:把 plan mode 从 todo list 升级为计划-评审-执行工作流。

建议任务:

- 生成 `plan.md` artifact。
- plan mode 下限制写代码。
- `exit_plan_mode` 请求用户确认。
- 前端增加 plan review 面板。

成功标准:

- 非平凡任务在执行前有明确计划。
- 用户批准前不会修改业务文件。

### 阶段 5:Goal Mode 与 Session SDK

目标:支持长期目标和多客户端共享 session 能力。

建议任务:

- 新增 GoalState、goal tools、goal events。
- 增加预算控制和阻塞状态。
- 抽取 protocol/schema。
- 扩展 DeerFlowClient 或新增 TS/Python SDK。
- 后续接 ACP adapter。

成功标准:

- 用户能创建长期目标,agent 可跨多轮推进。
- Web、本地客户端、CLI/IDE 可共享同一套 session 协议。

---

## 五、不建议直接照搬的部分

### 5.1 不建议放弃 LangGraph

Kimi Code 的本地 loop 很适合 coding agent,但 DeerFlow 已经围绕 LangGraph 形成了 Gateway、runs、stream bridge、checkpointer、middleware、external API 等平台化能力。直接替换会带来巨大迁移成本,且削弱服务端部署优势。

建议保留 LangGraph,在 LangGraph 的工具层、中间件层和 runtime event 层吸收 Kimi 机制。

### 5.2 不建议一开始做完整 TUI

Kimi Code 的 TUI 很强,但 DeerFlow 当前主入口是 Web workspace。若要做本地客户端,优先考虑 Tauri/Electron 包 Web UI + 本地后端管理,不必先复制 TUI。

### 5.3 不建议服务端开放任意 shell hooks

Kimi 的 hooks 偏本地用户机器。DeerFlow 若部署在服务端,直接执行用户配置 shell hook 风险很高。服务端应优先使用 webhook、审批策略、审计事件,本地客户端再开放命令型 hooks。

### 5.4 不建议无上限自动 goal

Goal Mode 很强,但必须有预算、状态、暂停/取消和用户可见控制。否则长期自动运行容易产生费用、权限和安全风险。

---

## 六、建议优先级

| 优先级 | 机制 | 原因 | 复杂度 | 预期收益 |
|---|---|---|---|---|
| P0 | AgentSwarm | 直接增强 agent 能力,复用现有 subagent 基础 | 中 | 高 |
| P0 | 权限策略链 | 是 swarm、plan、本地客户端、安全审计的基础 | 中高 | 高 |
| P1 | 后台任务统一模型 | 改善长任务可见性,支撑本地客户端 | 中 | 高 |
| P1 | Plan Mode 升级 | 降低复杂任务误执行风险 | 中 | 中高 |
| P2 | Goal Mode | 打开长期自主任务能力 | 高 | 高 |
| P2 | Session SDK / Protocol | 支撑 Web/CLI/desktop/IDE 多端 | 高 | 高 |
| P3 | ACP Adapter | 接入 IDE 生态 | 中高 | 中高 |
| P3 | Plugin Marketplace | 提升扩展生态产品化 | 中高 | 中 |

推荐落地顺序:

1. `AgentSwarm`
2. 权限策略链
3. 后台任务统一模型
4. Plan Mode 升级
5. Goal Mode
6. Session SDK / Protocol
7. ACP Adapter
8. Plugin Marketplace

---

## 七、开放问题

1. DeerFlow 的 `agent_swarm` 应该作为普通 tool 暴露给模型,还是作为 plan mode/swarm mode 的显式用户命令触发?
2. Subagent profile 应该写在 `config.yaml`,还是放入独立 `agents/` 目录并支持 per-user custom agent?
3. Swarm 子 agent 是否允许写同一 workspace?是否需要文件级锁或任务范围约束?
4. 权限审批在 Web 前端中断 run 后如何恢复?是否需要持久化 pending approval?
5. 后台任务是否复用现有 `runs` 表,还是新增 `background_tasks` 表?
6. Plan artifact 应该存在线程 user-data workspace、outputs,还是 run events 中?
7. Goal Mode 的自动推进是否允许跨浏览器离线运行?如果允许,谁负责预算和取消?
8. 本地客户端与服务端部署在 hooks/权限/沙箱能力上是否需要两套 profile?

---

## 附录:对 DeerFlow 的一句话建议

DeerFlow 的未来不应在“LangGraph 平台”和“Kimi 式本地 runtime”之间二选一。更稳的方向是:

> **继续使用 LangGraph 承载服务端 agent 编排,同时补齐 AgentSwarm、权限策略链、后台任务、Plan/Goal、Session SDK 和 ACP,让 DeerFlow 同时具备平台能力和本地 coding-agent 体验。**
