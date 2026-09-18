# RigBuilder Plan V3

> 状态：规划中。日期：2026-09-03。前置版本：[Plan_V2.4.md](Plan_V2.4.md)（真值验证与融合评分的原始规划）、[Data_Collection_Spec_V3.md](../Data_documents/Data_Collection_Spec_V3.md)（目标 Schema 与数据契约）。
> 本文档回答一个问题：Truth DB V3 大升级之后，哪些已经完成、哪些尚未完成、按什么顺序完成。

## 0. 版本定位

V3 将 Truth DB 从五张最小表升级为 33 张 `truth` 表加 12 个 Agent 只读视图的最终结构，并重建了数据契约、导入工具与 Agent 读取面。V2.4 的判断继续成立：SQL Agent 闭环不重做，重点转向"真值验证 + 融合算法 + 数据填充"。SQL Agent 读取面（inspect/query/validator/Claim 3.0）已随 V3 完成适配，本计划只在其上补齐后端验证与融合。

## Part 1：已完成并已验证

以下均通过 73 项离线测试与 `0004:head` 离线迁移 SQL 验证；真实 PostgreSQL 验收留给远端切换（见 2.1）。

- 离线数据契约：`app/data_contracts/v3.py` 覆盖全部 12 种 record_type；实体 UUID 统一为 `UUIDv5(NAMESPACE_URL, "https://rigbuilder.local/entity/" + entity_key)`；Release Manifest 强制 SHA-256，拒绝 drafts 路径与非 accepted Bundle。
- 完整 ORM 与迁移：33 张 truth 表、约束、索引与 12 个 `agent_catalog` 视图；Revision 0005 在单个 PostgreSQL 事务中完成"删旧五表 + 建 V3"，要求 `-x allow_truth_rebuild=true`，检测到 Schema 冲突即失败，downgrade 禁用，恢复只依赖 pg_dump。
- Importer：`--validate-only` 完全离线（无 `.env` 可用）、`--dry-run` 完整事务后回滚、`--apply` 显式提交；静态实体受控 upsert（改动必须有同 Release Evidence，且 Evidence 规范值必须匹配 Bundle 事实），Benchmark 以 `protocol_key + protocol_version` 精确关联协议；Benchmark/价格/Runtime 为追加式历史。
- 旧数据转换：22 硬件、8 模型（含 24 变体）、174 Evidence 全部转为 `data/v3/drafts/legacy/` 的 pending 草稿并带转换报告；false 能力转 unknown，`file_size_gb` 不换算为字节。
- Agent 适配：`inspect_database()` 输出静态 View Registry；validator 只允许 `agent_catalog` 视图与函数白名单；只读账号仅授 `agent_catalog` 的 USAGE/SELECT；Recommendation 升级为 Claim 3.0（fact / measurement / derived / preference）。
- 验收与运维工具：`preflight_v3`、`check_v3 --strict`、`reset_truth_data`（确认短语 `RESET_TRUTH_V3`）、`remote_cutover_v3.ps1` 一键编排，及《Truth_DB_V3_Remote_Cutover.md》远端操作手册。
- 清理：五张旧 V2 ORM 文件已删除；`import_data.py`、`seed_demo.py` 改为 tombstone，防止切换后误写 V2 数据。

## Part 2：未完成工作（问题 → 改进方向）

### 2.1 远端切换未执行

问题：0005 迁移与 Importer 从未在真实 PostgreSQL 上运行；事务原子性、权限边界和备份恢复均未真机验证。

改进方向：按《Truth_DB_V3_Remote_Cutover.md》执行：pg_dump 全库备份并校验哈希 -> `preflight_v3` -> 带 `-x allow_truth_rebuild=true` 的 `upgrade head` -> Importer dry-run -> apply -> `create_agent_readonly --apply` -> `check_v3 --strict` -> 二次 dry-run 验证幂等。任一步失败即停止并从备份恢复，不使用 downgrade。

### 2.2 正式 Release 未产生

问题：`data/v3/` 中的草稿全部为 pending，仓库没有可导入的正式 Release；V3 切换要求代码与至少一个审核通过的 Release 同时就绪。

改进方向：人工审核 legacy 草稿，重点是三件事：entity_key 前缀统一为 Spec V3 §4.1 的 `hw:` / `model:` / `variant:`（legacy 草稿现用 `hardware:`）；来源 `retrieved_at` 的 1970-01-01 哨兵值必须替换为真实采集时间；`file_size_bytes` 依据制品文件元数据重算。审核通过后移出 drafts、状态改为 accepted，用 `build_v3_manifest.py` 生成 hash-pinned manifest。

### 2.3 Truth Verification 已实现（待真实数据验收）

实现结果：Recommendation 通过 Schema 后由独立后端验证器复核；验证器通过 ORM 白名单读取固定列或 Attribute，核对类型、单位、比较规则与 accepted Evidence，并验证 Benchmark Run、版本化 Rule 与 Preference 排除规则。

当前流水线：`Claim -> 按 field_key 查固定列或 entity_attribute（或按 metric_key 查 benchmark_result）-> 核对类型、单位与值 -> 核对 evidence_ids 是否属于同一实体、同一字段且 review_status=accepted -> 输出 supported / conflict / missing / unverifiable`。missing 不会当作 supported；preference 不参与事实支持率；验证不依赖模型自证。离线自动化测试已覆盖四类 Claim，真实 PostgreSQL 与正式 Release 验收仍随 2.1/2.2 执行。

### 2.4 推荐融合算法已实现（fusion-v1）

实现结果：新增三 Agent 串行编排与 `POST /api/agent/fusion`；每个 Agent 独立完成 Schema 和 Truth Verification，再由确定性 `fusion-v1` 合并候选、执行结构化硬约束/软偏好检查并输出 Top-K、淘汰原因、双层分数和 fusion trace。

`fusion-v1` 将 Truth Verification 和硬约束作为门禁，使用等权 reciprocal-rank 共识、已确认软偏好效用以及事实支持率、证明覆盖率、信息完整度和 Agent 覆盖率计算结果。`score` 仅以 `agent_scores` 进入 trace，`model_confidence` 只展示；结果记录 policy version 与 `dataset_release.release_key`。离线可靠性权重校准、Decision Result 持久化和前端展示分别留给评估阶段与 2.5。

### 2.5 决策结果、追溯与前端呈现未实现

问题：当前仍使用模型自报 `score` 和自由文本 `reasons`；没有持久化 Decision Result / Presentation Result，Agent Run 也未绑定数据 Release、Prompt 与规则版本。

改进方向：将模型分改名为 `agent_score`，理由绑定 `claim_refs/rule_refs`；持久化候选、验证状态、评分分量和 fusion trace，并记录 release_key、Prompt/模型/规则版本。前端只根据 Decision Result 生成规格表与说明，不能改写事实和排名。

### 2.6 数据填充按 P0 -> P3 铺开

问题：V3 表结构已冻结但数据几乎为空；Spec V3 §15 的 P0（RTX 30/40/50 桌面 GPU、代表性模型与 GGUF、`RB-LLM-BENCH-1`、Source/Evidence）尚未采集。

改进方向：采集按 Spec V3 §15 阶段推进，采集员操作指引见《Data_Collection_Spec_V3.2.md》。验收门槛：数据组可以在不修改 Python 与数据库列的情况下新增一个长尾字段和一个 Benchmark 指标（Spec V3 §18.6）。

### 2.7 端到端完成定义未达成

问题：Spec V3 §18 中，1–3 与 6 已具备代码基础；4（Truth Verification）、5（各类实体样例）、7（Release/Prompt/模型/规则/Agent Run 追溯）与 8（真实 PostgreSQL 演练）未完成。

改进方向：把 §17.1 样例集（CPU、四类 GPU、规格组件、Dense/MoE 模型、Workload、成功/失败 Benchmark、价格快照）纳入首个正式 Release 的审核范围，一次覆盖第 5 与第 8 条。

### 2.8 遗留偏离待决策

- truth 多张表存在 V2/V3 双列并存（如 `vram_bytes/vram_gib`、`quantization/quantization_method`），与 Spec V3 §2.1 "一个事实只有一个规范存储位置"冲突。需决定：保留为兼容层并标注 canonical 列，或在首个 Release 前清理。
- `FieldDefinitionPayload.value_type` 缺 `integer`（Spec §8.1 有）；Bundle 内 `*_spec` 对象无 JSON Schema 结构约束，未知键会落入 `extra` 列；Evidence `conflict` 多来源冲突标记未实现。
- 三项均为小改动，但处在 Schema 冻结边界上，必须在首个正式 Release 之前决定做或不做，避免切换后返工。

## Part 3：推荐实施顺序

```text
人工审核 legacy 草稿 -> 生成首个正式 Release（含 §17.1 样例集）
-> 远端 pg_dump 备份 + preflight_v3
-> 0005 原子迁移（-x allow_truth_rebuild=true）
-> Importer dry-run -> apply -> 只读权限 -> check_v3 --strict -> 幂等 dry-run
-> 端到端 Agent 查询验收（Spec V3 §18.5 / §18.8）
-> Truth Verification（Plan_V2.4 §2.3 的 V3 版）
-> 用户硬约束与软偏好检查
-> 单模型 Decision Result + 数据/Prompt/模型/规则版本追溯
-> 三模型确定性融合与可信度校准（Plan_V2.4 §2.4）
-> Presentation Result 与前端规格化展示
-> 离线评估与回归门禁（Plan_V2.4 §2.6 指标）
-> 按 P1/P2 扩大采集
```

## Part 4：验收清单

- [ ] 远端：preflight 零错误；迁移单事务成功；旧五表消失，33 张 truth 表与 12 视图齐全；应用/审计表与 `alembic_version` 保留。
- [ ] 远端：`check_v3 --strict` 通过；agent_readonly 无法访问 truth 底表与 public 表；二次 dry-run 全部 skipped。
- [ ] 数据：首个正式 Release 按 §17.1 样例集端到端导入；每个 recommendable 实体有 accepted Evidence。
- [ ] 验证：推荐中每个关键 Claim 有 supported / conflict / missing / unverifiable 状态；missing 与 conflict 不混用；验证不依赖模型自证。
- [ ] 融合：输出 recommendation_score 与 credibility 双层结果；硬约束违规候选可稳定淘汰；fusion trace 可复现并记录 release_key。
- [ ] 评估：Claim 支持率、Evidence 覆盖率、硬约束满足率、可信度校准与融合相对单模型收益可复现。
- [ ] 采集：数据组在不改 Python 与数据库列的情况下新增一个长尾字段和一个 Benchmark 指标。

V3 的完成标志：系统能说明"哪些事实已验证、候选为何适合用户、可信度如何计算、结论基于哪个数据版本"，而不是把模型自报分数当作推荐真值。
