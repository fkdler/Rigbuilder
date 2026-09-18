# RigBuilder Plan V4：用户体系、呈现质量与融合提速

> **状态标记（2026-09-15）：本文不再修改，部分内容已过时。** 部署拓扑（现为设备 A 服务 + 设备 B 推理）、运维脚本（单设备栈脚本已删除）、固定 Agent 数量与部分测试/耗时数据，均以 [Plan_V4.3.md](Plan_V4.3.md) 与 [Quick_Start_Guide.md](../Quick_Start_Guide.md) 为准；本文仅作历史记录保留。
>
> 状态：**方案待评审**（本轮为计划书，尚未实施）
> 继承：[Plan_V3.6.md](Plan_V3.6.md)（前端决策终端 + `/admin` 可观测界面，P0/P1 已实施；§12-1 约束编辑器与 §12-4 死代码删除未实施）
> 继承：[Client_Server_Deployment_Guide_V1.md](../Client_Server_Deployment_Guide_V1.md)（客户端-服务端形态，2026-09-14 现行有效，**本轮将其 §10 的第 1/2/3 项从「尚未实现」转入实施**）
> 相关契约：[API_Documentation_V3.1.md](../API_documents/API_Documentation_V3.1.md)（**已滞后**，本轮要求补 V4 文档）、[Agent_Loop_Contract_V3.3.md](../Data_documents/Agent_Loop_Contract_V3.3.md)、[frontend_V2.md](../frontend_design/frontend_V2.md)
> 本文的核实方式：直接阅读 `backend/app/**`、`frontend/src/**`、`backend/alembic/versions/**`、`data/v3/releases/rigbuilder-v3-2-2026-09-07/manifest.json`，并以 `pytest --collect-only` 实测测试规模。**下文所有结论都标注了 `文件:行号` 或来源文档；只有标注「来源：文档记录」的数字才来自既有 Plan 的陈述。**

---

## 0. 版本结论

后端链路（模型 → Agent → 上下文 → 数据库工具 → 真值检验 → 融合 → 自然语言 → 持久化 → HTTP）**已经全部闭环且带测试**，其中「自然语言输出」也不是空白 —— `app/services/narrator.py` 已实现并已接入融合返回（`services/fusion.py:410`、`:759`），前端也已渲染（`RecommendationBlock.vue:73`、`:91`）。**所以本轮不是"从零做一个自然语言输出"，而是"把已有的逐候选解释，重塑成一个面向用户的最终结论"。**

真正**没有**的东西只有一类：**用户**。全仓无 `user` 表、无鉴权中间件、无会话归属过滤，`Client_Server_Deployment_Guide_V1.md §10` 把它明确列为"尚未实现、需要另行立项"的第 1、2、3 项（鉴权、会话归属与多租户隔离、速率限制）。本轮规划的第二条正好落在这一缺口上。

V4 做四件事：

1. **呈现质量** —— 把 Narrator 的产出升级为「主推 + 理由 + 备选 + 保留意见」的最终结论卡片；同时给出融合耗时分解与可实测的提速清单。
2. **用户数据系统** —— 新增 `user` 表、登录令牌、`conversation.owner_id` 归属维度、按用户的会话/记录/上下文隔离、管理员用户管理。
3. **前端美化** —— 在既有 terminal token 体系内重排信息层级，并把「最终结论」放到首位。
4. **API 文档 V4** —— 新建独立文档，覆盖鉴权、归属语义、SSE 事件表、admin 写接口。

**明确不做**：缓存、多 worker、跨进程锁、Redis、任何高并发设计（用户已确认本项目只做本机演示）。

---

# Part 1 · 当前项目进展（逐项核实）

## 1.1 后端链路状况

### 1.1.1 模型链路（已完成）

**调用链**

```text
Browser → Vite → FastAPI :8000 → llama-server ×3 (127.0.0.1:8081/8082/8083) → GGUF
```

| 环节 | 实现位置 | 结论 |
|---|---|---|
| Profile 构造 | `app/inference/profiles.py:55-89` `build_profiles()`，顺序 `PROFILE_ORDER = ("agent-a","agent-b","agent-c")`（`:19`） | 完成 |
| 并行能力判定 | `profiles.py:112-135` `parallel_configuration_issues()` / `settings_parallel_capable()`：要求三端点互异 + 三模型 ID 互异 | 完成 |
| 跨 Job 串行 | `app/inference/scheduler.py:17-26` `asyncio.Semaphore(1)`，全局单例 `:29` | 完成（**进程内**，因此只能单 worker） |
| 单次调用 | `app/services/llm.py:76-191` `complete_chat()`，三种模式 `chat / native_tools / schema`（`:103-125`），强制 `parallel_tool_calls=False`（`:119`）、`enable_thinking=False`（`:111`） | 完成 |
| 协议适配 | `app/agents/protocol/adapter.py:114` `ModelHandle.turn()` | 完成 |
| 端点探活 / tokenize | `app/inference/servers.py:100/143/158`、`:66` `count_prompt_tokens()` | 完成 |

**chat 路径**：`services/chat.py:25` `ChatService.reply()` → 同一串行闸门 → 一次 `complete_chat`（`:75`），带可选上下文压缩。
**fusion 路径**：`services/fusion.py:76` `FusionService.run()` → 串行闸门包裹（`:100-107`）→ `:119 _run()` 分派：

- **并行主线**（`settings_parallel_capable` 为真）：`fusion.py:634 _run_parallel()`，`asyncio.gather(..., return_exceptions=True)`（`:673-689`），每个 Agent 一个独立 `SessionLocal` 与独立 endpoint（`_agent_worker_parallel` `:423-632`）。
- **串行回退**：`fusion.py:119-419`，用 `run_agent_loop`（legacy 路径）。

> 本机部署已满足并行条件（`Client_Server_Deployment_Guide_V1.md §3.4` 实测 `parallel_capable: true`、三 profile 全部 `reachable`）。

### 1.1.2 Agent 链路（已完成）

**两套循环并存**（`agents/loop.py`，`__all__` 见 `:1446-1449`）：

| 路径 | 入口 | 用在哪 |
|---|---|---|
| 协议路径（主线） | `run_agent_loop_protocol()` `loop.py:1026` | 并行 fusion 的三个 worker（`fusion.py:515`） |
| Legacy | `run_agent_loop()` `loop.py:752`（内含 `_run_native()` `:435` / `_run_legacy()` `:678`） | 串行回退（`fusion.py:235`）与 `/api/agent/run` |

**工具集**（`app/tools/registry.py`）：

- `database_tools()` `:102` = `inspect_database` `:26` + `query_database` `:34` + `resolve_evidence` `:42`
- `full_tools()` `:106` = 上述 + `submit_recommendation` `:84`（终局工具，其 parameters 直接是 `Recommendation.model_json_schema()` `:98`）

**硬约束（全部在代码里，不是文档承诺）**

| 约束 | 位置 | 说明 |
|---|---|---|
| 轮数上限 | `loop.py:1119` `max_rounds = min(profile.max_tool_rounds, settings.agent_max_rounds)` | `profile.max_tool_rounds` 由 `agent_max_rounds` 驱动（`profiles.py:83`） |
| SQL 上限 | `loop.py:1292`（resolve_evidence 与 SQL 共享预算）、`:1320`（query_database） | `min(agent_max_sql_calls, profile.max_sql_calls)` |
| 重复语句拒绝 | `loop.py:1330-1350`（空白归一化 + `attempted_statements`） | 不花一次 DB 往返 |
| 单轮生成上限 | `loop.py:1126` `round_max_tokens = 8192` | 查询轮 |
| 终局生成上限 | `loop.py:1135` `finalize_max_tokens = 4096` | 受 `prompt_reserved_completion_tokens=2048` 约束的推导 |
| 上下文预检 + 裁剪 | `loop.py:1148-1167` → `agents/context_budget.py:199-232` `prune_old_tool_exchanges()` | `resolve_evidence` 结果受保护（`PROTECTED_TOOLS` `:170`） |
| 终局收敛开关 | `loop.py:1059`（有终局工具则用 `full_tools()`，否则只有 `database_tools()`）、`:1375-1438`（无终局工具时的 schema finalize 兜底） | `settings.agent_terminal_tool_supported`（`core/config.py:102`） |

> **本机取舍**：`AGENT_TERMINAL_TOOL_SUPPORTED=0`（走 schema finalize 路径）。依据 `Client_Server_Deployment_Guide_V1.md §3.4` 注释：实测 completed 6/6（0）vs 2/6（1）。

**失败隔离**：并行 worker 内任何异常都在 worker 内被捕获（`fusion.py:530-531`），不会取消另外两个 Agent；单测 `tests/test_one_agent_failure_isolation.py`、`tests/test_three_agent_concurrency.py`。

### 1.1.3 上下文管理（已完成，有两处已知不精确）

**存储**：`Conversation` / `ConversationMessage` / `ConversationContextSnapshot`（`app/models/`，迁移 `20260830_0002`、`0003`）。
**服务**：`app/context/service.py`。

| 能力 | 位置 | 说明 |
|---|---|---|
| 会话获取/创建 | `service.py:104-113` | 不存在或已删抛 `ConversationNotFoundError` |
| 追加消息 | `service.py:115-129` | `estimate_tokens` 记录 token_count |
| 提示词组装 | `service.py:262-314` `_compose_messages()` | system + 快照摘要 + 已确认约束 + 最近 `context_recent_turns*2` 条 |
| 历史上限 | `service.py:236-260` `_history_token_budget()` | `min(llm_context_window_tokens, agent_default_context_size) - reserved` × `compression_ratio(0.70)` |
| 二次封顶 | `service.py:288-292` | 再取 `agent_capacity × 0.35` |
| 单条消息截断 | `service.py:298` + `:67-84` `_cap_message_content()` | 超长历史消息只截给模型，**不改库里的原文** |
| 压缩候选判定 | `service.py:316-335` `prepare_context()` | 超阈值才产生 `CompressionCandidate` |
| 压缩产出 | `service.py:337-354` `compression_prompt()` + `:356-370` `parse_summary()` + `:372-387` `save_snapshot()` | 8 个固定 key（`SUMMARY_KEYS` `:30-39`），版本递增 |
| 开关 | 需 `llm_context_window_tokens` 非空（`service.py:327`） | 未配置时**自动压缩整体关闭** |

**两处已知不精确（已写在代码注释里，不是新发现）**：

1. `context/service.py:58-60` 用 `ceil(len/2)+4` 估算（保守）；`agents/context_budget.py:23-41` 用 `len/4`（乐观）。**两套估算并存且方向相反**，前者用于历史裁剪，后者用于循环内 preflight。
2. `context_budget.py:44-56` 明确记录：preflight 的估算在 UUID 密集历史下**偏低约 37%**，因此以服务端 `/tokenize` 的 `exact_prompt_tokens` 为最终判据。

### 1.1.4 数据库查询工具与安全边界（已完成）

`app/tools/database.py`（44 KB）、`validator.py`、`schema.py`、`tables.py`、`errors.py`、`registry.py`。

| 边界 | 位置 / 配置 |
|---|---|
| 只读角色，未配置即拒绝 | `db/session.py:28-30` fail-closed；角色由 `scripts/create_agent_readonly.py` 建 |
| 只允许 SELECT / WITH | `tools/validator.py` |
| 禁函数、禁窗口函数 | 同上前提；prompt 侧也明示（`agents/prompts.py:47-48`） |
| join ≤ 4 / 子查询深度 ≤ 3 / 列数 ≤ 40 / 长度 ≤ 10000 | `core/config.py:41-44` |
| 语句超时 3000 ms / 行数 ≤ 100 / 单 cell ≤ 500 字符 | `core/config.py:45-48` |
| 工具结果 ≤ 12000 字符（超出裁剪并告知模型收窄） | `core/config.py:49-52` |
| 只读连接池 10 + 10 | `core/config.py:88-89` |
| 审计 | `ToolCall` 表（tool/arguments/query_id/success/error_code/truncated/duration_ms/result_summary） |

### 1.1.5 真值检验（已完成）

`app/verification/service.py:TruthVerifier`，`repository.py` 提供只读 Truth 访问，`rules.py` 是派生规则求值器注册表（`rules.py:22-35`）。

- 入口 `service.py:38-43 verify()`：先取 release_key，无 release 直接抛 `truth_release_unavailable`。
- 候选级门禁 `service.py:45-85 _verify_candidate()`：`candidate_not_found` / `candidate_type_mismatch` / `candidate_not_recommendable` 任一出现即 `candidate_valid = False`。
- Claim 分派 `service.py:87-96`：

| claim_type | 校验方式 | 位置 |
|---|---|---|
| `fact` | 字段定义 → 库值与断言比对（单位换算 + tolerance）→ 引用 evidence 必须 `accepted` 且同实体同字段 | `:98-146` |
| `measurement` | 对齐 benchmark run（存在性 / 状态 / 主体 / 指标 / 值） | `:160-190` |
| `derived` | `rule_key@version` + 已注册求值器 | `:192-217` |
| `price` | 对齐最新 `PriceSnapshot`（currency + region + condition + price_type），带 `price_provenance` | `:219-273` |
| `preference` | 恒为 `unverifiable`（`preference_not_truth_claim`，`:96`） | 设计如此 |

- **四态**：`supported` / `conflict` / `missing` / `unverifiable`；每条带 `reason_code`、canonical 值、有效 evidence/price_snapshot/benchmark/rule_refs。
- **fail-closed 语义**：验证只读 Truth DB，模型自报的 `score` 与 `model_confidence` 完全不参与（`API_Documentation_V3.1.md §197`、`engine.py`）。

### 1.1.6 结果融合算法与返回（已完成）

**算法**：`app/fusion/engine.py:FusionEngine.fuse()`，策略版本 `fusion-v1`（`:23`）。

| 步骤 | 位置 | 公式/规则 |
|---|---|---|
| 归并键 | `:76-91` | `(candidate_type, candidate_id)`，同模型内先 `seen` 去重 |
| consensus | `:158` | `Σ(1/rank) / valid_agent_count` |
| 有偏好的推荐分 | `:161-169` | `100 × (0.70 × preference_utility + 0.30 × consensus)` |
| 无偏好的推荐分 | `:167-169` | `100 × consensus`（`preference_utility` 记 0.5） |
| credibility | `:186-191` | `100 × (0.40·fact_support + 0.25·proof_coverage + 0.20·information_completeness + 0.15·consensus·agent_coverage)` |
| hard 约束淘汰 | `:196-203` | 非 `satisfied` 即写 `hard_constraint_<status>:<id>` |
| 关键字段冲突淘汰 | `:204-211` | 命中的 hard field 约束对应 claim 为 `conflict` |
| Claim 归并 | `:259-274` | 同 key 取最高优先级状态：`supported > conflict > missing > unverifiable` |
| 稳定排序 | `:281-288` | score desc → credibility desc → consensus desc → type asc → id asc |
| 输出 | `:139-145` | `top_k = active[:top_k]` + `eliminated` + `trace` |

`trace.formula`（`:112-118`）把公式**随结果一起返回**，`input_summary`（`:119-133`）记录 `agent_rankings`、`constraint_count`、`requested_top_k`。
约束评估器 `app/fusion/constraints.py:25-34` 分派 field/price/compatibility/runtime 四类，全部确定性、只读库。

**返回契约**：`app/schemas/fusion.py:FusionRunResponse`（`:173-182`）= `agents[]` + `result` + **`natural_language`** + `error`。

**自然语言输出（已有，且是"防伪造"设计）**：`app/services/narrator.py`

- `narrate_fusion()` `:195-230`：复用常驻模型（`NARRATOR_PROFILE_ID`，默认 agent-b），超时 60 s（`:41`）、max_tokens 700（`:42`）。
- 只读 brief：`_brief()` `:114-140` 只放 supported 的 claim，不含排名以外的任何可改写信息。
- **数字忠实性校验** `_numbers_are_faithful()` `:78-102`：概览里的每个数字必须来自允许集合；每个候选的解释只允许出现自己的数字与自己的名称（**不得提及别的候选名**）。
- **强制降级**：解析失败 / 候选 id 序列不匹配 / 数字不忠实 / 超时 / LLM 错误 → 一律 `deterministic_narration()` `:147-172`（模板），**绝不返回半成品**。
- 单测：`tests/test_narrator.py`、`tests/test_narrator_faithfulness.py`。

### 1.1.7 HTTP 接口与持久化（已完成）

| 端点 | 位置 | 备注 |
|---|---|---|
| `POST /api/query/jobs` → 202 | `api/query.py:18-20` | 异步提交，立即返回 |
| `GET /api/query/jobs/{id}` | `:23-28` | 轮询 |
| `POST /api/query/jobs/{id}/cancel` | `:31-36` | |
| `GET /api/query/jobs/{id}/trace` | `:39-44` | 用户安全轨迹（无模型推理） |
| `GET /api/query/jobs/{id}/events` (SSE) | `:47-86` | `X-Accel-Buffering: no`，15 s 心跳，0.5 s 轮询 DB |
| `POST/GET/GET/DELETE /api/conversations` | `api/conversations.py:28/37/56/83` | soft delete；有活跃 job 时 409（`:88-102`） |
| `GET /api/admin/{summary,stats/agents,stats/jobs,endpoints,database}` | `api/admin.py:45/62/76/90/107` | **全部只读**（模块 docstring `:1-9` 明确声明） |
| `POST /api/agent/fusion` | `api/agent.py:13` | 同步长阻塞路径，前端不用 |
| `POST /api/agent/run` | `api/agent.py:37` | 单 Agent legacy，前端不接入 |
| `GET /health`、`/health/database` | `main.py:64/70` | **运维脚本依赖**（`stack_status.ps1`） |

**任务编排**：`app/services/jobs.py`

- 任务注册表在**进程内存**（`:33`），因此多 worker 下取消会失效。
- 重启恢复：`recover_after_restart()` `:35-49` 把所有非终态置 `failed` + `server_restarted`，**不重放**。
- 事件写入带行锁避免与取消竞争（`:326-336`）。
- 事件脱敏白名单：`_sanitize_event()` `:314-323`（**detail 只放行 11 个 key**）。

**数据库迁移**：`alembic/versions/` 7 个 revision（`20260827_0001` … `20260909_0007_create_query_jobs`）。

### 1.1.8 数据规模（实测自 release manifest）

`data/v3/releases/rigbuilder-v3-2-2026-09-07/manifest.json`：

| 类型 | 数量（按 manifest 条目计） |
|---|---|
| field_definition | 21 |
| hardware | ~90（GPU 覆盖 NVIDIA RTX 30/40/50、AMD RX 6000/7000/9000、Intel Arc；CPU 覆盖 Intel 12–15 代与 AMD Ryzen 5000/7000/9000；含 socket / 电源 / 内存 / 存储 spec 实体） |
| model | 13 |
| workload | 13 |
| organization | 21 |
| price_snapshot | ~90 |
| source_document | ~215 |
| 合计 | 437 条 accepted 记录，`release_status = accepted` |

### 1.1.9 后端链路小结

| 问题 | 结论 | 依据 |
|---|---|---|
| 模型链路 | ✅ 已完成（三端点并行 + 串行回退 + 进程内闸门） | `inference/*`、`services/llm.py` |
| 上下文管理 | ✅ 已完成（分层组装 + 预算封顶 + 单条截断 + 快照压缩） | `context/service.py` |
| 数据库查询工具 | ✅ 已完成（三工具 + 只读角色 + 校验器 + 行/超时/字符三重预算 + 审计表） | `tools/*`、`core/config.py:41-52` |
| 真值检验 | ✅ 已完成（4 类 claim、4 态、证据级校验、fail-closed） | `verification/service.py` |
| 融合算法与返回 | ✅ 已完成（fusion-v1 全公式 + trace + eliminated + top_k） | `fusion/engine.py` |
| 自然语言输出 | ✅ 已完成并接入（Narrator + 数字忠实校验 + 确定性降级） | `services/narrator.py`、`fusion.py:410/:759` |
| 用户 / 鉴权 / 归属 | ❌ **完全没有** | 全仓无 `user` 表、无 `Depends(...)` 鉴权、`conversations.py:38-53` 无 owner 过滤 |
| 后端测试规模 | 268 项（实测 `pytest --collect-only`；与 V3.6 记录 `266 passed, 2 skipped` 一致） | 本次实测 |

## 1.2 前端现状

**技术栈**：Vue 3 + TS + Vite 8 + Pinia + vue-router 5（history 模式），`package.json` 依赖仅 `axios / echarts / element-plus / pinia / vue / vue-router`。

**路由**（`src/router/index.ts:5-32`，4 条，无导航守卫、无 `meta`、无鉴权）：

| 路径 | 组件 | 加载 |
|---|---|---|
| `/` | `ConversationTerminal.vue` | 静态（首屏） |
| `/legacy` | `TerminalWorkbenchV2.vue` | 懒加载 |
| `/comparison` | `ModelComparisonView.vue` | 懒加载（Element Plus 随此 chunk，`:19` 注释） |
| `/admin` | `AdminDashboard.vue` | 懒加载（echarts 随此 chunk，`:25-26` 注释） |

**主界面**（`views/ConversationTerminal.vue`）：`AppSidebar` + `TerminalHeader` + 时间线（`:24-53`）。

- 每个 job 渲染：`AgentRunSummary`（`:73`）→ `RecommendationBlock`（fusion，`:77`）或 `terminal-answer`（chat，`:82`）→ 错误（`:87`）→ 运行中计时与取消（`:88`）。
- `fusionResult()` `:49-53` 把 `{kind:"fusion", ...}` 的 `kind` 剥掉后透传给 `RecommendationBlock`。

**融合结果渲染**（`components/terminal/RecommendationBlock.vue`）—— **比 V3.6 描述的更完整**：

| 内容 | 位置 |
|---|---|
| `top_k` 全量渲染（不是只渲染 `top_k[0]`） | `:21` `candidates = response.result?.top_k ?? []`，`:81` `v-for` |
| `natural_language.overview` 展示 | `:73` |
| **每个候选的自然语言解释** | `:91` `explanationFor(candidate)`（`:35-37`） |
| score 与 credibility 并列且语义区分 | `:86-87`（含 `title` 提示） |
| 已验证事实表 | `:98` `FactsTable` |
| metrics / evidence（排名 1 默认展开） | `:100-127`，默认规则 `:49` |
| execution trace / eliminated | `:131-145` |

**状态管理**（`stores/conversations.ts`）：

- 服务端为会话真值（`:41-49`）；`runners` / `eventsByJob` 按 conversation 隔离（`:47-49`）。
- SSE 主通道 + 断线降级 3 s 轮询（`:118-138`）。
- `currentConversationId`（`:44`）与 `currentMode`（`:52`）存 localStorage。
- `submit()` `:197-237`：无会话则先建；`createQueryJob` 固定 `top_k: 3`（`:221`）。

**两个已核实的断裂**（与 V3.6 §1.3 一致，本轮复核确认）：

1. **约束链路不通**：`components/terminal/Composer.vue:14` `await store.submit(text, store.currentConversationId)` 只传 2 个实参 → `conversations.ts:221` 的 `customConstraints ?? []` **恒为空数组**。后端约束模型完整（`api/agentFusion.ts:3-12`，4 类 + hard/preference），但界面上没有任何入口。
2. **无用户概念**：全目录检索 `login|auth|password|token|user_id|owner` 命中的 10 个文件全部是无关匹配（`author`、CSS token、`chartTheme` 字体串），**没有任何鉴权或用户代码**。

**Admin 界面**（`views/AdminDashboard.vue`）：只读，区块 = overview / agents / jobs / database / endpoints（`:134-166`），窗口筛选 `all time / 24h / 7d / 30d`（`:27-32`），逐区块降级（`:130-132`、`:144-165`），缺失显示 `—`。**无用户管理**、无任何写操作。

**死代码（V3.6 §1.1 记录，本轮复核仍在，未删除）**：
`views/TerminalWorkbench.vue`(17 KB)、`views/RecommendationWorkbench.vue`(11.5 KB)、`views/ChatView.vue`(2.3 KB)、`components/AsciiLogo.vue`(4 KB)、`components/SessionManager.vue`(6.7 KB)、`components/ConstraintEditor.vue`(5.2 KB)，合计约 47 KB。

**样式体系**：`styles/terminal-tokens.css`（在用的 token）+ `styles/tokens.css`（**零导入**，V3.6 §1.3-② 已核实其变量运行时未定义）。
`AsciiLogoLarge.vue` 是侧栏 LOGO，使用**彩虹渐变** —— 与设计简报 §3「禁止蓝紫渐变」冲突（V3.6 允许其存在，属存量违规）。

**测试**（`frontend/`，本次核实文件清单）：

- Vitest：`api/admin.spec.ts`、`stores/conversations.spec.ts`、`components/admin/{AgentStatsTable,chartTheme,format,JobStatsPanel}.spec.ts`、`components/terminal/{RecommendationBlock,TerminalHeader,TraceExpand}.spec.ts` —— **10 个文件**（V3.6 记录为 9 文件 / 71 项，实施后新增了 `format.spec.ts`）。
- Playwright：`e2e/admin-dashboard.spec.ts`、`e2e/conversation-terminal.spec.ts`，真机 `e2e/livesmoke.live.ts`（对应 `playwright.live.config.ts`）。
- **全部既有用例都假设"无需登录"**，这是本轮改造最大的回归面（见 §5）。

## 1.3 服务端-客户端约束与工作方式（刚完成）

来源：`docs/Client_Server_Deployment_Guide_V1.md`（2026-09-14 现行有效，取代旧的 `Application_Server_Guide_V1` / `Inference_Server_Guide_V1`）。

**拓扑**：1 台服务端 + N 台客户端（只需浏览器）

```text
客户端 1..N（浏览器 + 可选本机 Vite）
   │ HTTP —— 唯一需要打通的链路
   ▼
服务端（1 台）
   ├── FastAPI :8000  绑 0.0.0.0（唯一对外端口，单 worker）
   │     ├── PostgreSQL 127.0.0.1:5432（设计如此；本机实际 0.0.0.0，已记录为偏离）
   │     └── llama-server ×3 127.0.0.1:8081-8083（永不对外）
   └── 可选：Vite :5173 或 nginx :80
```

**三种接入形态**（§4.1）：A 各客户端自跑 dev server（**本机采用**，零 CORS）、B 浏览器直连后端（需 `FRONTEND_ORIGINS`）、C 服务端同源托管（生产推荐，需 nginx，仓库不提供）。

**硬约束（不可违反）**

| # | 约束 | 依据 |
|---|---|---|
| 1 | **后端必须单 worker** —— 闸门（`scheduler.py:17`）与任务表（`jobs.py:33`）都在进程内 | §12.4-1 |
| 2 | **推理端口只绑 127.0.0.1** —— `start_local_stack.ps1` 的 `$profiles` 硬编码 | §12.4-3 |
| 3 | **启动必须带 `NO_PROXY=127.0.0.1,localhost`** —— 否则 Windows 系统代理会让三端点全报 `router_unavailable`（已实测的坑） | §3.5 |
| 4 | **新增配置一律走 `Settings` 并给安全默认值** —— 保证"未配置也能起来" | §12.4-2 |
| 5 | **`backend/scripts/*.ps1` 必须 ASCII-only** | §12.4-7 |
| 6 | **不要删除/重命名三个运维脚本**（start / stop / status） | §12.4-5 |
| 7 | 前端新增端到端测试**不要硬编码 `<DEVICE_B_IP>`** | §12.4-6 |

**工作方式（日常运维）**：三个脚本覆盖全部动作 —— `start_local_stack.ps1`（`-BindAddress / -FrontendBindAddress / -SkipInference / -SkipFrontend / -WithChatSmoke`）、`stop_local_stack.ps1`（`-BackendOnly / -InferenceOnly / -WhatIf / -Force`）、`stack_status.ps1`（全绿退出码 0）。日志落 `.local-logs/`。
**关键坑**：`start_local_stack.ps1` 对**已占用端口是"复用"而非重启**，所以改完 `.env` 必须先 `stop_local_stack.ps1 -BackendOnly`，否则会伪装成成功（§11.1）。

**并发与容量语义（§7）**

| 现象 | 说明 |
|---|---|
| fusion Job 严格串行 | 闸门容量 1，包裹整个 fusion（`fusion.py:100-107`）；p50 约 86 s，第 3 个提交者约等 3 分钟 |
| 单 worker 不可扩 | 多 worker 会让闸门与任务表失效 |
| 重启打断所有人 | 任务不自动重放（`jobs.py:35-49`） |
| **会话列表零隔离** | `GET /api/conversations` 无 owner 过滤（`conversations.py:38-53`），`DELETE` 无归属校验（`:83-114`）→ **任何客户端都能看/删别人的会话** |
| 连接池可调 | `READONLY_POOL_SIZE` / `READONLY_MAX_OVERFLOW` |

**安全边界（§8）：当前版本没有任何鉴权。** 能访问 `:8000` 的人可以提交任务、读取全部会话、删除任意会话、查看全部运行统计。因此**只允许部署在受信局域网内**，绝不可映射到公网。

**§10「明确不做 / 后续」共 7 项**，其中第 1、2、3 项与本轮规划直接相关：

1. 鉴权与访问控制
2. 会话归属与多租户隔离
3. 提交速率限制

> **这就是用户规划第 (2) 条的定位：它不是新增需求，而是把部署指南里已经识别、已经写明"需要代码变更、不在本轮范围"的能力缺口正式立项。**

**2026-09-14 实测已通过**：后端 `0.0.0.0:8000`、推理 `127.0.0.1:8081-8083`、`/health`、`/health/database`、`/api/admin/endpoints`（三端点 reachable）、局域网地址可达、经 Vite 代理提交任务成功、SSE 逐条成帧。
**尚未执行**：前端页面 + `/admin` 六区块的真机验证、**双客户端并发**、跨客户端会话可见性确认（都需要真实客户端设备）。

---

# Part 2 · 本轮要做的事

## 2.1 目标一：呈现质量（最终自然语言输出 + 融合提速）

### 2.1.1 现状与差距（先说清"已经有什么"）

| 能力 | 现状 | 位置 |
|---|---|---|
| 自然语言输出 | **已有**：`overview` + 每候选 `explanation` | `narrator.py:195-230`、`schemas/fusion.py:161-171` |
| 前端展示 | **已有**：overview 段落 + 候选解释段落 | `RecommendationBlock.vue:73`、`:91` |
| 防数字伪造 | **已有**：数字忠实性校验 + 强制降级 | `narrator.py:78-102`、`:147-172` |
| 融合公式可解释 | **已有**：`trace.formula` + `input_summary` 随结果返回 | `engine.py:112-133` |

**差距（用户规划 (1) 真正指出的问题）**：

1. **主体仍是"排名列表 + 分数"**。`RecommendationBlock.vue:80-129` 的骨架是 `<ol>` 候选列表，score/credibility 是最显眼的元素，自然语言反而是列表上方的一段 `overview`（`:73`）。用户看到的仍是"按排名返回 + 一个分数"。
2. **Narrator 目前是"逐候选平铺解释"，不是"一个结论"**。`per_candidate` 的结构决定了它必须为每个候选写一段，天然无法表达"在这几个里我推荐哪个、为什么不是另一个"。
3. **没有"取舍说明"**。当 `agent_coverage < 1`、或某候选被 hard 约束淘汰、或事实缺失时，当前只有 `deterministic_narration` 的一句覆盖率，没有把"结论的不确定性"讲清楚。
4. **耗时**：融合 p50 **86.1 s** / p95 **154.0 s** / max 201.9 s（来源：Plan_V3.6 §1.2，117 个 job 实测），而 `FAST`（chat）只要 **743 ms / 1401 ms**（同源实测）。差距约 86 倍。

### 2.1.2 设计：把 Narrator 升级为「最终结论」（`PresentationResult`）

**新增契约（`app/schemas/fusion.py` 内新增，不改动既有字段）**：

```text
PresentationResult
├── headline: str                  # 一句话结论，例如"预算 5000 元内，最值得买的是 RTX 4060 Ti 16GB"
├── primary:                        # 主推（当 top_k 非空时必填）
│   ├── candidate_id: UUID          # 必须来自 top_k
│   ├── name: str                   # 必须等于该候选的 canonical_name
│   └── reasons: list[str]          # 2-4 条，每条必须引用该候选已验证 claim 的字段与值
├── alternatives: list[...]         # 备选 0-2 条，每条一句"与主推的差异"
├── caveats: list[str]              # 覆盖率不足、缺失事实、被淘汰的候选及原因（可为空）
└── per_candidate: list[...]        # 保留既有结构（向后兼容，前端明细区继续用）
```

**生成方式**：沿用现有 Narrator 通道（`narrate_fusion`），把 brief 扩展为包含 `eliminated` 的**原因摘要**与 `trace.agent_coverage`，并把 system prompt 从"逐候选改写"改为"给出一个结论"。**复用现有全部安全机制**：

- brief 仍然只读、只含 `supported` 的 claim（`narrator.py:114-140` 的扩展）；
- 数字忠实性校验扩展：`headline` / `reasons` / `alternatives` 里的数字必须落在允许集合内（沿用 `_numbers_are_faithful` `:78-102` 的思路）；
- `primary.candidate_id` 必须 ∈ `top_k` 且 `primary.name` 必须等于该候选的 `canonical_name`（沿用 `:99-101` 的"不得提及别的候选名"规则）；
- **任何校验失败 → 降级为扩展版 `deterministic_narration`**（模板也要输出主推 + 理由，不能退回只有分数）。

**硬规则（不可协商，写入文档与测试）**：

1. Narrator **不得改变**候选、排名、分数、可信度、事实与风险状态（继承 `narrator.py:28-31` 的既有纪律）。
2. `top_k` 为空时，`headline` 必须明说"没有候选通过真值验证"，`primary` 为 `null`。
3. `agent_coverage < 1` 时，`caveats` 必须明说覆盖不足，**不得暗示三模型共识**（继承 `narrator.py:37` 的既有规则）。

**前端改造（`RecommendationBlock.vue`）**：

- 顶部新增"最终结论"区块：`headline` 大字 → `primary`（名称 + `reasons` 列表）→ `alternatives` → `caveats`。
- 现有 `<ol>` 候选列表下移为「全部已验证候选（明细）」并默认折叠，保留 score/credibility/FactsTable/evidence/trace 不动。
- **对 `top_k.length === 1` 的既有行为保持等价**（沿用 V3.6 §7-1 的回归纪律）。

### 2.1.3 融合提速：耗时分解与可实测手段

**先诚实地分解**（每段都有代码依据）：

```text
总时长 = 排队等待 + max(三个 Agent) + 融合计算 + Narrator
         ↑           ↑                ↑           ↑
    串行闸门       每轮 = 1 次 LLM 生成 + 工具调用，最多 agent_max_rounds 轮
    (fusion.py:    (loop.py:1119, 本机 .env AGENT_MAX_ROUNDS=6)
     100-107)                        (engine.py:fuse，纯内存毫秒级)
                                                          (narrator.py:一次 LLM 调用，超时上限 60s)
```

**关键事实**：三个 Agent **已经是并行的**（`fusion.py:634-689` `asyncio.gather`），所以"并行化"不是可选项 —— 剩下的优化只能落在**每一轮的成本**和**轮数**上。

| # | 手段 | 依据 | 预期 | 风险 | 可验证方式 |
|---|---|---|---|---|---|
| S1 | **Narrator 与「结果写回 + SSE 完成事件」并发** | `fusion.py:406-410` 当前是 `append_message → commit → narrate` 串行，而 Narrator 需要的那次调用与前两者无依赖 | 省 1 次 LLM 往返（数秒） | 低 | 对比 `job.completed_at - started_at` 前后变化 |
| S2 | **精简 Agent system prompt** | `agents/prompts.py` 全文 23 KB，实测 system prompt 约 17.6 k 字符 ≈ **4029 真实 tokens**（`context_budget.py:26-32` 注释实测），而它**每一轮都要重发** | 每轮 prefill 明显下降；同时为工具结果腾出空间，减少 `prune_old_tool_exchanges`（`context_budget.py:199`）触发 | **中**（改 prompt 会改 Agent 行为，必须跑回归） | `agent_run.metrics` 的 `prompt_tokens` 分布 + 融合成功率 |
| S3 | **把常用查询模板写进 prompt**，减少 `row_count=0` 的无效轮次 | prompt 已有 `DATA_DICTIONARY`（`prompts.py:26-50`）在治这类病；可再补"性价比 GPU 检索模板" | 减少 1 轮无效 SQL 即省约 1/6 时长 | **中** | `agent_run.metrics.rounds_used` 的 p50/p95（`/api/admin/stats/agents` 已有该分布） |
| S4 | **下调 `AGENT_MAX_ROUNDS`**（本机 .env 现值 6） | `core/config.py:95` 默认 4；`loop.py:1119` 取 `min` | 直接封顶轮数 | **中高**（会增加 `round_limit_reached`，因为无终局工具时要留一轮做 schema finalize，见 `loop.py:1375-1438`） | 必须 A/B 实测 completed 率，不得凭感觉调 |
| S5 | **调整 agent-c 的模型**（当前与 agent-a 同为 Qwen3-8B） | `Client_Server_Deployment_Guide §3.4` 的 profile 表；agent-b 已是 4B | 可能显著缩短最慢 Agent | 低（部署层改动，不动代码） | 三端点 `max(agent_duration_ms)` |
| S6 | **前端感知速度补偿**：把 SSE 已有事件渲染成进度（排队 / 每个 Agent 的执行中 / 第几轮 / 工具名） | SSE 事件类型已很丰富（`fusion.py:164-348` 共发出 `model_loading / agent_started / model_ready / llm_started / llm_completed / tool_started / tool_completed / verification / fusion_completed`），且 `jobs.py:314-323` 已脱敏 | **零后端改动**，体感提升最大 | 低 | Playwright 断言事件按序出现 |
| S7 | 前端显示"预计剩余时间" | 可基于 `/api/admin/stats/jobs` 的 p50 与当前阶段 | 降低等待焦虑 | 低 | Vitest |

**明确不采用的"提速"手段**：

- **不放开串行闸门**：单张 GPU，放开只会让三个 Job 互相抢算力（`scheduler.py:9-15` 注释已说明设计意图）。
- **不加 worker**：违反部署指南 §12.4-1，会让闸门与任务表失效。
- **不做"先出 chat 初答再补融合"**：会引入两套结果语义，与"结果可信"的项目立意冲突。
- **不做缓存**：见 §2.4。

> **诚实声明**：上表所有"预期"都**不是实测数字**。本轮只承诺"每一步都有可验证的度量方式"，不承诺具体秒数。任何提速结论必须以 `/api/admin/stats/jobs` + `stats/agents` 的前后对比为准。

### 2.1.4 明确不做（呈现质量范围）

- 不改 `fusion-v1` 公式、不新增打分维度、不让 `model_confidence` 参与权重（`engine.py` 纪律）。
- 不让 Narrator 改写排名或引入 top_k 之外的候选。
- 不做多轮反问式推荐（"你更在意价格还是功耗？"）—— 属交互模式变更，另行立项。
- 不做图片/图表式推荐卡片。

---

## 2.2 目标二：用户数据系统与管理员界面

> 这是本轮**唯一的大工程**，也是部署指南 §10 第 1/2/3 项的正式立项。

### 2.2.1 现状（改造前的完整事实）

| 项 | 现状 | 位置 |
|---|---|---|
| 用户表 | 不存在。`models/__init__.py:12-25` 的 12 个导出里没有任何 user 类 | 全仓核实 |
| 鉴权 | 不存在。无中间件、无 `Depends`、无 `Authorization` 处理 | `main.py:50-61` 只有 CORS |
| 会话归属 | 不存在。list 无过滤，delete 无校验 | `api/conversations.py:38-53`、`:83-114` |
| Job 归属 | 不存在。`get/cancel/trace/events` 只要知道 UUID 就能访问 | `api/query.py:23/31/39/47` |
| 客户端"记忆" | `localStorage` 存 `rigbuilder.current_conversation_id` —— **这不是隔离机制** | `stores/conversations.ts:12,44`；部署指南 §7.4 已明确 |
| 已有约束持久化 | `UserConstraint` 表存在（按 `conversation_id`），是**会话约束**不是用户 | `models/user_constraint.py`、`context/service.py:222-233` |
| 速率限制 | 不存在 | 部署指南 §10-3 |

### 2.2.2 数据模型

**新增表 `user`（Alembic revision 0008）**：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `username` | varchar(64) unique not null | 登录名 |
| `display_name` | varchar(120) | 界面显示 |
| `password_hash` | varchar not null | 见下方哈希方案 |
| `role` | varchar(16) not null default `'user'` | `user` / `admin` |
| `status` | varchar(16) not null default `'active'` | `active` / `disabled` |
| `token_version` | integer not null default 0 | 递增即可强制该用户所有令牌失效 |
| `created_at` / `last_login_at` | timestamptz | |

**归属维度（同一 revision）**：

| 表 | 新增列 | 理由 |
|---|---|---|
| `conversation` | `owner_id UUID NULL FK(user.id)` | 会话归属的**唯一真源**；可空以兼容既有行 |
| `query_job` | `owner_id UUID NULL FK(user.id)` | 冗余：job 在 conversation 创建前就可提交（`jobs.py:54-59` 先建 job，`service.py:104-113` 后建/取会话），冗余可让鉴权不依赖 join，也便于 admin 统计 |

**密码哈希方案（不引入新依赖）**：

- 用标准库 `hashlib.pbkdf2_hmac("sha256", password, salt, 60_000)` + `secrets.token_bytes(16)` 盐，格式 `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`，校验用 `hmac.compare_digest`。
- 理由：项目至今只在明确获批时新增依赖（V3.6 §11「不新增 npm 依赖」、部署指南 §12.4-2 的配置纪律）。`passlib` / `bcrypt` 会带 C 扩展与打包负担；PBKDF2-HMAC-SHA256 是标准库、可单测、对本项目的威胁模型（受信局域网、课程演示）足够。
- **必须写入 Plan**：这是一个显式的安全取舍，若将来暴露到公网，应换成 Argon2/bcrypt 并加登录限速。

### 2.2.3 鉴权与令牌

**令牌方案：HMAC 签名 token（标准库实现，无状态）**

```text
payload = {"uid": "<uuid>", "role": "user|admin", "ver": <token_version>, "exp": <unix_ts>}
token   = base64url(payload_json) + "." + base64url(HMAC-SHA256(secret, payload_b64))
secret  = Settings.auth_secret_key（新增配置项，有安全默认值；未配置时启动生成并在日志打印一次提示）
```

- 校验：签名 + 未过期 + `ver` 与库中 `token_version` 一致（支持"强制下线"）。
- 有效期：建议 7 天（`Settings.auth_token_ttl_hours`）。
- 相比 JWT：不引依赖、不需要算法协商、`ver` 字段天然支持撤销。
- 相比服务端 session 表：少一次 DB 查询；代价是无法单令牌撤销（用 `token_version` 整体撤销，对本项目足够）。

**鉴权依赖（新增 `app/api/deps.py`）**：

| 依赖 | 行为 |
|---|---|
| `get_current_user` | 读 `Authorization: Bearer <token>` → 校验 → 查 user（status 必须 `active`）→ 返回 user；失败 401 + `WWW-Authenticate` |
| `require_admin` | 在 `get_current_user` 基础上要求 `role == "admin"`，否则 403 |

**保护范围（关键决策，必须写清楚）**：

| 端点组 | 要求 | 变更性质 |
|---|---|---|
| `/api/conversations/**` | 登录 | 行为变更：从"人人可见"到"只见自己的" |
| `/api/query/**`（jobs / cancel / trace / events） | 登录 + job 归属 | 行为变更 |
| `/api/chat`、`/api/agent/**` | 登录 | 行为变更 |
| `/api/admin/**` | **admin 角色** | 行为变更（当前无鉴权） |
| `/health`、`/health/database` | **保持公开** | **不得变更** —— `stack_status.ps1` 依赖它们（部署指南 §11.0） |
| **不做速率限制** | —— | 部署指南 §10-3 仍列为后续 |

### 2.2.4 隔离改造清单（逐文件，这是实施时的 checklist）

| 文件 | 位置 | 改动 |
|---|---|---|
| `models/user.py` | 新增 | `User` ORM |
| `models/__init__.py` | `:12-25` | 导出 `User` |
| `alembic/versions/2026xxxx_0008_create_users.py` | 新增 | 建 `user`；给 `conversation` / `query_job` 加 `owner_id`（nullable）+ 索引；写入 bootstrap admin |
| `app/api/deps.py` | 新增 | `get_current_user` / `require_admin` |
| `app/services/auth.py` | 新增 | 哈希、验签、建号、改密、登录 |
| `app/api/auth.py` | 新增 | `POST /api/auth/login`、`GET /api/auth/me`、`PATCH /api/auth/password` |
| `app/main.py` | `:57-61` | 注册 auth 路由 |
| `app/context/service.py` | `:104-113` `get_or_create_conversation` | 新增 `owner_id` 参数；指定 `conversation_id` 时校验归属（不匹配 → `ConversationNotFoundError`，**返回 404 而非 403，不泄露存在性**） |
| `app/context/service.py` | `:143-193` `list_conversations` | 加 `owner_id` 过滤；admin 可选看全部 |
| `app/context/service.py` | `:195-203` `soft_delete_conversation` | 加归属校验 |
| `app/api/conversations.py` | `:28/37/56/83` | 全部加 `Depends(get_current_user)` 并透传 owner |
| `app/api/query.py` | `:18/23/31/39/47` | 全部加鉴权；`:23-47` 的四个读取端点校验 job 归属 |
| `app/api/chat.py`、`app/api/agent.py` | 全文件 | 加鉴权并透传 owner |
| `app/services/jobs.py` | `:51-77 submit`、`:231-236 get`、`:238-245 events_after`、`:247-312 trace`、`:196-214 cancel` | 记录与校验 `owner_id` |
| `app/services/chat.py` / `services/fusion.py` | `chat.py:25`、`fusion.py:149` 与 `:660` | `get_or_create_conversation(..., owner_id=...)` |
| `app/core/config.py` | 新增项 | `auth_secret_key`、`auth_token_ttl_hours`、`bootstrap_admin_username`、`bootstrap_admin_password`（**全部给安全默认值**，遵守部署指南 §12.4-2） |
| `app/api/admin.py` | `:45-160` | 整组改为 `Depends(require_admin)`；**模块 docstring `:1-9` 的"只读"声明必须改写**（见 §2.2.5） |
| `app/api/admin.py` | 新增 | 用户管理写端点（见 §2.2.5） |

**不改的**：`agents/**`、`verification/**`、`fusion/**`、`tools/**`、`inference/**` —— 本轮**后端业务逻辑零改动**，只动 API 层与归属字段。

### 2.2.5 管理员用户管理（本轮新增的唯一写接口）

现状：`api/admin.py:1-9` 的 docstring 明确声明"每个端点只读，任何东西都不会改变推荐如何产生"，是 V3.5/V3.6 的一条纪律。**新增用户管理必须打破"admin 全只读"**，因此：

- 建议把用户管理**放在独立模块** `app/api/admin_users.py`（前缀 `/api/admin/users`），让 `admin.py` 继续保持纯只读，纪律不破。
- 端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/users` | 分页 + 搜索 + 状态/角色筛选 |
| POST | `/api/admin/users` | 建号（admin 设初始密码） |
| PATCH | `/api/admin/users/{id}` | 改 `display_name` / `role` / `status`；改密时递增 `token_version` |
| POST | `/api/admin/users/{id}/reset-password` | 重置密码并递增 `token_version`（强制下线） |
| GET | `/api/admin/users/{id}/activity` | 该用户的会话数 / 任务数 / 失败率（数据统计下钻） |

- **防呆（必须实现并有测试）**：不允许 admin 把**最后一个** admin 降级或禁用；不允许 `status=disabled` 的用户登录；被禁用用户的既有 token 立即失效（`token_version` + status 双重检查）。
- 用户管理只允许**禁用/软删**，不做物理删除 —— 否则会破坏 `conversation.owner_id` 外键与审计链（与 `conversations.py` 的 soft delete 一致）。

**数据统计**：现有 5 个 admin 只读端点**已能提供**总体统计（`stats/agents`、`stats/jobs`、`database`、`endpoints`、`summary`）。本轮只需在 `/admin` 页面上把它们**按用户维度加一层下钻**（通过新增的 `activity` 端点），不需要重写统计层。

### 2.2.6 前端设计

| 新增/修改 | 文件 | 说明 |
|---|---|---|
| Auth store | `src/stores/auth.ts`（新增） | token / user / role / login / logout / restore；token 存 localStorage |
| 请求注入 | `src/api/http.ts:5-11` | 请求拦截器加 `Authorization`；响应拦截器 401 → 清 token + 触发登录弹窗 |
| 登录弹窗 | `src/components/auth/LoginDialog.vue`（新增） | 模态；用户名 + 密码 + 错误行；`<dialog>` 或自绘（不引新 UI 库） |
| 主页面 | `src/views/ConversationTerminal.vue` | 保持为默认页；未登录可浏览，**提交/新建会话时触发登录弹窗** |
| 侧栏 | `src/components/AppSidebar.vue:15-20` NAV、`:82` footer | NAV 按角色过滤（普通用户不显示"运行统计"）；footer 显示当前用户 + 退出 |
| 路由守卫 | `src/router/index.ts:5-32` | 仅对 `/admin` 加 `beforeEnter`（需 admin）；**不**对 `/` 加守卫（保证"主页面仍是对话页"） |
| Admin 用户管理 | `src/views/AdminUsers.vue` + `src/components/admin/UserTable.vue`（新增） | 列表/建号/改角色/禁用/重置密码 |
| Admin 导航 | `src/views/AdminDashboard.vue` | 增加指向用户管理的入口 |

**布局分离**：`/admin` 与 `/admin/users` 已通过独立路由 + 独立布局与用户界面分离（`AdminDashboard.vue:101-102` 复用 `AppSidebar`，本轮如需要可换成独立 admin 侧栏）。

### 2.2.7 迁移与兼容（必须一次想清楚）

1. **迁移必须先于代码部署**：`0008` 加的是 nullable 列，对既有行安全。
2. **遗留会话归属**：`owner_id IS NULL` 的历史会话有两条出路 ——
   (a) 归给 bootstrap admin；
   (b) 保持 `NULL`，仅 admin 可见（普通用户列表天然看不到，因为过滤条件 `owner_id = me`）。
   **建议 (b)**：不改历史数据、不动审计链，且天然满足"隔离"。需要决策确认（§7-D3）。
3. **bootstrap admin 来源**：`Settings` 里的可选项；未配置时**不自动建号**（避免默认弱口令上线），改为启动时打印一条明确提示并要求管理员用一次性脚本建号。需要决策确认（§7-D4）。
4. **既有前端会立刻失效**：所有请求都会 401。因此 **§3 的 Batch D 与 Batch E 必须连续发布，中间态不可演示**。
5. **既有测试会立刻失效**：见 §5-1。

### 2.2.8 可行性判断

**可行，且是本轮四件事里最"确定"的一件**：

- 改动面**集中在 API 层 + `context/service.py` 的归属过滤 + 一个新表**；`agents/`、`verification/`、`fusion/`、`tools/`、`inference/` 一行不改，因此**推荐结果的可信性不受任何影响**。
- 不引入任何新依赖（PBKDF2 + HMAC 都是标准库）。
- 与部署形态无冲突：仍是单机单 worker，客户端只需浏览器；token 存 localStorage，形态 A 下不需要任何新 CORS 配置。
- 与既有纪律兼容：新增配置全部走 `Settings` 且有安全默认值（§12.4-2）；不动推理端口与脚本。

**风险集中在"回归面"而非"实现难度"**：既有 ~10 个 Vitest 文件与 ~24 个 Playwright 用例都假设匿名访问（§5-1），必须在同一批里补上登录 fixture。

---

## 2.3 目标三：前端美化与 API 文档

### 2.3.1 前端美化（在既有体系内做，不换框架）

约束（继承 `workbench-design-brief.md` 禁止项与 V3.6 §6）：

- 禁止：蓝紫渐变、发光光晕、3D/仪表盘/雷达类可视化、入场动效、大圆角卡片。
- 只用 `styles/terminal-tokens.css` 的既有 token；不新增全局 CSS 变量。
- 不引入 Element Plus 之外的新 UI 库；不引入在线字体。

本轮可做的美化（按收益排序）：

| # | 项目 | 说明 | 依赖 |
|---|---|---|---|
| B1 | **"最终结论"卡片置顶** | §2.1.2 的前端部分；这是美化与功能同一件事 | §2.1.2 |
| B2 | **运行进度可视化** | 把 SSE 事件渲染成阶段条（排队 → 各 Agent 执行中 → 校验 → 融合），复用 `AgentRunSummary.vue` | 零后端改动，§2.1.3 S6 |
| B3 | 空状态 / 首次使用引导 | `ConversationTerminal.vue:65-68` 目前只有两行文字 | 无 |
| B4 | 移动端细节 | `<760px` 侧栏横向条已有（`AppSidebar.vue:121-142`），可精修 | 无 |
| B5 | 登录弹窗视觉 | 与终端主题一致 | §2.2.6 |
| B6 | `AsciiLogoLarge.vue` 彩虹渐变合规化 | **存量违规**（简报禁蓝紫渐变），需 §7-D7 确认是否改 | 无 |

**不做的**：整站换皮、暗/亮主题切换（当前只有暗色终端风）、动效与转场、图表美化（echarts 主题已在 `chartTheme.ts` 收敛）。

### 2.3.2 API 文档（新增独立文档，不改旧文档）

现状：`docs/API_documents/` 最新是 `API_Documentation_V3.1.md`（**状态快照 2026-09-07**），且已经滞后 —— 它**没有** `/api/query/jobs`（2026-09-09 才出现的 `0007` 迁移）、没有 `/api/conversations` 列表、没有 `/api/admin/*`、没有 `natural_language` 字段。

**本轮要求**：

1. 新建 `docs/API_documents/API_Documentation_V4.md`（**不修改任何既有 API 文档**，与 V3.1 的定位纪律一致）。
2. 必须覆盖：`/api/auth/*`、鉴权要求与 401/403 语义、`/api/conversations/*`（含归属语义与 404 而非 403 的选择）、`/api/query/jobs/*`（含 **SSE 事件类型完整表**）、`/api/admin/*`（含新增用户管理写接口）、`natural_language` / `PresentationResult` 契约。
3. **写成可执行的纪律**：任何改动 API 的提交，必须同步更新本文档。这条要写进 V4 的完成定义，并由 code review 检查。
4. 同时在 `README.md` 的接口清单（`README.md:58-65`）补上 V4 的新端点。

---

## 2.4 目标四：缓存与高并发（明确不做）

**结论：本轮不做任何缓存，不做任何高并发设计。** 理由（用户已给出，这里补充技术依据）：

| 判断 | 依据 |
|---|---|
| 无并发压力 | 客户端-服务端形态是"1 台服务端 + 少量客户端"；fusion 本身严格串行（`scheduler.py:17`），队列天然存在 |
| 单 worker 是硬约束 | 部署指南 §12.4-1；加 worker 会让闸门与任务表失效，比缓存问题更优先 |
| 当前唯一"缓存"是 `Settings` 的 `lru_cache` | `core/config.py:127-129`；它的代价是"改配置必须重启"，已有明确运维规则（§12.2） |
| 课程要求 | 不要求部署到服务器，仅本机后端 + 一个前端客户端访问 |

**同时留出将来做缓存的入口条件**（写下来，避免以后盲做）：

- 出现"同一个问题被反复提交"且 `/api/admin/stats/jobs` 显示重复率明显 → 才考虑**查询级结果缓存**（按 release_key + 规范化问题 + 约束指纹做 key）。
- `/health/database` 与 admin 统计出现明显延迟（>`collect_*` 的 `elapsed_ms` 持续偏高）→ 才考虑**统计层缓存**。
- 只有在这两个条件出现时，才新开 Plan 讨论缓存失效策略（关键约束：**release_key 变化必须让所有缓存失效**，否则会返回旧 Release 的推荐 —— 这会直接破坏"结果可信"）。

---

# 3. 实施批次与顺序

原则：**每批独立可回退**；**任何一批都不能让"当前可演示的形态"长时间不可用**。

| 批次 | 内容 | 依赖 | 可独立验收 | 备注 |
|---|---|---|---|---|
| **A** | 前端"最终结论"区块 + SSE 进度可视化（S6） | 仅用现有 `natural_language` | ✅ | **零后端改动**，可立即见效 |
| **B** | 后端 `PresentationResult` + Narrator 升级 + 降级模板 + 单测 | A 之后对接 | ✅ | 新增字段，向后兼容（`natural_language` 保留） |
| **C** | 提速第一批：S1（narrate 与持久化并发）+ S2（prompt 精简）+ S3（查询模板） | B | ✅ | **必须先记录基线**（admin stats 的 p50/p95 + 成功率） |
| **D** | 用户系统后端：`user` 表 + 迁移 0008 + 鉴权 + 归属过滤 + 测试 | —— | ⚠️ 单独发布会让前端全 401 | **必须与 E 连续发布** |
| **E** | 用户系统前端：auth store + 登录弹窗 + 401 拦截 + 侧栏用户区 + 测试 fixture | D | ✅ | 与 D 同批发布 |
| **F** | 管理员用户管理页 + `admin_users.py` + `/admin` 鉴权 | D/E | ✅ | 同时改写 `api/admin.py` docstring |
| **G** | 前端美化（B3/B4/B6 等）+ `API_Documentation_V4.md` + README 更新 | 全部 | ✅ | 文档可与任意批次并行推进 |

**实施顺序上的一处取舍**：把 A 放最前，是因为它零风险、零依赖、立刻改善最被用户诟病的"看不到结论"问题；把 D 与 E 绑定，是因为中间态（后端已鉴权、前端未带 token）会让整个界面不可用。

---

# 4. 验收清单

## 4.1 呈现质量

- [ ] `top_k` 非空时，响应含 `PresentationResult`，且 `primary.candidate_id ∈ top_k`、`primary.name == 该候选 canonical_name`
- [ ] `headline` / `reasons` 中的每个数字都能在允许集合中找到（新增单测覆盖 `_numbers_are_faithful` 的扩展）
- [ ] Narrator 不可用 / 超时 / 输出不合规时，**仍然返回结构完整的确定性结论**（含主推 + 理由），不返回只有分数
- [ ] `top_k` 为空时 `headline` 明说无候选通过验证，`primary` 为 `null`
- [ ] `agent_coverage < 1` 时 `caveats` 明示覆盖不足
- [ ] 前端"最终结论"在候选列表之上；候选列表默认折叠但仍可展开查看 score/credibility/FactsTable/evidence/trace
- [ ] `top_k.length === 1` 的渲染与改造前**行为等价**（既有 `RecommendationBlock.spec.ts` 断言不得修改）

## 4.2 提速（每一行都必须有前后对比数据）

- [ ] 记录改造前基线：`/api/admin/stats/jobs` 的 `duration_ms.p50/p95`、`by_status`、`fusion_with_zero_coverage`、`/api/admin/stats/agents` 的 `rounds.p50/p95`
- [ ] S1 实施后，Narrator 不再串在 commit 之后（可用一次 job 的耗时分布对比说明）
- [ ] S2/S3 实施后，`prompt_tokens` 与 `rounds_used` 的分布有可测量的变化
- [ ] 融合成功率（`completed / fusion_jobs`）**不得下降**
- [ ] 前端：SSE 事件按序渲染（Playwright 断言 `queued → agent_started → ... → job_completed`）

## 4.3 用户系统

- [ ] `alembic upgrade head` 在既有库上成功，且既有 `conversation` / `query_job` 行数不变
- [ ] 未带 token 访问 `/api/conversations` → 401；带 user token 访问 `/api/admin/summary` → 403
- [ ] 用户 A 登录后，`GET /api/conversations` 只返回 A 的会话（B 的会话不可见）
- [ ] 用户 A 用 B 的 `conversation_id` 调 `GET /api/conversations/{id}` → **404**（不是 403，不泄露存在性）
- [ ] 用户 A 用 B 的 `job_id` 调 `GET /api/query/jobs/{id}`、`/trace`、`/events`、`/cancel` → 404
- [ ] `DELETE /api/conversations/{id}` 对非本人会话 → 404
- [ ] `/health` 与 `/health/database` **无需 token** 即可访问（`stack_status.ps1` 必须继续通过）
- [ ] 被 `status=disabled` 的用户：登录失败；**既有 token 立即失效**
- [ ] admin 不能把自己/最后一个 admin 降级或禁用（有测试）
- [ ] 重置密码后，该用户旧 token 失效（`token_version` 生效）
- [ ] 密码在库中以 PBKDF2 格式存储，**任何日志/响应都不出现明文或哈希**
- [ ] `pytest` 全绿且不少于 268 项；新增 `tests/test_auth.py`、`tests/test_conversation_isolation.py`

## 4.4 前端与文档

- [ ] 登录弹窗可从主页面触发；成功后停留/回到原会话；失败有可见错误
- [ ] 普通用户侧栏不出现"运行统计"；admin 用户可见
- [ ] `vue-tsc --noEmit` 通过；`vite build` 通过；首屏 JS **不超过 183.86 kB raw**（V3.6 实测基线）
- [ ] Vitest 全绿且不少于既有项数；Playwright 全绿且不少于既有项数（**含新增登录 fixture**）
- [ ] `docs/API_documents/API_Documentation_V4.md` 覆盖全部端点与鉴权语义；`README.md:58-65` 接口清单已更新
- [ ] `docs/Plan_documents/**` 既有 16 个文件与 `docs/frontend_design/**` 既有文件**一字未改**

## 4.5 不回归

- [ ] `git diff --stat -- backend/app/agents backend/app/verification backend/app/fusion backend/app/tools backend/app/inference` 为空（业务逻辑零改动）
- [ ] `fusion-v1` 公式与排序键未变（`engine.py:112-118`、`:281-288` 逐项一致）
- [ ] 部署指南的 6 条硬约束全部保持（单 worker、推理只绑回环、NO_PROXY、配置走 Settings、脚本 ASCII、不硬编码本机 IP）
- [ ] 三个运维脚本仍可用，`stack_status.ps1` 退出码 0

---

# 5. 回归风险与防线

| # | 风险 | 防线 |
|---|---|---|
| 1 | **既有前端测试与 e2e 全部假设匿名访问**，加鉴权后全红 | 在同一批（D+E）里提供测试 fixture：Vitest 侧 mock `http` 拦截器注入假 token；Playwright 侧用 `addInitScript` 预置 `localStorage` token，或起一个测试专用账号。**不得为通过而放宽既有断言** |
| 2 | **`api/admin.py` 从"只读无鉴权"变成"需 admin"**，可能影响 `stack_status.ps1` 或文档 | 先确认脚本只依赖 `/health` 与 `/health/database`（已核实）；用户管理放独立模块，保持 admin 统计模块只读 |
| 3 | 归属过滤写漏某个读路径 → 越权读 | 以上 §4.3 的清单逐条测；**统一在 `context/service.py` 里过滤**，不在 API 层各写一遍 |
| 4 | 迁移给既有行填错 owner | 新列 nullable、**不回填**（§2.2.7-2 建议）；迁移前后 `query_job` / `agent_run` 行数必须不变 |
| 5 | Narrator 升级破坏"不得改写排名"的纪律 | 扩展 `_numbers_are_faithful` + 新增 `primary ∈ top_k` 断言；`tests/test_narrator_faithfulness.py` 只增不减 |
| 6 | 精简 prompt（S2）导致 Agent 成功率下降 | 必须 A/B：同一组问题在改动前后各跑一遍，比对 `completed` 率与 `rounds_used`；**成功率下降即回退** |
| 7 | 下调 `AGENT_MAX_ROUNDS`（S4）导致 `round_limit_reached` 上升 | 无终局工具时需留一轮做 schema finalize（`loop.py:1375-1438`），因此**不得低于"1 轮查询 + 1 轮收尾"**；必须实测 |
| 8 | `auth_secret_key` 使用默认值上线 | 启动时若为默认值 → 打印显著警告；文档写明生产必须覆盖 |
| 9 | 首屏体积因 auth 层增长 | 登录弹窗与 auth store 必须进首屏（不能懒加载），因此要控制体积；以 `e2e/admin-dashboard.spec.ts` 的 bundle 守卫为回归线 |
| 10 | token 存 localStorage 的 XSS 暴露面 | 本项目不渲染用户 HTML（无 `v-html`），风险低；若将来引入富文本必须重新评估 |

---

# 6. 明确不做（V4 范围）

- **不做任何缓存**（§2.4）
- **不做速率限制、不做防爆破**（部署指南 §10-3 仍为后续）
- **不做跨进程锁、不做多 worker、不做 Redis**
- **不做自助注册**（建议由管理员建号；见 §7-D2）
- **不做第三方登录 / OAuth / SSO / 邮箱验证 / 找回密码**
- **不改 `agents/` `verification/` `fusion/` `tools/` `inference/` 的任何业务逻辑**
- **不改 `fusion-v1` 公式、排序键、淘汰规则**
- **不修改既有测试文件的断言**
- **不修改既有 Plan 文档、API 文档、frontend_design 文档**
- 不做用户头像 / 个人资料页 / 消息通知
- 不做部署形态变更（仍是单机单 worker，客户端只需浏览器）

---

# 7. 待决策事项

| # | 事项 | 选项 | 我的建议 |
|---|---|---|---|
| D1 | 登录弹窗的触发时机 | (a) 打开页面即弹出并遮挡 (b) 主页面可见，提交/新建会话时才弹出 (c) 打开页面即弹，但可关闭并浏览 | **(b)** 或 (c)：用户明确说"当前对话页是主页面，登录页以弹窗形式出现，登陆后提供服务"，(b)/(c) 最贴合 |
| D2 | 是否开放自助注册 | (a) 开放 (b) 关闭，仅 admin 建号 | **(b)**：与"管理员界面含用户管理"一致，也避免任何人注册后占用 GPU |
| D3 | 遗留会话（`owner_id IS NULL`）归属 | (a) 回填给 bootstrap admin (b) 保持 NULL，仅 admin 可见 | **(b)**：不改历史数据、不动审计链 |
| D4 | 初版 admin 账号来源 | (a) `.env` 配置，迁移时写入 (b) 一次性脚本手动创建 (c) 首次启动自动生成随机密码并打印一次 | **(b) 或 (c)**：避免默认弱口令；(a) 最方便但要求运维改 `.env`（且改完必须重启，见部署指南 §12.2） |
| D5 | 令牌有效期 | 1 天 / 7 天 / 30 天 | **7 天**，并提供"重置密码即全端下线"（`token_version`） |
| D6 | §12-1 遗留：结构化约束编辑器（field/price/compatibility/runtime 的增改删）是否本轮实施 | (a) 实施 (b) 继续挂起 | **建议本轮做**：它是"呈现质量"的另一半（用户说"不能僵硬地按排名返回"往往正是因为约束没进得去），且后端规则与测试都已在位（V3.6 §12-1 已核实），属于"接出一条已实现但界面到不了的路径" |
| D7 | §12-4 遗留：约 47 KB 死代码是否删除 | (a) 删除 (b) 保留 | **建议删除**（`ConstraintEditor.vue` 除外，它是约束实现的唯一参考）；删除前对每个文件名做全目录 grep + `vue-tsc` |
| D8 | `AsciiLogoLarge.vue` 的彩虹渐变是否合规化 | (a) 改为终端 token 单色/双色 (b) 保持 | 建议 (a)：它是设计简报禁止项的存量违规 |
| D9 | 是否把 agent-c 换成更小模型以提速 | (a) 换 (b) 保持两 8B + 一 4B | 需要先看 `/api/admin/stats/agents` 里三个 profile 的 `agent_duration_ms` 分布，再决定；属部署层改动，不影响代码 |
| D10 | 加速是否允许调整 `AGENT_MAX_ROUNDS` | (a) 允许，需实测 (b) 不动 | 建议 (a) 但**必须 A/B 实测 completed 率**，不得凭感觉调 |

---

# 8. 与既有文档/纪律的关系

| 文档 | 关系 |
|---|---|
| `Plan_V3.6.md` | 直接继承。其 §12-1（约束编辑器）、§12-4（死代码）在本轮 §7-D6/D7 一并处理 |
| `Client_Server_Deployment_Guide_V1.md` | **本轮实施其 §10 第 1/2/3 项**；其 §1.1 端口表、§7 并发语义、§8 安全边界、§12 配置协议在本轮全部继续有效。**§8「当前版本没有任何鉴权」将在本轮之后失效，该文档需要补一条"V4 起已具备鉴权"的说明 —— 按文档纪律，应新建 V2 而不是改写本文** |
| `API_Documentation_V3.1.md` | 已滞后（缺 query jobs / conversations 列表 / admin / natural_language）。**不改写**，新建 V4 |
| `frontend_V2.md` | 前端本轮改造完成后需要新增 `frontend_V3.md`（同样不改写 V2） |
| `Agent_Loop_Contract_V3.3.md` | 本轮不改 Agent 契约；若 S2/S3 改了 prompt，需在该契约文档中新增 V3.4 章节记录 prompt 变更 |
| `data/v3/README.md` | 数据侧本轮零改动，现有 accepted release 继续使用 |

---

**文档维护**：本文为 V4 计划书。实施过程中的偏差与结论请追加到本文的「实施结果」小节；若架构方向再次变化，请新建 `Plan_V5.md`，不要改写本文。

---

# 实施结果（2026-09-14，由 Plan_V4.1 执行）

> 本节执行后追加，**上方既有内容一字未改**。
> 三份留痕报告： [问题诊断](../reports/2026-09-14_pipeline_diagnosis.md)、[融合提速实测](../reports/2026-09-14_fusion_speedup_measurements.md)、[组合配置可行性](../reports/2026-09-14_bundle_recommendation_feasibility.md)。

## 与本文计划的偏差（均有依据，非简化）

| 计划项 | 实际处置 | 原因 |
|---|---|---|
| 独立的 `PresentationResult` 契约 | 已实现；并把 `NaturalLanguage` 改为它的**超集**（`overview`/`per_candidate` 语义不变） | `test_narrator*.py` 与 `e2e/conversation-terminal.spec.ts` 都断言 `overview`，继承是唯一零破坏的路径 |
| `FusionRunResponse.presentation` 字段 | 已加（由同一份 narration 投影，非第二份数据） | 按计划 |
| 候选列表「下移为明细并默认折叠」 | **改为「结论置顶 + 列表保持展开」** | `RecommendationBlock.spec.ts:104-123` 断言 rank 1 的 metrics 默认展开；折叠会破坏 4 条既有断言，而用户核心诉求（结论优先）已满足 |
| Narrator 与写库并发 | **未做** | 写库毫秒级 vs 叙事秒级，串行成本≈0；真并发还需跨线程 session |
| 提示词精简 | **未做** | 每条规则都有实测依据，且本轮无 A/B 实测条件；单 Agent 成功率已只有 36%–51% |
| 阶段进度条 | **未做**，改为显示「本次用了几个 Agent」 | 既有 UI 已显示每 Agent 状态/SQL/耗时与总计时，进度条是同义重复；Agent 数才解释耗时差异 |
| `resolve_query_mode` 语义 | 保持合取（domain ∧ decision），并扩充词表 | 避免把「你好」这类消息升为 86 秒的融合 |

## 未列入本文计划、但已一并修复的缺陷

1. **界面把快速问答标成 "Verified fusion complete"** —— `AgentRunSummary.vue:61` 是硬编码文案，而 chat 的 `llm_completed` 事件带 `model` 字段，于是渲染出 `A / agent-a / pending` 的假 Agent 行。这正是用户贴出的那段记录。已按真实 `resolved_mode` 分支，chat 改为 `Fast answer · unverified` 并加未经验证说明。
2. **Narrator 降级率 97.3%（71/73）** —— 三个叠加原因：候选名**子串误判**（`"RTX 4060"` 是 `"RTX 4060 Ti"` 的前缀，本库 GPU 家族恰全是这种命名）、`per_candidate` 要求 id 序列完全一致、输出上限偏小。已全部修复，详见提速报告 §4.3。
3. **`1080p` / `144Hz` 被当成待验证的产品事实** → 造成无谓降级。已加 `_NON_FACT_NUMBER` 区分「需求表述」与「产品断言」。
4. **`"PCIe 4.0"` 与存储值 `4` 被判为不同值** → 已统一为等价值。
5. **测试脚本可用性**：PowerShell here-string 向 `python -c` 传中文会破坏字面量（实测 SyntaxError），采集脚本改用 `\u` 转义。已记入提速报告。

## 门禁（全部实测）

| 项目 | 基线（本文 §1.4/§9） | 实施后 |
|---|---|---|
| 后端 pytest | 266 passed, 2 skipped | **300 passed, 2 skipped** |
| 前端 Vitest | 12 文件 / 106 项 | **14 文件 / 120 项** |
| Playwright（desktop+mobile） | 24 项 | **24 项**（无回归） |
| Playwright（真机 `test:live`） | 1 项 | 未跑（需 llama-server，本轮环境不具备） |
| `vue-tsc --noEmit` | 通过 | 通过 |
| `vite build` | 通过 | 通过（2312 modules） |
| 首屏 JS | 200.60 kB | **203.74 kB**（验收线 ≤ 273.59 kB） |
| 首屏 CSS | 36.07 kB | 38.77 kB |

## 未实测的部分（诚实记录）

- **融合耗时的改动前后对比**：需要 `llama-server ×3` + GPU，本轮环境中推理端点未启动，因此**不给出任何秒数承诺**。验证步骤见提速报告 §5。
- **Narrator 修复后的实际降级率**：需要重跑真实融合才能统计。
- **组合配置推荐**：按用户决定只出可行性报告，未实现。
- **兼容性/运行时约束的可用性提示**（可行性报告 §4 的陷阱）：已识别，**未实施**，建议下一轮做。

---

# 实施结果（2026-09-14，由 Plan_V4.2 执行）

> 本节执行后追加，**上方既有内容一字未改**。
> 本轮需求原文：把推荐从「单品」扩展为「核心配置」——核心硬件给具体型号并保持可验证，配套硬件只说明规格；同时把融合耗时从 77-86 秒压向 30 秒。
> 用户澄清的四点：① 定义为「核心配置」而非「装机」推荐，核心硬件具体、配套说明规格即可；② 部件级共识 + 后端组装；③ 压到 30 秒内；④ 定位是「受控的 SQL agent」（无 shell，只有查库工具）。

## 一、本次实测输出（逐项判读）

实测提示词：`我要上大学了，给我推荐一套配置吧，我想要新一点的`

### 1.1 生效的部分

| 观察项 | 实测结果 | 判定 |
|---|---|---|
| 时间偏好 | 推 `GeForce RTX 5090` / `5080` / `Radeon RX 9070 XT`，全部 2025 年 | ✅ **生效**。同一提示词改动前推的是 2021 年的 RX 6700 XT |
| 核心配置区块 | 界面上出现「核心配置」区；显卡为具体型号；电源为 `ATX 850W` 并带「规则推导」标签 | ✅ **`core_build` 端到端打通**（后端推导 → `PresentationResult` → `CoreBuildPanel`） |
| 电源推导 | 575W（核心部件功耗合计）+ 120W 余量 → 850W 档位 | ✅ 与组装器单测一致；真实库另一次独立验证为 420W → 550W |
| 覆盖率分母 | `coverage 1/3` | ✅ **修复生效**。旧实现在 2-Agent 场景会硬编码显示 `3/3` |
| 诚实标注 | 平台/内存/存储标「未收录」并给出原因；散热与机箱进 `gaps` | ✅ 未编造任何部件 |

### 1.2 未达成的部分（本轮关键结论）

| 问题 | 实测表现 | 严重度 |
|---|---|---|
| **Agent 成功率崩塌** | agent-b、agent-c **同时 failed**，`coverage 1/3` | 🔴 最严重 |
| **证据字段错乱** | 三个候选的 claim 全部落在 `entity.canonical_name`，值却显示成 `GeForce RTX 5090 GIB` / `... W` | 🔴 |
| **可信度失效** | 第一名 credibility **15**，`fact_support 0.00`、`proof_coverage 0.00` | 🔴 |
| **缺 CPU 候选** | prompt 已明确要求「同时提交 CPU 与 GPU」，实际只有显卡 | 🟠 |
| **推荐与场景不匹配** | 「上大学」+「新一点」→ 33500 元的 5090，无价格感知 | 🟠 |
| **headline 措辞** | 「这套配置以 GeForce RTX 5090 为核心」——只有一个核心部件却说「配置」 | 🟡 |

### 1.3 失败根因（取自 `public.agent_run`，非推断）

历史记录中的失败码全部指向**预算与上下文**，而不是数据库或验证逻辑：

| error_code | 现场指标 | 含义 |
|---|---|---|
| `context_budget_exceeded` | `llm_calls 4` / `rounds_used 4` / `total_tokens 49231` / `context_pruned_messages 2` | 4 轮烧掉 49k tokens，触发消息修剪后仍超预算 |
| `tool_protocol_error` | `rounds_used 1` / `llm_calls 0` / `tool_calls 0` | 第一轮就没能产出合法工具调用 |
| `router_unavailable` | `llm_calls 0` | 端点不可达（属旧的绑定问题，已修） |

同时取到 **`prompt_eval_duration_ms: 11786`** —— 单次 prefill 约 **11.8 秒**；而一次正常运行的 `llm_calls` 是 **7**。这是耗时量级的主要来源。

> 说明：本次运行（agent-a 3 SQL complete / agent-b 4 SQL failed / agent-c 5 SQL failed）的 `agent_run` 行与其错误码未能与界面逐一对上，因此上表列的是**同一失败模式下已取证的记录**；本轮的 Agent 失败原因**尚未拿到运行时错误码的直接证据**，这一点如实记录。

## 二、本轮已完成的工作

| # | 文件 | 改动 |
|---|---|---|
| 1 | `tools/tables.py` | `EVIDENCE_FIELD_BY_COLUMN`（列名单键）→ `EVIDENCE_FIELD_BY_VIEW_COLUMN`（视图+列复合键），新增 `evidence_fields_for_columns()` |
| 2 | `tools/database.py` | 三处调用点改为视图感知；`DEFAULT_EVIDENCE_FIELDS` 补 5 个 `cpu.*` 字段；`resolve_evidence` 默认视图改为覆盖面最广的 `component_profile_catalog` |
| 3 | `tools/registry.py` | `resolve_evidence` 的 `view` 说明改为跨品类 |
| 4 | `agents/loop.py` | legacy 路径默认视图对齐为 `component_profile_catalog` |
| 5 | `agents/prompts.py` | 新增「新度必须落到 `release_date`」硬规则；`LIMIT` 无 `ORDER BY` 从「禁止排序」改为「禁止裸 LIMIT」；`## Whole-machine requests` 改写为**核心配置**规则；补强 GPU 世代匹配（protocol 模板此前缺这条，正是 `architecture LIKE '%RTX%'` 的来源） |
| 6 | `services/build_assembler.py` | **新增**：平台/内存/电源/存储在数据库上确定性推导，每项带 `basis` 标注 |
| 7 | `services/fusion.py` / `services/narrator.py` | 融合后调用组装器并注入；降级模板也输出配置单；LLM brief 携带 `core_build` 且被告知不得改写 |
| 8 | `schemas/fusion.py` | 新增 `CoreBuild` / `CoreComponent` / `SupportingSpec` / `SupportBasis`；`PresentationResult.core_build`（加在父类，`NaturalLanguage` 子类自动继承） |
| 9 | `backend/.env` | `AGENT_MAX_ROUNDS` 6→**5**，`AGENT_MAX_SQL_CALLS` 6→**4** |
| 10 | 前端 | 新增 `CoreBuildPanel.vue`；`RecommendationBlock` 接入配置单 + 补齐 `natural_language` 回退路径 + 修覆盖率分母；`agentFusion.ts` / `conversations.ts` 补类型 |

**门禁**：后端 **322 passed / 2 skipped**（新增组装器 12、证据映射 7、prompt 规则 4）；前端 **73 passed**（新增配置单 6、结果区 3）；`vue-tsc --noEmit` 通过。

**测试当场抓出的 5 个缺陷**（含本轮自己引入的）：

1. `EVIDENCE_FIELD_BY_COLUMN` 把 CPU 的 `architecture` 认证成 `gpu.architecture`；
2. `DEFAULT_EVIDENCE_FIELDS` 全是 GPU 字段 → **CPU 实体根本拿不到可引用的证据**，只能退回 `canonical_name`；
3. `resolve_evidence` 默认视图只覆盖 GPU → 整机场景要多烧一轮；
4. 前端 `coverageText` 硬编码 `Math.round(c * 3)` → 2-Agent 场景显示 `3/3`；
5. 本轮我自己给 `pcie_generation` / `pcie_lanes` / `ecc_support` 加了映射，但 **`gpu_catalog` 并不暴露这三列**（`fact_evidence` 里有、视图里没有）——被新增的守卫测试当场抓住。

## 三、不足与教训（诚实记录）

### 3.1 速度与任务复杂度被同时加压，方向自相矛盾
本轮一边**收紧预算**（轮数 6→5、SQL 6→4），一边**扩大任务**（要求一次提交同时给出 CPU 与 GPU，还要多做一次跨品类 `resolve_evidence`）。已取证的正常运行需要 **6 轮**，被砍到 5 轮后 Agent 没有余量收官。**agent-b、agent-c 同时失败，属于这一决策的直接后果。**

### 3.2 prompt 扩充与提速目标冲突
为修「推荐旧卡」加入了大量规则，但实测 `prompt_eval_duration_ms ≈ 11.8s`、`total_tokens 49k/4 轮`。**prompt 越长，prefill 越贵、越容易 `context_budget_exceeded`。** 本文 §2.1.3 写的「精简 system prompt」这一条，本轮不但没做，反而反向操作了。

### 3.3 30 秒目标没有任何实测支持
`llm_calls 7` × prefill 11.8s 已是 80 秒量级的来源。**在 prefill 不降下来的前提下，30 秒不可能达成**，本轮对此没有有效动作。

### 3.4 证据字段错乱未定位到机制
三个候选的 claim 全部落到 `entity.canonical_name`，值却带着 `GIB` / `W` 单位后缀。这说明模型构造 claim 时把字段名与值弄混，但本轮**没有查到机制层面**（是 `resolve_evidence` 的返回结构引人误解，还是小模型在双品类任务下能力不足），因此**没有修复**。

### 3.5 产品定义只完成了一半
后端推导链是通的（三条规则 + 真实库验证），但**核心硬件只出了显卡、没有 CPU**；而平台与内存的推导**依赖 CPU 的 socket**，缺 CPU 直接导致平台/内存双双「未收录」。这不是组装器的缺陷，是 Agent 侧没有产出 CPU 候选。

### 3.6 缺 CPU 时没有可执行的降级
当前无 CPU 就让平台与内存并列显示两个「未收录」。对用户更合理的是给出一句可执行的下一步（「先确定 CPU 才能推荐平台与内存」），而不是两个并列的未收录。

## 四、下一轮优先级建议（先止损，再优化）

1. ✅ **已回退预算（同日执行）**：`AGENT_MAX_ROUNDS` 与 `AGENT_MAX_SQL_CALLS` 均已回到 **6**，`.env` 注释里记下了这次收紧→回退的实测依据。**成功率恢复之前，任何提速数字都无意义。**
2. **查清 claim 字段错乱**：这是可信度从 76 掉到 15 的直接原因，比提速更触及项目立身之本（真值检验）。
3. **prompt 瘦身**：同一语义只保留一处，目标把 prefill 从 11.8s 降到数秒级——**这是 30 秒目标唯一可行的路径**。
4. **强制产出 CPU 候选**：若模型反复给不出 CPU，考虑由后端在 `cpu_catalog` 内做一次确定性补选（与 `build_assembler` 推导同源），而不是靠 prompt 说服模型。
5. **价格感知**：「上大学」这类场景需要 soft price band；当前无约束时 `preference_utility` 固定 0.5，等于没有价格维度。

---

# 实施结果（2026-09-14 第二轮 · 预算回退后实测）

> 本节延续上一节的同一个 workstream（Plan_V4.2）。**上方内容一字未改**，只在此追加。
> 本节的目标是让**未参与本轮的 agent 仅通过阅读即可完整接续工作**，因此改动清单写到「文件 + 函数 + 原因」粒度。

## 六、回退动作与受控对照

### 6.1 回退动作

| 参数 | 第一轮 | 第二轮 | 位置 |
|---|---|---|---|
| `AGENT_MAX_ROUNDS` | 5 | **6** | `backend/.env` |
| `AGENT_MAX_SQL_CALLS` | 4 | **6** | `backend/.env` |

回退后**必须重启后端进程**才生效：`app/core/config.py` 的 `Settings` 带 `lru_cache`，改 `.env` 不重启等于没改。本轮实测确认端口 8000 的 PID 已由 13248 变为新值。

停止旧进程时**不要用 `stop_local_stack.ps1`**，它在 `stop_local_stack.ps1:94` 调用 `Get-CimInstance Win32_Process`，本机 WMI 会挂死；改用：

```powershell
$line = netstat -ano | Select-String ':8000\s' | Select-String 'LISTENING' | Select-Object -First 1
if ($line) { taskkill /F /PID ($line.Line -split '\s+')[-1] }
```

### 6.2 两轮对比：回退恢复了什么

同一提示词（`我要上大学了，给我推荐一套配置吧，我想要新一点的`），**唯一变量是预算**，构成一组受控对照：

| 观察项 | 第一轮（5 / 4） | 第二轮（6 / 6） | 判定 |
|---|---|---|---|
| `agent_coverage` | **1/3** | **3/3** | ✅ 成功率完全恢复 |
| 有贡献的 Agent | 仅 agent-a | agent-a / agent-b / agent-c | ✅ |
| **CPU 候选** | **无** | **Ryzen 5 7600**（agent-b）、**Ryzen 7 7700X**（agent-c） | ✅ |
| 配置单·平台 | 未收录 | **Socket AM5**（规则推导） | ✅ |
| 配置单·内存 | 未收录 | **DDR5**，三档可选 | ✅ |
| 配置单·电源 | ATX 850W（575W） | ATX 850W（640W，已含 CPU 功耗） | ✅ |
| headline | 「以 RTX 5090 为核心」（单件） | 「以 GeForce RTX 5090、Ryzen 5 7600 为核心」 | ✅ 措辞正确 |
| 5090 可信度 | 15 | 20 | 仍不合格 |

**由这组对照可确证的两条结论**（这是本轮最有价值的信息）：

1. **「CPU 候选缺失」不是 prompt 没写清楚，而是预算不足**。prompt 早已要求「submit BOTH a CPU candidate AND a GPU candidate」，但 5/4 的预算下 Agent 来不及查第二类目；恢复到 6/6 后 CPU 候选自然出现。
2. **平台与内存的「未收录」是 CPU 缺失的下游后果，不是组装器的缺陷**。组装器的推导链本身早已在真实库上验证通过；一旦拿到 CPU 的 `socket`，平台与内存立即推导成功。

**教训（应写入未来的调参纪律）**：`AGENT_MAX_ROUNDS` / `AGENT_MAX_SQL_CALLS` 与「一次提交要产出几个品类」**强耦合**。扩大任务量时必须同步放宽预算，两者不可分别独立优化。

## 七、仍未解决：claim 的 `field_key` 系统性错乱

### 7.1 现象（第二轮，与第一轮一致）

| 候选 | claim 的 `field_key` | claim 的值 | 验证状态 |
|---|---|---|---|
| GeForce RTX 5090 | `entity.canonical_name` | `GeForce RTX 5090 GB` | PARTIAL |
| GeForce RTX 5090 | `entity.canonical_name` | `GeForce RTX 5090 W` | PARTIAL |
| GeForce RTX 5090 | `entity.canonical_name` | `GeForce RTX 5090` | CONFLICT |
| GeForce RTX 5090 | `entity.canonical_name` | `GeForce RTX 5090` | CONFLICT |
| Ryzen 7 7700X | `entity.canonical_name` | `Ryzen 7 7700X MHz` | PARTIAL |
| Ryzen 7 7700X | `entity.canonical_name` | `Ryzen 7 7700X cores` | PARTIAL |
| Ryzen 7 7700X | `entity.canonical_name` | `Ryzen 7 7700X threads` | PARTIAL |
| Ryzen 7 7700X | `entity.canonical_name` | `Ryzen 7 7700X` | CONFLICT |
| **Ryzen 5 7600** | `entity.canonical_name` | **`Ryzen 5 7600`**（纯名称，无后缀） | **VERIFIED** ✅ |

### 7.2 这组数据本身就是诊断

- `entity.canonical_name` **是合法字段**（存在于 `truth.field_definition`），所以「字段名本身无效」不是问题；
- 三条 **CONFLICT** 的共同点是：值 = **纯产品名**，而该实体的 `canonical_name` 真值**不是**这个字符串（例如库里可能是带后缀的规范名）；
- 四条 **PARTIAL** 的共同点是：值 = **产品名 + 单位后缀**（`GB` / `W` / `MHz` / `cores` / `threads`）；
- **唯一 VERIFIED 的那条，值恰好是纯名称且与真值一致**。

**因此根因是**：模型想引用「显存 32GB」「功耗 575W」「主频 3.8GHz」这类字段，却把 `field_key` **统一写成最顺手、最常出现的 `entity.canonical_name`**，同时把「名称 + 单位」塞进了 `value`。它没有把字段名与值正确配对。

### 7.3 机制推断：`resolve_evidence` 的 `canonical_name` 回退可能在教坏模型

`app/tools/database.py` 的 `resolve_evidence` 末尾有一段「请求的字段一个都没匹配上时，回退成只返回 `canonical_name`」的逻辑：

```python
entries = [entry for entry in grouped.get(entity_id, []) if entry["field_key"] in wanted]
if not entries and wanted:
    entries = [
        entry for entry in grouped.get(entity_id, [])
        if entry["field_key"] == "entity.canonical_name"
    ]
```

这行的本意是「至少给模型一点东西，别给空」，但对小模型而言，它**会强化「canonical_name 就是这个实体唯一/默认可引用的字段」这一印象**。当「逐字照抄 `field_key`」这件事本身对 8B 模型有难度时，`canonical_name` 就成了它唯一记住的那个。

**支持这一推断的第二条证据**：所有失败 claim 的 `evidence` 折叠区都显示 **`0 proof`**，即模型**根本没有复制 `evidence_id`**——claim 是凭记忆构造的，而不是从 `observation.evidence` 抄的。prompt 里虽然明确写了「copy field_key, value, unit and evidence_id from observation.evidence」，**小模型没有执行这条指令**。

> 这部分**仅为推断，尚无运行时证据**。要确证需要：在一次真实运行中导出某个 Agent 的 `resolve_evidence` 原始返回与它随后的 `submit_recommendation` 参数，做逐条比对。当前系统没有记录这两者的对应关系，这也是 §9 建议补的观测点。

## 八、新暴露的产品问题：配置单内部不协调

第二轮推荐的是 **`GeForce RTX 5090` + `Ryzen 5 7600`**——一块 33500 元的旗舰显卡配一颗入门级 CPU，是明显的**头重脚轻**。

原因是 `app/services/build_assembler.py` 按 `role` 取「该品类排名最高者」：

```python
# Candidates arrive in fused rank order, so the first of a role wins.
by_role.setdefault(role, {"item": item, **entry})
```

**「部件级共识」保证了每个部件各自可信，但不保证两个部件相配。** 用户的诉求是「核心配置」，而一份内部失衡的配置不满足这个诉求——这属于**计划未覆盖的新问题**（计划 §2.1 只讨论了「单品 vs 组合」，没有讨论「组合内部的档次一致性」）。

## 九、完整改动清单（交接用）

### 9.1 后端

| 文件 | 函数 / 位置 | 具体改动 | 原因 |
|---|---|---|---|
| `app/tools/tables.py` | 模块级常量 | 删除 `EVIDENCE_FIELD_BY_COLUMN`（列名单键）；新增 `EVIDENCE_FIELD_BY_VIEW_COLUMN`（`(视图, 列) → field_key`）；新增 `evidence_fields_for_columns(columns, views)`，按语句实际视图收敛并**按 `columns` 过滤**；`__all__` 同步 | `cpu_catalog` 与 `gpu_catalog` 都有 `architecture` 列，单键会让 CPU 的架构认证成 `gpu.architecture` |
| `app/tools/database.py` | `_reverse_evidence` / `_build_row_bindings` / `_filter_evidence_for_result` | 三个函数新增可选参数 `views`；`_build_row_bindings` 的内层循环由 `(列, field_key)` 改为 `(列, field_key 元组)`，一列可对应多个候选字段 | 让 CPU 字段能反查到证据 |
| `app/tools/database.py` | `query_database` | 用 `views = getattr(validation, "tables", None)` 取语句视图并向下传 | `ValidationSuccess` 已带 `tables`；用 `getattr` 是因为测试用 `SimpleNamespace` 替身，缺属性时降级为「不限定视图」而不是报错 |
| `app/tools/database.py` | `DEFAULT_EVIDENCE_FIELDS` | 补 5 个 `cpu.*` 字段（`architecture` / `socket` / `cores_total` / `threads` / `base_power_w`） | 原集合全是 GPU 字段，**CPU 实体拿不到任何可引用证据**，只能退回 `canonical_name` |
| `app/tools/database.py` | `resolve_evidence` | 默认 `view` 由 `gpu_catalog` 改为 `component_profile_catalog` | 该视图含全部 95 个实体身份，CPU 与 GPU 可一次解析；已验证两种视图的 `entity_id` 是同一个 UUID |
| `app/tools/registry.py` | `resolve_evidence_tool` | `view` 参数的 description 改为「默认 component_profile_catalog，可跨品类」 | 小模型常忘传 `view` |
| `app/agents/loop.py` | `run_tool` 的 `resolve_evidence` 分支 | legacy 路径 `view` 默认值由 `gpu_catalog` 改为 `component_profile_catalog` | 与 protocol 路径对齐（protocol 路径不传时用函数默认值） |
| `app/agents/prompts.py` | 两个模板的 Workflow 段 | 新增 `RECENCY` 硬规则：新度偏好**必须**落到 `gpu_catalog.release_date` / `cpu_catalog.release_date` 的排序或过滤 | 实测同提示词推 2021 年旧卡，而库里有 2025 年新品 |
| `app/agents/prompts.py` | 两个模板的候选发现段 | 「不要 ORDER BY vram/price DESC」放宽为「允许服务于用户偏好（`release_date DESC` / `amount ASC`）的排序，禁止**无 ORDER BY 的裸 LIMIT**」 | 原措辞把「排序」整体禁掉了，而裸 `LIMIT` 才是取到任意切片的真凶 |
| `app/agents/prompts.py` | protocol 模板 `## Whole-machine requests` | 从「禁止跨品类、只能单品」重写为「**核心配置**」：要求同时提交 CPU 与 GPU 候选；配套部件由后端推导、**不得编造型号**；`SELECT` CPU 的 socket；缺口列入 `insufficient_information` | 用户的产品定义 |
| `app/agents/prompts.py` | protocol 模板的 identifier 规则 | 补强 GPU 世代匹配：`architecture` 只存代号（`Turing` / `Ada Lovelace` / `RDNA 3`），`LIKE '%RTX%'` 匹配不到；要取当代需按 `name` 或 `release_date` | 实测 Agent 写了 `architecture = 'Turing'` 与 `architecture LIKE '%RTX%'`（后者必然 0 行） |
| `app/services/build_assembler.py` | **新文件** | `assemble_core_build()`：① 用 `candidate_id` 反查 `component_profile_catalog.category` 定品类；② 从 `cpu_catalog.socket` 匹配 `platform` 档位；③ 平台 → 内存代际；④ `cpu.max_power_w + gpu.board_power_w + 120W` 向上取 `psu` 档位；⑤ 按消息匹配 `workload_requirement_catalog` 拿 ram/storage 需求；⑥ 每项带 `basis`；⑦ 非整机请求返回 `None` | 「核心硬件具体、配套说明规格即可」 |
| `app/services/fusion.py` | `_run` / `_run_parallel` | 在 `narrate_fusion` 之前调用 `assemble_core_build(candidates=result.top_k, message=..., session=...)`，并把结果作为 `core_build` 传入 | |
| `app/services/fusion.py` | `presentation_of` | 增加 `core_build=getattr(narration, "core_build", None)` | 让 `presentation` 与 `natural_language` 两个入口都能读到 |
| `app/services/narrator.py` | `deterministic_narration` | 新增 `core_build` 参数，两个 return 都携带；新增 `_fallback_headline`（有配置单时输出「这套配置以 … 为核心」） | 降级路径是这套部署实际走的路，必须也能出配置单 |
| `app/services/narrator.py` | `narrate_fusion` | 新增 `core_build` 参数；LLM 路径用 `narration.model_copy(update={"core_build": core_build})` 注入；两处 `deterministic_narration` 调用同步传参 | 配置单是后端的推导结果，不能被模型改写或遗漏 |
| `app/services/narrator.py` | `_brief` | 新增 `core_build` 段（core / supporting / gaps）；`NARRATOR_SYSTEM_PROMPT` 增加配置单规则（可命名、不得改写成自己的发现、不得加厂商型号） | 让 LLM 路径的 headline 也围绕配置单 |
| `app/schemas/fusion.py` | 模块级 | 新增 `SupportBasis` / `CoreComponent` / `SupportingSpec` / `CoreBuild`；`PresentationResult` 增加 `core_build: CoreBuild \| None = None` | 加在父类上，`NaturalLanguage` 是其子类，自动继承 |
| `backend/.env` | 预算段 | `AGENT_MAX_ROUNDS` 与 `AGENT_MAX_SQL_CALLS`：6 → 5/4（第一轮提速）→ **6/6（本轮回退）**，注释记录两轮依据 | |

### 9.2 前端

| 文件 | 改动 |
|---|---|
| `src/api/agentFusion.ts` | 新增 `SupportBasis` / `CoreComponent` / `SupportingSpec` / `CoreBuild` 类型；`PresentationResult` 增加 `core_build?: CoreBuild \| null` |
| `src/api/conversations.ts` | `NaturalLanguagePayload` 增加 `core_build?: CoreBuild \| null`；补 `import type { CoreBuild } from "./agentFusion"` |
| `src/components/terminal/CoreBuildPanel.vue` | **新文件**：核心硬件卡片 + 配套规格列表（依据三态标签：场景需求实心 / 规则推导描边 / 未收录虚线灰）+ `gaps`；沿用终端风格 BEM + scoped CSS |
| `src/components/terminal/RecommendationBlock.vue` | ① 接入 `CoreBuildPanel`；② 新增 `coreBuild` computed（先读 `presentation.core_build`，再回退 `naturalLanguage.core_build`）；③ `natural_language` 回退分支补 `core_build`；④ 修 `coverageText`——原实现是 `Math.round(c * 3)` 硬编码，2-Agent 场景会显示 `3/3`，现改为按 `trace.configured_agent_count` 计算，缺该字段时才回退为百分比 |
| `src/components/terminal/RecommendationBlock.spec.ts` | fixture 补 `configured_agent_count`；新增 3 个测试：2-Agent 覆盖率、`presentation` 携带配置单、仅 `natural_language` 携带配置单 |
| `src/components/terminal/CoreBuildPanel.spec.ts` | **新文件**：6 个测试（核心部件顺序、依据标签、未收录与推导样式区分、gaps 显示、无 gaps 时隐藏、部分构建不崩） |

### 9.3 测试基线

| 项目 | 数字 | 备注 |
|---|---|---|
| 后端 pytest | **322 passed / 2 skipped** | 新增：组装器 12、证据映射 7、prompt 规则 4 |
| 前端 Vitest | **73 passed** | 新增：配置单 6、结果区 3 |
| `vue-tsc --noEmit` | 通过 | |

`tests/test_one_agent_failure_isolation.py` 与 `tests/test_three_agent_concurrency.py` 里的 `fake_narrate` 替身已同步增加 `core_build=None` 参数（`narrate_fusion` 签名变更的连带修改）。

## 十、下一步（按优先级，含落点与验收方式）

1. **修 claim 的 `field_key` 错乱**（最高优先级——可信度 20 与 90 的差距就在这里，且直接触及项目的真值检验立场）
   - 落点 A：`app/tools/database.py` 的 `resolve_evidence`，评估**移除 `canonical_name` 回退**，或改为返回一条带明确提示的 `error`，避免「默认字段」的暗示；
   - 落点 B：`app/agents/prompts.py` 的 claim 规则，明确「`field_key` 只能逐字复制 `observation.evidence[].field_key`」并给出正反例；
   - 落点 C：提交校验层——当 claim 的 `value` 含单位后缀而 `field_key` 是 `entity.canonical_name` 时，**返回可读的修正提示**（参考已有的 `duplicate_statement` 机制，见 `app/agents/loop.py` 的重复语句检测），而不是静默判 CONFLICT。**这一条最可能一次性解决问题**，因为它把「模型不知道错」变成「模型被告知错在哪」。
   - **验收**：任一候选的 claim 出现 `gpu.vram_gib` / `gpu.board_power_w` / `cpu.socket` 等真实字段，且 `fact_support > 0`。
2. **配置单的档次匹配**：`build_assembler` 选定 CPU 后校验其与 GPU 是否同档（可用价格带或功耗带粗筛），失衡时**换用下一个 CPU 候选**或**在 `gaps` 中明确提示**。**验收**：不再出现「旗舰 GPU + 入门 CPU」。
3. **强制产出 CPU**：即使预算已恢复，也建议后端在整机场景下对 `cpu_catalog` 做一次确定性补选兜底（与 `build_assembler` 同源），避免模型偶发不给 CPU 时平台/内存双双落空。
4. **prefill 瘦身**（30 秒目标的唯一路径）：当前单次 `prompt_eval_duration_ms ≈ 11.8s`、`llm_calls 7`。需要实测「prompt 缩短 X% → prefill 降 Y%」的曲线后再定目标。
5. **补充观测点**（支撑第 1 项的证据需求）：把每个 Agent 的 `resolve_evidence` 原始返回与其 `submit_recommendation` 参数成对落库（或写入 `AgentRun.metrics` 的摘要），否则「模型没照抄」这一判断永远只能停留在推断。
