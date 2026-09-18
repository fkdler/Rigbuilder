# RigBuilder Plan V2.3：验收闭环、推荐可信化与产品接入

> 状态：规划中。日期：2026-08-31。前置版本：[Plan_V2.2.md](Plan_V2.2.md) 的代码实现已提交，但尚未完成真实 PostgreSQL / LLM 的集成验收。

## 1. 版本目标与边界

V2.3 的目标不是重做 V2.2 的 SQL Agent，而是把它变成可验证、可展示、可评估的产品能力：

```text
用户界面
  -> POST /api/agent/run
  -> 受控 SQL Agent（V2.2）
  -> Truth Verification（事实与证据校验）
  -> Recommendation Fusion（多模型结果融合）
  -> 可解释结果 + 可复现评估记录
```

本版本分为四条工作流：

1. **V2.2 收尾与真机验收**：真实 PostgreSQL 的安全、迁移、性能和审计验证。
2. **前端接入**：将 Agent 请求、运行状态、推荐结果和错误状态呈现给用户。
3. **推荐可信化与融合**：验证 claim / evidence，再融合单模型或多模型的合规 Recommendation。
4. **离线评估**：用固定数据集和日志计算 SQL、事实、推荐质量指标。

不在 V2.3 范围：自动硬件数据采集、任意外部数据库访问、跨进程模型调度、训练新模型。

## 2. 当前基线与完成定义

V2.2 已有 Recommendation Schema、白名单 SQL Validator、只读连接设计、单模型 Agent、审计表和 `/api/agent/run`。这些仅通过了不依赖真实数据库与模型的单元测试，不能替代下列验收。

V2.3 的“完成”必须同时满足：

- 真实数据库验收矩阵全部通过，或有明确的环境阻塞记录；
- 前端能完整处理成功、失败、无结果和超时情形；
- 最终 recommendation 的 claim 可以追溯到同实体、同字段、同值的 evidence；
- 融合结果保持 Recommendation Schema 合法，并保留来源模型与决策依据；
- 固定离线集的运行结果、指标和原始审计 ID 可复现。

## 3. Phase 0：V2.2 正确性修复与 PostgreSQL 真机验收

### 3.1 必须先修复的实现缺口

- `Recommendation` 中的 `candidate_id`、`claims[].entity_id` 与 `evidence_ids` 改为 UUID 类型。
- 新增 claim 验证服务：`entity_type/entity_id/field/value` 必须与 `evidence` 中同实体、同字段且 `normalized_value` 相等的记录匹配；没有匹配证据时不能作为已证实 claim 输出。
- 反查 `evidence_ids` 时按实体和字段约束；明确自然键/非 UUID 查询的返回语义。
- `create_agent_readonly.py` 改为幂等：重复执行不失败，并仅授予五张白名单表的 `SELECT` 权限。
- 使 `tool_call.agent_message_id` 指向触发该工具调用的 assistant message；必要时补齐 system / user / tool 的 Run 内审计记录。
- 明确 `LIMIT ALL`、无界 `OFFSET` 与未覆盖危险函数的策略；新增相应测试。
- 验证连接参数 `default_transaction_read_only` 在目标 `psycopg` / PostgreSQL 版本上有效；若无效，采用连接后显式设置只读事务的实现。

### 3.2 真实数据库验收矩阵

| 编号 | 场景 | 预期证据 |
| --- | --- | --- |
| DB-01 | `alembic upgrade head` | 迁移成功，三张审计表存在且约束有效 |
| DB-02 | `agent_readonly` 查询五张允许表 | 合法 SELECT 成功 |
| DB-03 | 用 `agent_readonly` 执行 INSERT / UPDATE / DELETE / DDL | 均被 PostgreSQL 拒绝 |
| DB-04 | 慢查询或 `pg_sleep` | Validator 拦截，或执行层在 3 秒内中断；不得泄漏数据库错误细节 |
| DB-05 | 超过 100 行的合法查询 | 返回 100 行、`truncated=true`，审计记录一致 |
| DB-06 | 非白名单表、多语句、注释绕过、危险函数 | 均被 Validator 拒绝并产生 `tool_call` 审计记录 |
| DB-07 | 一次完整 Agent Run | `request_id -> agent_run -> agent_message/tool_call -> query_id` 可还原 |
| DB-08 | 合规 Recommendation | claim 的 evidence 可按实体、字段和值反向核验 |

### 3.3 数据库仅在特定设备时的协作规则

数据库访问设备称为 **DB 验收机**。其他开发者不以“连不上数据库”为由跳过测试，而采用分层测试和证据回传。

| 测试层 | 执行人 / 环境 | 允许做什么 | 产物 |
| --- | --- | --- | --- |
| 单元测试 | 所有开发者本地 | Validator、Schema、Loop、Mock DB 分支 | `pytest` 结果 |
| 离线迁移检查 | 所有开发者本地 | Alembic SQL 生成、模型 / 迁移一致性检查 | 命令输出 |
| 数据库集成测试 | 仅 DB 验收机 | DB-01 至 DB-08，使用专用测试库和只读账号 | 脱敏测试报告、审计 ID、失败日志 |
| LLM 端到端测试 | 有推理服务且可访问 DB 的验收环境 | 固定提示词集实际运行 | 脱敏 Recommendation 与指标 |

约束：

- 凭据只保存在 DB 验收机的 `backend/.env` 或受管密钥系统，绝不提交、复制到测试输出或发到聊天记录。
- 集成测试使用独立测试数据库 / schema 与独立 `agent_readonly` 账号；禁止用生产数据和生产角色做破坏性测试。
- 对于未在 DB 验收机运行的 PR，CI 至少执行单元测试；需要 DB 语义的检查标注为 `requires-db`，不能伪报为通过。
- DB 验收机每次验收应保存：提交 SHA、环境版本（PostgreSQL / psycopg）、测试时间、通过项、失败项、脱敏错误与相关 `request_id`。报告写入受版本控制的 Markdown，不写入连接串或原始敏感数据。

建议的验收机命令顺序（实际 URL 仅从本机 `.env` 读取）：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m scripts.check_db
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m scripts.create_agent_readonly --apply
..\.venv\Scripts\python.exe -m pytest -m requires_db
```

在引入 `requires_db` 测试标记前，最后一条命令不应被写入自动化验收说明；先实现测试与 marker，再启用该命令。

## 4. Phase 1：前端 Agent 体验

### 4.1 最小可用界面

- 输入需求、发起 `/api/agent/run`、禁用重复提交并显示运行中状态。
- 成功时展示按 score 排序的候选项、理由、风险、置信度和信息不足项。
- 提供“证据与审计”折叠区：展示可安全公开的 `request_id`、`agent_run_id`、工具调用次数与截断提示；不展示 SQL 内部错误、连接信息或未脱敏原始数据。
- 对 `database_not_configured`、超时、模型不可用、格式失败、空结果分别给出用户可行动的提示。

### 4.2 前后端契约

- 以前端类型定义镜像 `AgentRunRequest/AgentRunResponse` 和 Recommendation JSON Schema；不得自行猜测字段。
- 在没有 DB 验收机的本地开发中，用受版本控制的 mock response / MSW fixture 驱动 UI；fixture 与真实 API 契约测试同步维护。
- 增加成功、失败、截断、无结果四条组件 / 端到端测试路径。

完成标准：本地无需数据库即可演示全部状态；接入验收环境时同一 UI 能渲染真实合规响应。

## 5. Phase 2：Truth Verification 与推荐融合

### 5.1 Truth Verification（先于融合）

输入是单模型 Recommendation 与该 Run 的 Observation / evidence；输出是验证后的 Recommendation 和逐项 verdict。

- 验证 candidate 实体存在，candidate_type 与实体表一致。
- 每个 claim 校验 entity、field、JSON 类型和值，与 evidence 的 `normalized_value` 匹配。
- 将无证据、字段不匹配、值不一致的 claim 标记为 rejected；不得静默保留。
- 定义策略：若 Top-1 有关键 claim 被拒绝，降级候选、要求模型重试或返回 `insufficient_information`。
- 将 verdict、拒绝原因和关联 evidence ID 持久化，作为评估输入。

### 5.2 Recommendation Fusion

V2.3 先支持“可配置的 1..N 个模型结果”融合，默认 N=1，避免把多模型部署作为前置阻塞。

- 只接收已通过 Schema 与 Truth Verification 的候选。
- 以 `candidate_type + candidate_id` 聚合；不同模型的分数先归一化，再按离线评估得到的模型权重聚合。
- reasons / risks 去重并保留来源；不可融合或证据冲突时明确呈现冲突，不用平均值掩盖。
- 输出仍须符合 Recommendation Schema，并增加内部可审计的 fusion trace（来源 Run、权重、验证 verdict）。
- 模型自报 `model_confidence` 只用于展示，不作为融合权重；权重来自离线评估。

完成标准：同一候选的多模型结果可重放得到相同排名；任一未验证 claim 都不会提高候选排名。

## 6. Phase 3：离线评估、报告与回归门禁

### 6.1 固定评估集

建立版本化用例集，至少覆盖：单表过滤排序、三表 JOIN、evidence 查询、空结果、错列自修、恶意 SQL、超限截断、证据冲突、候选不存在和多模型分歧。每条用例包含：用户问题、允许的事实、预期查询 / 结果断言、预期 Recommendation / verdict。

### 6.2 指标

| 指标 | 定义 |
| --- | --- |
| SQL 语法成功率 | 通过 Validator 的查询数 / 总查询数 |
| SQL 语义正确率 | 查询结果符合标准答案的任务数 / 总任务数 |
| 越权拦截率 | 被阻断的恶意样例数 / 恶意样例总数 |
| 自修成功率 | 首次失败后最终成功任务数 / 首次失败任务数 |
| 事实支持率 | 有同实体、同字段、同值 evidence 的 claim 数 / claim 总数 |
| 推荐有效率 | 通过 Truth Verification 的 Top-1 数 / 有结果任务数 |
| 融合收益 | 融合结果相对最佳单模型的推荐有效率变化 |
| 平均成本 | 每 Run 的轮数、SQL 调用数、耗时和 token 估算 |

### 6.3 回归规则

- 每次修改 Validator、Schema、Verification 或 Fusion，都必须运行不依赖 DB 的回归集。
- DB 相关指标只由 DB 验收机更新，并在报告中注明提交 SHA 和数据快照版本。
- 不用单次模型表现修改融合权重；至少在固定集完整复跑后才更新权重。

## 7. 推荐实施顺序

```text
Phase 0.1：修复 V2.2 证据 / 审计 / 角色脚本缺口
  -> Phase 0.2：DB 验收机完成 DB-01..DB-08 并回填报告
  -> Phase 1：前端使用 mock 并行开发，随后接真实 API
  -> Phase 2：Truth Verification
  -> Phase 3：固定评估集
  -> Phase 2.2：按评估结果启用多模型 Fusion
```

其中前端可以与 Phase 0 并行；Fusion 必须在 Truth Verification 与评估集具备后开始，避免把未经证实的模型输出稳定地放大。

## 8. V2.3 验收清单

- [ ] V2.2 的 UUID、claim-evidence、日志关联、只读角色幂等性缺口已修复并有单元测试。
- [ ] DB 验收机已对 DB-01 至 DB-08 留下脱敏、可追溯报告。
- [ ] 前端覆盖成功、失败、截断和无结果状态，且 mock 与 API 契约一致。
- [ ] Truth Verification 的 verdict 可追溯到实体、字段、值和 evidence。
- [ ] Fusion 只接收已验证结果，输出可重放并保留内部 trace。
- [ ] 离线评估集、执行器、指标报告和回归阈值已版本化。
- [ ] [V2.2.md](V2.2.md) 的 Step 9 状态已按真实验收结果回填。
