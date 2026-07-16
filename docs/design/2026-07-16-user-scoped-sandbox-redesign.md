# 用户级沙箱生命周期重构设计

**日期**：2026-07-16
**范围**：`backend/packages/harness/deerflow/sandbox/`、`backend/packages/harness/deerflow/community/aio_sandbox/`、`backend/packages/harness/deerflow/config/`、`backend/app/gateway/routers/threads.py`
**目标**：消除"新会话首次对话"的沙箱冷启动开销，同时简化沙箱生命周期模型，为 M1 多租户对齐隔离边界。
**状态**：设计待评审（未实施）

---

## 1. 背景与问题

当前沙箱模型为 **1 thread : 1 sandbox**：

- `AioSandboxProvider._sandbox_id_for_thread()`（`aio_sandbox_provider.py`）从 `thread_id` 确定性派生 `sandbox_id`，容器名随之确定为 `{container_prefix}-{sandbox_id}`。
- 暖池 `_warm_pool` 按 `sandbox_id` 索引，`_reclaim_warm_pool_sandbox()` 只命中**同一 thread** 释放的容器。
- 本地容器按 thread 挂载 4 个目录（`_get_thread_mounts()`）：`workspace` / `uploads` / `outputs` / `acp-workspace`，容器创建后挂载不可变更。

由此产生结构性后果：

1. **每个新会话的首次工具调用必然冷启动**：新 thread 的 `sandbox_id` 永远不可能在暖池中，必走 `_create_sandbox` → 容器创建 → `wait_for_sandbox_ready`（上限 60s，实测通常数秒至十几秒，Windows Docker Desktop 更慢）。
2. **同一用户的会话间无法共享执行环境**：用户开 5 个会话要起 5 个容器，`replicas`（默认 3）软上限与 LRU 驱逐在多会话场景下互相打架。
3. **供应发生在关键路径上**：虽然 `SandboxMiddleware` 默认 `lazy_init=True`（首个工具调用才获取），但获取本身是同步等待就绪的，用户在首条消息的流式输出中途被卡住。

agent 图编译层面已有缓存（`efe35282` 图缓存、`1827bb33` 模型客户端/prompt 缓存 + 启动预热），不是瓶颈；瓶颈集中在沙箱供应。

## 2. 根因分析

**隔离边界选错了层级。** thread 是对话，不是安全边界；同一用户的多个会话之间不需要容器级隔离。把隔离单元绑定在 thread 上，导致：

- 新会话 = 新隔离单元 = 无法复用任何已热资源；
- 容器数随会话数线性膨胀，生命周期管理（暖池、驱逐、孤儿回收）被迫复杂化。

正确的隔离边界是**用户（租户）**。这也与 M1 多租户控制面方向一致：租户配额、清理、审计都落在 user 维度。

## 3. 设计目标

| 目标 | 指标 |
|---|---|
| 新会话首次工具调用延迟 | P95 < 1.5s（预热命中时趋近 0） |
| 同一用户第 2..N 个会话的沙箱供应成本 | 0（共享已热容器） |
| 隔离性 | 用户间容器级隔离不削弱；用户内 thread 间由工具层 cwd 防护 |
| 兼容性 | 保留 `/mnt/user-data/{workspace,uploads,outputs}` 的 agent 可见契约（#2881）；旧布局数据可迁移 |
| 可回退 | `sandbox.scope` 配置开关，thread/user 双模式并存 |

非目标：不改动 AIO 镜像内部服务；不改动 LocalSandboxProvider 的语义；不引入匿名容器池（见 §6.4 论证）。

## 4. 总体设计

### 4.1 核心变更：`sandbox_id` 从 user_id 派生

| 维度 | 现状（scope=thread） | 新设计（scope=user） |
|---|---|---|
| `sandbox_id` 派生 | `_deterministic_sandbox_id(thread_id)` | `_deterministic_sandbox_id(user_id)` |
| 隔离单元 | 每会话一个容器 | 每用户一个容器 |
| 容器挂载 | 4 个 thread 级 bind-mount | 1 个用户级 bind-mount（见 §4.2） |
| 暖池/驱逐索引 | `_warm_pool[sandbox_id]` | 按 `user_id` 索引 |
| `replicas` 语义 | 最大并发容器数（≈活跃会话数） | 最大并发**用户**沙箱数 |
| 新会话供应成本 | 冷启动（必现） | 0（共享用户容器） |

### 4.2 挂载泛化：用户根目录一次挂载

宿主机布局**已支持**用户级嵌套（`paths.py:175` `thread_dir()`）：

```
{base}/users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}
{base}/users/{user_id}/threads/{thread_id}/acp-workspace/
```

新设计挂载用户根：

```
{host_base}/users/{user_id}  →  /mnt/users/{user_id}   (rw)
```

容器内 thread 真实路径：

```
/mnt/users/{user_id}/threads/{thread_id}/user-data/workspace
```

### 4.3 保留 agent 可见虚拟契约

agent/技能/提示词继续只认 `/mnt/user-data/{workspace,uploads,outputs}`（#2881 契约不变）。改写发生在**路径重写层**：

- `sandbox.scope=thread`：映射如现状 → 容器内 `/mnt/user-data/...`（thread 容器直挂）。
- `sandbox.scope=user`：映射按**当前活跃 thread** 解析 → `/mnt/users/{uid}/threads/{tid}/user-data/...`。

实现位置：沙箱路径重写（`sandbox/tools.py`、gateway `path_utils.py`）增加"当前 thread 上下文"入参；`SandboxMiddleware` 在 run 开始时把 `thread_id`/`user_id` 注入 middleware state（现有机制），工具调用时按 state 解析。对 agent 透明。

### 4.4 供应移出关键路径：按用户预测性预热

不引入匿名容器池（bind-mount 在容器创建时确定，"未绑定容器"无法后挂用户目录，见 §6.4）。改为**预测性预热**：

1. **thread 创建触发**：`POST /api/threads`（`backend/app/gateway/routers/threads.py:355` `create_thread`）在响应返回后 fire-and-forget 调用 `provider.acquire_async(user_id)`。用户看界面、打字的几秒内容器并行启动；首条消息触发工具时已就绪。
2. **网关启动触发（可选）**：对近期活跃用户（如 24h 内有 run）按 LRU 预热至 `prewarm_users` 上限。
3. **run 内兜底**：工具调用时若容器未就绪，沿用现有 `acquire_async` 等待逻辑（此时通常只需等零点几秒的就绪尾部）。

所有预热调用必须：不阻塞 HTTP 响应；失败仅记日志不影响会话创建；带去重（同一 user 并发预热合并为一次，复用现有 thread lock 机制改 user lock）。

### 4.5 工具层 cwd 防护（替代 mount 级隔离）

用户级共享容器后，thread 间隔离从 mount 层下沉到工具层：

- bash/文件工具的默认工作目录强制为当前 thread 的 `workspace`；
- 路径解析拒绝逃逸当前 thread 根（沿用并加强现有 `sandbox/security.py` 的路径校验，覆盖 `..`、绝对路径、符号链接）；
- **有意放行**：当前 thread 的 `uploads`/`outputs` 与用户级只读共享区（见 §5.3 跨会话文件引用）。

### 4.6 就绪探活与镜像侧说明

现有探活已只针对沙箱 API（`backend.py` 轮询 `/v1/sandbox`），并非等待 AIO 全部服务，无需分级改造。进一步压缩冷启动需镜像侧优化（精简常驻服务、延迟启动重服务），属于上游 AIO 镜像范畴，本文档不覆盖，仅记录为后续可选方向。

## 5. 详细设计

### 5.1 配置（`config/sandbox_config.py`）

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  scope: user                    # 新增：thread(默认,兼容) | user
  prewarm:
    enabled: true                # 新增：thread 创建时异步预热
    on_startup: false            # 新增：网关启动时预热近期活跃用户
    startup_users: 5             # 新增：启动预热用户数上限
  # 以下既有语义调整：replicas 在 scope=user 下表示最大并发用户沙箱数
```

- `SandboxConfig` 新增 `scope: Literal["thread", "user"] = "thread"`、`prewarm: PrewarmConfig`。
- `scope` 变更属于**运行时边界配置**，纳入 reload boundary 清单（重启生效，不做热切换）。

### 5.2 Provider 改造（`aio_sandbox_provider.py`）

| 改造点 | 说明 |
|---|---|
| `_sandbox_id_for_user()` | 新增；`scope=user` 时 `acquire*(user_id=...)` 走用户派生 |
| 锁粒度 | `_get_thread_lock()` → `_get_principal_lock(key)`，key 为 `user:{uid}` 或 `thread:{tid}` |
| 文件锁 | 锁文件从 thread 目录移到用户目录：`users/{uid}/sandbox-{sandbox_id}.lock` |
| 挂载 | `scope=user` 时 `_get_user_mounts(user_id)` 替换 `_get_thread_mounts()`：单个用户根 mount + 既有 skills mount |
| 暖池/驱逐 | `_user_sandboxes` / `_user_warm_pool` 按 user 索引；LRU 驱逐按用户最后活跃时间 |
| 孤儿回收 | 启动 reconcile 逻辑改为按用户沙箱识别（容器名前缀 + user 派生 id 不变，逻辑平移） |
| 释放语义 | thread 结束/删除不触发容器释放；容器按用户 idle_timeout 释放 |

`scope=thread` 路径保持现状，两套逻辑在 provider 内按配置分支，共享 backend/就绪/锁设施。

### 5.3 跨会话文件引用（顺带收益）

用户级容器天然支持"把上个会话的文件拿过来继续用"：

- 文件工具增加用户级只读视图：`/mnt/users/{uid}/threads/` 下其他 thread 的 `outputs` 对当前 run 只读可见；
- agent 需要引用时显式复制到当前 thread 的 `workspace`（避免隐式跨写）；
- 该能力在 `scope=user` 下默认开启，可配置关闭。

### 5.4 ACP workspace

现状为 thread 级只读 mount（`/mnt/acp-workspace`）。`scope=user` 下容器内路径变为 `/mnt/users/{uid}/threads/{tid}/acp-workspace`；只读约束从 mount 级降为工具层（ACP 结果由 lead agent 只读访问，写入方在宿主机侧，风险可控）。文档中注明该降级。

### 5.5 数据迁移

- `scope=user` 要求 thread 目录位于 `users/{uid}/threads/` 下；旧布局 `{base}/threads/{tid}`（无用户维度）需一次性迁移：
  - 提供 `scripts/migrate_thread_dirs_to_user_scope.py`：按 thread 归属（thread_store 记录）把旧目录移动到对应用户下；无归属记录的无认证部署归入 `users/default/`。
  - 迁移前 `scope=user` 启动时对缺失目录 fail-loud 并提示运行迁移脚本。
- 虚拟契约不变，agent 侧无感知；宿主机侧引用旧路径的脚本需同步更新。

### 5.6 观测

- 计时埋点：`provision_total`（容器创建）、`ready_wait`（探活等待）、`acquire_total`（获取全链路）、`prewarm_hit`（工具调用时容器已就绪的比例）。
- 日志：acquire 路径输出 `scope / user_id / sandbox_id / 来源(prewarm|run|warm) / 各阶段耗时`。
- Langfuse：run trace 增加 `sandbox.provision` span。
- 验收仪表盘指标：新会话首次工具调用 P95、预热命中率、暖池命中率、容器存活数。

## 6. 风险与权衡

| 风险/权衡 | 评估与对策 |
|---|---|
| 用户内跨 thread 文件可见性扩大 | 同用户本就可看自己的全部文件（gateway 层无隔离），定位为对齐而非削弱；agent 写越界由 §4.5 cwd 防护兜底，配专门测试 |
| ACP workspace 只读约束降级 | 写入方在宿主机，容器内仅 lead agent 读取；工具层校验 + 文档注明 |
| 容器存活时间变长（按用户 idle） | 内存占用上升；`idle_timeout`（默认 600s）继续生效，多租户部署按租户配额收紧 |
| 同用户并发 run 共享容器 → 资源争抢 | 单用户并发会话数是小的；争抢优于冷启动。必要时后续加 per-user 槽位（user+slot） |
| 与上游改动冲突面 | `aio_sandbox_provider.py` 是大改文件，与上游 #3494/#3518/#3464 邻近。顺序：先合上游这些 PR，再做本设计 P1 |
| 旧布局迁移失败 | 迁移脚本幂等 + dry-run 模式；启动 fail-loud 不静默兜底 |
| Windows 开发机 | 预热收益更大；LocalSandbox 开发流不受影响 |

### 6.4 附：为什么不引入匿名容器池

匿名池（预热 N 个未绑定容器，acquire 时取出绑定）要求容器创建后再追加用户目录挂载，而 bind-mount 只能在容器创建时指定；重建容器等于回到冷启动。命名卷/共享卷方案可以绕过，但引入卷生命周期管理的复杂度，且与 DooD 宿主机路径解析（`host_base_dir`）冲突。预测性按用户预热以零新抽象达到同等效果。

## 7. 实施里程碑

| 阶段 | 内容 | 预估 | 依赖 |
|---|---|---|---|
| P0 预热与观测 | §4.4 thread 创建预热 + §5.6 埋点；不改数据模型与挂载 | 1~2 天 | 无；先合上游 #3494/#3518/#3464 |
| P1 用户级沙箱 | §4.1~4.5、§5.1~5.5 全部；`sandbox.scope` 双模式 | 约 1 周 | P0 上线并观测一周；与上游 channels 大改动（#3487 系列）错开 |
| P2 多租户对齐 | 租户配额、清理策略、K8s provisioner 对齐、与 M1 控制面集成 | 单独评审 | M1 控制面落地 |

每个阶段交付物含：实现、单测/集成测试、本文档状态更新、（P1 起）CHANGELOG 条目。

## 8. 验证方案

- **单测**：`sandbox_id` 派生（双模式）、挂载规格生成、路径重写（含 thread 上下文）、cwd 防护（`..`/绝对路径/符号链接逃逸用例）、预热去重。
- **集成测试**：
  - 同用户连开 3 个会话：仅首个触发容器创建，后两个 `acquire` 命中活跃容器；
  - 新会话首次工具调用延迟 P95 < 1.5s（预热命中）；
  - thread A 无法写入 thread B 的目录（防护回归）；
  - `scope=thread` 全量回归（既有测试不红）。
- **迁移演练**：旧布局数据 → 迁移脚本 dry-run → 实迁 → 双模式冒烟。
- **灰度**：P1 先在本仓库自部署开启 `scope=user`，观察一周指标后定默认值。

## 9. 验收口径

1. `sandbox.scope: user` 下，新会话首条消息（含工具调用）全程无容器冷启动等待（预热命中时）；
2. 既有沙箱相关测试全部通过，`scope=thread` 行为与现状一致；
3. 观测指标（§5.6）可查询、可复盘；
4. 文档（本文档、`backend/docs/CONFIGURATION.md` 沙箱章节）与实现一致。
