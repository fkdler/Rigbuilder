# RigBuilder Plan V2.2：模型数据库工具闭环（草稿）

> 状态：2026-08-31 归档版（已实现；Step 9 真机验收待 PostgreSQL 就绪后补录）。实现记录见 [V2.2.md](V2.2.md)，新增 API 见 [API_Documentation_V2.2.md](../API_documents/API_Documentation_V2.2.md)。

## 1. V2.2 的定位

V2.1 完成了推理链路、会话持久化和三模型对比验收，但模型仍无法自主访问 Truth DB。V2.2 的目标是把 Plan_V2 §7 定义的**受控自主 SQL Agent** 中"数据库工具"这一环闭环：

> LLM 决定需要查询什么并生成只读 SQL；后端负责工具协议、SQL 校验、受控执行、日志与最终结构校验；PostgreSQL Truth DB 提供可信事实。

这是后续 P3（Truth Verification、Constraint Validation）与 P5（MTRF）的**前置**：没有统一的 Recommendation JSON 和可用的只读查询工具，真实性与融合无从谈起。V2.2 不做验证、不做融合，只把"模型 → 工具 → 数据库 → 结构化输出"这条最小闭环打通并留下评估依据。

安全上采用**分层防御**，任何一层被绕过都不至于写库或越权读：

```text
系统提示词 + 工具协议（约束模型行为，可绕过）
  → Pydantic 契约（输入形状，可绕过）
  → pglast Validator（语句/表/函数/资源上限，主防线）
  → agent_readonly 角色 + statement_timeout（物理兜底）
  → tool_call 日志（审计与评估）
```

## 2. 当前基线

| 项 | 现状 | 位置 |
| --- | --- | --- |
| 工具契约 | `ToolCall` / `ToolResult` 已有一版，缺 Observation 固化 | [tools/contracts.py](../../backend/app/tools/contracts.py) |
| 工具实现 | 只有五表白名单 + 两行 TODO | [tools/database.py](../../backend/app/tools/database.py) |
| Agent 模块 | 只有 `AgentRunStatus` 状态枚举 | [agents/states.py](../../backend/app/agents/states.py) |
| 最终输出 Schema | 无 Recommendation JSON 校验器，模型可输出自由文本 | 待 3.1 |
| 日志表 | 无 `agent_run` / `agent_message` / `tool_call` | [models/](../../backend/app/models/) |
| 数据库引擎 | 只有可写主引擎，缺只读连接 | [db/session.py](../../backend/app/db/session.py) |
| 依赖 | 缺 PostgreSQL AST 解析库（pglast） | [requirements.txt](../../backend/requirements.txt) |
| 串行锁 | `inference_scheduler.run_serial` 已就绪，可复用 | [inference/scheduler.py](../../backend/app/inference/scheduler.py) |

## 3. 工作分解（8 步）

每步给出：**作用 / 生效时机（运行时在哪一刻被触发）/ 完成标准**。

### 3.1 前置：冻结 Recommendation JSON Schema

- **作用**：规定模型**最终答案**的唯一格式（Top-K、score、model_confidence、reasons、risks、claims 含 evidence_ids），是可执行校验器而非示例文本。
- **为什么用 Pydantic**：契约与校验器合一，无文档/代码漂移；`ValidationError` 给出逐字段结构化错误，可回喂模型自修；与 FastAPI/现有 contracts 同栈；可导出 JSON Schema 进提示词；下游（P3/P5）可安全假设输入合法。
- **生效时机**：Agent 循环的**终局判断**——模型不再输出 ToolCall、输出最终文本时严格校验；中间轮不参与。开发期最先冻结，但直到 3.7 才有调用点。
- **交付物**：`backend/app/schemas/recommendation.py`；下面的示例 JSON 是**规范**，需同时固化进文档、单测与系统提示词：

```json
{
  "schema_version": "1.0",
  "recommendations": [
    {
      "candidate_id": "<hardware.id 的 UUID>",
      "candidate_type": "hardware",
      "name": "GeForce RTX 3060",
      "score": 82,
      "model_confidence": 0.72,
      "reasons": ["12GB VRAM 满足 8GB 最低要求"],
      "risks": ["二手货源不稳定"],
      "claims": [
        {
          "entity_type": "hardware",
          "entity_id": "<hardware.id>",
          "field": "vram_gb",
          "value": 12,
          "value_type": "number",
          "evidence_ids": ["<支撑 vram_gb 的 evidence.id>"]
        }
      ]
    }
  ],
  "insufficient_information": ["候选型号缺实测 benchmark"],
  "tool_summary": {"sql_calls": 1, "tables_queried": ["hardware"]}
}
```

- **字段规则（校验器必须执行的约束）**：
  - `schema_version`：契约版本，演进时递增。
  - `recommendations`：非空数组；**数组顺序即排名**（`[0]` 为 Top-1）。
  - `candidate_type`：V2.2 只允许 `hardware | model_variant`（`build` 待定义 `components` 后再解冻）；`candidate_id` 必须是 DB 实体 UUID，`name` 为人类可读名。
  - `score`：0–100 整数，用于排名；校验时允许 float 输入并取整，减少自修失败。
  - `model_confidence`：0.0–1.0，**仅审计展示，不参与 MTRF 融合**（融合权重来自离线测试）。
  - `claims[].field`：必须是 `entity_type` 对应表的已知列；`value` 断言"该实体该字段等于该值"，且与 `evidence.normalized_value` 同 JSON 类型、值相等（复用 `import_data.py` 的 `normalized_matches`）；`value_type`（number/string/boolean/object/array）帮助 P3 选择比较器。
  - `claims[].evidence_ids`：只引用支撑**该 claim.field** 的 evidence，不得引用该实体其它字段的证据。
  - `insufficient_information`：列出确实查不到的事实；不得把"未查"当"不存在"。
  - `tool_summary`：可选，结构化字段建议 `sql_calls` / `tables_queried` / `evidence_ids_collected`。
- **完成标准**：任意自由文本经 `json.loads + Pydantic` 校验能明确判定通过/失败；失败原因结构化（字段定位 + 错误类型）可返回给模型重试；示例 JSON 本身可通过校验。

### 3.2 工具契约补全（Observation）

- **作用**：定义模型怎么"说"要调工具（ToolCall）、后端返回什么（ToolResult）、查询结果长什么样（Observation）。
- **Observation 字段**：`success` / `columns` / `rows` / `row_count` / `truncated` / `query_id` / `evidence_ids`；失败路径复用同一结构，用 `error_code` / `error_hint` 替代 `columns/rows`。
- **生效时机**：**每一轮**——3.7 用它解析模型回复；工具执行后用它组回 ToolResult 回注上下文。
- **完成标准**：ToolCall/ToolResult/Observation 严格类型化；`truncated` 与 `evidence_ids` 语义明确；`QueryDatabaseArguments.sql` 的 `max_length` 与 3.4 预检共用同一 `sql_max_length` 来源。

契约示例（模型只发 ToolCall，后端只回 ToolResult）：

```text
// 模型 → 后端：查询
{"type": "tool_call", "tool": "query_database", "arguments": {"sql": "SELECT name, vram_gb FROM hardware WHERE vram_gb >= 12"}}

// 模型 → 后端：查看表结构
{"type": "tool_call", "tool": "inspect_database", "arguments": {}}

// 后端 → 模型：查询成功（data.observation）
{"type": "tool_result", "tool": "query_database", "success": true,
 "data": {"observation": {
   "success": true, "columns": ["name", "vram_gb"], "rows": [["RTX 3060", 12]],
   "row_count": 1, "truncated": false, "query_id": "<uuid>", "evidence_ids": ["<uuid>"]
 }}}

// 后端 → 模型：查询失败（仍回 Observation，success=false）
{"type": "tool_result", "tool": "query_database", "success": false,
 "data": {"observation": {
   "success": false, "error_code": "forbidden_table", "error_hint": "只允许查询五张白名单表"
 }}}
```

### 3.3 `inspect_database()`

- **作用**：给模型提供五张表的字段/类型/主外键/语义说明，避免模型猜列名写废 SQL。
- **生效时机**：每个 Agent Run **开头注入一次**（进系统提示词），进程内缓存；不在运行中重复查询。
- **设计要点**：从 SQLAlchemy 模型反射生成（不查库）；列名用 `col.name`（注意 evidence 表列名 `field`，ORM 属性 `field_name`）；白名单与 3.4 同源（共用 `ALLOWED_TABLES`）；返回表级外键以提示 JOIN 路径。
- **完成标准**：返回五表业务 Schema；不含系统元数据；与 Validator 白名单同一事实来源。

### 3.4 SQL Parser + Validator

- **作用**：安全门卫——只放行单条只读 SELECT/WITH，拒绝危险 SQL。它防的是**远不止写**（详见 §3.4.1）。
- **生效时机**：每次 `query_database` 请求**到达后端后、执行前**，同步执行；不依赖模型是否聪明。
- **设计要点（内部五段流水线，纯函数无状态）**：

```text
① 预检          长度 > sql_max_length → sql_too_long
② 解析          pglast → AST，失败 → syntax_error
③ 语句级        恰 1 条？→ multi_statement；顶层 SELECT/WITH？→ not_select
                SELECT INTO / FOR UPDATE / UNION 等另行拒绝
④ AST 遍历      表名对照 ALLOWED_TABLES（排除 CTE 名）→ forbidden_table:<t>
                危险函数 pg_sleep/pg_read_file 等 → forbidden_function:<fn>
                统计 JOIN 数/子查询深度/SELECT 字段数 → 超限拒绝
⑤ 改写          无 LIMIT 时自动追加 LIMIT (sql_max_rows+1)；
                自带 LIMIT ≤ sql_max_rows 保留，> sql_max_rows → limit_too_large
```

  注意：`COPY` 不是函数，它由 ③ 的 `not_select` 拦截；函数黑名单只管 `FuncCall` 节点。

- **错误分类**（决定 3.7 如何处理；覆盖 Validator 与 Executor 两类错误）：

| 分类 | 错误码 | 处理 |
| --- | --- | --- |
| 可修正（引导自修） | `syntax_error`、`forbidden_table`、`set_operation_not_allowed`、`too_many_joins`、`subquery_too_deep`、`too_many_select_columns`、`limit_too_large`、`execution_error` | 带 `error_hint` 回注上下文，让模型重试 |
| 阻断型（恶意/越权/环境错误） | `sql_too_long`、`multi_statement`、`not_select`、`forbidden_function`、`select_into_not_allowed`、`locking_clause_not_allowed`、`database_not_configured` | 计数 + 记日志 + 终止该方向 |

- **组件**：`tools/validator.py`（五段流水线）、`tools/errors.py`（错误码 + 提示文案 + 分类）。
- **完成标准**：攻击样例全部拒绝——`DROP TABLE x`、`SELECT 1; DELETE FROM hardware`、注释隐藏第二语句、`pg_sleep()`、`COPY`、非白名单表；合法单表 SELECT 通过；判定结果全部可落 `tool_call` 日志。

#### 3.4.1 为什么 Validator 防的不只是"写"

- **越权读取**：白名单之外的表（`pg_class`、`information_schema.*`、未来新增业务表）可泄露数据；
- **资源耗尽**：无 LIMIT、深子查询、多 JOIN、大字段数会拖垮查询并撑爆上下文；
- **多语句与注释绕过**：单条约束 + pglast AST 比正则更可靠；
- **正确性**：错表名/错列名应作为可修正错误回喂，而不是执行后返回空结果让模型误判为"不存在"；
- **可审计**：通过/拦截 + 错误码落库，支撑越权拦截率、自修率等评估指标。

#### 3.4.2 错误码目录（实现时逐条覆盖单测）

| 错误码 | 分类 | 触发点 | error_hint 要点 |
| --- | --- | --- | --- |
| `sql_too_long` | 阻断 | ① 预检 | 缩短 SQL 或减少一次查询的表 |
| `syntax_error` | 可修正 | ② pglast 解析失败 | 修正语法后重试 |
| `multi_statement` | 阻断 | ③ 语句数 ≠ 1 | 每次只允许一条语句 |
| `not_select` | 阻断 | ③ 非 SELECT/WITH | 只允许 SELECT（或 WITH...SELECT） |
| `select_into_not_allowed` | 阻断 | ③ SELECT INTO | 禁止写操作 |
| `locking_clause_not_allowed` | 阻断 | ③ FOR UPDATE/SHARE | 禁止行锁 |
| `set_operation_not_allowed` | 可修正 | ③ UNION/INTERSECT/EXCEPT | 拆成单次查询 |
| `forbidden_table` | 可修正 | ④ 表不在白名单 | 只查五张白名单表 |
| `forbidden_function` | 阻断 | ④ 命中函数黑名单 | 禁止该函数 |
| `too_many_joins` | 可修正 | ④ JOIN 数超限 | 减少 JOIN |
| `subquery_too_deep` | 可修正 | ④ 子查询深度超限 | 扁平化子查询 |
| `too_many_select_columns` | 可修正 | ④ SELECT 字段数超限 | 只列所需字段 |
| `limit_too_large` | 可修正 | ⑤ LIMIT > sql_max_rows | LIMIT 不得超过 100 |
| `execution_error` | 可修正 | 3.6 执行失败/超时 | 简化查询后重试 |
| `database_not_configured` | 阻断 | 3.6 只读引擎缺失 | 服务端配置错误 |
| `sql_call_limit_reached` | 限制（预算信号） | 3.7 SQL 预算用尽 | 不再执行 SQL，强制模型用已有信息作答 |
| `tool_arguments_invalid` | 可修正 | 3.7 参数不合契约 | 修正工具参数 |

### 3.5 只读执行环境

- **作用**：物理防线——即使 3.4 出 bug，数据库本身拒绝写；慢查询 3 秒中断；超 100 行截断。
- **生效时机**：与 3.4 同时——3.4 放行后、SQL 发给 PostgreSQL 的那一刻生效。`agent_readonly` 角色一旦建好，数据库层安全**立即生效**，不依赖代码。
- **设计要点**：
  - SQL：`CREATE ROLE agent_readonly LOGIN` + `GRANT CONNECT/USAGE` + 仅授五表 SELECT + `ALTER DEFAULT PRIVILEGES`；角色名/密码取自 `AGENT_READONLY_DATABASE_URL`，脚本幂等。
  - 代码：**新建只读引擎**（不复用可写主引擎），`connect_args={"options": "-c statement_timeout=3000 -c default_transaction_read_only=on"}`；`AGENT_READONLY_DATABASE_URL` 未配置时 `query_database` **失败关闭**，绝不回退主引擎。
  - 行数兜底：执行层 `fetchmany(sql_max_rows+1)`，多取 1 行用于判断是否截断；返回行数 > `sql_max_rows` 即 `truncated=True` 并丢尾行——与 3.4 改写值、契约长度三处对齐（此处是**兜底**，不是再追加一条 LIMIT）。

角色创建 SQL（由 `scripts/create_agent_readonly.py` 生成/执行）：

```sql
CREATE ROLE agent_readonly LOGIN PASSWORD '<password>';
GRANT CONNECT ON DATABASE rigbuilder TO agent_readonly;
GRANT USAGE ON SCHEMA public TO agent_readonly;
GRANT SELECT ON hardware, ai_model, model_variant, evidence, benchmark TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_readonly;
```

- **完成标准**：`agent_readonly` 连接执行写语句被数据库拒绝；慢查询 3 秒内中断；超 100 行截断并标记；未配置只读账号时工具失败关闭。

### 3.6 `query_database(sql)`

- **作用**：把 3.4 + 3.5 串成统一入口，返回 Observation（含 truncated、evidence_ids）。是整条工具链的**触发点**。
- **生效时机**：每次模型请求查询时——3.7 解析出 `tool=="query_database"` 后调用；3.4、3.5 在其内部依次触发。
- **设计要点**：先 `validate_sql`，失败返回 `Observation(success=False, error_code, error_hint)`，不碰库；`evidence_ids` 用可行方案**按结果行里的实体 UUID 反查** `evidence.entity_id IN (...)`（去重、上限 `sql_max_evidence_ids`），纯自然键/非 UUID 的反查留 TODO 占位。
- **完成标准**：成功/失败/截断三条路径都返回结构一致的 Observation；错误不泄露连接信息；单元格长度受限（`sql_max_cell_chars`）。

### 3.7 Agent 循环

- **作用**：所有工具的**运行时调度者**——决定何时调哪个工具、何时停止输出最终答案；执行终止条件；整个 Run 包进现有串行锁。
- **生效时机**：用户发起 Agent 请求时开始，贯穿整个 Run；是 3.1~3.6 的唯一触发源——在其实现前，前面步骤都是"可用但没人调用"的代码。
- **终止条件**：`agent_max_rounds`（默认 8）/ `agent_total_timeout_seconds`（默认 900s）兜底；`agent_max_sql_calls`（默认 6）用尽后不再接受 `query_database`，回注 `sql_call_limit_reached` 强制模型用已有信息作答。命中任一限制但无合规 Recommendation 时 Run 标记 failed；合规则 completed。
- **设计要点**：
  - 循环：上下文+inspect 注入 → LLM → 契约解析 → ToolCall 分发（query→3.6 / inspect→3.3）→ ToolResult 回注 → 终局 3.1 校验 → 失败按错误分类引导自修或终止。
  - 每步埋点写 3.8 日志表。
  - 系统提示词含**精确 ToolCall JSON 示例**与"失败可修正、别硬编"规则（提示词优化落点）。

循环伪代码：

```text
messages = [system(工具协议 + inspect schema + Recommendation 示例)] + 会话上下文 + [用户消息]
while rounds < agent_max_rounds:
    reply, model = llm(messages)            # 包在串行锁内
    记录 agent_message(assistant, reply)
    parsed = parse(reply)
    if parsed 是 Recommendation:
        validate → 通过: return completed
                → 失败: 回注 format_validation_errors，continue
    if parsed 是 ToolCall:
        if tool==query_database and sql_calls >= agent_max_sql_calls:
            回注 sql_call_limit_reached，continue
        result, event = run_tool(...)
        记录 tool_call(event)
        messages += tool_result
        if result 失败且分类==阻断型: return failed
    else:
        回注"只输出一个 JSON 对象"，continue
return failed(round_limit / timeout / sql_limit)
```

- **完成标准**：单模型能针对测试问题自主查询数据库并输出通过 3.1 校验的 Recommendation JSON；超限/工具失败明确标记，不阻塞后续请求。

### 3.8 日志表

- **作用**：黑匣子——把 Run 的每个阶段、每条消息、每次 SQL 调用（参数/耗时/结果摘要/状态）落库，串联成可还原记录；评估指标的唯一数据来源。
- **生效时机**：贯穿整个 Run **实时写入**；其价值在事后——SQL 正确率/自修率/调用次数/越权拦截率全从记录算出。
- **交付物**：`agent_run`（状态复用 `AgentRunStatus` + metrics JSONB）/ `agent_message` / `tool_call`（含 arguments、query_id、error_code、result_truncated、duration_ms）三表 + Alembic 迁移。
- **完成标准**：一次 Run 的每个阶段、每次 SQL 调用可经 `request_id` / `agent_run_id` / `tool_call_id` 串联还原。

关联示例（一次 Run 的还原路径）：

```text
request_id ── 1:N ── agent_run（request_id, conversation_id, model_id, status, metrics）
agent_run   ── 1:N ── agent_message（sequence_no, role, content, token_count）
agent_run   ── 1:N ── tool_call（sequence_no, tool, arguments, query_id, error_code, result_truncated, duration_ms）
tool_call.query_id ── 关联 ── Observation.query_id（同一查询在日志与上下文中的桥）
```

## 4. 模块联动（以 3.4 为枢纽）

| 联动对象 | 联动方式 |
| --- | --- |
| 3.2 contracts | `QueryDatabaseArguments.sql` 是唯一输入；`sql_max_length` 两端同源 |
| 3.6 database | 第一步即调 `validate_sql`；失败返回失败 Observation |
| 3.7 Agent 循环 | `error_hint` 经 ToolResult 回注驱动自修；错误分类决定"重试/终止" |
| 3.3 inspect + ALLOWED_TABLES | **白名单同源**——inspect 生成的 Schema 与 Validator 白名单共用同一常量，避免"告诉模型有表 X 又拒查 X" |
| 3.5 只读环境 | `fetchmany(sql_max_rows+1)` 与 100 行上限、超时参数从同一 Settings 读取 |
| 3.8 tool_call 日志 | 判定结果（拦截/通过 + 错误码）全部落库，支撑越权拦截率评估 |
| api / 安全边界 | Validator 内部细节、SQL 原文、结构错误不得经 API 泄露给前端 |

## 5. 实施顺序

依赖导向，前 5 步纯后端、不依赖模型，可先行单测：

```text
3.1 Schema → 3.2 契约 → 3.3 inspect → 3.4 Validator
  → 3.5 只读环境 → 3.6 query_database
  → 3.7 Agent 循环（3.8 日志表随动）
```

## 6. 配置与依赖变更

- `requirements.txt` 增加 `pglast`（或等价 AST 解析库）。
- `config.py` 增加只读连接与 Validator/Agent 规则项，默认值如下：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_READONLY_DATABASE_URL` | 无（必配） | 只读账号；未设置时 `query_database` 失败关闭 |
| `sql_max_length` | 10000 | 与 `QueryDatabaseArguments.sql.max_length` 同源 |
| `sql_max_joins` | 4 | JOIN 数上限 |
| `sql_max_subquery_depth` | 3 | 子查询嵌套上限 |
| `sql_max_select_columns` | 40 | SELECT 字段数上限 |
| `sql_max_rows` | 100 | Observation 行数上限（执行层取 101 判截断） |
| `sql_statement_timeout_ms` | 3000 | 单条 SQL 超时 |
| `sql_max_evidence_ids` | 100 | evidence_ids 反查上限 |
| `sql_max_cell_chars` | 500 | 单元格截断长度 |
| `agent_max_rounds` | 8 | 已有 |
| `agent_max_sql_calls` | 6 | 已有 |
| `agent_total_timeout_seconds` | 900 | 新增：整个 Run 总超时 |

- 数据库：`scripts/create_agent_readonly.py` 建角色脚本（幂等，`--dry-run`/`--apply`）；三张日志表 Alembic 迁移。

## 7. 边界（V2.2 不做）

- Truth Verification、Constraint Validation（P3）。
- MTRF 融合与模型可靠性权重（P5）。
- 多 Agent 服务端模型映射（Guide §7 预留，V2.2 仍用单模型跑通闭环）。
- Router 主动加载/卸载调度（scheduler TODO 属 P4 剩余，V2.2 沿用 autoload）。
- 前端最终推荐解释页、Benchmark 数据采集。

## 8. 验收清单

- [ ] Recommendation JSON Schema 冻结（含 `schema_version`、数组顺序=排名、`name`、`candidate_id`=实体 UUID），自由文本无法通过校验，失败信息可回喂模型。
- [ ] V2.2 `candidate_type` 仅允许 `hardware | model_variant`；`build` 未定义 `components` 前拒绝。
- [ ] `claims[].field` 必须是已知列，`value` 与 `normalized_value` 同类型且值相等，`evidence_ids` 只引用同 field 证据；`model_confidence` 仅审计、不参与融合。
- [ ] `inspect_database()` 返回五表 Schema，与 Validator 白名单同源，evidence 暴露 `field` 而非 `field_name`。
- [ ] `sql_max_length` 在 contracts 与 Validator 同源，长度校验一致。
- [ ] Validator 攻击样例全部拒绝：`DROP TABLE`、`SELECT 1; DELETE`、注释绕过、`pg_sleep`、`COPY`、非白名单表；合法单表 SELECT 通过。
- [ ] 无 LIMIT 的合法 SELECT 被自动改写为 `LIMIT sql_max_rows+1`；自带 LIMIT ≤ 上限保留；自带 LIMIT > 上限返回 `limit_too_large`。
- [ ] Validator 错误分"可修正/阻断型"两类，判定结果落 `tool_call` 日志。
- [ ] `query_database()` 在 `agent_readonly` + 3s 超时 + 100 行上限下返回 Observation，truncated 语义正确；未配置只读账号时返回 `database_not_configured`。
- [ ] `evidence_ids` 能从结果行 UUID 反查 evidence 并去重、受限。
- [ ] 单模型 Agent 循环能自主查询数据库并输出合规 Recommendation JSON；超限/工具失败明确标记，不阻塞后续请求。
- [ ] Agent Run / Tool Call 全程落库，可用 `request_id` / `agent_run_id` / `tool_call_id` 串联。
- [ ] 建立离线查询测试集雏形（见 §8.1）并记录各模型首轮表现（见 §8.2 指标）。

### 8.1 离线查询测试集雏形

| 用例 | 输入要点 | 期望 |
| --- | --- | --- |
| 单表过滤排序 | GPU 且 vram_gb ≥ 12，按 name 排序 | 返回 hardware 行，无截断 |
| 三表 JOIN | 模型 + 变体 + benchmark | 合法通过，列名/别名正确 |
| Evidence 查询 | 按 entity_type/entity_id 查 evidence | evidence_ids 反查命中 |
| 空结果 | 不存在的品牌/型号 | 0 行；模型归入 insufficient_information |
| 错列名自修 | 首次用错列名（如 `field_name`） | 第一次可修正错误，第二轮修正后成功 |
| 恶意 SQL 拒绝 | DROP / 多语句 / pg_sleep / 非白名单表 | 全部阻断且落 tool_call 日志 |
| 超限截断 | 无 LIMIT 的大结果集 | truncated=true，行数 = sql_max_rows |

### 8.2 评估指标（从 tool_call / agent_run 日志计算）

| 指标 | 定义 |
| --- | --- |
| SQL 语法成功率 | 通过 `validate_sql` 的 `query_database` 调用 / 总 `query_database` 调用 |
| SQL 语义正确率 | 查询结果与标准答案一致的任务数 / 任务总数 |
| 自修成功率 | 首次失败后最终成功的任务数 / 首次失败任务数 |
| 越权拦截率 | 阻断型错误数 / 恶意样例总数（应恒为 1.0） |
| 平均工具调用次数 | tool_call 记录数 / Run 数 |
| 有效结果利用率 | 最终 Recommendation 引用了结果中 evidence_ids 的 claim 数 / claim 总数 |

## 9. 归档说明

本文档已归档。完整改动清单、部署步骤、测试覆盖与验收状态见 [V2.2.md](V2.2.md)；新增接口说明见 [API_Documentation_V2.2.md](../API_documents/API_Documentation_V2.2.md)。Step 9 真机验收（需 PostgreSQL）完成后，将验收结果回填至实现记录。
