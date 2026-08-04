# Changelog

本文件记录 DeerFlow 的重要变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] — `feature-connector-onedata`

相对 `main`（`3b5b281d`）以来的功能汇总。日期区间：2026-07-12 ~ 2026-07-30。

### Added

#### 多租户 Published Agent 发布平台（M1–M4）

- **M1 控制面**：`published_agents` / `agent_drafts`、不可变 `agent_releases` 与内容寻址 `skill_revisions`、不可变内容存储、指令合成（`AGENT.md` + `SOUL.md`）、发布校验（8 规则）、回滚（仅移动 `current_release_id`）、Legacy Agent 导入为草稿、Gateway `/api/published-agents` CRUD / 发布 / 回滚 / 归档挂起恢复
- **M2 对外运行时**：`PublishedAgentResolver` 与可信上下文、无记忆（memory-free）外部运行策略、Agent API Key（创建 / 轮换 / 吊销）、凭证作用域 Agent Public API、分层配额引擎（平台硬顶 + owner/key 收紧）与幂等预留、用量计量与双主体审计
- **M3 渠道与飞书**：Agent Channel 绑定、加密 Secret Store（库内仅存 `secret_ref`）、数据库驱动 Feishu Supervisor、会话映射持久化与事件去重
- **M4 Agent Studio**：草稿编辑、Skill / Connector 授权、发布与 Release 历史 / 回滚、API Key / 飞书绑定 / 配额 / 用量面板、Draft Sandbox、运营指标（ops metrics）、多租户验收与 E2E

#### OneData Connector

- OneData 连接器适配器与 API 能力
- 前端连接器设置页支持 OneData 配置与绑定
- Mock OneData Server（本地 / Docker 联调）
- 相关设计文档与 `CONNECTORS.md` / `.env.example` 配置说明

#### Skills

- 公共 Skill **`ppt-master`**（模板、布局、生成 / 美化 / 模板填充等工作流与参考资源）

#### 文档与运维

- `docs/PUBLISHED_AGENTS.md` 运营手册
- Alembic 迁移链（含 `2026_07_30_widen_cred_ref` 等）
- `scripts/migrate_published_agents.py` Legacy 导入 CLI

### Changed

- Skill AI 创建 / 编辑工作区体验优化；Skill 线程导入逻辑改进
- OneData：Skill 可见性按角色区分（admin / 非 admin）；`call_connector_action_tool` 使用 `action_args`；`credential_ref` 加宽以支持更长 Fernet token
- SQL 安全：注释剥离等增强
- 对话线程 rejoin / 输入框等前端小改进（配合连接器与 Agent）

### Fixed

- M1–M3 多轮 code review 修复：发布原子性、跨 owner 隔离、飞书 supervisor 正确性门禁、幂等 replay、token 限额作用域、DB-first 写序等

### Security

- 飞书凭证仅以加密 `secret_ref` 持久化，明文不得进入数据库、日志或审计响应
- 外部 Run 不可选择内部 Release / 模型 / Skill revision / Connector grant / owner 或运行时配置
- 平台配额为硬顶；owner / key 覆盖仅可收紧

---

## 升级注意

1. Published Agents 需要持久化数据库（SQLite 单节点或 PostgreSQL 生产）；`database.backend: memory` 不适用于生产发布
2. Gateway 启动会应用 Alembic 迁移；升级前请备份，并确认迁移到达当前 head
3. OneData 需配置 API Base URL 等相关环境变量（见 `.env.example` / `CONNECTORS.md`）
4. 飞书 Channel 需配置加密 secret store；切勿将明文凭证写入配置库表

### 建议验证

- [ ] Published Agent：创建草稿 → 发布 → 回滚 → 归档 / 恢复
- [ ] Agent API Key 外部 Run（配额、幂等、审计）
- [ ] 飞书 Channel 绑定与消息进出（去重、supervisor）
- [ ] OneData 连接器 / Mock Server 联调
- [ ] Agent Studio 主流程与 Skill 创建 / 编辑
- [ ] DB migration 至最新 head
