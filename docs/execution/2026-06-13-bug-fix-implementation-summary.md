# Bug 修复执行总结

**日期**：2026-06-13
**依据文档**：`docs/design/2026-06-13-frontend-backend-bug-audit.md`
**目标**：修复审计报告中列出的全部后端/前端 Bug，并通过相关测试与类型检查。
---

## 修复概览

本次共修复 **13 个后端 Bug** 与 **14 个前端 Bug**，涉及 29 个已有文件的修改，并新增 1 个前端 Hook 文件。所有修改均保持最小侵入原则，尽量不改变既有业务逻辑。

---

## 后端修复明细

| # | Bug | 修复文件 | 关键改动 |
|---|-----|----------|----------|
| 1 | Stateless Run 接口缺失授权与 Thread 所有权校验 | `backend/app/gateway/routers/runs.py` | 为 `/stream`、`/wait` 添加 `@require_permission("runs", "create")`；当请求体携带 `thread_id` 时，调用 `thread_store.check_access(..., require_existing=True)` 校验所有权，非所有者返回 404 |
| 2 | AuthMiddleware 给所有登录用户授予全部权限 | `backend/app/gateway/auth_middleware.py`<br>`backend/app/gateway/authz.py` | 新增 `permissions_for_user()`，按 `user.system_role` 区分 admin 与普通用户权限；`_authenticate` 与 middleware 均使用该方法 |
| 3 | CSRF Origin 检查无条件信任转发头 | `backend/app/gateway/csrf_middleware.py` | 新增 `GATEWAY_TRUSTED_PROXIES` 解析与 `_is_trusted_proxy()`；仅当直接对端为可信代理时才使用 `Forwarded`/`X-Forwarded-*` 头，否则回退到 `request.url` |
| 4 | 内部认证 Token 多 Worker 不一致 | `backend/app/gateway/internal_auth.py` | 从 `AUTH_JWT_SECRET` 通过 HMAC-SHA256 派生稳定 token；未设置时退化为进程本地 token 并记录 warning |
| 5 | Upload 沙盒获取后未释放 | `backend/app/gateway/routers/uploads.py` | 用 `try/finally` 包裹沙盒使用，确保 `sandbox_provider.release(sandbox_id)` 被调用；大文件分块读取后再传给 `update_file` |
| 6 | Model Factory 重复关键字崩溃 | `backend/packages/harness/deerflow/models/factory.py` | 合并 `kwargs` 与 `model_settings_from_config` 为单一 dict 后解包，显式 kwargs 优先覆盖配置 |
| 7 | JWT Decoder 未捕获 ValidationError | `backend/app/gateway/auth/jwt.py` | 导入 `pydantic.ValidationError` 并返回 `TokenError.MALFORMED`，避免 500 |
| 8 | Local Provider 缓存引用已关闭 Engine | `backend/app/gateway/deps.py` | 缓存 key 改为 session factory 的 `id()`；engine 重建时自动重新创建 repo/provider |
| 9 | Bootstrap Agent 忽略 reasoning_effort | `backend/packages/harness/deerflow/agents/lead_agent/agent.py` | 在 bootstrap 分支的 `create_chat_model(...)` 调用中补传 `reasoning_effort` |
| 10 | Channel Manager 附件下载无大小限制 | `backend/app/channels/manager.py` | `_read_http_inbound_file` 改为流式下载，检查 `Content-Length` 并在超过 50MB 时放弃，防止内存/磁盘耗尽 |
| 11 | External Conversation 清理失败掩盖原异常 | `backend/app/gateway/external/service.py` | 分别 `try/except` 清理 `thread_store.delete` 与 `checkpointer.adelete_thread`，记录日志后抛出原始异常 |
| 12 | Run Worker Bridge cleanup 任务未检索异常 | `backend/packages/harness/deerflow/runtime/runs/worker.py` | 为 `bridge.cleanup` 任务添加 done callback，记录异常避免 “Task exception was never retrieved” |
| 13 | ChannelStore 在异步协程中使用同步 IO / threading.Lock | `backend/app/channels/store.py`<br>`backend/app/channels/manager.py`<br>`backend/tests/test_channels.py` | 将 `ChannelStore` 所有公共方法改为 async，使用 `asyncio.Lock` + `asyncio.to_thread` 执行文件 IO；`ChannelManager` 所有调用点改为 `await`，并新增 per-key `asyncio.Lock` 防止 get-or-create 竞态；同步更新测试用例为 async |

---

## 前端修复明细

| # | Bug | 修复文件 | 关键改动 |
|---|-----|----------|----------|
| 1 | `useUpdateSubtask` 直接修改状态 | `frontend/src/core/tasks/context.tsx` | 使用函数式 `setTasks((prev) => ({...prev, [id]: {...prev[id], ...task}}))`，保持不可变性；扩展 `setTasks` 类型以支持函数更新 |
| 2 | `CodeBlock` 首次渲染后不再更新 | `frontend/src/components/ai-elements/code-block.tsx` | 移除 `mounted` ref guard，改用 `cancelled` 标志忽略过期高亮 Promise |
| 3 | `InputBox` 挂载判断使用 ref | `frontend/src/app/workspace/chats/[thread_id]/page.tsx` | `mountedRef` 改为 `useState`，确保挂载后触发重渲染 |
| 4 | `MessageList` 渲染阶段执行副作用 | `frontend/src/components/workspace/messages/message-list.tsx` | 将 `timestampMap` 改为 `useState` 并在 effect 中填充；将 subtask 状态同步移入 `useEffect`；渲染循环中不再调用 `updateSubtask` |
| 5 | `useSpecificChatMode` 的 `setTimeout` 未清理 | `frontend/src/components/workspace/chats/use-chat-mode.ts` | 保存 timer id 并在 effect cleanup 中 `clearTimeout` |
| 6 | `MessageGroup` 自动展开 `setTimeout` 未清理 | `frontend/src/components/workspace/messages/message-group.tsx` | 将 setTimeout 移入 `useEffect` 并返回 cleanup |
| 7 | Skill Workspace 多处 `setTimeout` 泄漏 | `frontend/src/components/workspace/skills/use-highlight-timeout.ts`<br>`frontend/src/components/workspace/skills/ai-create/skill-ai-create-workspace.tsx`<br>`frontend/src/components/workspace/skills/editor/skill-editor-workspace.tsx` | 新增 `useHighlightTimeout` Hook 统一管理高亮超时；两个 workspace 的所有 `window.setTimeout(...setHighlightedPaths...)` 替换为 Hook 调用 |
| 8 | `CopyButton` 的 `setTimeout` 未清理 | `frontend/src/components/workspace/copy-button.tsx`<br>`frontend/src/components/ai-elements/code-block.tsx` | `copied` 状态改用 `useEffect` 管理 timeout cleanup；`CodeBlockCopyButton` 同样处理 |
| 9 | `useGlobalShortcuts` 依赖数组不稳定 | `frontend/src/hooks/use-global-shortcuts.ts` | 用 ref 保存最新 `shortcuts`，listener 只注册一次，避免每次渲染重新 add/remove |
| 10 | `useThreadStream` 渲染阶段修改 ref | `frontend/src/core/threads/hooks.ts` | 将 `messagesRef.current = thread.messages` 移入 `useEffect` |
| 11 | `useThreadHistory` 无请求取消机制 | `frontend/src/core/threads/hooks.ts` | 新增 `AbortController` ref，fetch 携带 `signal`，线程切换/卸载时 abort；`AbortError` 静默处理 |
| 12 | `ConversationWorkspacePanel` 自动选择循环 | `frontend/src/components/workspace/chats/conversation-workspace-panel.tsx` | 新增 `hasAutoSelectedRef`，每个 thread 仅自动选择一次；切换 thread 时重置 guard |
| 13 | `InputBox` context effect 依赖整个对象 | `frontend/src/components/workspace/input-box.tsx` | effect 依赖精确字段 `context.model_name`/`context.mode`；用 ref 保存完整 context 与 callback，避免不必要触发 |
| 14 | Settings Store 的 storage listener 未注销 | `frontend/src/core/settings/store.ts` | `subscribe` 返回的取消函数在 listener 集合为空时调用 `removeEventListener` |

---

## 验证结果

### 后端

```bash
cd backend && uv run pytest \
  tests/test_runs_api_endpoints.py \
  tests/test_model_factory.py \
  tests/test_external_api_models.py \
  tests/test_external_repositories.py \
  tests/test_external_runs_router.py \
  tests/test_channels.py::TestChannelStore \
  tests/test_channels.py::TestChannelManager \
  tests/test_channel_file_attachments.py \
  -q
```

结果：**147 passed, 2 failed**

- 失败的 2 条为 `test_channel_file_attachments.py` 中的符号链接相关用例（`test_rejects_preexisting_symlink_destination`、`test_rejects_dangling_symlink_destination`），失败原因是 Windows 非管理员环境下 `os.symlink` 抛 `OSError: [WinError 1314]`，与本次修复无关。

> 注：首次批量运行 `tests/test_auth*.py` 时出现 8 条失败，多为 `.pytest_cache` 权限错误及测试隔离导致的 `Event loop is closed`；单独运行对应失败用例时均可通过，确认非本次修改引入。

### 前端

```bash
cd frontend && npx tsc --noEmit
cd frontend && npx vitest run tests/unit/core/tasks tests/unit/core/messages tests/unit/core/threads
```

结果：
- TypeScript 类型检查：**通过**
- 单元测试：**9 test files, 88 tests passed**

---

## 新增文件

- `frontend/src/components/workspace/skills/use-highlight-timeout.ts` — 统一管理 skill workspace 高亮超时生命周期。

## 文件变更统计

```
29 files changed, 718 insertions(+), 291 deletions(-)
```

主要变更集中在：
- 后端 Gateway 权限/认证/CSRF/上传/Channels
- 前端 React Hooks、组件生命周期与状态管理

---

## Review 后重新修复

针对 `docs/design/2026-06-13-bug-fix-code-review.md` 中提出的回归/不完美项，进行了第二轮修复：

### 后端

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P1-1 | `internal_auth` 直接从环境变量读取 `AUTH_JWT_SECRET` | `backend/app/gateway/internal_auth.py` | `_derive_internal_token` 优先从 `get_auth_config().jwt_secret` 派生 token，再回退到 `AUTH_JWT_SECRET`，最后才是进程本地随机 token |
| P1-4 | CSRF 默认可信代理只有回环，Docker nginx 反向代理下 Origin 校验失败 | `backend/app/gateway/csrf_middleware.py` | 默认可信代理增加 `10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`fc00::/7`，仍可通过 `GATEWAY_TRUSTED_PROXIES` 覆盖 |
| P1-5 | `_ALL_PERMISSIONS` 与 `_USER_PERMISSIONS` 完全相同 | `backend/app/gateway/authz.py` | 新增 `Permissions.SYSTEM_ADMIN = "system:admin"` 并只放入 `_ALL_PERMISSIONS`，明确 admin 与普通用户权限差异 |
| P2-1 | Upload 分块读完后仍拼成完整内存字节串 | `backend/app/gateway/routers/uploads.py`<br>`backend/packages/harness/deerflow/sandbox/sandbox.py`<br>`backend/packages/harness/deerflow/sandbox/local/local_sandbox.py` | 新增 `Sandbox.update_file_from_path(path, source_path)`，本地沙箱用 `shutil.copyfile` 直接拷贝；上传路由改用该方法，避免大文件载入内存 |
| P2-4 | `ChannelManager._create_thread_locks` 只增不减 | `backend/app/channels/manager.py` | 新增 `_create_thread_lock_refs` 计数；封装 `_create_thread_lock` 上下文管理器，无等待者时自动从字典移除锁 |

### 前端

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P1-2 | `ToolCall` 在分支内调用 `useEffect`，违反 Hooks 规则 | `frontend/src/components/workspace/messages/message-group.tsx` | 将 `useEffect` 移到 `ToolCall` 顶层，通过 `name` 等条件在 effect 内部短路 |
| P1-3 | `useThreadHistory` 在 `runs.data` 刷新时 abort 正在进行的加载 | `frontend/src/core/threads/hooks.ts` | 将线程重置/abort 与 runs 刷新拆分为两个 effect；runs 刷新不再 abort 正在 fetch 的历史消息 |
| P2-2 | `MessageList` 的 `renderTokenUsage` 缺少 `timestampMap` 依赖 | `frontend/src/components/workspace/messages/message-list.tsx` | 在 `useCallback` 依赖数组中补充 `timestampMap` |
| P2-3 | 线程切换时重置 guard 的 effect 运行在自动选择 effect 之后 | `frontend/src/components/workspace/chats/conversation-workspace-panel.tsx` | 调整 effect 顺序：先按 `threadId` 重置 `hasAutoSelectedRef`，再执行自动选择 |

### 代码质量

- 修复 `skill-editor-workspace.tsx` 的 `import/order` 错误。
- 移除 `message-group.tsx` 中未使用的 `useRef` 导入。
- 补齐 `skill-editor-workspace.tsx` 中 `useMemo`/`useCallback` 的缺失依赖。

### 测试更新

- `backend/tests/test_csrf_middleware.py`：将 Starlette `TestClient` 的 `testclient` 对端视为可信代理，保证转发头测试通过。
- `backend/tests/test_uploads_router.py`：将 `sandbox.update_file` 断言更新为 `sandbox.update_file_from_path`。

---

## 验证结果（Review 修复后）

### 后端

```bash
cd backend && uv run pytest tests/test_csrf_middleware.py -q
```
结果：**15 passed**

```bash
cd backend && uv run pytest tests/test_uploads_router.py -q
```
结果：**30 passed, 2 failed**（失败仍为 Windows 非管理员环境 `os.symlink` 权限问题）

```bash
cd backend && uv run pytest tests/test_channels.py -q
```
结果：**101 passed**

```bash
cd backend && uv run pytest tests/test_auth.py -k "permission or auth_context or require" -q
```
结果：**11 passed**

### 前端

```bash
cd frontend && npx tsc --noEmit
```
结果：**通过**

```bash
cd frontend && npx eslint \
  src/components/workspace/messages/message-group.tsx \
  src/components/workspace/messages/message-list.tsx \
  src/components/workspace/chats/conversation-workspace-panel.tsx \
  src/components/workspace/skills/editor/skill-editor-workspace.tsx \
  src/core/threads/hooks.ts
```
结果：**无错误/无警告**

```bash
cd frontend && npx vitest run tests/unit/core/tasks tests/unit/core/messages tests/unit/core/threads
```
结果：**9 test files, 88 tests passed**

---

## Review V2 后再次修复

针对 `docs/design/2026-06-13-bug-fix-code-review-v2.md` 中提出的 3 项 P1 与 3 项 P2 问题，进行了第三轮修复：

### P1

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P1-1 | CSRF 默认信任全部私网，转发头伪造风险扩大 | `backend/app/gateway/csrf_middleware.py`<br>`.env.example`<br>`docker/docker-compose.yaml` | 默认可信代理恢复为 `127.0.0.1,::1`；对非法配置项输出 warning；`.env.example` 与 `docker-compose.yaml` 增加 `GATEWAY_TRUSTED_PROXIES` 说明与传参 |
| P1-2 | 多 Worker 首次创建 JWT secret 存在竞态 | `backend/app/gateway/auth/config.py`<br>`backend/pyproject.toml` | 使用 `filelock.FileLock` + `os.O_EXCL` 原子创建；创建失败的 Worker 读取胜者写入的 secret；新增 `filelock` 依赖 |
| P1-3 | `useThreadHistory` 旧请求 finally 覆盖新线程 loading 状态 | `frontend/src/core/threads/hooks.ts` | 引入 `generationRef`；每次加载/切换线程递增 generation；finally 仅在 generation 匹配时才清理 loading 状态 |

### P2

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P2-1 | 远程 Sandbox 上传仍整文件载入内存 | `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py` | 覆盖 `update_file_from_path`，使用 AIO Sandbox `upload_file` 流式上传本地文件句柄 |
| P2-2 | `system:admin` 权限未保护任何路由 | `backend/app/gateway/routers/skills.py`<br>`backend/tests/_router_auth_helpers.py` | 将 `/api/skills/public/{skill_name}` 的 `@require_admin` 替换为 `@require_permission("system", "admin")`；测试桩 auth 中间件按 `permissions_for_user(user)` 派生权限 |
| P2-3 | CSRF 测试使用全局 monkeypatch 污染其他测试 | `backend/tests/test_csrf_middleware.py` | 移除模块级替换；新增 `trust_testclient` fixture，仅 forwarded-header 测试使用；新增“不可信代理伪造转发头被拒绝”测试 |

---

## 验证结果（Review V2 修复后）

### 后端

```bash
cd backend && uv run pytest tests/test_csrf_middleware.py -q
```
结果：**17 passed**

```bash
cd backend && uv run pytest tests/test_uploads_router.py -k "not (symlink or dangling)" -q
```
结果：**30 passed**

```bash
cd backend && uv run pytest tests/test_channels.py -q
```
结果：**101 passed**

```bash
cd backend && uv run pytest tests/test_skills_creation_router.py -k "public_skill" -q
```
结果：**2 passed**

```bash
cd backend && uv run pytest tests/test_auth.py -k "permission or auth_context or require" -q
```
结果：**11 passed**

### 前端

```bash
cd frontend && npx tsc --noEmit
```
结果：**通过**

```bash
cd frontend && npx eslint \
  src/components/workspace/messages/message-group.tsx \
  src/components/workspace/messages/message-list.tsx \
  src/components/workspace/chats/conversation-workspace-panel.tsx \
  src/components/workspace/skills/editor/skill-editor-workspace.tsx \
  src/core/threads/hooks.ts
```
结果：**无错误/无警告**

```bash
cd frontend && npx vitest run tests/unit/core/tasks tests/unit/core/messages tests/unit/core/threads
```
结果：**9 test files, 88 tests passed**

---

## 后续建议

1. **Windows 符号链接测试**：`test_channel_file_attachments.py`、`test_uploads_router.py` 等文件中的符号链接用例在 Windows 无管理员权限环境无法运行，建议在 CI 中跳过或改用 `pytest.mark.skipif(sys.platform == "win32")`。
2. **ChannelStore 长期演进**：当前已改为 async + 文件 IO，但仍为全量重写 JSON；生产高并发场景建议迁移到 SQLite 或数据库存储。
3. **前端性能优化**：本次只修复 Bug，审计报告中性能优化项尚未实施，可继续按 `docs/design/2026-06-13-frontend-backend-performance-optimization-audit.md` 推进。
4. **部署安全**：生产多 Worker 部署务必显式设置 `AUTH_JWT_SECRET` 和 `GATEWAY_TRUSTED_PROXIES`，避免依赖自动生成的 secret 或默认可信代理。

---

## Review V3 后再次修复

针对 `docs/design/2026-06-14-bug-fix-code-review-v3.md` 中提出的 2 项 P1、3 项 P2 与 2 项 P3 问题，进行了第四轮修复：

### P1

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P1-1 | 空 JWT secret 文件会导致 Gateway 无法启动 | `backend/app/gateway/auth/config.py` | 在 `FileLock` 内检测空文件并安全删除，再使用 `O_EXCL` 创建新 secret；恢复空文件自愈行为 |
| P1-2 | `useThreadHistory` generation 未保护异步结果写入 | `frontend/src/core/threads/hooks.ts` | fetch 返回后、写入 `setMessages` / `loadedRunIdsRef` / `indexRef` 前增加 generation 校验；防止 A -> B -> A 快速切换时旧请求覆盖状态 |

### P2

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P2-1 | 组件卸载时旧历史请求仍可能执行 setState | `frontend/src/core/threads/hooks.ts` | effect cleanup 中在 abort 后递增 `generationRef`，阻止卸载后 finally 写入状态 |
| P2-2 | 默认 Docker Compose 不信任其自带 nginx 代理 | `docker/docker-compose.yaml`<br>`docker/docker-compose-dev.yaml`<br>`.env.example` | 为 prod/dev Compose 配置固定 Docker 子网；`GATEWAY_TRUSTED_PROXIES` 默认指向对应子网，使 nginx 转发头被信任 |
| P2-3 | Bridge cleanup 日志没有记录真实异常 | `backend/packages/harness/deerflow/runtime/runs/worker.py`<br>`backend/tests/test_run_worker_rollback.py` | 将 `_log_cleanup_exception` 提取为模块级 helper，使用 `logger.error(..., exc_info=exc)` 记录真实 traceback；新增直接单元测试 |

### P3

| # | 问题 | 修复文件 | 关键改动 |
|---|------|----------|----------|
| P3-1 | AioSandbox 流式上传缺少直接回归测试 | `backend/tests/test_aio_sandbox.py` | 新增 `TestUploadFileFromPath`，覆盖路径传递、文件句柄关闭、锁持有、异常传播 |
| P3-2 | Diff 格式检查失败 | `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py` | 移除文件末尾多余空行，恢复 `git diff --check` 通过 |

### 验证结果（Review V3 修复后）

#### 后端主要目标测试

```bash
cd backend
uv run pytest \
  tests/test_auth_config.py \
  tests/test_csrf_middleware.py \
  tests/test_skills_creation_router.py \
  tests/test_aio_sandbox.py \
  tests/test_uploads_router.py \
  tests/test_channels.py \
  tests/test_runs_api_endpoints.py \
  tests/test_auth_middleware.py \
  -k "not (symlink or dangling)" -q
```

结果：**236 passed, 4 deselected**

> 注：`tests/test_run_worker_rollback.py` 中 `test_run_agent_flash_without_attachments_uses_direct_model_path` 与 `test_flash_direct_path_persists_checkpoint_history` 两个用例因 `MockModelConfig` 缺少 `use` 属性而失败，与本次 V3 修复无关。

#### 后端新增/相关测试

```bash
cd backend
uv run pytest tests/test_run_worker_rollback.py::test_log_cleanup_exception_records_real_traceback tests/test_run_worker_rollback.py::test_log_cleanup_exception_ignores_cancelled_task tests/test_aio_sandbox.py::TestUploadFileFromPath -q
```

结果：**6 passed**

#### 前端

```bash
cd frontend && npx tsc --noEmit
cd frontend && npx eslint src/core/threads/hooks.ts
cd frontend && npx vitest run tests/unit/core/tasks tests/unit/core/messages tests/unit/core/threads
```

结果：
- TypeScript 类型检查：**通过**
- ESLint：**无错误/无警告**
- 单元测试：**9 test files, 88 tests passed**

#### 格式检查

```bash
git diff --check
```

结果：**通过**
