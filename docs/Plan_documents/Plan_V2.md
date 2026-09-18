# 基于大语言模型的真值推荐系统 —— 最终架构与后期实施方案（V2）

## 0. 文档定位

本文在 `Plan_V1`、`Plan_V1.1` 和 `Data_Collection_Spec_V1` 的基础上，冻结项目的最终工程架构，并明确后期实现重点。

V2 不改变项目主题：

> **面向个人计算机硬件与本地 AI 部署的多模型真值推荐系统。**

V2 继续采用受控自主 SQL Agent：

> **LLM 决定需要查询什么并生成只读 SQL；后端负责工具协议、SQL 校验、受控执行、日志与最终事实审查；PostgreSQL Truth DB 提供可信事实。**

---

## 1. V2 冻结的五项决策

### 1.1 前后端分离是最终架构

仓库固定采用：

```text
frontend/    Vue 3 + TypeScript + Vite
backend/     FastAPI + Agent + PostgreSQL
```

FastAPI 不再承担正式前端静态资源的开发职责。开发环境中，Vue 由 Vite 独立运行并通过代理访问 FastAPI；部署时可将前端构建产物交给 Nginx 等静态服务器，并将 `/api` 反向代理到 FastAPI。

后续不再退回单体 HTML/FastAPI 静态页面，也不为了形式上的“微服务化”继续拆分后端进程。

### 1.2 多模型默认物理串行

多个 Agent 在业务上相互独立并可同时进入任务队列，但单张推理 GPU 同一时刻原则上只运行一个模型：

```text
Agent A → 加载 Model A → 完成全部 Agent 回合 → 保存 → 卸载
Agent B → 加载 Model B → 完成全部 Agent 回合 → 保存 → 卸载
Agent C → 加载 Model C → 完成全部 Agent 回合 → 保存 → 卸载
```

只有在完整流程实测表明总耗时无法满足项目要求时，才重新评估物理并行、增加推理设备或缩减 Agent 数量。不能仅凭直觉提前增加并行模型调度复杂度。

### 1.3 前端不是近期重点

近期前端只需支持：

- 提交用户需求；
- 显示请求状态和 Agent 进度；
- 展示最终推荐、理由、风险、关键证据和错误；
- 提供最小的开发/实验观察入口。

动画、复杂可视化、主题系统、移动端深度适配和大规模组件封装均后置。ECharts 仅在评估结果和推荐解释确有展示需要时使用。

### 1.4 上下文与数据库搜索是近期核心

V2 优先解决：

1. 用户会话历史如何持久化、组装和压缩；
2. 多 Agent 之间如何保持推理独立；
3. 模型如何稳定地产生数据库工具调用；
4. SQL 如何被后端安全校验和执行；
5. 查询结果如何带证据返回并被模型正确利用。

### 1.5 真实性、融合与评估是后期重点

以下能力当前尚未实现，不得在演示或文档中描述为已完成：

- Claims Extraction / Truth Verification；
- 用户硬约束的独立校验；
- MTRF 多模型真值感知融合；
- 模型可靠性权重；
- 系统性性能评估和效果评估。

这些能力在单模型 SQL Agent 闭环稳定后依次实现，是后半程的核心研究与课程成果。

---

## 2. 当前实现基线

### 2.1 已实现

- `frontend/` 与 `backend/` 独立目录；
- Vue 3、TypeScript、Vite、Vue Router、Pinia、Axios、Element Plus 基础框架；
- FastAPI、Pydantic Settings、SQLAlchemy、Alembic；
- PostgreSQL 五张 Truth DB 核心表：`hardware`、`ai_model`、`model_variant`、`evidence`、`benchmark`；
- FastAPI 到另一台设备 `llama-server` 的 OpenAI-compatible HTTP 调用；
- 单进程内的异步 GPU 串行锁；
- 数据采集与标准化输入规范；
- Agent 状态、上下文存储接口、数据库工具 JSON 契约的代码骨架。

### 2.2 已预留但尚未实现

- 远程模型加载、健康检查、卸载和异常清理；
- 持久化会话和自动上下文压缩；
- `inspect_database()` 的正式实现；
- `query_database(sql)`、SQL Parser、Validator 和只读 Executor；
- 单 Agent 多轮工具循环；
- Agent Run、Tool Call 和资源指标的持久化日志。

### 2.3 尚未进入实现阶段

- Recommendation JSON 的完整业务 Schema；
- Truth Verification；
- Constraint Validation；
- MTRF；
- 完整 Baseline、消融实验和性能/效果评估；
- 最终推荐解释页面。

---

## 3. 最终系统架构

```text
Browser
   ↓
frontend/：Vue 3 + TypeScript + Pinia + Axios
   ↓ HTTP / SSE（按需）
backend/：FastAPI Application Server
   │
   ├── API Layer
   │     ├── Conversation API
   │     ├── Recommendation API
   │     └── Agent Run Status API
   │
   ├── Conversation Context Service
   │     ├── 原始消息
   │     ├── 结构化用户约束
   │     ├── Context Snapshot
   │     └── 自动压缩
   │
   ├── Agent Controller
   │     ├── Tool Protocol Parser
   │     ├── inspect_database()
   │     ├── query_database(sql)
   │     └── Agent Loop / 终止条件
   │
   ├── Inference Scheduler
   │     ├── 逻辑任务队列
   │     ├── 物理串行锁
   │     ├── Router 状态监督与模型 ID 选择
   │     └── 失败隔离与指标记录
   │
   ├── Truth Verification
   ├── Constraint Validation
   ├── MTRF Fusion
   ├── Evidence Service
   └── Evaluation Service
   │
   ├──────── SQL ────────→ PostgreSQL Truth DB
   │
   └──────── HTTP ───────→ Inference Server
                              └── llama.cpp / llama-server
                                  └── GGUF Model A / B / C
```

Application Server 保存业务状态、数据库和审计记录；Inference Server 运行一个 llama.cpp Router，并由 Router 负责模型实例的发现、加载、卸载和转发。LLM 不持有数据库凭据，也不直接访问数据库或文件系统。

---

## 4. 最终仓库边界

```text
RigBuilder/
├── frontend/
│   ├── src/api/             前端 API 封装
│   ├── src/router/          页面路由
│   ├── src/stores/          会话和运行状态
│   ├── src/views/           需求、进度、结果、实验页面
│   └── .env.example         前端 API 地址与超时
│
├── backend/
│   ├── app/api/             FastAPI 路由与 DTO 边界
│   ├── app/services/        应用用例编排
│   ├── app/inference/       串行调度与 Router 监督
│   ├── app/agents/          Agent Controller 与状态机
│   ├── app/context/         会话、摘要、上下文组装
│   ├── app/tools/           数据库工具协议与执行
│   ├── app/models/          SQLAlchemy 模型
│   ├── app/db/              数据库连接与事务
│   ├── alembic/             Schema 迁移
│   ├── scripts/             导入与维护脚本
│   └── .env.example         后端、数据库和推理配置
│
├── data/
│   ├── raw/                 原始材料，不入 Git/不直接入库
│   └── standardized/        已核验 JSON，唯一导入源
│
└── docs/
    ├── Plan_V1.md
    ├── Plan_V1.1.md
    ├── Plan_V2.md
    └── Data_Collection_Spec_V1.md
```

该边界作为最终架构冻结；允许在各目录内部增加模块，但不再改变前后端顶层分离方式。

---

## 5. 模型调用与物理串行设计

### 5.1 调度状态

每个 Agent Run 使用统一状态：

```text
queued
  → loading_model
  → running
  → saving_result
  → unloading_model
  → completed

任一阶段 → failed → cleanup
```

必须记录：

```text
request_id / conversation_id / agent_run_id
model_id / model_path / quantization
queue_wait_ms
model_load_ms / model_unload_ms
inference_ms / total_ms
input_tokens / output_tokens（可估算）
tool_call_count
peak_vram_gb / peak_ram_gb
status / error_code
```

### 5.2 串行锁的部署约束

当前进程内 `asyncio.Lock` 只对单个 FastAPI 进程有效。在数据库分布式锁或其他跨进程协调机制实现前，FastAPI 只能启动一个负责推理调度的 worker。

Inference Server 不再需要自建 Model Manager。它运行一个 `llama-server` Router：`--models-max 1` 约束最多一个已加载模型，Router 负责模型实例的加载、卸载与子进程生命周期。Application Server 仍保持全局 Agent Run 锁，保证 Agent A 的多轮模型调用、SQL 工具调用和最终输出完成后，才启动 Agent B；Router 不替代该业务顺序控制。具体部署与验收见 [Inference Server Guide](../Inference_Server_Guide_V1.md)。

### 5.3 先优化串行，再考虑并行

在决定物理并行前，依次尝试：

1. 同模型任务批处理，减少重复加载；
2. 设置短时 Keep-Warm，减少频繁装卸；
3. 减少无效 Agent 轮次和重复 SQL；
4. 缩短上下文，限制 Observation 体积；
5. 选择更合适的量化、上下文长度和 GPU offload；
6. 对低价值场景减少参与 Agent 数量；
7. 将非 GPU 的 SQL、验证和日志阶段与下一步准备逻辑重叠执行。

只有在记录 P50/P95 总耗时、排队时间、模型装载占比和 GPU 利用率后，仍无法满足需求，才评估第二张 GPU、多推理服务器或受显存约束的有限并行。

---

## 6. 模型上下文设计

### 6.1 两种上下文必须分离

1. **用户会话上下文**：保存持续需求、预算、已有硬件、偏好和澄清结果；
2. **Agent Run 上下文**：保存某一个模型独立的工具调用、Observation、错误修正和最终输出。

Agent A、B、C 不共享彼此的内部推理历史。它们只共享后端确认过的用户约束、允许的数据库 Schema 和同一 Truth DB，以保证多模型比较公平。

### 6.2 建议持久化实体

```text
conversation
conversation_message
conversation_context_snapshot
user_constraint
agent_run
agent_message
tool_call
```

原始消息用于审计，不因摘要而删除；Context Snapshot 是可重建、带版本号的派生数据。

### 6.3 每次调用的上下文顺序

```text
System Prompt
→ 工具协议与安全规则
→ 当前 Conversation Summary
→ 结构化用户硬约束/软偏好
→ 已确认 Truth Facts 与 Evidence 引用
→ 当前 Agent 最近 N 轮消息
→ 当前用户消息或 Tool Result
```

预算分配必须显式化，至少区分：系统与工具预算、摘要预算、最近对话预算、Observation 预算和输出预算。

### 6.4 自动压缩

初始策略沿用 V1.1：上下文预计达到模型窗口的约 70% 时触发，保留最近 4～6 轮原始消息。

压缩输出使用固定 JSON Schema：

```json
{
  "user_goal": "",
  "hard_constraints": [],
  "soft_preferences": [],
  "existing_hardware": [],
  "confirmed_facts": [],
  "rejected_options": [],
  "pending_questions": [],
  "agent_progress": ""
}
```

压缩时必须满足：

- 用户硬约束以结构化记录为准，摘要不得覆盖或改写；
- Truth DB 事实保留实体 ID、字段、值和 Evidence ID；
- Tool Result 只压缩展示内容，原始 SQL 与完整结果仍保存在日志；
- 新摘要通过 JSON Schema 和关键字段完整性校验后才能替换旧摘要；
- 压缩失败时保留旧快照，不能写入不完整结果；
- 记录压缩前后 token、耗时、摘要版本和使用的模型。

### 6.5 上下文评估

需要单独建设多轮对话测试集，测量：

- 预算、品牌、已有硬件等硬约束保留率；
- 压缩前后事实一致率；
- 旧约束被新信息明确修改时的更新正确率；
- 上下文 token 降幅；
- 压缩额外延迟；
- 不同模型在相同上下文上的输出稳定性。

---

## 7. 数据库搜索与受控 SQL Agent

### 7.1 数据入口必须遵循数据规范

Truth DB 的数据流保持：

```text
官方页面 / 项目实测
    ↓
data/raw/（不直接入库）
    ↓ 人工核验和标准化
data/standardized/*.json（唯一导入源）
    ↓ backend/scripts/import_data.py
PostgreSQL
```

标准化数据继续使用自然键解析 UUID，并严格区分：

```text
official > measured > derived > LLM 参数知识
```

`evidence` 必须保存字段级来源；`benchmark` 只能保存项目真实测试，并冻结延迟单位和完整测试环境。

### 7.2 工具保持最小化

V2 首先只提供：

```text
inspect_database()
query_database(sql)
```

`inspect_database()` 只返回五张白名单表的业务字段、类型、主外键和语义说明。`query_database(sql)` 只接受一个只读查询，并返回结构化列、行数、数据和可恢复错误。

不同本地模型的原生 Function Calling 能力不一致，因此上层统一使用 JSON 工具协议；某个模型的协议差异由 LLM Gateway Adapter 消化，Agent Controller 不直接依赖 llama.cpp 的某个具体响应字段。

### 7.3 SQL Validator 不能只依赖正则表达式

推荐采用 PostgreSQL SQL Parser/AST 完成语句分类和实体提取，再实施：

- 仅允许一个 `SELECT` 或 `WITH ... SELECT`；
- 禁止 DDL、DML、事务控制、多语句和绕过型注释；
- 仅允许五张业务表和明确字段；
- 限制 JOIN、子查询、SQL 长度和返回字段数；
- 自动或外层强制最多 100 行；
- `statement_timeout = 3000 ms`；
- 数据库会话设为只读事务；
- 使用只有 `SELECT` 权限的 `agent_readonly` 账号；
- 错误信息结构化且不泄露连接信息。

数据库权限是最后一道安全边界；即使 Validator 出现缺陷，`agent_readonly` 也不能写库。

### 7.4 Observation 设计

查询结果不能无限加入上下文。Observation 至少包含：

```json
{
  "success": true,
  "columns": [],
  "rows": [],
  "row_count": 0,
  "truncated": false,
  "query_id": "",
  "evidence_ids": []
}
```

后端需要限制单元格长度、总字符数和总 token；被截断时明确告诉模型，不允许模型把“未返回”理解成“不存在”。关键结论应尽量查询并返回 Evidence，而不是只给实体值。

### 7.5 单 Agent 最小循环

```text
用户需求 + 上下文
→ LLM
→ inspect_database / query_database
→ JSON Schema 校验
→ SQL Validator
→ Read-only Executor
→ Tool Result
→ LLM 自我修正或继续查询
→ Recommendation JSON
```

默认限制：最多 8 个 Agent 回合、6 次 SQL 调用、单次 SQL 3 秒，并增加 Agent 总超时。达到限制后必须用已有信息输出或明确标记信息不足。

### 7.6 数据库搜索评估

建立带标准答案的查询任务集，至少覆盖：

- 单表过滤与排序；
- 模型、变体、硬件 JOIN；
- Evidence 查询；
- Benchmark 条件筛选；
- 空结果与信息不足；
- 错列名后的自我修正；
- 恶意或越权查询拒绝。

指标包括 SQL 语法成功率、语义正确率、工具调用次数、自我修正成功率、查询耗时、有效结果利用率和越权拦截率。

---

## 8. 结构化推荐输出

在实现真实性检查前，先冻结 Recommendation JSON Schema。至少包含：

```json
{
  "recommendations": [
    {
      "candidate_id": "",
      "candidate_type": "hardware | model_variant | build",
      "score": 0,
      "confidence": 0.0,
      "reasons": [],
      "risks": [],
      "claims": [
        {
          "entity_type": "",
          "entity_id": "",
          "field": "",
          "value": null,
          "evidence_ids": []
        }
      ]
    }
  ],
  "insufficient_information": [],
  "tool_summary": {}
}
```

模型输出先经过 JSON Schema 校验和有限修复；无法修复时标记 Agent 失败，不能直接把自由文本送入融合算法。

---

## 9. 后期重点一：真实性与约束检查

### 9.1 Truth Verification

真实性检查必须独立于生成该推荐的 Agent：

```text
Recommendation Claims
→ Entity Resolver
→ Truth DB 精确查询
→ 值、单位、类型与 Evidence 比较
→ supported / conflict / missing / unverifiable
→ S_fact
```

即使 Agent 曾经查询过数据库，也必须重新检查最终 Claims，因为模型可能在最终生成时混淆实体、忽略 Observation 或重新产生幻觉。

验证结果至少记录 Claim、数据库真值、比较规则、Evidence、状态和原因。`missing` 与 `conflict` 必须区分：没有数据不能被当作错误事实，也不能被当作已验证事实。

### 9.2 Constraint Validation

用户硬约束从会话中结构化提取并允许用户确认，例如：

- 总预算；
- GPU 品牌；
- 最低 VRAM/RAM；
- 是否必须支持视觉或代码；
- 已有硬件不可替换；
- 功耗或平台限制。

后端根据 Truth DB 和规则验证，不采信模型的“满足约束”陈述。违反关键硬约束的候选直接淘汰；软偏好只参与评分。

---

## 10. 后期重点二：MTRF 多模型融合

MTRF 只在以下条件满足后实现：

1. 多个 Agent 输出统一 Schema；
2. Claims 可以稳定验证；
3. 用户硬约束可以独立判断；
4. 已有一套离线评估集用于估计模型可靠性。

基础分量沿用 V1：

```text
S_fact          事实一致性
S_constraint    用户约束满足度
S_preference    软偏好匹配度
S_consensus     多模型共识
S_model         模型离线可靠性
```

```text
S(x) = αS_fact + βS_constraint + γS_preference + δS_consensus + εS_model
```

实现时必须注意：

- 硬约束可作为淘汰规则，不应仅被低权重稀释；
- 失败 Agent 不计入“未推荐”，共识分母使用实际有效 Agent 数；
- 同一错误被多个模型重复不能变成真值，共识不能覆盖事实冲突；
- `w_m` 来自离线测试，不由模型自报 confidence 决定；
- 权重和阈值必须版本化，并通过消融实验说明贡献；
- 最终 Top-K 结果保留每个分量和淘汰原因，支持解释与复现。

---

## 11. 后期重点三：性能与效果评估

### 11.1 对比组

保持四组实验：

1. **LLM Only**：只依赖模型参数知识；
2. **LLM + Fixed Truth Context**：后端一次性提供固定事实；
3. **Single SQL Agent**：单模型自主查询 Truth DB；
4. **Multi-SQL-Agent + Verification + MTRF**：完整方案。

必要时增加消融组：无上下文压缩、无 Truth Verification、无模型可靠性权重、无共识分量。

### 11.2 指标体系

**真实性：**

- Claim 事实准确率；
- 幻觉率；
- Evidence 覆盖率；
- Truth Verification 通过率、冲突率和缺失率。

**数据库 Agent：**

- SQL 语法/语义正确率；
- 自我修正成功率；
- 平均工具调用次数；
- 查询结果有效利用率；
- 越权查询拦截率。

**上下文：**

- 硬约束保留率；
- 压缩事实一致率；
- token 压缩率；
- 压缩延迟；
- 长对话推荐稳定性。

**推荐：**

- 硬约束满足率；
- 推荐有效率；
- Top-K 命中率；
- 多模型一致性；
- 重复运行稳定性。

**性能：**

- 模型加载/卸载时间；
- 排队时间；
- 首 Token 延迟和总推理时间；
- SQL 延迟；
- 端到端 P50/P95；
- GPU VRAM、RAM、CPU、Token/s；
- 单请求总 token 与上下文压缩次数。

### 11.3 可复现性

每次实验保存：模型文件标识与量化版本、llama.cpp build、采样参数、上下文长度、GPU offload、测试数据版本、数据库快照/迁移版本、Prompt 版本、MTRF 权重版本和随机种子（模型支持时）。

所有日志通过 `request_id`、`conversation_id`、`agent_run_id`、`tool_call_id` 串联。

---

## 12. API 与数据契约冻结顺序

在多人继续并行开发前，按以下顺序冻结：

1. Data Standardized JSON Schema；
2. Conversation / Message / Constraint Schema；
3. LLM Gateway 内部消息格式；
4. `inspect_database()` Schema；
5. `query_database(sql)` 与 Tool Result Schema；
6. SQL Validator 规则与错误码；
7. Recommendation JSON Schema；
8. Truth Verification Claim/Result Schema；
9. Agent Run 状态与日志 Schema；
10. MTRF 输入、分量和输出 Schema；
11. 前端 API DTO。

前端 DTO 放在最后，是因为前端只负责呈现稳定的后端业务结果，不应反向决定 Agent 和算法内部结构。

---

## 13. 后期实施路线与优先级

### P0：数据入口可复现

- 修正并冻结 `data/standardized/` 五类 JSON；
- 实现 `backend/scripts/import_data.py`；
- 支持 Schema 校验、自然键解析、单事务、`--dry-run`；
- 导入一批真实官方数据与少量真实 Benchmark。

完成标准：数据库可由 Git 中的标准化数据稳定重建，所有关键事实可追溯到 Evidence。

### P1：会话与上下文

- 增加 Conversation、Message、Context Snapshot、Constraint 表；
- API 接受并返回 `conversation_id`；
- 保存原始消息并组装最近对话；
- 实现 token 预算和自动压缩；
- 建立上下文保真测试。

完成标准：经过多轮对话和至少一次压缩后，用户硬约束不会丢失或被篡改。

### P2：单模型 SQL Agent 最小闭环

- 实现 `inspect_database()`；
- 创建 `agent_readonly`；
- 实现 Parser、Validator、Executor；
- 实现统一 JSON Tool Protocol；
- 实现 Agent Loop、限制、错误自修正与日志。

完成标准：单模型能针对测试问题自主查询数据库并输出符合 Schema 的 Recommendation JSON。

### P3：真实性与约束

- 实现 Claims Extraction/标准化；
- 实现 Truth Verification；
- 实现 Evidence Lookup；
- 实现用户硬约束和软偏好检查；
- 输出可解释的验证详情。

完成标准：推荐中的每个关键 Claim 都有 supported/conflict/missing/unverifiable 状态，硬约束违规候选可稳定淘汰。

### P4：多模型物理串行

- 部署一个 `llama-server` Router，使用 `--models-max 1`；
- 实现 Router Adapter / Supervisor，读取模型目录、模型状态和加载失败信息；
- 实现 Agent Run 全生命周期队列、全局锁和 `finally` 清理；
- 顺序运行 Agent A/B/C；
- 保证 Agent 上下文隔离；
- 记录模型生命周期和资源指标。

完成标准：一次请求可依次运行多个模型；单模型失败不影响已完成结果；GPU 不同时加载多个目标模型。

### P5：MTRF 与评估

- 实现各评分分量；
- 生成模型可靠性权重；
- 进行 Baseline 和消融实验；
- 评估上下文、SQL Agent、真实性、推荐与性能；
- 根据串行流程实测决定是否需要并行优化。

完成标准：权重、指标和 Top-K 结果可复现，能够用实验回答“Truth DB、SQL Agent、真实性检查和多模型融合分别带来了什么”。

### P6：前端完善

- 接入会话 ID 和运行状态；
- 展示多 Agent 阶段进度；
- 展示 Top-K、证据、验证状态、约束和评分分量；
- 增加必要的实验图表；
- 完成错误、空结果和超时状态。

该阶段不应阻塞 P0～P5。

---

## 14. 项目边界

V2 仍不做：

- 开放式 Shell Agent；
- LLM 直接持有数据库凭据；
- LLM 执行写 SQL；
- 自动联网采集后直接写 Truth DB；
- 未经人工核验的数据自动成为官方真值；
- Kubernetes、复杂微服务和消息队列；
- 全量电商 SKU 与实时电商价格；
- 为追求界面效果而提前消耗核心算法开发时间；
- 在没有性能数据时提前实施多模型物理并行。

---

## 15. V2 最终结论

> **RigBuilder 的最终工程架构确定为 Vue 前端与 FastAPI 后端分离，Application Server 负责上下文、Agent、受控 SQL、Truth DB、真实性验证、融合和评估，Inference Server 负责本地 GGUF 模型推理。多模型在业务上独立排队、在单 GPU 上默认物理串行。近期优先完成会话上下文和数据库搜索闭环；随后实现独立 Truth Verification、约束检查、MTRF 及系统性性能与效果评估。前端只围绕核心流程提供必要交互和解释展示。**
