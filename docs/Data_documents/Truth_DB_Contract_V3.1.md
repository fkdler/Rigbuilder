# RigBuilder Truth DB 契约 V3.1（冻结）

> 冻结日期：2026-09-04  
> 数据库修订：`0006_freeze_truth_v3_1`  
> Bundle / Release Manifest：`schema_version = "3.1"`

本文是 V3.1 的增量冻结记录。既有 V3/V3.1 文档保留为历史设计与调查员材料，不在本次修改；若旧文档、旧草稿与本文或已提交 JSON Schema 冲突，以本文、`data/v3/schemas/*.json` 和当前 ORM/迁移为准。

## 1. 冻结范围

V3.1 冻结以下四个边界：

1. Truth Bundle 与 Release Manifest 的离线 JSON 契约；
2. `truth` Schema 的 33 张表及其规范列；
3. `agent_catalog` 的 12 个只读视图；
4. Release 校验、Evidence 审核语义和导入行为。

对外推荐响应的版本号不在本次 Truth 数据契约升级范围内。

## 2. 破坏性变更

- Bundle 和 Manifest 只接受 `3.1`；V3.0 legacy 草稿继续留在 `data/v3/drafts/legacy/`，但不得进入 V3.1 Release。
- 一个事实只保留一个规范存储位置。V2/V3 过渡期的别名列与 `extra` 万能 JSON 列已从数据库和 Bundle 删除，不做隐式换算或字段猜测。
- `hardware.category` 固定为 `cpu / gpu / memory / storage / psu / platform`。桌面、笔记本和数据中心 GPU 通过产品字段及 `laptop_gpu_spec` / `datacenter_gpu_spec` 表达，不再使用第二套 `hardware_type`。
- `FieldDefinitionPayload.applies_to_entity_type` 必填，`value_type` 支持 `integer`。
- 八类 `*_spec` 均为严格类型对象，未知键直接拒绝；长尾事实必须先注册 `field_definition`，再写入 `entity_attribute`。
- Runtime 使用 `variant_key + runtime_key + checked_at` 的追加式快照；规范状态列为 `support_status`。

## 3. Evidence 冻结语义

- 每条 Evidence 必须显式提供 `review_status = pending / accepted / rejected / conflict`。
- accepted Bundle 不得包含 pending 或 rejected Evidence；accepted 与 conflict 可共同保存。
- 只有 accepted Evidence 能满足 recommendable 实体的必需字段覆盖，并出现在 `agent_catalog.fact_evidence`。
- conflict Evidence 保留来源原文和冲突规范值，不覆盖当前事实，也不计入事实支持。
- 规范字段为 `raw_excerpt`、`source_locator`、`normalized_value`、`review_status`、`reviewed_at`；旧 `raw_value / quote / locator` 不再属于 Evidence 契约。

## 4. 规范列决议

以下是容易混淆的最终列名：

| 事实 | V3.1 规范列 | 已移除的过渡列 |
|---|---|---|
| 硬件分类 | `hardware.category` | `hardware.hardware_type` |
| GPU 显存 | `gpu_spec.vram_gib` | `vram_bytes` |
| GPU 带宽 | `memory_bandwidth_gb_s` | `bandwidth_gbps` |
| CPU 核数/功耗/PCIe | `cores_total / max_power_w / pcie_generation` | `cores / tdp_w / pcie_version` |
| 模型参数/上下文 | `total_parameters / context_length_tokens` | `parameter_count / context_length` |
| 变体量化/格式 | `quantization_method / weight_format` | `quantization / format` |
| 来源访问与内容摘要 | `accessed_at / content_hash` | `retrieved_at / content_sha256` |
| Workload 类型 | `workload_type` | `category` |
| 价格区域 | `market_region` | `region` |
| 兼容关系 | `relation_key` | `relationship` |

所有当前规范列的完整机器可读定义见：

- `data/v3/schemas/bundle.schema.json`
- `data/v3/schemas/release-manifest.schema.json`
- `backend/app/models/truth_v3.py`

## 5. 冻结门禁

- `0006` 只允许在首个正式 Release 前、`catalog_entity` 与 `dataset_release` 均为空时执行；否则迁移立即失败。
- 迁移重建视图后会恢复既有标准 `agent_readonly` 角色的 12 个 SELECT 授权。
- Release 只能引用 accepted Bundle，路径不得进入 `drafts`，每个文件必须通过 Manifest SHA-256 校验。
- recommendable 实体的身份、核心规范和所有已提供的强类型规格必须有同实体、同字段的 accepted Evidence。
- Benchmark、Price 与 Runtime 均为追加式历史；Benchmark 必须精确绑定 `protocol_key + protocol_version`。

## 6. 当前验收基线

- 数据库：33 张 `truth` 表、12 个 `agent_catalog` 视图；ORM 与实库列集合完全一致。
- 权限：`agent_readonly` 可查询全部 12 个视图，不能访问 `truth`/`public` 底表，事务强制只读。
- 数据：首个 V3.1 Release 尚未导入；当前 Truth 行数为 0，避免旧数据污染。
- 进入正式采集前不得再新增规范列或恢复兼容别名；新长尾字段通过 Field Definition + Attribute 扩展。
