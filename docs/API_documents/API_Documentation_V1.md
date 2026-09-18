# API 文档 V1

> 文档状态：本文档不再修改；后续如有更新，则使用新的后缀。

## 1. 范围与基地址

本版本提供单模型聊天、持久化会话和历史恢复接口。一个 `conversation_id` 是用户可见对话的唯一边界；后续多模型、多 Agent 的内部运行不会创建或要求客户端传递独立的模型会话 ID。

开发环境基地址：`http://127.0.0.1:8000`。交互式 OpenAPI 文档位于 `/docs`。

## 2. 约定

- 请求与响应均为 JSON，编码为 UTF-8。
- `conversation_id` 使用 UUID。
- 聊天消息最大长度为 4,000 个字符。
- 除 `GET /health` 外，错误响应使用 `{ "detail": "..." }`。
- 浏览器只调用 FastAPI；不直接调用设备 B 的 `llama-server`。

## 3. 基础健康检查

### `GET /health`

仅验证 FastAPI 进程，不检查数据库或推理服务。

```json
{ "status": "ok" }
```

### `GET /health/database`

验证 FastAPI 到 PostgreSQL 的连接。

成功响应：

```json
{ "status": "ok", "database": "reachable" }
```

数据库不可用时返回 `503`。

## 4. 发送聊天消息

### `POST /api/chat`

创建新会话或在既有会话中追加一轮对话。后端会先持久化用户消息，组装“系统提示词 → 最新有效摘要 → 已确认约束 → 最近原始消息 → 当前消息”的上下文，调用本地模型，再持久化最终助手回复。

请求：

```json
{
  "message": "我已经有 RTX 4090，预算一万元，适合跑什么代码模型？",
  "conversation_id": null
}
```

- `message`：必填，长度 1–4,000。
- `conversation_id`：首次请求传 `null` 或省略；后续请求必须传回此前响应的 ID。

成功响应：

```json
{
  "conversation_id": "b6977752-caa7-49b2-99b0-2368be44b7df",
  "answer": "……",
  "model": "agent-qwen",
  "context_compressed": false
}
```

- `model` 是设备 B Router 返回的逻辑模型 ID，不是 GGUF 路径。
- `context_compressed` 表示本轮是否成功写入新的摘要快照；`false` 不表示历史没有被保存。

错误：

| 状态码  | 场景                                                                    |
| ------- | ----------------------------------------------------------------------- |
| `404` | 提供的`conversation_id` 不存在。                                      |
| `422` | 请求 JSON、UUID 或消息长度不合法。                                      |
| `502` | 设备 B 的模型服务无法完成请求。用户消息仍已持久化，便于诊断和稍后继续。 |
| `503` | LLM 地址配置无效。                                                      |

## 5. 读取会话历史

### `GET /api/conversations/{conversation_id}`

读取指定会话的完整原始 user/assistant/system 消息，用于刷新页面后恢复对话显示。摘要和用户约束属于后端内部上下文，不在此接口返回。

成功响应：

```json
{
  "id": "b6977752-caa7-49b2-99b0-2368be44b7df",
  "status": "active",
  "messages": [
    {
      "id": "f2a3c83b-a6e2-4f82-940a-a48ed24dcbd6",
      "role": "user",
      "content": "我已有 RTX 4090。",
      "created_at": "2026-08-30T08:00:00Z"
    },
    {
      "id": "2bd9bb9c-b950-4f05-8d7d-62f3323d605e",
      "role": "assistant",
      "content": "……",
      "created_at": "2026-08-30T08:00:04Z"
    }
  ]
}
```

错误：`404` 表示会话不存在；`422` 表示路径中的 UUID 格式不正确。

## 6. 临时三模型对比

### `POST /api/model-comparison`

用于开发验收：同一问题由后端使用 `LLM_TEST_MODEL_IDS` 中配置的三个模型 ID 依次请求。该接口不创建会话、不持久化对比回复，且客户端不能传入模型 ID。

请求：

```json
{ "message": "比较你们适合的本地部署方案。" }
```

成功响应中的 `results` 顺序固定对应环境变量中的 A、B、C：

```json
{
  "results": [
    {
      "model_id": "agent-qwen",
      "response_model": "agent-qwen",
      "answer": "……",
      "error": null
    }
  ]
}
```

单个模型失败时仍返回 `200`，该项的 `error` 有值且其他模型继续执行。若 `LLM_TEST_MODEL_IDS` 未配置为恰好三个不同 ID，返回 `503`。

## 7. 上下文窗口与压缩

持久化不依赖模型上下文窗口；无论是否启用压缩，原始消息都会保留在 PostgreSQL。

自动压缩取决于后端环境变量 `LLM_CONTEXT_WINDOW_TOKENS`：

```dotenv
# 示例：仅在已确认当前 Router 模型窗口后设置。
LLM_CONTEXT_WINDOW_TOKENS=32768
CONTEXT_COMPRESSION_RATIO=0.70
CONTEXT_RECENT_TURNS=6
```

- 未设置 `LLM_CONTEXT_WINDOW_TOKENS`：保存全部历史，并只向模型提供最近 `CONTEXT_RECENT_TURNS` 轮原始消息；不会自动生成摘要。
- 设置后：当估算上下文超过窗口的 `CONTEXT_COMPRESSION_RATIO` 时，后端尝试生成并通过 JSON Schema 校验的版本化摘要。
- 摘要失败时，后端保留旧摘要和所有原始消息，并继续使用受预算限制的最近消息完成当前请求。

未来多模型时，窗口大小会由服务端 Agent → 模型配置提供；客户端不传递窗口大小、模型路径或任意模型 ID。

## 8. 后续

本版本未提供会话删除、用户约束编辑、模型选择、SQL 查询或多 Agent 运行接口。`user_constraint` 与 `conversation_context_snapshot` 已作为内部持久化实体创建，待约束提取和多 Agent Controller 实现后再开放受控 API。
