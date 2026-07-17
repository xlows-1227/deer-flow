# DeerFlow Backend

DeerFlow is a LangGraph-based AI super agent with sandbox execution, persistent memory, and extensible tool integration. The backend enables AI agents to execute code, browse the web, manage files, delegate tasks to subagents, and retain context across conversations - all in isolated, per-thread environments.

---

## Architecture

```
                        ┌──────────────────────────────────────┐
                        │          Nginx (Port 2026)           │
                        │      Unified reverse proxy           │
                        └───────┬──────────────────┬───────────┘
                                │
            /api/langgraph/*    │    /api/* (other)
            rewritten to /api/* │
                                ▼
               ┌────────────────────────────────────────┐
               │        Gateway API (8001)              │
               │        FastAPI REST + agent runtime    │
               │                                        │
               │ Models, MCP, Skills, Memory, Uploads,  │
               │ Artifacts, Threads, Runs, Streaming    │
               │                                        │
               │ ┌────────────────────────────────────┐ │
               │ │ Lead Agent                         │ │
               │ │ Middleware Chain, Tools, Subagents │ │
               │ └────────────────────────────────────┘ │
               └────────────────────────────────────────┘
```

**Request Routing** (via Nginx):
- `/api/langgraph/*` → Gateway LangGraph-compatible API - agent interactions, threads, streaming
- `/api/*` (other) → Gateway API - models, MCP, skills, memory, artifacts, uploads, thread-local cleanup
- `/` (non-API) → Frontend - Next.js web interface

### External API V1

业务系统可以通过用户级 Bearer API Key 调用稳定的 `/api/v1/external/*` 接口。External API Key 只代表所属用户访问外部接口，不能访问模型、Skill、Connector 等管理接口。

使用前需要：

1. 使用浏览器登录会话调用 `POST /api/v1/api-keys/current/rotate`，生成或轮换 API Key；完整 Key 只返回一次。
2. 通过 `PUT /api/v1/api-keys/current/policy` 配置允许调用的 Skill 白名单。
3. 外部系统携带 `Authorization: Bearer dfk_<key_id>_<secret>` 创建 Conversation，再在 Conversation 中创建异步 Run。

主要接口：

```text
GET    /api/v1/external/skills
POST   /api/v1/external/conversations
GET    /api/v1/external/conversations/{conversation_id}
POST   /api/v1/external/conversations/{conversation_id}/runs
GET    /api/v1/external/runs/{run_id}
POST   /api/v1/external/runs/{run_id}/cancel
```

创建新对话并启动异步 Run：

```bash
export DEERFLOW_API_KEY='dfk_<key_id>_<secret>'

curl -X POST 'http://localhost:8001/api/v1/external/conversations' \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H 'Idempotency-Key: create-crm-session-789' \
  -H 'Content-Type: application/json' \
  -d '{"source":"crm","external_conversation_id":"crm-session-789","default_skill":"customer-summary"}'

curl -X POST 'http://localhost:8001/api/v1/external/conversations/<conversation_id>/runs' \
  -H "Authorization: Bearer ${DEERFLOW_API_KEY}" \
  -H 'Idempotency-Key: run-crm-session-789-1' \
  -H 'Content-Type: application/json' \
  -d '{"message":"总结这个客户的历史信息","skill":"customer-summary","mode":"standard"}'
```

相同对话的后续请求继续使用原 `conversation_id`，因此会复用内部 Thread 历史。要开启不继承历史的新对话，应重新调用 Conversation 创建接口。`skill` 只覆盖当前 Run，不会修改 Conversation 的默认 Skill。创建 Conversation 和 Run 时建议始终提供稳定且唯一的 `Idempotency-Key`；同一用户默认最多同时运行 3 个 External Run。

`Idempotency-Key` 只保证同一业务请求不会重复创建 Conversation 或 Run，不保证 Agent 输出内容完全确定。涉及付款、审批、删除等确定性业务操作时，应由业务系统继续执行权限校验、状态机校验和去重，不应仅依赖模型输出触发。

建议在生产环境显式设置稳定的 `EXTERNAL_API_KEY_PEPPER`。更换 Pepper 会导致已有 API Key 全部失效。External API V1 依赖 SQLite 或 PostgreSQL 持久化，在 `database.backend=memory` 模式下关闭失败。

启用 SQLite/PostgreSQL 持久化后，自定义 Agent 的 `setup_agent` / `update_agent` 写入应走异步 Gateway：身份、草稿、Skills 只在单个数据库事务中提交。Gateway 每次构建 Agent 前按 owner 注入当前数据库草稿，将 AGENT.md 与 SOUL.md 组合为可信运行指令，并丢弃请求伪造的 `__agent_*` 内部字段。数据库明确查无该草稿时，迁移窗口内可只读回退到当前 owner 的旧 `SOUL.md` / `config.yaml`；数据库错误、跨 owner 文件和共享目录均不触发回退，旧文件也不再参与双写。同步 `DeerFlowClient` 在该模式下会拒绝自定义 Agent 运行，避免从新事件循环复用 Gateway 所属的异步数据库引擎；无数据库的 CLI/embedded 模式仍支持同步文件读写。Studio 新建草稿默认使用显式空 Skills；对话式/旧导入省略 Skills 时才使用继承模式，发布时会把 owner 当前可选择的 Skills 固化为不可变 revisions。发布服务以单条 SQL 捕获草稿及子表；Skill 文件树经过稳定性复核，connector requirements 直接从最终冻结的 `SKILL.md` bytes 派生，同 checksum revision 的 owner/visibility/caps/content_ref 不变量不一致会 fail closed。发布与对话式 authoring 统一按 identity → draft 加锁，并发编辑时返回 409 且不创建 Release。Connector grant 在草稿保存和发布时都会与权威 Connector type capabilities 求交；PATCH 的 Skill/grant 子项拒绝空值、额外字段和重复项。旧 Agent 导入在一个事务内写 identity、draft 与 Skills，失败后可安全重试。Published-Agent slug 在控制面创建/导入、Gateway assistant 路由和数据库草稿运行时查询中统一使用保留大小写的 `[A-Za-z0-9-]{1,64}` 契约。

Published Agent 公共运行时会从不可变 Skill revision 的 `SKILL.md` 派生 `allowed-tools`，并在 Connector 服务层按 `(connector_id, capability)` 执行 Release 授权，即使运行身份是 owner 也不能绕过。无 `Idempotency-Key` 的 Run 使用服务端唯一 quota attempt id；超时会等待 worker 刷新 token 后再结算，published middleware 同时强制 `max_tokens_per_run`。Agent Key quota override 在写入时仅接受已知字段的正整数。Agent Key、quota reservation、published conversation 与 audit 查询均在仓储层携带 owner scope。

冻结 Skill 的完整 `SKILL.md` 正文也会直接组合进 Published Run 的可信指令；正文与 `allowed-tools` 都来自同一个不可变 revision，跨 owner 的 private revision 会 fail closed。Published 模式禁用 Title/Summarization 辅助模型，并让 token 预算在 loop warning 等请求改写之后执行；计费用量即使在全局 `run_events.track_token_usage=false` 时也强制采集。Quota reserve 事务会在 Run 持久化之前预绑定服务端 `run_id`，随后挂接带有界重试的结算任务；Gateway shutdown 会限时排空，重启与周期恢复会从 pending 绑定记录幂等补写 usage，超过 max-run deadline 的共享数据库 orphan 按 timeout 收敛。如果预绑定后进程在 Run 落库前退出，恢复任务会在 deadline 后确认 Run 不存在，再释放 reservation 与 owner-scoped 未完成幂等 claim。启动阶段取消会删除尚未绑定 worker 的 pending Run，并释放相同资源。

---

## Core Components

### Lead Agent

The single LangGraph agent (`lead_agent`) is the runtime entry point, created via `make_lead_agent(config)`. It combines:

- **Dynamic model selection** with thinking and vision support
- **Middleware chain** for cross-cutting concerns (9 middlewares)
- **Tool system** with sandbox, MCP, community, and built-in tools
- **Subagent delegation** for parallel task execution
- **System prompt** with skills injection, memory context, and working directory guidance

### Middleware Chain

Middlewares execute in strict order, each handling a specific concern:

| # | Middleware | Purpose |
|---|-----------|---------|
| 1 | **ThreadDataMiddleware** | Creates per-thread isolated directories (workspace, uploads, outputs) |
| 2 | **UploadsMiddleware** | Injects newly uploaded files into conversation context |
| 3 | **SandboxMiddleware** | Acquires sandbox environment for code execution |
| 4 | **SummarizationMiddleware** | Reduces context when approaching token limits (optional) |
| 5 | **TodoListMiddleware** | Tracks multi-step tasks in plan mode (optional) |
| 6 | **TitleMiddleware** | Auto-generates conversation titles after first exchange |
| 7 | **MemoryMiddleware** | Queues conversations for async memory extraction |
| 8 | **ViewImageMiddleware** | Injects image data for vision-capable models (conditional) |
| 9 | **ClarificationMiddleware** | Intercepts clarification requests and interrupts execution (must be last) |

### Sandbox System

Per-thread isolated execution with virtual path translation:

- **Abstract interface**: `execute_command`, `read_file`, `write_file`, `list_dir`
- **Providers**: `LocalSandboxProvider` (filesystem) and `AioSandboxProvider` (Docker, in community/). Async runtime paths use async sandbox lifecycle hooks so startup, readiness polling, and release do not block the event loop.
- **Virtual paths**: `/mnt/user-data/{workspace,uploads,outputs}` → thread-specific physical directories
- **Skills path**: `/mnt/skills` → `deer-flow/skills/` directory
- **Skills loading**: Recursively discovers nested `SKILL.md` files under `skills/{public,custom}` and preserves nested container paths
- **File-write safety**: `str_replace` serializes read-modify-write per `(sandbox.id, path)` so isolated sandboxes keep concurrency even when virtual paths match
- **Tools**: `bash`, `ls`, `read_file`, `write_file`, `str_replace` (`write_file` overwrites by default and exposes `append` for end-of-file writes; `bash` is disabled by default when using `LocalSandboxProvider`; use `AioSandboxProvider` for isolated shell access)

### Subagent System

Async task delegation with concurrent execution:

- **Built-in agents**: `general-purpose` (full toolset) and `bash` (command specialist, exposed only when shell access is available)
- **Concurrency**: Max 3 subagents per turn, 15-minute timeout
- **Execution**: Background thread pools with status tracking and SSE events
- **Flow**: Agent calls `task()` tool → executor runs subagent in background → polls for completion → returns result

### Memory System

LLM-powered persistent context retention across conversations:

- **Automatic extraction**: Analyzes conversations for user context, facts, and preferences
- **Structured storage**: User context (work, personal, top-of-mind), history, and confidence-scored facts
- **Debounced updates**: Batches updates to minimize LLM calls (configurable wait time)
- **System prompt injection**: Top facts + context injected into agent prompts
- **Storage**: JSON file with mtime-based cache invalidation

### Tool Ecosystem

| Category | Tools |
|----------|-------|
| **Sandbox** | `bash`, `ls`, `read_file`, `write_file`, `str_replace` |
| **Built-in** | `present_files`, `ask_clarification`, `view_image`, `task` (subagent) |
| **Community** | Tavily (web search), Jina AI (web fetch), Firecrawl (scraping), DuckDuckGo (image search) |
| **MCP** | Any Model Context Protocol server (stdio, SSE, HTTP transports) |
| **Skills** | Domain-specific workflows injected via system prompt |

### Gateway API

FastAPI application providing REST endpoints for frontend integration:

| Route | Purpose |
|-------|---------|
| `GET /api/models` | List available LLM models |
| `GET/PUT /api/mcp/config` | Manage MCP server configurations |
| `GET/PUT /api/skills` | List and manage skills |
| `POST /api/skills/install` | Install skill from `.skill` archive |
| `GET /api/memory` | Retrieve memory data |
| `POST /api/memory/reload` | Force memory reload |
| `GET /api/memory/config` | Memory configuration |
| `GET /api/memory/status` | Combined config + data |
| `GET /api/files` | List current user's file library items |
| `GET /api/files/folders` | List current user's file library folders |
| `POST /api/threads/{id}/uploads` | Upload files (auto-converts PDF/PPT/Excel/Word to Markdown, rejects directory paths, auto-renames duplicate filenames in one request) |
| `GET /api/threads/{id}/uploads/list` | List uploaded files |
| `DELETE /api/threads/{id}` | Delete DeerFlow-managed local thread data after LangGraph thread deletion; unexpected failures are logged server-side and return a generic 500 detail |
| `GET /api/threads/{id}/artifacts/{path}` | Serve generated artifacts |

Notes:
- These APIs are **always scoped to the authenticated user**. Supplying a different `user_id` (e.g. as a query parameter) is rejected with 403.

### IM Channels

The IM bridge supports Feishu, Slack, and Telegram through two compatible paths:

- Legacy channels remain configured in `config.yaml`. They use the JSON conversation mapping store; Slack and Telegram use `runs.wait()`, while legacy Feishu streams and updates one in-thread card.
- Published-Agent Feishu bindings are created by an authenticated Agent owner under `/api/published-agents/{agent_id}/channels`. They are database-backed and execute the binding's immutable Published-Agent Release through resolver and quota policy; they never fall back to the default Agent or legacy JSON mapping.

Database-backed Feishu requires a stable Fernet deployment key. Generate one once and provide the same value to every Gateway replica and restart:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export DEER_FLOW_SECRET_STORE_KEY='<generated-key>'
```

The encrypted credential bundle (`app_secret`, required `verification_token`, and optional `encrypt_key`) is stored below `${DEER_FLOW_HOME:-.deer-flow}/secret-store/feishu/`; SQL rows contain only an opaque `secret_ref`. Do not rotate the deployment key without re-encrypting existing entries. If the key is absent or invalid, the Gateway logs `Published Feishu Supervisor unavailable`; Published-Agent APIs and legacy `config.yaml` channels continue to run, but binding lifecycle routes return 503.

Owner routes support create/list/test/start/stop/restart/credential rotation/delete. A create request uses:

```json
{
  "app_id": "cli_...",
  "app_secret": "...",
  "verification_token": "...",
  "encrypt_key": "..."
}
```

Creating a binding leaves it inactive. `start` returns after the WebSocket ready handshake and a fast local cleanup-backlog projection; sandbox cleanup itself runs in the binding's background coordinator and never delays peer lifecycle operations. Supervisor start/stop/restart/credential-rotation/delete operations share one per-binding lifecycle lock. Physical row deletion happens before that lock is released, startup re-reads listed rows before creating a runtime, and retired keyed locks are reclaimed only after all waiters exit. A pending cleanup outbox keeps the running binding unhealthy, and transient health-persistence failures are retried without stopping cleanup recovery. `DELETE` returns `409` and retains the binding row plus encrypted secret while cleanup is pending. Runtime connection loss marks only that binding unhealthy. Incoming events follow `verify → durable dedup → binding → DB conversation mapping → Published-Agent resolver → cheap input checks → quota reserve → attachment materialization → Run → usage settlement`; private chats isolate by Feishu user, while groups isolate by chat and optional topic.

All database-backed Feishu clients are scheduled on one process-owned SDK event loop because `lark-oapi` 1.x exposes a module-level loop. Each binding still owns and stops only its own connection/tasks, so stopping one binding does not interrupt peers. Event claims require the unforgeable system scope and a persisted Feishu binding before any dedup row is written.

Dynamic binding Runs accept at most 10 inbound files, at most 50 MiB per file, and enforce the Release's aggregate `UTF-8 text + actual attachment bytes` limit. After resolver and quota admission, resources are fetched with authenticated streaming HTTP: connection/read timeouts are 5/10 seconds, the complete download is capped at 60 seconds, `Content-Length` is prechecked, and every raw network chunk is counted before it is written. Sandbox acquisition uses a provider-issued lease handle with a 15-second admission deadline: foreground success explicitly accepts the handle, while late completion is conditionally abandoned by the provider instead of releasing a naked sandbox ID. AIO assigns every backend create an operation token and keeps compensation ownership until the real blocking call finishes, including after the 120-second warning deadline. Late destroy re-enters the same thread/file-lock protocol and preserves capacity already adopted or accepted by a successor; cancellation while waiting for an OS file lock transfers unlock/close to the lock worker instead of closing its live handle. Non-mounted sandbox copies have 60-second per-file and 120-second batch deadlines.

Partial host and remote-sandbox files are removed on rejection, timeout, or cancellation. Critical cleanup tasks are isolated from disposable card/progress tasks: binding stop drains them for up to 2 seconds without cancelling them. Every uncertain cleanup is recorded below `${DEER_FLOW_HOME:-.deer-flow}/published-attachment-cleanup/` as `producer_pending → ready_to_delete → deleting`, with renewable producer leases plus atomic delete claims/fencing. One global pass scans each JSON candidate once within the 10-second total deadline, selects at most 25 claimable jobs across all bindings, runs at most four concurrently, and persists a rotating cursor; live producer/claim leases do not consume the batch and cannot starve ready work. Each delete call has a 2-second deadline and remote deletion is retried three times with bounded backoff. Per-binding recovery starts only after WebSocket ready; a Gateway-level 30-second janitor also recovers jobs for inactive or deleted bindings without Feishu credentials. Cleanup health is committed by generation/CAS against the file-locked durable store, so an older recovery snapshot cannot overwrite a newly persisted backlog. The outbox is removed only after remote and host deletion are confirmed. Host uploads, sandbox acquisition/cache ownership, model allowlist validation, Run/Thread persistence, Run worker context, outputs, and final attachments all run under the trusted Published-Agent owner; a cached thread/owner conflict fails closed, and the caller's ambient ContextVars are restored after success, failure, or cancellation.

Gateway stream consumption is decoupled from Feishu card I/O through a one-item latest-progress queue. Slow intermediate progress may be dropped after the 250 ms drain window, but final values and artifacts are drained independently. Once a quota reservation is bound to a started Run, ordinary release cannot free it: every post-start cancellation/finalization failure becomes detached recovery, while Run cancellation and worker join each have a short cleanup deadline so a non-cooperative worker cannot hold the dispatcher forever.

Gateway startup automatically upgrades persistence to Alembic head `2026_07_14_channel_mappings` (`agent_channels`, `channel_conversation_mappings`, and `channel_event_dedup`). Diagnose an unhealthy binding through `GET .../channels` and `POST .../channels/{binding_id}/test`, then check Gateway logs for the redacted error class. Focused regression:

```bash
uv run pytest tests/test_agent_channels_router.py tests/test_feishu_supervisor.py \
  tests/test_feishu_event_dedup.py tests/test_feishu_websocket_lifecycle.py \
  tests/test_channel_mapping_store.py tests/test_feishu_published_run_flow.py \
  tests/test_feishu_parser.py tests/test_aio_sandbox.py tests/test_aio_sandbox_provider.py \
  tests/test_local_sandbox_provider_mounts.py tests/test_user_context.py \
  tests/test_gateway_services.py -q
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- API keys for your chosen LLM provider

### Installation

```bash
cd deer-flow

# Copy configuration files
cp config.example.yaml config.yaml

# Install backend dependencies
cd backend
make install
```

### Configuration

Edit `config.yaml` in the project root:

```yaml
models:
  - name: gpt-4o
    display_name: GPT-4o
    use: langchain_openai:ChatOpenAI
    model: gpt-4o
    api_key: $OPENAI_API_KEY
    supports_thinking: false
    supports_vision: true

  - name: gpt-5-responses
    display_name: GPT-5 (Responses API)
    use: langchain_openai:ChatOpenAI
    model: gpt-5
    api_key: $OPENAI_API_KEY
    use_responses_api: true
    output_version: responses/v1
    supports_vision: true
```

Set your API keys:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

### Running

**Full Application** (from project root):

```bash
make dev  # Starts Gateway + Frontend + Nginx
```

Access at: http://localhost:2026

**Backend Only** (from backend directory):

```bash
# Gateway API + embedded agent runtime
make dev
```

Direct access: Gateway at http://localhost:8001

---

## Project Structure

```
backend/
├── src/
│   ├── agents/                  # Agent system
│   │   ├── lead_agent/         # Main agent (factory, prompts)
│   │   ├── middlewares/        # 9 middleware components
│   │   ├── memory/             # Memory extraction & storage
│   │   └── thread_state.py    # ThreadState schema
│   ├── gateway/                # FastAPI Gateway API
│   │   ├── app.py             # Application setup
│   │   └── routers/           # 6 route modules
│   ├── sandbox/                # Sandbox execution
│   │   ├── local/             # Local filesystem provider
│   │   ├── sandbox.py         # Abstract interface
│   │   ├── tools.py           # bash, ls, read/write/str_replace
│   │   └── middleware.py      # Sandbox lifecycle
│   ├── subagents/              # Subagent delegation
│   │   ├── builtins/          # general-purpose, bash agents
│   │   ├── executor.py        # Background execution engine
│   │   └── registry.py        # Agent registry
│   ├── tools/builtins/         # Built-in tools
│   ├── mcp/                    # MCP protocol integration
│   ├── models/                 # Model factory
│   ├── skills/                 # Skill discovery & loading
│   ├── config/                 # Configuration system
│   ├── community/              # Community tools & providers
│   ├── reflection/             # Dynamic module loading
│   └── utils/                  # Utilities
├── docs/                       # Documentation
├── tests/                      # Test suite
├── langgraph.json              # LangGraph graph registry for tooling/Studio compatibility
├── pyproject.toml              # Python dependencies
├── Makefile                    # Development commands
└── Dockerfile                  # Container build
```

`langgraph.json` is not the default service entrypoint.  The scripts and Docker
deployments run the Gateway embedded runtime; the file is kept for LangGraph
tooling, Studio, or direct LangGraph Server compatibility.

---

## Configuration

### Main Configuration (`config.yaml`)

Place in project root. Config values starting with `$` resolve as environment variables.

Key sections:
- `models` - LLM configurations with class paths, API keys, thinking/vision flags
- `tools` - Tool definitions with module paths and groups
- `tool_groups` - Logical tool groupings
- `sandbox` - Execution environment provider
- `skills` - Skills directory paths
- `title` - Auto-title generation settings
- `summarization` - Context summarization settings
- `subagents` - Subagent system (enabled/disabled)
- `memory` - Memory system settings (enabled, storage, debounce, facts limits)

Provider note:
- `models[*].use` references provider classes by module path (for example `langchain_openai:ChatOpenAI`).
- If a provider module is missing, DeerFlow now returns an actionable error with install guidance (for example `uv add langchain-google-genai`).

### Extensions Configuration (`extensions_config.json`)

MCP servers and skill states in a single file:

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    },
    "secure-http": {
      "enabled": true,
      "type": "http",
      "url": "https://api.example.com/mcp",
      "oauth": {
        "enabled": true,
        "token_url": "https://auth.example.com/oauth/token",
        "grant_type": "client_credentials",
        "client_id": "$MCP_OAUTH_CLIENT_ID",
        "client_secret": "$MCP_OAUTH_CLIENT_SECRET"
      }
    }
  },
  "skills": {
    "pdf-processing": {"enabled": true}
  }
}
```

### Environment Variables

- `DEER_FLOW_CONFIG_PATH` - Override config.yaml location
- `DEER_FLOW_EXTENSIONS_CONFIG_PATH` - Override extensions_config.json location
- Model API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, etc.
- Tool API keys: `TAVILY_API_KEY`, `GITHUB_TOKEN`, etc.

### LangSmith Tracing

DeerFlow has built-in [LangSmith](https://smith.langchain.com) integration for observability. When enabled, all LLM calls, agent runs, tool executions, and middleware processing are traced and visible in the LangSmith dashboard.

**Setup:**

1. Sign up at [smith.langchain.com](https://smith.langchain.com) and create a project.
2. Add the following to your `.env` file in the project root:

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

**Legacy variables:** The `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, and `LANGCHAIN_ENDPOINT` variables are also supported for backward compatibility. `LANGSMITH_*` variables take precedence when both are set.

### Langfuse Tracing

DeerFlow also supports [Langfuse](https://langfuse.com) observability for LangChain-compatible runs.

Add the following to your `.env` file:

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

If you are using a self-hosted Langfuse deployment, set `LANGFUSE_BASE_URL` to your Langfuse host.

### Dual Provider Behavior

If both LangSmith and Langfuse are enabled, DeerFlow initializes and attaches both callbacks so the same run data is reported to both systems.

If a provider is explicitly enabled but required credentials are missing, or the provider callback cannot be initialized, DeerFlow raises an error when tracing is initialized during model creation instead of silently disabling tracing.

**Docker:** In `docker-compose.yaml`, tracing is disabled by default (`LANGSMITH_TRACING=false`). Set `LANGSMITH_TRACING=true` and/or `LANGFUSE_TRACING=true` in your `.env`, together with the required credentials, to enable tracing in containerized deployments.

---

## Development

### Commands

```bash
make install    # Install dependencies
make dev        # Run Gateway API + embedded agent runtime (port 8001)
make gateway    # Run Gateway API without reload (port 8001)
make lint       # Run linter (ruff)
make format     # Format code (ruff)
```

### Code Style

- **Linter/Formatter**: `ruff`
- **Line length**: 240 characters
- **Python**: 3.12+ with type hints
- **Quotes**: Double quotes
- **Indentation**: 4 spaces

### Testing

```bash
uv run pytest
```

---

## Technology Stack

- **LangGraph** (1.0.6+) - Agent framework and multi-agent orchestration
- **LangChain** (1.2.3+) - LLM abstractions and tool system
- **FastAPI** (0.115.0+) - Gateway REST API
- **langchain-mcp-adapters** - Model Context Protocol support
- **agent-sandbox** - Sandboxed code execution
- **markitdown** - Multi-format document conversion
- **tavily-python** / **firecrawl-py** - Web search and scraping

---

## Documentation

- [Configuration Guide](docs/CONFIGURATION.md)
- [Architecture Details](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [File Upload](docs/FILE_UPLOAD.md)
- [Path Examples](docs/PATH_EXAMPLES.md)
- [Context Summarization](docs/summarization.md)
- [Plan Mode](docs/plan_mode_usage.md)
- [Setup Guide](docs/SETUP.md)

---

## License

See the [LICENSE](../LICENSE) file in the project root.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.
