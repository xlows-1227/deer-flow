# External API V1 实施记录

## 1. 实施状态

- 日期：2026-06-08
- 状态：已完成后端实现，等待评审与部署
- 对应设计：`docs/design/2026-06-08-external-api-bearer-key-design.md`
- 对应计划：`docs/execution/plans/2026-06-08-external-api-v1.md`

## 2. 已实现能力

- 用户级 Bearer API Key 的生成、查询、轮换、策略更新和吊销。
- API Key 使用 HMAC-SHA256 与服务端 Pepper 保存，不持久化明文。
- API Key 仅可认证 `/api/v1/external/*`，不能访问现有管理接口。
- External API Key 成功认证后映射到现有用户上下文，并仅在认证成功后跳过 CSRF。
- 查询当前 Key 可调用的 Skill，并在每次 Run 中显式指定一个 Skill。
- 创建和查询公开 Conversation；公开 `conversation_id` 映射内部 Thread，不暴露 `thread_id`。
- 使用相同 `conversation_id` 创建后续 Run，从而继续历史对话。
- 创建、查询和取消异步 External Run。
- Conversation 与 Run 创建支持 `Idempotency-Key`，包含并发抢占、冲突检测和过期重用。
- 每个用户默认最多同时运行 3 个活跃 Run；同一 Conversation 复用现有拒绝并发策略。
- External API 响应和审计包含请求 ID；审计仅记录元数据，不记录 Key、消息、回答或凭据。
- SQLite/PostgreSQL 新增 API Key、Conversation、幂等和审计表及迁移。

## 3. 接口清单

浏览器会话与 CSRF 保护：

```text
GET    /api/v1/api-keys/current
POST   /api/v1/api-keys/current/rotate
PUT    /api/v1/api-keys/current/policy
DELETE /api/v1/api-keys/current
```

Bearer API Key：

```text
GET  /api/v1/external/skills
POST /api/v1/external/conversations
GET  /api/v1/external/conversations/{conversation_id}
POST /api/v1/external/conversations/{conversation_id}/runs
GET  /api/v1/external/runs/{run_id}
POST /api/v1/external/runs/{run_id}/cancel
```

## 4. 部署配置

- 生产环境必须设置稳定的 `EXTERNAL_API_KEY_PEPPER`，长度至少 32 个字符。
- 更换 Pepper 会使已有 API Key 全部失效。
- External API V1 依赖 SQLite 或 PostgreSQL；内存数据库模式下关闭失败。
- 部署时应执行新增 Alembic 迁移 `2026_06_08_external_api_v1`。

## 5. 验证结果

- External API 专项测试：`70 passed`。
- 直接影响范围回归测试：`167 passed`。
- 本次修改文件定向 Ruff 检查：通过。
- 前端相关文件 ESLint 与 Prettier 检查：通过。
- `app` 与 `packages` Python 编译检查：通过。
- 阶段性全量后端测试：`3748 passed, 33 skipped, 91 failed`；随后新增或调整的 External API 用例已通过专项与直接影响范围回归验证。

全量失败未包含 External API 专项用例，主要来自仓库现有 Windows 路径与符号链接差异、SQLite 文件句柄、日志捕获、缺失脚本和已有 Run 排序行为。本次实施未修改这些失败对应的无关模块。

## 6. 首版限制

- 不提供同步消息接口、SSE 事件流、历史消息分页、文件上传、生成物下载或 Webhook。
- 不提供前端 API Key 设置页面。
- 每个用户只允许一把状态为 `active` 的 API Key。
- 应用层按 `Content-Length` 拒绝超过 256 KB 的 External API 请求；部署层仍应配置一致或更严格的请求体上限。
- 首版不在应用进程内实现分布式请求频率限流；生产环境应由 Nginx 或 API Gateway 按 Key/IP 配置频率限制。
- `Idempotency-Key` 只防止重复创建，不保证 Agent 输出完全确定；确定性业务操作仍由业务系统执行状态机、权限和去重校验。
