# API 文档 V2.2（新增接口）

> 仅记录 V2.2 新增接口；V1 已有接口见 [API_Documentation_V1.md](API_Documentation_V1.md)。约定与 V1 相同：JSON/UTF-8，`conversation_id` 为 UUID，消息 1–4,000 字符，错误响应 `{ "detail": "..." }`，浏览器只调用 FastAPI。

## 基地址

开发环境：`http://127.0.0.1:8000`。OpenAPI：`/docs`。

## POST /api/agent/run

运行一次受控 SQL Agent：后端注入数据库 Schema 与工具协议，模型可调用 `inspect_database` / `query_database` 查询五张白名单表，最终输出通过 Recommendation JSON Schema 校验的推荐结果。整个 Run 包在单 GPU 串行锁内，并全程落 `agent_run` / `agent_message` / `tool_call` 日志。

请求：

```json
{
  "message": "预算 8000 元，推荐一块显存至少 12GB 的显卡",
  "conversation_id": null
}
```

- `message`：必填，1–4,000 字符。
- `conversation_id`：首次传 `null` 或省略；后续传回先前响应的 ID，沿用同一会话上下文。

成功响应（`status=completed`）：

```json
{
  "request_id": "8f14e45f-ea73-4f3a-bc1e-6f6c0b1a2c3d",
  "agent_run_id": "9f6c2c2a-6f7a-4e8c-9d1f-2b3c4d5e6f70",
  "conversation_id": "b6977752-caa7-49b2-99b0-2368be44b7df",
  "status": "completed",
  "model": "agent-b",
  "recommendation": {
    "schema_version": "1.0",
    "recommendations": [
      {
        "candidate_id": "b6977752-caa7-49b2-99b0-2368be44b7df",
        "candidate_type": "hardware",
        "name": "GeForce RTX 3060",
        "score": 82,
        "model_confidence": 0.72,
        "reasons": ["12GB VRAM 满足 8GB 最低要求"],
        "risks": ["二手货源不稳定"],
        "claims": [
          {
            "entity_type": "hardware",
            "entity_id": "b6977752-caa7-49b2-99b0-2368be44b7df",
            "field": "vram_gb",
            "value": 12,
            "value_type": "number",
            "evidence_ids": ["a1b2c3d4-e5f6-7890-abcd-ef0123456789"]
          }
        ]
      }
    ],
    "insufficient_information": [],
    "tool_summary": {"sql_calls": 1, "tables_queried": ["hardware"]}
  },
  "error": null,
  "rounds_used": 3,
  "sql_calls_used": 1
}
```

- `request_id`：本次 HTTP 请求的唯一 ID。
- `agent_run_id`：本次 Agent Run 的唯一 ID（日志还原根）。
- `recommendation`：合规 Recommendation JSON；若 Run 失败则为 `null`。
- `status`：`completed` 或 `failed`。
- `rounds_used` / `sql_calls_used`：实际轮数与 SQL 调用次数（评估指标用）。
- `error`：失败时的非敏感错误信息。

错误：

| 状态码 | 场景 |
| --- | --- |
| `404` | `conversation_id` 不存在。 |
| `422` | 请求 JSON、UUID 或消息长度不合法。 |
| `502` | 推理服务无法完成请求（用户消息与已产生的日志仍保留）。 |
| `503` | LLM 地址配置无效。 |

说明：工具失败、超限、阻断型 SQL 错误不会以 HTTP 错误返回，而是体现在 `status=` + `error` + 日志表中的 `error_code`，便于按 Run 分析。

## 审计与评估

一次 Run 可通过 `request_id → agent_run → agent_message/tool_call` 串联还原；`tool_call` 记录每次 SQL 的参数、`query_id`、`error_code`、`result_truncated`、耗时与结果摘要，供 §8.2 指标（SQL 正确率、自修率、越权拦截率等）计算。
