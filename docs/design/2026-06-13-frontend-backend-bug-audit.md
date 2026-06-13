# 前后端 Bug / 正确性审计报告

**日期**：2026-06-13
**范围**：backend（Python/FastAPI/LangGraph）+ frontend（Next.js/React/TypeScript）
**目标**：梳理当前代码中可导致功能异常、权限绕过、资源泄漏或运行崩溃的具体 Bug，并给出修复建议。

---

## 后端 Bug

### 1. Stateless Run 接口缺失授权与 Thread 所有权校验

- **文件**：`backend/app/gateway/routers/runs.py`（约 35–88 行）
- **问题**：`stateless_stream()` 与 `stateless_wait()` 没有加 `@require_permission` 装饰器，且从请求体读取 `thread_id` 后未校验当前用户是否拥有该线程。任何登录用户只要知道 UUID 就能继续/冒充他人会话。
- **风险**：**会话劫持 / 越权执行 Run**
- **修复建议**：
  1. 添加 `@require_permission("runs", "create")`；
  2. 若请求带 `thread_id`，先查询 `threads_meta` 表校验 `owner_id` 或 `require_existing=True` 的 owner_check；
  3. 校验通过后再调用 `start_run()`。

---

### 2. AuthMiddleware 给所有登录用户授予全部权限

- **文件**：`backend/app/gateway/auth_middleware.py:125`
- **问题**：`request.state.auth = AuthContext(user=user, permissions=_ALL_PERMISSIONS)` 直接给每个用户所有权限。只要某个路由忘记 `owner_check=True`，`require_permission()` 就会失效。
- **风险**：**普通用户可执行管理员操作**
- **修复建议**：根据 `user.system_role` 或权限表映射权限集合；常规用户只应拥有普通权限。

---

### 3. CSRF Origin 检查无条件信任转发头

- **文件**：`backend/app/gateway/csrf_middleware.py:135-150`
- **问题**：`_request_origin()` 优先使用 `Forwarded`、`X-Forwarded-Proto`、`X-Forwarded-Host`、`X-Forwarded-Port` 构建 origin，但未校验直接连接端 `request.client.host` 是否属于可信代理。在直连/开发模式下，客户端可伪造这些头使跨站请求看起来是同源。
- **风险**：**登录 CSRF 绕过**
- **修复建议**：仅在 `request.client.host` 位于配置的可信代理列表时才信任转发头；否则回退到 `request.url.scheme/netloc`。

---

### 4. 内部认证 Token 在多 Worker 环境下不一致

- **文件**：`backend/app/gateway/internal_auth.py:11`
- **问题**：`_INTERNAL_AUTH_TOKEN = secrets.token_urlsafe(32)` 在模块导入时生成，每个 uvicorn/gunicorn worker 拿到不同 token。Channels 模块通过 HTTP 调用 Gateway 时，若请求被路由到不同 worker，内部认证头会被拒绝。
- **风险**：**多 Worker 部署时 IM Channel 集成失败**
- **修复建议**：从稳定共享密钥派生 token，例如读取环境变量 `DEER_FLOW_INTERNAL_TOKEN` 或复用 `AUTH_JWT_SECRET`。

---

### 5. Upload 沙盒获取后未释放

- **文件**：`backend/app/gateway/routers/uploads.py:224-311`
- **问题**：`sync_to_sandbox=True` 时调用 `sandbox_provider.acquire(thread_id)`，但全程没有 `sandbox_provider.release(sandbox_id)`；`sandbox.update_file(..., file_path.read_bytes())` 也会把整文件读入内存。
- **风险**：**沙盒槽位泄漏 / 大文件内存爆增**
- **修复建议**：用 `try/finally`（或上下文管理器）包裹沙盒使用并调用 `release`；分块流式写入文件。

---

### 6. Model Factory 重复关键字导致崩溃

- **文件**：`backend/packages/harness/deerflow/models/factory.py:202`
- **问题**：`model_class(**kwargs, **model_settings_from_config)` 当两个字典存在相同 key 时直接抛出 `TypeError`。虽然近期处理了 `reasoning_effort`，但 `temperature`、`max_tokens` 等重叠仍会崩溃。
- **风险**：**运行时模型创建崩溃**
- **修复建议**：先合并为单个 dict 再解包，并明确调用方覆盖配置的优先级：

  ```python
  final_kwargs = {**model_settings_from_config, **kwargs}
  model_instance = model_class(**final_kwargs)
  ```

---

### 7. JWT Decoder 未捕获 Pydantic ValidationError

- **文件**：`backend/app/gateway/auth/jwt.py:40-55`
- **问题**：`decode_token()` 在 try 中返回 `TokenPayload(**payload)`，但只捕获 `jwt.*` 异常。当 payload 字段类型错误（如 `exp` 为字符串、缺少 `sub`）时，`pydantic.ValidationError` 会直接上抛成 500。
- **风险**：**500 而非 401，泄露内部错误**
- **修复建议**：导入 `pydantic.ValidationError`，增加 `except ValidationError: return TokenError.MALFORMED` 分支。

---

### 8. Local Provider 模块级缓存引用已关闭 Engine

- **文件**：`backend/app/gateway/deps.py:277-300`
- **问题**：`_cached_repo` / `_cached_local_provider` 是模块全局变量。当 engine/session factory 被关闭重建（测试、热重载、程序化重启）后，缓存仍指向已 dispose 的 factory，导致连接错误。
- **风险**：**热重载/测试后数据库连接异常**
- **修复建议**：在 `langgraph_runtime()` 启动时清理缓存，或按 engine/factory 身份做缓存 key，不匹配则重新创建。

---

### 9. Bootstrap Agent 忽略 reasoning_effort 配置

- **文件**：`backend/packages/harness/deerflow/agents/lead_agent/agent.py:655`
- **问题**：bootstrap 分支调用 `create_chat_model(name=model_name, thinking_enabled=thinking_enabled, ...)` 时没有传 `reasoning_effort`，而默认分支在 681 行传了。
- **风险**：**配置不生效**
- **修复建议**：在 `is_bootstrap` 分支中也传入 `reasoning_effort=reasoning_effort`。

---

### 10. Channel Manager 下载附件无大小限制

- **文件**：`backend/app/channels/manager.py`（约 67–75、435–496 行）
- **问题**：`_read_http_inbound_file()` 使用 `client.get(url)` 并返回 `resp.content`，没有任何大小限制；`_ingest_inbound_files()` 随后把完整 blob 写入磁盘。恶意大附件可导致 Gateway 内存/磁盘耗尽。
- **风险**：**DoS / 资源耗尽**
- **修复建议**：下载前检查 `Content-Length`；使用流式写入并设置明确上限（如 50 MB），超限则中断。

---

### 11. External Conversation 清理失败掩盖原始异常

- **文件**：`backend/app/gateway/external/service.py:129-145`
- **问题**：`self._repo.create()` 失败后执行清理（`thread_store.delete`、`checkpointer.adelete_thread`）。若清理再抛异常，会掩盖最初的异常。
- **风险**：**排障困难**
- **修复建议**：每个清理步骤单独 `try/except` 并记录日志，最后抛出原始异常：`raise original_exc from None`。

---

### 12. Run Worker 的 Bridge 清理任务未检索异常

- **文件**：`backend/packages/harness/deerflow/runtime/runs/worker.py:847`
- **问题**：`finally` 中 `asyncio.create_task(bridge.cleanup(run_id, delay=60))` 后既不等待也不取结果。若 `bridge.cleanup()` 抛异常，会留下 “Task exception was never retrieved” 日志，资源也可能未释放。
- **风险**：**资源泄漏 / 未检索任务异常**
- **修复建议**：附加 done callback 记录异常，或在 lifespan shutdown 时维护并等待这些清理任务。

---

### 13. ChannelStore 在异步协程中使用同步文件 IO 与 threading.Lock

- **文件**：`backend/app/channels/store.py:36-70、87-137`
- **问题**：IM 会话映射存储在 async 协程中使用 `threading.Lock`、`path.read_text()`、`tempfile.NamedTemporaryFile` + `Path.replace()`，会阻塞事件循环；且 `threading.Lock` 在异步任务竞争时会卡死协程。
- **风险**：**事件循环阻塞 / 延迟 / 并发问题**
- **修复建议**：替换为 `asyncio.Lock`，文件读写通过 `asyncio.to_thread()` 或 `aiofiles`；生产环境建议迁移到 SQL 持久化。

---

## 前端 Bug

### 1. useUpdateSubtask 直接修改状态对象

- **文件**：`frontend/src/core/tasks/context.tsx:44-49`
- **问题**：该 hook 直接原地修改 `tasks[task.id]`，然后选择性调用 `setTasks`。React 状态必须不可变；原地修改会破坏 memoization、导致漏渲染，且 `useCallback` 的依赖数组失效。
- **风险**：**状态不一致 / 渲染异常**
- **修复建议**：始终生成新对象：

  ```ts
  updateSubtask = (task) => {
    setTasks((prev) => ({
      ...prev,
      [task.id]: { ...prev[task.id], ...task } as Subtask,
    }));
  };
  ```

---

### 2. CodeBlock 首次渲染后不再更新

- **文件**：`frontend/src/components/ai-elements/code-block.tsx:85-99`
- **问题**：`mounted.current` 在首次高亮后设为 `true` 且不再重置。后续 `code`/`language` 变化会跳过 `setHtml`，导致流式/编辑场景下代码块显示旧内容。
- **风险**：**内容陈旧 / 编辑态不同步**
- **修复建议**：移除 `mounted` 守卫，改用 effect-ID / 取消机制忽略过期的高亮 Promise。

---

### 3. 使用 ref 控制 InputBox 挂载导致渲染失败

- **文件**：`frontend/src/app/workspace/chats/[thread_id]/page.tsx:51、54-56、237`
- **问题**：`mountedRef.current = true` 写在 `useEffect` 中，但 ref 不会触发重渲染。条件 `{mountedRef.current ? <InputBox /> : <div />}` 在 mount 后仍可能一直渲染占位 div。
- **风险**：**InputBox 不显示**
- **修复建议**：改用 `useState(false)` 与 `useEffect(() => setMounted(true), [])`。

---

### 4. MessageList 在渲染阶段执行副作用

- **文件**：`frontend/src/components/workspace/messages/message-list.tsx:294-300、475、485`
- **问题**：`timestampMapRef.current.set(...)` 与 `updateSubtask(...)` 在 render 阶段被直接调用。修改 ref 和调用 context setter 在渲染期执行会违反 React 规则，可能导致 tearing 或无限循环。
- **风险**：**渲染异常 / 死循环**
- **修复建议**：将两者移入以 `groupedMessages`/流式状态为依赖的 `useEffect`，或用 memoized 计算替代 ref 修改。

---

### 5. useSpecificChatMode 的 setTimeout 未清理

- **文件**：`frontend/src/components/workspace/chats/use-chat-mode.ts:24-40`
- **问题**：第 30 行的 `setTimeout` 未取消。若组件在 100ms 内卸载，仍会对已卸载树执行 `setInputRef.current(inputInitialValue)` 与 `textarea.focus()`。
- **风险**：**对已卸载组件 setState / 焦点错误**
- **修复建议**：保存 timeout id 并在 effect cleanup 中 `clearTimeout`。

---

### 6. MessageGroup 自动展开的 setTimeout 未清理

- **文件**：`frontend/src/components/workspace/messages/message-group.tsx:593-604`
- **问题**：`setTimeout(() => { select(...); setOpen(true); }, 100)` 无 cleanup，组件卸载后仍可能调用 artifact context setter。
- **风险**：**对已卸载组件 setState**
- **修复建议**：保存 timeout 并在 `useEffect` cleanup 中清理。

---

### 7. Skill Workspace 中多处 setTimeout 泄漏

- **文件**：
  - `frontend/src/components/workspace/skills/ai-create/skill-ai-create-workspace.tsx`（约 406、473、930、947、1156 行）
  - `frontend/src/components/workspace/skills/editor/skill-editor-workspace.tsx`（约 401、804 行）
- **问题**：`window.setTimeout(() => setHighlightedPaths(new Set()), 4000)` 等模式重复出现且无清理。快速切换 thread 或卸载组件会留下 pending timer。
- **风险**：**对已卸载组件 setState / 内存泄漏**
- **修复建议**：抽取 `useHighlightTimeout` hook，统一返回 cleanup 函数并在组件/effect cleanup 中调用。

---

### 8. CopyButton 的 setTimeout 未清理

- **文件**：`frontend/src/components/workspace/copy-button.tsx:20`
- **问题**：`setTimeout(() => setCopied(false), 2000)` 无 cleanup，超时前卸载会触发 React warning。
- **风险**：**轻微内存泄漏 / warning**
- **修复建议**：用 `useEffect` 清理 timeout，或封装 `useTimeout` hook。

---

### 9. useGlobalShortcuts 因依赖数组不稳定导致反复注册

- **文件**：`frontend/src/hooks/use-global-shortcuts.ts:20-52`
- **问题**：effect 依赖 `shortcuts` 数组。调用方若每次渲染生成新数组，会反复 `addEventListener/removeEventListener`。回调若未用 ref 包裹，还会捕获旧闭包。
- **风险**：**性能损耗 / 快捷键回调状态陈旧**
- **修复建议**：内部用 ref 保存最新 `shortcuts`，listener 只注册一次；`handleKeyDown` 中读取 ref。

---

### 10. useThreadStream 在渲染阶段修改 ref

- **文件**：`frontend/src/core/threads/hooks.ts:745-747`
- **问题**：`if (thread.messages.length >= messagesRef.current.length) { messagesRef.current = thread.messages; }` 在 render 阶段执行，属于副作用。
- **风险**：**渲染异常 / 竞态**
- **修复建议**：将赋值移入以 `thread.messages` 为依赖的 `useEffect`。若 `onUpdateEvent` 需要同步访问，可单独维护一个由 effect 更新的 ref。

---

### 11. useThreadHistory 缺乏请求取消机制

- **文件**：`frontend/src/core/threads/hooks.ts:797-894`
- **问题**：`loadMessages` 使用大量 ref 与空依赖数组的 `useCallback`，在 `runs.data` 变化时触发加载。没有 `AbortController` 或 cleanup 来取消进行中的请求，组件卸载或切线程时可能遗留不一致状态。
- **风险**：**竞态 / 对已卸载组件 setState**
- **修复建议**：将 `AbortController` 贯穿 fetch 链，在 `useEffect` cleanup 与线程切换时取消；重置 guard refs。

---

### 12. ConversationWorkspacePanel 自动选择可能形成循环

- **文件**：`frontend/src/components/workspace/chats/conversation-workspace-panel.tsx:307-320`
- **问题**：`useEffect` 在 `filePaths` 变化时调用 `selectArtifact(filePaths[0]!, true)`。由于 `select` 会更新 `selectedArtifact`，而该 effect 又依赖 `selectedArtifact`，选择变化可能再次触发 artifact 派生并反复跳转。
- **风险**：**无限选择跳转 / 界面抖动**
- **修复建议**：限制 effect 每个 thread/mount 只自动选择一次，或添加 `hasAutoSelected` ref 防止重复选择同一文件。

---

### 13. InputBox 模型自动选择 effect 依赖整个 context 对象

- **文件**：`frontend/src/components/workspace/input-box.tsx:321-340`
- **问题**：effect 依赖整个 `context` 对象。`useThreadSettings` 返回的 `settings.context` 在任意本地设置变化时都会重新 memo，导致 effect 频繁执行；虽然 guard 避免了真死循环，但仍可能触发不必要的 `onContextChange`。
- **风险**：**多余回调 / 潜在循环**
- **修复建议**：effect 只依赖用到的字段（`context.model_name`、`context.mode`），并在父组件中 memoize 传给 `InputBox` 的 `context` 对象。

---

### 14. Settings Store 的 storage 监听器未移除

- **文件**：`frontend/src/core/settings/store.ts:43-49`
- **问题**：全局 `storage` listener 注册一次后永不移除。虽然是单例，但在测试/HMR 中会泄漏 listener，并保持对模块状态的旧闭包引用。
- **风险**：**测试/HMR 中监听器泄漏**
- **修复建议**：提供 `destroy()` 或 `unregisterStorageListener()` 方法，或基于订阅者数量动态注册/注销。

---

## 修复优先级建议

| 优先级 | 问题 | 原因 |
|--------|------|------|
| P0 | Stateless Run 越权、AuthMiddleware 全权限、CSRF 转发头信任 | 安全与权限 |
| P0 | Upload 沙盒未 release、Model Factory TypeError | 稳定性 / 崩溃 / 资源泄漏 |
| P1 | JWT ValidationError、Local Provider 缓存、Bootstrap reasoning_effort | 正确性 |
| P1 | 前端 state 直接修改、CodeBlock stale、InputBox ref mount、MessageList render 副作用 | 渲染正确性 |
| P2 | 未清理的 setTimeout / AbortController / listener | 健壮性 |
| P2 | Channel 附件大小限制、ChannelStore 同步 IO | 安全与性能 |
