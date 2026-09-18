# RigBuilder Plan V3.3：三模型并行、自主 SQL Agent 与统一 Tool Protocol 改造

> 状态：方案设计（V3.2 → V3.3 差距分析与第一阶段文件级清单）。
> 前置版本：[Plan_V3.2.md](Plan_V3.2.md)；输入规范：`docs/RigBuilder V3.3 三模型并行自主 SQL Agent 改造提示词.md`。
>
> 本文结论均以当前仓库代码为准，只描述差异、保留/修改/新增边界与回归风险，不把目标值写成完成结果。本版本**不立即删除**任何 legacy fallback，先让新 native-tool + terminal-tool 路径通过自动化测试与实机 smoke test。

## 0. V3.2 现状基线（代码核实结果）

当前实现链路（backend/app 各文件已核实）：

```text
FastAPI(main.py, 单 worker)
 -> /api/query/jobs -> QueryJobManager._execute(jobs.py)
    -> resolve_query_mode -> ChatService.reply | FusionService.run(fusion.py)
       -> inference_scheduler.run_serial(scheduler.py)   # 全局 asyncio.Lock
          -> FusionService._run()
             -> 单个 SessionLocal() 贯穿整个 fusion
             -> loaded_first() 排序(inference/router.py)
             -> for model_id in [a,b,c]:   # 串行
                -> AgentRun 落库
                -> run_agent_loop(agents/loop.py)
                   -> _run_native: complete_chat(tools=…) ×≤3轮(LLM Gateway services/llm.py)
                   -> schema mode: complete_chat(response_schema=Recommendation.model_json_schema())
                   -> 失败路径: schema 修复 1 次 -> legacy_finalize -> _run_legacy
                -> TruthVerifier.verify / no_verified_candidates 门禁
                -> AgentRun metrics 落库
             -> FusionEngine.fuse(fusion/engine.py, 确定性排序)
 -> result_payload 写回 query_job; SSE 由 sink 同步回调落库(services/events.py, jobs.py)
```

关键事实（影响本方案设计，务必保留）：

1. **推理只依赖单 Router**：`Settings.llm_base_url`（默认 `:8080/v1`）+ `LLM_TEST_MODEL_IDS`（恰三个 ID，由同一 llama-server 的 `models-max=1` 热切换加载），`--parallel 1 / --ctx-size 12288`。
2. **LLM Gateway 是单函数 `complete_chat`**：直接拼 OpenAI 兼容 payload，`tools`（tool_choice=auto, parallel_tool_calls=False）与 `response_format.json_schema`（strict）两个协议互相独立、可切换；无 per-model endpoint 概念。
3. **AgentRunner 直接处理 provider 消息形状**：`agents/loop.py` 手写 `assistant.tool_calls` / `role:tool` 消息数组，legacy 文本 JSON 协议另走 `parse_model_reply`；尚未有 canonical protocol 归一化边界。
4. **capability 是惰性进程缓存**：`services/llm.py` 模块级 `dict[model_id,bool|None]`，由真实用户请求第一次跑完后根据 metrics 回填，无启动冒烟测试、无 capability profile。
5. **存在"Tool Mode → JSON Schema Mode"二次协议切换**：`_run_native` 末尾追加 user 提示并以 `response_schema` 再生成一次；该模式历史上出现过 `Final JSON mode failed after database tools completed`，现有一性 legacy finalize 兜底。
6. **SQL Tool 已有行/单元格上限但无 token 预算**：`sql_max_rows=100`、`sql_max_cell_chars=500`、`sql_max_evidence_ids=100`；超行只置 `truncated=true`，无"请缩窄 SQL 重查"的显式指令；整份 ToolResult（含 evidence 描述符）全量回注上下文。
7. **无 context preflight**：每轮 LLM 调用前不检查 `prompt_tokens + reserved_completion <= n_ctx`，靠 Router `ctx-size=12288` 兜底，出现过 `prompt tokens > n_ctx`。
8. **fusion 全程单 DB session**：`SessionLocal` 由 `FusionService._run` 创建并贯穿三 Agent 的 AgentRun/Message/ToolCall 落库与 TruthRepository；SQLAlchemy `Session` 非协程安全，这是并行化时必须先拆开的点。
9. **事件 sink 是同步回调**（`EventSink=Callable[[dict],None]`，每条事件独立开新 session + `SELECT ... FOR UPDATE` 分配 sequence），并发事件顺序天然按行锁串行化，可保留。
10. **Agent 预算**：native 最多 3 工具轮 + 最终 schema 轮；`agent_max_rounds=4`、`agent_max_sql_calls=4`、单 Agent 超时 900s、fusion 总超时 540s。

---

## 1. V3.2 与 V3.3 目标架构的差异（逐节对照）

### 1.1 推理拓扑（提示词第二节 / 第十二节）
| 维度 | V3.2 现状 | V3.3 目标 |
|---|---|---|
| 服务进程 | 1 个 llama-server Router（`:8080`, models-max=1） | 3 个独立 llama-server：agent-a `:8081` / agent-b `:8082` / agent-c `:8083`，模型常驻 |
| 模型互斥 | `inference_scheduler.run_serial` 全局 asyncio.Lock 串行化 | 移除全局锁；三个 AgentRunner 并发 |
| 单 server parallel | `--parallel 1`（与 models-max=1 配套） | 每个 server `--parallel 1`（单个 Agent 内部仍是 LLM→Tool→LLM 串行） |
| 模板/参数 | 共用一个 Router preset，三模型共享上下文/模板配置 | 每 server 可独立选 GGUF、chat template、ctx、reasoning、tool 参数 |
| 健康/错误 | 单 `get_loaded_models()` 探测 Router | 每 endpoint 独立 health/readiness/timeout/error-normalization/metrics |

### 1.2 LLM Gateway 与协议归一化（提示词第五、六节）
- V3.2：`complete_chat()` 是唯一调用点，直接面对 OpenAI 兼容 payload；`LLMCallResult/LLMToolCall` 已接近 canonical，但 AgentRunner 仍直接组装 provider 消息数组、感知 `tool_calls/role:tool` 与 legacy JSON 文本格式。
- V3.3：建立统一内部对象（ToolDefinition / ToolCall / ToolResult / AssistantTurn）与能力面（endpoint、n_ctx、native_tools、multi_turn_tools、parallel_tools、thinking、terminal_tool、chat_template、max_tool_rounds、max_sql_calls）；AgentRunner 只与 canonical object 交互，模型差异下沉到 Adapter。Agent 不得感知 `<tool_call>`、`[TOOL_CALLS]`、Gemma tool token、Qwen XML 等。
- capability 从"惰性进程缓存 bool"升级为"启动 smoke test + Model Capability Profile"（Test1~Test4 见提示词第六节），未通过则不进入 READY，直接标记 `TOOL_PROTOCOL_UNSUPPORTED`。

### 1.3 Agent 生命周期：去掉二次协议切换（提示词第七、八节）
- V3.2：`query_database` 若干轮 native tool → 末尾追加 user 消息 + `response_schema` 走一次独立 JSON Schema 生成（甚至再做一次 schema repair）。
- V3.3：新增 terminal tool `submit_recommendation`（无外部副作用，arguments 即 Recommendation Schema），工具集合固定为 `inspect_database / query_database / submit_recommendation`；整个生命周期只使用同一种 Tool Calling 协议，模型查询完毕直接 `submit_recommendation` 收尾。
- `submit_recommendation` 参数用 Pydantic `Recommendation` 严格校验；校验失败把 ValidationError 转成普通 ToolResult（`recommendation_schema_validation_failed` + details）回注同一 LLM，允许基于已有 Observation 修正，重试上限 1~2 次；仍失败则该 Agent 结束为 `schema_validation_failed`。
- 禁止后端为模型创造 Evidence/Claim/候选；只允许基于已有 Observation 唯一证明的结构修复（沿用 `_normalize_recommendation_reply` 的既有约束）。

### 1.4 上下文与 ToolResult 体积（提示词第四节）
- V3.2：Observation 全量塞回上下文，超行仅 `truncated=true`；无每轮 token 分类记账，无 preflight。
- V3.3：给 SQL Tool 增加资源预算（最大行、鼓励显式列、最大单字段长度、最大 ToolResult 字节/token），超限返回带 `truncated=true` 且附"请用更具体 SQL 继续"的 Observation；每轮 LLM 调用前 preflight，分别记录 system/message/tool schema/tool result/prompt 总量/n_ctx/剩余 completion 预算，保证 `prompt + reserved_completion <= n_ctx`，否则确定性压缩或返回 context budget 错误，不等 Router 400。

### 1.5 并发调度与会话隔离（提示词第十、十一、十二节）
- V3.2：fusion 内单 session + 串行 for 循环；DB 证据查询使用同一 session。
- V3.3：`asyncio.gather` 并发三个 run_agent；单 Agent 失败不取消另外两个（保持 partial coverage / failure isolation）；每个 Agent 的 messages / tool_calls / SQL audit / metrics / context / failures 完全隔离；每 Agent 独立 SessionLocal（ORM 线程安全 + 证据反查独立）；DB 连接池需支撑 3 Agent × 每 Agent ≤4 SQL 的并发。加载顺序不再决定执行顺序，但融合前仍按 configured_order 恢复确定性排序（此语义必须保留）。

### 1.6 错误与可观测性（提示词第十二、十三节）
- 保留 UI 只显示安全 failure code 的现状；Router/server HTTP 400/500 的关键底层信息必须保留进内部日志。
- 指标扩展为并行语义（TTFT、model load、prompt eval、generation、tokens/s、SQL tool time、verification、Agent wall、fusion、Job 总延迟、单/双/三 Agent 并发吞吐、GPU VRAM idle/peak），并比较"V3.2 串行 vs V3.3 常驻并发"。

### 1.7 不变的部分（提示词第一、三、九、十节）
- 保留"自主 SQL Agent"设计，绝不改成后端 RAG 预检索。
- LLM 决定"查什么/推荐什么"，Backend 决定"能不能安全查"，Truth Verification 决定"证据是否支撑"，Fusion 决定"如何形成可信结果"，Adapter 决定"格式如何归一化"。
- 只读权限、SQL Validator、agent_catalog 注册视图、单条 SELECT/WITH..SELECT、写操作禁止、轮次/SQL 次数上限、Evidence/Entity/Claim 验证规则、Truth DB 硬门禁、至少一个 candidate_valid 候选才可进融合——全部原样保留。
- 三个 Agent 收相同 User Query/System Rules/Tool 定义/Schema Contract/Recommendation Schema/证据规则，但查询路径各自自主，不共享 Evidence Bundle。

---

## 2. 可原样保留的模块（仅微调或不改）

| 模块 | 结论 |
|---|---|
| `app/verification/*`（rules/service/repository/comparison） | 保留，硬门禁不动 |
| `app/fusion/engine.py`、`constraints.py` | 保留；并行后仍按 configured_order 确定性输入，不做公式改动 |
| `app/tools/validator.py`、`tables.py`、`errors.py` | 保留（errors 增加新 code） |
| `app/tools/database.py` 执行/证据反查主干 | 保留；只扩 budget/截断消息与池检查 |
| `app/context/*`（contracts/service） | 保留 |
| `app/models/*`、`app/db/session.py`、`app/schemas/*`（recommendation/fusion/verification/query） | 保留（AgentRun metrics 为 dict，兼容新指标字段） |
| `app/services/jobs.py`、`events.py`、`api/query.py` | 保留；job/event 状态机与 SSE 语义不因并行改变 |
| `app/api/chat.py`、`app/services/chat.py`、`routing.py` | 保留（chat 不走 fusion scheduler） |
| `app/services/agent.py`（单 Agent /api/agent/run） | 保留对外接口，内部改为按 profile 绑定调用 |
| 只读账号/注册视图/SQL 审计/Truth Release 绑定 | 保留 |

---

## 3. 需要修改的模块

| 模块 | 修改内容 |
|---|---|
| `app/core/config.py` | 新增三模型独立配置（endpoint/模型文件/context 等），见 §6；新增 tool-result budget 与 preflight 相关设置 |
| `app/services/llm.py` | `complete_chat` 支持 endpoint/base_url 参数与 canonical 化；保留 `generate_reply` 兼容包装；capability 惰性缓存逐步迁移到 profile 模块 |
| `app/inference/scheduler.py` | 移除 fusion 路径的全局互斥（保留并发上限保护）；拆分 per-endpoint 健康/超时/错误归一化（可平移 router.py 已有逻辑） |
| `app/inference/router.py` | 单 Router `loaded_first` 改为"每个 endpoint 是否就绪/loaded"的探测；保留对多 endpoint 的 health check |
| `app/agents/loop.py` | 核心改造：canonical 消息/对象；工具集合加 `submit_recommendation`；删除正常路径 Tool→Schema 二次切换；schema 修复逻辑移入 terminal tool 错误恢复；上下文 preflight |
| `app/agents/prompts.py` | system prompt 描述三工具 + `submit_recommendation` 收尾语义 + 截断后缩窄 SQL 的指引 |
| `app/tools/contracts.py` | ToolCall/ToolResult 的 tool 枚举加 `submit_recommendation`；`SubmitRecommendationArguments`；ToolResult 增加体积/截断元信息 |
| `app/tools/database.py` | 增加 ToolResult token/字节预算与显式截断消息（truncated + 指引缩窄）；列数提示（鼓励显式列） |
| `app/tools/schema.py` | 与 submit_recommendation 的 Schema 注入配合（工具定义由 profile/注册表统一提供） |
| `app/services/fusion.py` | 三 Agent 串行 → `asyncio.gather`；每 Agent 独立 session/事件闭包；单 Agent 失败隔离；排序与 gate 保留；融合入参仍是候选/验证对象 |
| `app/main.py` | lifespan 启动后执行 capability smoke test（异步）；暴露 /health/inference 聚合（可选） |
| `frontend`（src 相关 trace/agent 视图） | 并发后事件按 agent 交错到达，需验证 trace 分组、agent 级终态与 `agent_coverage` 展示不受顺序影响（先做后端回归，再改 UI） |

---

## 4. 需要新增的模块

建议全部落在后端，尽量聚合网关能力：

| 新增 | 内容 |
|---|---|
| `app/inference/profiles.py` | `ModelProfile` dataclass（model/endpoint/context_size/native_tools/multi_turn_tools/parallel_tools/thinking_enabled/terminal_tool_supported/chat_template/max_tool_rounds/max_sql_calls）+ 从 Settings 构建三模型 profile 的注册表 |
| `app/inference/servers.py` | 每 endpoint 的 client 工厂、health/readiness、请求 timeout、错误归一化、重启诊断信息收集 |
| `app/agents/protocol/*` | canonical：`ToolDefinition/ToolCall/ToolResult/AssistantTurn`（吸收 services/llm.py 的 LLMToolCall/LLMCallResult 与 tools/contracts.py 的 ToolResult，避免循环引用时以 protocols 为单一事实源）；`QwenAdapter/MistralAdapter/GemmaAdapter` 或统一 `llama.cpp OpenAI-compatible Adapter` + 校验钩子；`message history` 构造器 |
| `app/agents/capabilities.py` | 启动/手动 smoke test（Test1 单 tool call、Test2 回注 ToolResult、Test3 连续两次 tool call、Test4 terminal tool args 解析为 schema）；结果写 capability cache / profile |
| `app/agents/context_budget.py`（或并入 loop） | prompt token 分项统计 + preflight：system/messages/tool schema/tool result/n_ctx/reserved_completion；确定性压缩或 `context_budget_exceeded` 错误 |
| `app/tools/registry.py`（可选） | 统一工具定义来源：三工具 JSON schema + `submit_recommendation` 的 Recommendation schema（供 native tools 与 canonical 共用） |
| tests | `test_tool_protocol.py`、`test_terminal_tool.py`、`test_multi_turn_tool.py`、`test_context_budget.py`、`test_three_agent_concurrency.py`、`test_one_agent_failure_isolation.py`（见 §8） |

> 原则：`submit_recommendation` 是纯校验型 terminal tool（无外部执行）；校验错误以 ToolResult 形态回注，属正常工具协议，不回退 legacy、不重跑 SQL。

---

## 5. 推荐的新调用链

```text
FastAPI(main.py)
 -> /api/query/jobs -> QueryJobManager._execute
    -> FusionService.run(无全局锁, 并发限额)
       -> asyncio.gather(run_agent(a), run_agent(b), run_agent(c))   # 互不取消
            每个 run_agent(独立 SessionLocal + 独立事件/审计/metrics):
              AgentProfile.prepare(system prompt + Schema Contract + 工具集合)
              [loop: canonical chat -> AssistantTurn
                 -> 解析 tool_calls(ToolCall)
                 -> 执行 inspect_database / query_database / submit_recommendation
                 -> ToolResult 体积预算 -> 回注 assistant+tool 消息(带 tool_call_id)
                 -> context preflight 后进入下一轮]
              -> submit_recommendation -> Recommendation.model_validate
                   校验失败 -> ToolResult(success=false, schema_validation_failed) 回注重试(≤2)
                 -> TruthVerifier.verify(独立 repository/session)
                 -> no_verified_candidates 门禁  -> AgentRun 终态
       -> 失败隔离: 单个 Agent 异常只标记该 Agent failed
       -> 按 configured_order 恢复顺序 -> FusionEngine.fuse(不变)
       -> 结果写回 query_job / SSE
```

- 每个 Agent 内部仍串行 `LLM → Tool → LLM → Tool → LLM`，一次 Agent 只对一个 endpoint、一个常驻模型。
- 旧路径 `_run_legacy`/legacy_finalize/`generate_reply` 全保留（此阶段不删），仅在能力冒烟判定某模型 tool 协议不可用时按 profile 静态选择。

---

## 6. 配置文件级变更

`app/core/config.py` 新增（示范形态，字段名待实现冻结）：

```python
# V3.3 three resident servers
agent_models: str = ""          # JSON 或 key=value: agent-a 的 endpoint/model/ctx…
# 例如每项独立 base_url：AGENT_A_BASE_URL=http://127.0.0.1:8081/v1, AGENT_B_…, AGENT_C_…
# tool result budget
tool_max_result_bytes / tool_max_result_tokens / tool_max_result_rows
# context preflight
prompt_reserved_completion_tokens; context_overflow_strategy
# db pool for concurrent agents
readonly_pool_size / readonly_max_overflow / pool_pre_ping
```

`.env` 说明文档更新 + `llama-server` 启动基线（每模型一份）写入 `docs/Inference_Server_Guide`（V3.3 增补三端口基线）。

---

## 7. 潜在回归风险

1. **Session 并发使用**：`FusionService._run` 单 session 是当前 AgentRun/Message/证据反查/TruthRepository 的共同载体。并行拆分不彻底 → `Session` 跨协程使用/事务交错/`agent-a 的 ToolResult 注入 agent-b`。回归防线：三 Agent 各自独立 `SessionLocal()` 与独立 repository，`test_one_agent_failure_isolation` + `test_three_agent_concurrency`（mock 推理服务）先绿再上实机。
2. **确定性排序被破坏**：并行结束时间乱序，若在 gather 返回后按列表序直接进 fusion，会导致可重放性回归。防线：fusion 输入恒按 `configured_order` 重排（现有 `order_index` 逻辑保留并在测试中固定）。
3. **DB 连接瓶颈**：3 Agent × ≤4 SQL 并发 + 事件 sink 每事件独立 session，连接池默认值不足会阻塞。防线：readonly pool 增大 + `pool_pre_ping`；`context budget` 测试与并发测试覆盖。
4. **取消/超时语义漂移**：全局锁移除后 `cancel` 中断的是 `asyncio.gather`，需确保 AgentRun 行状态与 job 状态机仍一致（沿用 request_id 收尾），不要变成"只有浏览器不等待"。
5. **tool 协议变化导致 Schema 服从性回退**：把"最终 JSON Schema 生成"换成 terminal tool 后，部分模型可能不再走严格 json_schema grammar；但三模型均需保持 native tools 支持能力（capability Test1-4 门禁）。回归期**保留** schema-mode 作为可选 terminal 校验后端路径与旧 fallback，不直接删除。
6. **事件交错展示**：前端 trace 按 agent 分组但事件全局 sequence；并发下 agent 间事件穿插，需前端验证按 `agent` 字段渲染不破坏现状。
7. **浮点/指标口径**：`total_tokens` 累计 vs 单轮 `prompt_tokens` 的口径混淆会放大并行后的上下文误判；preflight 只使用每轮 prompt 实测 + 估算，不与前端累计数混用。
8. **模型独立参数放大配置面**：每模型单独 GGUF/template/ctx/reasoning 需要独立验收矩阵；冒烟测试不通过模型不得进入正式 fusion，避免"跑几十秒才失败"。
9. **README/Quick Start/API_documents 同步**：新配置项与三服务启动基线需同步文档，避免新环境按旧文档部署成单 Router。

---

## 8. 测试与实施顺序

### 8.1 测试更新（回归 + 新增）
- 保留全部现有 141 项通过用例（其 mock 单 Router 形态仍可作为 gateway 层单测）；其中以"单 Router 串行 fusion"为假设的用例需审计并参数化多 endpoint。
- 新增：tool protocol（canonical 往返/畸形 tool_calls）、terminal tool（成功/一次失败恢复/重试上限耗尽 → `schema_validation_failed`）、multi-turn tool（tool_call_id 回注/连续两次 tool）、context budget（超限即 preflight 错误而非 400）、three-agent concurrency、one-agent failure isolation、per-endpoint readiness 失败隔离。

### 8.2 阶段推进（对齐提示词第十五节）
1. **P1 三独立 llama-server 常驻**：脚本/文档、`.env` 三 endpoint、显存边界验证（3090 Ti 24GB）。涉及 config/inference/profiles 起步。
2. **P2 逐模型 multi-turn tool smoke test**：capabilities.py + profiles.py，对三个 endpoint 独立跑 Test1-4，固化 protocol 能力。
3. **P3 canonical Tool Protocol + capability profile**：新增 protocol 包并让 loop/gateway 边界归一化；legacy 路径保持原样并存。
4. **P4 增加 submit_recommendation terminal tool**：contracts/schema/prompt/loop + 单模型完整 Agent 测试。
5. **P5 取消正常路径 native-tools → JSON Schema 切换**：submit_recommendation 收尾生效（schema-mode 仅保留为注册工具的后端校验/可选 terminal 校验兜底）。
6. **P6 三 AgentRunner 并发 + 移除全局锁**：fusion.py gather 化、session 隔离、连接池与取消语义回归。
7. **P7 SQL ToolResult budget + context preflight**：消灭 exceeds-context 类错误。
8. **P8 跑旧测试 + 新增六类测试全绿**。
9. **P9 RTX 3090 Ti 实机联调**：V3.2 串行基线 vs V3.3 并发对比（指标矩阵按第十三节）。

> 当前阶段交付 = 本文档（提示词第十四节的"先输出"部分）。在 P1 实机确认三个 server 常驻无 OOM、P4 单模型完整 Agent 通过前，`_run_legacy`、`legacy_finalize`、schema finalization 与单 Router 代码路径一律保留、只加开关，不做删除。

## 9. 完成定义（V3.3）

- 三个模型常驻三 endpoint，一次 fusion 中三 Agent 并发运行、无全局锁、单 Agent 故障隔离。
- Agent 从第一轮到 `submit_recommendation` 只使用一种 Tool Calling 协议；不再出现 "Final JSON mode failed after database tools completed"。
- SQL Tool 有确定性体积预算；模型见到 `truncated=true` 可自缩窄 SQL；每轮 preflight 保证 `prompt + reserved <= n_ctx`。
- capability profile 启动冒烟，不兼容模型在进入正式 fusion 前即被标记。
- 三 Agent 消息/审计/metrics 完全隔离；融合前仍按 configured_order 确定性排序。
- Truth Verification 与 fusion-v1 硬门禁零弱化；新增六类回归测试 + 原 141 项测试全绿；实机对比指标可复现。

## 10. 本轮代码实施结果（2026-09-09，真机联调前）

状态：**P1 之外的全部软件阶段已在代码层落地，离线全量 160 passed + 1 skipped 通过**；未包含 `test_truth_verification_postgres.py`（需要显式环境开关，仍按原样跳过）。

| 阶段 | 结果 | 落点 |
|---|---|---|
| P1 三 llama-server 常驻 | 配置/脚本就绪，真机待验证 | `.env.example` 新段、`app/inference/profiles.py`、`servers.py`、`capability_smoke.py` |
| P2 逐模型 smoke | 框架就绪，无权重跳过 | `app/agents/capabilities.py`、`main.py` 开关、`scripts/capability_smoke.py` |
| P3 canonical Tool Protocol | 完成 | `app/agents/protocol/{contracts,adapter}.py`、`tools/registry.py`、`tools/contracts.py` |
| P4 submit_recommendation | 完成 | `agents/loop.run_agent_loop_protocol`、`agents/prompts` 协议版、校验回注重试 ≤2 |
| P5 去二次协议切换 | 正常路径已取消 | 协议路径只用 Tool Calling；旧 schema/legacy 原样保留 |
| P6 三 Agent 并发 | 完成 | `fusion.py`：容量 1 闸门内 `asyncio.gather` + 每 Agent 独立 Session/Repository/证据/事件；按 `configured_order` 确定性重排 |
| P7 budget + preflight | 完成 | `context_budget.py`、`tools/database.py` 字符预算 + 缩窄提示（`truncated_reason/reduction_hint`） |
| P8 测试 | 160 passed + 1 skipped | 新增 tool protocol / terminal / multi-turn / context budget / three-agent concurrency / one-agent isolation / narrator |
| P9 实机对比 | 待推理设备就绪 | 运行 `scripts/capability_smoke.py` 后按 §13 指标对比 |

API 契约（只增不改，前端零改动）：
- `FusionRunResponse` 新增可选 `natural_language`（`NaturalLanguage.overview` + `per_candidate`），无有效结果/默认 `None`，旧前端不受影响。
- 并行仅在三个独立 endpoint 配置齐全时启用（`Settings` 缺省回落单 Router → 串行路径保持原样，既有测试零改动）。

Narrator：默认复用 `NARRATOR_PROFILE_ID=agent-b`（无第四个常驻模型）；只接收验证后只读简报，输出严格 JSON，candidate 白名单校验，失败/超时/非法输出一律回落确定性模板，不虚构候选与数值。

遗留（需真机确认后才决定删除/放量）：
- `_run_legacy` / `legacy_finalize` / schema finalization / 单 Router 串行路径全部保留。
- `services/agent.py`（单 Agent `/api/agent/run`）仍走旧串行路径，本轮未切换到 profile/协议模式；如需可下轮对齐。
- capability 冒烟在真机出现未通过项时，应先根据该模型实际输出修复 adapter/工具 schema 后再判定，不应把临时模板问题当作协议不支持。
