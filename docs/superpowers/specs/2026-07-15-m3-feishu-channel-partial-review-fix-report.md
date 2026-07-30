# 多租户 Agent 发布平台 — M3 部分实现复审修复报告

**状态：** 本轮代码复审中的 4 项 Spec P1、2 项 Standards P1 和 1 项 Standards P2 已完成代码侧修复；本地聚焦门禁通过。真实双 Feishu App smoke 与 PostgreSQL 并发门禁仍需部署环境确认，因此不以本文替代最终 M3 Review Gate。

**日期：** 2026-07-15

**关联文档：**

- [M3 部分实现代码复审](./2026-07-15-m3-feishu-channel-partial-code-review.md)
- [多租户 Agent 发布平台开发计划](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md)
- [多租户 Agent 发布平台设计](./2026-07-12-multi-tenant-agent-publishing-design.md)

**修复范围：** M3-S1–M3-S4、M3-T1–M3-T3，以及复审第 5 节中可在本地自动化验证的回归项。

---

## 1. 结果概述

动态 Feishu 绑定现在形成独立于 legacy `config.yaml` Channel 的生产链路：

```text
authenticated WebSocket
  → event token / timestamp verification
  → durable (binding_id, event_id) claim
  → MessageBus
  → binding/Agent-validated DB conversation mapping
  → PublishedAgentResolver(source="feishu")
  → quota reserve
  → memory-free Gateway Published Run
  → terminal usage settlement
  → safe outbound response
```

动态绑定不会读取 legacy JSON mapping，不会选择默认 Agent，也不会让 `/memory`、`/models`、`/new` 等文本进入 legacy 命令路径。相同绑定事件的并发重放在 quota reserve 之前被数据库唯一约束拦截；成功、失败、取消和超时均沿用 Published Run 的安全终态与用量模型。

---

## 2. Finding 关闭记录

| Finding | 修复结果 | 主要落点 | 回归证据 |
|---|---|---|---|
| M3-S1 生产消息未接 Published 链路 | 增加 binding-aware dispatcher、DB mapping、Resolver、quota、Gateway Run executor 与 usage settlement；绑定命令同样禁止回落 legacy | `app/channels/published_runtime.py`、`manager.py`、`service.py`、`gateway/app.py` | `test_feishu_published_run_flow.py` 覆盖完整顺序、目标 Agent、DB thread、重复事件单 Run/usage、命令隔离、不可用/超限/超时和真实 Gateway Run seam |
| M3-S2 动态绑定无 token/签名材料 | SecretStore 改存版本化完整凭据包：`app_secret + verification_token + encrypt_key`；Supervisor 解密后传入生产 Channel；应用入口比较 token 并拒绝过期事件 | `publishing/feishu_credentials.py`、`published_agent_channels.py`、`supervisor.py`、`feishu.py` | `test_agent_channels_router.py`、`test_feishu_event_dedup.py` |
| M3-S3 stop/restart 未终止 SDK WS | 增加可终止 `_LarkWebSocketSession`，持有 event loop/client/session 句柄，显式 disconnect，等待 session 与 worker thread 确认退出后才移除 registry 或创建替代实例 | `app/channels/feishu.py`、`supervisor.py` | `test_feishu_websocket_lifecycle.py`、`test_feishu_supervisor.py` |
| M3-S4 未 ready 即 healthy、晚到错误丢失 | `start()` 等待 ready/error 握手；SDK receive task 显式观察 late failure；runtime callback 按 binding 持久化 unhealthy；generation token 忽略旧实例的迟到错误 | `app/channels/feishu.py`、`supervisor.py` | ready 前不返回、同步失败、ready 后失败、peer 隔离、stale generation 测试 |
| M3-T1 mapping 无 owner 硬约束 | trusted ingress 必须传不可伪造 system-scope sentinel，并在写入前验证 binding → Agent；owner list 强制 `binding_id + owner_user_id` join，不再提供全局枚举 | `persistence/channel_mapping/sql.py`、`app/channels/store.py` | `test_channel_mapping_store.py` 覆盖 forged scope、跨 owner list、跨 Agent 冲突及并发 get-or-create |
| M3-T2 README/CLAUDE 未同步 | 增加 key 生成、secret-store 路径、Owner API、生命周期、消息顺序、legacy 兼容、迁移、健康诊断和测试命令；补充 `.env.example` | `backend/README.md`、`backend/CLAUDE.md`、`.env.example` | 文档与当前生产接线一致 |
| M3-T3 docstring/type 不完整 | 补齐 repository、Supervisor lifecycle、route handler、Feishu ingress 和 Published runtime 的 docstring/具体类型；移除 review 指出的隐式 inbound `Any` | 相关 Python 模块 | Ruff、format、compileall 全部通过 |

### 2.1 WebSocket 校验机制说明

Feishu 长连接通过 `app_id/app_secret` 建立已认证 WebSocket。当前 `lark-oapi` 的长连接实现会使用 `EventDispatcherHandler.do_without_validation()`，WebSocket 帧没有 HTTP callback 的 timestamp/nonce/signature headers；因此动态绑定不能声称执行了 HTTP 回调签名校验。生产入口现在显式校验事件 header 中的 `verification_token`、稳定 `event_id` 和 5 分钟时间窗，并在 durable dedup、quota 与 Run 之前失败关闭。`encrypt_key` 仍作为完整 Feishu 配置材料加密保存并传入 SDK dispatcher。

### 2.2 生命周期不变量

- healthy 只表示该实例完成了 WebSocket ready 握手。
- stop/delete 只有在 SDK session 和 worker thread 确认退出后才持久化 inactive 或删除绑定。
- restart/凭据轮换先停止旧实例，再创建新实例；不存在两个活连接的切换窗口。
- late connection failure 只移除并标记对应 binding；其他 binding 不受影响。
- 每次启动拥有独立 generation token，旧实例迟到的 callback 不能污染替代实例。
- 缺少 durable event repository 的动态绑定拒绝启动。

### 2.3 owner/system scope 不变量

- Owner 管理 API 的每次读写都通过 `published_agents.owner_user_id` 限定。
- Supervisor 跨 owner 扫描只能使用 `SYSTEM_CHANNEL_SUPERVISOR_SCOPE`。
- Inbound mapping 只能使用 `SYSTEM_CHANNEL_MAPPING_SCOPE`，且 repository 仍查询 `agent_channels` 验证稳定 binding → Agent 关系。
- Mapping owner list 必须同时提供 `binding_id` 与 `owner_user_id`；跨 owner 返回空列表，不暴露内部 thread id。

---

## 3. 部署与兼容结果

- 数据库仅保存 opaque `secret_ref`；完整凭据 JSON 在 `${DEER_FLOW_HOME:-.deer-flow}/secret-store/feishu/` 中使用 Fernet 加密。
- 必需部署变量为 `DEER_FLOW_SECRET_STORE_KEY`。缺失或无效时，只禁用数据库 Feishu Supervisor；Published-Agent 控制面/运行时与 legacy `config.yaml` Channel 继续可用。
- Gateway 启动时自动应用 `2026_07_14_agent_channels → 2026_07_14_channel_mappings`。
- legacy Slack/Telegram/DingTalk/Feishu 继续使用原有 config 与 JSON mapping；只有携带 Supervisor 可信 binding metadata 的消息进入 Published 分支。
- 第一版仍按开发计划采用单实例 Supervisor；多副本 binding lease 不在本轮范围内。

---

## 4. 本地验证记录

### 4.1 M3 聚焦 + legacy IM 回归

```text
156 passed, 5 warnings
```

覆盖：

```text
test_secret_store.py
test_agent_channel_repo.py
test_agent_channels_router.py
test_feishu_supervisor.py
test_channel_mapping_store.py
test_feishu_event_dedup.py
test_feishu_websocket_lifecycle.py
test_feishu_published_run_flow.py
test_channels.py
test_feishu_parser.py
test_harness_boundary.py
test_published_agents_app_wiring.py
```

5 条 warning 均来自现有第三方依赖的 deprecation/pending-deprecation 提示，不是测试失败。

### 4.2 静态与迁移检查

```text
ruff check --no-cache（19 个相关 Python 文件）: All checks passed
ruff format --check --no-cache: 19 files already formatted
python -m compileall: passed
Alembic heads: 2026_07_14_channel_mappings (head)
git diff --check: passed
```

---

## 5. 仍需部署环境完成的门禁

以下两项无法由本地 fake/SQLite 回归替代，仍应在最终 M3 Review Gate 前执行：

1. **真实 Feishu smoke：** 两个 Published Agent 各绑定一个真实 Feishu App，执行 add/test/start、私聊/群聊/话题消息、停用其一验证另一个存活、凭据轮换、restart、stop，并确认旧连接不再收事件。
2. **PostgreSQL 并发门禁：** 对同一 p2p/group/topic mapping 和同一 `(binding_id, event_id)` 做跨连接并发竞争，确认稳定单 thread、单 Run、单 usage；相同 event id 的不同 binding 相互独立。

完成上述部署验证并通过后，才可把本报告的“代码侧关闭”升级为最终 M3 Review Gate 通过。
