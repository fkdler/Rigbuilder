# RigBuilder Plan V2.1：当前实现归档与后续路线

> 状态：2026-08-30 归档。本文件记录 V2 基线之后已经实际完成并验收的工作，以及下一阶段的明确边界；仍然存在尚未验证的能力！

## 1. V2.1 的定位

V2 定义了系统边界：设备 A 负责前端、FastAPI、Truth DB、上下文和受控 Agent；设备 B 负责 `llama-server` Router、GGUF 与 GPU。V2.1 记录该架构从“单模型最小链路”进入“本地三模型串行对比测试”的实际状态。

当前仍只有一个用户可见的 `conversation` 边界。未来即使多个模型参与同一轮回复，它们也属于同一个用户会话；不能以“每个模型一份会话”代替综合结果。

## 2. 已完成并已验收

### 2.1 Truth DB 与数据入口

- PostgreSQL Truth DB 已迁移并连接成功。
- `data/standardized/` 中的 22 个硬件、8 个模型、24 个模型变体和 174 条 Evidence 已通过导入器校验并入库。
- 导入器支持自然键解析、单事务、`--dry-run` 与幂等重跑。

### 2.2 设备 A / 设备 B 推理链路

设备 B 已启动一个 `llama-server` Router：

```text
Router model IDs: agent-a, agent-b, agent-c
Constraint: --models-max 1
Binding: 0.0.0.0:8080
```

设备 A 已验证到设备 B 的 TCP 8080 连通，并成功通过 `GET /models` 发现三个 preset。三个模型初始处于 `unloaded` 状态，由 Router 在请求时按需加载；这符合单 GPU 物理串行的约束。

### 2.3 三模型同题对比测试

已新增临时对比接口 `POST /api/model-comparison` 与前端三栏测试页：

```text
用户输入同一个问题
  → 设备 A 在一个全局推理锁内依次请求 agent-a → agent-b → agent-c
  → 前端按 A / B / C 三栏展示三份原始回复
```

- 模型 ID 只在设备 A 的 `LLM_TEST_MODEL_IDS` 配置，不由浏览器传入。
- 三次调用严格串行；单个模型失败时仍返回其他模型的结果与该模型的错误状态。
- 该接口仅用于对比验收：不创建会话，不将三份结果误写为用户最终答案。
- 前端与后端构建、接口加载、顺序调用和缺失配置的错误处理均已验证。

### 2.4 会话持久化基础

- 已创建 `conversation`、`conversation_message`、`conversation_context_snapshot`、`user_constraint` 表。
- 普通聊天接口可持久化用户消息与最终助手回复，并通过 `conversation_id` 恢复历史。
- 原始消息不因摘要而删除；摘要为可版本化的派生数据。
- 上下文窗口使用 `LLM_CONTEXT_WINDOW_TOKENS` 显式配置。该值尚未基于目标模型实测，因此自动压缩当前默认关闭。

## 3. 尚未完成：结果融合与推荐

三模型对比页只展示三份**原始模型回答**，并不构成推荐系统的最终输出。以下能力尚未实现：

1. **结构化输出约束**：三个模型尚未统一生成 Recommendation JSON；模型可输出自由文本，无法安全进入后续算法。
2. **结果融合**：未实现 MTRF 或其他融合算法，未计算事实一致性、约束满足、偏好匹配、模型可靠性与模型间共识。
3. **真实性检查**：尚未从模型回答中抽取 Claims，也未回查 Truth DB 与 Evidence 得到 `supported / conflict / missing / unverifiable` 状态。
4. **约束检查**：预算、已有硬件、显存、用途等用户硬约束尚未被独立规则验证；目前模型文本不能视为“满足约束”的证明。
5. **最终输出效果**：未实现 Top-K 候选、评分分量、理由、风险、Evidence 引用、信息不足项和可解释淘汰原因。前端三栏不是最终推荐界面。

下一阶段应先冻结 Recommendation JSON 与 Claim/Verification Schema，再实现约束验证和事实验证，最后才选择/实现融合公式。没有这些输入契约，直接对三份自然语言回答投票或平均会放大重复错误。

## 4. 尚未完成：模型数据库工具与搜索能力

当前模型没有可用的 Truth DB 工具闭环。

- `inspect_database()` 尚未正式实现。
- `query_database(sql)`、SQL AST Parser/Validator、只读数据库账号、查询超时、行数限制和结构化 Observation 均未实现。
- 模型无法自主检索 `hardware`、`ai_model`、`model_variant`、`evidence`、`benchmark` 五张事实表。
- 因此目前**不知道各模型的数据库搜索能力**：既未知其生成 SQL 的语法正确率，也未知 JOIN/Evidence 查询语义正确率、自我修正能力、工具调用次数和越权查询拦截效果。

应在实现只读 SQL 工具后，建立离线测试集，至少覆盖：单表筛选、模型/变体/硬件 JOIN、Evidence 追溯、空结果、错误列名修复、恶意 SQL 拒绝和 Benchmark 条件查询。评价对象是“模型 + 受控工具协议”闭环，而不是裸模型记忆。

## 5. 尚未完成：效率与效果评估

当前只确认单模型链路主观速度可用，并确认三模型 Router 调用顺序正确；尚未形成可复现实验或结论。

尚未记录或评估：

- 每个模型的冷加载、卸载、模型切换和端到端耗时；
- 首 Token 延迟、生成 token/s、输入/输出 token 数与上下文压缩次数；
- GPU VRAM、RAM、CPU 使用、Router 队列等待与失败率；
- 三模型对比的总耗时与 P50/P95；
- 推荐正确性、Evidence 覆盖、事实冲突率、硬约束满足率和用户偏好匹配；
- 与单模型、固定 Truth Context、单 SQL Agent 和完整融合方案的对照/消融实验。

后续应为每次 Agent Run 记录 `request_id`、`conversation_id`、模型 ID、量化、上下文窗口、加载/推理/总耗时和资源指标。只有收集这些数据后，才能判断三个模型串行是否足够、是否需要优化加载策略，以及融合是否真正改善结果。

## 6. 建议实施顺序

1. 为三模型对比增加受控运行日志和性能指标采集；确认每个模型的实际上下文窗口，设置 `LLM_CONTEXT_WINDOW_TOKENS` 并测试摘要保真。
2. 冻结 Recommendation JSON、Claim 和 Verification Result Schema。
3. 实现 `inspect_database()` 与只读 `query_database(sql)`，包括 Parser/Validator、`agent_readonly` 权限、3 秒超时与 100 行上限。
4. 建立数据库工具离线评测集，先回答“每个模型是否会正确搜索数据库”。
5. 实现用户约束提取/确认、Truth Verification 和 Constraint Validation。
6. 在前述输入稳定后实现融合算法，并输出 Top-K、分量得分、Evidence、风险与信息不足。
7. 使用固定数据版本、固定模型版本和测试集完成效果与效率评估；再决定是否需要并行或更多 GPU。

## 7. 文档版本与归档说明

本轮修改了 [API_Documentation_V1](../API_documents/API_Documentation_V1.md)，原因是新增了 `POST /api/model-comparison`，并需说明它不持久化结果、模型 ID 只能在服务端配置、单模型失败的返回语义。

本轮修改了 [Application_Server_Guide_V1](../Application_Server_Guide_V1.md)，原因是设备 A 现在实际使用 `LLM_TEST_MODEL_IDS` 连接设备 B 的三个 Router preset，需要说明配置位置、A/B/C 请求顺序和错误处理。

这两份 V1 文档已经记录当前可运行的验收基线。**本文档不再修改；后续如有更新，则使用新的后缀文档。**
