# RigBuilder Plan V3.2：Agent 实机闭环、问题复盘与后续路线

> 状态快照：2026-09-09。前置版本：[Plan_V3.1.md](Plan_V3.1.md)。
>
> 本文以当前仓库、Truth DB 正式 Release、设备 A/B 联调、异步 Job/SSE 实测和自动化测试为准。它区分“代码已实现”“离线测试通过”“设备实测通过”和“仍需验收”，不把目标值写成完成结果。

## 0. 版本结论

V3.2 已经形成可运行的核心 MVP：用户问题可以自动分流到快速聊天或三 Agent 可信融合；Agent 能调用只读数据库工具，输出结构化 Recommendation，经后端 Truth Verification 和确定性 `fusion-v1` 门禁后，在前端展示候选、证据、可信度、执行轨迹和失败原因。

与 V3.1 相比，真实 LLM、三模型 Router、原生工具调用、持久任务、SSE 轨迹、模型复用和结构化前端均已进入实现并完成至少一次设备联调。项目不再处于“LLM 尚未连接”的状态。

项目仍不能视为最终完成。当前主要缺口已经从“链路不存在”转为“多模型稳定性、融合质量评估、数据覆盖、专用决策结果存储和完整浏览器验收”。特别是：单个有效 Agent 即可产出融合结果，但 33% Agent coverage 不应直接等同于高质量三模型共识。

## 1. 当前系统基线

### 1.1 部署拓扑

```text
浏览器前端
  -> FastAPI（设备 A，单 worker）
     -> PostgreSQL Truth DB / Job 与审计表
     -> 全局推理调度锁
        -> llama.cpp Router（设备 B，RTX 3090 Ti 24GB）
           -> agent-a / agent-b / agent-c，按需加载且同时仅驻留一个模型
```

设备 B 的当前推荐启动基线：

```powershell
.\llama-server.exe `
  --models-max 1 `
  --parallel 1 `
  --ctx-size 12288 `
  --models-preset "D:\code\Ground-Turth System\llama-b10630-bin-win-cuda-12.4-x64\models.ini" `
  --host 0.0.0.0 `
  --port 8080
```

不得同时添加 `--models-dir`，否则 Router 会同时暴露目录模型 ID 和 preset ID，增加模型选择及加载歧义。`models.ini` 的 section ID 必须与 `/models`、设备 A `.env` 中的 `agent-a/b/c` 完全一致。

固定使用 `--parallel 1`。此前看到的 `n_slots=4 / kv_unified=true` 来自未显式限制 parallel；它会把上下文和 KV 资源按多个 slot 规划，不适合当前单请求、单 GPU、`models-max=1` 的部署方式。

### 1.2 上下文与显存边界

- Agent 初始提示曾约为 5219 tokens，因此 `c=4096` 不足，可能在首轮工具调用前就截断重要约束。
- 当前 Router 固定 `ctx-size=12288`，运行时应只有一个 slot。
- Agent 可见数据库 Schema 已从 21,453 字符压缩为 8,253 字符，约为原来的 38.5%。
- `inspect_database` 也只返回紧凑 SQL 契约，避免系统提示和工具结果重复注入完整注册表元数据。
- 前端显示的 Agent token 数是多轮请求的累计 usage，不是单个 slot 的实际上下文占用。例如 39,899 tokens 可能是四轮 prompt/completion 累加；判断是否超过上下文应查看每一轮的 `prompt_tokens`。
- 24GB 显存只能保证部署边界明确，不能保证所有 Q8 模型始终可加载。模型权重、CUDA 工作缓冲、KV cache、桌面占用和驱动保留必须共同计入。

### 1.3 当前正式数据与读取边界

- 当前实测 Release：`rigbuilder-v3-2-2026-09-07`。
- `gpu_catalog` 已能查询 RTX 5070、5070 Ti、5080、5090、5090D，NVIDIA 规范键为 `org:nvidia`。
- Agent 只能读取 `agent_catalog` 注册视图，只允许单条只读 `SELECT` 或 `WITH ... SELECT`。
- 候选 ID 必须来自查询结果中的 `entity_id`；`query_id` 不是事实证据 ID。
- Fact Claim 必须由同实体、同字段的 accepted Evidence 支持；模型自报分数和置信度不构成真值。

## 2. 已完成并已验证

### 2.1 Truth DB、验证与融合

- Truth DB V3.1/V3.2 基础结构、正式 Release、Agent 只读视图和权限边界已建立。
- Fact、Measurement、Derived、Preference Claim 的后端验证框架已实现。
- `fusion-v1` 已实现实体门禁、事实冲突门禁、硬约束门禁、软偏好效用、共识、可信度和可重放 trace。
- 不存在或伪造的候选 UUID 不再因为 Recommendation JSON 可解析而被判定为成功。至少一个候选必须满足 `candidate_valid=true`，否则 Agent 归类为 `no_verified_candidates`，不得进入融合。
- 失败 Agent 不进入共识分母，但会降低 `agent_coverage`；融合输入在执行后恢复配置顺序，模型加载顺序不会改变确定性排名。

### 2.2 LLM Gateway 与有界 Agent

- LLM Gateway 返回结构化调用结果，包括 content、tool calls、finish reason、模型、usage、llama.cpp timings、调用模式和总耗时。
- 原生请求启用 prompt cache、关闭 thinking/reasoning，并关闭并行工具调用。
- 工具阶段最多生成 512 tokens；最终结构化结果最多生成 2048 tokens。
- 原生 Agent 最多执行 3 个工具轮次、4 次 SQL；legacy 最多 4 轮和一次格式修复。
- 原生 `tool_calls` 会带 `tool_call_id` 回注 assistant/tool 消息。
- SQL 校验失败作为正常工具结果返回，不会错误触发协议回退。
- 模型原生能力按进程缓存，API 重启后重新探测。
- 原生工具已经成功读取数据后，如果后续 JSON Schema、模板或 Router HTTP 协议阶段失败，只允许基于已有 Observation 做一次无工具 legacy finalization；不得从原始提示重新执行 SQL。
- 对模型结构输出的修复受已返回证据约束：只有实体和值唯一匹配时才能纠正 evidence/field 映射；无证据 Claim 会被移除，重复候选会合并，绝不生成事实证据。

### 2.3 自动路由、持久任务和实时事件

- `auto / chat / fusion` 三种模式已实现，显式模式优先于自动判断。
- “你好”“你能做什么”等能力或解释类问题走快速聊天；同时具有硬件领域词和推荐、预算、兼容、部署等决策意图时走可信融合。
- `query_job` 和 `query_event` 已持久化请求、解析模式、状态、结果、错误、生命周期和有序事件。
- 已提供创建任务、读取任务、SSE 续传事件和幂等取消接口。
- SSE 支持 `Last-Event-ID/after`、heartbeat、断线重连；前端连续失败后降级为轮询。
- 服务重启会把遗留 queued/running 任务标为 `server_restarted`，当前单进程版本不自动重复执行。
- 取消任务会取消实际 asyncio task 并释放推理调度锁，不再只是终止浏览器等待。

### 2.4 前端交互与可观测性

- 首页已提供自动、快速问答和可信融合模式选择，并显示后端最终解析模式。
- active job ID、最后事件 sequence 和会话可恢复；刷新页面后可以续接任务状态。
- 执行轨迹覆盖排队、模型加载、LLM、数据库、验证、Agent、融合和任务终态。
- 每个 Agent 展示轮次、工具次数、累计 token、协议和成功/失败状态。
- 最终结果使用 Recommendation Card 和 Trace Viewer，不再直接输出原始 JSON。
- `top_k=[]` 时会明确显示“没有候选通过 Truth DB 验证”，同时展示失败 Agent、错误码和被淘汰候选。
- 部分 Agent 失败时会显示覆盖率警告，避免把单 Agent 结果误解为三模型一致结论。
- 快速聊天明确不具备实时数据库查询能力，不应再根据模型旧知识断言 RTX 50 系“尚未发布”；需要当前硬件事实时提示改用可信融合。

### 2.5 自动化验证

截至本快照：

- 后端：`141 passed, 1 skipped`。
- 前端：TypeScript 类型检查与生产构建通过。
- 覆盖内容包括原生 tool calls、tool_call_id 回注、Schema 输出、协议回退、工具轮次上限、SQL Validator、自动路由、loaded-first 调度、Job 状态、SSE 顺序/重连、取消和旧同步接口兼容性。
- 唯一跳过项仍是需要显式环境开关的真实 PostgreSQL 冒烟测试，不代表对应能力不存在，但应在发布验收中单独执行。

## 3. 设备 A/B 联调复盘

### 3.1 失败样本：任务完成但无可见结果

原始联调任务约 87.3 秒结束：

| Agent | 表面状态 | 实际问题 |
|---|---|---|
| agent-b | completed | 返回伪造候选 UUID；Truth DB 中 `candidate_not_found`，但后端错误地把“已生成 Verification 对象”当作通过 |
| agent-a | failed | Router 在加载或执行模型时返回 HTTP 500 |
| agent-c | failed | 原生阶段已有查询结果，但协议回退从原始提示重跑 legacy，累计 7 次数据库工具调用，最终达到 round limit |

融合接收了 agent-b 的无效输入，随后候选在 FusionEngine 中被淘汰，得到 `top_k=[]`。旧前端只循环渲染 `top_k`，所以页面只留下“0 verified”和审计信息，看起来像“任务完成但没有最终输出”。

这一样本确认了四个问题：

1. Agent 成功判定缺少 Truth DB 有效候选门禁。
2. 原生工具成功后的协议回退可能重复执行只读工具。
3. 完整 Schema 重复注入增加上下文和 prompt eval 压力。
4. 前端没有为空结果设计真实、可操作的终态。

四项均已在 V3.2 修复并加入回归测试。

### 3.2 成功样本：RTX 5070

修复后使用异步 Job API 实测同类问题“推荐一款 50 系NV甜品显卡”：

- 总耗时约 151.6 秒。
- loaded-first 使当前已加载的 agent-c 首先执行。
- agent-c 只执行 2 次数据库查询：第一次返回 5 个 RTX 50 系候选，第二次返回 2 条价格信息。
- 最终返回数据库实体 `GeForce RTX 5070`。
- VRAM、显存类型、板卡功耗、架构 4 条 Fact Claim 均为 `supported`。
- `fact_support=1.0`、`proof_coverage=1.0`、`information_completeness=1.0`、`credibility=90.0`。
- 本次融合只有 agent-c 成功，因此 `agent_coverage=1/3`；结果可发布，但属于部分覆盖，不代表三模型共识。
- agent-c 每轮 prompt tokens 最高约 7052，低于 12288；累计 token 较大不等于上下文溢出。

随后对最终结构修复后的 agent-b 做单 Agent 实测：

- 状态 `completed`。
- 3 次数据库工具调用。
- Truth DB 验证通过。
- 说明此前 agent-b 的失败主要是 Claim/重复候选结构问题，而不是数据库缺少 RTX 50 系数据。

### 3.3 OOM 与模型加载问题结论

此前 Ornith 加载失败的直接日志是 CUDA 申请约 8045 MiB 工作缓冲失败，发生在 KV cache 建立前，因此不是 `ctx-size=12288` 单独造成。Windows `nvidia-smi --query-compute-apps` 对 WDDM 图形进程经常返回 `[N/A]`，不能据此断言显存空闲。

当前可运行配置证明 3090 Ti 24GB 能运行该 Router 基线，但以下因素仍会让单个模型偶发加载失败：

- 桌面、浏览器、LM Studio、Overlay 等 WDDM 图形占用；
- Q8 权重加 CUDA 工作缓冲接近显存上限；
- 前一个模型退出后显存尚未完全回收；
- 特定模型/模板在 tools 或 JSON Schema 请求中触发 llama.cpp 500；
- preset 参数或模型文件格式与当前 llama.cpp build 不兼容。

因此 Router HTTP 500 必须结合设备 B 上同一时刻、同一模型的前置日志判断，不能只看设备 A 的统一错误信息。

## 4. 当前仍存在的问题

### 4.1 agent-a 尚未完成稳定性闭环

最近一次完整融合中，agent-a 已成功加载并完成多个 LLM 轮次，但生成了两个无效 SQL：使用不存在的 `product_family` 列，以及把 UUID 传给错误的价格字段。修正查询后，最终 JSON Schema 阶段仍出现 Router HTTP 500。

代码已经增加 Schema HTTP 失败的一次性无工具 finalization，但还需要在设备 B 上重新完成 agent-a 专项实测。若仍失败，应保存 Router 原始响应和模型子进程退出前日志，判断是模板兼容、Schema grammar、上下文还是显存问题。

### 4.2 模型输出质量仍不一致

不同模型对同一 Schema 的服从能力差异明显：

- 可能遗漏 `field_key/evidence_ids`；
- 可能把价格证据写成无规则的 derived claim；
- 可能返回重复候选；
- 可能无视系统提示中的列名，生成不存在的字段；
- 某些模型支持原生 tools，但不稳定支持严格 JSON Schema。

当前后端只做可证明、可审计的结构修复，不能替模型创造缺失事实。未来模型评估应分别统计工具成功率、SQL 首次正确率、Schema 首次通过率、修复率和候选有效率，而不能只统计“接口返回 200”。

### 4.3 融合耗时仍高

151.6 秒的成功样本虽然未超时，但离交互式推荐仍偏慢。SQL 仅为毫秒级，主要耗时来自：

- 三个模型按需加载和卸载；
- 每个 Agent 多轮 prompt eval；
- 最终 JSON Schema 生成及修复；
- 失败模型仍消耗完整的加载和部分推理时间。

在 `models-max=1` 和单 GPU 约束下，一次三模型融合至少需要两次必要模型切换，不能通过前端伪进度或单纯增大超时解决。

### 4.4 33% coverage 的产品语义需要进一步冻结

当前设计允许一个 Agent 成功、两个 Agent 失败时继续融合，这是为了可用性和故障隔离。但此时 consensus 在有效 Agent 集合内可能为 1.0，用户容易误读为“三模型一致”。前端已展示部分覆盖警告，后续还需要明确发布策略：

- 交互模式可以返回单 Agent 通过 Truth DB 的结果，但标记“低覆盖”；
- 高可信模式可要求至少 2/3 Agent 产生有效候选；
- credibility 或最终等级应对 coverage 设置更直观的上限/分级，而不只依赖公式中的乘项。

该策略变更必须通过固定评估集验证后再调整 `fusion-v1`，不能临时改公式。

### 4.5 数据与评估仍不完整

- Benchmark Run、Metric Definition、长尾 Entity Attribute 和生产 Derived Rule 的覆盖仍不足。
- 当前 RTX 50 系存在规格和部分价格数据，但“甜品级”本身是偏好/价值判断，需要预算、地区、分辨率、功耗和工作负载才能形成更可靠的排序。
- 尚未建立覆盖问候、硬件推荐、兼容性、本地模型部署、无数据、冲突证据和模型失败的固定端到端评估集。
- 尚未量化三 Agent 相对最佳单 Agent 的 Recall/NDCG、事实支持率收益和延迟成本。

### 4.6 持久化边界尚未最终完成

`query_job.result_json` 已能保存任务最终结果，Agent Run、Message、Tool Call、Event 和 metrics 也可追溯。但专用的 Decision Result / Presentation Result 尚未实现，当前仍缺少：

- 决策候选、评分分量和最终呈现的规范化长期表；
- Prompt 版本、模型文件哈希/量化、llama.cpp build、规则版本与 Release 的完整绑定；
- 独立于运行日志、可稳定重放和导出的用户决策记录；
- 受限 Narrator 或模板化自然语言最终说明。

### 4.7 部署能力仍限于单进程

当前 Task 注册表在内存中，持久状态和事件在 PostgreSQL 中。该方案只支持单 FastAPI worker；多 worker 会造成任务所有权和取消路由不一致。引入多 worker 前必须先迁移到外部任务队列或数据库租约执行器。

### 4.8 前端仍需完整浏览器验收

当前已通过类型检查和生产构建，但仍需在真实桌面与窄屏浏览器完成：

- SSE 断网、重连、去重和轮询降级；
- 页面刷新后的 active job 恢复；
- 真实取消与锁释放；
- 键盘导航、focus、`aria-live`；
- 长模型名、长错误信息、空结果和淘汰候选展开；
- Recommendation Card 是否给出足够清晰的“推荐原因、风险和证据”，而不只展示评分。

## 5. 项目整体完成情况

以下以“是否达到可验收成果”描述完成度，不使用没有统计依据的总百分比。

| 工作流 | 当前状态 | 结论 |
|---|---|---|
| Web 前后端与会话 | 已完成 | 可运行、可恢复会话，快速聊天实测可用 |
| Truth DB V3 与正式 Release | 核心完成 | 正式 Release 和 GPU 数据可用；Benchmark/Attribute/Rule 数据仍需扩展 |
| 只读 SQL Agent | 已完成并实测 | 原生工具、Validator、审计、有界循环可用 |
| Truth Verification | Fact 闭环完成 | RTX 5070 实机 Claim 验证通过；Measurement/Derived 真实样例不足 |
| 三 Agent 调度与融合 | 核心完成 | 串行锁、loaded-first、部分失败和确定性融合可用；三模型全成功尚未稳定 |
| Job/SSE/取消 | 已实现 | 自动化测试通过；真实断网/重启/取消浏览器验收待补 |
| 结构化前端结果 | 核心完成 | 候选、trace、失败与空结果可见；需要继续完善最终自然语言解释 |
| 性能指标 | 基础采集完成 | 已有 load/LLM/tool/verification/fusion/total；尚未形成评估报表与发布门禁 |
| 离线质量评估 | 未完成 | 尚无冻结问题集、基线模型和融合收益结论 |
| 专用 Decision/Presentation Result | 未完成 | 当前由 Job JSON 承载，不是最终长期契约 |
| 生产化部署 | 未完成 | 仍是单 worker、单 GPU、开发级本地部署 |

按课程项目目标判断，系统已经满足“多个本地大模型、Web 可操作、官方数据、结构化推荐、事实验证、可解释 trace、资源与延迟指标”的演示基础。要达到可重复实验和稳定交付标准，还必须完成三模型稳定性、固定评估集、数据扩充、浏览器验收和结果版本追溯。

## 6. V3.2 后续实施路线

### P0：完成当前联调闭环

目标：让三个配置 Agent 分别在相同问题集上稳定结束，并明确协议能力。

- 对 agent-a、agent-b、agent-c 分别执行原生 tool-call 和 JSON Schema 冒烟测试。
- 保存每个模型的 tools 支持、Schema 支持、legacy fallback、首轮 SQL 正确率和错误日志。
- 重新执行至少两次连续 A→B→C 融合，确认每个模型单任务最多加载一次，第二次融合最多发生两次切换。
- 对 agent-a 的 Schema HTTP 500 做专项复测；仍失败时建立该模型固定 legacy-finalize capability，而不是每个任务重复探测。
- 验证一次运行中 SQL 总数绝不超过 4，协议回退不重复已成功工具。

完成门槛：三个 Agent 都能给出明确的 `completed` 或可解释、可复现的兼容性失败；不得出现无终态、重复工具和伪成功。

### P1：建立端到端评估集与发布门禁

目标：从“能跑”转为“能够比较、回归和证明”。

- 冻结不少于以下类别的问题：快速问候、系统能力、GPU 推荐、预算推荐、显存部署、硬件兼容、无数据、冲突数据、非法 SQL、模型加载失败。
- 每个问题保存期望路由模式、必选实体/字段、禁止断言、最大 SQL 次数和最大时延。
- 分模型统计工具调用成功率、SQL 首次正确率、候选有效率、Schema 首次通过率、事实支持率和 P50/P95 时延。
- 比较单 Agent 与三 Agent 的 Recall@K、NDCG、硬约束满足率、Claim 支持率、coverage 和总延迟。
- 在有数据前不引入模型可靠性权重；先保留 equal weights 作为可解释基线。

完成门槛：每次 Prompt、模型、Schema、融合策略或数据 Release 变更都能运行同一评估集，并阻止关键指标回退。

### P2：优化延迟与模型生命周期

目标：在不降低真值门禁的前提下减少融合等待。

- 分离并展示真实的 model load、prompt eval、generation、tool、verification、fusion 和 queue 时间。
- 根据评估结果缩短工具提示和最终 Schema，减少无效 Claim 与重复候选造成的 repair。
- 对模型建立 capability profile，直接选择 native-schema、native-tools+legacy-finalize 或完整 legacy，避免运行时反复试错。
- 研究是否能在保持 `models-max=1` 的条件下安全预热下一模型；任何预热不得破坏全局串行锁和显存上限。
- 若未来更换更大显存设备，再评估 `models-max>1`；当前 3090 Ti 不把多模型常驻列为默认方案。

建议性能目标需在 P1 基线后冻结。现阶段只保留已验证目标：快速问答首事件小于 500ms，热响应目标小于 10 秒；融合不得使用伪进度，且必须在预算内给出明确终态。

### P3：完善数据、Measurement 与 Derived 验证

目标：使“性能和推荐价值”不只依赖静态规格。

- 补齐 Metric Definition、Benchmark Protocol/Run/Result。
- 为 RTX 30/40/50、代表性本地模型和量化变体采集可复现实测。
- 引入地区、币种、时间和库存限定的价格比较。
- 为功耗、显存余量、模型可运行性等 Derived Claim 注册版本化规则。
- 扩展长尾 Entity Attribute，并验证不修改数据库列即可新增字段。

完成门槛：至少一个硬件推荐同时具有 supported Fact、Measurement 和版本化 Derived Claim，并能从前端追溯到 Release、Evidence、Benchmark 和 Rule。

### P4：冻结 Decision Result 与最终说明

目标：把运行日志与用户可保存的决策成果分离。

- 新增规范化 Decision Result，保存候选、门禁、评分分量、淘汰原因和完整版本绑定。
- 新增 Presentation Result 或确定性模板，生成面向用户的推荐摘要、适用场景、风险、缺失信息和替代方案。
- 如引入 Narrator LLM，只允许改写语言，不得修改候选、排名、数值、事实状态和风险。
- 提供稳定的查询、导出和重放接口。

完成门槛：同一个 Decision Result 在不重新调用 LLM 的情况下可以重复生成一致的核心结论，并能解释结论基于哪个数据、模型、Prompt 和策略版本。

### P5：完成前端与部署验收

目标：达到可演示、可恢复、可诊断的交付状态。

- 完成桌面与窄屏浏览器验收和无障碍检查。
- 为断网、Router 不可用、模型加载失败、协议回退、Agent timeout、取消和服务重启提供清晰错误文案与恢复动作。
- 增加历史 Decision 列表、详情和证据查看，但不向用户暴露内部思维链、完整 SQL 或数据库原始行。
- 冻结单 worker 部署手册、备份恢复、日志采集和设备 A/B 启停检查。
- 若需要多 worker，再引入外部队列；不得直接复制当前内存 Task 注册表。

完成门槛：新环境按 Quick Start 可独立部署，验收人员能完成快速聊天、可信融合、刷新恢复、取消、失败诊断和历史结果查看。

## 7. 下一轮推荐执行顺序

```text
agent-a Schema/HTTP 500 专项复测
-> 三模型逐个 capability 冒烟测试
-> 连续两次完整融合与加载顺序验收
-> 冻结最小端到端问题集和当前指标基线
-> 修复评估集中最频繁的 SQL/Schema 失败
-> 完成桌面与窄屏浏览器验收
-> 扩充 Benchmark/Metric/Rule 数据
-> Decision Result / Presentation Result
-> 融合质量校准与性能优化
-> 最终部署、实验报告和演示验收
```

## 8. V3.2 验收清单

### 已满足

- [x] 快速问题可自动走 chat，并保留会话上下文。
- [x] 推荐/部署决策可自动走 fusion，用户可手动覆盖模式。
- [x] Router 使用 `models-max=1 / parallel=1 / ctx-size=12288` 基线完成真实推理。
- [x] 至少一个配置模型完成原生数据库工具调用、结构化输出、Truth Verification 和融合。
- [x] RTX 50 系数据可从 Truth DB 查询，未再错误断言“尚未发布”。
- [x] Agent loop 有工具轮次、SQL 次数、格式修复和总时限边界。
- [x] 原生协议回退不重复已成功数据库工具。
- [x] 无效候选不得以 Agent completed 身份进入融合。
- [x] Job、Event、SSE 重连、轮询降级和真实取消已实现。
- [x] 前端能展示执行轨迹、结构化候选、失败 Agent、淘汰原因和空结果终态。
- [x] 后端自动化测试与前端生产构建通过。

### 尚未满足

- [ ] agent-a/b/c 在固定评估集上分别完成稳定 capability 验收。
- [ ] 连续两次三模型融合达到模型加载复用验收要求。
- [ ] 固定端到端评估集和质量/性能发布门禁建立。
- [ ] 至少 2/3 Agent coverage 的高可信模式或分级策略冻结。
- [ ] Measurement、Derived Rule 和 Benchmark 真实数据闭环。
- [ ] 专用 Decision Result / Presentation Result 和完整版本追溯。
- [ ] 桌面、窄屏、断网恢复、取消和服务重启浏览器验收。
- [ ] 新环境从零部署、备份恢复和最终演示脚本验收。

## 9. V3.2 完成定义

V3.2 的核心工程实现已经完成，但版本验收尚未全部结束。只有当三个模型的能力边界可复现、固定评估集可阻止质量回退、真实浏览器流程完成验收、数据能够支持性能/价值判断、最终决策可按版本重放时，项目才从“可运行可信推荐 MVP”进入“可重复实验与稳定交付”状态。

系统的最终成功标准仍是：给出合理输入和合理推荐输出，同时能够说明候选来自哪个 Truth DB 实体、哪些事实已被官方 Evidence 或本地 Benchmark 支持、哪些信息仍缺失、可信度如何计算、失败发生在哪个阶段，以及结论绑定哪个数据、模型、Prompt 和规则版本。
