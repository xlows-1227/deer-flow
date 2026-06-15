# 从 Mini-Agent 迁移聊天记录到 deer-flow

把 [MiniMax-AI/Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) 的会话历史
批量导入 deer-flow，变成 deer-flow 里可继续对话的 thread。

## 背景：Mini-Agent 的"聊天记录"存在哪？

Mini-Agent **没有对话数据库**。它的历史记录只有一处可靠来源：

```
~/.mini-agent/log/agent_run_YYYYMMDD_HHMMSS.log
```

每启动一次 `mini-agent`（交互或 `--task`）都会生成一个这样的日志文件，
里面按 `[N] REQUEST / RESPONSE / TOOL_RESULT` 分段记录了这次会话的全部
LLM 请求、回复、工具调用。**一个 `.log` 文件 = 一次完整会话。**

其它文件不是迁移源：

| 路径 | 内容 | 能否迁移 |
|---|---|---|
| `agent.messages`（内存） | 当前会话消息 | ❌ 进程退出即失 |
| **`~/.mini-agent/log/*.log`** | **完整对话过程** | ✅ **唯一来源** |
| `~/.mini-agent/.history` | prompt_toolkit 命令行历史（只有输入） | ❌ 无回复 |
| `<workspace>/.agent_memory.json` | SessionNoteTool 的零散笔记 | ❌ 非对话 |

本脚本解析这些 `.log` 文件，重建消息序列，通过 deer-flow 的 API 灌进去。

## 它做了什么

对每个 `.log` 文件执行三步：

1. **解析**：取出日志里**最后一个 `[N] REQUEST` 块**——它包含这一整段对话
   `self.messages` 的完整快照（REQUEST 块是单调增长的，最后一个就是全部）。
2. **转换**：把 Mini-Agent 的消息转成 LangGraph 消息字典：
   - `role: user` → `type: "human"`
   - `role: assistant` → `type: "ai"`，并把工具调用从 OpenAI 格式
     (`function.name`/`function.arguments`) 转成 LangChain 格式
     (`name`/`args`)
   - `role: tool` → `type: "tool"`，保留 `tool_call_id`；缺失时自动按顺序
     绑定到前面 assistant 的 tool_call
   - `role: system` → 丢弃（deer-flow 运行时自己注入 system prompt）
   - assistant 的 `thinking` 保留到 `additional_kwargs.reasoning`
3. **导入**：
   - `POST /api/threads` 建空 thread
   - `POST /api/threads/{id}/state`，body 为
     `{"values": {"messages": [...], "title": "..."}}`，一次性写入消息和标题

标题取自该会话第一条用户消息（截断到 60 字符）。

> 为什么走 API 而不直接写 `deerflow.db`？
> deer-flow 的消息存在 LangGraph 的 checkpoint blob 里（`checkpoints` /
> `checkpoint_blobs` 表），是 LangGraph 内部序列化格式，手动重建极易出错。
> 走 API 让 deer-flow 自己生成 checkpoint，格式绝对正确。

## 前置条件

1. **deer-flow 正在运行**，且你知道它的访问地址（默认
   `http://localhost:3000`）。启动方式见 deer-flow 根目录 README 的
   Quick Start。
2. **Mini-Agent 的日志目录**存在（默认 `~/.mini-agent/log`），里面有
   `.log` 文件。
3. Python 3.10+（脚本只用标准库，**无需安装任何依赖**）。

## 用法

所有命令在 `scripts/migrate-from-mini-agent/` 目录下执行。

### 1) 先试跑（强烈建议）

解析 + 转换但不写服务器，检查会话数、消息数、标题是否合理：

```bash
python migrate.py --logs ~/.mini-agent/log --dry-run
```

输出示例：

```
DRY-RUN: 137 file(s) in /home/you/.mini-agent/log
  (skipping 0 already-imported from migration-state.json)

  · agent_run_20260613_103000.log msgs=12 «写一个 Python 冒泡排序»
  · agent_run_20260613_145500.log msgs=5  «帮我分析这段日志»
  ...
Done.
  processed : 137
  dry-run   : 137
```

### 2) 只迁 1 条，到 deer-flow 前端确认效果

```bash
python migrate.py --logs ~/.mini-agent/log \
  --base-url http://localhost:3000 --limit 1
```

打开 deer-flow 前端，找到刚导入的 thread（标题就是第一条用户消息），
点进去确认：消息顺序、工具调用配对、中文显示都正常。

### 3) 全量迁移

```bash
python migrate.py --logs ~/.mini-agent/log \
  --base-url http://localhost:3000
```

支持断点续传：已成功导入的文件记录在 `migration-state.json` 里，
中途挂掉或 Ctrl+C 后**重跑同一命令**会自动跳过已完成的，从断点继续。

失败的文件记在 `migration-failures.json`，同样重跑即可重试（失败文件不在
state 里）。

## 参数一览

| 参数 | 默认 | 说明 |
|---|---|---|
| `--logs` | `~/.mini-agent/log` | Mini-Agent 日志目录 |
| `--base-url` | 必填（除非 `--dry-run`） | deer-flow 地址，如 `http://localhost:3000` |
| `--auth-token` | 无 | 若网关要求鉴权，传 Bearer token |
| `--dry-run` | off | 只解析转换，不写服务器 |
| `--limit N` | 全部 | 只处理前 N 个文件（冒烟测试） |
| `--state-file` | `./migration-state.json` | 断点续传状态文件 |
| `--failures-file` | `./migration-failures.json` | 失败记录文件 |
| `--reset-state` | off | 忽略已有 state，全部重导（会重复） |
| `--delay S` | 0 | 每个文件之间休眠 S 秒（限流用） |

## 文件说明

```
scripts/migrate-from-mini-agent/
├── migrate.py          # 入口：CLI + 批处理 + 断点续传
├── log_parser.py       # 解析 .log 文件 → Mini-Agent 消息列表
├── converter.py        # Mini-Agent 消息 → LangGraph 消息字典
└── deerflow_client.py  # deer-flow threads API 同步客户端（stdlib）
```

## 已知限制

- **会话摘要**：Mini-Agent 在 token 超过 80000 时会触发历史摘要
  (`_summarize_messages`)，把中间过程压缩成 summary 文本。发生摘要后，
  最后一个 REQUEST 块里的消息已经是摘要后的版本——中间的工具调用细节会丢失，
  但用户意图和最终结果都在。这是源数据的固有限制，无法从日志恢复已丢弃的内容。
- **按文件粒度**：一个 `.log` = 一个 thread。如果某次 `mini-agent` 会话里你
  用了 `/clear` 开新话题，两段对话会被合并进同一个 thread（Mini-Agent 的日志
  不区分 clear 边界）。
- **只导历史，不续聊**：导入后这些 thread 是"快照"，可以在 deer-flow 里继续
  对话，但原 Mini-Agent 的工具环境（workspace 文件等）不会一起迁移。
