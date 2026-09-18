# 基于大语言模型的真值推荐系统 —— V1.1 改进说明

## 1. 定位与修改范围

V1.1 不改变 V1 的核心原则：**本地 LLM 可以决定查询什么，应用后端决定 SQL 是否可以执行，Truth DB 是事实唯一来源。**

本版只细化模型调用相关的三个改进方向：

1. 多模型 Agent 的调度与显存管理；
2. 对话内上下文保存与自动压缩；
3. 面向 Truth DB 的受控工具协议。

本版仍不允许模型直接连接 PostgreSQL、直接持有数据库凭据、执行 Shell 命令或执行写操作。

---

## 2. 多模型：逻辑并发、物理串行

### 2.1 问题

系统希望让 Agent A、Agent B、Agent C 针对同一个用户请求独立给出推荐。但推理服务器的单张 GPU 显存不足以同时常驻多个 GGUF 模型；若并行加载，会出现显存不足、频繁换页或显著性能下降。

因此 V1.1 不采用“多模型物理并行推理”。

### 2.2 调度原则

多个 Agent 在业务层是独立任务，即可以同时创建、排队、记录状态、等待结果和汇总结果；但模型加载与推理在 GPU 上严格串行。

```text
同一用户请求
    ↓
Agent Controller 创建 Agent A / B / C 任务
    ↓
Inference Scheduler（按队列取一个任务）
    ↓
┌──────────────────────────────────────────┐
│ Agent A                                   │
│   加载 Model A → 多轮推理 / SQL 工具调用  │
│   → 保存完整结果与日志 → 卸载 Model A     │
├──────────────────────────────────────────┤
│ Agent B                                   │
│   加载 Model B → 多轮推理 / SQL 工具调用  │
│   → 保存完整结果与日志 → 卸载 Model B     │
├──────────────────────────────────────────┤
│ Agent C                                   │
│   加载 Model C → 多轮推理 / SQL 工具调用  │
│   → 保存完整结果与日志 → 卸载 Model C     │
└──────────────────────────────────────────┘
    ↓
全部 Agent 结果就绪后进行 Truth Verification、约束检查和 MTRF
```

这里的“逻辑并发”仅表示多个任务可同时存在；**同一时刻只允许一个模型占用推理 GPU**。

### 2.3 模型任务状态

每个 Agent Run 至少具有以下状态，便于前端显示、失败恢复和实验统计：

```text
queued → loading_model → running → saving_result → unloading_model → completed
                                      ↓
                                    failed
```

建议记录：

```text
request_id
agent_run_id
model_id / model_path
queue_entered_at
model_loaded_at
started_at
completed_at
unloaded_at
status
error
peak_vram_gb
total_inference_seconds
tool_call_count
```

### 2.4 推理服务器职责

Application Server 保持 FastAPI、PostgreSQL、Agent Controller、SQL Validator 和日志；Inference Server 只负责加载某一个 GGUF 模型并提供 `llama-server` HTTP 服务。

一次模型切换的最小过程：

1. Scheduler 确认 GPU 空闲；
2. 启动（或切换到）对应模型的 `llama-server`；
3. 轮询其健康检查，确认模型加载完成；
4. Application Server 通过局域网 HTTP 运行该 Agent 的全部推理回合；
5. 持久化 Agent 结果、工具日志和资源指标；
6. 正常停止该服务并确认显存释放；
7. Scheduler 开始队列中的下一个 Agent。

同一模型若连续对应多个任务，可以在任务批次结束后再卸载，以减少加载开销；但必须设置最大空闲时间和显存释放策略。V1.1 默认优先保证实验可控性，而非追求最大吞吐量。

### 2.5 失败与隔离

- 一个 Agent 超时、模型加载失败或输出不合法，只标记该 Agent 为 `failed`，不阻塞已完成 Agent 的结果。
- 发生异常时仍须执行卸载/清理动作，并记录错误与资源数据。
- 为每个 Agent 设置最大总轮数、最大工具调用次数和总推理时间；V1 建议值仍为 8 轮、6 次 SQL 调用、单次 SQL 3 秒。
- MTRF 必须记录实际参与融合的 Agent 数量，不能将失败 Agent 视为“未推荐”。

---

## 3. 对话上下文：持久化、分层、自动压缩

### 3.1 问题

HTTP 接口本身是无状态的。浏览器显示过往消息，不代表模型在下一轮仍能看到它们。若每轮都把全量历史发送给模型，上下文会持续增长，增加延迟、显存压力和模型遗忘关键约束的风险。

因此 V1.1 的上下文由 Application Server 管理，不能依赖浏览器或推理服务器保存。

### 3.2 会话数据模型

每次浏览器会话对应一个 `conversation_id`；每次发送消息对应一个 `turn_id`。建议新增以下逻辑实体（可在后续迁移中落为表）：

```text
conversation
  id, user/session identifier, created_at, updated_at, active_summary, summary_version

conversation_message
  id, conversation_id, turn_id, role, content, created_at, token_estimate

conversation_context_snapshot
  id, conversation_id, version, summary, preserved_facts, created_at
```

`conversation_message` 保存可审计的原始对话；压缩只生成摘要快照，**不删除原始记录**。

### 3.3 每次模型调用的上下文组成

每轮提交给模型的消息按以下顺序组装：

```text
1. 固定 System Prompt
2. 当前项目/Agent 的工具协议与安全约束
3. 已压缩的会话摘要（如存在）
4. 必须保留的结构化事实
   - 用户硬约束：预算、已有硬件、品牌限制、显存下限等
   - 已确认的推荐候选与排除原因
   - 已执行工具调用的关键 Observation 摘要
5. 最近 N 轮原始消息
6. 当前用户消息或当前工具 Observation
```

其中“用户硬约束”和经 Truth DB 取得的事实必须以结构化形式保留，不能仅依靠自然语言摘要，以免压缩时改变含义。

### 3.4 自动压缩策略

当“已有摘要 + 最近消息 + 本轮预计输入”的 token 估算接近该模型可用上下文预算时触发压缩。建议先从 **70% 上下文预算** 触发，保留最近 4～6 轮原始消息。

压缩流程：

```text
旧摘要 + 较早的原始消息
        ↓
Context Compressor（可由当前本地模型或专用小模型执行）
        ↓
新的结构化摘要 JSON
        ↓
校验必需字段不丢失
        ↓
保存 Context Snapshot
        ↓
后续调用使用新摘要 + 最近原始消息
```

摘要建议包含：

```json
{
  "user_goal": "用户要解决的问题",
  "hard_constraints": ["不可违背的约束"],
  "soft_preferences": ["偏好"],
  "existing_hardware": ["已有设备"],
  "confirmed_facts": ["经过 Truth DB 确认的事实"],
  "rejected_options": ["已排除候选及原因"],
  "pending_questions": ["仍需澄清或查询的问题"],
  "agent_progress": "当前阶段"
}
```

压缩器的输出必须经过 JSON Schema 校验。校验失败时保留旧摘要并缩短最近消息窗口，而不能以无效摘要覆盖上下文。

### 3.5 Agent 与用户会话的边界

- 用户会话上下文用于理解持续的需求、预算和偏好。
- 每个 Agent Run 有独立的工具调用上下文和独立模型对话，不与其他 Agent 共享其内部推理文本。
- 各 Agent 只共享后端已确认的用户约束、数据库 Schema 和工具 Observation；最终融合阶段只读取结构化 Recommendation JSON、验证结果和日志摘要。

这样既能比较多模型的独立行为，也避免 Agent A 的结论污染 Agent B。

---

## 4. 数据库工具：模型生成、后端执行

### 4.1 工具边界

V1.1 继续只开放两个通用工具：

| 工具 | 作用 | 模型不得做的事 |
| --- | --- | --- |
| `inspect_database()` | 获取允许查询的 Schema、字段和关系 | 访问系统表或未授权表 |
| `query_database(sql)` | 提交只读 SQL，获得结构化 Observation | 直连数据库、写入或修改数据库 |

LLM 从不获得 PostgreSQL 的地址、账号、密码或连接对象。所有工具实际运行在 Application Server。

### 4.2 推荐的统一工具调用协议

不同本地模型对原生 function calling 的支持程度可能不同。为保证多模型公平可比，V1.1 建议 Agent Controller 优先使用受 JSON Schema 约束的**文本 JSON 工具协议**：

```json
{
  "type": "tool_call",
  "tool": "query_database",
  "arguments": {
    "sql": "SELECT name, vram_gb FROM hardware WHERE type = 'GPU' LIMIT 20"
  }
}
```

工具结果作为下一轮模型输入：

```json
{
  "type": "tool_result",
  "tool": "query_database",
  "success": true,
  "data": {
    "columns": ["name", "vram_gb"],
    "rows": [["Example GPU", 24]],
    "row_count": 1
  }
}
```

完成时必须输出：

```json
{
  "type": "final",
  "recommendation": {
    "recommendations": []
  }
}
```

如果某个模型经过验证能够稳定支持 llama-server 的原生工具调用，可在 Gateway 内将其适配为同一内部协议；上层 Agent Controller 不应绑定某个模型厂商的字段格式。

### 4.3 inspect_database() 的返回范围

只返回白名单表及其业务 Schema：

```text
hardware
ai_model
model_variant
evidence
benchmark
```

每张表只暴露字段名、类型、允许的关联关系和语义说明；不得暴露 PostgreSQL 系统表、其他 schema、数据库账号或运行环境信息。

### 4.4 query_database(sql) 的执行链路

```text
LLM 输出 JSON 工具调用
    ↓
Tool Protocol Parser / JSON Schema Validator
    ↓
SQL Validator
    ↓
Read-only SQL Executor
    ↓
PostgreSQL (agent_readonly)
    ↓
结构化 Tool Result / 结构化 Error
    ↓
下一轮 LLM
```

SQL Validator 至少实施：

- 仅允许 `SELECT` 或 `WITH ... SELECT`；
- 表、schema 和字段白名单；
- 禁止 DDL、DML、多语句、注释绕过和系统表访问；
- 最大 SQL 长度、最大 JOIN 数、最大子查询层数；
- `statement_timeout = 3000 ms`；
- 返回最多 100 行，缺少 `LIMIT` 时后端强制限制；
- 使用仅有 `SELECT` 权限的 `agent_readonly` 账号。

无论 SQL 是否成功，均返回不会泄露内部信息的结构化结果，例如：

```json
{
  "type": "tool_result",
  "tool": "query_database",
  "success": false,
  "error": {
    "code": "column_not_found",
    "message": "Requested column is not available.",
    "hint": "Use fields returned by inspect_database()."
  }
}
```

### 4.5 必要日志与评估

每次模型调用、工具调用和上下文压缩应关联同一个 `request_id`、`conversation_id` 和 `agent_run_id`，至少记录：

```text
模型与量化版本
队列等待时间、模型加载/卸载时间、总推理时间
输入/输出 token 估算、上下文压缩次数
工具名称、SQL、校验结果、执行耗时、返回行数、错误代码
最终 Recommendation JSON 的 Schema 校验结果
Truth Verification 与硬约束检查结果
```

这些数据既用于故障定位，也直接支持 V1 的 SQL 成功率、工具调用效率、推理延迟、显存占用与多模型效果比较。

---

## 5. V1.1 最小闭环

V1.1 优先实现并验证以下顺序：

1. 将当前单轮 LLM Gateway 改为可指定 `conversation_id` 的会话接口；
2. 持久化原始消息、用户硬约束和上下文摘要；
3. 实现 token 估算、阈值触发和摘要 JSON 校验；
4. 实现 `inspect_database()` 与受限 `query_database(sql)`；
5. 完成单模型、单 Agent 的多轮工具调用闭环；
6. 添加 Inference Scheduler，使多个 Agent 任务排队、模型物理串行加载与卸载；
7. 收集 Agent 结果，再接入 Truth Verification、约束检查和 MTRF。

在第 5 步通过之前，不接入多个模型的融合逻辑；在第 6 步通过之前，不宣称系统支持多模型并发推理。
