# Mini-Agent 聊天记录迁移到 DeerFlow 指南

> 把 [MiniMax-AI/Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) 的会话历史批量导入 deer-flow，变成 deer-flow 里可继续对话的 thread。
>
> 迁移脚本位于：`scripts/migrate-from-mini-agent/`
>
> 适用场景：你已经用 Mini-Agent 跑过上百段对话，现在想切到 deer-flow 继续用，但不想丢掉历史记录。

---

## 目录

- [一、背景：两个项目的存储机制](#一背景两个项目的存储机制)
  - [1.1 Mini-Agent 的聊天记录存在哪](#11-mini-agent-的聊天记录存在哪)
  - [1.2 DeerFlow 的聊天记录存在哪](#12-deerflow-的聊天记录存在哪)
- [二、迁移方案总览](#二迁移方案总览)
- [三、前置准备](#三前置准备)
- [四、操作步骤](#四操作步骤)
  - [4.1 第一步：试跑（强烈建议）](#41-第一步试跑强烈建议)
  - [4.2 第二步：只导 1 条并验证](#42-第二步只导-1-条并验证)
  - [4.3 第三步：全量迁移](#43-第三步全量迁移)
- [五、参数说明](#五参数说明)
- [六、故障排查](#六故障排查)
- [七、已知限制](#七已知限制)
- [八、技术实现说明](#八技术实现说明)
- [九、文件清单](#九文件清单)

---

## 一、背景：两个项目的存储机制

理解这段背景，你就能判断迁移过程中遇到的问题出在哪一环。

### 1.1 Mini-Agent 的聊天记录存在哪

Mini-Agent **没有对话数据库**。它只有三处跟"历史"相关的文件：

| 路径 | 内容 | 能否作为迁移源 |
|---|---|---|
| `agent.messages`（进程内存） | 当前会话的消息列表 | ❌ 进程退出即丢失 |
| **`~/.mini-agent/log/agent_run_YYYYMMDD_HHMMSS.log`** | **每次启动的完整对话过程** | ✅ **唯一可靠来源** |
| `~/.mini-agent/.history` | prompt_toolkit 命令行历史（只有用户输入） | ❌ 没有回复内容 |
| `<workspace>/.agent_memory.json` | SessionNoteTool 记的零散笔记 | ❌ 不是对话 |

**关键结论：你的"上百条会话" = `~/.mini-agent/log/` 目录下的上百个 `.log` 文件，每个文件 = 一次完整对话。**

日志文件是人类可读的文本，内部按 `[N] REQUEST / RESPONSE / TOOL_RESULT` 分段：

```
[1] REQUEST        ← self.messages 的完整快照（此刻整段对话）
{ "messages": [ {role:system...}, {role:user, "写个排序"} ], "tools": [...] }

[2] RESPONSE       ← 这一步 AI 的回复
{ "content": "...", "thinking": "...", "tool_calls": [...], "finish_reason": "..." }

[3] TOOL_RESULT    ← 工具执行结果
{ "tool_name": "write_file", "arguments": {...}, "success": true, "result": "..." }

[4] REQUEST        ← 下一步的完整快照（包含上面新增的消息）
...
```

REQUEST 块是**单调增长**的——每一步都包含前面所有消息。所以**最后一个 REQUEST 块就是整段对话的完整内容**，这是解析日志的核心依据。

> Windows 上 `~` 即 `C:\Users\<你的用户名>\`，也就是 `%USERPROFILE%\.mini-agent\log\`。

### 1.2 DeerFlow 的聊天记录存在哪

DeerFlow 的存储分两层（都在同一个 SQLite 文件 `backend/.deer-flow/data/deerflow.db`）：

| 存储层 | 存什么 | 谁管理 |
|---|---|---|
| `threads_meta` 表 | 会话目录：thread_id、标题、状态、所属 agent、时间戳 | deer-flow 自己的 ORM |
| **LangGraph checkpointer**（`checkpoints` / `checkpoint_blobs` 表） | **真正的消息内容**，以 LangChain message 对象序列化后存进 checkpoint blob | LangGraph 内部 |

**关键坑点：** deer-flow 的消息**没有独立的 messages 表**，而是藏在 LangGraph 的 checkpoint blob 里，是 LangGraph 的内部序列化格式。手动拼这个 blob 极易出错，所以**必须走 API 让 deer-flow 自己生成 checkpoint**，不能直接写库。

写入入口是 `POST /api/threads/{thread_id}/state`，body 传 `{"values": {"messages": [...], "title": "..."}}`，deer-flow 会把这些消息原样塞进 checkpoint 并同步标题到 `threads_meta`。

---

## 二、迁移方案总览

```
~/.mini-agent/log/*.log          deer-flow thread
        │                              ▲
        ▼                              │
  ┌───────────┐    ┌───────────┐    ┌──┴──────────┐
  │ log_parser│ →  │ converter │ →  │deerflow_client│
  └───────────┘    └───────────┘    └──────────────┘
   解析 .log       转成 LangGraph      调 API 写入
   → 消息序列       消息字典            (建 thread + 灌消息)
```

每个 `.log` 文件经过：

1. **解析**：取出最后一个 `[N] REQUEST` 块的 messages 数组。
2. **转换**：Mini-Agent 消息 → LangGraph 消息字典（详见下表）。
3. **导入**：
   - `POST /api/threads` 建空 thread，拿到 thread_id
   - `POST /api/threads/{id}/state`，body `{"values": {"messages": [...], "title": "..."}}` 一次性写入消息和标题

**消息格式映射：**

| Mini-Agent | DeerFlow (LangGraph) | 转换要点 |
|---|---|---|
| `role: "user"` | `type: "human"` | content 直接搬 |
| `role: "assistant"` | `type: "ai"` | content 搬；`thinking` 存入 `additional_kwargs.reasoning` |
| assistant 的 `tool_calls` | AIMessage 的 `tool_calls` | ⚠️ 格式要转：`function.name`→`name`、`function.arguments`→`args` |
| `role: "tool"` | `type: "tool"` | 保留 `tool_call_id`、`name`；缺失时自动按序绑定 |
| `role: "system"` | 丢弃 | deer-flow 运行时自己注入 system prompt，导入会冲突 |

标题取自该会话第一条用户消息（截断到 60 字符）。

---

## 三、前置准备

1. **DeerFlow 正在运行**，且你知道访问地址（默认 `http://localhost:3000`）。启动方式见根目录 README 的 Quick Start。
2. **Mini-Agent 日志目录存在**（默认 `~/.mini-agent/log`），里面有 `.log` 文件。可以先确认一下：
   ```bash
   # Windows cmd
   dir "%USERPROFILE%\.mini-agent\log\*.log"
   ```
3. **Python 3.10+**。脚本只用标准库，**无需安装任何额外依赖**。

---

## 四、操作步骤

所有命令在 `scripts/migrate-from-mini-agent/` 目录下执行：

```bash
cd D:\cursorcode\deer-flow\scripts\migrate-from-mini-agent
```

### 4.1 第一步：试跑（强烈建议）

解析 + 转换但不写服务器，检查会话数、消息数、标题是否合理：

```bash
python migrate.py --logs "%USERPROFILE%\.mini-agent\log" --dry-run
```

预期输出类似：

```
DRY-RUN: 137 file(s) in C:\Users\你\.mini-agent\log
  (skipping 0 already-imported from migration-state.json)

  · agent_run_20260613_103000.log msgs=12 «写一个 Python 冒泡排序»
  · agent_run_20260613_145500.log msgs=5  «帮我分析这段日志»
  ...
Done.
  processed : 137
  dry-run   : 137
```

**检查点：** 文件数对不对？每个文件的消息数合理吗？标题是不是对应的内容？如果有文件显示 `skipped` 或 `error`，先看 [故障排查](#六故障排查)。

### 4.2 第二步：只导 1 条并验证

```bash
python migrate.py --logs "%USERPROFILE%\.mini-agent\log" ^
  --base-url http://localhost:3000 --limit 1
```

然后打开 deer-flow 前端，找到刚导入的 thread（标题就是第一条用户消息），点进去确认：

- ✅ 消息顺序正确（user → assistant → tool → user ...）
- ✅ 工具调用和工具返回正确配对显示
- ✅ 中文内容正常无乱码
- ✅ 标题正确

> ⚠️ 这一步很重要。我只在本地用样本日志验证过解析和转换逻辑，**真实写库那一跳没有连真 deer-flow 跑过**。先导 1 条确认无误，再全量。

### 4.3 第三步：全量迁移

```bash
python migrate.py --logs "%USERPROFILE%\.mini-agent\log" ^
  --base-url http://localhost:3000
```

支持断点续传：已成功导入的文件记录在 `migration-state.json` 里。中途挂掉或 Ctrl+C 后**重跑同一命令**会自动跳过已完成的，从断点继续。失败的文件记在 `migration-failures.json`，重跑即可自动重试（失败文件不在 state 里）。

---

## 五、参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--logs` | `~/.mini-agent/log` | Mini-Agent 日志目录 |
| `--base-url` | 必填（除非 `--dry-run`） | deer-flow 地址，如 `http://localhost:3000` |
| `--auth-token` | 无 | 若网关要求鉴权，传 Bearer token |
| `--dry-run` | 关 | 只解析转换，不写服务器 |
| `--limit N` | 全部 | 只处理前 N 个文件（冒烟测试用） |
| `--state-file` | `./migration-state.json` | 断点续传状态文件 |
| `--failures-file` | `./migration-failures.json` | 失败记录文件 |
| `--reset-state` | 关 | 忽略已有 state，全部重导（会产生重复 thread） |
| `--delay S` | 0 | 每个文件之间休眠 S 秒（限流用） |

**常用组合：**

```bash
# 重试上次失败的
python migrate.py --logs ... --base-url ...

# 完全重来（删掉旧数据后）
python migrate.py --logs ... --base-url ... --reset-state

# 慢一点，避免给服务器压力
python migrate.py --logs ... --base-url ... --delay 0.5
```

---

## 六、故障排查

### 6.1 试跑时大量文件显示 `skipped: no user/assistant messages found`

可能原因：
- 该会话只打了招呼没真正对话（正常，可忽略）
- 日志文件格式异常（Mini-Agent 老版本格式不同）。把该 `.log` 文件发我看看。

### 6.2 报错 `create_thread returned no thread_id`

deer-flow 服务没正常响应。检查：
- `--base-url` 是否正确
- deer-flow 是否在运行（浏览器打开 `http://localhost:3000` 能不能访问）
- 直接测一下 API：`curl http://localhost:3000/api/threads/search -X POST -H "Content-Type: application/json" -d "{}"`

### 6.3 报错 `HTTP 404` / `network error`

地址或端口不对。deer-flow 的 API 走 `/api/...` 前缀，确认你填的 `--base-url` 能拼出 `http://你的地址/api/threads`。

### 6.4 导入成功，但前端看不到消息

可能原因：
- deer-flow 前端有缓存，刷新一下页面
- 消息确实写进去了但格式 deer-flow 前端不认。在浏览器开发者工具里看 `GET /api/threads/{id}/state` 的返回，确认 `values.messages` 是个数组且每条有 `type`/`content`。把返回内容发我。

### 6.5 工具调用和返回没配对显示

脚本会自动修复缺失的 `tool_call_id`，但少数情况（比如 tool 消息比 assistant 的 tool_call 还多）可能修不好。把那条 thread 的 messages JSON 发我。

### 6.6 想清空重导

deer-flow 端删除 thread（在前端删，或调 `DELETE /api/threads/{id}`），然后：
```bash
python migrate.py --logs ... --base-url ... --reset-state
```

---

## 七、已知限制

1. **会话摘要导致的内容丢失**
   Mini-Agent 在 token 超过 80000 时会触发历史摘要（`_summarize_messages`），把中间的工具调用过程压缩成一段 summary 文本。发生摘要后，最后一个 REQUEST 块里的消息已经是摘要后的版本——中间的工具调用细节会丢失，但用户意图和最终结果都在。**这是源数据的固有限制，无法从日志恢复已丢弃的内容。**

2. **按文件粒度，不识别 `/clear` 边界**
   一个 `.log` = 一个 thread。如果某次 Mini-Agent 会话里你用了 `/clear` 开了新话题，两段对话会被合并进同一个 thread（日志不区分 clear 边界）。

3. **只迁历史，不迁环境**
   导入后这些 thread 是"快照"，可以在 deer-flow 里继续对话，但原 Mini-Agent 的工具环境（workspace 里的文件等）不会一起迁移。

4. **系统提示词丢弃**
   Mini-Agent 的 system message 不导入（deer-flow 运行时会注入自己的 system prompt）。这是有意为之，避免冲突。

---

## 八、技术实现说明

给想理解原理或二次开发的人。

### 8.1 为什么走 API 而不直接写 `deerflow.db`

deer-flow 的消息存在 LangGraph 的 checkpoint blob 里（`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 表）。这些 blob 是 LangGraph 内部的序列化格式（pickle/protobuf 混合），手动重建极易导致：
- blob 版本不匹配，读取时反序列化失败
- `channel_values` 结构不对，前端渲染崩溃
- checkpoint 元数据（step、writes、parents）缺失，历史回溯异常

走 `POST /api/threads/{id}/state` 让 deer-flow 的 `update_thread_state` 处理器自己生成 checkpoint，格式绝对正确。代价是慢一点（每条对话一次 HTTP 往返），但上百条完全可接受。

### 8.2 消息字典格式

转换后的消息是 LangChain 的 **dict 表示**（即 `BaseMessage.model_dump()` 的输出），而不是 LangChain 对象实例。这是因为 deer-flow 的 `serialize_channel_values` 对 dict 原样返回，对 LangChain 对象才调 `model_dump()`——存 dict 进去，读出来还是 dict，前端能直接渲染。

示例：
```json
{
  "type": "ai",
  "content": "好的，我来帮你写。",
  "id": "mig-ai-0002-4b3e385f",
  "tool_calls": [
    {"name": "write_file", "args": {"path": "bubble.py"}, "id": "call_abc123", "type": "tool_call"}
  ],
  "additional_kwargs": {"reasoning": "用户要冒泡排序..."}
}
```

### 8.3 tool_call_id 自动修复

Mini-Agent 老版本日志里，tool 消息有时不带 `tool_call_id`。但 LangGraph 要求每个 ToolMessage 必须引用一个真实的 tool_call id，否则前端配对失败。脚本的 `_repair_tool_call_ids` 会把缺失 id 的 tool 消息按顺序绑定到前一个 assistant message 的 tool_call id 上。

### 8.4 已验证的内容

| 验证项 | 状态 |
|---|---|
| 日志解析（含中文、工具调用、多轮） | ✅ 用样本日志跑通 |
| 消息格式转换（含 tool_calls 格式） | ✅ 输出符合 LangChain dict 规范 |
| tool_call_id 缺失修复 | ✅ 边界用例通过 |
| dry-run 端到端 | ✅ 跑通 |
| 真实写库（连真 deer-flow） | ⚠️ **未验证**，请务必先 `--limit 1` 试 |

---

## 九、文件清单

```
scripts/migrate-from-mini-agent/
├── migrate.py          # 入口：CLI + 批处理 + 断点续传 + 失败重试
├── log_parser.py       # 解析 .log 文件 → Mini-Agent 消息列表
├── converter.py        # Mini-Agent 消息 → LangGraph 消息字典
├── deerflow_client.py  # deer-flow threads API 同步客户端（纯 stdlib）
└── README.md           # 脚本目录内的简版说明（本文是更完整的版本）
```

运行后还会生成（可删除，不影响功能）：
```
migration-state.json       # 断点续传状态（记录已导入的文件名）
migration-failures.json    # 失败记录（供重试参考）
```

---

如有问题，把出错的 `.log` 文件和报错信息一起反馈，便于定位。
