# 多租户 Agent 发布平台 - M1 第十六轮代码复审

**状态：** 已复审，功能问题已关闭，剩余 Minor 待整理
**日期：** 2026-07-14

**关联文档：**

- M1 实现规格：[2026-07-12-m1-agent-control-plane-impl-spec.md](./2026-07-12-m1-agent-control-plane-impl-spec.md)
- 第十五轮复审：[2026-07-14-m1-agent-control-plane-code-fifteenth-review.md](./2026-07-14-m1-agent-control-plane-code-fifteenth-review.md)

**复审范围：**

- 分支：`feat/m1-agent-control-plane`
- 固定点：`64c3cad69347c8d758d9397adc430b717f1be502`
- 复审对象：固定点之后的当前未提交工作区（`git diff HEAD --`；无新增 commit）
- 重点：第十五轮 3 个 Important 与 2 个 Minor 的关闭情况，以及修复 diff 的仓库规范符合性

---

## 1. 复审结论

第十五轮的 5 项问题均已实质关闭：Skill connector caps 已从最终冻结的 `SKILL.md` bytes 派生并校验同 checksum 元数据不变量；publish 与对话式 authoring 已统一为 `published_agents → agent_drafts` 锁序；Connector grant 在保存和发布时均与权威 type capabilities 求交；PATCH 嵌套请求已结构化并拒绝畸形/重复项；旧 Agent 导入已收敛为单事务 UOW。

本轮 **Spec 轴无发现**，未发现缺失需求、scope creep 或明确行为回归。Standards 轴发现 **0 个 Critical、0 个 Important、3 组 Minor**，均不影响本轮功能正确性：

1. 新增公开请求模型缺少 docstring，模型校验器缺少返回类型标注。
2. Connector capability 支持判定在保存与发布路径重复实现，存在后续规则漂移风险。
3. 修复后遗留一个无调用的单项 Skill 快照转发方法，以及 `AgentImportService` 已不再使用的 draft repository 依赖。

**结论：Ready to merge：Yes（需以 PostgreSQL CI 门禁通过为准）。** 三组 Minor 可在合并前顺手清理，也可登记为后续维护项。

---

## 2. Spec 轴

### 2.1 第十五轮问题关闭状态

| 第十五轮问题 | 本轮状态 | 复核结果 |
|---|---|---|
| Important-1：Skill 元数据与文件 bytes 可混版 | **已关闭** | 双文件树捕获与三次权威 metadata 读取 fail closed；caps 从冻结 bytes 解析；同 checksum 元数据不一致时报错 |
| Important-2：publish/authoring 行锁顺序不统一 | **已关闭** | 最终发布事务先锁 identity、再锁 draft，且 `FOR UPDATE OF` 显式限定表；新增 PostgreSQL 交叉用例 |
| Important-3：Connector capability 未与 type 能力集合校验 | **已关闭** | Connector adapter 返回不可变权威能力集合，Draft 保存与 publish 聚合校验均求交 |
| Minor-1：PATCH 嵌套请求缺少结构化校验 | **已关闭** | 嵌套模型 `extra="forbid"`，非空/长度/重复项返回 422，服务层也拒绝重复写入 |
| Minor-2：旧 Agent 导入非原子 | **已关闭** | identity、draft、Skills 在同一 session/commit 中写入，flush/commit 失败整体回滚 |

未发现实现超出 M1 §40.1 的实质 scope creep；文档、实现和新增测试对上述五项的描述一致。

---

## 3. Standards 轴

### 3.1 Minor-1：新增公开请求模型与校验器未完整遵循文档/类型规范

**相关文件：**

- [published_agents.py](../../../backend/app/gateway/routers/published_agents.py#L95)
- [backend/CONTRIBUTING.md](../../../backend/CONTRIBUTING.md)

`SkillSelectionRequest` 与 `ConnectorGrantRequest` 是本轮新增的公开请求模型，但没有类 docstring；`PatchDraftRequest.reject_duplicate_nested_entries()` 没有返回类型。仓库规范要求公开函数/类使用 docstring，并要求函数签名提供类型标注。

建议为两个模型补充简短契约说明，并将校验器标注为 `-> Self`。这是文档/静态可读性问题，不改变运行时行为。

### 3.2 Minor-2：Connector capability 支持判定存在重复逻辑

**相关文件：**

- [draft_service.py](../../../backend/packages/harness/deerflow/publishing/draft_service.py#L392)
- [validation.py](../../../backend/packages/harness/deerflow/publishing/validation.py#L129)

`supported_capabilities` 的类型检查与 membership 判断在 Draft 保存路径和 publish 校验路径重复，并在 `validation.py` 内再次展开。保存与发布的双重校验是规格要求，不能删除；但底层纯判定可复用一个无副作用 helper，使两条业务边界继续独立调用同一规则，避免将来一侧接受新容器类型或规范化方式而另一侧遗漏。

该项属于 **Duplicated Code** 判断项，不是硬性违规。

### 3.3 Minor-3：原子导入/批量快照重构后遗留无效抽象

**相关文件：**

- [skills_index.py](../../../backend/packages/harness/deerflow/publishing/skills_index.py#L256)
- [import_service.py](../../../backend/packages/harness/deerflow/publishing/import_service.py#L67)

`StorageSkillsIndex._resolve_publish_snapshot()` 只把单个名称包装成列表后转发到 `resolve_publish_snapshots()`，仓库内已无调用，属于 **Middle Man**。`AgentImportService` 改用 `PublishedAgentRepository.import_authoring_bundle()` 后，构造器中的 `draft_repo` 与 `self._drafts` 也不再参与生产逻辑，只被测试用于窥探内部状态，属于 **Speculative Generality**。

建议删除无调用转发方法，并从 `AgentImportService`、factory、CLI 与测试替身中移除未使用的 draft repository 依赖；测试通过 repository/返回结果验证草稿状态。

---

## 4. 验证记录

### 4.1 专项测试

本轮直接受影响的 8 个测试文件：

```text
146 passed, 1 warning in 21.88s
```

PostgreSQL 并发门禁文件：

```text
3 skipped, 1 warning in 1.22s
```

3 条跳过均因本机未配置 `TEST_POSTGRES_URL`；CI 必须继续以 `REQUIRE_POSTGRES_TESTS=1` 强制执行，合并判断以该门禁结果为准。

### 4.2 静态检查

```text
ruff check --no-cache <17 个本轮相关 Python 文件>
All checks passed!

ruff format --no-cache --check <17 个本轮相关 Python 文件>
17 files already formatted

git diff --check HEAD
通过
```

本轮未重新执行全 backend 测试，因此实现规格 §40.2 记录的 `499 passed, 5 skipped, 2 deselected` 未在本次复审中独立复现。

---

## 5. 最终结论

第十五轮三个 Important 与两个 Minor 均已关闭，当前未发现阻塞 M1 合并的 Spec 问题。剩余三组问题均为文档、类型和重构残留层面的 Minor，不影响 Release 正确性、租户隔离、原子性或 Connector 最小权限边界。

**Ready to merge：Yes（需以 PostgreSQL CI 门禁通过为准）。**
