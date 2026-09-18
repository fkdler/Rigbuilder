# RigBuilder API 文档 V3.1（历史快照）

> 2026-09-15 更新：旧 `/api/model-comparison`、`/api/agent/run`、`/api/agent/fusion` 已移除。当前会话入口为 `/api/query/jobs`；本文保留历史契约，不再代表这些旧接口仍可调用。

> 状态快照：2026-09-07。本文以 `backend/app/api/`、Pydantic Schema 与 `Plan_V3.1.md` 为准，描述已实现的 HTTP 接口；不把尚未实现的 Decision Result / Presentation Result 当作前端契约。

## 通用约定

- 服务版本为 `0.3.0`，开发环境默认基址为 `http://127.0.0.1:8000`。
- 所有请求与响应均为 JSON；UUID 以字符串传递；`message` 长度为 1–4000 字符。
- 前端只读取后端返回的候选、验证、分数和 trace，不能改写事实、验证状态或排名。

## 已暴露端点

| 方法与路径 | 用途 | 当前前端状态 |
| --- | --- | --- |
| `GET /health` | 进程健康检查 | 可直接调用 |
| `GET /health/database` | PostgreSQL 连通性检查 | 可直接调用 |
| `POST /api/chat` | 单模型会话 | 已有 API 客户端 |
| `GET /api/conversations/{conversation_id}` | 读取持久化会话 | 已有 API 客户端 |
| `POST /api/model-comparison` | 三个模型的原始回答并列比较 | 当前页面使用 |
| `POST /api/agent/run` | 单 Agent 的受控推荐与真值验证 | 后端已实现，前端未接入 |
| `POST /api/agent/fusion` | 三 Agent 融合推荐 | 后端已实现，建议作为新推荐工作台的主接口 |

`GET /health` 返回 `{ "status": "ok" }`；数据库可用时 `GET /health/database` 返回 `{ "status": "ok", "database": "reachable" }`，不可用时为 `503`。

## 基础对话与模型比较

### `POST /api/chat`

请求：

```json
{ "message": "给我一个本地部署建议", "conversation_id": null }
```

响应包含 `conversation_id`、`answer`、`model` 与 `context_compressed`。不存在的会话返回 `404`；LLM 未配置返回 `503`；推理服务失败返回 `502`。

### `POST /api/model-comparison`

请求仅包含 `message`。响应为：

```json
{
  "results": [
    {
      "model_id": "configured-model-a",
      "response_model": "configured-model-a",
      "answer": "...",
      "error": null
    }
  ]
}
```

这是原始模型回答的对照接口，不产生推荐排名、验证证据或融合分数。

## 单 Agent 推荐：`POST /api/agent/run`

请求：

```json
{ "message": "推荐一张适合本地模型的 GPU", "conversation_id": null }
```

响应始终使用下列外层结构；`recommendation`、`verification` 与 `release_key` 在失败时可为 `null`：

```json
{
  "request_id": "UUID",
  "agent_run_id": "UUID",
  "conversation_id": "UUID",
  "status": "completed",
  "model": "configured-model",
  "recommendation": { "schema_version": "3.0", "recommendations": [] },
  "verification": { "release_key": "...", "candidates": [] },
  "release_key": "...",
  "error_code": null,
  "error": null,
  "rounds_used": 1,
  "sql_calls_used": 1
}
```

## Recommendation 与 Truth Verification

`recommendation.schema_version` 固定为 `3.0`。每个候选含 `candidate_id`、`candidate_type`（`hardware`、`ai_model`、`model_variant` 或 `workload`）、`name`、模型自报的 `score`、可选 `model_confidence`、`reasons`、`risks` 与 `claims`。

Claim 约束如下：

- `fact` 必须带 `field_key` 与 `evidence_ids`。
- `measurement` 必须带 `metric_key` 与 `benchmark_run_ids`。
- `derived` 必须带 `rule_refs`。
- `preference` 不得作为事实证据使用。

验证结果由后端独立读取 Truth DB 产生，状态只能是 `supported`、`conflict`、`missing` 或 `unverifiable`。每个候选同时返回 `fact_support`、`proof_coverage`、`information_completeness` 及各 Claim 的 canonical 值、比较方式和有效证据 ID。

## 三 Agent 融合：`POST /api/agent/fusion`

该接口使用配置中的三个不同模型在同一会话和数据 Release 中串行执行 Agent，再通过 `fusion-v1` 融合。至少一个 Agent 成功即可产生融合结果；失败 Agent 不进入共识分母，但会降低 `agent_coverage`。

### 请求

```json
{
  "message": "推荐一张适合本地模型的 GPU",
  "conversation_id": null,
  "top_k": 3,
  "constraints": [
    {
      "constraint_id": "minimum-vram",
      "kind": "hard",
      "confirmed": true,
      "target": "field",
      "field_key": "gpu.vram_gib",
      "operator": "gte",
      "value": 12,
      "unit": "GiB"
    },
    {
      "constraint_id": "budget",
      "kind": "hard",
      "target": "price",
      "operator": "lte",
      "value": 5000,
      "currency": "CNY",
      "region": "CN",
      "condition": "new",
      "price_type": "current_new"
    }
  ]
}
```

- `top_k` 默认为 `3`，范围为 `1–10`；`constraints` 默认为空列表。
- 每条约束的 `constraint_id` 必须唯一；`confirmed` 只能为 `true`（省略时默认 `true`）。
- `kind` 只能为 `hard` 或 `preference`；`weight` 可选，默认 `1`，范围为 `(0, 1]`。
- `target` 是判别字段：
  - `field`：需要 `field_key`、`operator`（`eq`、`ne`、`gt`、`gte`、`lt`、`lte`、`in`、`contains`）与非空 `value`；可选 `unit`、`qualifier_key`。
  - `price`：需要数值 `value` 与数值比较操作符；`currency`、`region`、`condition`、`price_type` 分别默认 `CNY`、`CN`、`new`、`current_new`。
  - `compatibility`：需要 `other_entity_id`、`relation_key`；可选 `direction`（默认 `outgoing`）与 `required_status`（默认 `compatible`）。
  - `runtime`：需要 `runtime_key`；`required_status` 默认 `supported`。

接口不负责从自然语言中擅自提取约束。前端应在调用前让用户检查、确认并显式传入约束。

### 成功响应

```json
{
  "request_id": "UUID",
  "conversation_id": "UUID",
  "status": "completed",
  "release_key": "rigbuilder-v3-2-2026-09-07",
  "policy_version": "fusion-v1",
  "agents": [
    {
      "agent_run_id": "UUID",
      "model_id": "configured-model-a",
      "response_model": "configured-model-a",
      "status": "completed",
      "recommendation": { "schema_version": "3.0", "recommendations": [] },
      "verification": { "release_key": "...", "candidates": [] },
      "error_code": null,
      "error": null
    }
  ],
  "result": {
    "release_key": "rigbuilder-v3-2-2026-09-07",
    "policy_version": "fusion-v1",
    "top_k": [],
    "eliminated": [],
    "trace": {}
  },
  "error": null
}
```

`result.top_k` 与 `result.eliminated` 的候选均包含：`canonical_name`、`recommendation_score`、`credibility`、三项验证指标、`constraint_validity`、`preference_utility`、`consensus`、来源模型/排名、`agent_scores`、`model_confidences`、约束验证结果、融合 Claim 与 `elimination_reasons`。`trace` 包含参与/失败模型、`agent_coverage`、公式、输入摘要与稳定排序规则。

硬约束为 `violated` 或 `unknown` 时，候选进入 `eliminated`；实体无效或关键 Claim 为 `conflict` 时也会淘汰。

### 失败语义

- `404`：请求引用的会话不存在。
- `409`：Truth 数据条件不满足或执行中 Release 变化。响应为 `{ "detail": { "code": "truth_release_unavailable", "message": "..." } }` 等结构。
- `503`：融合模型配置或 LLM 基址无效。
- `200` 且 `status: "failed"`：请求已被受理，但三个 Agent 均在融合前失败，或融合引擎未产出结果；此时查看 `agents` 与 `error`。

## `fusion-v1` 固定规则

```text
有偏好：recommendation_score = 100 × (0.70 × preference_utility + 0.30 × consensus)
无偏好：recommendation_score = 100 × consensus
credibility = 100 × (0.40 × fact_support + 0.25 × proof_coverage
                      + 0.20 × information_completeness
                      + 0.15 × consensus × agent_coverage)
```

Agent 的 `score` 仅以 `agent_scores` 记录，`model_confidence` 仅展示；二者都不参与最终融合公式。
