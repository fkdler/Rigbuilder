# 基于大语言模型的真值推荐系统 —— 第一版方案（V1：受控自主 SQL Agent）

## 0. 方案定位

本文件定义项目第一版正式实施方案。

项目聚焦：

> **面向个人计算机硬件与本地 AI 部署的多模型真值推荐系统**

第一版采用的核心 Agent 方案为：

> **受控自主 SQL Agent**

其核心思想是：

> **允许 LLM 自主决定“需要查什么”，并自主生成只读 SQL 查询 PostgreSQL；但 LLM 不直接持有数据库账号，也不直接执行 SQL。所有 SQL 均由后端统一校验、受控执行并记录。**

因此系统同时保留：

- LLM 的自主检索能力；
- PostgreSQL 的结构化真值能力；
- 后端的安全控制能力；
- 独立 Truth Verification 的事实审查能力；
- 多模型之间的推理差异；
- MTRF 多模型真值感知融合。

暂定不采用开放式 Shell Agent，也区别于一般 RAG。

---

# 1. 项目主题

## 1.1 课程题目

**基于大语言模型的真值推荐系统**

## 1.2 项目具体场景

**面向个人计算机硬件与本地 AI 部署的多模型真值推荐系统**

系统面向具有以下需求的用户：

- 个人电脑装机；
- 硬件升级；
- 本地大语言模型部署；
- GPU / VRAM / RAM 与模型规模匹配；
- 模型量化方案与硬件适配。

用户通过自然语言描述预算、使用场景、已有硬件和性能偏好。

系统将同一请求依次交给多个本地部署的大语言模型。每个模型作为一个独立 Agent，可以自主查询同一套本地 Truth DB，根据自己认为必要的信息生成 SQL，获得可信事实后形成独立推荐。

多个 Agent 的结果最终经过：

1. 输出结构化；
2. 独立真值验证；
3. 用户硬约束检查；
4. 用户软偏好评分；
5. 多模型共识分析；
6. MTRF 融合；

最终输出 Top-K 推荐结果。

---

# 2. 第一版核心思想

第一版系统遵循以下原则：

> **LLM 可以主动查真值，但系统最终不能直接相信 LLM。**

完整逻辑为：

```text
用户请求
    ↓
Agent Controller
    ↓
本地 LLM
    ↓
LLM 判断缺少什么事实
    ↓
自主生成只读 SQL
    ↓
query_database(sql)
    ↓
SQL Validator
    ↓
PostgreSQL
    ↓
结构化查询结果
    ↓
LLM 继续推理
    ↓
必要时再次查询
    ↓
最终 Recommendation JSON
    ↓
独立 Truth Verification
    ↓
Constraint Validation
    ↓
MTRF Fusion
    ↓
最终 Top-K 推荐
```

其中最重要的职责划分为：

> **LLM 决定查什么、如何组合查询；后端决定 SQL 是否允许执行；PostgreSQL 负责提供真值。**

---

# 3. 为什么第一版采用 SQL Agent

项目不采用完全固定的查询工具作为核心 Agent 机制。

例如以下方式：

```text
get_hardware(name)
get_model(name)
search_model_variants(filters)
```

虽然安全、简单，但 LLM 的查询过程仍受到大量人工预定义接口限制。

第一版改为：

```text
inspect_database()
query_database(sql)
```

让 LLM 可以：

- 自己了解数据库结构；
- 自己选择需要查询的表；
- 自己选择字段；
- 自己组合 WHERE 条件；
- 自己进行 JOIN；
- 自己排序、筛选、聚合；
- 根据第一次查询结果继续第二次查询；
- 根据 SQL 错误自行修改查询。

因此系统更接近真正的数据库 Agent，而非普通 Function Calling。

---

# 4. 为什么第一版不直接使用 Shell Agent

更自由的方案是给 LLM：

```text
terminal(command)
```

允许其使用：

```text
psql
grep
cat
find
python
```

甚至访问文件系统。

这种方案虽然自主性更高，但会显著增加：

- 文件系统安全风险；
- Shell 命令执行风险；
- 环境隔离需求；
- Docker / Sandbox 复杂度；
- 凭据泄露风险；
- 实验不可控性；
- 行为日志分析难度；
- 项目实现工作量。

同时，本项目主要需要的能力本质上是：

- 条件筛选；
- 多表关联；
- Evidence 查询；
- Benchmark 查询；
- 结构化事实检索。

PostgreSQL + SQL 已足够完成第一版业务。

因此：

> **V1 使用受控自主 SQL Agent。而 Shell Agent 作为未来扩展方向。**

---

# 5. 目标用户

## 5.1 普通装机用户

例如：

- “预算 8000 元，主要玩 2K 游戏，也需要写代码，推荐核心配置。”
- “CPU 可以 AMD，但显卡希望 NVIDIA。”
- “开发和虚拟机需求高，对游戏要求一般。”

## 5.2 硬件升级用户

例如：

- “现在是 Ryzen 5 5600X + RTX 3060，预算 3000 元优先升级什么？”
- “已有 32GB DDR5，想提升游戏性能，应该升级 CPU 还是 GPU？”

## 5.3 本地 AI 用户

例如：

- “RTX 4090 推荐部署哪些带视觉能力的本地模型？”
- “24GB 显存适合哪些 20B～35B 的模型？”
- “Qwen3.8-27B 的某个量化版本需要什么硬件？”
- “16GB 显存，希望部署中文和代码能力较好的模型，有哪些候选？”

---

# 6. 第一版业务范围

第一版聚焦核心硬件和本地 AI 部署。

## 6.1 普通硬件推荐

重点支持：

- CPU；
- GPU；
- RAM 容量；
- SSD 容量；
- PSU 功率等级。

主板仅做平台级推荐，例如：

- AM5 + B850；
- LGA 平台 + 对应芯片组。

第一版不建设完整电商 SKU 库。

## 6.2 本地 AI 推荐

支持两个方向：

### 硬件 → 模型

根据：

- GPU；
- VRAM；
- RAM；
- 用户用途；
- 模型能力偏好；

推荐适合部署的模型和量化版本。

### 模型 → 硬件

根据：

- 模型名称；
- 参数量；
- 量化方式；
- 模型文件大小；
- 上下文；
- 是否支持视觉；

推荐：

- 最低可运行配置；
- 推荐配置；
- 较舒适配置。

---

# 7. Truth DB 设计

第一版以 PostgreSQL 作为核心真值知识库。

系统不将 LLM 参数知识视为最终可信事实。

优先级为：

> **官方来源 > 项目实测 > 规则推导 > LLM 参数知识**

知识分为三类。

## 7.1 Official Truth

来源包括：

- NVIDIA 官方产品页；
- AMD 官方产品页；
- Intel ARK；
- 模型官方 Hugging Face 页面；
- 模型官方 ModelScope 页面；
- 模型官方 GitHub；
- 官方技术文档。

典型字段：

- GPU 显存；
- CPU 核心数；
- 功耗；
- 架构；
- 模型参数量；
- 上下文长度；
- 是否支持视觉；
- 官方模型变体；
- 官方量化文件信息。

## 7.2 Measured Data

项目组实际实验得到的数据，例如：

- 某模型在 RTX 3090 Ti 上的峰值显存；
- Token/s；
- 首 Token 延迟；
- RAM 占用；
- 特定上下文下是否可运行。

必须与官方数据分开标记。

## 7.3 Derived Data

由规则或公式推导，例如：

- 根据模型文件大小估算最低内存；
- 根据 GPU / CPU 功耗估算 PSU；
- 根据显存余量判断部署舒适度；
- 根据模型大小推导部署可行性。

每条数据都记录：

```text
provenance = official / measured / derived
```

---

# 8. 第一版数据库核心表

第一阶段优先建立：

```text
hardware
ai_model
model_variant
evidence
benchmark
```

同时保留业务与实验记录表：

```text
query
agent_run
tool_call
llm_judgement
recommendation_result
evaluation_result
resource_metric
```

---

# 9. 核心表职责

## 9.1 hardware

保存硬件结构化事实。

典型字段：

```text
id
name
type
manufacturer
architecture
vram_gb
memory_type
tdp_w
release_date
```

## 9.2 ai_model

保存模型级信息。

典型字段：

```text
id
name
publisher
parameter_b
context_length
supports_vision
supports_code
license
```

## 9.3 model_variant

保存同一模型不同部署变体。

典型字段：

```text
id
model_id
quantization
format
file_size_gb
recommended_vram_gb
recommended_ram_gb
source
```

## 9.4 evidence

为结构化事实保存来源证据。

典型字段：

```text
id
entity_type
entity_id
field
normalized_value
raw_value
publisher
source_url
retrieved_at
provenance
```

## 9.5 benchmark

保存项目实测。

例如：

```text
model_variant_id
hardware_id
context_length
peak_vram_gb
ram_gb
tokens_per_second
first_token_latency
test_environment
```

---

# 10. Agent 工具设计

第一版不为每种业务实体建立大量固定工具。

核心仅开放少量通用数据库工具。

## 10.1 inspect_database()

用途：

> 让 LLM 获取允许访问的数据库 Schema。

返回内容包括：

- 可访问表；
- 字段名；
- 字段类型；
- 主键；
- 外键关系；
- 字段简要语义。

示意：

```json
{
  "tables": {
    "hardware": {
      "columns": [
        "id",
        "name",
        "type",
        "manufacturer",
        "vram_gb",
        "tdp_w"
      ]
    },
    "ai_model": {
      "columns": [
        "id",
        "name",
        "parameter_b",
        "supports_vision"
      ]
    },
    "model_variant": {
      "foreign_keys": {
        "model_id": "ai_model.id"
      }
    }
  }
}
```

LLM 不需要知道 PostgreSQL 内部系统表。

---

## 10.2 query_database(sql)

用途：

> 允许 LLM 自主提交只读 SQL。

例如：

```sql
SELECT name, vram_gb
FROM hardware
WHERE type = 'GPU'
  AND vram_gb >= 16
ORDER BY vram_gb DESC;
```

或者：

```sql
SELECT
    m.name,
    v.quantization,
    v.file_size_gb
FROM ai_model m
JOIN model_variant v
    ON m.id = v.model_id
WHERE m.parameter_b BETWEEN 20 AND 35
  AND v.file_size_gb <= 24;
```

LLM 可以自主决定：

- 查哪张表；
- 查哪些字段；
- 是否 JOIN；
- 如何筛选；
- 如何排序；
- 是否聚合；
- 是否继续下一次查询。

---

# 11. Agent Loop

一次 Agent 的内部循环如下：

```text
用户请求
    ↓
Agent Controller
    ↓
LLM
    ↓
判断是否需要数据库信息
    ↓
需要
    ↓
inspect_database() / query_database(sql)
    ↓
后端验证
    ↓
执行 SQL
    ↓
返回 Observation
    ↓
LLM 继续推理
    ↓
是否还需要查询？
    ├─ 是 → 再次 query_database()
    └─ 否 → 输出 Recommendation JSON
```

例如：

```text
用户：
24GB 显存，想部署 20B～35B 的视觉模型。

Agent：
先找参数量符合范围的模型。

SQL 1：
SELECT id, name, parameter_b
FROM ai_model
WHERE parameter_b BETWEEN 20 AND 35
  AND supports_vision = TRUE;

Observation：
...

Agent：
需要进一步查看这些模型有哪些适合 24GB 显存的量化变体。

SQL 2：
SELECT ...
FROM ai_model m
JOIN model_variant v
ON ...
WHERE ...

Observation：
...

Agent：
还需要获取关键事实证据。

SQL 3：
SELECT ...
FROM evidence
WHERE ...

Observation：
...

Agent：
形成最终推荐。
```

---

# 12. SQL 执行安全机制

LLM 生成 SQL，但绝不直接连接数据库。

执行链路为：

```text
LLM
 ↓
query_database(sql)
 ↓
Agent Controller
 ↓
SQL Validator
 ↓
SQL Executor
 ↓
PostgreSQL Read-Only User
```

第一版至少实现以下约束。

## 12.1 只读数据库账号

专门创建 Agent 使用的 PostgreSQL 用户：

```text
agent_readonly
```

只授予：

```text
SELECT
```

权限。

不授予：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
CREATE
TRUNCATE
```

---

## 12.2 SQL 类型限制

原则上仅允许：

```text
SELECT
WITH ... SELECT
```

禁止所有 DDL / DML 写操作。

---

## 12.3 表白名单

LLM 只能访问：

```text
hardware
ai_model
model_variant
evidence
benchmark
```

不能访问：

- PostgreSQL 系统表；
- 用户账号表；
- 应用敏感表；
- 数据库凭据；
- 其他 schema。

---

## 12.4 查询时间限制

例如：

```text
statement_timeout = 3000 ms
```

避免复杂 SQL 长时间占用数据库。

---

## 12.5 返回行数限制

例如：

```text
最大返回 100 行
```

对于未包含 LIMIT 的查询，后端可以自动限制最大结果数。

---

## 12.6 SQL 长度与复杂度限制

第一版可限制：

- SQL 最大长度；
- JOIN 数量；
- 子查询层数；
- 返回字段数量。

避免异常查询。

---

## 12.7 SQL 日志

每次调用均记录：

```text
agent_run_id
model_name
sql
validation_result
execution_time
row_count
error
timestamp
```

用于：

- Agent 行为分析；
- 错误分析；
- 安全审计；
- 性能评估；
- 后续论文 / 课程报告实验。

---

# 13. SQL 错误与 Agent 自我修正

第一版允许 SQL 执行失败后，将错误以结构化形式返回给 Agent。

例如：

```json
{
  "success": false,
  "error_type": "column_not_found",
  "message": "column vram does not exist",
  "hint": "hardware table contains column vram_gb"
}
```

LLM 可以继续生成：

```sql
SELECT name, vram_gb
FROM hardware;
```

因此 Agent 具备：

```text
尝试
↓
收到错误
↓
修正 SQL
↓
重新查询
```

的基本自主纠错能力。

---

# 14. Agent 查询次数限制

为避免无限循环，第一版设置：

```text
最大 SQL Tool Call 次数：6
单次 SQL 超时：3 秒
Agent 总轮数上限：8
单 Agent 总推理时间限制
```

达到限制后，Agent 必须：

- 使用已有信息完成回答；
- 或标记信息不足。

---

# 15. 多模型 Agent 设计

多个本地模型不是分别承担固定业务，而是作为多个并列推荐 Agent。

对同一个用户请求：

```text
用户请求
    ↓
Agent Controller
    ↓
┌──────────┬──────────┬──────────┐
│ Agent A  │ Agent B  │ Agent C  │
│ 自主 SQL │ 自主 SQL │ 自主 SQL │
│ 独立查询 │ 独立查询 │ 独立查询 │
│ 独立推荐 │ 独立推荐 │ 独立推荐 │
└──────────┴──────────┴──────────┘
    ↓
统一输出 Schema
    ↓
Truth Verification
    ↓
Constraint Validation
    ↓
MTRF
    ↓
Top-K
```

所有 Agent：

- 访问同一个 Truth DB；
- 使用相同数据库权限；
- 使用相同工具；
- 使用相同输出 JSON Schema；
- 但自主决定查询路径；
- 自主决定 SQL；
- 独立形成推荐。

这样可以比较不同 LLM 的：

- 查询策略；
- SQL 能力；
- 事实利用能力；
- 推荐结果；
- Tool Call 效率。

---

# 16. Agent 输出格式

所有 Agent 最终必须输出统一 JSON。

例如：

```json
{
  "recommendations": [
    {
      "candidate_id": "...",
      "score": 88,
      "confidence": 0.91,
      "reasons": [
        "..."
      ],
      "risks": [
        "..."
      ],
      "claims": [
        {
          "entity_type": "hardware",
          "entity_id": "...",
          "field": "vram_gb",
          "value": 24
        }
      ]
    }
  ]
}
```

其中：

```text
claims
```

用于后续独立 Truth Verification。

---

# 17. 为什么 Agent 查过数据库后仍需 Truth Verification

即使 Agent 自己查询过 Truth DB，也不能直接相信最终生成结果。

原因包括：

- 查错实体；
- SQL 写错；
- JOIN 关系理解错误；
- 忽略查询结果；
- 将多个型号混淆；
- 最终生成阶段重新产生幻觉；
- 推荐理由引用了未查询的信息。

因此最终输出必须再次独立核验。

流程：

```text
Agent Recommendation
        ↓
Claims Extraction
        ↓
独立 Truth DB Lookup
        ↓
事实比较
        ↓
Fact Score
```

例如：

```text
Agent：
RTX 3090 Ti = 20GB VRAM

Truth DB：
RTX 3090 Ti = 24GB

结果：
Fact Conflict
```

重要原则：

> **Agent 的 SQL 查询是“获取信息”；Truth Verification 是“审查最终陈述”。两者职责不同。**

---

# 18. 用户约束验证

系统独立验证用户硬约束。

例如：

- 总预算不能超过 8000；
- 必须 NVIDIA；
- 显存至少 16GB；
- 必须支持视觉；
- 模型参数量限定范围；
- 已有硬件不可替换。

违反关键硬约束的推荐直接淘汰。

LLM 自己声称“满足条件”不能作为最终依据。

---

# 19. MTRF v1

算法暂称：

**MTRF：Multi-LLM Truth-Aware Recommendation Fusion**

即：

**多大模型真值感知推荐融合算法**

第一版包含以下部分。

## 19.1 官方事实一致性

```text
S_fact
```

用于衡量 Agent 最终 claims 与 Truth DB 的一致程度。

## 19.2 用户硬约束

```text
S_constraint
```

硬约束不满足时，可直接淘汰候选。

## 19.3 用户软偏好

```text
S_preference
```

例如：

- 游戏性能；
- 大显存；
- 中文能力；
- 代码能力；
- 本地 AI；
- 功耗；
- 性价比。

## 19.4 多模型共识

```text
S_consensus(x)
=
推荐 x 的 Agent 数量
/
Agent 总数量
```

## 19.5 模型可靠性权重

根据各 LLM 在离线测试集上的表现确定：

```text
w_m
```

综合评分：

```text
S(x)
=
α S_fact
+ β S_constraint
+ γ S_preference
+ δ S_consensus
+ ε S_model
```

最终重新生成 Top-K。

---

# 20. 完整系统运行流程

```text
用户自然语言请求
        ↓
Request Parser
        ↓
结构化用户需求
        ↓
Agent Controller
        ↓
┌───────────────────────────────┐
│ Agent A                       │
│  ↳ inspect_database()         │
│  ↳ 自主生成 SELECT SQL        │
│  ↳ PostgreSQL                 │
│  ↳ 多轮 Observation           │
│  ↳ 独立推荐                   │
├───────────────────────────────┤
│ Agent B                       │
│  ↳ inspect_database()         │
│  ↳ 自主生成 SELECT SQL        │
│  ↳ PostgreSQL                 │
│  ↳ 多轮 Observation           │
│  ↳ 独立推荐                   │
├───────────────────────────────┤
│ Agent C                       │
│  ↳ inspect_database()         │
│  ↳ 自主生成 SELECT SQL        │
│  ↳ PostgreSQL                 │
│  ↳ 多轮 Observation           │
│  ↳ 独立推荐                   │
└───────────────────────────────┘
        ↓
Normalized Recommendation JSON
        ↓
Truth Verification
        ↓
Constraint Validation
        ↓
MTRF Fusion
        ↓
Evidence Lookup
        ↓
Top-K Recommendation
        ↓
Vue Web 展示
```

---

# 21. 两台机器部署架构

第一版仍采用两台机器协同部署。

关键结论：

> **数据库不需要部署在 GPU 机器。**

## 21.1 Application Server

负责：

- Vue；
- FastAPI；
- PostgreSQL；
- Truth DB；
- Evidence；
- Agent Controller；
- SQL Validator；
- SQL Executor；
- Truth Verification；
- MTRF；
- Evaluation；
- Tool / SQL 日志。

示例：

```text
192.168.1.10
```

## 21.2 Inference Server

负责：

- RTX 3090 Ti；
- llama.cpp；
- llama-server；
- 本地 GGUF LLM。

示例：

```text
192.168.1.20
```

---

# 22. 为什么数据库和 GPU 不必同机

LLM 的职责只是：

```text
生成 SQL
```

真正执行 SQL 的是 Application Server。

运行流程：

```text
Application Server
FastAPI
    │
    │ HTTP Prompt
    ▼
Inference Server
LLM
    │
    │ Tool Call:
    │ query_database("SELECT ...")
    ▼
Application Server
Agent Controller
    ↓
SQL Validator
    ↓
SQL Executor
    ↓
localhost PostgreSQL
    ↓
查询结果
    │
    │ HTTP
    ▼
Inference Server
LLM
```

因此：

> **SQL 的生成位置与 SQL 的执行位置可以完全不同。**

GPU 机器只承担计算密集型推理。

---

# 23. 本地模型部署

LLM 不嵌入 FastAPI 进程。

采用：

```text
llama.cpp
+
llama-server
+
GGUF
```

通过 OpenAI-compatible API 调用。

由于 RTX 3090 Ti 显存有限，多个 Agent 对应模型可以顺序运行：

```text
Model A
↓
Agent A
↓
卸载 / 切换

Model B
↓
Agent B
↓
卸载 / 切换

Model C
↓
Agent C
```

开发阶段允许 Mock LLM。

---

# 24. 基础技术栈

## 前端

- Vue 3
- TypeScript
- Vite
- Vue Router
- Pinia
- Axios
- Element Plus
- ECharts

## 后端

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- httpx

## 数据层

- PostgreSQL
- pgvector（后期可选）

## Agent

- 自定义 Agent Controller
- JSON Tool Calling
- inspect_database()
- query_database(sql)
- SQL Validator
- SQL Executor
- Agent Loop

## 数据处理

- NumPy
- Pandas
- scikit-learn

## 本地推理

- llama.cpp
- llama-server
- GGUF

## 数据采集

- httpx / requests
- BeautifulSoup4
- huggingface_hub
- Playwright（按需）

## 测试

- pytest
- psutil
- pynvml

## 协作与部署

- Git
- Gitee
- Docker / Docker Compose

---

# 25. 第一版评估设计

至少进行以下四组对比。

## Baseline 1：LLM Only

LLM 完全依赖参数知识回答。

测量：

- 事实准确率；
- 幻觉率；
- 推荐质量。

## Baseline 2：LLM + Fixed Truth Context

后端提前查询统一真值，并把固定 Context 提供给 LLM。

用于验证：

> 提供官方真值是否降低幻觉。

## Baseline 3：Single SQL Agent

单个 LLM 使用：

```text
inspect_database()
query_database(sql)
```

自主查询 Truth DB。

用于评估：

- SQL 查询正确率；
- Tool Call 效率；
- 自主检索效果；
- 推荐质量。

## Proposed：Multi-SQL-Agent + Truth Verification + MTRF

多个 LLM 独立自主 SQL 查询，再通过：

- Truth Verification；
- Constraint Validation；
- MTRF；

形成最终推荐。

---

# 26. 第一版评估指标

## 26.1 可信性

- 事实准确率；
- 幻觉率；
- Evidence 覆盖率；
- Truth Verification 通过率。

## 26.2 推荐

- 推荐有效率；
- 硬约束满足率；
- Top-K 命中率；
- 推荐稳定性；
- 多模型一致性。

## 26.3 SQL Agent

新增重点指标：

- SQL 语法成功率；
- SQL 语义正确率；
- 平均 SQL Tool Call 次数；
- 无效 SQL 比例；
- SQL 自我修正成功率；
- 平均查询耗时；
- 查询结果有效利用率。

## 26.4 性能

- 总推理延迟；
- SQL 查询延迟；
- GPU 显存；
- RAM；
- CPU；
- Token/s；
- 总 Token 消耗。

---

# 27. 可解释性设计

系统最终不仅展示自然语言推荐理由，还应展示：

```text
推荐对象
↓
哪些 Agent 推荐
↓
各 Agent 使用了哪些数据库事实
↓
关键 SQL 查询摘要
↓
Truth Verification 结果
↓
用户约束满足情况
↓
MTRF 综合评分
↓
官方 Evidence
```

第一版前端不一定展示完整 SQL，但后台必须保存完整 SQL 日志。

开发 / 实验页面可以展示：

```text
Agent A
Query 1 → SQL → Observation
Query 2 → SQL → Observation
Final Answer
```

这将成为系统非常重要的可解释性展示。

---

# 28. 第一版项目边界

V1 明确不做：

- 开放式 Shell Agent；
- LLM 任意终端访问；
- LLM 直接持有 PostgreSQL 凭据；
- LLM 直接操作数据库物理文件；
- INSERT / UPDATE / DELETE；
- 自动修改知识库；
- 自动联网爬取并写入数据库；
- 微服务；
- Kubernetes；
- 消息队列；
- 全量电商 SKU；
- 实时京东 / 淘宝价格；
- 大规模 RAG；
- 全量 Hugging Face 模型。

第一版重点是：

> **结构化 Truth DB + LLM 自主 SQL 查询 + 后端安全执行 + 独立真值验证 + 多模型融合。**

---

# 29. 第一版的最小闭环

第一版真正需要优先跑通的是：

```text
用户问题
↓
LLM
↓
inspect_database()
↓
LLM 自主生成 SELECT SQL
↓
query_database(sql)
↓
PostgreSQL
↓
Observation
↓
LLM 最终 Recommendation JSON
↓
Truth Verification
```

多 Agent 和 MTRF 都建立在这个闭环之上。

---

# 30. 当前实施顺序

## 阶段 1：Truth DB

1. 冻结数据标准；
2. 建立 PostgreSQL；
3. 建立：
   - hardware
   - ai_model
   - model_variant
   - evidence
   - benchmark
4. 导入一小批真实官方数据；
5. 使用普通 Python 验证 SQL 查询。

## 阶段 2：SQL Agent 最小闭环

6. 实现 inspect_database()；
7. 实现 query_database(sql)；
8. 创建 agent_readonly 数据库账号；
9. 实现 SQL Validator；
10. 实现 SQL Executor；
11. 接入一个本地 LLM；
12. 跑通单 Agent 多轮 SQL 查询。

## 阶段 3：结构化推荐

13. 冻结 Recommendation JSON Schema；
14. 实现用户请求解析；
15. 实现 Agent 终止条件；
16. 实现 Tool Call / SQL 日志。

## 阶段 4：可信性

17. 实现 Claims Extraction；
18. 实现 Truth Verification；
19. 实现用户硬约束验证；
20. 实现 Evidence Lookup。

## 阶段 5：多模型

21. 接入多个本地 LLM；
22. 顺序运行多个 SQL Agent；
23. 统一输出格式；
24. 比较各模型 SQL 行为差异。

## 阶段 6：MTRF

25. 实现 S_fact；
26. 实现 S_constraint；
27. 实现 S_preference；
28. 实现 S_consensus；
29. 引入模型可靠性权重；
30. 输出最终 Top-K。

## 阶段 7：评估与 Web

31. Baseline 对比；
32. SQL Agent 指标；
33. 性能评估；
34. Vue 前端；
35. 推荐解释页面；
36. 实验结果整理。

---

# 31. 团队实施时应冻结的接口

在分工前，优先冻结以下内容：

```text
Database Schema
inspect_database() Schema
query_database(sql) Schema
SQL Validator 规则
Recommendation JSON Schema
Truth Verification Claims Schema
MTRF v1 输入输出
LLM Gateway API
```

这样数据库、后端、Agent、算法、前端可以并行开发。

---

# 32. V1 最终架构

```text
Browser
   ↓
Vue 3
   ↓
FastAPI
   │
   ├── Request Parser
   ├── Agent Controller
   │      │
   │      ├── inspect_database()
   │      └── query_database(sql)
   │                 │
   │           SQL Validator
   │                 │
   │           SQL Executor
   │                 │
   │            PostgreSQL
   │
   ├── Truth Verification
   ├── Constraint Validation
   ├── MTRF
   ├── Evidence Service
   └── Evaluation
   │
   └──────────── HTTP ────────────→ llama-server
                                     │
                                     └── RTX 3090 Ti
                                         LLM A / B / C
```

---

# 33. V1 一句话总结

> **第一版系统采用“受控自主 SQL Agent”架构：本地 LLM 自主判断需要哪些事实、自主探索数据库 Schema、自主生成只读 SQL；后端负责 SQL 校验与执行，PostgreSQL 提供统一 Truth DB，最终推荐再经过独立 Truth Verification 和 MTRF 多模型融合。数据库与 GPU 推理服务物理解耦，数据库和后端部署在 Application Server，RTX 3090 Ti 机器仅负责 LLM 推理。**
