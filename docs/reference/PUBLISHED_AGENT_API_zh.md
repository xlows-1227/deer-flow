# Published Agent API 外部系统接入指南

本文面向需要从 CRM、工单系统、企业门户、自动化服务等外部系统调用 DeerFlow 已发布 Agent 的开发者。

## 1. 接入流程概览

```text
在 Agent Studio 发布 Agent
        ↓
在“集成”中创建 Agent API Key
        ↓
外部系统保存 agent_id 和 dfa_... Key
        ↓
创建 Conversation，保存 conversation_id
        ↓
向 Conversation 提交 Run
        ↓
同步等待、SSE 流式接收，或异步轮询结果
```

Published Agent API 的固定前缀是：

```text
/api/v1/agents/{agent_id}
```

`agent_id` 是已发布 Agent 的稳定 ID，不是 Agent slug，也不是 Release ID。重新发布或回滚不会改变 `agent_id`、API Key 和已有 `conversation_id`。

## 2. 接入前准备

### 2.1 在控制台完成配置

1. 创建并配置 Agent。
2. 保存草稿并在“发布”页面创建第一个 Release。
3. 进入 Agent Studio 的“集成”页面。
4. 创建一个有明确用途的 API Key，例如 `CRM 生产环境`。
5. 立即复制完整 Key，并保存到外部系统的 Secret Manager。
6. 复制 Agent ID。

完整 Key 以 `dfa_` 开头。完整明文只应在创建后的当前页面会话中复制，不要写入源码、日志、浏览器前端代码或 Git 仓库。

### 2.2 外部系统需要保存的配置

```text
DEERFLOW_BASE_URL=https://deerflow.example.com
DEERFLOW_AGENT_ID=pa_...
DEERFLOW_AGENT_API_KEY=dfa_...
```

生产环境建议把 API Key 放入 Vault、Kubernetes Secret 或云厂商 Secret Manager，并且只允许服务端读取。不要把 Agent API Key 下发给浏览器或移动客户端。

## 3. 认证方式

所有 Published Agent API 请求都使用 Bearer Agent Key：

```http
Authorization: Bearer dfa_...
```

浏览器登录 Cookie、用户级 API Key 和 CSRF Token 都不能替代 Agent Key。一个 Key 只能访问它所属的 Agent。

可以先读取安全元数据，验证地址、Agent ID 和 Key：

```bash
curl --request GET \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY"
```

示例响应：

```json
{
  "agent_id": "pa_example",
  "display_name": "客户支持助手",
  "description": "回答客户问题并查询业务数据",
  "avatar": null
}
```

## 4. 创建 Conversation

外部系统应先创建 Conversation，再向该 Conversation 提交消息。

```bash
curl --request POST \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "external_conversation_id": "crm-ticket-20260727-001",
    "metadata": {
      "system": "crm",
      "tenant": "north-region"
    }
  }'
```

示例响应：

```json
{
  "conversation_id": "conv_8a726cd00ec54a14ad9a8a065f31d82f",
  "status": "active",
  "created_at": "2026-07-27T10:00:00Z",
  "updated_at": "2026-07-27T10:00:00Z"
}
```

外部系统必须持久化返回的 `conversation_id`。

- 同一业务会话后续继续使用原 `conversation_id`，Agent 会保留该 Conversation 内的多轮消息上下文。
- 需要全新上下文时，创建新的 Conversation。
- `external_conversation_id` 可选，建议填写外部系统稳定且可追踪的业务会话 ID。
- 在同一个 Agent Key 下重复创建相同 `external_conversation_id` 会返回 `409 external_conversation_exists`，响应中包含已有 `conversation_id`。
- Conversation 绑定创建它的 Agent Key。换一个新 Key 后不能读取旧 Key 创建的 Conversation，需要重新创建 Conversation。
- `metadata` 必须是 JSON 对象，编码后不能超过 32 KB。

## 5. 提交消息

### 5.1 推荐：同步等待

适用于一次响应能在外部系统 HTTP 超时时间内完成的场景。

```bash
CONVERSATION_ID="conv_8a726cd00ec54a14ad9a8a065f31d82f"

curl --request POST \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations/$CONVERSATION_ID/runs/wait" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY" \
  --header "Idempotency-Key: crm-ticket-20260727-001-message-001" \
  --header "Content-Type: application/json" \
  --data '{
    "message": "请总结这个客户的问题，并给出下一步处理建议。",
    "metadata": {
      "request_source": "ticket-detail"
    }
  }'
```

示例响应：

```json
{
  "run_id": "5c9fa352-08da-4c7a-973d-d305ef0a9a2c",
  "conversation_id": "conv_8a726cd00ec54a14ad9a8a065f31d82f",
  "status": "completed",
  "answer": "客户当前主要反馈……建议下一步……",
  "error": null,
  "created_at": "2026-07-27T10:01:00Z",
  "updated_at": "2026-07-27T10:01:08Z"
}
```

Run 请求只接受：

| 字段       | 必填 | 说明                                                             |
| ---------- | ---: | ---------------------------------------------------------------- |
| `message`  |   是 | 非空字符串，最多 200,000 个字符，并受 Agent 实际输入字节配额限制 |
| `metadata` |   否 | JSON 对象，最多 32 KB                                            |

调用方不能通过请求覆盖模型、Release、Skills、Connector、工具、Owner 或运行时策略；额外字段会返回 `422`。

### 5.2 多轮对话

继续使用同一个 `conversation_id`，并为每个新的业务请求生成新的 `Idempotency-Key`：

```bash
curl --request POST \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations/$CONVERSATION_ID/runs/wait" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY" \
  --header "Idempotency-Key: crm-ticket-20260727-001-message-002" \
  --header "Content-Type: application/json" \
  --data '{"message":"把建议整理成可以直接回复客户的邮件。"}'
```

同一个 Conversation 同一时间只允许一个 Run。并发提交会返回 `409 conversation_busy`，外部系统应等待当前 Run 结束后再提交下一条消息。

## 6. 三种 Run 模式

| 模式 | Endpoint               | 适用场景                           |
| ---- | ---------------------- | ---------------------------------- |
| 同步 | `POST .../runs/wait`   | 后端服务等待完整答案，接入最简单   |
| SSE  | `POST .../runs/stream` | 页面或服务需要实时展示生成过程     |
| 异步 | `POST .../runs`        | 长任务、任务队列、Webhook 前置处理 |

### 6.1 SSE 流式

```bash
curl --no-buffer --request POST \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations/$CONVERSATION_ID/runs/stream" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY" \
  --header "Idempotency-Key: crm-ticket-20260727-001-message-003" \
  --header "Content-Type: application/json" \
  --data '{"message":"流式生成一份处理方案。"}'
```

常见 SSE 事件包括：

- `metadata`：包含公开的 `run_id` 和 `conversation_id`。
- `messages-tuple`、`values`、`custom`：经过安全字段白名单过滤的运行内容。
- `end`：流结束。
- `: heartbeat`：保持连接活跃的注释行。

客户端应按标准 SSE 协议解析 `event`、`id` 和 `data`，不要依赖未公开的内部字段。断线重连时可以发送 `Last-Event-ID`。

### 6.2 异步创建和轮询

创建异步 Run：

```bash
curl --request POST \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations/$CONVERSATION_ID/runs" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY" \
  --header "Idempotency-Key: crm-ticket-20260727-001-message-004" \
  --header "Content-Type: application/json" \
  --data '{"message":"执行一项耗时较长的分析。"}'
```

接口返回 `202` 和 `run_id`。随后轮询：

```bash
RUN_ID="run-id-from-create-response"

curl --request GET \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations/$CONVERSATION_ID/runs/$RUN_ID" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY"
```

可能的公开状态包括：

```text
pending → running → completed
                  ↘ failed
                  ↘ cancelled
                  ↘ timeout
```

取消尚未结束的 Run：

```bash
curl --request POST \
  "$DEERFLOW_BASE_URL/api/v1/agents/$DEERFLOW_AGENT_ID/conversations/$CONVERSATION_ID/runs/$RUN_ID/cancel" \
  --header "Authorization: Bearer $DEERFLOW_AGENT_API_KEY"
```

## 7. Idempotency-Key

所有 Run 创建请求都建议携带：

```http
Idempotency-Key: <外部系统稳定业务请求ID>
```

规则：

- 最长 128 个字符，去除首尾空格后不能为空。
- 网络超时、连接中断或可重试的 5xx 后，必须使用原来的 Endpoint、请求体和 `Idempotency-Key` 重试。
- 相同 Key、相同 Endpoint、相同请求体会返回原 Run，不会重复执行和重复计费。
- 相同 Key 但请求体或 Run 模式不同，会返回 `409 idempotency_conflict`。
- 每一条新的业务消息必须使用新的 `Idempotency-Key`。
- `Idempotency-Key` 只避免重复创建 Run，不保证模型输出具有确定性。

涉及付款、审批、删除、发货等业务操作时，外部系统仍必须执行自己的权限校验、状态机和业务去重，不能仅依赖模型文本触发关键操作。

## 8. 常见错误和重试建议

| HTTP 状态 | 常见错误码                                                   | 建议                                        |
| --------: | ------------------------------------------------------------ | ------------------------------------------- |
|     `401` | `missing_agent_key`、`invalid_agent_key`                     | 检查 Bearer Key；Key 被删除或过期后不要重试 |
|     `404` | `agent_not_found`、`conversation_not_found`、`run_not_found` | 检查 Agent ID，并确认资源由当前 Key 创建    |
|     `409` | `conversation_busy`                                          | 等待当前 Run 结束后重试                     |
|     `409` | `idempotency_in_progress`                                    | 短暂退避，继续使用原 Idempotency-Key        |
|     `409` | `idempotency_conflict`                                       | 修复调用方幂等键生成逻辑，不要原样重试      |
|     `410` | `agent_suspended`                                            | 联系 Agent Owner 恢复 Agent                 |
|     `413` | `input_too_large`                                            | 缩短消息或拆分请求                          |
|     `422` | 参数校验失败                                                 | 修正请求字段、类型或 Idempotency-Key        |
|     `429` | 配额或限流错误码                                             | 读取 `Retry-After`，退避后重试              |
|     `503` | `agent_api_unavailable`                                      | 服务暂不可用，指数退避重试并告警            |

认证中间件错误通常使用：

```json
{
  "error": {
    "code": "invalid_agent_key",
    "message": "The Agent Key is invalid or expired."
  }
}
```

业务路由错误通常使用：

```json
{
  "detail": {
    "code": "conversation_busy"
  }
}
```

客户端应兼容这两种错误外层结构。

## 9. Python 最小示例

```python
import os
import uuid

import httpx

base_url = os.environ["DEERFLOW_BASE_URL"].rstrip("/")
agent_id = os.environ["DEERFLOW_AGENT_ID"]
agent_key = os.environ["DEERFLOW_AGENT_API_KEY"]

headers = {
    "Authorization": f"Bearer {agent_key}",
    "Content-Type": "application/json",
}

with httpx.Client(base_url=base_url, headers=headers, timeout=660) as client:
    conversation_response = client.post(
        f"/api/v1/agents/{agent_id}/conversations",
        json={"external_conversation_id": f"order-{uuid.uuid4()}"},
    )
    conversation_response.raise_for_status()
    conversation_id = conversation_response.json()["conversation_id"]

    business_request_id = str(uuid.uuid4())
    run_response = client.post(
        f"/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs/wait",
        headers={"Idempotency-Key": business_request_id},
        json={"message": "请给出这笔订单的处理建议。"},
    )
    run_response.raise_for_status()
    result = run_response.json()
    print(result["status"])
    print(result["answer"])
```

同步等待的客户端超时应大于 Agent 的 `max_run_seconds`。如果外部网关不允许长连接，改用异步 Run 或 SSE。

## 10. 当前版本边界

- 外部 Run 在创建时解析当时的当前 Release；正在运行的 Run 不受重新发布或回滚影响，下一次 Run 使用新的当前 Release。
- 同一 Conversation 保留多轮消息，但 Published Agent 不启用 DeerFlow 跨 Conversation 长期记忆。
- 当前公开 Run 请求只接受文本 `message` 和安全 `metadata`，不提供公开文件上传参数。
- 公开 JSON 和 SSE 不暴露 Release ID、模型名、内部指令、Skill revision、Connector 信息、凭据、内部 Thread ID 或服务器文件路径。
- Connector 凭据始终由 DeerFlow 保存，外部系统不需要也不能通过 Run 请求传入。

## 11. 上线检查清单

- [ ] Agent 已发布并且状态正常。
- [ ] 外部系统使用稳定 `agent_id`，没有使用 slug 或 Release ID。
- [ ] Agent API Key 只保存在服务端 Secret Manager。
- [ ] 已持久化 `conversation_id` 与外部业务会话的映射。
- [ ] 每个新 Run 使用唯一的业务 `Idempotency-Key`。
- [ ] 网络重试保留原请求体和原 Idempotency-Key。
- [ ] 同一个 Conversation 的 Run 串行提交。
- [ ] `429` 按 `Retry-After` 退避。
- [ ] 客户端兼容两种错误响应外层结构。
- [ ] 关键业务动作在外部系统内继续执行授权、状态机和去重。
- [ ] 日志不会记录完整 Agent API Key 或敏感消息。
