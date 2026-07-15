# 多租户 Agent 发布平台 — M3 部分实现代码复审

**状态：** Review Gate 未通过；当前 F3.1–F3.3 部分实现存在 4 项 Spec P1、2 项 Standards P1 和 1 项 Standards P2

**日期：** 2026-07-15

**固定点：** `2be6bdd6357ecd316edb344d66777ca06ef2a383`

**复审 HEAD：** `54e2fcd747aec84cfacbcee79b943e67afaccf6e`

**复审范围：** `git diff 2be6bdd6...HEAD`，即以下 3 个 M3 提交：

- `fb310f98` — Agent Channel 与加密 SecretStore
- `61426198` — 数据库驱动的 Feishu Supervisor
- `54e2fcd7` — 持久化会话映射与事件去重

**规格来源：**

- [多租户 Agent 发布平台开发计划](../plans/2026-07-12-multi-tenant-agent-publishing-dev-plan.md) F3.1–F3.3
- [多租户 Agent 发布平台设计](./2026-07-12-multi-tenant-agent-publishing-design.md) §6.2–§6.3、§7.6、§7.8–§7.9、§8.4、§10、§13、§18.5、§19–§20

用户说明本次只实现 M3 的部分功能，因此本复审不把尚未开始的整段 F3.4 当作缺陷；但已经声明实现的 F3.1–F3.3 必须满足各自的完整契约。

---

## 1. 结论

SecretStore、Agent Channel CRUD、Supervisor 基本骨架、事件去重表和会话映射仓储已经具备，聚焦测试与旧 Channel 回归也全部通过。不过，生产消息链路尚未把这些组件组合起来：动态 Feishu 事件进入 MessageBus 后仍由旧 `ChannelManager` 使用 JSON 映射并运行默认 Agent，新增 DB 映射没有生产消费者，也没有接入 Published-Agent Resolver 和 quota。与此同时，生产动态绑定没有实际校验 token/签名，`stop`/`restart` 不能终止真实 WebSocket 客户端，Supervisor 又会在连接真正建立前记录 healthy。

因此，当前测试通过只能证明组件级行为，不能证明 F3.3 的 `verify → dedup → binding → mapping → resolver → reserve → run` 主链路成立，也不能证明 F3.2 的动态生命周期成立。M3 Review Gate 当前应保持阻塞。

---

## 2. Spec 轴

### [P1] M3-S1：绑定消息没有进入 DB 映射和 Published-Agent 执行链路

**证据：**

- `backend/app/gateway/deps.py:183` 仅把 `DbMappingStore` 放入 `app.state.channel_mapping_store`；生产代码没有读取该 state。
- `backend/app/channels/service.py:62-77` 仍固定用旧 `ChannelStore()` 构造 `ChannelManager`。
- `backend/app/channels/manager.py:756-810` 仍调用 JSON store 的 `get_thread_id()` / `set_thread_id()`，并通过旧路径创建 thread。
- `backend/app/channels/manager.py:612-658` 不读取 inbound metadata 中的 `binding_id` / `agent_id`，因此动态绑定仍解析为默认 assistant/session。
- `backend/app/channels/store.py:178-211` 的 `DbMappingStore` 接口也没有被 Manager 或其他生产分发器调用。

**对应规格：** 开发计划 F3.3 的持久化映射、并发一致性及固定执行顺序；设计 §7.8、§10。

**影响：**

- 多进程/多副本仍以进程本地 JSON 为会话事实来源，DB 唯一约束没有实际保护生产流量。
- 私聊 `(binding, chat, user)` 与群聊 `(binding, chat, topic)` 的隔离规则没有实际生效。
- 绑定到指定 Published Agent 的机器人可能运行默认 Agent。
- 事件没有经过 F2.1 Resolver、F2.5 quota reserve 和 Published runtime policy。
- 现有去重测试只证明同一事件进入 bus 一次，没有锁定“dedup 必须先于 reserve”的规范顺序。

**修复要求：** 增加 binding-aware 的生产分发分支，将 DB mapping、稳定 Agent 解析、Published-Agent Resolver、quota reserve 与 Run 串成一条受测链路；不得仅把仓储挂到 `app.state`。端到端测试必须同时断言目标 Agent、DB thread 映射、调用顺序、重复事件只产生一个 Run/一次计费。

### [P1] M3-S2：生产动态绑定没有实际启用 token/签名校验

**证据：**

- `backend/app/channels/feishu.py:128` 对动态绑定默认构造 `FeishuEventVerifier()`，默认 verification token 为空。
- `backend/app/channels/feishu.py:43-49` 在 token 为空时完全跳过 token 比对，只检查 event id 与时间戳。
- `backend/app/channels/supervisor.py:54-72,165-173` 只向 `FeishuChannel` 传递 `app_id`、`app_secret`、`binding_id` 和 `agent_id`，没有传递 verification token、encrypt key 或等价签名材料。
- `backend/app/channels/feishu.py:244-250` 因而使用两个空值构建 SDK dispatcher。
- `backend/tests/test_feishu_event_dedup.py:85-100` 的“无效签名”测试只是注入恒定返回 `False` 的 lambda，没有覆盖生产 verifier 和 Supervisor 接线。

**对应规格：** 开发计划 F3.3“事件签名校验、重放时间戳拒绝”；设计 §6.3、§10 第 2 步、§18.5。

**影响：** 当前应用层只能拒绝缺失 event id 或过期时间戳，不能证明伪造/篡改事件会在 dedup、quota 和 Run 之前被拒绝。

**修复要求：** 明确动态 WebSocket 绑定使用的 Feishu 验签机制，把所需校验材料纳入加密 SecretStore 载荷并安全传入生产 channel；增加通过真实生产构造路径执行的合法、token/签名篡改、过期重放测试，不能以注入恒假 verifier 代替验签测试。

### [P1] M3-S3：`stop` / `restart` 没有停止真实 Feishu WebSocket 客户端

**证据：**

- `backend/app/channels/feishu.py:252-259` 把 `ws_client` 保存在 `_run_ws()` 的局部变量中，并阻塞于 SDK `start()`。
- `backend/app/channels/feishu.py:264-275` 的 `stop()` 只修改 `_running`、取消 DeerFlow 本地 task 并执行 `thread.join(timeout=5)`；它没有保存或关闭 SDK connection/event loop。
- join 超时后，代码直接清空 `_thread` 引用并记录 stopped。
- `backend/app/channels/supervisor.py:231-274` 随后会把绑定标记为 inactive，或在 restart 时创建新实例。

**对应规格：** 开发计划 F3.2 的 stop/restart/delete API 契约、Supervisor 隔离生命周期与动态启停验收；设计 §6.3、§18.5。

**影响：** 停用或删除后的旧连接仍可能继续接收事件；restart/凭据轮换可能同时留下旧、新连接，造成重复消费、错误凭据继续生效和绑定隔离失效。

**修复要求：** 持有可终止的 SDK client/connection/event-loop 句柄，显式关闭连接并等待线程确认退出；只有确认停止后才从 registry 移除、更新 inactive 或启动替代实例。用阻塞式 fake 或真实 SDK seam 断言 stop 后线程/连接已经结束，restart 不存在双连接窗口。

### [P1] M3-S4：Supervisor 在 WebSocket 尚未连接时就记录 healthy

**证据：**

- `backend/app/channels/feishu.py:201-218` 在启动后台线程前设置 `_running=True`，随后立即返回。
- `backend/app/channels/supervisor.py:173-188` 只检查该布尔值就持久化 `health=healthy`。
- 后续 WebSocket 鉴权、网络连接或线程退出异常只在 `_run_ws()` 中记日志，没有回传 Supervisor 或更新数据库健康态。
- `backend/tests/test_feishu_supervisor.py:104-118` 使用在 `start()` 内同步抛错的 fake，未覆盖真实 channel 的异步连接失败。

**对应规格：** 开发计划 F3.2“启动失败标记 unhealthy 且不影响其他 binding”及 Supervisor health 验收；设计 §6.3、§8.4、§18.5。

**影响：** 错误凭据、连接失败或线程立即退出时，start API 和数据库仍显示 healthy/running；运维和 owner API 会收到错误状态。

**修复要求：** 为 channel start 增加 ready/error 握手或 future，在真实连接成功后才记录 healthy；线程退出或重连失败必须回调 Supervisor，按 binding 更新 unhealthy，且不得影响其他 binding。补充“后台线程在 start 返回后失败”的回归测试。

---

## 3. Standards 轴

### [P1] M3-T1：Channel Mapping 仓储不满足 owner-scope 硬约束

`backend/packages/harness/deerflow/persistence/channel_mapping/sql.py:55-129` 的 `get_or_create_thread()` 接受任意 `binding_id` / `agent_id`，既没有 `owner_user_id`，也不校验 binding → Agent → owner 关系；首次写入完全信任调用方。`list_mappings(binding_id=None)` 还可以枚举所有租户的 mapping 和内部 thread id。

这违反 `backend/CLAUDE.md:267` 的“所有 repository 方法必须 tenant/owner scoped”不变量。错误的内部调用方可以污染其他 binding 的映射，使合法流量后续触发 `MappingScopeConflictError`；导出的全局 list 入口也泄露跨 owner 会话元数据。

**修复要求：** owner 管理路径必须 join `agent_channels` / `published_agents` 验证 owner；可信 inbound 系统路径必须使用与 Supervisor 类似的不可伪造显式 system scope，并在仓储层校验 binding 与 agent 的稳定关系。移除无 scope 的全局 list 路径。

### [P1] M3-T2：新架构和必需配置没有同步到 README / CLAUDE

M3 新增了 owner API、迁移、DB mapping、Supervisor 和必需的 `DEER_FLOW_SECRET_STORE_KEY`，但本轮 diff 没有修改 `backend/README.md` 或 `backend/CLAUDE.md`。当前 `backend/CLAUDE.md:493-525` 仍只描述 config.yaml + JSON mapping 的 legacy channel；`backend/README.md:183-187` 也只描述旧 Feishu 流程。

这违反 `backend/CLAUDE.md:70-76` 的关键文档同步规则，也违反开发计划 M3 Review Gate“更新 backend/CLAUDE.md 的 IM Channels 说明”。当前缺少 key 生成/配置、secret-store 位置、数据库生命周期、owner routes、legacy 兼容、迁移和故障诊断说明；而缺少 key 时 `backend/app/gateway/app.py:269-295` 会仅以 warning 静默禁用 DB Supervisor。

**修复要求：** 修复实现时同步 README 与 CLAUDE，写清部署配置、API、生命周期、兼容路径、迁移、健康诊断和聚焦测试命令。

### [P2] M3-T3：新增公共操作缺少完整 docstring / 类型注解

`backend/app/channels/feishu.py:666`、`backend/app/channels/supervisor.py:114-310`、`backend/app/gateway/routers/published_agent_channels.py:133-266`、`backend/packages/harness/deerflow/persistence/agent_channel/sql.py:45-225` 和 `backend/packages/harness/deerflow/persistence/channel_mapping/sql.py:55-176` 的多项公共方法/路由没有 docstring；`_prepare_inbound(..., inbound, ...)` 与 `_owned_query(...)` 等签名缺少完整参数/返回类型。

这违反 `backend/CONTRIBUTING.md:144-151` 的类型注解和公共 API 文档要求。

**修复要求：** 为公共 repository、Supervisor lifecycle 和 route handler 补充 docstring，明确 owner/system scope、状态副作用、SecretStore 约束和并发语义；补齐具体类型，不使用无边界的隐式 `Any`。

本轮没有发现需要单列的 Fowler smell blocker；Ruff 已覆盖的格式问题未重复列为 finding。

---

## 4. 验证记录

```text
M3 focused + legacy IM Channel regression: 139 passed
ruff check --no-cache（24 个 M3 Python 文件）: All checks passed
ruff format --check --no-cache（24 个 M3 Python 文件）: 24 files already formatted
git diff --check 2be6bdd6...HEAD: passed
Alembic heads: 2026_07_14_channel_mappings (head)
Alembic fresh in-memory SQLite upgrade-to-head: passed
```

第一次在 Windows sandbox 内运行 pytest 时，28 个依赖 `tmp_path` 的用例因系统 Temp ACL 在 setup 阶段报 `PermissionError`，其余 111 个用例通过。使用获批的工作区专用 `--basetemp` 重跑后为 `139 passed`，因此这些 setup error 不计为代码失败；测试临时目录已清理。

本轮没有执行真实 Feishu app 的 live WebSocket smoke，也没有执行 PostgreSQL 并发门禁。两项仍属于 M3 Review Gate 的部署验证要求，且不能替代上述生产链路修复。

---

## 5. 关闭 Review Gate 所需的最小回归

1. 生产构造路径的合法签名、篡改签名/token、过期时间戳测试；失败事件不得触发 dedup claim、quota 或 Run。
2. 完整顺序测试：`verify → dedup → binding → DB mapping → PublishedAgentResolver → quota reserve → Run`。
3. 同一 `(binding_id, event_id)` 并发重放只产生一个 Run 和一条 usage；不同 binding 的相同 event id 互不影响。
4. 私聊 `(binding, chat, user)`、群聊 `(binding, chat, topic)` 在 SQLite 与 PostgreSQL 下并发 get-or-create 都只得到一个稳定 thread，且不跨 owner/Agent。
5. 两个 binding 并行运行时，stop/delete 一个不会影响另一个；被停止连接确认退出，不再接收事件。
6. restart/凭据轮换只保留一个新实例，旧实例连接已关闭；异步连接失败会把该 binding 标记 unhealthy。
7. 动态 binding 的 inbound 必须运行其稳定绑定的 Published Agent，并受 Release Resolver、runtime policy 和 quota 约束；不得回落到默认 Agent。
8. legacy `config.yaml` Channel 回归继续全绿；DB Supervisor 缺 key/单 binding 失败不得影响 legacy Channel 和 Agent 发布主流程。
9. README、CLAUDE 和部署示例完成同步，并执行一次真实 Feishu app add/test/start/message/stop smoke。

---

## 6. Review Gate 判定

在 M3-S1 至 M3-S4、M3-T1 至 M3-T2 关闭，并补齐第 5 节对应回归之前，不应把组件级 `139 passed` 解释为 F3.1–F3.3 已完成，也不应进入 M3 Review Gate 通过状态。
