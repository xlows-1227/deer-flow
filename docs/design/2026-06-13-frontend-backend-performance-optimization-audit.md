# 前后端性能优化审计报告

**日期**：2026-06-13
**范围**：backend（Python/FastAPI/LangGraph）+ frontend（Next.js/React/TypeScript）
**目标**：识别高影响的性能瓶颈、重复计算、同步阻塞、渲染浪费与缓存失效问题，并给出优化建议。

---

## 后端性能优化

### 1. JsonlRunEventStore.list_messages 全量扫描线程事件

- **文件**：`backend/packages/harness/deerflow/runtime/events/store/jsonl.py:136-147`
- **问题**：每次分页查询都要读取该线程全部 JSONL 运行文件，反序列化所有事件，在 Python 中过滤 `category == "message"`，最后切片。时间复杂度为 `O(线程总事件数)`，与 `limit` 无关。
- **优化建议**：
  - 生产环境优先使用 SQL-backed `RunEventStore`；
  - 若保留 JSONL，则维护每线程的 message seq 索引或游标，避免全量扫描。

---

### 2. RunJournal 无界 Buffer 与丢并发 Flush

- **文件**：`backend/packages/harness/deerflow/runtime/journal.py:342-393`
- **问题**：`_put` 把每个事件追加到 `_buffer`；`_flush_sync` 只要发现有任何 flush task 仍在执行就直接返回。高事件吞吐下事件会在内存中无限堆积，且失败 flush 会被重新 prepend 到 buffer。
- **优化建议**：
  - 为 `_buffer` 设置容量上限（如按事件数或字节数）；
  - 允许少量并发 flush（如 2–3 个），避免单线程阻塞；
  - 超限后按时间/重要性丢弃旧事件，而非无限增长。

---

### 3. RunJournal 在回调线程同步序列化大消息

- **文件**：`backend/packages/harness/deerflow/runtime/journal.py:218、272、324、331`
- **问题**：在 `on_chat_model_start`、`on_llm_end`、`on_tool_end` 中直接调用 `m.model_dump()` / `message.model_dump()`。长会话或多模态内容会阻塞回调线程并产生大量临时 dict。
- **优化建议**：将原始消息存入 buffer，把 `model_dump()` 推迟到异步 flush 路径；或提供更轻量的序列化路径。

---

### 4. MemoryUpdateQueue 在 Threading Timer 线程中执行 IO 与 LLM

- **文件**：`backend/packages/harness/deerflow/agents/memory/queue.py:157-221`
- **问题**：`threading.Timer` 触发后，`_process_contexts` 同步调用 `capture_rollup_input`（文件写入）并最终调用 `DailyRollupService._summarize`（LLM invoke），阻塞后台线程；上下文之间还 sleep 0.5s。
- **优化建议**：改为基于 asyncio 的 debounce 队列，文件/LLM 工作通过 `asyncio.to_thread` 或原生 async LLM 调用分发。

---

### 5. DailyRollupService._summarize 同步调用 LLM

- **文件**：`backend/packages/harness/deerflow/agents/memory/rollup.py:132-151`
- **问题**：`_summarize` 调用 `model.invoke(...)`。虽然 scheduler 通过 `asyncio.to_thread` 运行，但每个 rollup 仍会阻塞一个 worker 线程完整 LLM 延迟。
- **优化建议**：将 `_summarize` / `rollup_date` 改为 async，使用 `await model.ainvoke(...)`；在可聚合时批量 rollup 输入。

---

### 6. MemoryStorageV2.list_daily 读取全部 Daily 文件

- **文件**：`backend/packages/harness/deerflow/agents/memory/storage_v2.py:91-106`
- **问题**：列出用户 daily 目录下所有 `*.json` 文件，逐个读取反序列化，过滤 deleted 后再应用 `limit`。成本为 `O(天数)`，即使只需要最近几条。
- **优化建议**：维护 reverse-chronological 索引（如 `index.json`），或将 daily summary 迁移到数据库。

---

### 7. ProfileConsolidator 每次全量重建 Profile

- **文件**：`backend/packages/harness/deerflow/agents/memory/consolidation.py:44-50`
- **问题**：每次 rollup 成功后都获取所有 active daily summaries 并从头重建整个 profile。
- **优化建议**：增量合并：只把当天新 summary 合并到现有 profile，避免全量重算。

---

### 8. RunRepository.put 每次 Upsert 都全行 SELECT

- **文件**：`backend/packages/harness/deerflow/persistence/run/sql.py:81-126`
- **问题**：`RunManager._persist_snapshot_to_store` 每产生一次状态 snapshot 就调用 `put`，而 `put` 先 `SELECT *`、再修改对象、再 commit，比针对性 UPDATE 重得多。
- **优化建议**：状态/进度更新走已有的 `update_status` / `update_run_progress` 方法，而非 `put`。

---

### 9. RunRepository.list_pending / list_inflight 无结果上限

- **文件**：`backend/packages/harness/deerflow/persistence/run/sql.py:189-219`
- **问题**：这两个查询没有 `LIMIT`。启动时的 `reconcile_orphaned_inflight_runs` 可能把所有 pending/running run 一次性加载进内存。
- **优化建议**：增加 `limit` 参数；或用 server-side cursor、分批处理。

---

### 10. get_enabled_skills_for_config 缓存因 Config 对象 identity 失效

- **文件**：`backend/packages/harness/deerflow/agents/lead_agent/prompt.py:130-153`
- **问题**：缓存 key 是 `id(app_config)`。`deps.get_config()` 在 `config.yaml` mtime 变化时会重新加载并生成新的 `AppConfig` 对象，导致技能缓存失效，所有 skill 文件被重新扫描。
- **优化建议**：缓存 key 改为 config 路径 + mtime / 版本号；仅在 skill 路径真正变化时失效。

---

### 11. ChannelStore 每次变更都重写整个 JSON 文件

- **文件**：`backend/app/channels/store.py:36-107`
- **问题**：整个 IM 会话→thread 映射存在内存中，每次 `set_thread_id` / `remove` 都序列化到临时文件再原子替换。写入为 `O(n)`，且多进程写入时内存副本不会重新加载。
- **优化建议**：用 SQLite 持久化，或改为 append-only log + 定期 compaction。

---

### 12. MemoryStreamBridge 只按事件数限流

- **文件**：`backend/packages/harness/deerflow/runtime/stream_bridge/memory.py:32-78`
- **问题**：`queue_maxsize=256` 仅限制事件数量，但 `values` 模式事件包含完整 checkpoint state，单个事件可能很大；run 结束后还保留 60s（`worker.py:847`）。
- **优化建议**：除了事件数，还按总字节数与保留时长做上限，防止内存突增。

---

### 13. ThreadMetaRepository.search 对 JSON metadata 做无索引过滤

- **文件**：`backend/packages/harness/deerflow/persistence/thread_meta/sql.py:126-150`
- **问题**：每个 metadata filter 都是对 `metadata_json` 的 JSON 表达式，无数据库索引；offset 分页在数据量大时也会退化。
- **优化建议**：对常用过滤 key 增加 generated/indexed 列；或使用专用搜索引擎；offset 分页改为 cursor 分页。

---

### 14. Suggestions 端点每个请求新建 Chat Model

- **文件**：`backend/app/gateway/routers/suggestions.py:132`
- **问题**：每次建议请求都调用 `create_chat_model(...)`，可能重新读取配置并初始化 provider 客户端。
- **优化建议**：使用 `get_cached_chat_model(...)` 或缓存解析后的 model 实例。

---

### 15. JsonlRunEventStore._ensure_seq_loaded 首次写入扫描全部文件

- **文件**：`backend/packages/harness/deerflow/runtime/events/store/jsonl.py:53-68`
- **问题**：首次写入线程前，会扫描所有 JSONL 文件并反序列化每一行，只为找到当前最大 `seq`。
- **优化建议**：持久化每线程 seq 计数器（如小文件 `seq.meta`），或只读取最新文件的最后一行。

---

## 前端性能优化

### 1. MessageList 的 getMessageGroups 每次渲染重新计算

- **文件**：`frontend/src/components/workspace/messages/message-list.tsx:191`
- **问题**：`getMessageGroups(messages)` 在每次渲染（包括每次流式 tick）都重新执行。该函数最坏情况下是 O(n²)，因为要扫描所有 group 找 matching tool-call id。
- **优化建议**：`useMemo(() => getMessageGroups(messages), [messages])`。

---

### 2. MessageList 的 getAssistantTurnUsageMessages 未 Memoize

- **文件**：`frontend/src/components/workspace/messages/message-list.tsx:239`
- **问题**：该函数在每次流式 tick 都重建每 group 的 AI message 数组。
- **优化建议**：`useMemo(() => getAssistantTurnUsageMessages(groupedMessages), [groupedMessages])`。

---

### 3. MessageList 无虚拟化

- **文件**：`frontend/src/components/workspace/messages/message-list.tsx:367`
- **问题**：整个消息列表用普通 `.map(...)` 渲染。长线程会产生成百上千 DOM 节点与 React 元素，无任何虚拟化。
- **优化建议**：引入 `@tanstack/react-virtual` 或 `react-window` 对消息 group 做虚拟化；保留现有 “load more history” sentinel。

---

### 4. MessageListItem 未 React.memo

- **文件**：`frontend/src/components/workspace/messages/message-list-item.tsx:127`
- **问题**：只有内部 `MessageContent` 做了 memo，父组件 `MessageListItem` 没有。每次父组件重渲染都会重渲染所有可见消息项。
- **优化建议**：`export const MessageListItem = memo(MessageListItem_)`，自定义比较器检查 `message.id`、`isLoading` 等关键 prop。

---

### 5. MessageGroup 未 Memoize

- **文件**：`frontend/src/components/workspace/messages/message-group.tsx:45`
- **问题**：虽然内部做了若干 `useMemo` 转换，但组件本身未 memo，父组件每次重渲染都会协调所有子节点。
- **优化建议**：用 `React.memo` 包裹，使只有 messages 实际变化的 group 才重渲染。

---

### 6. useThreadStream 返回对象身份不稳定

- **文件**：`frontend/src/core/threads/hooks.ts:769`
- **问题**：`useThreadStream` 每次渲染返回新的 `mergedThread` 对象 `{ ...thread, messages: mergedMessages }`，导致下游 `MessageList` / `ChatBox` props 永远无法引用稳定。
- **优化建议**：在 hook 内部用 `useMemo` 稳定返回对象、以及 `mergedMessages` / `humanMessageCount`。

---

### 7. humanMessageCount 每次渲染 filter

- **文件**：`frontend/src/core/threads/hooks.ts:480`
- **问题**：`thread.messages.filter(...)` 每次渲染都执行一遍。
- **优化建议**：改为单次 reduce 并用 `useMemo` 缓存：`useMemo(() => thread.messages.reduce(...), [thread.messages])`。

---

### 8. ThreadContext.Provider value 每次渲染都是新对象

- **文件**：`frontend/src/app/workspace/chats/[thread_id]/page.tsx:157`
- **问题**：Provider 传入内联对象 `{ thread, isMock }`，每次渲染都是新引用，即使 thread 稳定也会触发所有 context consumer 重渲染。
- **优化建议**：`const threadContextValue = useMemo(() => ({ thread, isMock }), [thread, isMock]);` 传给 Provider。

---

### 9. LoadMoreHistoryIndicator 的 IntersectionObserver 随 callback 重建

- **文件**：`frontend/src/components/workspace/messages/message-list.tsx:104-126`
- **问题**：每当 `throttledLoadMore` 变化就会新建/销毁 IntersectionObserver。而 `throttledLoadMore` 依赖父组件的 `loadMore` callback，可能频繁抖动。
- **优化建议**：用一个 ref 持有单一 observer 实例，observer callback 中通过 ref 调用最新 callback，不因 callback identity 变化而重建 observer。

---

### 10. CodeBlock 同时生成 light/dark 两份高亮 HTML

- **文件**：`frontend/src/components/ai-elements/code-block.tsx:61-72`
- **问题**：`highlightCode` 每次都会通过 Shiki 生成两套主题的 HTML，而当前只展示一套。
- **优化建议**：
  - 只生成当前激活主题，读取 `resolvedTheme` 或媒体查询；
  - 另一套主题延迟生成/按需生成；
  - 同时修复 `mounted` guard 导致的内容不更新问题。

---

### 11. CodeEditor 全量导入 language-data

- **文件**：`frontend/src/components/workspace/code-editor.tsx:9`
- **问题**：直接 import 完整的 `@codemirror/language-data` 的 `languages` bundle 和 `@uiw/react-codemirror`，只要加载 artifact detail 视图就会全部打包。
- **优化建议**：用 `next/dynamic` 或组件内动态 `import()` 懒加载 CodeMirror 与语言支持，仅在打开 artifact 时请求 chunk。

---

### 12. 多个静态列表 Hook 默认 staleTime 为 0 且窗口聚焦即刷新

- **文件**：
  - `frontend/src/core/skills/hooks.ts:23`
  - `frontend/src/core/connectors/hooks.ts:5`
  - `frontend/src/core/agents/hooks.ts:13`
  - `frontend/src/core/memory/hooks.ts:23`
  - `frontend/src/core/mcp/hooks.ts:6`
- **问题**：这些 TanStack Query hook 使用默认 `staleTime: 0` 与 `refetchOnWindowFocus: true`。Skills、Connectors、Agents、Models、Memory、MCP 配置等数据变更不频繁，却每次挂载/切回标签页都重新请求。
- **优化建议**：设置 `staleTime: 5 * 60 * 1000` 并关闭 `refetchOnWindowFocus`，必要时手动 invalidate。

---

### 13. useSandboxFiles 切回窗口即刷新

- **文件**：`frontend/src/core/sandbox/hooks.ts:9`
- **问题**：`staleTime: 1000` 且未关闭 `refetchOnWindowFocus`，用户 alt-tab 回聊天就会重新拉取 sandbox 文件列表。
- **优化建议**：`staleTime` 提高到 30s，设置 `refetchOnWindowFocus: false`；文件写入/上传后显式 invalidate。

---

### 14. useThreadHistory 串行加载历史 Run 消息

- **文件**：`frontend/src/core/threads/hooks.ts:817-860`
- **问题**：在一个 `do…while` 循环中串行加载每个 run 的消息。线程 run 数量多时很慢。
- **优化建议**：在并发限制下用 `Promise.all` 并行获取各 run 的消息，然后合并/去重。

---

### 15. WorkspaceFileTree 每次重建且无虚拟化

- **文件**：`frontend/src/components/workspace/chats/conversation-workspace-panel.tsx:216`
- **问题**：`buildSandboxFileTree(files)` 每次渲染都重建文件树，并渲染所有行，文件数量大时开销高。
- **优化建议**：`useMemo` 缓存 tree；文件数量大时做虚拟化。

---

## 快速收益清单（建议优先执行）

| 优先级 | 优化项 | 预期收益 |
|--------|--------|----------|
| P0 | `MessageList` 中 `groupedMessages` 与 `turnUsageMessagesByGroupIndex` 加 `useMemo` | 减少每次流式 tick 的重计算 |
| P0 | `MessageListItem`、`MessageGroup` 加 `React.memo` | 减少可见消息的重渲染 |
| P0 | 静态列表 hook 增加 `staleTime`、关闭 `refetchOnWindowFocus` | 大量减少无效请求 |
| P1 | 消息列表虚拟化 | 长线程渲染与内存大幅优化 |
| P1 | `useThreadStream` 返回对象 memoize、`ThreadContext.Provider` value memoize | 稳定引用，减少下游重渲染 |
| P1 | 后端 `RunJournal` buffer 上限与并发 flush、JSONL store 改用 SQL | 后端事件吞吐与内存稳定 |
| P2 | `CodeBlock` 只生成当前主题、`CodeEditor` 懒加载 | 减少 CPU 与 bundle |
| P2 | Memory rollup/storage 增量化与异步化 | 降低 LLM 与文件 IO 阻塞 |
