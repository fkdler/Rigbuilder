# RigBuilder 数据收集与 Truth DB 规范 V3

> 状态：最终目标 Schema 冻结候选
> 日期：2026-09-02
> 依据：[Data_Collection_Spec_V1.md](Data_Collection_Spec_V1.md)、[Data_Collection_Spec_V2.md](Data_Collection_Spec_V2.md)、[DC_Guide.md](DC_Guide.md)、[Plan_V2.4.md](../Plan_documents/Plan_V2.4.md) 以及当前 SQLAlchemy/Alembic/Importer 实现。
> V3 经评审并完成首个 Alembic 迁移后，作为新数据唯一目标；V1/V2 仅用于旧数据迁移和历史追溯。

## 0. 核心决定

V3 不再以当前五张最小表为长期边界，而是先按最终业务目标冻结数据库结构，再让数据采集和代码开发并行推进。

最终业务目标仍是：

> **面向个人电脑配置、硬件升级、游戏/工程 Workload 和本地 AI 部署的多模型真值推荐系统。**

V3 采用以下结构：

1. 所有可引用对象使用统一 `catalog_entity.id`，解决 Evidence、Benchmark、价格和推荐候选的多态关联；
2. 硬件身份放在 `hardware`，CPU、GPU、Laptop GPU、数据中心 GPU 和其他组件用分类规格表；
3. 高频、会参与硬约束和 SQL 过滤的字段使用强类型固定列；
4. 长尾但仍有价值的规格使用带字段定义的类型化 `entity_attribute`，新增长尾字段不迁移数据库；
5. Benchmark 使用通用 Run/Subject/Metric 模型，未来新增 FPS、1% Low、Token/s、CPU 分数或新指标只增加定义和数据；
6. 价格使用时间快照，Runtime 支持也保留检查时间，不覆盖历史；
7. Evidence 同时支持官方、项目实测、独立测评、零售和社区来源，不再伪装成三个不够用的枚举；
8. SQL Agent 查询稳定只读视图，不直接面对全部规范化底表；
9. 数据文件使用稳定 `entity_key`/`source_key` 关联，不依赖数据库 UUID，可在数据库实现完成前开始采集；
10. 旧五表仅转换为待审核草稿；正式切换删除旧五表并以 `entity_key` 重新生成 UUID，不保留旧 UUID。

V3 的目标不是保证数据库永远零变更，而是让绝大多数新增型号、属性、来源、Benchmark 指标和 Runtime 变成**新增数据**，只有出现新的实体关系或时间模型时才需要 Schema 迁移。

## 1. 最终范围与边界

### 1.1 V3 支持的数据域

- CPU：消费级、移动端、工作站；
- GPU：桌面消费卡、Laptop GPU、专业卡、可独立部署的数据中心/AI 卡；
- 平台：Socket、芯片组和平台级兼容能力；
- 内存、SSD、电源：规格级 Profile，不建设品牌 SKU 全库；
- Workload：固定核心游戏、工程/机械专业软件及其版本化系统需求；
- 本地模型：Dense/MoE、文本/视觉等正式开放权重模型；
- 模型制品：GGUF、Safetensors、AWQ、GPTQ 等具体部署变体；
- Runtime 支持：llama.cpp、Transformers、vLLM、SGLang 等版本化支持状态；
- Benchmark：项目实测和条件完整的外部独立测评；
- 价格：CNY MSRP/首发价和带时间的当前新件参考价；二手价独立标记；
- Evidence：字段级来源、原始表述、规范化值、来源等级和采集时间；
- 兼容关系与确定性规则：Socket、内存、接口、功耗和部署条件。

### 1.2 继续排除

- 笔记本、品牌台式机、OEM 主机和服务器整机 SKU；
- DGX/HGX/NVL72 等机柜级系统作为普通推荐候选；
- 全量主板、内存条、SSD、电源的品牌/零售 SKU 库；
- 自动联网抓取后未经审核直接写 Truth DB；
- 把 LLM 生成内容、搜索摘要或论坛共识作为官方事实；
- 将推荐结果本身保存为“硬件真值”。动态生成的整套配置属于 Decision Result，不属于产品目录。

### 1.3 覆盖能力与数据完成度分离

V3 Schema 一次支持最终范围，但不要求第一批把所有域采满。代码可以先实现 GPU/LLM，采集员可同时准备 CPU、Workload 和价格数据；未达到验收门槛的域不在前端开放。

## 2. 稳定性设计原则

### 2.1 核心列、扩展属性和动态记录三分法

| 数据类型               | 存储方式                                | 示例                                              |
| ---------------------- | --------------------------------------- | ------------------------------------------------- |
| 高频、稳定、硬约束字段 | 分类规格表固定列                        | GPU 显存、Laptop TGP 范围、CPU 核心数、Socket     |
| 长尾或厂商特有字段     | `field_definition + entity_attribute` | CUDA Compute Capability、显示输出、特殊编解码能力 |
| 随时间或测试条件变化   | 快照/Run 表                             | 当前价格、Runtime 支持、游戏 FPS、LLM Token/s     |

同一个事实只能有一个规范存储位置。固定列已经存在的字段不得再复制到 `entity_attribute`。

### 2.2 不使用 PostgreSQL ENUM 承载可扩展业务词表

`category`、`market_segment`、`form_factor`、`source_type`、`metric_key` 等使用受控字符串，并由版本化词表和导入器校验。新增正式类型时更新词表，不做数据库类型迁移。

数据库仍对长度、非负值、范围、唯一键、外键和“一次只能填写一种类型值”等结构规则使用约束。

### 2.3 `null` 的统一含义

- 固定列的 `null` 只表示“当前未知/未确认”；
- “明确不支持”使用 `false` 或状态 `unsupported`；
- “不适用”由实体分类决定，或在属性状态中写 `not_applicable`；
- 禁止用 `false` 表示未知，禁止用 0 表示缺失。

### 2.4 单位进入字段名或定义表

- 固定列直接带单位，例如 `_w`、`_mhz`、`_mib`、`_gib`、`_gb_s`；
- 可扩展字段和 Benchmark 指标由定义表指定唯一规范单位；
- 原始单位保存在 Evidence，规范化时只转换一次。

## 3. 总体数据模型

正式切换在同一个 PostgreSQL 事务中按外键顺序删除 `public` 旧五表并新建独立 `truth` Schema；其他 `public` 应用/审计表保持不变。SQL Agent 只获得 `agent_catalog` Schema 中只读视图的 `SELECT` 权限。

```text
truth.catalog_entity
├── truth.organization
├── truth.hardware
│   ├── truth.cpu_spec
│   ├── truth.gpu_spec
│   │   ├── truth.laptop_gpu_spec
│   │   └── truth.datacenter_gpu_spec
│   ├── truth.memory_spec
│   ├── truth.storage_spec
│   ├── truth.psu_spec
│   └── truth.platform_spec
├── truth.ai_model
│   └── truth.model_variant
└── truth.workload

truth.catalog_entity ──< truth.entity_alias
truth.catalog_entity ──< truth.entity_attribute >── truth.field_definition
truth.catalog_entity ──< truth.evidence_claim >── truth.source_document
truth.catalog_entity ──< truth.compatibility_edge >── truth.catalog_entity
truth.catalog_entity ──< truth.benchmark_subject >── truth.benchmark_run
truth.benchmark_run  ──< truth.benchmark_metric >── truth.metric_definition
truth.catalog_entity ──< truth.price_snapshot >── truth.source_document
```

### 3.1 表组

| 表组         | 表                                                                                                                                                                                 |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 统一身份     | `catalog_entity`、`organization`、`entity_alias`                                                                                                                             |
| 可扩展事实   | `field_definition`、`entity_attribute`                                                                                                                                         |
| 硬件         | `hardware`、`cpu_spec`、`gpu_spec`、`laptop_gpu_spec`、`datacenter_gpu_spec`、`memory_spec`、`storage_spec`、`psu_spec`、`platform_spec`、`compatibility_edge` |
| 本地模型     | `ai_model`、`model_capability`、`model_variant`、`runtime_support_snapshot`                                                                                                |
| Workload     | `workload`、`workload_profile`、`workload_requirement`                                                                                                                       |
| 来源与事实   | `source_document`、`evidence_claim`                                                                                                                                            |
| Benchmark    | `benchmark_protocol`、`metric_definition`、`benchmark_run`、`benchmark_subject`、`benchmark_metric`                                                                      |
| 动态市场数据 | `price_snapshot`                                                                                                                                                                 |
| 确定性规则   | `rule_definition`                                                                                                                                                                |
| 数据治理     | `dataset_release`、`import_batch`                                                                                                                                              |

底表数量较多，但 SQL Agent 不直接查询这些表，而是查询第 12 节定义的少量业务视图。

ORM 实现约定：分类子表的统一实体主外键列名为 `entity_id`；Field/Metric Definition 同时使用内部 UUID `id` 和唯一稳定键 `field_key/metric_key`。关联表可使用内部 UUID 主键，但本节列出的自然键仍由 UNIQUE 约束强制，Importer 只使用稳定键生成引用。

### 3.2 全局约束与索引

- 所有实体主键为 UUID，所有外键在数据库层真实声明；
- 分类子表使用 `hardware_id` 作为 PK/FK，保证与硬件一对一；
- `entity_key`、`source_key`、`field_key`、`metric_key` 和 `rule_key + version` 建唯一索引；
- 高频筛选列建立组合索引，例如 GPU 显存/形态、CPU Socket/核心数、模型参数/上下文；
- Attribute 建立 `(field_key, value_number)`、`(field_key, value_integer)`、`(field_key, value_boolean)` 等部分索引；
- Evidence 建立 `(entity_id, field_key, review_status)` 和 `source_document_id` 索引；
- Benchmark 建立 `(protocol_id, status, test_date)`、Subject 的 `(entity_id, subject_role)` 和 Metric 的 `(metric_key, value_number)` 索引；
- Price 建立 `(entity_id, market_region, condition, observed_at DESC)` 索引；
- 动态历史表默认禁止物理覆盖旧记录，通过最新视图选取当前状态。

## 4. 统一实体、组织和别名

### 4.1 `truth.catalog_entity`

所有能够被 Evidence、Benchmark、价格、兼容关系或 Recommendation Claim 引用的对象先创建统一实体。

| 字段                            | 类型          | 规则                                                               |
| ------------------------------- | ------------- | ------------------------------------------------------------------ |
| `id`                          | UUID          | 主键；统一由稳定 `entity_key` 生成 UUIDv5                           |
| `entity_key`                  | string        | 全局唯一、不可变、可读的稳定键                                     |
| `entity_type`                 | string        | `organization/hardware/ai_model/model_variant/workload` 等受控值 |
| `canonical_name`              | string        | 官方规范名称                                                       |
| `release_date`                | date nullable | 具体实体正式发布日期                                               |
| `lifecycle_status`            | string        | `active/discontinued/legacy/upcoming/unknown`                    |
| `recommendable`               | boolean       | 是否允许作为推荐候选，不代表数据完整                               |
| `created_at` / `updated_at` | timestamptz   | 数据库审计时间                                                     |

`entity_key` 示例：

```text
org:nvidia
hw:nvidia:geforce-rtx-4090:desktop:global
hw:nvidia:a100-80gb:pcie:global
model:meta:llama-3.1-8b-instruct
variant:meta-llama-3.1-8b-instruct:gguf:q4-k-m:bartowski
workload:game:counter-strike-2
```

名称修正、URL 改动和别名增加不得改变 `entity_key`。所有 V3 实体统一使用以下规则生成确定性 UUID；旧实体 UUID 不进入 V3：

```text
UUIDv5(NAMESPACE_URL, "https://rigbuilder.local/entity/" + entity_key)
```

### 4.2 `truth.organization`

以 `catalog_entity.id` 为主外键，保存厂商、模型发布组织、仓库维护者、零售商和测评机构：

```text
entity_id
organization_type
website_url
country_or_region
```

硬件厂商、原模型发布方和社区量化维护者必须是不同组织实体，不能因为同一制品同时出现其名称而混用。

### 4.3 `truth.entity_alias`

```text
id
entity_id
alias
alias_type          abbreviation / localized / former / search
language
normalized_alias
```

`UNIQUE(entity_id, normalized_alias)`。别名只用于搜索和解析，不能替代官方名称参与去重。

## 5. 硬件表

### 5.1 `truth.hardware`：统一硬件身份

每条记录代表一个具体产品 Variant 或一个明确的规格级 Profile。

| 字段                    | 说明                                                       |
| ----------------------- | ---------------------------------------------------------- |
| `entity_id`           | PK/FK →`catalog_entity.id`                              |
| `manufacturer_id`     | FK →`organization.id`；通用规格 Profile 可空            |
| `record_kind`         | `product/spec_profile`                                   |
| `category`            | `cpu/gpu/memory/storage/psu/platform`                    |
| `product_family`      | 系列，例如正式产品家族                                     |
| `model_name`          | 型号主体                                                   |
| `variant_name`        | 容量、后缀或正式变体                                       |
| `market_segment`      | `consumer/professional/datacenter/workstation/mobile` 等 |
| `form_factor`         | `desktop/laptop/pcie/sxm/nvl/udimm/rdimm/m2/...`         |
| `region_code`         | `GLOBAL/CN/...`，非区域版用 `GLOBAL`                   |
| `official_product_id` | ARK ID、官方 Product ID 等，可空                           |

去重优先级：官方 Product ID；若没有，则使用制造商、官方名称、Variant、形态、容量和区域共同确定 `entity_key`。

### 5.2 `truth.cpu_spec`

```text
hardware_id UUID PK/FK
architecture
codename
cores_total INTEGER
threads INTEGER
performance_cores INTEGER
efficiency_cores INTEGER
base_clock_mhz INTEGER
boost_clock_mhz INTEGER
l2_cache_mib NUMERIC
l3_cache_mib NUMERIC
base_power_w NUMERIC
max_power_w NUMERIC
socket
memory_channels INTEGER
max_memory_gib NUMERIC
ecc_support BOOLEAN nullable
pcie_generation NUMERIC
pcie_lanes INTEGER
integrated_gpu
npu_model
```

NPU TOPS、混合核心组成、特定内存速度等口径多变字段通过类型化 Attribute 保存，字段键必须带精度或条件，例如 `cpu.npu_tops.int8`，不能把 NPU TOPS 与平台总 AI TOPS 混用。

### 5.3 `truth.gpu_spec`

所有桌面、Laptop、专业和数据中心 GPU 共有：

```text
hardware_id UUID PK/FK
architecture
vram_gib NUMERIC
memory_type
memory_bus_width_bit INTEGER
memory_bandwidth_gb_s NUMERIC
ecc_support BOOLEAN nullable
interface
pcie_generation NUMERIC
pcie_lanes INTEGER
board_power_w NUMERIC nullable
```

CUDA Core、Stream Processor、Xe Core 等厂商指标名称和可比性不同，通过 Attribute 使用不同 `field_key` 保存，不合并成一个虚假的“GPU 核心数”。Tensor/RT 世代、Compute Capability、显示输出、编码器和电源接口也使用 Attribute。

### 5.4 `truth.laptop_gpu_spec`

只用于 `form_factor=laptop`：

```text
hardware_id UUID PK/FK
tgp_min_w NUMERIC
tgp_max_w NUMERIC
boost_clock_min_mhz INTEGER
boost_clock_max_mhz INTEGER
dynamic_boost_w NUMERIC nullable
```

Laptop GPU 仍是规格级实体，不代表具体笔记本性能。没有 TGP 范围或测试整机环境时，只能做规格筛选。

### 5.5 `truth.datacenter_gpu_spec`

用于专业/数据中心部署特性：

```text
hardware_id UUID PK/FK
mig_support BOOLEAN nullable
nvlink_support BOOLEAN nullable
nvlink_bandwidth_gb_s NUMERIC nullable
display_enabled BOOLEAN nullable
max_power_w NUMERIC nullable
```

FP64、TF32、BF16、FP16、FP8、INT8 以及未来 FP4 等理论指标通过 Attribute 保存：

```text
gpu.compute.fp64.tflops
gpu.compute.bf16_tensor_dense.tflops
gpu.compute.fp8_tensor_sparse.tflops
```

字段定义中必须固定是否 Tensor、是否稀疏和单位，不允许只写一个含义不明的 `fp16`。

### 5.6 规格级组件表

这些表允许 `hardware.record_kind=spec_profile`，不要求制造商。

`truth.memory_spec`：

```text
hardware_id
memory_standard
form_factor
capacity_gib
speed_mt_s
ecc
registered
voltage_v nullable
```

`truth.storage_spec`：

```text
hardware_id
storage_type
interface
pcie_generation nullable
capacity_gib
sequential_read_mb_s_class nullable
sequential_write_mb_s_class nullable
typical_use_case
```

`truth.psu_spec`：

```text
hardware_id
atx_standard
rated_power_w
efficiency_certification
connector_profile JSONB
```

`truth.platform_spec`：

```text
hardware_id
chipset
socket
memory_standard JSONB
max_memory_gib nullable
pcie_generation nullable
overclock_support BOOLEAN nullable
```

`connector_profile`、`memory_standard` 使用受控 JSON 数组，因为它们是多值集合；元素结构由 V3 JSON Schema 固定，不能保存任意说明文字。

### 5.7 `truth.compatibility_edge`

用于不能仅靠相同字段 JOIN 表达的兼容或排除关系：

```text
id
source_entity_id
target_entity_id
relation_key          supports / incompatible / requires / recommended_with
status                confirmed / conditional / deprecated
conditions JSONB nullable
valid_from / valid_to nullable
evidence_required BOOLEAN
source_document_id UUID nullable
rule_key / rule_version nullable
```

Socket、内存标准、物理接口等可由确定性规则判断的关系不需要枚举每一对产品；只有官方例外、条件关系或无法由字段推导的关系才建 Edge。确认关系必须至少引用 Source 或已版本化 Rule，不能只有一段无来源备注。

## 6. 本地模型与部署制品

### 6.1 `truth.ai_model`

```text
entity_id UUID PK/FK
publisher_id UUID FK
family
release_version
model_type               dense / moe
total_parameters BIGINT nullable
active_parameters BIGINT nullable
model_stage              base / instruct / chat / reasoning
context_length_tokens BIGINT nullable
license_name
commercial_use_status    allowed / restricted / unknown
official_repo_url
official_model_card_url
architecture_notes TEXT nullable
```

MoE 的总参数和激活参数分别保存。不能用同一个 `parameter_b` 猜测其含义。

### 6.2 `truth.model_capability`

能力和模态使用三态/多态记录，不再使用不可空布尔值：

```text
model_id
capability_key       text_input / vision_input / audio_input / code / reasoning / tool_use / ...
support_status       supported / unsupported / unknown / partial
notes nullable
UNIQUE(model_id, capability_key)
```

能力强弱由统一能力 Benchmark 表达，`supported` 只表示存在该能力，不表示水平优秀。

### 6.3 `truth.model_variant`

一条记录代表一个可下载、可定位的部署制品，而不是任意镜像 URL：

```text
entity_id UUID PK/FK
model_id UUID FK
maintainer_id UUID FK
quantization_method
bits_per_weight NUMERIC nullable
weight_format
file_size_bytes BIGINT nullable
artifact_revision nullable
official_status       official / community / converted_by_project
repository_url
artifact_manifest JSONB nullable
checksum nullable
```

`repository_url` 不再参与唯一键。仓库迁移或 URL 规范化不会制造新 Variant；真正不同的量化方法、格式、维护者或 Revision 才拆记录。分片制品的 `file_size_bytes` 为所有必需文件之和；`artifact_manifest` 使用受控数组保存文件角色、路径、大小、Checksum 和是否必需，可表达分片权重与多模态 Projector，不能放任意仓库元数据。

### 6.4 `truth.runtime_support_snapshot`

Runtime 支持会变化，必须保留检查时间：

```text
id
variant_id
runtime_key            llama_cpp / transformers / vllm / sglang / ...
support_status         supported / unsupported / partial / unknown
min_version nullable
tested_version nullable
backend nullable
checked_at timestamptz
notes nullable
source_document_id
```

同一 Variant/Runtime 可有多条历史快照；Agent 默认查询最新记录。

## 7. Workload 与系统需求

### 7.1 `truth.workload`

```text
entity_id UUID PK/FK
publisher_id UUID nullable
workload_type          game / engineering / productivity / benchmark_suite
family
current_version nullable
engine nullable
official_url
```

游戏和工程软件都使用 Workload；版本差异由 Profile 的 `version_label` 和有效期表达。

### 7.2 `truth.workload_profile`

```text
id
workload_id
profile_key            minimum / recommended / high_1080p / high_1440p / ...
version_label
settings JSONB
valid_from / valid_to nullable
source_document_id
```

官方最低/推荐配置与项目定义的性能目标必须使用不同 `profile_key` 和来源，不能混为一条记录。

### 7.3 `truth.workload_requirement`

每个 Profile 的要求按组件拆行：

```text
id
profile_id
component_role         cpu / gpu / ram / storage / os / feature
operator               eq / gte / one_of / text_only
reference_entity_id nullable
numeric_value nullable
unit_key nullable
value_text nullable
required_feature_key nullable
raw_value
```

能规范化时使用实体引用或数值；只能保留厂商自然语言时使用 `operator=text_only`。`text_only` 可供 LLM 解释，但不能自动证明某硬件满足要求。

游戏的分辨率、画质、RT、Upscaler 和 Frame Generation 是 Benchmark/Profile 设置，不是 GPU 永久属性。

## 8. 长尾字段与字符串降级

### 8.1 `truth.field_definition`

```text
id UUID PK
field_key UNIQUE
applies_to_entity_type
value_type          number / integer / boolean / string / date / json
canonical_unit nullable
cardinality         one / many
comparison_method   exact / tolerance / set_contains / range / text_only
tolerance nullable
storage_kind        column / attribute
storage_path nullable
filterable BOOLEAN
claimable BOOLEAN
description
active BOOLEAN
```

固定列也注册 `field_key`，例如：

```text
gpu.vram_gib                    -> gpu_spec.vram_gib
cpu.cores_total                 -> cpu_spec.cores_total
laptop_gpu.tgp_min_w            -> laptop_gpu_spec.tgp_min_w
model.context_length_tokens     -> ai_model.context_length_tokens
```

### 8.2 `truth.entity_attribute`

```text
id
entity_id
field_key
qualifier_key string        default / vendor-defined stable key
value_number nullable
value_integer nullable
value_boolean nullable
value_text nullable
value_date nullable
value_json nullable
value_status       known / text_only / unknown / not_applicable
qualifier JSONB nullable
UNIQUE(entity_id, field_key, qualifier_key)
```

`qualifier_key` 是规范化条件的稳定摘要，避免直接用任意 JSON 做自然键；普通单值字段固定为 `default`。数据库 CHECK 约束要求最多只有一个 `value_*` 非空；导入器再根据 `field_definition.value_type` 校验准确类型、单位以及 `qualifier_key` 与 `qualifier` 的一致性。

### 8.3 降级顺序

1. 能可靠规范化且高频：固定列；
2. 能可靠规范化但长尾：类型化 Attribute；
3. 只能保存原文：`value_text + value_status=text_only`；
4. 无可靠来源或无法支持功能：不入库并降低功能范围。

`text_only` 字段允许 SQL Agent 阅读，但 `field_definition.claimable=false`，不能参与确定性硬约束、数值排序或自动 Truth Verification。

价格、Benchmark、Runtime 支持和兼容关系禁止放进 Attribute，因为它们有时间、条件或多实体关系。

## 9. 来源与 Evidence

### 9.1 `truth.source_document`

```text
id UUID
source_key UNIQUE
publisher_id nullable
source_tier          A / B / C / D
source_type          official_page / datasheet / model_card / paper /
                     project_measurement / independent_benchmark /
                     retailer / community / derived_rule / ...
title
url
archive_url nullable
published_at nullable
accessed_at timestamptz
market_region nullable
language nullable
content_hash nullable
availability_status  accessible / archived / unavailable
```

来源等级：

- A：官方/一手发布方；
- B：测试条件完整的独立 Benchmark 或论文；
- C：大型零售和市场渠道；
- D：社区经验，仅供补充和异常线索。

### 9.2 `truth.evidence_claim`

```text
id UUID
entity_id UUID FK
field_key FK
source_document_id UUID FK
normalized_value JSONB nullable
unit_key nullable
raw_excerpt TEXT nullable
source_locator nullable       page / section / table / file
provenance_key               official / project_measured /
                             independent_measured / derived /
                             retail_observed / community_reported
review_status                pending / accepted / rejected / conflict
collected_at timestamptz
reviewed_at nullable
notes nullable
```

规则：

- 身份字段也必须有 Evidence，例如 `entity.canonical_name`、`hardware.manufacturer`；
- `normalized_value` 必须按 `field_definition` 校验，并与当前规范值一致；
- 多来源冲突全部保留，通过 `review_status=conflict` 标记，不能覆盖原文；
- Tier D 不能单独支持关键结构化事实；
- Evidence 支持事实，不直接证明“这是最佳推荐”；
- 原文字符串只绑定一个明确 `field_key`，不得用整页备注替代字段级证据。

## 10. 通用 Benchmark 模型

现有 `benchmark` 表只能表达“模型变体 × 一张硬件”的五个指标，无法覆盖游戏、CPU、工程软件、失败运行和多组件环境。V3 改为通用 Run 模型。

### 10.1 `truth.benchmark_protocol`

```text
id
protocol_key
version
name
workload_type
method_document_url
settings_schema JSONB
environment_schema JSONB
active
UNIQUE(protocol_key, version)
```

协议定义哪些设置必填以及指标口径。`RB-LLM-BENCH-1` 作为首个项目协议迁移到这里。

### 10.2 `truth.metric_definition`

```text
id UUID PK
metric_key UNIQUE
value_type          number / integer / boolean / string
canonical_unit nullable
higher_is_better nullable
comparison_scope
description
active
```

示例：

```text
llm.generation_tokens_per_second
llm.first_token_latency_ms
llm.peak_vram_gib
game.average_fps
game.one_percent_low_fps
cpu.single_core_score
model.capability_score
```

新增指标只增加定义，不新增数据库列。

### 10.3 `truth.benchmark_run`

数据 Bundle 必须用 `protocol_key + protocol_version` 精确引用协议；只写 `protocol_key` 不足以区分同一协议的多个版本。

```text
id UUID
protocol_id
source_document_id
run_origin             project / independent / official
test_date
status                 success / partial / oom / failed / invalid
settings JSONB
settings_hash
environment JSONB
sample_count INTEGER
aggregation_method     median / mean / p95 / reported
raw_result_url nullable
raw_result_hash nullable
notes nullable
```

`settings` 示例包含上下文、Prompt、分辨率、画质、RT、Upscaler、采样参数；`environment` 包含 OS、CPU、GPU、RAM、驱动、Runtime 和版本。两者由 Protocol JSON Schema 校验，不接受随意键名。

失败和 OOM 是有价值的可行性证据，V3 正式保存，但不能写入成功性能视图。

### 10.4 `truth.benchmark_subject`

```text
run_id
entity_id
subject_role        workload / cpu / gpu / model / model_variant / platform
is_primary
quantity
PRIMARY KEY(run_id, entity_id, subject_role)
```

同一 Run 可同时关联游戏、CPU、GPU、模型变体和平台，从而完整表达测试环境。

### 10.5 `truth.benchmark_metric`

```text
run_id
metric_key
value_number nullable
value_integer nullable
value_boolean nullable
value_text nullable
statistic             reported / median / mean / p95 / min / max
sample_count nullable
PRIMARY KEY(run_id, metric_key, statistic)
```

数据库 CHECK 约束要求一个 Metric 只有一种 `value_*` 非空，且类型与 Metric Definition 一致。只允许同一 Protocol 版本、相同 Workload/设置及兼容环境下的指标直接比较。禁止跨分辨率、画质、Runtime 或 Benchmark 版本平均。

## 11. 价格、规则和数据治理

### 11.1 `truth.price_snapshot`

```text
id
entity_id
source_document_id
price_type           msrp / launch / current_new / current_used
amount NUMERIC(14,2)
currency             V3 首批 CNY
market_region        CN
condition            new / used / refurbished
availability         in_stock / out_of_stock / preorder / unknown
seller_id nullable
observed_at timestamptz
notes nullable
```

价格只追加快照，不覆盖历史。推荐查询使用相同地区、条件和合理时间窗口内的最新快照；二手价不与新件价混合。

### 11.2 `truth.rule_definition`

保存确定性兼容/估算规则的版本和说明：

```text
rule_key
version
rule_type
description
input_field_keys JSONB
output_field_key nullable
implementation_ref
source_document_id
active
```

公式在后端实现和测试，不把可执行代码存入数据库。Recommendation 的 `rule_refs` 引用 `rule_key + version`。

### 11.3 `truth.dataset_release` 与 `truth.import_batch`

`dataset_release` 保存数据版本、Git Commit、Manifest Hash、发布日期和状态；`import_batch` 保存导入开始/结束时间、Release、执行者、added/updated/skipped/rejected 数量和错误报告。

所有正式推荐结果记录所用 `dataset_release`，保证评估可复现。

## 12. SQL Agent 稳定视图与 Claim

### 12.1 `agent_catalog` 只读视图

第一版建议开放：

```text
agent_catalog.gpu_catalog
agent_catalog.cpu_catalog
agent_catalog.component_profile_catalog
agent_catalog.model_catalog
agent_catalog.model_variant_catalog
agent_catalog.runtime_support_latest
agent_catalog.workload_catalog
agent_catalog.workload_requirement_catalog
agent_catalog.benchmark_result
agent_catalog.price_latest
agent_catalog.fact_evidence
agent_catalog.entity_attribute_catalog
```

视图职责：

- 展平高频 JOIN，给 Agent 提供清晰业务字段；
- 过滤 rejected Evidence、invalid Benchmark 和非当前价格；
- 保留 `entity_id/entity_key`；
- 不把 `text_only` 属性包装成可比较数值；
- 不自动把不同 Benchmark 条件变成一行平均值。

新增长尾 Attribute 和 Metric 不需要修改底表；只有确实成为高频 Agent 字段时才可增加视图列。视图变化不要求重采已有数据。

### 12.2 Recommendation Claim V3

```text
claim_type       fact / measurement / derived / preference
entity_id
field_key nullable
metric_key nullable
value
evidence_ids []
benchmark_run_ids []
rule_refs []
```

验证规则：

- `fact`：按 `field_definition` 查询固定列或 Attribute，并核对 Evidence；
- `measurement`：必须引用条件相符的 Benchmark Run；
- `derived`：必须引用已版本化 Rule 和输入事实；
- `preference`：不冒充数据库事实，不计入事实支持率。

这样新增 Attribute 不需要把 Claim Schema 的 `field` 枚举写死在 Python 列名中。

## 13. V3 数据文件与并行采集

V3 不再要求所有人编辑五个巨型数组。标准化区按域和实体拆分，降低 Git 冲突：

```text
data/v3/
├─ schemas/                         JSON Schema
├─ dictionaries/                    受控词表、Field/Metric 定义
├─ sources/                         来源文档
├─ catalog/
│  ├─ hardware/
│  │  ├─ gpu/{nvidia,amd,intel}/
│  │  ├─ cpu/{intel,amd}/
│  │  └─ profiles/{platform,memory,storage,psu}/
│  ├─ models/{qwen,deepseek,llama,gemma,mistral,glm,phi}/
│  └─ workloads/{games,engineering}/
├─ benchmarks/{llm,games,cpu,gpu,model-capability}/
├─ prices/YYYY-MM/
└─ releases/                        Manifest 与审核结果
```

### 13.1 通用 Bundle

每个实体文件是一个 JSON Object，不写数据库 UUID：

```json
{
  "schema_version": "3.0",
  "record_type": "hardware",
  "status": "pending",
  "payload": {
    "identity": {
      "entity_key": "hw:vendor:example-gpu:desktop:global",
      "canonical_name": "Example GPU",
      "entity_type": "hardware",
      "release_date": null,
      "lifecycle_status": "unknown",
      "recommendable": false
    },
    "manufacturer_key": "org:vendor",
    "hardware_type": "desktop_gpu",
    "record_kind": "product",
    "product_family": "Example Family",
    "model_name": "Example GPU",
    "variant_name": null,
    "market_segment": "consumer",
    "form_factor": "desktop",
    "region_code": "GLOBAL",
    "official_product_id": null,
    "gpu_spec": {
      "architecture": null,
      "vram_gib": null,
      "memory_type": null,
      "memory_bus_width_bit": null,
      "memory_bandwidth_gb_s": null,
      "ecc_support": null,
      "interface": null,
      "pcie_generation": null,
      "pcie_lanes": null,
      "board_power_w": null
    },
    "attributes": [],
    "evidence": []
  },
  "review_notes": []
}
```

模型、Workload、Benchmark 和价格使用各自 JSON Schema，但都通过 `entity_key`、`source_key`、`field_key` 和 `metric_key` 关联。

### 13.2 收集与代码并行流程

```text
先冻结 schemas/ + dictionaries/ + 示例 Bundle
        ├── 数据组：按 Bundle 采集、审核、提交
        └── 代码组：V3 ORM、迁移、Importer、Views、Verifier
                         ↓
                   Importer 可用后批量 dry-run
```

在 V3 Importer 完成前可以收集和 JSON Schema 校验，但不得手工把 V3 Bundle 写进旧五表。

### 13.3 原始材料

`data/raw/` 继续不入 Git、不直接导入。标准化 Bundle 只保存最短必要原文、来源键和规范值；PDF/HTML/截图、完整测试日志和临时提取结果保存在原始区或受控对象存储。

## 14. 规范化规则

### 14.1 数值单位

| 数据           | 规范                                           |
| -------------- | ---------------------------------------------- |
| 显存/内存/容量 | GiB；能取得字节时除以`2^30`                  |
| 模型文件       | 精确`file_size_bytes`，显示时计算 GiB        |
| 频率           | 固定列 MHz                                     |
| 带宽           | GB/s，原始口径保留 Evidence                    |
| 功率           | W                                              |
| 参数量         | 实际参数数 BIGINT，显示时换算 B                |
| 价格           | amount + currency，不写在字符串中              |
| 延迟           | ms                                             |
| 吞吐           | Metric Definition 指定，例如 Token/s、frames/s |

### 14.2 Variant 拆分

以下任一正式差异影响部署或推荐时拆成不同实体：

- 显存容量；
- Desktop/Laptop；
- PCIe/SXM/NVL；
- 区域版本；
- 正式功耗范围；
- 显存位宽/带宽；
- 模型量化方法、格式、维护者或 Artifact Revision。

### 14.3 不猜值

- 官方未给出：`null` 或无属性记录；
- 只有说明文字：`text_only`；
- 可计算：必须有 `rule_key + version + inputs`；
- 来源冲突：全部保留并标记 conflict；
- 不用同架构邻近型号补值，不用文件大小直接等同运行显存。

## 15. 数据采集优先级

Schema 从 V3 首次迁移开始完整建立，数据按阶段填充。

### P0：迁移与核心闭环

- Field/Metric/词表与 JSON Schema；
- 现有 22 GPU、8 模型、24 Variant、174 Evidence 的审计迁移；
- NVIDIA RTX 30/40/50 桌面 GPU；
- 当前可部署的代表性文本模型和 GGUF；
- `RB-LLM-BENCH-1` 项目实测；
- Source/Evidence、Agent Views 和 Truth Verification。

### P1：完整个人电脑配置

- Intel/AMD 主流桌面 CPU；
- NVIDIA/AMD/Intel 消费级桌面 GPU；
- Socket/芯片组、内存、SSD、电源规格 Profile；
- 兼容与功耗规则；
- CNY 新件价格快照；
- 固定核心游戏与官方系统需求。

### P2：移动端、专业和工程场景

- Laptop GPU 及 TGP 区间；
- Ryzen AI/Core Ultra、Threadripper/Xeon W；
- NVIDIA 专业卡和可独立部署的 AI 卡；
- 工程软件 Workload；
- 条件完整的第三方游戏/工程 Benchmark；
- 多模态、MoE 和更多 Runtime。

### P3：扩展

- 更多地区、二手市场、长尾 Workload；
- 非 CUDA 数据中心计算卡；
- 只有在产品目标明确变化时才考虑具体整机 SKU。

阶段只控制“采多少”，不再临时改变实体关系或核心字段。

## 16. 从当前五表迁移

### 16.1 映射

| 当前表            | V3 目标                                          | 迁移规则                                                          |
| ----------------- | ------------------------------------------------ | ----------------------------------------------------------------- |
| `hardware`      | pending Hardware Bundle                       | 当前 22 条生成新 `entity_key`；不保留旧 `id`                    |
| `ai_model`      | pending Model Bundle                          | 参数 B 换算实际参数后仍需证据复核；不保留旧 `id`                |
| `model_variant` | pending Variant                               | URL 移出唯一键；`file_size_gb` 仅留待复核显示字符串              |
| `evidence`      | pending Source/Evidence                       | 按 URL 生成 `source_key`；字段映射为 `field_key`；不保留旧 ID   |
| `benchmark`     | `benchmark_run + subject + metric`             | 每条旧记录映射一个 Run；当前标准化数据为 0 条                     |

现有 `supports_vision=false`、`supports_code=false` 不能自动迁移为 `unsupported`，因为旧 Schema 无法区分“未知”和“明确不支持”。只有 Evidence 明确时才生成 Capability 状态，否则迁移为 `unknown`。

现有 `file_size_gb` 不直接乘 `2^30` 生成精确字节；必须根据制品文件元数据重新计算。

### 16.2 受控重建步骤

1. 先把旧 JSON 转换到 `data/v3/drafts/legacy/`，全部保持 pending；
2. 完成 V3 JSON Schema、完整 ORM、Importer、Agent Views 与验收工具；
3. 远端对整个数据库执行 `pg_dump` 并验证备份哈希；
4. `preflight_v3` 验证 Revision、权限、旧表范围与 Schema 冲突；
5. 单个 Alembic 事务按外键顺序删除旧五表并一次创建完整 V3 Schema/View；
6. 对审核通过的正式 Release 先 dry-run，再 apply；
7. 给 `agent_readonly` 只授予 `agent_catalog` 的 `USAGE/SELECT`；
8. 运行严格验收和代表性 Agent 查询，再次 dry-run 验证幂等；
9. 失败时停止应用并恢复 `pg_dump`，不使用 downgrade 恢复旧数据。

### 16.3 迁移验收

- V3 实体 ID 均严格等于基于 `entity_key` 的固定 UUIDv5，旧 UUID 不保留；
- 每条 accepted Evidence 能解析 Entity、Field 和 Source；
- 同一规范事实在固定列与 Attribute 中没有重复；
- V3 Views 的关键旧查询有等价结果；
- 旧 22/8/24/174 条输入均进入 pending 草稿且有转换报告；
- 应用/审计表保持不变，旧五表消失，V3 表组与视图完整；
- 回滚依赖已验证的整库备份，不依赖 Alembic downgrade。

## 17. Schema 冻结与变更政策

### 17.1 V3.0 冻结前必须完成

- 所有表的 SQLAlchemy Model 和 Alembic Migration 评审；
- JSON Schema、词表和自然键规则评审；
- 至少各用一个 CPU、桌面 GPU、Laptop GPU、数据中心 GPU、MoE 模型、游戏 Benchmark 和价格快照做样例导入；
- 对 Evidence、Attribute、Benchmark 和 Price 做边界测试；
- 使用真实 PostgreSQL 完成迁移和回滚演练。

### 17.2 冻结后哪些变化不改表

- 新硬件/模型/Workload/来源；
- 新别名、地区、Form Factor 受控值；
- 新长尾字段；
- 新 Benchmark 指标或 Protocol 版本；
- 新 Runtime 或支持快照；
- 新价格来源和月份；
- 新 Evidence 来源类型；
- 新规则版本。

### 17.3 只有这些情况允许 V3.x Schema 迁移

- 新需求无法由现有实体关系表达，而不仅是缺一个字段；
- 现有关系的基数或时间语义错误；
- 数据完整性无法通过现有约束和定义表保证；
- 安全、审计或性能问题有真实证据；
- 迁移提供向后兼容视图、数据回填和回滚方案。

不因为“某页面又出现一个规格名”增加列；优先新增 Field Definition。

## 18. V3 完成定义

V3 不是文档写完即完成。必须同时满足：

1. `truth` 底表、约束、索引和 `agent_catalog` 视图由 Alembic 创建；
2. V3 JSON Schema、示例 Bundle、Importer、`--validate-only` 和 `--dry-run` 可用；
3. 当前五表 JSON 完成 pending 草稿转换并有审计报告，正式 Release 只含 accepted Bundle；
4. Agent 只通过稳定视图查询，Truth Verification 使用 `field_key/metric_key`；
5. 至少一条 CPU、四类 GPU、规格组件、Dense/MoE 模型、Workload、成功/失败 Benchmark 和价格快照通过端到端验证；
6. 数据组可在不修改 Python 和数据库列的情况下增加一个新长尾字段和一个新 Benchmark 指标；
7. 数据 Release、Prompt、模型、规则和 Agent Run 可以互相追溯；
8. 完成真实 PostgreSQL 备份、原子迁移、幂等导入、恢复演练和只读权限验收。

V3 的冻结原则是：**稳定核心关系，开放受控扩展点，避免把不确定的世界全部硬编码成列，也避免把所有内容退化成无法验证的字符串。**
