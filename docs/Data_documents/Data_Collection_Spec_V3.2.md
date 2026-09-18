# RigBuilder 数据收集说明 V3.2（面向调查员）

## 1. 版本与目录

- Bundle 与 Release Manifest 的 `schema_version` 固定为 `"3.2"`；导入器拒绝 `3.1` 及更早版本。
- 调查数据放在 `data/v3/drafts/`，状态为 `"pending"`，不得导入。
- 审核通过的数据移至 `data/v3/releases/<release-key>/`，状态改为 `"accepted"`，再生成带 SHA-256 的 `manifest.json`。
- `drafts/legacy/` 是历史迁移材料，不属于 V3.2 Release。

## 2. 来源与 Evidence

`sources/` 中每个 `source_document` 只登记一份可追溯的来源页面。实体 Bundle 的 `payload.evidence` 则将该来源附着到实体。

- recommendable 实体或 recommendable 模型变体必须至少有一条 `review_status: "accepted"` 的 Evidence。
- 一条已接受 Evidence 的来源覆盖同一实体 Bundle 内的全部已填事实；不要求每个字段另建一条 Evidence。
- 已接受 Bundle 不得包含 `pending` 或 `rejected` Evidence；`conflict` 可保留，但不能作为 Bundle 放行依据。
- 显式提供的 Evidence 仍必须引用存在的 `source_key`，且其 `normalized_value` 必须与对应 Bundle 事实一致。
- Evidence 会按原样写入 `truth.evidence_claim`；只有显式提供的字段级 Evidence 会出现在 `agent_catalog.fact_evidence`。系统不会补造字段级证据。

## 3. 审核步骤

1. 打开 `source_document.url`，确认来源、页面内容和 `accessed_at`。
2. 核对同一 Bundle 的事实；不被来源支持的值应修正或置空。
3. 将可作为该 Bundle 依据的 Evidence 标为 `accepted` 并填写 `reviewed_at`；冲突材料标为 `conflict`。
4. 确认 Bundle 内没有 pending/rejected Evidence 后，将 Bundle 标为 `accepted` 并移入 Release 目录。
5. 在 `backend` 目录运行 `build_v3_manifest`、`import_v3 --validate-only` 和 `import_v3 --dry-run`；只有 dry-run 成功后才能 apply。

## 4. 保留的字段规则

字段结构、单位、实体键、严格 spec 对象和属性扩展机制沿用 V3.1 JSON Schema 的设计，但以 V3.2 生成的 Schema、当前 ORM 与本说明为准。未知事实保持 `null`；不得猜测、不得用邻近型号补值。
