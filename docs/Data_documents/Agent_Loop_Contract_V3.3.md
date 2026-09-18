# RigBuilder Agent 循环行为契约 V3.3（冻结）

> 冻结日期：2026-09-12
> 数据库修订：**无**（未新增 Alembic revision）
> Truth DB 契约：沿用 [Truth_DB_Contract_V3.2.md](Truth_DB_Contract_V3.2.md)，`schema_version = "3.2"`

## 0. 边界声明

本文件不是数据采集规范，也**不改动 Truth DB 契约**。它冻结的是 **SQL Agent 运行时**如何读取、纠正与提交针对 `agent_catalog` 的只读查询——即真值数据在进入融合前的最后一段行为约束。

- 不新增、不修改任何 `truth` 表、`agent_catalog` 视图或只读角色，因此不新增数据库修订。
- `Recommendation` schema、claim 覆盖规则、`TruthVerifier`、`fusion-v1`、`candidate_valid` 门禁、只读 SQL 边界与确定性排序**零弱化**。
- 本文件记录的行为以实测为准；未实测的推断一律标注，不写成结论。

## 1. 起点基线（2026-09-12 实测）

修复前一次真实融合的终态：

```text
status = failed
error  = "All configured Agents failed before fusion."
result = null
agent-a  round_limit_reached
agent-b  round_limit_reached
agent-c  tool_protocol_error（第 1 轮，零次查询）
```

6 次 Agent 运行产出 0 次提交，前端只能得到报错。因此本轮的验收对象不是"没有报错"，而是**真值校验后的结果质量**：`completed`、`agent_coverage`、`credibility`、`fact_support`。

## 2. 上下文与预算契约

| 条款 | 内容 |
|---|---|
| 预检依据 | 每轮 preflight 的容量判定使用端点自身 `/tokenize` 的实测计数，估算仅作降级 fallback |
| 超限处置 | 超限时先**成对**丢弃最旧的工具往返（assistant tool-call 与其 tool 结果必须同时删除，孤立 `tool_call_id` 会被服务端拒绝）；丢不动才判 `context_budget_exceeded` |
| 保护对象 | `resolve_evidence` 的结果**不参与丢弃**：它携带本 Run 唯一可引用的真实 `candidate_id` 与 `evidence_id` |
| 单次工具结果上限 | `resolve_evidence` 单次最多解析 **3** 个实体（`RESOLVE_EVIDENCE_ENTITY_LIMIT`），超出部分以 `truncated_reason="entity_limit"` 明示并提示可二次调用 |
| 单轮生成上限 | 工具轮与终局轮同为 **8192** token |

**实测依据**：`len/4` 估算对含 UUID 的密集 JSON 严重乐观——估 11 550 而实际 20 559，被服务端拒绝；估 8 886 而实际 14 194。三次 Agent 融合曾累积到 17 527、18 353、19 848 真实 token（窗口 16 384），已取得有效证据的 Agent 因此无法提交。单次解析 19 个实体曾返回 21 306 字符 / 11 775 token，占预检容量 82%。

## 3. 候选身份契约

| 条款 | 内容 |
|---|---|
| 候选来源 | `candidate_id` 只能来自本 Run 内工具真实返回过的实体 ID；`resolve_evidence` 是获得它的唯一入口 |
| 提交门禁 | 提交的 `candidate_id` 若不在本 Run 已知集合内，判定 `unprovable_candidate_id` 并拒绝，同时回注可用 ID 供模型当场自修 |
| 惰性条件 | 本 Run 未获得任何实体 ID 时该检查**不生效**，以免阻塞"合理地无可推荐"的 Run |
| 无 ID 时的提示 | 未获得任何候选 ID 时，提示词必须说明当前 Release 的构成（`gpu_catalog` 20 张 NVIDIA 卡，RTX 30/40/50 系），阻止提交不存在的产品名或架构名 |

**实测依据**：三个 Agent 均提交过 `00000000-0000-0000-0000-000000000000` 或自造 UUID；`Recommendation` schema 无法区分真伪 UUID，错误直到真值校验才以 `candidate_not_found` 暴露，且 schema 约束的终局路径没有重试余量。

## 4. 循环自约束契约

| 条款 | 内容 |
|---|---|
| 语句去重 | 空白归一化后**拒绝执行本 Run 已执行过的语句**，不消耗数据库往返与轮次，并告知这是第 N 次相同语句 |
| 终局指引 | 提示模型走哪个终局路径必须**跟随实际提供的工具集**；终局工具未提供时不得要求"调用 submit_recommendation" |
| 轮次耗尽兜底 | 终局工具不可用时，轮次耗尽可能落到 schema 约束的 finalize，而非直接判失败 |
| 事实主张修复 | 缺 `evidence_ids` 的 fact claim 按值匹配该实体的证据，比较必须**数值归一化**（`8`、`"8"`、`8.0` 等价） |

## 5. 错误诊断契约

只读执行失败必须**点名具体问题**，不能只回错误码；以下提示在成功路径上同样适用。

| 错误形态 | 必须给出的信息 |
|---|---|
| SELECT 列表把字符串字面量当列 | 指出该位置应为列。PostgreSQL 会**成功执行** `SELECT 'gpu'` 并返回常量列，行数看似合理而值是错的 |
| 未知列 | 给出列名并附最接近的候选项（`difflib`） |
| 引用未注册表/视图 | 点名是哪张表不是注册视图 |
| 列引用歧义 | 给出应使用的限定形式（驱动消息仅说明"列引用不明确"，从不指出限定来源） |
| 列不在该视图上 | 指出该视图实际具备的列 |

## 6. 实测数据字典

`agent_catalog` 视图在**当前 Release** 上的真实取值。Agent 曾整轮猜测这些值（`market_region='China'`、`price_type='retail'`、`lifecycle_status='current'`），而每次猜错都返回 0 行——循环无法区分"值猜错"与"数据不存在"。

- `price_latest`：`market_region in ('CN')`；`currency in ('CNY')`；`condition in ('new','used')`；`price_type in ('current_new','current_used')`；`availability in ('in_stock','out_of_stock','unknown')`。
- `price_latest` **没有 `entity_id` 列**，只带 `entity_key`，须按 `g.entity_key = p.entity_key` 关联；选择 `p.entity_id` 会因未知列失败。
- 价格行是 region + currency + condition + price_type 的组合：`'new'` 配 `'current_new'`，`'used'` 配 `'current_used'`。不存在 `'retail'`、`'MSRP'` 或 `'CN'` 以外的地区。
- `gpu_catalog`：`manufacturer_key in ('org:nvidia','org:amd','org:intel')`（`'org:'` 前缀必需，`'nvidia'` 匹配不到任何行）；`category = 'gpu'`；`market_segment = 'consumer'`。
- `gpu_catalog.lifecycle_status`：20 张 NVIDIA 卡中仅 **1** 张为 `'active'`，其余 19 张为 `'unknown'`。按 `'active'` 过滤会丢弃几乎整个目录；应改用 `recommendable = true`，除非问题明确涉及生命周期。
- `entity_attribute_catalog` 在本 Release **存在但为空**。事实取自 `gpu_catalog` 自身已有的 `architecture`、`vram_gib`、`memory_type`、`memory_bus_width_bit`、`memory_bandwidth_gb_s`、`board_power_w`。
- 窗口函数（`RANK()` / `ROW_NUMBER()`）被 SQL 校验器拒绝（`forbidden_function`）。排序须由模型自行完成。
- 取值统一小写，不区分大小写变体。

> **核验方式**：以下语句在 `backend` 目录执行即可复现本节全部取值。修订发布后必须重跑并同步
> `backend/app/agents/prompts.py` 的 `DATA_DICTIONARY` 与本文件。
>
> ```powershell
> ..\.venv\Scripts\python.exe -c "
> from sqlalchemy import text; from app.db.session import SessionLocal
> q = ['SELECT DISTINCT market_region FROM agent_catalog.price_latest',
>      'SELECT DISTINCT currency FROM agent_catalog.price_latest',
>      'SELECT DISTINCT condition FROM agent_catalog.price_latest',
>      'SELECT DISTINCT price_type FROM agent_catalog.price_latest',
>      'SELECT DISTINCT manufacturer_key FROM agent_catalog.gpu_catalog',
>      'SELECT lifecycle_status, count(*) FROM agent_catalog.gpu_catalog GROUP BY 1',
>      'SELECT count(*) FROM agent_catalog.entity_attribute_catalog']
> with SessionLocal() as s:
>     [print(r) for stmt in q for r in s.execute(text(stmt)).all()]
> "
> ```
>
> **已知缺口**：`prompts.py` 中曾写有「刷新来源：`backend/scripts/catalog_values.py`」，但该脚本
> **从未创建**（`0c6cb16` 引入的悬空引用，重新分组后该行已移除）。目前没有自动化测量脚本，只能按上述语句手工核验；
> 补一个自动化测量脚本仍属对既有文件的改动，尚未执行。

## 7. 终局工具开关（实测取舍）

```text
AGENT_TERMINAL_TOOL_SUPPORTED=0   数据库工具 + JSON-Schema 约束终局   ← 采用
AGENT_TERMINAL_TOOL_SUPPORTED=1   以 submit_recommendation 收尾，全程 Tool Calling
```

同一问题、同一端点、每个模型 2 轮，共 12 次：

| 设置 | completed | 通过 Truth 校验 |
|---|---|---|
| `0` | **6/6 (100%)** | **4/6** |
| `1` | 2/6 (33%) | 0/6 |

设置 `1` 失效的机制：模型习惯在**同一回合**内先查询再提交，提交 JSON 被单轮生成上限截断成非法参数，而适配器对不可解析的工具参数**直接终止该 Agent，无重试**。

> 换用更小的模型或更长的上下文前，不要改成 `1`。

## 8. 变更清单

相对本次改造起点 `0b96f36`：生产代码（`backend/app`）**18 文件 +2970 / −54**；测试与脚本 **4 文件 +327 / −2**；全部 **33 文件 +6828 / −79**。

下表是本仓库 `dev` 分支上承载这些改动的实际提交（`git log 0b96f36..HEAD`）。原始细粒度提交已在重新分组时合并，因此一行对应多项改动。

| 提交 | 内容 |
|---|---|
| `85a7eb5` | 循环可达提交：终局指引跟随工具集、单轮上限 512→8192、终局兜底；拒绝重复语句与工具未曾返回的候选 ID；事实主张按数值归一化匹配 |
| `035a8bf` | 上下文预检改用端点 `/tokenize` 实测计数（`len/4` 为校准结果，见 §9），超限时成对丢弃最旧工具往返并保护 `resolve_evidence` |
| `0c6cb16` | 数据字典；SQL 诊断提示：歧义列的限定形式、未注册视图点名、畸形 SELECT 列表与未知列 |
| `a873ef7` | 新增 `resolve_evidence` 工具：由 `entity_key` 取得真实 `candidate_id` 与已接受证据；限制单次解析实体数 |
| `92da66d` | 同步工具契约与注册表，使新增工具通过 `ToolCall`/`ToolResult` 的校验 |
| `aed4d8e` | 无候选 ID 时说明 Release 构成；按名称与实体键查询候选与证据 |
| `01b0f64` | 记录终局工具实测结论并新增开关；上下文预算配置项 |
| `5317682` | capability 探针补 `await`；轮次上限由配置驱动；端点 tokenize 计数 |
| `3e2573a` | 钉住终局工具分支；钉住 `resolve_evidence` 边界并增加真库校验 |

构建 `resolve_evidence` 期间另行发现并修复三处缺陷：提示词未转义花括号导致 `str.format` 抛 `KeyError`（同类缺陷此前已上线过一次；现由 `test_prompt_formatting.py` 守护，变异验证：重新引入该缺陷使 3 个用例失败）；registry 新增工具而未同步 `ToolCall`/`ToolResult` 的 `Literal`，导致全部 Agent 死于 `ValidationError`；首版对每个实体返回全部证据，19 个实体产生 70 条并推高 prompt 至 17 531 token。

## 9. 已尝试且实测无效、不得重复的手段

| 手段 | 实测结果 |
|---|---|
| token 估算 `len/4` → `len/3` | **回归**。llama.cpp 实测 17.6k 字符提示词为 4 029 token，`len/3` 估成 5 862，误杀本来合法的运行，使可用的 2/3 覆盖率融合变成 `status=failed` 且无结果 |
| 自动附加证据（方案一） | 被 `_filter_evidence_for_result` 静默清空，零收益 |
| 自动附加证据（方案二） | 有一条漏过假 session，零收益 |
| 协议重试 | 无实测收益 |
| 只保护最新轮次的 `resolve_evidence` 裁剪 | 比全部保护更差（`coverage ≥2/3` 从 5/8 降到 1/6） |
| 数据字典（§6） | 对 agent-a/agent-b 有效，**对 agent-c 无效** |
| 英文 locale 假设 | 已测试并否决：agent-c 在中文报错下能重试成功，英文报错下不重试 |

## 10. 参数基线

```text
AGENT_TERMINAL_TOOL_SUPPORTED = 0
AGENT_MAX_ROUNDS              = 6
AGENT_MAX_SQL_CALLS           = 6
AGENT_TOTAL_TIMEOUT_SECONDS   = 300
CONTEXT_KEEP_TOOL_ROUNDS      = 2
CONTEXT_PRUNE_MAX_MESSAGES    = 40
round_max_tokens              = 8192   （代码内常量，app/agents/loop.py）
RESOLVE_EVIDENCE_ENTITY_LIMIT = 3      （代码内常量，app/tools/database.py）
```

## 11. 验收实测

修复后连续 8 次端到端融合（同一问题、真实路由与 Job/SSE）：

| 指标 | 结果 |
|---|---|
| `completed` | **8/8** |
| `coverage` 每次 | `[2/3, 1/3, 1/3, 2/3, 2/3, 1/3, 2/3, 2/3]` → **5/8 达到 ≥2/3** |
| `credibility` 中位数 | **81.29**（min 1.67，max 90.0） |
| `fact_support` 中位数 | **0.86** |
| 各 Agent | agent-a 7/8 成功、agent-b 6/8、**agent-c 0/8** |

合并修复后的全部运行（16 次）：`completed 15/16 (94%)`，`coverage ≥ 2/3` 占 **11/16 (69%)**。

**关键因果证据**：在一次变量隔离中，仅加入 §2 的 `resolve_evidence` 实体上限，`coverage` 即由**恒为 1/3** 变为 `[2/3, 2/3, 2/3]`，复测出现 `[1/3, 2/3, 1.0, 2/3]`。**在该改动之前，本项目的 `coverage` 从未高于 1/3。**

离线全量测试：**301 passed / 0 failed**。

## 12. 遗留与边界

- **`agent-c` 的 SQL 能力未解决**：8/8 仍失败于其自写的 SQL。§5 的诊断提示使其**失败更快、更可诊断**，未使其会写 SQL。这是模型能力问题，不是循环缺陷；换用更强的模型或降低其任务难度属于产品决策。
- **偶发 `context_budget_exceeded`**：单条 `resolve_evidence` 结果仍有约 5.8k 字符，多次调用会累积。
- **`agent-c` 于 0/8**：融合在 `coverage` 为 2/3 时仍能产出结果，但覆盖率与 `credibility` 受影响。
- **`release_key` 未持久化**：`agent_run.metrics` 中无该字段（真库 177 条 Run 实测为 0），因此事后**无法证明**某次运行时模型实际收到的系统提示词与当前 Release 一致。
- **数据字典缺少测量脚本**：`prompts.py` 指向的 `backend/scripts/catalog_values.py` 从未创建（§6）。
- **提示词与工具集存在已知不一致**：融合路径只生成一次四工具协议提示词，而 `AGENT_TERMINAL_TOOL_SUPPORTED=0` 只向模型提供数据库工具。提示词因此宣传了一个该 Run 并未提供的工具。该行为已确认，尚未处置。
- **`_run_legacy` / `legacy_finalize` / schema finalization / 单 Router 串行路径全部保留**，未删除。

## 13. 维护约定

- 本文件随实现变更更新；下一版改名 `Agent_Loop_Contract_V3.4` 或按需新增后缀，不改历史版本。
- 所有"实测"数字必须来自真实运行，不得以文档基线替换。
- 逐模型的在本机权重与上下文配置下的可达上限，记录在 [../Local_Test_Setup.md](../Local_Test_Setup.md) 第 12 节；本节只保留与行为契约相关的结论。
