# 数据收集与标准化规范 V2

> 状态：执行规范
> 日期：2026-09-02
> 依据：[Data_Collection_Spec_V1.md](Data_Collection_Spec_V1.md)、[DC_Guide.md](DC_Guide.md)、当前 SQLAlchemy 模型、Alembic `20260827_0001` 迁移和 `backend/scripts/import_data.py`。

## 0. 结论先行

V2 不建设硬件百科，也不同时启动 CPU、GPU、主板、内存、SSD、电源、游戏、工程软件、LLM、价格和各类 Benchmark 的全量采集。

当前可落地的第一闭环收窄为：

> **基于 NVIDIA Ampere 及更新架构的桌面单卡，推荐可由 llama.cpp/CUDA 本地部署的文本类 Dense GGUF 模型；也可从指定模型反向推荐显存档位或已有实测的 GPU。**

V2.0 继续使用现有五张 Truth DB 表和五个标准化 JSON 文件，不要求先改数据库：

```text
hardware.json
ai_models.json
model_variants.json
evidence.json
benchmarks.json
        ↓
backend/scripts/import_data.py
        ↓
hardware / ai_model / model_variant / evidence / benchmark
```

本轮优先补“证据质量和真实 Benchmark”，不继续无边界扩 SKU。仓库在 2026-09-02 的基线是：22 条硬件、8 个模型、24 个模型变体、174 条 Evidence、0 条 Benchmark。当前最大缺口不是型号数量，而是没有实测闭环。

### 0.1 V2.0 能承诺什么

- 查询 NVIDIA 桌面 GPU 的显存、显存类型、架构、板卡功耗和发布日期；
- 查询文本类 Dense 模型的参数量、上下文、代码定位和许可证名称；
- 查询经过筛选的 GGUF `Q4_K_M` / `Q5_K_M` 变体及准确文件大小；
- 在测试条件完整时，比较项目实测的显存、主存、生成速度和首 Token 延迟；
- 每个非空实体规格字段都能回到 Evidence；每条性能数据都保留在带完整环境的 Benchmark 记录中；
- 数据不足时返回“信息不足”或带假设的估算，不把“文件能放下”表述为“一定能运行”。

### 0.2 V2.0 明确不承诺什么

- 不推荐完整装机单、具体笔记本或品牌整机；
- 不回答游戏 FPS、工程软件性能、CPU 性价比、主板兼容、整机功耗或电源选型；
- 不做价格、预算内最优、在售状态或二手行情推荐；
- 不覆盖 Laptop GPU；同名 Laptop GPU 的 TGP 差异无法由当前 Schema 正确表达；
- 不覆盖 AMD/Intel GPU 的本地 LLM 性能；当前首个测试协议固定为 llama.cpp/CUDA；
- 不覆盖 NVIDIA 数据中心卡、专业卡和多 GPU 系统的购买推荐；它们可在后续作为独立受众扩展；
- 不覆盖 MoE、视觉/音频模型、AWQ/GPTQ、vLLM/SGLang 等部署路径；当前表无法完整表达 active parameters、投影文件、Runtime 兼容和多卡拓扑；
- 不把厂商理论算力、官方游戏推荐配置或第三方视频结论当作本项目实测性能；
- 不把 LLM 自己阅读自由文本后产生的判断当作已验证结构化事实。

如果演示期内不能补齐合格 Benchmark，产品表述必须进一步降级为“本地 LLM 规格检索与部署候选筛选”，不能宣传为真实性能推荐系统。

## 1. 为什么必须取舍

`DC_Guide.md` 中很多字段有业务价值，但当前数据库没有对应的实体、列或关系。仅仅因为 `import_data.py` 的 `HARDWARE_TYPES` 接受 `CPU/RAM/SSD/PSU`，不代表这些类别已经可用：`hardware` 表没有 CPU 核心数、Socket、内存容量、SSD 接口或 PSU 功率等级等字段。

当前数据库的真实表达能力如下：

| 需求                               | 当前能否正确表达                             | V2 决策              |
| ---------------------------------- | -------------------------------------------- | -------------------- |
| 桌面 GPU 核心规格                  | 可以，字段较少                               | V2.0 采集            |
| Laptop GPU TGP 范围                | 不可以，只有单一`tdp_w`                    | 暂缓                 |
| CPU / 主板 / RAM / SSD / PSU 推荐  | 不可以，缺关键结构化字段与兼容关系           | 暂缓                 |
| 文本 Dense LLM + GGUF              | 基本可以                                     | V2.0 采集            |
| MoE / 多模态 / Runtime 兼容        | 不完整                                       | 暂缓                 |
| 本地 LLM 单卡实测                  | 可以，但环境只能写在一个字符串中             | V2.0 按固定协议采集  |
| 游戏 / 工程 Workload               | 不可以，没有 Workload 表                     | 暂缓                 |
| 游戏 Benchmark                     | 不可以，现表只能关联模型变体和一个硬件实体   | 暂缓                 |
| 价格快照                           | 不可以，没有地区、日期、价格类型和可用性字段 | 暂缓                 |
| 第三方 Benchmark / 零售 / 社区证据 | 不可以，`provenance` 枚举不支持            | 不进入 V2.0 Truth DB |

强行把上述信息塞入名称、`architecture`、`memory_type` 或 `test_environment`，虽然可能通过数据库类型检查，却会破坏筛选、去重和 Truth Verification，因此禁止。

## 2. 数据分层与进入数据库的门槛

```text
官方页 / 官方模型卡 / 受控项目实测
        ↓
data/raw/                 原始快照、摘录、临时表；不直接导入
        ↓ 人工核对、单位转换、去重
data/standardized/        唯一导入输入；五个 UTF-8 JSON 数组
        ↓ --dry-run
单事务导入 PostgreSQL
        ↓
SQL Agent 只读查询 + 后端 Truth Verification
```

数据分三层处理：

1. **结构化真值**：稳定、可比较、会用于过滤或验证的字段，写入实体列，并有逐字段 Evidence；
2. **原文证据**：来源如何表述该字段，写入 `evidence.raw_value`，不能代替结构化值；
3. **参考文本**：当前无法规格化且不对应现有列的信息，只留在 `data/raw/` 或采集待办中。在增加正式存储结构前，不允许进入推荐评分。

标准化文件只能包含已经人工核验、准备写库的数据。自动抓取结果、搜索摘要、LLM 提取结果和待确认字段不能直接进入 `data/standardized/`。

## 3. V2.0 采集范围和顺序

### 3.1 P0-A：先审计现有数据

在新增实体前先完成：

1. 现有五个 JSON 均可由当前导入器 `--dry-run` 通过；
2. 复核自然键和官方名称，特别是显存容量不同、区域版和 Desktop/Laptop 的拆分；
3. 复核 `file_size_gb` 单位，统一按第 6 节重新计算；
4. 复核 Evidence 的 `publisher` 是否为实际页面/仓库维护者；
5. 复核社区 GGUF 来源是否被错误标记为原模型发布方的 `official`；
6. 不以重新导入覆盖数据库中的冲突值；当前导入器遇到冲突会整批失败，修正已有库必须走单独的受控数据修订流程。

### 3.2 P0-B：硬件候选池

首批只维护当前已经覆盖的 NVIDIA GeForce RTX 30/40/50 系列桌面 GPU，目标是覆盖对本地 LLM 有意义的 8GB、12GB、16GB、24GB、32GB 显存档位，而不是“每代所有 SKU”。

新增硬件需同时满足：

- NVIDIA 正式发布的桌面独立 GPU；
- Ampere 或更新架构；
- 有直接官方产品页或 Datasheet；
- `vram_gb`、`memory_type`、`tdp_w` 至少可确认；
- 型号在当前用户仍可能持有或获得，对本地模型部署有区分价值。

以下差异必须拆成不同 `hardware` 记录，并编码进官方 `name`：

- 显存容量不同，例如 8GB / 16GB；
- Desktop / Laptop 不同；
- D / D v2 等正式区域型号不同；
- PCIe / SXM / NVL 等正式形态不同；
- 同名但部署能力或功耗边界显著不同的官方变体。

V2.0 不为“系列占位符”建记录，例如 `GeForce RTX 40 Series` 不能作为硬件实体。

### 3.3 P0-C：模型候选池

只采满足以下条件的具体模型版本：

- 权重可公开下载并允许本地部署；
- 当前版本为文本输入/输出、Dense 架构；
- 有 Instruct、Chat、Reasoning 或 Code 等明确用途；
- 有官方模型卡，可确认参数量、上下文和许可证；
- 有可用的 GGUF 变体；
- 在项目实际拥有的单卡硬件或相邻显存档位上具有部署意义。

目标不是追逐每一个最新系列名，而是形成小而有区分度的候选池：

- 小型档：约 7B～10B；
- 中型档：约 11B～20B；
- 大型单卡档：约 21B～40B；
- 40B 以上只保留少量对照项，不要求实测全覆盖。

`Qwen3.x`、`DeepSeek-V4` 等系列名只有在具体权重已经正式发布、可下载、来源可核验并满足当前 Schema 时才采；不能按路线图、新闻或推测预建记录。

### 3.4 P0-D：模型变体

每个模型首批最多维护：

- `Q4_K_M`：默认覆盖；
- `Q5_K_M`：质量/资源折中对照；
- `Q8_0`：仅在实测或演示确有需要时保留，不作为所有模型的完整性要求。

不采集大量低位宽 IQ 变体、个人临时仓库、网盘文件或没有说明的重打包。社区量化必须有稳定仓库、清晰模型卡、明确上游模型和可核对的文件列表。

### 3.5 P0-E：项目实测

完成条件不是“收集了一些网上跑分”，而是至少形成一组可复现的项目实测：

- 至少 1 张项目真实可用的 NVIDIA GPU；
- 至少 2 个不同参数档的模型；
- 每个模型至少 `Q4_K_M` 和 `Q5_K_M`；
- 每个变体至少测试 4096 与 8192 两个配置上下文；
- 最低形成 8 条成功、环境完整的 Benchmark 记录。

超出显存导致失败的运行暂不写入 `benchmark`，因为当前表没有 `success/error/oom` 字段。失败日志保留在 `data/raw/`；如果失败样本对推荐很重要，应先迁移 Schema 再正式采集。

## 4. 通用格式、自然键和名称

### 4.1 JSON 通用规则

- 文件编码为 UTF-8，顶层必须是数组，每个元素必须是对象；
- 建议两个空格缩进；
- 日期只用 `YYYY-MM-DD`；
- 数值使用 JSON number，不能混入单位；
- 布尔值只能用 `true` / `false`；
- 可空字段未知时使用 `null`；可选字段未知时可以省略；
- 禁止空字符串、`N/A`、`unknown`、`-1` 或猜测值；
- URL 必须为直接、稳定的 `http(s)` 页面，不用搜索页、短链或带跟踪参数的 URL；
- 五个文件都必须存在，没有记录时写 `[]`；
- 不写数据库 UUID，关联一律使用自然键，由导入器解析。

当前导入器采用严格字段白名单：多一个未实现字段也会失败。因此本文标记为“后续字段”的内容不能提前加进现有 JSON。

### 4.2 当前自然键

| 实体              | 当前自然键                                     | 使用要求                                       |
| ----------------- | ---------------------------------------------- | ---------------------------------------------- |
| `hardware`      | `manufacturer + name + type`                 | 所有正式变体差异必须体现在`name` 中          |
| `ai_model`      | `publisher + name`                           | `name` 必须是具体版本，不是系列占位符        |
| `model_variant` | 模型自然键 +`quantization + format + source` | 一个变体只选一个主仓库 URL；镜像不能重复建实体 |

`source` 被包含在模型变体唯一键中，因此 URL 改动会被视为新实体。收集时必须使用仓库根页的规范 URL，去掉结尾 `/`、查询参数和文件浏览临时路径。

### 4.3 名称规范

- 使用来源中的官方英文名称和大小写；
- 不在 `name` 中拼接自创别名、营销描述或价格；
- 由于当前没有 `variant/form_factor/region` 列，确属官方名称一部分的容量、Laptop、D、PCIe、SXM、NVL 等标识必须保留；
- 当前没有 aliases 表，不要用逗号把多个别名塞进 `name`；别名检索属于后续 Schema 扩展。

## 5. 五个标准化文件的准确契约

本节描述的是当前 `import_data.py` 实际接受的输入，不是理想化 Schema。

### 5.1 `hardware.json`

所有键都必须出现；后五个事实字段可以为 `null`。

| 字段             | 类型        | 可空 | V2 语义                                                 |
| ---------------- | ----------- | ---- | ------------------------------------------------------- |
| `name`         | string      | 否   | 官方具体型号；必须能区分正式变体                        |
| `type`         | string      | 否   | V2.0 固定为`GPU`                                      |
| `manufacturer` | string      | 否   | V2.0 固定规范名`NVIDIA`                               |
| `architecture` | string      | 是   | 官方架构名                                              |
| `vram_gb`      | number      | 是   | 官方标称显存容量，单位见第 6 节                         |
| `memory_type`  | string      | 是   | 如`GDDR6X`，不附容量                                  |
| `tdp_w`        | number      | 是   | 桌面卡使用官方板卡功耗/TGP 对应值；不得混入整机建议功率 |
| `release_date` | date string | 是   | 该具体变体的正式上市/可用日期，不确定则`null`         |

示例：

```json
[
  {
    "name": "GeForce RTX 3090 Ti",
    "type": "GPU",
    "manufacturer": "NVIDIA",
    "architecture": "Ampere",
    "vram_gb": 24,
    "memory_type": "GDDR6X",
    "tdp_w": 450,
    "release_date": "2022-03-29"
  }
]
```

CPU/RAM/SSD/PSU 虽然被当前导入器枚举接受，但 V2.0 禁止新增，因为这些记录无法支撑真实推荐。

### 5.2 `ai_models.json`

所有键都必须出现。两个布尔字段不能为 `null`，所以必须在入库前确认其操作性定义：

- `supports_vision=true`：该具体模型原生接受图像输入；V2.0 文本模型固定为 `false`，且模型卡应能确认其为文本模型；
- `supports_code=true`：发布方明确将该模型定位为代码模型或明确宣称代码能力；它不表示代码 Benchmark 一定优秀；
- 无法确认 `true/false` 的模型暂不入库，不能用 `false` 表示“未知”。

| 字段                | 类型             | 可空 | V2 语义                                           |
| ------------------- | ---------------- | ---- | ------------------------------------------------- |
| `name`            | string           | 否   | 具体、版本化的模型名                              |
| `publisher`       | string           | 否   | 原模型发布组织，不是量化仓库维护者                |
| `parameter_b`     | number           | 是   | Dense 模型总参数量，单位十亿参数                  |
| `context_length`  | positive integer | 是   | 官方声明的最大上下文 Token 数，不是本项目实测长度 |
| `supports_vision` | boolean          | 否   | 是否原生接受图像输入                              |
| `supports_code`   | boolean          | 否   | 是否被发布方明确定位为支持代码用途                |
| `license`         | string           | 是   | 官方许可证名称；不自行总结商业使用结论            |

MoE 模型需要总参数量和激活参数量，当前表只有一个 `parameter_b`，因此 V2.0 不采 MoE，不能任选一个数字填入。

### 5.3 `model_variants.json`

必填键为 `model_publisher`、`model_name`、`quantization`、`format`、`file_size_gb`、`source`；两个推荐内存字段为可选。

| 字段                    | 类型       | 可空/可省略 | V2 语义                                            |
| ----------------------- | ---------- | ----------- | -------------------------------------------------- |
| `model_publisher`     | string     | 否          | 必须匹配`ai_models.json`                         |
| `model_name`          | string     | 否          | 必须匹配`ai_models.json`                         |
| `quantization`        | string     | 否          | V2.0 规范为`Q4_K_M` / `Q5_K_M` / 按需 `Q8_0` |
| `format`              | string     | 否          | V2.0 固定为`GGUF`                                |
| `file_size_gb`        | number     | 是          | 一个可运行变体所有必需权重分片之和                 |
| `recommended_vram_gb` | number     | 是/可省略   | V2.0 原则上留空，不能由文件大小直接猜测            |
| `recommended_ram_gb`  | number     | 是/可省略   | V2.0 原则上留空，除非有已版本化推导规则和证据      |
| `source`              | URL string | 否          | 该量化变体的主仓库根页                             |

`recommended_vram_gb` 是变体级单值，却无法表达 Runtime、上下文、KV Cache、GPU offload 和并发条件。V2.0 优先让 Agent 查询 `benchmark`，不从“文件大小 + 固定余量”生成一个伪精确推荐值。

### 5.4 `evidence.json`

当前 Evidence 只能绑定以下事实字段：

| `entity_type`   | 合法`field`                                                                            |
| ----------------- | ---------------------------------------------------------------------------------------- |
| `hardware`      | `architecture`、`vram_gb`、`memory_type`、`tdp_w`、`release_date`              |
| `ai_model`      | `parameter_b`、`context_length`、`supports_vision`、`supports_code`、`license` |
| `model_variant` | `file_size_gb`、`recommended_vram_gb`、`recommended_ram_gb`                        |

记录格式：

```json
[
  {
    "entity_type": "hardware",
    "entity_key": {
      "manufacturer": "NVIDIA",
      "name": "GeForce RTX 3090 Ti",
      "type": "GPU"
    },
    "field": "vram_gb",
    "normalized_value": 24,
    "raw_value": "24 GB GDDR6X",
    "publisher": "NVIDIA",
    "source_url": "https://example.com/direct-official-product-page",
    "provenance": "official"
  }
]
```

规则：

- `normalized_value` 必须与实体 JSON 中相应字段类型和值完全一致，不能为 `null`；
- 每个实体的每个非空事实字段至少有一条 Evidence，导入器会全局检查；
- `raw_value` 应保存支持该字段的最短必要原文或结构化页面值；
- `publisher` 写实际页面/仓库发布者，不能把社区量化仓库写成原模型组织；
- 一个字段可有多条 Evidence，但不要复制相同页面制造虚假覆盖率；
- 当前 `retrieved_at` 由数据库在导入时生成，不是网页访问日期。原始采集日期应记在原始材料中，标准化后应尽快导入；
- 当前 Evidence 不接受 `name`、`manufacturer`、`publisher`、`quantization`、`format`、`source` 等身份字段。这是已知 Schema 缺口，不得用伪造的 `field` 绕过；
- `benchmark` 不在 Evidence 的合法 `entity_type` 中，项目实测由 Benchmark 记录本身承担。

### 5.5 `benchmarks.json`

当前表只支持“一个模型变体 × 一个硬件实体”的本地 LLM 项目实测。所有键都必须出现；导入器允许部分指标为 `null`，但 V2 验收要求更严格。

| 字段                    | 类型             | V2 验收要求                                      |
| ----------------------- | ---------------- | ------------------------------------------------ |
| `hardware_key`        | object           | 必须解析到实际测试的 GPU                         |
| `variant_key`         | object           | 必须解析到准确的模型变体及仓库                   |
| `context_length`      | positive integer | 测试时配置的上下文窗口，不是本次 Prompt Token 数 |
| `peak_vram_gb`        | number/null      | 成功运行原则上必填；按固定监控方法测得           |
| `ram_gb`              | number/null      | 可可靠测量时填写；方法写入环境字符串             |
| `tokens_per_second`   | number/null      | 成功运行必填；只记录生成阶段速度                 |
| `first_token_latency` | number/null      | 成功运行必填；单位固定为毫秒                     |
| `test_environment`    | string/null      | V2 成功记录必填，使用第 8 节格式                 |

Benchmark 不接受网上测评数字。第三方结果既不是 `measured` 项目实测，当前表也没有来源、测试日期和 Workload 维度，必须留到后续 Schema。

## 6. 单位、精度和空值

### 6.1 统一单位

| 字段                                    | 规范                                                         |
| --------------------------------------- | ------------------------------------------------------------ |
| `vram_gb` / `ram_gb` / 推荐内存字段 | GiB；能取得字节值时除以`2^30`                              |
| `file_size_gb`                        | 所有必需文件精确字节数之和除以`2^30`，数据库按两位小数保存 |
| `tdp_w`                               | W                                                            |
| `parameter_b`                         | 十亿参数，数据库最多三位小数                                 |
| `tokens_per_second`                   | 生成 Token/s，数据库最多三位小数                             |
| `first_token_latency`                 | ms，数据库最多三位小数                                       |

显卡官方标称“24 GB”等容量可按标称数值写入 `vram_gb`，并在 Evidence 保留原始单位。模型仓库页面常用十进制 `GB` 显示文件大小；V2 必须优先读取精确文件字节数并换算为 GiB，不能直接抄页面显示值后再声称是 GiB。

仓库现有 `file_size_gb` 数据是在本规范冻结前收集的，需在 P0-A 中复核。`backend/app/tools/tables.py` 当前把这些列解释为 GiB，本文沿用该语义。

### 6.2 数值精度

- 不保存来源没有提供的伪精度；
- 文件大小、VRAM 和 RAM 按数据库列精度四舍五入到两位小数；
- `normalized_value` 使用与实体 JSON 完全相同的舍入后数值；
- 不把区间平均成单值；当前列不能表达区间时，字段留空并暂缓对应场景；
- 不把 `null`、0 和“不适用”混为一谈。0 只在来源明确为真实零值时使用。

## 7. Evidence 来源和 `provenance`

当前数据库只允许三个值，含义必须严格固定：

### 7.1 `official`

用于厂商、原模型发布组织或该官方制品仓库直接发布的事实，例如 GPU 规格、模型参数量、上下文、许可证、官方 GGUF 文件信息。

“知名聚合站”“社区维护仓库”“原模型作者名称写在页面里”都不自动等于 `official`。

### 7.2 `measured`

用于项目组直接完成并保存方法的观察或测量，例如从目标制品的精确文件元数据计算出的实际字节总量，或由项目实测支撑的变体级字段。若社区 GGUF 的 `file_size_gb` 进入库，应把实际仓库维护者写入 `publisher`，并用 `raw_value` 记录所加总的文件和字节数。

`benchmark` 表本身没有 `provenance` 或 Evidence 外键；它只能保存项目实测，不能因为此处存在 `measured` 枚举就导入第三方跑分。

### 7.3 `derived`

只用于有版本号、输入值、公式和边界条件的确定性规则。`raw_value` 必须能说明推导，`source_url` 必须指向可访问的版本化规则文档。没有已冻结规则时不得使用。

### 7.4 当前不能入库的来源

第三方硬件测评、MLPerf 外部结果、零售价格、论坛、Reddit、视频和聚合站在 `DC_Guide.md` 中分别可作 Tier B～D 线索，但当前 `provenance` 约束无法准确标识它们。V2.0 不把这些来源伪装成 `official/measured/derived`。需要它们时先扩展 Schema、导入器和验证逻辑。

## 8. LLM Benchmark 固定协议 RB-LLM-BENCH-1

### 8.1 固定口径

- Runtime：llama.cpp/llama-server，CUDA 后端；
- 单 GPU；明确 GPU offload 层数，默认全量 offload，做不到时必须如实记录；
- 每个测试先 1 次 warm-up，再做至少 3 次正式运行；
- `tokens_per_second`：三次生成阶段 Token/s 的中位数；
- `first_token_latency`：请求提交到收到第一个输出 Token 的毫秒数中位数，包含 Prompt evaluation；
- `peak_vram_gb`：正式运行期间该进程/Runtime 对目标 GPU 的峰值已用显存；监控工具和采样方式必须记录；
- `ram_gb`：若填写，使用进程峰值 RSS/Working Set；不能可靠取得则为 `null`，不得用整机内存容量代替；
- `context_length`：Runtime 配置的上下文窗口；实际 `prompt_tokens` 和 `output_tokens` 单独写在环境字符串；
- 同一比较组使用完全相同的 Prompt 文本、采样参数和输出 Token 上限；
- 发生 OOM、加载失败或输出异常时不生成成功 Benchmark 记录。

### 8.2 `test_environment` 格式

当前没有结构化环境表，暂时使用稳定的分号分隔 `key=value` 字符串。至少包含：

```text
protocol=RB-LLM-BENCH-1;
test_date=YYYY-MM-DD;
os=<name/version>;
cpu=<model>;
system_ram_gib=<number>;
gpu=<exact model>;
driver=<version>;
runtime=llama.cpp;
build=<commit or release>;
backend=CUDA;
gpu_layers=<value>;
ctx_size=<value>;
prompt_id=<versioned prompt id>;
prompt_tokens=<value>;
output_tokens=<value>;
batch=<value>;
ubatch=<value>;
flash_attn=<on/off>;
sampling=<short normalized settings>;
vram_monitor=<tool/method>;
warmup=1;
runs=3;
aggregation=median
```

这是允许使用字符串的明确场景，因为环境主要用于复现和解释，不直接作为硬约束字段。缺少 build、GPU、上下文、Prompt Token、输出 Token 或 runs 的记录不能进入 V2 合格 Benchmark 集。

## 9. 采不到的字段如何处理

按以下顺序处理，不能跳级：

### 9.1 第一选择：可靠结构化

当字段会用于 SQL 过滤、确定性规则、排序或 Truth Verification，且来源可比较时，才进入正式结构化列。例如 `vram_gb`、`context_length`、`file_size_gb`。

### 9.2 第二选择：`null` + 保留原始材料

来源没有给出、各来源口径不一致或当前列无法正确表达时，写 `null` 或省略可选字段。不要猜值。若该字段是某个场景的硬条件，则该实体暂时不能参与该场景推荐。

### 9.3 第三选择：受限自由文本

字符串只能用于：

- `evidence.raw_value`：支持一个已经存在的结构化字段；
- `benchmark.test_environment`：记录复现环境；
- `data/raw/`：待清洗的原始说明。

它可以让审核员或 LLM看到上下文，但不能作为确定性筛选、自动评分或“已验证 Claim”。禁止把“支持 CUDA 12.x，某些框架可能有限制”之类说明塞进 `architecture` 或 `memory_type`。

### 9.4 最后选择：砍掉字段、实体或功能

出现以下任一情况时直接缩小范围：

- 关键字段长期只有互相冲突的非官方来源；
- 当前数据库无法表达区间、组合条件或多实体测试环境；
- 无法建立可比较的 Benchmark 协议；
- 数据维护成本明显超过其对课程核心“真值验证和多模型推荐”的贡献；
- 自由文本只能让 LLM凭经验猜结论，无法被后端验证。

这不是数据缺失的临时掩盖，而是产品边界：例如 Laptop GPU 没有 TGP 范围时，就不面向笔记本性能推荐用户。

## 10. 可选的后续 `entity_attribute` 迁移

如果 P0 实测闭环完成后，确实需要让 SQL Agent 读取无法统一成固定列的长尾规格，可增加一张窄表，而不是继续给 `hardware` 添加几十个高度可空列：

```text
entity_attribute
- id UUID
- entity_type
- entity_id
- key
- normalized_value JSONB nullable
- value_text TEXT nullable
- unit nullable
- status: normalized | text_only
- UNIQUE(entity_type, entity_id, key)
```

使用规则：

- 能可靠标准化时写 `normalized_value`，并固定 `key` 和单位；
- 只能保存说明时写 `value_text` 且 `status=text_only`；
- `text_only` 仅供解释和人工复核，不参与硬约束、数值排序或自动 Claim 支持；
- 价格快照、Workload、游戏 Benchmark 和兼容关系不能塞入该表，它们需要独立实体和时间/关系字段。

此方案**不属于当前 V2.0 输入契约**。启用前必须同时完成：

1. Alembic 迁移和 SQLAlchemy 模型；
2. 新的 `entity_attributes.json` 校验和导入顺序；
3. Evidence 对 `attribute:<key>` 的绑定规则；
4. `ALLOWED_TABLES`、表/列语义和 SQL Validator 更新；
5. Recommendation Claim Schema 与 Truth Verification 更新；
6. 回归测试和真实 PostgreSQL 验收。

只改数据库而不改验证链路，不会让字符串成为可信数据。

## 11. 导入器的实际行为和操作流程

### 11.1 当前行为

`backend/scripts/import_data.py` 会：

- 强制读取五个固定文件；
- 拒绝未知键、错误类型、非法 URL、负数、重复自然键和无法解析的外键；
- 要求所有非空实体事实字段有直接 Evidence；
- 按硬件、模型、变体、Evidence、Benchmark 顺序写入；
- 使用一个数据库事务，任一冲突或错误导致整批回滚；
- 相同记录跳过，已存在自然键但字段值不同则报冲突，不会自动更新；
- `--dry-run` 仍会连接真实数据库并走完整事务，只是在结束时回滚；它不是纯离线 JSON 校验器。

### 11.2 每批操作

1. 为本批建立任务清单，写明实体范围、来源负责人和审核人；
2. 保存原始页面关键信息或精确文件元数据到 `data/raw/`；
3. 按本文规范写入五个标准化 JSON；
4. 人工检查自然键、单位、`publisher`、URL 和 Evidence 对应关系；
5. 在 `backend/` 目录执行：

   ```powershell
   ..\.venv\Scripts\python.exe -m scripts.import_data --dry-run
   ```
6. 审核 dry-run 的 added/skipped 数量；
7. 经维护者确认后去掉 `--dry-run` 正式导入；
8. 再次运行 dry-run，预期所有记录为 skipped，验证幂等；
9. 用只读 SQL 抽查实体、Evidence 和 Benchmark 关联。

`backend/scripts/check_db.py` 只执行 `SELECT 1` 检查连接，不验证表结构、数据完整性或导入结果，不能代替上述步骤。

## 12. 单条记录验收

### 12.1 Hardware

- [ ] 是 V2.0 范围内的 NVIDIA 桌面 GPU；
- [ ] 官方具体名称和正式变体已确认；
- [ ] `manufacturer + name + type` 唯一；
- [ ] 显存、显存类型和功耗没有混用其他变体；
- [ ] 每个非空事实字段至少一条直接 Evidence；
- [ ] 单位和日期符合本文；
- [ ] 没有用名称承载非身份类长文本。

### 12.2 AI Model

- [ ] 是具体、已发布、可下载的文本 Dense 模型；
- [ ] 发布组织和官方模型卡已确认；
- [ ] 参数量、上下文、模态和许可证有证据；
- [ ] `supports_code` 使用本文定义，不把主观代码水平写成布尔事实；
- [ ] 不用 `false` 表示未知；
- [ ] 不把系列占位、API-only 或未来版本写入。

### 12.3 Model Variant

- [ ] 精确关联到已有模型；
- [ ] 量化名和格式规范；
- [ ] 主仓库稳定且可访问；
- [ ] 分片文件大小按精确字节求和并换算；
- [ ] 社区仓库维护者没有被冒充为原模型发布方；
- [ ] 没有凭文件大小猜 `recommended_vram_gb`；
- [ ] 镜像没有被当成第二个相同变体。

### 12.4 Evidence

- [ ] `field` 是当前允许字段；
- [ ] `normalized_value` 与实体值严格一致；
- [ ] `raw_value` 确实支持该字段；
- [ ] `publisher` 是实际来源方；
- [ ] `source_url` 是直接页面；
- [ ] `provenance` 没有为了通过约束而错误分类；
- [ ] 同页重复证据没有被用于抬高覆盖率。

### 12.5 Benchmark

- [ ] 是项目组真实执行的成功运行；
- [ ] 精确硬件和模型变体可解析；
- [ ] 使用 `RB-LLM-BENCH-1`；
- [ ] Token/s 与 TTFT 口径正确；
- [ ] 至少三次正式运行并取中位数；
- [ ] 环境字符串包含所有必填键；
- [ ] 没有混入第三方结果或 OOM 失败记录；
- [ ] 不同测试条件的数据没有直接平均。

## 13. 功能开放门槛

| 功能                  | 数据/实现门槛                                 | 未满足时的产品行为                 |
| --------------------- | --------------------------------------------- | ---------------------------------- |
| GPU 规格筛选          | 核心字段和 Evidence 完整                      | 可展示规格，不保证运行             |
| 模型/变体筛选         | 模型和 GGUF 变体完整                          | 可展示候选，不把文件大小当显存需求 |
| “该 GPU 能跑该模型” | 同 GPU 实测，或已版本化且可验证的推导规则     | 标记“未验证/信息不足”            |
| 性能比较              | 同协议、同条件 Benchmark                      | 不比较 Token/s 或延迟              |
| Laptop 推荐           | TGP 区间、整机散热/CPU/RAM 和对应实测 Schema  | 不支持该受众                       |
| 完整装机推荐          | CPU/平台/内存/SSD/PSU 结构化表及兼容规则      | 不输出完整配置单                   |
| 游戏/工程推荐         | Workload、版本、条件化 Benchmark 和来源表     | 不支持该场景                       |
| 性价比/预算推荐       | CNY 价格快照、地区、日期、可用性和更新机制    | 不声称性价比或预算最优             |
| 第三方数据融合        | 扩展来源类型、测试条件、Evidence 与可信度规则 | 第三方数据只作采集线索             |

## 14. V2 完成定义

V2 数据工作只有同时满足以下条件才算完成：

1. 五个标准化文件通过当前导入器 dry-run，正式导入后再次运行全部 skipped；
2. 现有实体的自然键、单位、实际发布者和 Evidence 分类完成一次审计；
3. 至少 8 条 `RB-LLM-BENCH-1` 成功实测进入数据库；
4. 系统可用 SQL 从硬件到模型变体、Evidence、Benchmark 完成一次闭环查询；
5. 对没有实测或推导依据的“可运行/更快”结论稳定输出信息不足；
6. 前端和演示文案不超出第 0 节定义的用户范围；
7. 若上述 Benchmark 无法完成，则正式砍掉性能推荐，只保留规格检索和候选筛选。

V2 的质量标准不是“字段最多”，而是：进入数据库的每个值有明确语义，系统知道哪些能比较、哪些只能阅读、哪些必须拒绝回答。
