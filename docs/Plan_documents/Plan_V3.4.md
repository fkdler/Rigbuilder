# RigBuilder Plan V3.4：可信数据链与 Conversation Terminal

> 状态：**软件修复完成，自动化验收通过；设备 B 三端点实机并行待验收。**
>
> 2026-09-10 自动化基线：后端 `209 passed, 1 skipped`；前端 Vitest `4 passed`；Playwright 五个强制场景在 desktop/mobile 共 `10 passed`；前端类型检查与生产构建通过。

## 0. 验收结论

V3.4 保留三 Agent、Truth Verification、Fusion、Job/SSE 与 Conversation 架构，已修复此前不能发布验收的两项问题：新会话现在先由服务端创建，完成后的 Job 结果也能从 Conversation detail 恢复。可信链、真实 Trace、部分 Agent 失败展示和启动配置门禁同步收紧。

当前仓库运行配置仍把三个 Agent 指向同一 `8081`，只能使用安全的串行回退。代码具备三端点并行能力，但在设备 B 的 `8081/8082/8083`、三个不同模型身份、capability smoke 和并发 Fusion 全部实测前，不得标记为“实施完成”。

## 1. Backend 与公共接口

- `POST /api/conversations`：返回 `201` 和服务端生成的 Conversation；新前端不再生成本地 UUID。
- `GET /api/conversations`：返回 `last_message_summary`、`latest_job_status`、`active_job_id`。
- `GET /api/conversations/{id}`：每个 Job 返回请求文本、完整 chat/fusion 判别式结果、NaturalLanguage、Agent 摘要和 trace summary；完成状态只读取 `QueryJob.status`。
- `DELETE /api/conversations/{id}`：运行中任务所属会话返回 `409 conversation_has_active_job`，取消后才允许软删除。
- `GET /api/query/jobs/{job_id}/trace`：从持久化 `AgentRun`、`ToolCall` 和 Verification 生成真实轨迹。只读 SQL 只在通过 Validator 且执行成功后进入深层详情；不返回数据库原始行、系统提示、内部消息或 reasoning content。
- 旧 `/api/chat`、`/api/agent/*`、Job/SSE 接口保持兼容。

## 2. 可信数据链

### 2.1 Evidence binding

- `row_bindings` 使用 `{entity_id, fields: {field_key: {value, unit, evidence_id}}}`；旧 `rows/columns/evidence/evidence_ids` 继续保留。
- 只有 entity、field 和 normalized value 全部相等才允许绑定，已删除回退到首条 evidence 的行为。
- rows、evidence、bindings 共用一个硬字符预算。超限按尾部行、字段、Evidence 联动裁剪；极小预算下宁可返回空结果，也不把超限 ToolResult 注入上下文。
- 每轮裁剪后重新过滤 Evidence，binding 不会指向已删除实体或字段。

### 2.2 Claim coverage

- 覆盖校验以候选为边界，禁止候选间共享 Claim。
- 价格、VRAM、功耗、百分比和常见 benchmark 数字同时校验数值与单位。
- 缺失、数值不一致或单位不一致时，`submit_recommendation` 返回结构化 validation error；同一 Agent 只能在既定轮次内修复，后端不补造 Claim。
- 未修复的 coverage 错误只使该 Agent 失败，其他 Agent 仍可参与 Fusion。

### 2.3 Price provenance

- `valid_evidence_ids` 只保存 `EvidenceClaim` ID。
- 价格快照写入独立的 `valid_price_snapshot_ids`，并公开 `observed_at/source/region/condition/price_type` provenance。
- missing/conflict price 不会标为 VERIFIED，也不会进入 Narrator 的 supported facts。

### 2.4 Narrator

- overview 只读取全局 coverage、参与模型数和最终排名。
- `per_candidate` 只读取本候选名称、分数和 supported claims；候选集合与顺序都必须和 FusionResult 一致。
- 已删除默认放行 1–10 数字。跨候选数值、无支持文本事实、非法顺序、超时或非法 JSON 均整体回退 deterministic narration。
- deterministic fallback 同样只读取 FusionResult 和 supported claims。

## 3. Conversation Terminal

- 新会话提问流程为 `POST /api/conversations` → `POST /api/query/jobs`。
- Store 分离 Conversation、Job/event 与 UI 状态；localStorage 只保存当前会话、模式和 UI 偏好。
- 启动时恢复所有 `active_job_id`，为每个活动 Job 重连 SSE；切换会话不会取消或关闭其他会话的连接。
- Job 终态后刷新 sidebar 和 detail；进入已完成会话直接从完整结果恢复，缺失时再请求 Job API。
- 删除失败不会提前移除本地项，409 与其他后端错误会保持服务器/UI 一致。
- Timeline 同时显示用户请求、chat 回复和 fusion 结果；NaturalLanguage 同时显示 overview 与对应候选 explanation。
- Facts 读取最终 `FusedCandidate.claims`；Agent 摘要读取 SSE/最终 Agent 结果；TraceExpand 读取 `/trace`，不再用 claims 伪造 ToolCalls。
- 单 Agent 失败保留错误，并按实际 `1/3`、`2/3` coverage 展示。
- Header 使用紧凑 `mode:` 控件，sidebar 改为平坦行结构；补齐键盘操作、label、aria-live、可见 focus、44px 目标和 reduced-motion。

## 4. 自动化验收

### 后端

- Conversation create/list/detail/delete/409 使用真实 FastAPI 请求覆盖。
- 覆盖新会话首问、完整 chat/fusion 序列化、完成/运行 Job 恢复和真实 trace。
- Evidence 覆盖值不匹配、跨实体/字段与合计预算。
- Claim 覆盖跨候选污染、错误数值、错误单位、百分比和有限修复失败。
- Narrator 覆盖跨候选数值、1–10 幻觉、候选顺序、文本事实与 deterministic fallback。
- Price 覆盖 snapshot provenance 与 Evidence ID 类型隔离。

结果：`209 passed, 1 skipped`。跳过项是需要显式真实 PostgreSQL 环境的集成测试。

### 前端

- Vitest + Vue Test Utils：`4 passed`，覆盖服务端会话创建、删除失败一致性、overview/per-candidate、partial failure 和真实 trace。
- Playwright desktop + mobile：`10 passed`，即下列五个场景各在两种视口执行：
  1. A 运行时切换 B，A 不被取消且可恢复。
  2. 刷新后恢复 running SSE 或 completed result。
  3. Conversation 删除与运行中删除冲突。
  4. Narrator 失败时 deterministic presentation 可用。
  5. 单 Agent 失败时结果、coverage 和错误描述正确。

## 5. 设备 B 待验收项

1. 启动三个独立 `llama-server`：`8081/8082/8083`，三个不同 `/models` 身份；仓库不硬编码本地 GGUF 路径或量化。
2. 每个服务确认 `--parallel 1 --ctx-size 12288`、一个 slot，并记录 llama.cpp 版本与显存峰值。
3. 三个 profile 分别通过 terminal tool、ToolResult 回注和连续工具调用 capability smoke。
4. 设置 `AGENT_PARALLEL_REQUIRED=1` 与 `CAPABILITY_SMOKE_ON_STARTUP=1`；端点重复、模型身份重复或 smoke 失败必须阻止以“并行就绪”启动。
5. 实测并发 Fusion、单端故障隔离、Agent coverage、SQL/LLM/验证/Fusion/总耗时与前端/数据库一致。

只有本节全部通过后，状态才能从“软件修复完成并待实机验收”改为“实施完成”。
