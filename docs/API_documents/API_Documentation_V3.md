# API 文档 V3（Truth Claim）

> 此文档配合 Data_Collection_Spec_V3.md 使用，规范可能的最终版本数据库要求的接口。

V3 保留 `POST /api/agent/run`，并新增 `POST /api/agent/fusion`。单 Agent 成功响应中的 `recommendation` 仍为向后兼容的 Recommendation 3.0，同时增加后端生成的 `verification` 与 `release_key`。

```json
{
  "schema_version": "3.0",
  "recommendations": [
    {
      "candidate_id": "36e7d6f4-9c3a-5a1c-a48a-6cbb31ee9c54",
      "candidate_type": "hardware",
      "name": "Example GPU",
      "score": 82,
      "model_confidence": 0.72,
      "reasons": ["显存满足工作负载要求"],
      "risks": ["缺少相同环境的项目实测"],
      "claims": [
        {
          "claim_type": "fact",
          "entity_id": "36e7d6f4-9c3a-5a1c-a48a-6cbb31ee9c54",
          "field_key": "gpu.vram_gib",
          "metric_key": null,
          "value": 12,
          "value_type": "number",
          "unit": "GiB",
          "qualifier_key": null,
          "statistic": null,
          "evidence_ids": ["882a4ccb-2c5c-5fd7-8f38-c11e603f977e"],
          "benchmark_run_ids": [],
          "rule_refs": []
        }
      ]
    }
  ],
  "insufficient_information": [],
  "tool_summary": {"sql_calls": 1, "tables_queried": ["gpu_catalog"]}
}
```

约束：`fact` 必须带 `field_key/evidence_ids`；`measurement` 必须带 `metric_key/benchmark_run_ids`；`derived` 必须带 `rule_refs`；`preference` 不得冒充事实。历史 V1/V2 Agent 输出保留在日志中，但不作为 V3 UUID 引用来源。

## Truth Verification 响应

模型 Recommendation 通过 Schema 后，后端使用 `truth` ORM 独立复核，不执行模型生成的验证 SQL。成功响应增加：

```json
{
  "release_key": "rigbuilder-v3-2026-09",
  "verification": {
    "release_key": "rigbuilder-v3-2026-09",
    "candidates": [{
      "candidate_id": "36e7d6f4-9c3a-5a1c-a48a-6cbb31ee9c54",
      "candidate_valid": true,
      "fact_support": 1.0,
      "proof_coverage": 1.0,
      "information_completeness": 1.0,
      "claims": [{
        "claim_index": 0,
        "status": "supported",
        "reason_code": "truth_and_evidence_match",
        "canonical_value": 12,
        "canonical_unit": "GiB",
        "comparison_method": "exact",
        "valid_evidence_ids": ["882a4ccb-2c5c-5fd7-8f38-c11e603f977e"]
      }]
    }]
  }
}
```

状态只允许 `supported / conflict / missing / unverifiable`。没有 accepted Release 时运行失败并返回 `truth_release_unavailable`。

## `POST /api/agent/fusion`

该接口让 `LLM_TEST_MODEL_IDS` 中三个不同模型在同一上下文和数据 Release 上依次执行完整 SQL Agent，然后由 `fusion-v1` 后端算法融合。至少一个 Agent 成功即可输出结果；失败 Agent 不作为“未推荐”参与共识分母。

请求中的约束必须已经由用户确认，本接口不负责自然语言约束抽取：

```json
{
  "message": "推荐一张适合本地模型的 GPU",
  "conversation_id": null,
  "top_k": 3,
  "constraints": [
    {
      "constraint_id": "minimum-vram",
      "kind": "hard",
      "target": "field",
      "field_key": "gpu.vram_gib",
      "operator": "gte",
      "value": 12,
      "unit": "GiB",
      "confirmed": true
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

`target` 支持 `field / price / compatibility / runtime`。硬约束为 `violated` 或 `unknown` 时均淘汰候选；软偏好只参与效用分。响应包含三个 Agent 的运行状态、Top-K、淘汰列表、评分分量及可重放的 fusion trace。

`fusion-v1` 固定规则：

```text
有软偏好：recommendation_score = 100 * (0.70 * preference_utility + 0.30 * consensus)
无软偏好：recommendation_score = 100 * consensus
credibility = 100 * (0.40 * fact_support + 0.25 * proof_coverage
                      + 0.20 * information_completeness
                      + 0.15 * consensus * agent_coverage)
```

Agent 的 `score` 仅以 `agent_scores` 名义进入 trace，`model_confidence` 仅展示；二者都不参与最终公式。
