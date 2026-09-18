# RigBuilder Truth DB 契约 V3.2（冻结）

> 冻结日期：2026-09-07  
> 数据库修订：`0006_freeze_truth_v3_1`（无 DDL 变更）  
> Bundle / Release Manifest：`schema_version = "3.2"`

## 1. 版本边界

- 导入器、Manifest 和正式 Release 只接受 `3.2`；V3.1 文档和草稿为历史资料，不具备导入兼容性。
- 现有 `truth` 表、`agent_catalog` 视图和只读角色不变，因此不新增 Alembic revision。

## 2. Bundle 级 Evidence 语义

- accepted Bundle 不得包含 pending 或 rejected Evidence；accepted 与 conflict 可共同保存。
- 每个 recommendable 实体或 recommendable 变体必须至少带一条 accepted Evidence。
- 该 accepted Evidence 的来源被视为同一 Bundle 中全部已填事实的审核依据；导入器不再要求每个非空字段拥有同名 accepted Evidence。
- conflict Evidence 不放行 Bundle；没有 accepted Evidence 的 recommendable 实体/变体被拒绝。
- 显式 Evidence 仍必须链接存在的 source document，且非 conflict 的 `normalized_value` 必须匹配该 Evidence 的 `field_key` 对应事实。
- EvidenceClaim 仅存储 Bundle 明确提供的 Evidence；`agent_catalog.fact_evidence` 仍只公开 accepted 的显式字段级 claim，不推导或伪造 claim。

## 3. 静态更新

已存在实体的静态事实发生变更时，同一 Bundle 至少有一条 accepted Evidence 即可放行更新。pending 或 conflict Evidence 不足以放行。

## 4. 发布门禁

Release 必须位于 `drafts/` 外，只引用 accepted Bundle，并通过 Manifest SHA-256、Schema、来源引用、显式 Evidence 值一致性和 Bundle 级 Evidence 校验。价格、Benchmark 与 Runtime 继续遵循追加式历史规则。
