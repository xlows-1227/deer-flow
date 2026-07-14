# 多租户 Agent 发布平台 — M2 代码复审

**状态：** 阻塞发现已修复；剩余 1 项非阻塞结构性建议；全仓测试基线待 CI 确认

**日期：** 2026-07-14

**固定点：** `3bc06941d6bf187df8d4a4a13af07752d5afd91f`

**复审对象：** 固定点之后的 M2 六个功能提交及 Review Gate 修复。

**关联规格：** [2026-07-14-m2-published-runtime-agent-api-impl-spec.md](./2026-07-14-m2-published-runtime-agent-api-impl-spec.md)

---

## 1. 结论

首次 Spec/Standards 双轴复审发现 4 个功能阻塞与 2 组规范阻塞：公共 API runtime source 错误、Connector 权威能力 wiring 错误、Key 配额聚合不独立、Conversation 唯一映射冲突，以及文档/格式/docstring 缺口。

上述阻塞均已修复并补测试。M2 专项、External API/运行时兼容回归与全仓 Ruff 通过。`agent_public_api.py` 仍同时承担 HTTP、幂等、配额、结算与 SSE 编排，属于非阻塞的 Divergent Change 建议，可在进入 M3 前后拆出 application service。

由于完整仓库测试存在 M2 之前的确定性失败与本地环境失败，本轮结论为：**M2 功能可进入下一阶段评审，但 merge gate 仍需干净基线/CI 全量确认。**

---

## 2. Spec 轴发现与关闭状态

| 严重度 | 首次发现 | 修复 | 状态 |
|---|---|---|---|
| P0 | Public API 把 `agent-api:<key>` 传给只允许 `api|feishu` 的 Resolver，所有链路可能 500 | Runtime 固定传 `source="api"`；Conversation DB source 单独编码；测试断言 Resolver source | 已关闭 |
| P1 | Resolver wiring 使用原始 `ConnectorRepository`，无 `supported_capabilities`，授权恒为空 | Gateway 改用 `ConnectorServiceRepo(make_connector_service())`；Resolver 再次检查 active；wiring 测试锁定 adapter | 已关闭 |
| P1 | Key 低配额按整个 Agent 聚合，会被其他 Key 消耗 | `EffectiveQuota` 同时保留 Agent/credential limits；原子仓储同时检查两层；新增独立 Key + Agent hard cap 测试 | 已关闭 |
| P1 | 新 credential 唯一约束与旧 user/source/external-id 约束冲突 | Published Conversation 内部 source 使用 `agent-api:<credential_id>`；runtime source 保持 `api`；路由测试锁定 | 已关闭 |
| P2 | wait/SSE/cancel 测试缺失 | 新增 wait terminal answer、SSE 脱敏/end、get/cancel/跨 credential 测试 | 已关闭 |

未发现明显 scope creep。

---

## 3. Standards 轴发现与关闭状态

| 严重度 | 首次发现 | 修复 | 状态 |
|---|---|---|---|
| P1 | README、CLAUDE、API、CONFIGURATION 未同步 | 四份文档已更新，并新增 M2 实现规格/复审记录 | 已关闭 |
| P1 | 新增公共类/函数缺 docstring，SSE consumer 缺返回类型 | 新 M2 公共模型、路由、Resolver/Quota/Repository/serializer 增加契约说明；SSE 标注 `AsyncIterator[str]` | 已关闭 |
| P2 | 18 个 M2 文件未通过 Ruff format | 已格式化；全仓 679 文件 format check 通过 | 已关闭 |
| P2 | `agent_public_api.py` 职责较多（Divergent Change） | 未改变，当前保持一个明确 façade；建议后续抽 application orchestration service | 非阻塞 |

---

## 4. 验证

```text
M2 最终全功能回归：82 passed, 1 skipped
External API / runtime 兼容：153 passed, 10 deselected
ruff check --no-cache .：All checks passed
ruff format --no-cache --check .：679 files already formatted
```

完整仓库门禁的限制、首个失败和 live smoke 范围见实现规格 §11。

## 5. 修复后独立复核

原 Spec 审查轴对当前工作区只读复核后确认：首次 5 项发现全部关闭，无新阻塞。原 Standards 审查轴确认文档、公共 docstring、SSE 返回类型与 Ruff 格式均已关闭；其最后指出的 metadata repository 依赖缺类型注解也已补为 `PublishedAgentRepository`。剩余仅为 `agent_public_api.py` 职责偏多的非阻塞重构建议。
