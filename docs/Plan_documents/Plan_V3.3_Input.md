---
name: RigBuilder V3.3 并行Agent与推荐链改造
overview: 在不依赖推理真机的条件下，将 RigBuilder 从"单 Router 全局锁 + 三 Agent 串行"改造为"三独立 llama-server endpoint + Job 内三 Agent 并行"；完成 submit_recommendation terminal tool、统一 tool 协议与 capability 设计、SQL ToolResult/context 预算，并在融合结果后由复用模型(默认 agent-b)生成自然语言摘要与逐候选解释，保持前端 API 向后兼容。
todos:
  - id: cfg-profiles
    content: 新增三模型 endpoint 与 ToolResult 预算/DB池配置，实现 ModelProfile 注册表与 per-endpoint client
    status: completed
  - id: protocol-registry
    content: 用 [skill:lsp-code-analysis] 核对引用后用 canonical 协议契约、Adapter 与统一工具注册表
    status: completed
    dependencies:
      - cfg-profiles
  - id: loop-terminal
    content: Agent 协议路径接入 submit_recommendation 终局、校验错误回注重试，保留旧 finalize 路径
    status: completed
    dependencies:
      - protocol-registry
  - id: context-budget
    content: 实现 context preflight 记账与 SQL ToolResult token/字节预算及缩窄提示
    status: completed
    dependencies:
      - protocol-registry
  - id: fusion-parallel
    content: 用 [subagent:code-explorer] 核对调用点后把 Fusion 改为闸门内三 Agent 并发并隔离会话
    status: completed
    dependencies:
      - loop-terminal
  - id: narrator-api
    content: 实现 Narrator(复用 agent-b) 自然语言包装并向 fusion/API schema 增量加可选字段
    status: completed
    dependencies:
      - fusion-parallel
  - id: smoke-tests-docs
    content: 实现 capability 冒烟框架与脚本，新增六类测试并跑全量回归与文档同步
    status: completed
    dependencies:
      - context-budget
      - narrator-api
---

## 用户需求

基于 V3.2 现状对 RigBuilder 后端进行 V3.3 架构升级。推理设备（远端设备 B，RTX 3090 Ti）尚未安装模型权重，本机为前后端与数据库设备。本轮目标：**在真机联调前，尽可能在代码层完成可编译、可被 mock/离线自动化测试覆盖的改造**，并为权重就绪后提供冒烟入口。

### 1. 由串行改为并行
- 一次 fusion 请求内，三个 Agent（agent-a/b/c）真正并发运行（asyncio.gather），互不取消。
- 不同 fusion Job 之间仍串行：保留容量为 1 的"fusion 执行闸门"，行为最可控。
- 移除原"全局推理锁用于三模型互斥"的语义；全局锁不再序列化三 Agent。
- 每个 Agent 拥有完全隔离的 DB 会话、messages、tool_calls、SQL audit、metrics、context、failures（SQLAlchemy Session 非协程安全，单 session 贯穿全 fusion 的写法必须拆开）。
- 单个 Agent 失败/异常不影响另外两个；融合前仍按 configured_order 恢复确定性排序。

### 2. 设计好模型、工具与调用链
- 三模型各自独立 llama-server endpoint（:8081/:8082/:8083），代码层先落地可配置的 Model Capability Profile 与 per-endpoint 健康检查、超时、错误归一化、指标。
- AgentRunner 只与 canonical Tool Protocol（ToolDefinition/ToolCall/ToolResult/AssistantTurn）交互；模型差异收敛到 Provider/Protocol Adapter（当前以 llama.cpp OpenAI 兼容路径为主）。
- capability 启动冒烟 Test1~4（单工具调用、回注 ToolResult 续轮、连续两次工具调用、terminal tool 参数解析）：无权重时可跳过、不阻塞启动；冒烟未通过的模型标记不可进入正式 fusion。

### 3. 完善模型输出、真值检查、推荐链路
- 新增 terminal tool `submit_recommendation`（无外部副作用，arguments 即 Recommendation Schema），正常路径取消"native tools → JSON Schema mode"二次协议切换；旧 finalize/legacy 路径保留作兜底与能力降级。
- `submit_recommendation` 校验失败转成普通 ToolResult（recommendation_schema_validation_failed + details）回注同一 LLM 修正，重试上限 1~2 次，不重跑 SQL、不伪造 Evidence/Claim。
- Truth Verification 硬门禁与至少一个 candidate_valid 才进融合的规则零弱化。
- **自然语言包装（Narrator）**：复用融合模型之一（默认 agent-b），在融合完成后对其发起一次纯 chat 请求；输出"整体结论摘要 + 逐候选解释"（候选推荐理由、风险、证据依据），不得改写候选、排名、数值、事实状态与风险；无有效候选/结果为空时也要给出不虚构的可读终态（确定性兜底文案）；Narrator 失败/超时/输出非法时回退到确定性模板。

### 4. API 关系约束（前端暂不改动）
- 前端本轮零改动，但后端调整必须保证 API 契约向后兼容：`/api/query/jobs`、`/api/query/jobs/{id}`、SSE events、FusionRunResponse/FusionAgentResult 的既有字段只增不改；新增字段一律可选并给出文档化说明。
- legacy fallback / schema finalization / 单 Router 兼容代码一律保留，只新增开关与并行路径。

## 交付边界
- 不安装/部署任何模型权重；真机冒烟以独立脚本与启动开关形式提供。
- 完成后：全部既有自动化测试（141 passed, 1 skipped）不回归；新增六类离线测试全绿；文档同步配置与新契约。


## 技术栈选择
沿用现有后端技术栈，不做框架替换：
- FastAPI + uvicorn（单 worker 现状不变）、Pydantic v2、SQLAlchemy 2.x + PostgreSQL Truth DB。
- llama.cpp llama-server 的 OpenAI-compatible 端点（三 endpoint），httpx.AsyncClient 异步调用。
- pytest（沿用现有 mock/fake 测试模式）覆盖离线行为；真机冒烟用独立脚本（幂等、可跳过）。

## 实施策略与关键决策

### 1. 配置与模型画像（先行落地，向后兼容）
- `Settings` 增加：每 Agent endpoint（AGENT_A/B/C_BASE_URL，缺省回落 `llm_base_url`，保证旧单 Router 配置仍可运行）、narrator 模型指定（默认 agent-b）、ToolResult 体积预算、preflight 保留 completion、DB 池参数、冒烟开关。
- 新增 `ModelProfile`（id/endpoint/base_url/context_size/native_tools/multi_turn_tools/parallel_tools/thinking_enabled/terminal_tool_supported/max_tool_rounds/max_sql_calls/reserved_completion）与注册表：三模型全部逻辑从散落的 `settings.llm_base_url + llm_test_model_ids` 收拢到 profile。
- 保留 `native_capability()/set_native_capability()` 兼容函数，内部委托给 profile 模块，避免双份状态漂移。

### 2. Canonical Tool Protocol 与统一工具注册表
- 新增 `agents/protocol/`：canonical `ToolDefinition / ToolCall / ToolResult / AssistantTurn / CanonicalMessage` + Adapter（canonical → OpenAI-compatible payload；响应 → canonical + 校验，畸形 tool_calls 归一为 LLMProtocolError）。AgentRunner 不再直接拼接 provider 形状消息。
- 新增 `tools/registry.py` 统一工具定义：`inspect_database`、`query_database`、`submit_recommendation`（arguments 内联 Recommendation JSON Schema）。
- 兼容策略：`NATIVE_TOOLS`（仅两个 DB 工具）保留给 legacy/旧测试；协议路径使用 `FULL_TOOLS = NATIVE_TOOLS + [submit_recommendation]`，按 profile.terminal_tool_supported 选择。

### 3. Agent 生命周期（terminal tool 收尾 + 去二次协议切换）
- 协议路径循环内每轮以 canonical 调用 handle：解析 ToolCall → 执行工具 → 回注 assistant + tool 消息（tool_call_id）→ context preflight → 下一轮；模型调用 `submit_recommendation` 即终局。
- 校验失败：把 ValidationError 转 ToolResult(success=false, code=recommendation_schema_validation_failed, details) 回注；同一 Agent 内重试 ≤2；仍失败结束为 `schema_validation_failed`。
- 能力降级：profile.terminal_tool_supported=false 或冒烟未通过时走既有 native→schema finalize/legacy 路径（原代码保留，不加删除）。
- 保持 `run_agent_loop` 旧签名与语义不动（既有测试零改动），协议路径以新入口 `run_agent_loop_protocol(model_handle, profile, base_messages, evidence_session, ...)` 提供。

### 4. Context preflight 与 ToolResult 体积预算
- `context_budget.py`：按轮分别记账 system/messages/tool schema/tool result/prompt 总量，比对 model n_ctx 与保留 completion；不满足则执行确定性压缩或返回 `context_budget_exceeded`，杜绝"等 Router 400"。
- `tools/database.py`：在既有行/单元格/evidence 上限之上增加结果 token/字节预算；超限置 truncated=true 并附"结果过大，请用更具体 SQL 缩小范围"的显式提示，模型据此自缩窄（观察 existing `Observation.truncated` 语义扩展，不破坏字段）。

### 5. Fusion 并行化与会话隔离
- 保留 `inference_scheduler.run_serial` 作为 fusion 执行闸门（容量 1，跨 Job 串行）；闸门内 `asyncio.gather(..., return_exceptions=True)` 并发三 Agent。
- `FusionService._run` 重构为：`_prepare`（单会话：conversation/user 消息、release_key、base_messages）→ 每 Agent 独立协程（独立 SessionLocal 写入 AgentRun/Message/ToolCall、独立 TruthRepository 与 evidence_session、独立事件闭包）→ `AgentOutcome` 收集 → configured_order 确定性重排 → FusionEngine.fuse（不变）→ Narrator → 结果落库。
- 单 Agent 异常（LLMServiceError/超时/未知异常）在该协程内转失败结果；外层 CancelledError（Job 取消）先标记该 request 的 RUNNING AgentRun 再传播。
- DB：readonly 连接池加大 + pool_pre_ping + 配置化，支撑 3 Agent × ≤4 SQL 并发与事件 sink（sink 本身每事件独立会话 + FOR UPDATE 分配 sequence，可保留）。

### 6. Narrator（自然语言包装）
- `services/narrator.py`：只接收由验证后数据构造的只读简报（Top 候选 + 分数 + fact_support + 风险 + coverage + 缺失信息），以 agent-b profile 发起一次纯 chat（无工具），要求输出严格 JSON（overview + per_candidate[{candidate_id, explanation}]），Pydantic 校验 + 候选 ID 白名单（必须 ∈ top_k）+ 只允许字符串内容（不携带数值断言，避免与源数据冲突）。
- 失败/超时/非法输出 → 确定性模板兜底；无有效候选时输出"没有候选通过 Truth DB 验证"的可读终态，不虚构。
- fusion 结果 `FusionRunResponse/FusionResult` 增量可选字段 natural_language（缺省 None），旧前端不受影响。

### 7. Capability 冒烟框架
- `agents/capabilities.py` + `backend/scripts/capability_smoke.py`：Test1~4 按 profile 逐一执行；真机可用时运行即得 READY / TOOL_PROTOCOL_UNSUPPORTED 清单；无权重/endpoint 不可达时打印 skipped 并以非零码显式报告"未验证"，启动开关 `capability_smoke_on_startup` 默认关闭，不阻塞开发与测试。

## 性能与可靠性要点
- 总延迟从"三 Agent 串行和"降为 max(三 Agent) + 融合 + Narrator；指标分别记录 TTFT、prompt eval、generation、SQL tool、verification、Agent wall、fusion、Narrator、Job 总时长（沿用 llama timings + usage 口径，累计 total_tokens 不与单轮 prompt_tokens 混用）。
- preflight 估算用字符/token 近似（约 len/4）与响应 usage 校准，开销 O(消息长度)，不做整库扫描。
- 日志沿用现有 logger 与错误码体系；per-endpoint 的 HTTP 400/500 保留底层关键信息进内部日志，UI 只暴露既有安全错误码；事件经 `_sanitize_event` 白名单清洗。
- 回归控制：所有重构对新路径做增量、旧路径与旧测试不动；`run_agent_loop`/`complete_chat`/`native_capability` 等对外符号保持兼容；删除代码须等真机 smoke 通过后另行决策。

## 架构设计（目标调用链）
```mermaid
flowchart TD
  FE[前端(本轮不改)] -->|POST /api/query/jobs| JOB[QueryJobManager._execute]
  JOB -->|fusion| RUN[FusionService.run]
  RUN --> GATE[fusion 执行闸门 容量1: 跨Job串行]
  GATE --> GATHER[asyncio.gather 三Agent并发]
  GATHER --> A[Agent-a 独立Session/Profile :8081]
  GATHER --> B[Agent-b 独立Session/Profile :8082]
  GATHER --> C[Agent-c 独立Session/Profile :8083]
  A --> L1[canonical loop: LLM→Tool→LLM...→submit_recommendation]
  B --> L2[canonical loop]
  C --> L3[canonical loop]
  L1 --> V1[TruthVerifier 独立Repository]
  L2 --> V2[TruthVerifier 独立Repository]
  L3 --> V3[TruthVerifier 独立Repository]
  GATHER -->|AgentOutcome 集合, 失败隔离| SORT[按 configured_order 确定性重排]
  SORT --> FUSE[FusionEngine.fuse 不变]
  FUSE --> NAR[Narrator: 复用agent-b 纯chat 自然语言包装]
  NAR --> OUT[FusionRunResponse + natural_language 可选字段]
  OUT --> JOB2[result_payload 落库 / SSE 事件]
```

## 目录结构与文件级清单

```
d:\RigBuilder\backend\
├── app\
│   ├── core\
│   │   └── config.py                 # [MODIFY] 新增三 endpoint/profile 配置、ToolResult 预算、preflight、DB池、冒烟开关、narrator 指定；旧字段保持兼容
│   ├── inference\
│   │   ├── profiles.py               # [NEW] ModelProfile/Registry：由 Settings 构建 agent-a/b/c 画像（含 n_ctx、endpoint、工具能力、保留token）
│   │   ├── servers.py                # [NEW] per-endpoint 异步 client：health/readiness、请求超时、错误归一化(router_unavailable/llm_timeout等)、metrics
│   │   ├── scheduler.py              # [MODIFY] 保留 run_serial 作为容量1闸门；语义注释更新，不再承担"三模型互斥"
│   │   └── router.py                 # [MODIFY] 增加 per-endpoint 探测；loaded_first 保留给单 Router 兼容模式
│   ├── agents\
│   │   ├── protocol\
│   │   │   ├── contracts.py          # [NEW] canonical: ToolDefinition/ToolCall/ToolResult/AssistantTurn/CanonicalMessage
│   │   │   ├── adapter.py            # [NEW] llama.cpp OpenAI-compatible Adapter：canonical→payload、响应→canonical+校验、畸形归一为LLMProtocolError
│   │   │   └── __init__.py
│   │   ├── capabilities.py           # [NEW] Test1~4 冒烟 + 状态缓存（READY/TOOL_PROTOCOL_UNSUPPORTED/SKIPPED），与 llm.py 兼容函数委托
│   │   ├── context_budget.py         # [NEW] 分项记账 + preflight（system/messages/tool schema/tool result/prompt/n_ctx/reserved）→压缩或 context_budget_exceeded
│   │   ├── loop.py                   # [MODIFY] 协议路径入口 run_agent_loop_protocol：FULL_TOOLS、submit_recommendation 终局、校验错误回注重试≤2、去二次切换；旧 run_agent_loop/_run_legacy 原样保留
│   │   ├── prompts.py                # [MODIFY] system prompt：三工具描述 + submit_recommendation 收尾语义 + truncated 后缩窄 SQL 指引
│   │   └── states.py                 # 不改（可考虑补充协议状态常量，非必需）
│   ├── tools\
│   │   ├── contracts.py              # [MODIFY] tool 枚举加 submit_recommendation、SubmitRecommendationArguments；ToolResult/Observation 截断与预算元信息扩展（字段可选，向后兼容）
│   │   ├── registry.py               # [NEW] 统一工具定义源：db 工具 + submit_recommendation(内联 Recommendation schema)
│   │   ├── database.py               # [MODIFY] ToolResult token/字节预算 + 显式"缩窄重查"截断提示；行/单元格/evidence 上限不变
│   │   ├── schema.py                 # [MODIFY] 与 registry 对齐（inspect 输出结构不变）
│   │   ├── validator.py / tables.py  # 不改
│   │   └── errors.py                 # [MODIFY] 增补 recommendation_schema_validation_failed / sql_result_too_large / context_budget_exceeded 提示与分类
│   ├── services\
│   │   ├── llm.py                    # [MODIFY] complete_chat 支持 endpoint/base_url 覆盖；capability 兼容函数委托 profile；generate_reply 保留
│   │   ├── fusion.py                 # [MODIFY] run=闸门；_prepare+三Agent gather 协程（独立SessionLocal/Repository/evidence/事件）+ AgentOutcome + 确定性重排 + Narrator 接入 + 取消标记
│   │   ├── agent.py                  # [MODIFY] 单 Agent 路径切换到 profile/handle（对外接口不变）
│   │   ├── narrator.py               # [NEW] 只读简报→agent-b 纯chat→严格JSON(overview+per_candidate)+候选白名单+确定性兜底
│   │   ├── jobs.py / events.py       # 基本不改；错误码映射按需补充 per-endpoint 聚合（向后兼容）
│   │   └── chat.py / routing.py      # 不改
│   ├── schemas\
│   │   └── fusion.py                 # [MODIFY] FusionRunResponse/FusionResult 增量可选 natural_language（None 默认，旧前端不受影响）
│   ├── api\
│   │   ├── agent.py                  # [MODIFY] 仅透传新增可选字段；端点/既有字段不变
│   │   └── query.py / chat.py        # 不改
│   ├── main.py                       # [MODIFY] lifespan：recover_after_restart；capability_smoke_on_startup 为真且 endpoint 可达时执行冒烟（不可达自动跳过不阻塞）
│   └── db\session.py                 # [MODIFY] readonly 池参数化 + pool_pre_ping（默认值不变，仅支持配置放大）
├── scripts\
│   └── capability_smoke.py           # [NEW] 真机冒烟入口：三 endpoint Test1~4 + 常驻/并发检查；无权重输出 skipped 清单并以显式状态退出
└── tests\
    ├── test_tool_protocol.py         # [NEW] canonical 往返、畸形 tool_calls 归一
    ├── test_terminal_tool.py         # [NEW] submit 成功/校验失败回注重试成功/重试耗尽→schema_validation_failed/不重跑SQL
    ├── test_multi_turn_tool.py       # [NEW] tool_call_id 回注、连续两次工具调用
    ├── test_context_budget.py        # [NEW] preflight 触发确定性错误/压缩而非 Router 400
    ├── test_three_agent_concurrency.py  # [NEW] 三 Agent 并发重叠时序、会话隔离、确定性重排进融合
    ├── test_one_agent_failure_isolation.py  # [NEW] 单 Agent 失败不影响另两个、partial coverage 保留
    ├── test_narrator.py              # [NEW] 复用 agent-b、严格 JSON、候选白名单、失败兜底、契约向后兼容
    └── 既有测试                        # 保持零/最小改动通过
```

## 关键接口结构（实现约束）
```python
# app/inference/profiles.py —— 模型画像（单一事实源）
@dataclass(frozen=True)
class ModelProfile:
    id: str                       # agent-a / agent-b / agent-c
    endpoint_url: str             # http://127.0.0.1:8081/v1
    model_id: str
    context_size: int
    native_tools: bool
    multi_turn_tools: bool
    parallel_tools: bool
    thinking_enabled: bool
    terminal_tool_supported: bool
    chat_template: str | None
    max_tool_rounds: int
    max_sql_calls: int
    reserved_completion_tokens: int
    capability: Literal["ready", "tool_protocol_unsupported", "unverified", "skipped"] = "unverified"

# app/agents/protocol/contracts.py —— canonical 工具协议
class ToolDefinition(BaseModel): name: str; description: str; parameters: dict
class ToolCall(BaseModel): id: str; name: str; arguments: dict[str, Any]
class ToolResult(BaseModel): tool_call_id: str; name: str; success: bool
    content: str | None = None; data: dict | None = None; error: dict | None = None; truncated: bool = False
class AssistantTurn(BaseModel): content: str; tool_calls: list[ToolCall]; finish_reason: str | None
    usage: dict; timings: dict; model: str; protocol: str

# app/services/fusion.py —— 并发收集单元
@dataclass
class AgentOutcome:
    model_id: str; agent_run_id: UUID; loop_result: AgentLoopResult
    recommendation: Recommendation | None; verification: RecommendationVerification | None
    error_code: str | None; error: str | None
# 融合前一律按 configured_order 重排 AgentOutcome，再构造 AgentFusionInput。

# app/schemas/fusion.py —— 增量可选自然语言字段（向后兼容）
class NaturalLanguage(BaseModel):
    overview: str
    per_candidate: list[NarratorCandidateText]   # candidate_id 必须 ∈ top_k 候选
# FusionRunResponse / FusionResult 新增字段：natural_language: NaturalLanguage | None = None
```


## Agent 扩展
### SubAgent
- **code-explorer**
  - 用途：在并行化与 loop 改造前核对 `run_agent_loop`、`complete_chat`、`native_capability`、`SessionLocal`、`FusionService._run` 的全部调用点与测试依赖，确保签名/行为调整不遗漏调用方。
  - 预期结果：输出完整调用点清单与受影响测试集合，作为第 5 项改造的回归基线。
### Skill
- **lsp-code-analysis**
  - 用途：在引入 canonical 类型与 ModelProfile 时用语义导航定位符号定义/引用/调用层级，验证新抽象对既有引用与 schema 的兼容性。
  - 预期结果：每次改动前确认受影响文件集合与未使用/失效引用，避免破坏旧路径。
