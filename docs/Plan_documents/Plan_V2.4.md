# RigBuilder Plan V2.4：推荐可信度定义与结果呈现

> 状态：规划中。日期：2026-09-02。前置版本：[Plan_V2.3.md](Plan_V2.3.md)。

V2.4 不重做现有 SQL Agent，而是在 V2.2/V2.3 基础上重新聚焦题目核心：当推荐没有唯一真值时，系统如何验证事实、判断候选是否适合用户，并给出可解释、可复现的推荐可信度。

## Part 1：已完成部分

### 1. Web 与推理基础链路

- 已完成 Vue 前端与 FastAPI 后端分离架构。
- 已具备本地 LLM 调用、会话持久化、上下文管理和多模型串行对比能力。
- 已建立硬件、本地 AI 模型、模型变体、Evidence 和 Benchmark 的 Truth DB 模型、迁移及数据导入入口。

### 2. 受控 SQL Agent 基线

- 已实现 `inspect_database()` 和 `query_database(sql)` 工具协议。
- 已实现五表白名单、只读数据库连接设计、SQL Validator、查询超时、行数截断和错误分类。
- 已实现 `/api/agent/run`，模型可以查询 Truth DB 后输出结构化推荐。
- 已建立 `agent_run`、`agent_message` 和 `tool_call` 审计记录，可保存运行过程和工具调用信息。

### 3. Recommendation 结构化输出

- 已有 Recommendation Schema，包含候选、模型评分、推荐理由、风险、Claims、Evidence 引用和信息不足项。
- 后端可以校验模型最终输出；格式不合法时可要求模型修正或将 Agent Run 标记为失败。
- 该 Schema 已为 Truth Verification、融合和前端展示提供统一输入，但 UUID 类型、证据绑定和评分含义仍需在 V2.4 中收尾。

## Part 2：未完成部分与针对性问题

### 1. 推荐“真值”尚未明确定义

问题：推荐通常没有唯一正确答案。官方数据只能证明硬件参数等事实，不能直接证明“某个候选是唯一最佳推荐”。当前 `score` 和 `model_confidence` 主要由模型自报，不能直接作为最终推荐分数或可信度。

改进方向：将推荐结果拆成四个可解释部分：

- **事实真实性**：Claims 是否被 Truth DB 和 Evidence 支持；
- **约束有效性**：候选是否满足预算、显存、功耗和兼容性等硬约束；
- **推荐效用**：候选对当前用户场景的适合程度；
- **推荐可信度**：事实支持、证据覆盖、信息完整度和可靠模型共识是否充分。

最终对用户分别展示 `recommendation_score` 和 `credibility`。模型自报分数改为 `agent_score`，仅作为单模型意见，不直接决定最终排名。

### 2. Recommendation Schema 仍需收尾

问题：当前候选 ID、Claim 实体 ID 和 Evidence ID 尚未全部使用 UUID；自然语言理由也没有强制关联到事实或业务规则。

改进方向：

- 将 `candidate_id`、`claims[].entity_id` 和 `evidence_ids` 改为 UUID；
- 为推荐理由增加 `claim_refs` 或 `rule_refs`，避免无依据的自然语言理由进入最终结果；
- 区分 `fact`、`derived` 和 `preference`：原子事实由 Truth DB 验证，推导结论由后端规则计算，偏好判断只用于推荐效用；
- LLM 的最终机器输出仍为纯 Recommendation JSON，不在 JSON 外附加自由文本。

### 3. Truth Verification 尚未实现

问题：模型查询过数据库不代表最终 Claims 一定正确。模型仍可能写错实体、字段、值或引用不匹配的 Evidence。

改进方向：实现独立于生成 Agent 的验证器：

```text
Claim
  -> 校验实体与字段
  -> 后端精确查询 Truth DB
  -> 按类型、单位和比较规则核对值
  -> 校验 Evidence 是否属于同一实体、字段和值
  -> supported / conflict / missing / unverifiable
```

候选级结果保留事实支持率、Evidence 覆盖率、冲突数和缺失数。关键 Claim 冲突时淘汰或显著降低可信度；`missing` 不得被当作 `supported`，也不能与 `conflict` 混为一类。

### 4. 多模型融合尚未实现

问题：最终结果不应简单选择某一个模型，也不应再让另一个 LLM 自由总结三个答案。多个模型达成共识也不能把共同错误变成真值。

改进方向：三个 Agent 独立输出并分别完成 Schema 与 Truth Verification 后，由后端确定性算法融合：

1. 按 `candidate_type + candidate_id` 合并候选；
2. 淘汰违反用户硬约束或存在关键事实冲突的候选；
3. 根据已验证事实和用户偏好计算推荐效用；
4. 使用离线可靠性权重和候选排名计算多模型共识；
5. 输出最终 Top-K，同时保留各模型来源、评分分量、分歧和淘汰原因。

默认支持单模型结果，具备评估集后再启用三模型权重。`model_confidence` 只展示，不参与融合。

### 5. 前端结果需要兼顾规格化与自然语言

问题：直接展示原始 JSON 不利于用户理解，完全使用自然语言又难以验证和复现。

改进方向：采用双层结果：

- **Decision Result**：保存候选、Claims、验证状态、约束、评分分量、Evidence 和 fusion trace，作为唯一决策依据；
- **Presentation Result**：供前端展示需求复述、场景分析、推荐摘要、理由、风险和规格化配置表。

第一版优先用后端模板将已验证数据生成自然语言。后续可增加受限的 Narrator LLM 做语言润色，但它不能修改候选、排名、参数、风险或新增事实；生成失败时回退模板结果。

前端至少展示：

- 用户需求与已确认约束；
- 场景分析和信息不足项；
- Top-K 推荐、推荐分和可信度；
- 规格化配置表；
- 推荐理由、风险、Evidence、模型共识与分歧。

### 6. 真实验收与无唯一答案评估尚未完成

问题：当前缺少真实 PostgreSQL / LLM 验收记录，也缺少适合多解推荐的固定评估集。以唯一标准答案计算准确率不适用于本项目。

改进方向：

- 先完成 V2.3 的 UUID、审计关联、只读角色幂等、Validator 边界和 DB-01～DB-08 验收；
- 建立带用户约束、可接受候选集合和分级相关性的固定用例；
- 记录 Claim 支持率、Evidence 覆盖率、硬约束满足率、Recall/NDCG、可信度校准和融合相对单模型的收益；
- 所有评估保存数据版本、Prompt 版本、模型版本、权重版本和审计 ID，保证结果可复现。

### 7. 推荐实施顺序

```text
V2.2/V2.3 收尾与真实 DB 验收
  -> 修订 Recommendation Schema
  -> 原子事实 Truth Verification
  -> 用户约束与规则验证
  -> 单模型 Decision Result
  -> 前端规格化展示与模板化说明
  -> 固定评估集
  -> 三模型确定性融合与可信度校准
```

V2.4 的完成标志是：系统不再把模型自报分数当作推荐真值，而是能够说明“哪些事实已验证、候选为何适合用户、可信度如何计算、多个模型如何形成最终结果”，并在前端以自然语言说明和规格化配置共同呈现。
