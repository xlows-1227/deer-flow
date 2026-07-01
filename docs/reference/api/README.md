# DeerFlow Gateway 接口文档（按模块）

> 本目录按后端 `app/gateway/routers/` 路由模块组织，自动生成自 FastAPI OpenAPI 规范（`create_app().openapi()`）。
> 如需修改接口，请调整对应路由与 Pydantic 模型后重新生成；不要直接手改本目录的接口清单与字段表。

## 通用约定

- **统一入口**：生产/开发均通过 Nginx（默认端口 `2026`）访问。`/api/langgraph/*` 转发到内嵌 LangGraph 运行时，其余 `/api/*` 转发到 Gateway REST API。
- **基础地址**：`http(s)://<host>:2026`，下文示例以 `${DEERFLOW_BASE_URL}` 表示。
- **认证**：除 `健康检查`、`认证`、`外部 API V1` 外，绝大多数接口要求已登录的浏览器会话（Cookie）；写操作还需 CSRF 令牌（Double Submit Cookie，由前端自动携带）。`外部 API V1` 使用 `Authorization: Bearer <API_KEY>` 的用户级 API Key，且仅可访问 `/api/v1/external/*`。
- **请求/响应格式**：有请求体的接口使用 `application/json`（文件上传使用 `multipart/form-data`）；响应体默认 `application/json`。
- **错误处理**：`/api/v1/external/*` 返回统一错误信封 `{"error": {code, message, request_id, details}}`；其余接口遵循 FastAPI 默认错误格式（`detail` 字段）。
- **请求 ID**：响应头 `X-Request-ID`，便于问题定位。
- **交互式文档**：开发环境可访问 `/docs`（Swagger）与 `/redoc`；设置环境变量 `GATEWAY_ENABLE_DOCS=false` 可在生产关闭。

## 模块索引

| 模块 | 文档 | 基础路径 | 端点数 | 认证 |
| --- | --- | --- | :---: | --- |
| 健康检查 Health | [health.md](./health.md) | `/health` | 1 | 公开接口，无需认证。 |
| 认证 Auth | [auth.md](./auth.md) | `/api/v1/auth` | 9 | 部分接口公开（登录/注册/初始化/OAuth 回调），部分需要已登录会话（详见各接口说明）。 |
| API Key 管理 API Keys | [api-keys.md](./api-keys.md) | `/api/v1/api-keys` | 4 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 外部 API V1 External API | [external-api.md](./external-api.md) | `/api/v1/external` | 6 | 使用 Bearer API Key 认证：请求头 `Authorization: Bearer <API_KEY>`，仅能访问 `/api/v1/external/*`。 |
| 模型 Models | [models.md](./models.md) | `/api/models` | 2 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 自定义模型 Custom Models | [user-models.md](./user-models.md) | `/api/models/custom` | 4 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| MCP 配置 MCP | [mcp.md](./mcp.md) | `/api/mcp` | 5 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 工具配置 Tools | [tools.md](./tools.md) | `/api/tools` | 2 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 记忆 Memory | [memory.md](./memory.md) | `/api/memory` | 18 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 技能 Skills | [skills.md](./skills.md) | `/api/skills` | 25 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 智能体 Agents | [agents.md](./agents.md) | `/api/agents` | 8 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 会话 Threads | [threads.md](./threads.md) | `/api/threads` | 10 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 会话内运行 Thread Runs | [thread-runs.md](./thread-runs.md) | `/api/threads/{thread_id}/runs` | 13 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 无状态运行 Stateless Runs | [runs.md](./runs.md) | `/api/runs` | 4 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 反馈 Feedback | [feedback.md](./feedback.md) | `/api/threads/{thread_id}/runs/{run_id}/feedback` | 6 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 上传 Uploads | [uploads.md](./uploads.md) | `/api/threads/{thread_id}/uploads` | 4 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 文件库 Files | [files.md](./files.md) | `/api/files` | 7 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 生成物 Artifacts | [artifacts.md](./artifacts.md) | `/api/threads/{thread_id}/artifacts` | 1 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 建议问题 Suggestions | [suggestions.md](./suggestions.md) | `/api/threads/{thread_id}/suggestions` | 1 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 分享 Shares | [shares.md](./shares.md) | `/api/threads · /api/share` | 2 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 定时任务 Scheduler | [scheduler.md](./scheduler.md) | `/api/scheduler` | 9 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| IM 渠道 Channels | [channels.md](./channels.md) | `/api/channels` | 2 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| 连接器平台 Connectors | [connectors.md](./connectors.md) | `/api/connectors · /api/connector-types` | 24 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |
| Assistants 兼容 Assistants Compat | [assistants-compat.md](./assistants-compat.md) | `/api/assistants` | 4 | 需要已登录的浏览器会话（Cookie）。`POST`/`PUT`/`PATCH`/`DELETE` 等写操作还需携带 CSRF 令牌（Double Submit Cookie，前端框架自动处理）。 |

## 相关文档

- 外部 API 接入手册（业务方接入用）：[../EXTERNAL_API_V1_zh.md](../EXTERNAL_API_V1_zh.md)
- 外部 API 机器可读规范：[../external-api-v1.openapi.yaml](../external-api-v1.openapi.yaml)
- 外部 API 测试手册：[../EXTERNAL_API_V1_TEST_MANUAL_zh.md](../EXTERNAL_API_V1_TEST_MANUAL_zh.md)
