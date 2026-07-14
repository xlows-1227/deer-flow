# Skill 执行内容脱敏代码 Review

## 状态

- 状态：Superseded
- 日期：2026-07-13
- 优先级：P0
- 对应 Spec：[`2026-07-13-skill-execution-content-redaction-spec.md`](./2026-07-13-skill-execution-content-redaction-spec.md)
- 实施记录：[`../execution/2026-07-13-skill-execution-content-redaction-implementation.md`](../execution/2026-07-13-skill-execution-content-redaction-implementation.md)
- 复审文档：[`2026-07-13-skill-execution-content-redaction-code-review-v2.md`](./2026-07-13-skill-execution-content-redaction-code-review-v2.md)
- 影响范围：`SkillContentRedactor`、RunJournal、run worker / StreamBridge、Gateway 读时脱敏、前端 Tool Display Policy、相关回归测试

## 摘要

本文档为初审记录，保留当时发现的 Medium / Low 问题供对照。修复后的复审结论见 [`code-review-v2`](./2026-07-13-skill-execution-content-redaction-code-review-v2.md)：初审 4 项 Medium 与 2 项前端 Low 均已闭环，审查通过。

## 审查依据

- [`2026-07-13-skill-execution-content-redaction-spec.md`](./2026-07-13-skill-execution-content-redaction-spec.md)
- [`../execution/2026-07-13-skill-execution-content-redaction-implementation.md`](../execution/2026-07-13-skill-execution-content-redaction-implementation.md)
- Spec「Review 重点」五项决策问题
- 工作区相关 diff（未提交）：`privacy.py`、`journal.py`、`worker.py`、`app/gateway/skill_redaction.py`、Gateway routers、前端 execution panel 与对应测试

## 审查范围

| 区域 | 覆盖 |
|---|---|
| Phase 0 前端默认安全展示 | 是 |
| Phase 1 `SkillContentRedactor` | 是 |
| Phase 2 RunJournal / StreamBridge / tracing | 是 |
| Phase 3 Gateway messages/events/state/history/share | 是 |
| Phase 4 projection manifest / grants | 已通过服务端可信 request state 注入；客户端输入会被剥离 |
| External API V1 / OpenAI compat | 抽查：不暴露 tool payload；模型最终 answer 复述属 Spec 非目标 |
| DeerFlowClient 内嵌 `stream()` | 未纳入本次 diff；同进程嵌入通常不跨用户边界 |

---

## Review 结论（初审）

主链路方向正确，未发现 Critical / High 级主路径透传。初审仍发现 4 项 Medium 与若干 Low，详见下文「发现的问题」。

**后续状态**：上述问题已在修复轮次中处理；以 [`code-review-v2`](./2026-07-13-skill-execution-content-redaction-code-review-v2.md) 为准。

---

## Spec「Review 重点」结论

| # | Spec 问题 | 本次结论 |
|---|---|---|
| 1 | RunEventStore 普通 message/trace 是否只保存脱敏事件，原始状态仅保留在内部 checkpointer？ | **接受并已落地。** Journal `_put` 前脱敏；checkpointer 保留原文；用户可见 state/history 读时脱敏。 |
| 2 | 用户可见 ToolMessage 是否保留原工具名与 `tool_call_id`，经 `additional_kwargs.skill_execution` 表达脱敏状态？ | **是。** 固定结果 `Skill instructions loaded.`，并写入 `visibility` / `event_type` / `skill_execution`。 |
| 3 | 是否需同时覆盖 `bash`、subagent、tracing，还是最小 P0 = `read_file` + 全部用户 API？ | **已覆盖。** `bash`、subagent `task` 结果、后续 trace/error 与 tracing callback 均进入统一安全边界。 |
| 4 | 历史 RunEventStore 原文读时长期遮蔽，还是一次性物理清理？ | **当前采用读时长期遮蔽；物理清理列为后续迁移项，与 Spec 发布顺序一致。** |
| 5 | owner 执行记录是否也始终隐藏 Skill 原文？ | **是。** 执行记录不区分 owner；原文仅能通过受权限保护的 Skill 内容接口 / 编辑器查看。 |

---

## 发现的问题

### P2-1. checkpoint / state 路径上非 `read_file` 孤儿 ToolMessage 可能 fail-open

**严重度**：Medium（安全）

**相关文件**：

- `backend/packages/harness/deerflow/skills/privacy.py`（`_tool_result_descriptor`）
- `backend/app/gateway/skill_redaction.py`（`redact_channel_values`）

**问题**：

孤儿 `read_file` ToolMessage（同批无配对 AI tool call）会保守隐藏；但 `grep` / `glob` / `ls` / `bash` 在 redactor 内若 `descriptor is None` 则原样深拷贝返回。

Run events 读时路径在 `redact_run_event_rows()` 中对 `bash/grep/glob/ls` 做了跨页补查与 `_inject_fail_closed_metadata()`；**checkpoint / state / history / wait / share 使用的 `redact_channel_values()` 没有这套 legacy 逻辑**，只做单次 `redact_stream_payload()`。

**影响**：

中断 run、summarization 拆散配对、状态损坏等场景下，`GET /api/threads/{id}/state`、history、wait、公开 share 等可能返回 Skill 目录列表或命令输出原文。

**建议**：

- 在 `_tool_result_descriptor()` 中对所有可能访问 Skill 投影的工具（至少 `grep`/`glob`/`ls`/`bash`）在孤儿场景与 `read_file` 同等 fail-closed；或
- 让 `redact_channel_values()` 对 `messages` 复用与 `redact_run_event_rows()` 相同的 orphan 处理。
- 补充针对 `GET .../state` 的 `grep`/`ls` 孤儿回归测试。

---

### P2-2. `task` 子代理工具结果不受 Skill redactor 约束

**严重度**：Medium（安全 / Spec 覆盖缺口）

**相关文件**：

- `backend/packages/harness/deerflow/skills/privacy.py`（`redact_message` / `_classify_tool_call`）
- `frontend/src/components/workspace/chats/workspace-tool-execution-panel.tsx`（`collectToolExecutions` 跳过 `task`）

**问题**：

Redactor 仅通过 Skill 投影路径与 `(run_id, namespace, tool_call_id)` 关联识别敏感调用。父 run 上的 `task` ToolMessage 通常是自由文本汇总，不会走 `read_file`/`bash` 路径分类；即使子代理在 namespace 内读过 Skill，父级 `task` 结果也不会替换为安全占位。

Spec 将「模型最终自然语言复述」列为非目标，但 **`task` 工具结果是结构化 ToolMessage，属于用户可见执行记录**，应在覆盖范围内。

**影响**：

子代理若在返回串中复述 Skill 指令或 supporting file 内容，会进入 SSE、RunEventStore、Gateway messages/events/state。前端执行面板虽跳过 `task`，主消息流仍可通过 `findToolCallResult` 展示。

**建议**：

- 对 `task` / subagent 结果在用户边界统一替换为安全摘要（保留 status/metadata）；或
- harness 层禁止将 Skill 原文写入返回给父 agent 的字符串，并对已知 subagent 命名空间内结果 fail-closed。
- 增加「subagent `task` 返回含 marker」的跨边界回归测试。
- 若本 PR 明确最小范围为 `read_file` + 用户 API，须同步修订实施记录中的“Phase 0–4 全覆盖”表述。

---

### P2-3. 跨页孤儿 `read_file` 会误伤非 Skill 文件读取

**严重度**：Medium（正确性 / 兼容性）

**相关文件**：

- `backend/app/gateway/skill_redaction.py`（`_legacy_results_needing_context`）
- `backend/packages/harness/deerflow/skills/privacy.py`（孤儿 `read_file` 保守隐藏）

**问题**：

`_legacy_results_needing_context` 只为 `bash`/`grep`/`glob`/`ls` 触发跨页补查。分页结果页只有 `read_file` ToolMessage、配对 AI 调用在更早一页时，不会向前加载调用上下文。`SkillContentRedactor` 随后把所有未观察到的 `read_file` 结果按孤儿 Skill 读取 fail-closed，**非 Skill 的 `/mnt/user-data/...` 文件内容会被错误替换成 `Skill instructions loaded.`**。

这违反 Spec「不改变非 Skill 工具在后端消息协议中的业务语义」。

**建议**：

- 将 `read_file` 纳入跨页补查集合。
- 仅在补查后仍无法配对时才对 `read_file` 保守隐藏。
- 增加「非 Skill `read_file` 跨页不被误伤」回归测试。

---

### P2-4. `skill_projection_manifest` / `skill_grants` 未进入 Gateway run context 白名单

**严重度**：Medium（Phase 4 接线缺口）

**相关文件**：

- `backend/app/gateway/services.py`（`_CONTEXT_CONFIGURABLE_KEYS` / `merge_run_context_overrides`）
- `backend/packages/harness/deerflow/skills/privacy.py`（`from_run_context`）
- `backend/packages/harness/deerflow/runtime/runs/worker.py`

**问题**：

worker 已从 `config["context"]` 读取 `skill_projection_manifest` / `skill_grants` 做投影级脱敏，但 `merge_run_context_overrides` 白名单未包含这两个键。经 `RunCreateRequest.context` 或服务端注入意图传入的授权信息不会进入运行时 `config`。

Gateway 启动的 run 会退回仅依赖配置根 `/mnt/skills` 路径兼容分类，无法按 Spec 用 per-run 投影根（如 `/runtime-skills/...`）识别敏感访问。

**建议**：

- 将这两个键加入白名单；**不得允许客户端伪造未授权 grant**，应由服务端在创建 run 时写入。
- 增加「自定义投影根路径被识别为敏感」的 worker / Gateway 集成测试。
- 若 Phase 4 尚未对外启用，实施记录应标注为“组件就绪、Gateway 接线待完成”，避免与“Phase 0–4 已完成”冲突。

---

### P3-1. 前端未显式处理 `visibility=redacted`，且未优先使用服务端安全 `skill_name`

**严重度**：Low（纵深防御 / UX）

**相关文件**：

- `frontend/src/components/workspace/chats/workspace-tool-execution-panel.tsx`

**问题**：

Spec 要求收到 `additional_kwargs.visibility="redacted"` 时显示「内容已隐藏」，且不能提供展开原文交互。当前依赖默认安全策略隐藏结果，兜底有效，但：

- 未显式读取 `visibility=redacted`；
- Skill 名称仍优先从 path 推断，未使用服务端已脱敏 args 中的 `skill_name`。

**建议**：

- 显式分支处理 `visibility=redacted`。
- 优先展示服务端返回的安全 `skill_name` / `skill_execution` metadata。

---

### P3-2. 主聊天 CoT / 工具卡片纵深防御未与 execution panel 对齐

**严重度**：Low（纵深防御）

**相关文件**：

- `frontend/src/components/workspace/messages/message-group.tsx` 等主聊天渲染路径

**问题**：

本次 diff 主要加固了 `WorkspaceToolExecutionPanel`。主聊天路径仍可能展示工具 args（路径/命令摘要），内容安全依赖服务端已改写 args。服务端脱敏生效时风险可控，但 Spec 要求前端默认安全策略作为纵深防御，主路径尚未完全对齐。

**建议**：

后续将 Tool Display Policy 抽成共享模块，主聊天与执行面板共用。

---

### P3-3. 工作区混杂与本改动无关的未提交变更

**严重度**：Low（发布与审阅）

**问题**：

同工作区还存在 `openai_compat`、auth、agents/new 等无关改动。混在同一 PR 会降低审阅质量，并增加回滚面。

**建议**：

Skill 脱敏单独成 PR；无关变更拆分提交。

---

## 已确认符合 Spec 的要点

| 区域 | 结论 |
|---|---|
| 双轨架构（ADR-SSCR-001） | 内部 graph/checkpointer 保留原文；用户边界统一 redactor 生成副本 |
| RunJournal 写时脱敏 | `_put()` → `redact_event()`；原 LangChain 对象不被 mutate |
| SSE / worker | `bridge.publish()` 前 `redact_stream_payload()`；异常/error 不泄露正文 |
| Tracing | `_restrict_unsafe_tracing_callbacks()` 剥离未声明 `deerflow_skill_content_safe=True` 的外部 callback；`RunJournal` 已声明 safe |
| Gateway 读时 | messages/events + legacy 跨页（`bash/grep/glob/ls`）+ fail-closed 有测试；share 复用 `redact_channel_values` |
| 路径规范化 | `_normalize_path` / `_is_under`，含 `..` 逃逸用例 |
| 关联键 | `(run_id, namespace, tool_call_id)`；subgraph 隔离有测试 |
| 未知 stream mode | 递归脱敏，异常时固定 `redaction_error`，不透传 |
| 前端 execution panel | 默认不展示 raw args/result；`read_file` 永不渲染文件正文 |
| 指标 | 进程级低基数快照；label 白名单 |
| 性能 | 实施记录：200 条消息 p95 ~3.9ms，满足 Spec ≤10ms |
| External API V1 | 仅最终 answer / 通用错误，不暴露 tool 消息（模型复述属非目标） |

---

## Spec 对照速览

| Spec 要求 | 状态 |
|---|---|
| 内部原文 / 用户可见副本双轨 | 通过 |
| Journal 写前脱敏 | 通过 |
| StreamBridge 全 mode 脱敏 | 通过 |
| Gateway messages/events/state/history/share | 通过；`read_file/grep/glob/ls/bash` 孤儿结果 fail closed |
| 旧数据读时脱敏 + 跨页 | 通过；`read_file` 参与跨页配对，普通文件读取不被误伤 |
| tracing 禁原始采集 | 通过 |
| 前端默认安全展示 | 通过；execution panel 与主聊天复用共享 Tool Display Policy |
| `bash` / subagent | 通过；父 run 的 `task` args/result/事件元数据均脱敏 |
| projection / grant 主分类 | 通过；仅允许服务端可信 request state 注入运行上下文 |
| 脱敏不修改模型/checkpointer 对象 | 通过 |
| 未知结构 / redactor 异常 fail-closed | 通过（异常路径） |

---

## 合并前 checklist

1. [x] 修复 P2-1（state/history/share 孤儿非 `read_file` fail-closed）并补测试
2. [x] 修复 P2-3（`read_file` 纳入跨页补查，避免误伤非 Skill 读取）并补测试
3. [x] P2-2 纳入 `task` 脱敏，并覆盖父消息、SSE、trace/error 与事件元数据
4. [x] P2-4 由服务端可信 request state 接通 grant/manifest，拒绝客户端伪造授权上下文
5. [ ] 提交/PR 时仅选择脱敏相关文件；工作区无关改动已原样保留
6. [x] P3-1 / P3-2 前端纵深防御对齐

---

## 修复落地记录

1. **P2-1**：孤儿 `read_file/grep/glob/ls/bash` ToolMessage 统一 fail closed，state/history/share 复用同一 redactor。
2. **P2-3**：`read_file` 加入 legacy 跨页上下文补查；有明确非 Skill 调用时保留原业务结果。
3. **P2-2**：`task` 调用仅保留 description/type，父级结果替换为固定成功/失败摘要；原始 prompt、result、artifact、metadata 与后续 trace/error 均不可见。
4. **P2-4**：客户端 `body.context` 与 `config.context` 中的 manifest/grants 均被剥离；仅从服务端拥有的 `request.state` 深拷贝注入。
5. **P3-1 / P3-2**：新增共享 `getToolDisplayPolicy()`，显式处理 `visibility=redacted`，优先采用服务端安全 `skill_name`，并禁止主聊天展示 raw bash 命令或受保护路径。
6. **验证**：后端相关跨层测试 `220 passed`；前端完整单元测试 `40 files / 249 tests passed`；TypeScript、ESLint、Prettier、Ruff 均通过。
