# Plan V4.4：场景适配、证据计算与完整配置重构

计划日期：2026-09-15；实施验收更新：2026-09-16。承接 V4.3，保留设备 A 服务 + 设备 B 两个推理进程。本文先记录实施计划，验收结果在末尾追加；不把计划当成已完成。

## 1. 故障与已确认的数据边界

- “一套普通办公电脑”被 classify_request 判成 single，整机组装提前返回；这是应用错误，不是模型能力不足。
- fusion-v1 无显式偏好时 recommendation_score=100*consensus，规格可核验不等于场景适配。只在末端调整文案无法挽救错误候选池。
- query jobs 的失败回退按错误码白名单触发，协议错误等未覆盖；失败结果原文也可能被前端展示。
- 本次只读实查：CPU 40、GPU 41、benchmark_result 0；CPU 的 integrated_gpu 列存在，integrated_graphics 不存在。核显 accepted Evidence 为 0；CPU 架构、核心数、基础功耗等各有 40 条 accepted Evidence。
- 核显列有值不等于通过 TruthVerifier；不能从 CPU 名称后缀猜核显，也不能将 NULL 当作无核显。新数据发布日不能充当硬件规格采集日。

## 2. 模块设计与实施顺序

1. 需求契约：独立表示 scope（整机/单部件）、workload、display_strategy、required_roles；普通办公默认核显优先，显式游戏/渲染/独显需求优先于该默认。完整配置纠错追问复用同会话原始需求，重新检索；不把旧 CPU 当作用户指定零件。
2. 工具计算层：query_database 的 Observation 增加 decision_context。自主 SQL 检索保持不变，后端为返回的候选补充规范化事实和可复算的相对规格位置；不让模型靠内部知识判断新型号定位。
3. 排序层：硬约束和 TruthVerifier 仍先过滤。普通办公采用版本化的适配策略，功耗优先、核心数满足基本门槛后收益饱和；核显证据缺失显式标记。对所有已读取且有证据的候选执行确定性排序，LLM 自评分不作为性能证据。非办公场景保留原融合，逐步建立专门评测后扩展。
4. 输出层：整机始终列出 CPU、显示方案、主板平台、内存、存储、电源、散热、机箱的处理状态；缺型号时提供标注为通用建议的规格方向。不能把这种配置草案称为全部兼容性已核验的采购单。
5. 断言校验与有界重写：结构化属性走 TruthVerifier；计算断言核对规则版本、实体、同组成员、分数、时间和证据链。自由文本对实体、数字、比较和保证语句做保守拦截，失败仅允许一次重写，再退到证据模板。通用自然语言语义的完全自动验证不在本次能力承诺内。
6. 后端调试：保留原始输出与计算输入/淘汰原因供审计，用户界面只显示可理解的处理状态和证据。所有非基础设施的融合失败允许一次通用知识回退；回退也失败时只显示中文服务说明。

## 3. 工具 schema（增加字段，不破坏 SQL 协议）

输入仍为 `query_database({"sql":"SELECT ..."})`。新增返回结构：

```json
{
  "decision_context": {
    "policy_version": "catalogue-position-v1",
    "release_key": "accepted release key",
    "snapshot_applied_at": "ISO datetime",
    "data_cutoff": "oldest evidence observation date, or null",
    "candidates": [{
      "entity_id": "UUID",
      "category": "cpu",
      "facts": {"cpu.base_power_w": {"value": 65, "unit": "w", "evidence_ids": ["UUID"], "observed_at": "ISO datetime or null", "confidence": 0.9}},
      "position": {
        "basis": "specification_only",
        "cohort": "category + verified architecture",
        "generation_basis": "architecture_proxy",
        "cohort_size": 8,
        "dimensions": {"cpu.cores_total": {"percentile": 0.25, "rank": 6, "sample_count": 8}},
        "performance_score": null,
        "label": "relative specification position; not measured performance"
      },
      "missing_fields": ["cpu.integrated_gpu", "benchmark.performance", "release_date"],
      "confidence": {"meaning": "required fact evidence coverage, not model certainty", "coverage": 0.75}
    }]
  }
}
```

具体字段以代码生成 schema 为准。未知必须 null/缺失说明；不得伪造发布时间、价格、市场全量排名。架构作为代际代理会跨越产品批次，同架构排名只代表库内样本。所有有效比较应携带参与实体及输入 Evidence ID，截断结果不能冒充完整市场。

## 4. 模型系统提示要求

你的内部知识可能落后于当前数据库。用户意图优先于历史推荐。自主调用只读数据库工具获取候选；所有具体硬件规格、发布时间、性能、排名和比较必须来自本轮工具证据。复制实体、字段和证据 ID，不得用型号名称、内部知识或常识补全缺失硬件事实。规格位置不是性能排名。只解释计算层给出的适配依据；不重新定义分数。普通办公优先考虑基本需求和功耗，不把最多核心或最新旗舰当作默认最优。整机与独显需求是两个独立维度。没有证据时说明待确认项，给出有用的配置草案，不编造已核验结果。

## 5. 校验与重写策略

| 断言 | 校验 | 失败处理 |
|---|---|---|
| 实体/属性/单位 | 相同实体 accepted Evidence + TruthVerifier 当前字段值 | 排除冲突候选或删去该断言 |
| 核显 | 非空名称仍必须有 accepted 同字段证据；NULL=未知 | 显示方案待确认，不承诺无独显可用 |
| 时间 | source observation、release snapshot、product release 分开；未来采集时间不接受 | 时间未知不冒充当前；价格/库存不能由静态规格推断 |
| 排名/比较 | 同品类同架构、同维度同单位；输入证据、样本数、规则版本匹配 | 跨代或性能比较没有基准则拒绝该断言 |
| 适配 | 硬约束先行；分值只来自政策参数与可核验事实 | 缺关键事实不计满分，输出不确定性 |
| 自由文本 | 实体绑定、数字白名单、性能保证与无依据比较拦截 | 一次短重写，失败后确定性证据模板 |
| 整机完整性 | 各必需角色有型号、规格草案或明确缺口 | 不得只显示 CPU 后宣称整机完成 |

不无限重试。取消、模型加载失败和连接不可用不触发循环回退。不将所有静态老规格自动判过期：静态事实注明日期；实时数据另设有效期，当前无实时数据就保持 unknown。

## 6. 评测与交付

- 办公案例：scope=bundle；不默认附加高功耗独显；同等证据下普通 CPU 优先于 125W/24 核型号；CPU 单独输出率=0。
- 追问案例：“不是完整配置”“补齐其他配件”保持原办公场景；有新游戏/渲染需求时不能继续办公默认。
- 事实安全：错实体、错证据、错单位、缺核显证据、陈旧/未来日期、不支持的性能比较均有负例；关键错误接受数=0。
- 排序稳定：打乱候选顺序、Agent 自评分或调用顺序，不改变有明确适配差异的主选择。
- 数据缺失：没有基准时 performance_score 必须 null；缺关键元数据时安全降级。
- 延迟：记录总耗时与工具/选择/校验/重写耗时；单样本不是 P95。目标是无额外模型规划轮次，重写最多一次。
- 回归：后端全套 pytest，前端 unit/build；设备 B 可达时验证真实办公请求与同会话追问。记录真实结果，失败时不得记成通过。

## 7. 本轮状态

### 已实现

| 模块 | 实现文件（相对项目根目录） | 已落地行为 |
|---|---|---|
| 需求及政策 | `backend/app/services/requirements.py`、`routing.py` | 整机范围与 CPU/GPU 角色分离；办公默认 CPU 路线，游戏/渲染/明确独显需求覆盖默认；纠错追问重建原需求 |
| 工具元数据 | `backend/app/services/catalogue_position.py`、`tools/database.py` | 对本轮返回实体附加证据事实、日期、字段覆盖度、库内同架构规格位置、办公适配分与缺失项；只读，不导入或更改 Evidence |
| Schema | `backend/app/schemas/decision_context.py` | 类型化 Observation 扩展；完整 JSON Schema 见 `docs/API_documents/Decision_Context_V4.4.schema.json` |
| 候选选择 | `backend/app/agents/selection.py` | 模型自主 SQL 获得候选；办公终选覆盖本轮已查到的证据候选，避免 LLM 只提交高功耗 i9；元数据和证据 ID 传入选择阶段 |
| 融合后场景排序 | `backend/app/services/request_scope.py` | 在 TruthVerifier/硬约束过滤之后实施 `office-fit-v1`；所有候选先参与场景排序，再裁剪 top_k；保留原始 Agent 分数与排序供审计 |
| 配置草案 | `backend/app/services/build_assembler.py`、`schemas/fusion.py` | CPU、显示、主板平台、内存、存储、电源、散热、机箱均有状态；未知核显不声称可无独显使用 |
| 输出校验 | `backend/app/services/assertions.py`、`narrator.py` | 保留结构化 TruthVerifier；额外拦截已知的无证据性能、保证、时间、核显和属性数值混用；紧凑理由最多重写一次，然后证据模板回退 |
| 失败处理 | `backend/app/services/jobs.py` | 非基础设施的失败状态统一尝试一次 FAST；可恢复 fusion 总超时找到会话后亦可回退；两次都失败则保存安全中文说明，不保存可直接展示的内部错误 |
| 前端 | `frontend/src/components/terminal/RecommendationBlock.vue` | 展示配置草案及各项待确认内容；办公不把缺独显当缺部件；游戏配置保留必需 GPU 校验 |

### 排序的精确定义

`P = min(1, 65 / base_power_w)`，`C = min(1, cores_total / 4) * min(1, 8 / cores_total)`，`office_fit = 100 * (0.70P + 0.30C)`。无正值或缺关键证据时不计算有效分值，最终排序按 0 分处理，不补值。6 核/65W 为 100 分，24 核/125W 为 46.4 分。这里的 65W、4 核、8 核是明示的初版产品政策参数，**没有办公实测支撑，不能称为普遍最优硬件阈值**。

有明确偏好约束时，最终分数为 `0.8 * office_fit + 0.2 * 100 * preference_utility`；无偏好为 `office_fit`。硬约束仍由原 ConstraintEvaluator 先行筛除，未知硬约束不放行。相同适配分按 canonical_name、candidate_id 稳定排列，不将同分候选的字母先后解释成性能胜负。推荐候选仍只能来自本轮实际 SQL 返回集合，后端不偷偷添加未检索产品。

原 `fusion-v1` 内核与事实协议保留；这是可追踪的场景后处理政策，版本记录在 `trace.formula.scenario_policy`，逐候选分解在 `trace.input_summary.scenario_scores`。**其他场景尚未套用办公公式，也未宣称完成所有场景的生态位加权排序。**

### 规格位置、时间与置信度

- 同组定义目前为品类 + 已有 accepted 架构证据；同架构仅是代际代理，不等于确切发布代际，也不保证市场全覆盖。
- 每维 `rank = 1 + count(peer_value > own_value)`；`percentile = count(peer_value < own_value)/(sample_count-1)`，数值越大排越前。这是规格分布，**功耗数值排前并不代表更好**。少于两个有效样本不产生维度排名。
- 每个计算 ID 对规则版本、字段、架构和排序后的实体/数值/Evidence 输入做哈希；`verify_position` 要求实体、字段、release、规则、完整计算结果一致。模型生成的比较句不因为包含一个计算 ID 就自动可信。
- 性能基准为 0，因此 `performance_score` 恒为 null。模型不能以核心数、频率或架构名推导游戏 FPS、办公响应、噪声或实际整机耗电。
- `snapshot_applied_at` 为发布包应用时间；`data_cutoff` 为参与计算的证据采集/源文档访问时间中最早值（UTC）。当前实查截止为 `2026-09-03T00:00:00+00:00`，不是最新模型发布包的 9 月 15 日。
- 事实级 confidence 取匹配 accepted Evidence 的最小记录置信度；candidate coverage 是定义字段集合的证据覆盖比例。它们均不是模型自信概率，也不保证场景适配。CPU 核显缺证据通常使本字段集合覆盖率为 3/4。
- 控制工具字符预算：先去掉较大的比较证明及对应排名，再对重复事实用 `facts_reference=row_bindings` 引用；必要时同步裁剪行、证据和元数据，附 omitted_reason/truncated。极端单行耗尽预算时元数据候选可为空，不能冒充已经完成生态位评估。

### 验收结果与复现

- 后端：363 passed，1 skipped。包含办公与重负载意图、完整草案、同会话纠错、政策排序稳定性、错误实体/字段/发布版本/排名、数字属性混用、失败回退及最多一次重写；原 TruthVerifier、Claims、fusion 测试仍通过。
- 前端：126 单测通过，生产构建通过；办公配置草案 Playwright 桌面/手机共 2 项通过，并查看了两张截图，没有水平溢出，各项待确认标签可见。
- 中间真实设备 A/B 验收：`v44-live-20260916-002922.json` 两轮约 6.97 秒与 10.33 秒，均 completed/fusion，主选 Core i5-12400，各项配件栏目保留。此时已移除早期的 i9 默认选择、CPU 单项输出及内部错误；后续工具预算整合的最终验收另记。
- **最终工具预算整合验收**：`.local-logs/office-eval-20260916-003636.json`；首问 8.179 秒，纠错追问 8.172 秒，两轮均 completed/fusion、主选 Core i5-12400、8 项栏目覆盖率 100%。没有将未知核显描述为已验证。第一轮理由采用确定性证据模板；第二轮为模型解释，仍应关注“核心过多导致能耗与散热冗余”等隐含因果措辞，当前文本规则没有完备验证这类表述。
- 本轮实测使用当前代码启动独立 JobManager 进程连接设备 B，不证明用户原先已运行的 uvicorn 自动加载了最新代码。
- 可重复脚本：`backend/scripts/eval_office_refactor.py`；默认离线计算政策分，`--live` 会在本机数据库创建一个测试会话，运行两轮并保存完整结果。脚本的 8 项覆盖率只是栏目覆盖率，**不是八项型号均已核验的比例**。

```powershell
cd D:\RigBuilder\backend
& 'D:\RigBuilder\.venv\Scripts\python.exe' scripts/eval_office_refactor.py
& 'D:\RigBuilder\.venv\Scripts\python.exe' scripts/eval_office_refactor.py --live
```

### 尚未解决，不能算作已完成

1. 没有核显 accepted 证据、板型/视频接口/BIOS 兼容证据及完整零件型号库。目前能给出逐项草案，不能生成全部可直接采购、已验证兼容的整机。
2. 没有可比较的性能基准、实价或真实办公测量。规格位置不能升级为该时代真实性能生态位；跨厂商、不同形态/功耗墙的同架构样本还需更细的分组政策。
3. 自由中文全文的实体、时间、比较和隐含断言尚无完备语义证明。本轮是结构化 TruthVerifier + 计算重放 + 保守文本规则；仍可能出现“稳定性”等宽泛措辞。不能用正则命中率声称消除了所有幻觉。下一步应把叙述断言先生成结构化引用，再渲染句子，加入人工标注负例集。
4. 一次/少量真实对话不是大样本 P50/P95，也不是长期稳定性结论；候选检索覆盖、跨轮需求改变、复杂否定、预算和并发会话仍需专项评测。
5. 后续优先补核显和主板证据、建立明确办公工作负载和性能协议，再校准生态位/适配权重；不通过扩大模型自由发挥补事实缺口。
