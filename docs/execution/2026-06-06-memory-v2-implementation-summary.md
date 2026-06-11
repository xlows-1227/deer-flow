# 2026-06-06 记忆系统实施总结

## 概述

本次修改完成了每日按人记忆系统的主要落地工作，并围绕实际使用中发现的问题补齐了设置页、清空语义、立即汇总、单会话汇总和记忆证据污染防护。

关联文档：

- 产品需求：[`../product/2026-06-05-memory-daily-person-summary.md`](../product/2026-06-05-memory-daily-person-summary.md)
- 架构设计：[`../design/2026-06-05-memory-daily-person-architecture.md`](../design/2026-06-05-memory-daily-person-architecture.md)

## 产品与界面修改

### 设置页清理

- 设置页记忆模块聚焦展示长期画像、每日总结和手动记忆。
- 移除旧记忆系统的历史上下文展示，避免用户误以为旧字段仍参与新记忆链路。
- 支持立即汇总、删除每日总结、导入导出、添加和删除手动记忆。
- “清空全部记忆”确认文案明确覆盖长期画像、每日总结和手动记忆。

### 历史会话单独汇总

- 侧栏最近对话菜单新增“总结到记忆”。
- 完整历史对话页新增“总结到记忆”按钮。
- 点击后仅总结所选会话，不汇总其它会话。
- 提供处理中、成功、无可总结内容和失败提示。

### 前端数据同步

- 汇总、删除、导入、清空和手动记忆修改后刷新对应的 memory/profile/daily 查询缓存。
- 清空全部记忆后立即清除前端长期画像和每日总结缓存。

## 文档组织修改

- `docs` 下的文档按 `product`、`design`、`execution` 分类保存。
- 产品需求、确认决策和验收口径放入 `docs/product`。
- 架构设计、技术决策和接口方案放入 `docs/design`。
- 实施记录、测试结果和变更总结放入 `docs/execution`。
- 后续新增或更新的项目文档默认使用中文；API、代码符号和专有名词可保留英文。

## 后端与架构修改

### v2 记忆模型

新增并接入：

- 每日总结 `DailyPersonSummary`
- 长期画像 `MemoryProfile`
- 来源事件 `MemorySourceEvent`
- 汇总输入 `MemoryRollupInput`
- 用户级文件存储 `MemoryStorageV2`
- 长期画像合并器 `ProfileConsolidator`
- Prompt 注入选择器
- 旧记忆迁移和兼容层

### 汇总触发机制

- 后台 scheduler 周期处理待汇总输入。
- 设置页立即汇总会先调用 `MemoryUpdateQueue.flush_user()`，确保当前用户队列中的会话内容已落盘。
- Flash 直接运行路径补充记忆捕获，避免绕过常规运行链路后出现“暂无可汇总的会话内容”。
- 历史会话接口直接读取 checkpointer 中的最新完整会话消息。

### 单会话增量合并

新增：

```text
POST /api/threads/{thread_id}/memory/rollup
```

单会话汇总采用增量合并：

- 保留当天其它会话已形成的每日总结内容。
- 合并各分类列表并进行精确文本去重。
- 合并 `sourceThreads` 和 `sourceRuns`。
- 同一天同一线程重复点击时直接返回已有总结。
- 汇总成功后重建长期画像。

### 全部清空语义

`DELETE /api/memory` 在 v2 模式下会清除当前用户：

- 长期画像。
- 每日总结。
- rollup 输入。
- 来源与 tombstone 文件。
- 手动记忆。
- 旧版用户级 `memory.json`。
- 旧版 agent 级 `memory.json`。
- 尚未处理的记忆捕获队列。

### 手动记忆保护

长期画像从每日总结重建时，保留来源为 `manual` 的用户手动记忆，避免自动合并覆盖用户主动维护的内容。

## 记忆质量问题与修正

### 发现的问题

一次仅包含“你好”的历史会话汇总后，长期画像中出现：

- `利用AI对业务文档进行总结并输出为Markdown`
- `偏好将文档总结结果保存为Markdown文件`

两条内容相近，但分别进入了使用习惯和偏好分类。

### 根因

- 会话消息中包含注入的旧 `<memory>` 上下文。
- 助手回复主动复述了旧记忆。
- 捕获逻辑将完整会话作为新证据，导致记忆系统总结并强化自己。
- rollup prompt 没有明确要求事实原子化和跨字段不重复。
- 原有去重仅处理完全相同文本，无法识别语义相近表达。

### 已实施修正

- 自动捕获只保留用户真实消息。
- 删除用户消息中的 `<system-reminder>` 和 `<memory>` 注入块。
- 助手回复不再作为用户记忆证据。
- prompt 要求每条事实只表达一个维度，并只进入一个分类。
- prompt 明确区分使用习惯和输出偏好。
- 没有结构化记忆信号时不保存每日总结，避免问候产生空洞记忆。

## 主要代码范围

### 后端

- `backend/app/gateway/app.py`
- `backend/app/gateway/memory_scheduler.py`
- `backend/app/gateway/routers/memory.py`
- `backend/app/gateway/routers/threads.py`
- `backend/packages/harness/deerflow/agents/memory/`
- `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`
- `backend/packages/harness/deerflow/runtime/runs/worker.py`
- `backend/packages/harness/deerflow/config/memory_config.py`
- `config.example.yaml`

### 前端

- `frontend/src/app/workspace/chats/page.tsx`
- `frontend/src/components/workspace/recent-chat-list.tsx`
- `frontend/src/components/workspace/settings/memory-settings-page.tsx`
- `frontend/src/core/memory/`
- `frontend/src/core/i18n/locales/`

### 测试

- `backend/tests/test_memory_v2_architecture.py`
- `backend/tests/test_memory_queue.py`
- `backend/tests/test_memory_router.py`
- `backend/tests/test_threads_router.py`
- `backend/tests/test_run_worker_rollback.py`

## 验证结果

- 记忆架构、队列、路由和线程接口相关测试：`66 passed`。
- Ruff：通过。
- 前端 Prettier：通过。
- 前端 ESLint：通过。
- `git diff --check`：通过。

前端全量 TypeScript 检查仍存在仓库原有问题：

```text
tests/unit/core/files/thread-upload-to-file-item.test.ts
```

该测试使用字符串类型的 `size`，但 `UploadedFileInfo.size` 当前要求数字类型；与本次记忆系统修改无关。

## 当前已知限制

- 自动去重仍以精确文本为主，尚未实现跨日期、跨分类的语义去重。
- 已汇总线程后新增消息不会自动更新当天该线程的记忆。
- 已经由旧污染输入生成的每日总结不会被自动修正，需要删除对应每日总结后重新汇总。
- 每日总结当前使用 UTC 日期作为稳定默认值，尚未按用户时区切分。
- suppression、“不再提及”、来源详情查看和每日总结编辑尚未完成。

## 后续建议

1. 为 rollup 输入增加内容哈希或 checkpoint ID，支持线程内容变化后的安全重新汇总。
2. 增加轻量语义去重或规范化规则，优先处理同一分类内的近义事实。
3. 在设置页展示记忆分类和来源，使用户能区分偏好、使用习惯和近期关注。
4. 支持从设置页强制重新汇总某个会话，并替换该线程此前贡献的内容。
5. 补充用户时区配置，按用户本地日期生成每日总结。
