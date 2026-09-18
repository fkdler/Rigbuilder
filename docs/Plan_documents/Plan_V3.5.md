# RigBuilder Plan V3.5：后端运行时可观测性与管理界面

> 状态：**方案待评审**（本轮为计划书，尚未实施）
> 继承：[Plan_V3.4.md](Plan_V3.4.md)（可信数据链与 Conversation Terminal）
> 2026-09-13 基线：后端 `213 passed, 1 skipped`；前端 Vitest `4 passed`；Playwright desktop+mobile `10 passed`；`vue-tsc` + `vite build` 通过
> 数据规模：`agent_run` **183** 次运行、`query_job` 60 个、`query_event` 4182 条、累计 **7 823 641** token
> 相关契约：[Agent_Loop_Contract_V3.3.md](../Data_documents/Agent_Loop_Contract_V3.3.md)

## 0. 版本结论

V3.4 完成后，系统的**写入侧**已经闭环：三 Agent 并行、真值校验、`fusion-v1`、Job/SSE、Conversation 全部可用且测试通过。缺口在**读取侧**。

后端目前只有两个运维端点：

```text
GET /health           → {"status": "ok"}
GET /health/database  → {"status": "ok", "database": "reachable"}
```

而 `agent_run.metrics` 里**已经落库的 33 个键**（耗时、token、轮次、上下文裁剪数、重复语句拒绝数、已知候选 ID 数、终局路径……）**没有任何查询入口**。要看这些数字只能连库手写 SQL，这在本轮 agent 循环排障中被反复证明是瓶颈：定位「覆盖率天花板」时必须在 psql 里手写 JSONB 聚合，定位「agent-c 失败形态」时必须逐条 dump `agent_message`。

V3.5 只做一件事：**把这些已存在的数据接一个只读出口**。本轮**不新增埋点、不新增表、不新增迁移、不改热路径**。

## 1. 当前系统基线（代码与数据核实结果）

### 1.1 现有后端端点

| 模块 | 端点 |
|---|---|
| `app/main.py` | `GET /health`、`GET /health/database` |
| `app/api/agent.py` | `POST /api/agent/fusion`、`POST /api/agent/run` |
| `app/api/chat.py` | `POST /api/chat`、`POST /api/model-comparison` |
| `app/api/conversations.py` | `POST /api/conversations`、`GET /api/conversations`、`GET /api/conversations/{id}`、`DELETE /api/conversations/{id}` |
| `app/api/query.py` | `POST /api/query/jobs`、`GET /api/query/jobs/{id}`、`POST /api/query/jobs/{id}/cancel`、`GET /api/query/jobs/{id}/trace`、`GET /api/query/jobs/{id}/events`（SSE） |

**4 个路由模块、13 个端点。** 无任何 metrics / stats / admin 入口。

### 1.2 可用数据源（实测）

`agent_run.metrics`（JSONB）的 33 个键，及其在 183 次运行中的覆盖次数：

| 键 | 覆盖 | 键 | 覆盖 |
|---|---|---|---|
| `rounds_used` / `sql_calls_used` / `tool_calls` | 183/183 | `context_pruned_messages` | 132/183 |
| `agent_messages` / `parallel` / `protocol` | 183/183 | `known_candidate_ids` / `duplicate_statements_rejected` | 126/183 |
| `fusion_request` / `truth_verified` | 183/183 | `terminal_finalize` | 113/183 |
| `queue_duration_ms` / `total_duration_ms` | 183/183 | `terminal_tool` / `native_fallback` / `submit_attempts` | 107/183 |
| `verification_duration_ms` | 183/183 | `agent_duration_ms` / `unclaimed_reasons_count` | 107/183 |
| `llm_calls` / `total_tokens` / `prompt_tokens` / `completion_tokens` | 180/183 | `fusion_duration_ms` | 60/183 |
| `llm_duration_ms` / `tool_duration_ms` | 180/183 | `protocol_retries` | 24/183 |
| `generation_duration_ms` / `prompt_eval_duration_ms` | 180/183 | `llama_timings` / `native_tools_supported` | 180/183 |

> **这是设计的关键约束**：33 个键里有 6 个只覆盖 107–132 次运行。缺失的键必须报 `null`，**不能报 0**，否则「没有记录」与「真实为零」会被混为一谈。

其他可用来源：

| 来源 | 内容 |
|---|---|
| `agent_run` | `status`（completed 60 / failed 123）、`error_code`、`model_id`（a/b/c 各 61 次） |
| `query_job` / `query_event` | 任务状态、`resolved_mode`、起止时间、每 job 平均 82 条事件 |
| `truth.dataset_release` | `rigbuilder-v3-2-2026-09-07` / `accepted` |
| `information_schema` | `agent_catalog` **12 视图**、`truth` **33 表** |
| `alembic_version` | `0007_create_query_jobs` |
| `app/agents/capabilities.py` | `capability_store`（进程内 capability 状态，已实现） |
| `app/inference/servers.py` | `probe_profiles()`（三端点可达性与延迟，已实现） |
| `app/db/session.py` | `SessionLocal`（应用账号）、`get_readonly_engine()`（只读账号 + `statement_timeout` + `default_transaction_read_only=on`） |

### 1.3 一个当前无法从界面看到的结论

实测聚合（183 次运行）：

```text
LLM 耗时      9 275 745 ms   （占 Agent 总耗时约 96.6%）
工具耗时          5 146 ms
LLM 调用次数        1 028 次
累计 token      7 823 641
```

即**性能瓶颈完全在推理侧**，工具与数据库开销可忽略。这个结论目前必须靠手写 SQL 才能得到，而这正是 V3.5 要消除的。

## 2. 可原样保留的模块（仅微调或不改）

- `app/agents/**`（含 `loop.py`、`capabilities.py`、`context_budget.py`）—— **零改动**
- `app/services/**`（`fusion.py`、`jobs.py`、`agent.py`、`llm.py`）—— **零改动**
- `app/verification/**`、`app/fusion/**`、`app/tools/**` —— **零改动**
- `app/inference/profiles.py`、`servers.py` —— **只读调用，不改**
- `app/models/**` —— 复用，不新增 ORM 模型
- `app/db/session.py` —— 复用 `SessionLocal`，不改
- 全部 13 个现有端点的路径、响应结构、状态码 —— **不得改变**

## 3. 需要修改的模块

| 模块 | 改动 | 理由 |
|---|---|---|
| `app/main.py` | `/health/database` 升级；注册 admin 路由 | 端点已在此文件；`lifespan`（含 `parallel_configuration_issues` 与 capability smoke 门禁）**一行不动** |
| `backend/tests/`（新增文件） | 见 §8 | 沿用既有测试风格 |

> **实施时的取舍**：初稿曾计划把 `/health` 与 `/health/database` 迁出到新的 `app/api/health.py`。**放弃该重构**——多改一个文件就多一份回归面，而收益只是文件更整齐。改为**原地手术式升级**，把 `main.py` 的改动压到最小。

## 4. 需要新增的模块

```text
backend/app/admin/__init__.py        # 包说明
backend/app/admin/models.py          # 自足 Pydantic 响应模型
backend/app/admin/stats.py           # agent_run / query_job / query_event 的 SQL 聚合
backend/app/admin/introspection.py   # 数据库概况 + 三端点实时状态
backend/app/api/admin.py             # 新路由（prefix="/api/admin"）
backend/tests/test_admin_stats.py    # 新增测试
```

响应模型放在 `app/admin/models.py` 而非 `app/schemas/`，**目的是让本轮不编辑任何既有模块**。`app.schemas.*` 已被现有模块大量导入，往那里加东西会扩大回归面。

## 5. 端点清单

| 方法 | 路径 | 内容 |
|---|---|---|
| `GET` | `/health` | **行为不变** |
| `GET` | `/health/database` | **升级**：由 `reachable` 扩展为 release / 视图数 / 表数 / 迁移版本 / 探针延迟 |
| `GET` | `/api/admin/stats/agents` | 模型运行统计 |
| `GET` | `/api/admin/stats/jobs` | 任务与事件统计 |
| `GET` | `/api/admin/endpoints` | 三端点实时状态 |
| `GET` | `/api/admin/database` | 数据库概况与各视图行数 |
| `GET` | `/api/admin/summary` | 前四项汇总（仪表盘一次取数） |

### 5.1 `/health/database`（升级，保留原字段）

```json
{
  "status": "ok",
  "database": "reachable",
  "release_key": "rigbuilder-v3-2-2026-09-07",
  "release_status": "accepted",
  "alembic_version": "0007_create_query_jobs",
  "agent_view_count": 12,
  "truth_table_count": 33,
  "latency_ms": 3
}
```

**`status` 与 `database` 两个字段必须保留且语义不变**（只增不改）。`backend/scripts/start_local_stack.ps1` 的连通性检查读的正是这两个字段，改掉会直接弄坏一键启动脚本的验收输出。

### 5.2 `/api/admin/stats/agents`

参数：`window_hours`（默认 24，`<=0` 表示全部时间）、`model_id`（可选）、`limit`（默认 20）。

按模型分组返回：

```text
runs / completed / failed / completion_rate
truth_verified / truth_verified_rate
errors                     按错误码降序
rounds / sql_calls / llm_calls / tool_calls     各含 avg / p50 / p95 / max
prompt_tokens / completion_tokens / total_tokens            合计
llm_duration_ms / tool_duration_ms / prompt_eval_duration_ms / generation_duration_ms  合计
context_pruned_messages / duplicate_statements_rejected / protocol_retries            合计
runs_with_usable_candidate_id         known_candidate_ids > 0 的运行数
metric_coverage                      每个键覆盖的运行数（消除 null 的歧义）
```

另附 `by_error_code` 与 `by_day` 两个分组，用于回答「agent-c 历史上坏在哪」「今天比昨天好了多少」。

### 5.3 `/api/admin/stats/jobs`

```text
by_status / by_resolved_mode / by_error_code
duration_ms       p50 / p95 / max（completed_at - started_at）
coverage          p50 / p95 / max（取自 result_payload，不重算）
events_by_type
fusion_jobs                     fusion 任务总数
fusion_with_zero_coverage       其中 agent_coverage = 0 的数量
recent                          最近 N 个 job 的 id/状态/模式/耗时/coverage/事件数
```

`agent_coverage` **从存储的 payload 直接读取，不重算**，以保证管理界面永远不可能与前端显示不一致。非 fusion 任务该值为 `null`，fusion 但无有效候选为 `0.0` —— 两者分开统计，合并成一个平均值会把语义抹平。

### 5.4 `/api/admin/endpoints`

并行探测每个 profile，返回：

```text
profile_id / endpoint_url / configured_model_id
reachable / latency_ms / loaded_models / error
capability_state           来自 capability_store
context_size / max_tool_rounds / max_sql_calls / terminal_tool_supported
distinct_endpoints / distinct_model_ids / configuration_issues / parallel_capable
```

并**暴露此前只能读 `.env` 或查库才知道的配置事实**：

```text
agent_terminal_tool_supported     当前 false
agent_parallel_required           当前 false
capability_smoke_on_startup       当前 false
agent_max_rounds / agent_max_sql_calls
```

> 这一项直接对应排障痛点：`AGENT_TERMINAL_TOOL_SUPPORTED` 的取值决定了 Agent 走哪条终局路径（实测该开关的 A/B 结果是 completed 6/6 对 2/6），但它此前**在运行时完全不可见**。

### 5.5 `/api/admin/database`

```text
release_key / release_status / alembic_version
agent_view_count / truth_table_count / recommendable_entities
views                每个 agent_catalog 视图的行数
empty_views          空视图清单
key_tables           关键 truth 表行数
empty_key_tables     空表清单
warnings             降级原因（release 缺失、视图列表不可读等）
```

`empty_views` 与 `empty_key_tables` 是**「哪些能力还没有数据」的直接答案**。实测当前状态：

```text
空视图：benchmark_result、entity_attribute_catalog、runtime_support_latest
空表：  benchmark_run(0)、benchmark_subject(0)、benchmark_metric(0)、
        runtime_support_snapshot(0)、entity_attribute(0)、compatibility_edge(0)、rule_definition(0)
```

这解释了为什么「本地 AI 部署」与「性能评估」两个场景目前取不到事实支撑 —— 架构齐全、数据为零。把这件事变成页面上一行字，比留在文档里更有用。

**行数取值策略**：`rows > 50 000` 时改用 `pg_class.reltuples` 估算并置 `estimated: true`；当前语料最大仅 799 行（`fact_evidence`），全部走精确 `count(*)`。

## 6. 界面层

分两步，**API 先行**：

- **P1（本轮必做）**：完成 §5 的 JSON API。FastAPI 自带 `/docs` 即可读，**零新依赖**。
- **P2（API 稳定后独立验收）**：一个自包含只读页面 `GET /admin`（服务端返回单文件 HTML + 内联 CSS/JS，从上述端点取数）。**不引入 Jinja2、不引入前端构建工具**；若需图表用内联 SVG 条形图，**不引入图表库**。

分成两步的理由：P1 无依赖风险、可立即验收；P2 的形态（原生 HTML vs 做进 Vue 前端）需看到 P1 的真实数据后再定，届时单独评审。

## 7. 潜在回归风险

1. **聚合查询拖慢或锁库** —— 走 `SessionLocal` 只读 SELECT；只读引擎已配 `statement_timeout`；所有查询支持 `window_hours` 限制扫描量；不触碰热路径事务。防线：验收要求 `/api/admin/summary` 在 183 行数据下 < 500 ms。
2. **新路由与现有路由冲突** —— 统一挂在 `/api/admin`，与 `/api/{agent,chat,conversations,query}` 无重叠。防线：测试断言现有 13 条路径**逐条未变**。
3. **改 `main.py` 波及 lifespan** —— `lifespan`（含并行配置自检与 capability smoke 门禁）**一行不动**，只升级一个端点函数并加两行路由注册。防线：`test_fusion_api.py` 与启动脚本探针继续通过。
4. **`metrics` 缺失键被当成 0** —— 每个指标返回 `null` 而非 0，并在 `metric_coverage` 里给出该键覆盖的运行数。防线：专测。
5. **端点探测阻塞** —— `asyncio.gather` 之外再套整体超时上限（默认 12 s）；任一 profile 失败只置该 profile `reachable=false`，不影响其余两个。
6. **统计口径与前端不一致** —— `agent_coverage` 直接读 payload，不重算；文档逐项写明每个指标的来源键。
7. **只读账号越界** —— 数据库概况用**应用账号**读 `information_schema` 与 `truth.*`，**不复用** `get_readonly_engine()`（该账号只有 `agent_catalog` 的 SELECT 权限，是设计边界）。
8. **大表 `count(*)` 变慢** —— 按 §5.5 阈值切换到 `reltuples` 估算并标注 `estimated`。
9. **`/health/database` 升级破坏启动脚本** —— 原两个字段只增不改，见 §5.1。

## 8. 测试与实施顺序

### 8.1 测试更新（回归 + 新增）

新增 `backend/tests/test_admin_stats.py`，**不依赖真实数据库**（`monkeypatch` 注入桩 session / 桩行）：

- 空库：所有聚合返回 0 或 `null`，不抛异常
- `metrics` 缺键：相关指标为 `null` 且覆盖计数正确
- 单个视图不存在：数据库概况降级为可用项，不抛 500
- 端点探测：一个 profile 抛异常时其余仍 `reachable=true`
- `agent_coverage` 的 `null`（非 fusion）与 `0.0`（fusion 无候选）不被合并
- 错误码分布按计数降序
- **契约守护**：现有 13 条路由仍在 OpenAPI 中且路径未变
- **只读守护**：admin 模块不出现 `commit` / `flush` / `add(` 写路径
- `/health/database` 仍返回 `status` 与 `database` 两字段

另加一条 opt-in 真库用例（`RUN_POSTGRES_INTEGRATION=1`），对库中全部 `agent_run` 跑一次聚合，断言不抛异常。

### 8.2 阶段推进

1. **P1 只读聚合层**：`app/admin/models.py` + `stats.py`，纯函数式聚合，无路由。
2. **P2 自省层**：`app/admin/introspection.py`（数据库概况 + 端点探测）。
3. **P3 路由与 health 升级**：`app/api/admin.py`、`main.py` 原地升级 + 注册。
4. **P4 测试**：§8.1 全部通过，且现有 213 项零回归。
5. **P5 验收**：§9 逐条取证。
6. **P6（可选）**：`GET /admin` 只读页面。

## 9. 验收清单

### 已满足（V3.5 之前）

- 三 Agent 并行、真值校验、`fusion-v1`、Job/SSE、Conversation 全部可用
- 后端 `213 passed, 1 skipped`；前端 Vitest `4 passed`；Playwright `10 passed`；构建通过
- Truth DB release `rigbuilder-v3-2-2026-09-07` accepted，12 视图 / 33 表

### 尚未满足（V3.5 目标）

- `agent_run.metrics` 的 33 个键有查询入口
- `/health/database` 报告 release、视图数、表数、迁移版本
- 三端点实时状态与 capability 状态可从接口读到
- 空视图 / 空表清单可见（当前 3 个空视图、7 个空表）
- 运行期配置开关（`agent_terminal_tool_supported` 等）无需读 `.env` 即可看见
- `GET /admin` 只读页面（P2，可选）

## 10. 完成定义（V3.5）

- 新增 5 个只读端点与 1 个升级端点，**全部只读**：不新增表、不新增 Alembic revision、不写任何业务表。
- `agents/`、`services/`、`verification/`、`fusion/`、`tools/`、`inference/` **零改动**。
- 现有 13 条路由的路径、响应结构、状态码**逐条未变**；`/health/database` 的 `status`/`database` 两字段语义不变。
- `agent_run.metrics` 的每个指标都能通过接口读到，且**缺失与零可区分**（`null` + `metric_coverage`）。
- 单端点故障、单视图缺失、`metrics` 为 `NULL` 一律降级为「不可用」，不返回 500、不阻塞主链路。
- `pytest tests -q` 全绿，且现有 213 项零回归。
- `/api/admin/summary` 在 183 行数据下 < 500 ms。
- 验收前后 `agent_run` / `query_job` 行数不变（证明零写库）。

## 11. 明确不做

- 不新增数据库表、不新增 Alembic revision
- 不改 `app/agents/**`、`app/services/**`、`app/verification/**`、`app/fusion/**`、`app/tools/**`
- 不改前端（P2 若做进 Vue 需单独立项，需跑 vitest + playwright + 构建）
- 不引入 Prometheus / Grafana / 任何新依赖
- **不做鉴权** —— 与现有全部端点一致（当前系统无任何鉴权），面向本地单机部署。**若后续要暴露到局域网，这是必须先补的一项**。
- 不做历史数据回填：`metrics` 缺键的运行不会因此补齐，接口只如实报告覆盖数。

## 12. 待确认事项

1. **`/api/admin/*` 与 `/admin` 的访问控制**：当前计划不做鉴权，与现状一致。是否需要至少一个静态 token 头？请在评审时明确。
2. **P2 页面形态**：原生单文件 HTML（后端直出）还是做进现有 Vue 前端？建议看到 P1 真实数据后再定。
3. **`window_hours` 默认值**：当前计划默认 24 小时。历史排障需要更长的窗口，是否改为默认「全部时间」？
