# RigBuilder 数据收集说明 V3.1（面向调查员）

> 状态：执行规范（面向数据收集员）。日期：2026-09-03。
> 依据：[Data_Collection_Spec_V3.md](Data_Collection_Spec_V3.md)（数据库与契约的正式定义）。
> 本文件只讲一件事：**怎么采集、怎么填写、怎么提交**。数据库结构和代码约定不在本文件范围内。

## 0. 你的任务

一句话：**每个对象写一个 JSON 文件，填入事实，每条事实附上证据出处，标记为 pending，提交审核。**

- 你不需要懂数据库。数据库表结构已经冻结，你填的是 JSON 文件，导入由程序完成。
- 你不需要写得完美。拿不到的值留空（`null`），不要猜。
- 你写的每一个数字，原则上都要能回答"这是从哪个网页/文件看到的"。

## 1. 五个基本概念

| 概念          | 是什么                                                     | 例子                                                  |
| ------------- | ---------------------------------------------------------- | ----------------------------------------------------- |
| 实体 Entity   | 一个被收录的对象                                           | 一张显卡、一个模型、一个游戏                          |
| entity_key    | 实体的"永久身份证"，一串可读字符串，**定了就不能改** | `hw:nvidia:geforce-rtx-4090:desktop:global`         |
| 来源 Source   | 一条信息的出处网页/文件                                    | NVIDIA 官网规格页、一篇评测                           |
| 证据 Evidence | "某个实体的某个字段，值是多少，出自哪个来源"               | RTX 4090 的 vram_gib=24，出自官网规格页               |
| Bundle        | 你提交的一个 JSON 文件                                     | `data/v3/catalog/hardware/gpu/nvidia/rtx-4090.json` |

### 1.1 entity_key 命名规则

entity_key 一旦被审核接受就**永久不可变**，改名等于换了一个人。请按以下前缀命名（小写、用冒号和短横线分隔）：

| 前缀          | 用于                                               | 示例                                                                                      |
| ------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `org:`      | 组织（厂商、发布方、量化维护者、零售商、测评机构） | `org:nvidia`、`org:bartowski`                                                         |
| `hw:`       | 硬件                                               | `hw:nvidia:geforce-rtx-4090:desktop:global`、`hw:intel:core-i5-14600k:desktop:global` |
| `model:`    | AI 模型（发布方 + 模型名）                         | `model:meta:llama-3.1-8b-instruct`                                                      |
| `variant:`  | 模型部署制品（量化方法/格式/维护者不同就要拆开）   | `variant:meta-llama-3.1-8b-instruct:gguf:q4-k-m:bartowski`                              |
| `workload:` | 游戏/软件 Workload                                 | `workload:game:counter-strike-2`                                                        |

> 注意：`drafts/legacy/` 里的旧草稿用的是 `hardware:` 前缀，审核转正时会统一改掉。**你新写的数据请直接用上表前缀。**

## 2. 文件放哪里

```text
data/v3/
├─ schemas/          JSON Schema（程序用，不用你改）
├─ dictionaries/     受控词表（可选值查这里）
├─ sources/          来源文档
├─ catalog/          目录实体
│  ├─ hardware/gpu/{nvidia,amd,intel}/
│  ├─ hardware/cpu/{intel,amd}/
│  ├─ hardware/profiles/{platform,memory,storage,psu}/
│  ├─ models/{qwen,deepseek,llama,gemma,mistral,glm,phi}/
│  └─ workloads/{games,engineering}/
├─ benchmarks/{llm,games,cpu,gpu,model-capability}/
├─ prices/YYYY-MM/
└─ releases/         正式发布（只有审核人放文件）

data/v3/drafts/      待审核区（pending 草稿，永远不会被导入数据库）
```

铁律：

1. **pending 的文件放在 `drafts/` 下**，或按上面的正式目录放但 `status` 保持 `"pending"`；
2. **`drafts/` 里的文件永远不会进数据库**——导入器直接拒绝；
3. 正式 Release 必须位于 `drafts/` 之外、全部为 accepted、且由审核人生成 manifest（见第 7 节）。

## 3. Bundle 通用结构

每个文件就是一个 Bundle，五个顶层字段：

| 字段               | 怎么填                                                                      |
| ------------------ | --------------------------------------------------------------------------- |
| `schema_version` | 固定写`"3.1"`                                                             |
| `record_type`    | 这个文件的类型（见第 4 节），如`hardware`、`model`、`source_document` |
| `status`         | 你提交时一律写`"pending"`；只有审核人可以改成 `"accepted"`              |
| `payload`        | 实际内容                                                                    |
| `review_notes`   | 数组。有任何拿不准的地方写在这里，给审核人看                                |

所有实体类 payload 的开头都是 `identity`：

```json
"identity": {
  "entity_key": "hw:nvidia:geforce-rtx-3060:desktop:global",
  "canonical_name": "GeForce RTX 3060",
  "entity_type": "hardware",
  "release_date": "2021-02-25",
  "lifecycle_status": "active",
  "recommendable": true
}
```

| identity 字段        | 说明                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------- |
| `entity_key`       | 见 1.1                                                                                  |
| `canonical_name`   | 官方全名，照抄官方写法                                                                  |
| `entity_type`      | `organization` / `hardware` / `ai_model` / `model_variant` / `workload`       |
| `release_date`     | 官方发布日期，`YYYY-MM-DD`；不知道就 `null`                                         |
| `lifecycle_status` | `announced` / `upcoming` / `active` / `legacy` / `discontinued` / `unknown` |
| `recommendable`    | 是否允许被推荐给用户。数据没采全就先写`false`                                         |

一个真实的最小硬件例子（简化自现有草稿）：

```json
{
  "schema_version": "3.0",
  "record_type": "hardware",
  "status": "pending",
  "payload": {
    "identity": {
      "entity_key": "hw:nvidia:geforce-rtx-3060:desktop:global",
      "canonical_name": "GeForce RTX 3060",
      "entity_type": "hardware"
    },
    "hardware_type": "desktop_gpu",
    "manufacturer_key": "org:nvidia",
    "gpu_spec": {
      "architecture": "Ampere",
      "vram_gib": 12,
      "memory_type": "GDDR6",
      "board_power_w": 170
    },
    "evidence": [
      {
        "source_key": "source:nvidia-rtx-3060-specs",
        "field_key": "gpu.vram_gib",
        "normalized_value": 12,
        "raw_value": "12 GB GDDR6"
      }
    ]
  },
  "review_notes": []
}
```

## 4. 各类数据怎么填

### 4.1 硬件（`record_type: "hardware"`）

`hardware_type` 只能是八个值之一：`cpu`、`desktop_gpu`、`laptop_gpu`、`datacenter_gpu`、`memory`、`storage`、`psu`、`platform`。

常用字段（都写在 payload 里，与 spec 同名）：

- 通用：`manufacturer_key`（指向 `org:` 实体）、`product_family`、`model_name`、`variant_name`、`market_segment`（consumer/professional/datacenter/workstation/mobile）、`form_factor`、`region_code`（默认 `GLOBAL`）、`official_product_id`。
- 显卡写 `gpu_spec`：`architecture`、`vram_gib`、`memory_type`、`memory_bus_width_bit`、`memory_bandwidth_gb_s`、`ecc_support`、`interface`、`pcie_generation`、`pcie_lanes`、`board_power_w`。
- CPU 写 `cpu_spec`：`architecture`、`socket`、`cores_total`、`threads`、`performance_cores`、`efficiency_cores`、`base_clock_mhz`、`boost_clock_mhz`、`base_power_w`、`max_power_w`、`l3_cache_mib` 等。
- 笔记本 GPU 另建 `hardware_type: "laptop_gpu"` 的记录，写 `tgp_min_w` / `tgp_max_w`。

规则：

- 厂商没给的值写 `null`，**不要用 0 或 false 占位**；
- 长尾参数（Tensor Core 数、编解码器、显示输出等）不要硬塞进 `gpu_spec`，写在 `attributes` 数组里，每条形如 `{"field_key": "gpu.tensor_cores", "value_integer": 512}`（只能有一个值字段）；词表里没有的字段先在 review_notes 里说明。证据仍挂在 payload 的 `evidence` 数组，用同一个 `field_key`。

### 4.2 组织（`record_type: "organization"`）

字段很少：`identity` + `website_url`、`organization_type`（vendor/publisher/maintainer/retailer/media…）、`country_or_region`。硬件厂商、模型发布方、量化维护者**必须各建各的组织实体**，不许合并。当前 V3.1 合约未定义 `country_code`，不要写入 Bundle。

### 4.3 模型与变体（`record_type: "model"`）

模型主体字段：`publisher_key`（指向 `org:`）、`model_type`（`dense` / `moe`）、`total_parameters` 与 `active_parameters`、`model_stage`（base/instruct/chat/reasoning）、`context_length_tokens`、`license_name`、`commercial_use_status`、`official_repo_url`、`official_model_card_url`。

- **参数量写整数个数**：8.03B 写 `"total_parameters": 8030000000`，不要写 8.03；
- MoE 模型必须分别填 `total_parameters` 和 `active_parameters`；
- 能力写在 `capabilities` 数组：`{"capability_key": "vision", "status": "supported"}`。status 只能是 `supported` / `unsupported` / `partial` / `unknown`。**只有官方明确说了支持/不支持才能写 supported/unsupported，否则写 `unknown`**——这是 V2 教训（旧数据里 false 被迫全部改回 unknown）。

模型文件写在 `variants` 数组里，每个变体有自己的 `identity`（`variant:` 前缀）：

- `quantization_method`（如 `q4-k-m`）、`weight_format`（`gguf`/`safetensors`/`awq`/`gptq`）、`bits_per_weight`、`maintainer_key`、`repository_url`、`artifact_revision`、`official_status`（official/community/converted_by_project）；
- `file_size_bytes` 要**精确字节数**（从仓库 API 或文件属性拿）。只看到"约 4.9 GB"就别填这个字段，放 review_notes 说明；
- 量化方法、格式、维护者、Revision 任何一项不同，就拆成不同 variant。

### 4.4 来源（`record_type: "source_document"`）

每个出处一个文件：

- `source_key`：稳定键，建议 `source:<发布方>-<页面短名>`；
- `title`、`url`（必须完整可访问）、`publisher`、`source_type`（`official_page`/`datasheet`/`model_card`/`paper`/`project_measurement`/`independent_benchmark`/`retailer`/`community`/`derived_rule`）、`retrieved_at`（你**实际看到**这个页面的时间，ISO 8601）、`language`、`market_region`、`archive_url`（有就存）；
- `source_tier` 分级：

| 等级 | 是什么                               | 能干什么                           |
| ---- | ------------------------------------ | ---------------------------------- |
| A    | 官方一手（厂商规格页、模型卡、论文） | 支撑关键事实                       |
| B    | 测试条件完整的独立测评/论文          | 支撑 Benchmark                     |
| C    | 大型零售/市场渠道                    | 支撑价格                           |
| D    | 论坛、社区经验                       | 只做线索和补充，不能单独撑关键事实 |

### 4.5 Workload（`record_type: "workload"`）

游戏和工程软件都算。`workload_type`：`game` / `engineering` / `productivity` / `benchmark_suite`。

- 官方最低/推荐配置写在 `profiles` 数组：`profile_key`（`minimum`/`recommended`/`high_1080p`…）、`version_label`、`source_key`；
- 每条配置的拆行写在 `requirements` 数组：`component_role`（cpu/gpu/ram/storage/os/feature）、`operator`（`gte`/`eq`/`one_of`/`text_only`），再按事实类型填写 `reference_entity_key`、`numeric_value + unit_key`、`required_feature_key` 或 `value_text`；
- 官方只有一句"需要 DX12 兼容显卡"这类话时，用 `"operator": "text_only"` + `"value_text": "需要 DX12 兼容显卡"`，**不要自己翻译成数值**；
- 分辨率、画质、RT、Upscaler 是测试/Profile 设置，不是硬件属性，写在 Benchmark 的 `settings` 或 Profile 的 `settings` 里。

### 4.6 价格（`record_type: "price_snapshot"`）

- `price_key`（如 `price:hw:...:cn:2026-09`）、`entity_key`、`source_key`、`observed_at`（看到价格的时间）、`currency: "CNY"`、`amount`；
- `price_type`：`msrp` / `launch` / `current_new` / `current_used`；`condition`：`new` / `used` / `refurbished`；
- **二手价单独立条目，绝不和新件价混在一起**；价格只追加、不覆盖，上个月的记录不动。

### 4.7 Benchmark（`record_type: "benchmark_run"`）

- 先确认有对应 `benchmark_protocol`（如项目协议 `RB-LLM-BENCH-1`）；Run 必须同时填写准确的 `protocol_key` 和 `protocol_version`；
- `run_key` 唯一；`started_at` 必填并使用 ISO 8601 时间；另填 `run_origin`（project/independent/official）、`test_date`、`status`（`success` / `partial` / `oom` / `failed` / `invalid`）；
- 测试对象写 `subjects` 数组（`entity_key` + `role`：model_variant / gpu / cpu / workload / platform），环境写 `environment`（OS、驱动、Runtime 版本、内存）；
- 结果写 `metrics` 数组：`metric_key`（如 `llm.generation_tokens_per_second`、`game.average_fps`）、值、`statistic`（reported/median/…）；
- **失败和 OOM 也照样提交**——"跑不动"是有价值的真值，程序会保证它们不混进成功结果；
- 不同分辨率/画质/Runtime 的结果分开成不同 Run，不要合并平均。

### 4.8 Runtime 支持（`record_type: "runtime_support"`）

某个 variant 在某个 Runtime（llama.cpp/vllm/…）上能不能跑：`snapshot_key`（唯一键）、`variant_key`、`runtime`、`version`、`support_status`、`observed_at`、`min_version`、`backend`、`source_key`。**只新增快照，不修改旧记录**——"上个月不支持、这个月支持了"本身就是数据。

## 5. 证据（evidence）怎么挂

payload 里的 `evidence` 数组，每条对应一个字段值：

| 字段                    | 说明                                                                                                                        |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `source_key`          | 指向第 4.4 节的来源文件，**必须是已存在的 source_key**                                                                |
| `field_key`           | 证据支撑的字段，如`gpu.vram_gib`、`model.total_parameters`                                                              |
| `raw_value`           | 页面原文（照抄那一句），最重要的字段                                                                                        |
| `normalized_value`    | 你归一化后的值（数字就写数字）                                                                                              |
| `quote` / `locator` | 可选：更长的原文 / 出处位置（如"规格表第 2 行"）                                                                            |
| `provenance_key`      | `official` / `project_measured` / `independent_measured` / `derived` / `retail_observed` / `community_reported` |

常用 `field_key` 对照：

```text
gpu.vram_gib  gpu.board_power_w  gpu.architecture  gpu.memory_type  gpu.memory_bandwidth_gb_s
cpu.cores_total  cpu.socket  cpu.boost_clock_mhz  cpu.max_power_w
model.total_parameters  model.context_length_tokens  model.license  model.capability.<名字>
variant.file_size_bytes  variant.repository_url  variant.quantization_method
entity.canonical_name  hardware.category  hardware.manufacturer  organization.website_url
```

规则：

1. **一条证据只绑定一个字段**，不要写"整页都是依据"；
2. `normalized_value` 必须和实体里实际填的值一致（导入器会核对）；
3. `entity.canonical_name`、`hardware.manufacturer` 这类身份字段也要有证据；
4. 两个来源打架时：两条证据都提交，在 review_notes 里写明冲突，由审核人标记，**不要自己挑一个删掉另一个**。

## 6. 单位与数值

| 数据      | 单位/写法                   | 正确示例                          | 错误示例                            |
| --------- | --------------------------- | --------------------------------- | ----------------------------------- |
| 显存/内存 | GiB，字段名带`_gib`       | `vram_gib: 24`                  | `vram_gb: 24`（旧字段）           |
| 文件大小  | 精确字节`file_size_bytes` | `4939212390`                    | `"4.9 GB"`（只能进 review_notes） |
| 频率      | MHz                         | `boost_clock_mhz: 2520`         | `2.52 GHz`                        |
| 带宽      | GB/s                        | `memory_bandwidth_gb_s: 1008`   | `1 TB/s`                          |
| 功率      | W                           | `board_power_w: 450`            | `0.45 kW`                         |
| 参数量    | 整数个数                    | `total_parameters: 8030000000`  | `8.03`                            |
| 价格      | `amount` + `currency`   | `amount: 4999, currency: "CNY"` | `"约5000元"`                      |
| 上下文    | tokens                      | `context_length_tokens: 131072` | `128K`                            |

三条底线：

1. **不猜值**：官方没写就是 `null`；不要用邻近型号的数补；不要拿文件大小当运行显存；
2. **未知 ≠ 不支持**：不知道就 `unknown`/`null`；`false` 和 `unsupported` 只在官方明确说不支持时用；
3. **一个事实只写一个地方**：固定字段已有的值不要重复写进 attributes。

## 7. 提交与审核流程

```text
调查员：写 Bundle（status=pending）-> 按 §8 自查 -> 交给审核人
审核人：核对来源与数值 -> 改为 accepted -> 移入正式 Release 目录（drafts 之外）
审核人：backend 目录运行
    ..\.venv\Scripts\python.exe -m scripts.build_v3_manifest --release-dir <目录> --release-key <如 rigbuilder-v3-2026-09> <文件...>
    -> 生成带 SHA-256 的 manifest.json
导入由运维执行 import_v3（先 dry-run 再 apply），与你无关
```

会被打回的典型原因：

- `raw_value` 缺失或不是原文照抄；
- 数值和 `normalized_value` 对不上；
- 用了词表之外的状态值（如 capability 写了 `maybe`）；
- entity_key 拼错、重复或大小写不规范；
- price/benchmark/runtime 修改了历史记录而不是新增；
- `file_size_bytes` 来自"约 XX GB"的目测换算。

## 8. 提交前自查清单

- [ ] 每个 JSON 文件能通过 `data/v3/schemas/bundle.schema.json` 的直觉检查（字段名拼写、枚举值）；
- [ ] entity_key 用了正确前缀（`org:`/`hw:`/`model:`/`variant:`/`workload:`），且不会与现有实体重复；
- [ ] 所有数字字段没有单位、千分位和文字混入；
- [ ] 拿不到的值写 `null`，没有用 0/false/空字符串占位；
- [ ] 每个非空的关键字段都有 evidence，`raw_value` 是页面原文；
- [ ] 每条 evidence 的 `source_key` 指向一个真实提交的来源文件；
- [ ] 来源的 `retrieved_at` 是真实采集时间（不是占位值）；
- [ ] capability 只有官方明确说法才写 `supported`/`unsupported`，否则 `unknown`；
- [ ] 价格/Benchmark/Runtime 是新增条目，没有改动历史记录；
- [ ] `status` 是 `"pending"`，所有拿不准的点写进了 `review_notes`。
