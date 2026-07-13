# 多租户 Agent 发布平台 - M1 第十四轮代码复审

**状态：** 已复审，待修复  
**日期：** 2026-07-14

**关联文档：**

- 设计文档：[2026-07-12-multi-tenant-agent-publishing-design.md](./2026-07-12-multi-tenant-agent-publishing-design.md)
- 开发计划：[../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第十三轮复审：[2026-07-13-m1-agent-control-plane-code-thirteenth-review.md](./2026-07-13-m1-agent-control-plane-code-thirteenth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 复审头：`cf84822c13b50c668877e55dfd535e56018a5fed` + 当前未提交工作区
- 本轮重点：第十三轮 4 个 Important 与 3 个 Minor 的关闭情况，以及发布快照并发一致性、Skill revision 固化、真实 Gateway 启动链路和全仓门禁

---

## 1. 复审结论

第十三轮提出的问题已经按正确方向关闭：数据库 miss 只回退当前 owner 的旧文件；请求无法再伪造 `__agent_*`；`inherit` 会在发布时物化为不可变 Skill revisions；数据库草稿运行时会按顺序注入完整 `AGENT.md + SOUL.md`。authoring state 已改为单 SQL，真实 HTTP 下一轮 Agent 运行已覆盖 prompt/tool policy 与 legacy fallback，全仓 Ruff 门禁也已恢复。

本轮仍发现 **0 个 Critical、2 个 Important、2 个 Minor**：

1. 发布服务在事务外、通过多条 SQL 读取草稿及子表，并在另一个事务中创建 Release；并发 PATCH 可以让发布读取到从未同时存在过的“主草稿 + Skills + Connector grants”混合状态。
2. Skill 的可选性校验、文件读取和 visibility/owner 元数据读取由多个独立 `StorageSkillsIndex` 快照完成；校验后 Skill 被禁用、删除或 owner 元数据变化时，仍可能发布空内容、失效内容，甚至按默认 `public` 固化 revision。
3. Alembic 的 `fileConfig()` 会禁用已存在的应用 logger；Gateway lifespan 跑过迁移后，运行期 `app.*` / `deerflow.*` 日志可能静默，且已经形成稳定的测试顺序依赖。
4. 结构化创建接受任意 1–64 字符 slug，但当前草稿运行时只接受 `[A-Za-z0-9-]+`，`assistant_id` 还会统一小写并把下划线改为连字符；部分已成功创建/发布的 Agent 无法按其保存 slug 进入当前草稿运行链路。

**结论：Ready to merge：No。** 两个 Important 关闭前，不建议进入 M2。

---

## 2. 已确认修复

- **旧 Agent 迁移窗口兼容已恢复**：数据库明确 miss 时仅检查当前 owner 的旧 `config.yaml`；共享目录、其他 owner 及数据库异常均不回退。
- **运行时内部字段防伪已关闭**：Gateway 对 `config.context` 使用 allowlist，对 `configurable` 清除 `__agent_*`；hydration 在任何早退前再次清除两个容器。
- **`inherit` 发布语义已关闭**：结构化创建和历史迁移默认 `explicit + []`；只有对话式 setup/旧导入省略 Skills 时使用 `inherit`，发布时会枚举并固定当前可选 Skills。
- **完整指令注入已关闭**：数据库草稿调用 `compose_agent_instructions()`，full graph 与 flash direct 均消费同一个可信指令块。
- **authoring state 单快照已关闭**：owner + slug、identity、draft、skills、grants 由一条 SQL 返回。
- **完整 Ruff 门禁已关闭**：`ruff check .` 与 `ruff format --check .` 均通过。
- **真实运行时集成覆盖已补齐**：HTTP E2E 已从 bootstrap 继续执行数据库草稿 Agent，并验证 prompt、tool groups、伪造字段隔离和 owner 旧文件回退。

---

## 3. Critical 问题

无。

---

## 4. Important 问题

### 4.1 Important-1：发布读取与 Release 写入不在同一一致性边界，可生成混合草稿快照

**相关文件：**

- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [published_agent/sql.py](../../../backend/packages/harness/deerflow/persistence/published_agent/sql.py)
- [2026-07-12-multi-tenant-agent-publishing-dev-plan.md](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)

**问题说明：**

开发计划 F1.5 明确要求发布在一个数据库事务内完成“读草稿 → 校验 → Skill revision → Release → 指针切换”。当前 `PublishService.publish()` 在进入发布事务前调用 `AgentDraftRepository.get()`，完成校验、Connector 解析和 Skill 内容准备后，才在 `_publish_unit_of_work()` 中打开新 session/transaction。

更关键的是，`AgentDraftRepository.get()` 本身依次执行三条 SELECT：先读 `agent_drafts` 主行，再读 Skills，最后读 Connector grants。在 PostgreSQL 默认 `READ COMMITTED` 下，每条语句都可以看到不同的已提交快照。并发结构化 PATCH 若在三条查询之间提交，发布可能组合出：

```text
旧 revision / 旧 AGENT.md + 新 Skills + 新 Connector grants
```

这个组合从未作为一个草稿 revision 同时存在，却会被校验并固化成不可变 Release。即使三条读取恰好一致，读取完成后到 Release 事务提交前也没有锁或 revision 重检，发布无法证明自己固化的是一个稳定草稿版本。

**影响：**

- 不可变 Release 可能不对应任何真实草稿 revision，历史对比与审计语义失真。
- 新 Skill 的 Connector 要求可能错误地与旧 grants 组合，或相反，得到不可线性化的校验结果。
- 成功的并发 PATCH 与成功的发布都向调用方返回 2xx，但最终 Release 无法由任一请求单独解释。
- 当前测试只覆盖 publish 与 PATCH 各自的正常/失败路径，没有覆盖两者的真实交叉时序。

**建议修复：**

- 为发布增加 agent_id + owner 的单 SQL 草稿快照读取，一次返回主行、Skills 与 grants，并保留 `revision`。
- 在最终发布事务内锁定/重读 draft revision；若与已校验快照不一致，则返回明确冲突或重新获取并完整重试，不能继续创建 Release。
- 所有能修改 Skills/grants 的仓储方法都必须递增同一 draft revision，否则最终重检无法覆盖子表更新。
- 增加 PostgreSQL 双连接测试：发布在读取或准备阶段暂停，并发 PATCH 成功后恢复发布；最终必须冲突/重试，不能产生混合 Release。

### 4.2 Important-2：Skill 校验与内容/权限固化使用多个独立快照，存在 TOCTOU 和默认 public 回退

**相关文件：**

- [factory.py](../../../backend/packages/harness/deerflow/publishing/factory.py)
- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py)
- [publish_service.py](../../../backend/packages/harness/deerflow/publishing/publish_service.py)
- [validation.py](../../../backend/packages/harness/deerflow/publishing/validation.py)

**问题说明：**

生产 `_OwnerAwareSkillsIndex` 的 `is_selectable_by()`、`list_selectable_by()`、`files_for()` 和 `get()` 每次都会构造一个新的 `StorageSkillsIndex`。发布流程先在 `validate_draft_for_publish()` 中检查 Skill 可选性，随后才分别调用 `files_for()` 读取文件、调用 `get()` 读取 visibility/caps。

因此“校验通过的 Skill”与“最终写入 revision 的 Skill”不是同一个不可变解析结果。若 Skill 在校验后被禁用、删除、编辑或修改 owner 元数据：

- `files_for()` 可以返回空映射，但发布仍会对“仅包含 skill name”的内容计算 checksum 并写入空快照；
- `get()` 可以返回 None，而代码会把 visibility 默认成 `public`，将原私有 Skill 错误固化为 public revision；
- disabled 状态只在先前校验中读取，准备阶段不会再次拒绝，因而可发布校验后已禁用的内容；
- 文件内容、caps 与 visibility 可能分别来自不同时间点。

这违反了“所有选中 Skill 必须存在、启用、属于 owner 且可以产出不可变 revision”的发布规则。

**影响：**

- 发布成功的 Release 可能包含空 Skill revision 或校验时不存在的内容版本。
- 私有 Skill 在竞态下可能被错误记录为 public，污染后续 revision 去重与权限元数据。
- Release 中记录的 connector capabilities 可能与实际固化文件不匹配。
- `inherit` 会扩大竞态面，因为枚举、校验和逐个固化同样使用不同索引快照。

**建议修复：**

- 给生产 Skill adapter 增加一次性 `resolve_publish_snapshot(name, owner)`：同时返回 enabled、visibility、owner、caps 与文件 bytes，后续校验和 revision 创建只消费该不可变对象。
- 未找到元数据、文件为空/缺 `SKILL.md`、owner/visibility 不完整时必须 fail closed，发布路径不得默认 `public`。
- 对 `inherit` 先构建完整的 owner-scoped Skill snapshot 列表，再基于同一列表校验与固化。
- 增加可变 fake index 与真实存储竞态测试：校验后删除/禁用/改 owner/改内容时，发布必须拒绝或固定校验时已捕获的同一快照，不能混用新状态。

---

## 5. Minor 问题

### 5.1 Alembic 自动迁移会禁用已加载应用 logger，并造成测试顺序依赖

[migrations/env.py](../../../backend/packages/harness/deerflow/persistence/migrations/env.py) 调用 `fileConfig(config.config_file_name)` 时使用默认 `disable_existing_loggers=True`。Gateway 在导入全部路由/logger 后才由 lifespan 调用自动迁移，因此迁移会把已存在的 `app.gateway.services` 等 logger 标记为 disabled；后续 `apply_logging_level()` 只调整 level，不会重新启用它们。

可稳定复现：先运行 `test_published_agents_app_wiring.py`（启动 Gateway 并跑迁移），再运行 `test_build_run_config_context_plus_configurable_warns`，后者收不到应有 warning；反向或单独执行则通过。

建议在 Alembic env 使用 `fileConfig(..., disable_existing_loggers=False)`，或在应用内迁移时跳过 Alembic logging 重配置，并增加迁移后关键 logger 仍启用的测试。

### 5.2 结构化 slug 契约与当前草稿运行标识不一致

[published_agents.py](../../../backend/app/gateway/routers/published_agents.py) 只限制 slug 长度；[services.py](../../../backend/app/gateway/services.py) 会把 `assistant_id` 小写并将 `_` 改为 `-`；[runtime_loader.py](../../../backend/packages/harness/deerflow/publishing/runtime_loader.py) 最终又按 `^[A-Za-z0-9-]+$` 校验并用该值查询数据库。

专项验证中，`bad/name`、`has space`、`under_score` 均可通过 `CreateAgentRequest`，但会被运行时拒绝；`MiXeD` 可通过运行时字符校验，却会在 assistant 映射后变成 `mixed`，无法命中原 slug。

建议定义并复用唯一的 slug 规范化/校验函数，在创建、导入、assistant 路由和数据库查询边界保持一致；若运行时应使用稳定 `agent_id`，则不要再通过有损 slug 归一化选择草稿。

---

## 6. 第十三轮问题关闭状态

| 第十三轮问题 | 本轮状态 | 说明 |
|---|---|---|
| Important-1：DB 启用后旧文件 Agent 无法运行 | **已关闭** | owner-only DB miss fallback + HTTP legacy run 已覆盖 |
| Important-2：请求可覆盖 `__agent_*` hydration | **已关闭** | Gateway allowlist/scrub + hydration 二次 scrub + HTTP 伪造测试 |
| Important-3：`inherit` 未进入发布快照 | **已关闭** | 默认/迁移语义明确，publish 会 materialize 当前可选 Skills |
| Important-4：数据库草稿忽略 AGENT.md | **已关闭** | 完整组合指令已进入 full graph 与 flash direct |
| Minor-1：authoring state 非单快照 | **已关闭** | owner + slug 单 SQL 加载 identity/draft/skills/grants |
| Minor-2：全仓 Ruff 失败 | **已关闭** | `ruff check .` 与 `ruff format --check .` 均通过 |
| Minor-3：运行时测试停留在 helper | **已关闭** | 真实 HTTP 下一轮 DB Agent/legacy Agent 执行已覆盖 |

---

## 7. 验证记录

### 7.1 静态检查

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
652 files already formatted

git diff --check
通过（仅有工作区 CRLF 提示，无 whitespace error）
```

### 7.2 第十三轮修复专项

```text
83 passed, 1 skipped, 2 warnings in 40.84s
```

覆盖 runtime loader、Gateway 配置隔离、Publish/inherit、authoring snapshot、真实 HTTP 下一轮运行及迁移默认值。跳过项为本地无 PostgreSQL。

### 7.3 扩展 M1 回归

```text
1 failed, 414 passed, 3 skipped, 3 warnings in 59.96s
```

唯一失败：

```text
test_gateway_services.py::test_build_run_config_context_plus_configurable_warns
```

两文件最小复现：

```text
uv run pytest -q \
  tests/test_published_agents_app_wiring.py \
  tests/test_gateway_services.py::test_build_run_config_context_plus_configurable_warns -vv

1 failed, 3 passed
```

该失败由 Alembic 禁用既有 logger 引起，见 Minor-1。标准全量 `uv run pytest -q` 在本地 300 秒执行窗口内未完成，因此未取得完整全仓结果；这不是额外的断言失败，但 M1 的 `make test` 全量门禁仍需在 CI 给出完成证据。

### 7.4 PostgreSQL

本地没有可用 PostgreSQL，相关迁移与双连接测试跳过；CI 已设置 `REQUIRE_POSTGRES_TESTS=1`。本轮 Important-1 必须补 PostgreSQL 发布/PATCH 交叉时序测试，不能仅依赖 SQLite。

---

## 8. 修复优先级

1. 将发布草稿读取、revision 稳定性检查与 Release 写入收敛到可证明的一致性边界，阻止混合快照。
2. 将 Skill 校验、文件、caps、visibility/owner 固化为同一个 fail-closed 不可变解析结果。
3. 修复 Alembic 对应用 logger 的全局副作用，恢复测试顺序无关性与生产日志。
4. 统一结构化 slug 与草稿运行标识契约。

---

## 9. 最终结论

第十三轮的所有问题均已实质关闭，修复质量较上一轮明显提升，尤其是 DB 草稿运行时权威性、迁移兼容和真实 HTTP 覆盖已经形成闭环。

但发布服务目前只保证“Release/Skill revision/指针写入”位于单事务，尚未保证“被发布的草稿与 Skill 解析结果”来自一个稳定快照。由于 Release 一经创建不可变，这两个并发一致性问题必须在 M1 关闭；否则 M2 会把无法追溯到真实草稿状态的 Release 直接暴露给外部 API 与 IM 运行时。

**Ready to merge：No。**
