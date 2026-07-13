# Skill 执行内容脱敏代码 Review V2

## 状态

- 状态：Passed
- 日期：2026-07-13
- 优先级：P0
- 对应 Spec：[`2026-07-13-skill-execution-content-redaction-spec.md`](./2026-07-13-skill-execution-content-redaction-spec.md)
- 初审文档：[`2026-07-13-skill-execution-content-redaction-code-review.md`](./2026-07-13-skill-execution-content-redaction-code-review.md)
- 实施记录：[`../execution/2026-07-13-skill-execution-content-redaction-implementation.md`](../execution/2026-07-13-skill-execution-content-redaction-implementation.md)
- 影响范围：初审 P2/P3 修复闭环、回归测试、前端 Tool Display Policy 共享模块

## 摘要

对照初审 [`code-review`](./2026-07-13-skill-execution-content-redaction-code-review.md) 的 4 项 Medium 与 2 项前端 Low，复审确认均已按建议落地，并有对应回归测试。未发现新的 Critical / High / Medium 安全缺口。剩余仅提交拆分与 Phase 4 调度路径接线等 Low 事项，不阻断合并。

## 审查依据

- [`2026-07-13-skill-execution-content-redaction-spec.md`](./2026-07-13-skill-execution-content-redaction-spec.md)
- [`2026-07-13-skill-execution-content-redaction-code-review.md`](./2026-07-13-skill-execution-content-redaction-code-review.md)
- [`../execution/2026-07-13-skill-execution-content-redaction-implementation.md`](../execution/2026-07-13-skill-execution-content-redaction-implementation.md)
- 修复后工作区代码与相关单测

## 审查范围

| 初审项 | 复审覆盖 |
|---|---|
| P2-1 孤儿非 `read_file` fail-open | 是 |
| P2-2 `task` 子代理结果 | 是 |
| P2-3 跨页 `read_file` 误伤 | 是 |
| P2-4 grant/manifest 接线与伪造 | 是 |
| P3-1 / P3-2 前端纵深防御 | 是 |
| P3-3 PR 拆分 | 仍为流程项 |

---

## Review 结论

**复审通过。** 初审 Medium 项均已关闭；主链路与 Spec 验收标准一致。

- 0 项 Critical / High
- 0 项未解决 Medium
- 2 项 Low 残余（提交组织；调度器路径尚未注入可信 Skill context）

后端目标测试本轮执行结果：

```text
tests/test_skill_content_redactor.py
tests/test_run_journal_skill_redaction.py
tests/test_run_stream_skill_redaction.py
tests/test_shares_skill_redaction.py
tests/test_thread_run_messages_pagination.py
tests/test_threads_router.py
tests/test_gateway_services.py
→ 118 passed
```

---

## 初审项闭环核对

### P2-1 — 已解决

**修复要点**：

- `SkillContentRedactor._FAIL_CLOSED_ORPHAN_TOOL_NAMES` 现为 `{read_file, grep, glob, ls, bash}`。
- 孤儿结果在 `_tool_result_descriptor()` 中统一返回 descriptor，因此 `redact_channel_values()` / state / history / share 无需单独 legacy 逻辑即可 fail-closed。

**证据**：

- `backend/packages/harness/deerflow/skills/privacy.py`
- `test_get_thread_state_fails_closed_for_orphan_skill_context_tools`（参数化 `grep/glob/ls/bash`）

---

### P2-2 — 已解决

**修复要点**：

- `observe_message` 将 `task` 记入 `_subagent_calls`。
- AI `task` args 仅保留 `description` / `subagent_type` + `redacted`。
- Tool 结果替换为固定成功/失败摘要，并清空 provider metadata / artifact。
- 同 run 后续 `trace` / `error` 在存在 subagent 调用时一并隐藏。

**证据**：

- `test_redacts_parent_visible_subagent_task_result_and_preserves_status`
- `test_redacts_subagent_task_event_metadata`
- `test_redacts_trace_error_text_after_subagent_task`
- `test_run_agent_redacts_parent_visible_subagent_task_result`

---

### P2-3 — 已解决

**修复要点**：

- `_LEGACY_CONTEXT_TOOL_NAMES` 已包含 `read_file`。
- 跨页先 `redact_event_batch(older)` 建立观察索引，再脱敏当前页；已观察的非 Skill `read_file` 保留原文。

**证据**：

- `test_run_messages_preserves_non_skill_read_file_across_page_boundary`
- `test_run_messages_loads_legacy_call_context_across_page_boundary`
- `test_run_messages_fail_closed_when_legacy_call_context_is_missing`

---

### P2-4 — 已解决（且比初审建议更安全）

**修复要点**：

- **不**把 `skill_projection_manifest` / `skill_grants` 加入客户端可写白名单。
- `build_run_config()` 从 client `context` 剥离上述键。
- `inject_server_skill_context()` 仅从 `request.state` 深拷贝注入运行时 `config['context']`。
- worker `from_run_context()` 消费该可信 context；自定义投影根可被识别。

**证据**：

- `test_client_context_cannot_inject_skill_projection_authority`
- `test_server_injects_trusted_skill_projection_authority`
- `test_run_agent` 投影根 stream 回归（`/runtime-skills/...`）

该实现满足初审「不得允许客户端伪造 grant」约束，优于简单加入 merge 白名单。

---

### P3-1 / P3-2 — 已解决

**修复要点**：

- 新增共享模块 `frontend/src/core/tools/display-policy.ts`。
- 执行面板读取 ToolMessage `additional_kwargs.visibility === "redacted"`，并优先使用服务端 `skill_name`。
- 主聊天 `message-group.tsx` 复用同一 `getToolDisplayPolicy`；受保护路径不再用于 artifact 自动打开 / 路径展示。

**证据**：

- `frontend/src/core/tools/display-policy.ts`
- `workspace-tool-execution-panel.tsx` / `message-group.tsx`
- `frontend/tests/unit/core/tools/display-policy.test.ts`

---

### P3-3 — 仍为流程项（不阻断）

工作区仍可能混有与脱敏无关的未提交变更（如 OpenAI compat、auth、agents/new）。合并时请只挑选本功能文件，或拆 PR。

---

## 复审新发现

### P3-4. 调度器 run 路径未调用 `inject_server_skill_context`

**严重度**：Low（Phase 4 完整性，当前非泄露）

**相关文件**：`backend/app/gateway/scheduler.py`

调度任务创建 run 时调用了 `build_run_config` + `merge_run_context_overrides`，但未调用 `inject_server_skill_context`。客户端无法经 merge 白名单伪造 grant（安全侧无 fail-open）；但若未来定时任务依赖 per-run 投影根，服务端可信 manifest/grants 也不会被注入，将退回配置根 `/mnt/skills` 兼容分类。

**建议**：

Skill sharing / projection 对调度任务上线前，为 scheduler 增加与 Gateway HTTP run 相同的可信注入点。

---

## Spec「Review 重点」复审结论

| # | Spec 问题 | V2 结论 |
|---|---|---|
| 1 | RunEventStore 是否只存脱敏事件 | 维持 Accepted；写时 + 读时双防线仍在 |
| 2 | 保留工具名 / `tool_call_id` + `skill_execution` | 维持；`task` 另用 `subagent_execution` |
| 3 | bash / subagent / tracing 覆盖范围 | **已覆盖** bash、`task`、tracing callback 限制 |
| 4 | 历史原文读时遮蔽 vs 物理清理 | 维持读时长期遮蔽 |
| 5 | owner 执行记录是否也隐藏原文 | 维持始终隐藏 |

---

## Spec 对照速览（修复后）

| Spec 要求 | V2 状态 |
|---|---|
| 双轨架构 | 通过 |
| Journal 写前脱敏 | 通过 |
| StreamBridge 全 mode 脱敏 | 通过 |
| Gateway messages/events/state/history/share | 通过（含孤儿 fail-closed） |
| 旧数据读时脱敏 + 跨页（含 `read_file`） | 通过 |
| tracing 禁原始采集 | 通过 |
| 前端默认安全展示（panel + CoT） | 通过 |
| bash / subagent | 通过 |
| projection / grant 服务端权威 | 通过（HTTP run）；scheduler 见 P3-4 |
| fail-closed / 不 mutate 原文 | 通过 |

---

## 合并前 checklist

1. [x] P2-1 孤儿上下文工具 fail-closed + state 测试
2. [x] P2-2 `task` 用户边界脱敏 + 测试
3. [x] P2-3 `read_file` 跨页补查且不误伤普通文件
4. [x] P2-4 客户端伪造剥离 + 服务端可信注入
5. [x] P3-1 / P3-2 共享 Tool Display Policy
6. [ ] 合并时排除无关工作区改动（P3-3）
7. [ ] （后续）scheduler 接入 `inject_server_skill_context`（P3-4）

---

## 建议

1. **可以合并** Skill 脱敏相关改动。
2. 合并时严格按文件挑选，避免带入无关 diff。
3. Phase 4 对调度任务启用投影前，关闭 P3-4。
