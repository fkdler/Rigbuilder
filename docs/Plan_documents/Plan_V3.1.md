# RigBuilder Plan V3.1

> 状态快照：2026-09-07。依据：真实 PostgreSQL `check_v3 --strict` 与后端离线测试。本文只说明当前实现状态和后续规划。

## 已完成

- **Truth DB V3.1 已切换并验收**：数据库版本为 `0006_freeze_truth_v3_1`；33 张 `truth` 表、12 个 `agent_catalog` 只读视图、1 个正式 Release 均存在。95 个可推荐实体全部拥有 accepted Evidence；`agent_readonly` 仅可读取视图，事务只读、搜索路径和权限边界检查均通过。
- **当前数据已导入，但仍不完整**：14 个基础模型、95 个硬件、39 个模型变体、23 个组织、13 个 Workload、799 条 accepted Evidence、80 条价格已入库。当前 `Benchmark Run`、`Metric Definition`、`Entity Attribute` 均为 0。
- **无需 LLM 的基础链路可用**：会话持久化、上下文组装、确认约束注入、`inspect_database`、SQL 白名单校验、只读查询和 Agent 审计均已实现。`LLM_CONTEXT_WINDOW_TOKENS` 未配置，因此自动摘要压缩默认关闭。
- **Truth Verification 已实现**：后端独立验证 Fact、Measurement、Derived、Preference Claim。它通过 ORM 白名单读取 Truth DB，核对实体、字段、类型、单位、值、accepted Evidence、Benchmark Run 和版本化 Rule；输出 `supported / conflict / missing / unverifiable`。真实数据已提供 Release 与 Evidence 基础，但 Measurement、Attribute 和生产 Rule 尚无真实数据覆盖。
- **融合推荐 `fusion-v1` 已实现**：三个 Agent 独立输出后，按 `candidate_type + candidate_id` 合并候选。实体无效、Truth conflict，以及硬约束为 `violated` 或 `unknown` 的候选直接淘汰。

  - 共识采用有效 Agent 的等权 reciprocal-rank；失败 Agent 不进入共识分母，但会降低 Agent coverage。
  - 有软偏好时，`recommendation_score = 100 × (0.70 × preference_utility + 0.30 × consensus)`；无软偏好时只使用 consensus。
  - `credibility = 100 × (0.40 × fact_support + 0.25 × proof_coverage + 0.20 × information_completeness + 0.15 × consensus × agent_coverage)`。
  - `score` 仅作为 Agent 自报分记录，`model_confidence` 仅展示；两者均不参与融合。结果输出 Top-K、淘汰原因、来源模型、评分分量、`release_key`、策略版本和可重放 trace。

- **验证证据**：`check_v3 --strict` 已通过；离线测试为 `120 passed, 1 skipped`。跳过项是显式启用后才运行的真实 PostgreSQL Truth Verification 冒烟测试。

## 未完成（含规划）

- **LLM 实机验收**：三模型 ID 已配置，但 `LLM_MODEL` 为空，尚未连接并验证真实 LLM。后续依次验收单 Agent、三 Agent 部分失败和完整 `/api/agent/fusion` 链路。
- **上下文压缩**：先测定目标模型的上下文窗口，再配置 `LLM_CONTEXT_WINDOW_TOKENS`，启用自动摘要，并建立长会话中约束保留与事实一致性的回归用例。
- **数据持续采集与完善**：这是核心未完成项。优先补齐 Metric Definition、Benchmark Protocol/Run/Metric、长尾 Attribute，以及 Spec V3 P0 的 GPU、模型变体、价格和 Source/Evidence。每批数据均经 Release、Importer dry-run 和 strict check 验收。
- **真值验证实数验收**：为 Measurement、Attribute 补充真实样例；为 Derived 注册生产规则并验证输入事实、规则版本与结果可追溯。
- **融合优化**：先建设固定评估集和离线指标，再以校准后的模型可靠性权重替换等权；随后扩展连续偏好效用、候选多样性、模型相关性去重和 Benchmark 条件匹配。
- **决策结果与前端**：尚未持久化 Decision Result / Presentation Result，Agent Run 也未完整绑定 Release、Prompt 和规则版本。后续新增可重放结果存储，并由前端只读展示规格、理由、风险和 trace。
- **评估门禁**：建立 Claim 支持率、Evidence 覆盖率、硬约束满足率、NDCG/Recall、可信度校准和融合相对单模型收益的离线回归门禁。
