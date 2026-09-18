# 数据收集与标准化规范 V1

本目录是 Truth DB 的受控数据缓冲区，数据只能按以下流程进入 PostgreSQL：

```text
官方页面 / 项目实测
        ↓
data/raw/                 原始材料；不入 Git、不直接入库
        ↓ 人工核对与标准化
data/standardized/        结构化 JSON；入 Git、可复现、可审核
        ↓ scripts/import_data.py（后续实现）
SQLAlchemy → PostgreSQL
```

当前项目尚未实现 `scripts/import_data.py`。本规范冻结其输入格式和行为要求，避免数据收集后需要返工。

## 1. 目录职责

```text
data/
├─ Data_Collection_Spec_V1.md       本文档（提交 Git）
├─ raw/                          原始网页摘录、PDF、截图、临时表格（不提交 Git，忽略）
└─ standardized/                 已核验、可导入的 JSON（提交 Git）
   ├─ hardware.json
   ├─ ai_models.json
   ├─ model_variants.json
   ├─ evidence.json
   └─ benchmarks.json
```

`raw/` 可以包含 HTML、PDF、网页摘录、截图、手工笔记或其他不统一格式；它仅用于追溯和人工审核，不能被导入脚本读取。敏感信息、账号 Cookie、下载的受版权保护材料不得提交到仓库。

`standardized/` 是唯一的可导入数据源。每次修改都应能通过 Git diff 审核其值与来源。

## 2. 通用标准

- 所有文件使用 UTF-8 编码、合法 JSON、顶层为数组、使用两个空格缩进。
- 日期使用 `YYYY-MM-DD`，例如 `2022-03-29`。
- 数值使用 JSON number：写 `24`、`450`、`4.7`，不写 `"24 GB"`、`"450 W"`。
- 布尔值使用 `true` / `false`，不能写 `"yes"` / `"no"`。
- 无法确认的可选值使用 `null`，不要用空字符串、`N/A` 或猜测值。
- 每条事实都应有直接支持它的 `evidence` 记录；`raw_value` 保留页面原文，`normalized_value` 保存供查询使用的标准值。
- URL 必须指向实际的来源页面，而非搜索结果页、首页或短链。
- 不在标准化文件中填写数据库 UUID。导入器负责查询或生成 UUID。
- 录入前去重：不能创建相同硬件、模型或模型变体的重复记录。

## 3. 数据优先级与来源

第一批以本地 AI 推荐最需要的数据为主：主流 GPU、常用开源模型、常见 GGUF/量化变体，以及少量真实实测。

来源优先级与 `provenance` 必须一致：

| 来源                            | `provenance` | 使用场景                                       |
| ------------------------------- | -------------- | ---------------------------------------------- |
| 厂商/模型发布方的官方页面或文档 | `official`   | 硬件规格、模型能力、许可证、官方发布的文件信息 |
| 项目组真实执行的测试            | `measured`   | 显存占用、吞吐、延迟、特定上下文是否可运行     |
| 可说明公式或规则的计算结果      | `derived`    | 推荐显存/内存等估算                            |

不要将论坛、测评转载或未经核验的聚合网站标记为 `official`。若来源不能归入这三类，暂时留在 `raw/`，待确认数据策略后再导入。

## 4. 标准化文件格式

### 4.1 `hardware.json`

数据库去重键是 `manufacturer + name + type`。当前优先录入 GPU；CPU、RAM、SSD、PSU 的 Schema 扩展需另行确认后再批量收集。

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
    "release_date": null
  }
]
```

`type` 推荐统一使用大写枚举：`GPU`、`CPU`、`RAM`、`SSD`、`PSU`。ORM 属性名是 `hardware_type`，但数据库列和标准化 JSON 均使用 `type`。

### 4.2 `ai_models.json`

数据库去重键是 `publisher + name`。

```json
[
  {
    "name": "Example-7B-Instruct",
    "publisher": "Example Org",
    "parameter_b": 7.0,
    "context_length": 32768,
    "supports_vision": false,
    "supports_code": true,
    "license": "Apache-2.0"
  }
]
```

### 4.3 `model_variants.json`

变体必须通过模型的自然键关联；导入器再查询对应 `model_id`。同一变体的去重键为 `model_id + quantization + format + source`。

```json
[
  {
    "model_publisher": "Example Org",
    "model_name": "Example-7B-Instruct",
    "quantization": "Q4_K_M",
    "format": "GGUF",
    "file_size_gb": 4.7,
    "recommended_vram_gb": 6,
    "recommended_ram_gb": 12,
    "source": "https://replace-with-the-direct-model-variant-page"
  }
]
```

`recommended_vram_gb` 和 `recommended_ram_gb` 如果来自公式估算，必须同时增加 `provenance: "derived"` 的 Evidence 解释推导依据。

### 4.4 `evidence.json`

数据库中的 `evidence` 使用 `entity_type + entity_id`，但尚未入库时没有 UUID。因此标准化文件必须使用 `entity_type + entity_key`；导入器根据自然键解析出 UUID。

不能只使用 `entity_name`，因为相同名称在不同厂商、模型发布方或变体下可能不唯一。

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
    "source_url": "https://replace-with-the-direct-official-product-page",
    "provenance": "official"
  }
]
```

可用的 `entity_type` 与 `entity_key`：

| `entity_type`   | `entity_key`                                                                |
| ----------------- | ----------------------------------------------------------------------------- |
| `hardware`      | `manufacturer`、`name`、`type`                                          |
| `ai_model`      | `publisher`、`name`                                                       |
| `model_variant` | `model_publisher`、`model_name`、`quantization`、`format`、`source` |

`field` 应与所描述实体的 Schema 字段名一致，例如 `vram_gb`、`tdp_w`、`parameter_b`、`context_length`、`file_size_gb`。对一个字段有多个来源时可保留多条 Evidence，但不得篡改原始值。

### 4.5 `benchmarks.json`

仅保存项目组真实测试。每条记录必须写明足以复现和解释结果的运行环境。

```json
[
  {
    "hardware_key": {
      "manufacturer": "NVIDIA",
      "name": "GeForce RTX 3090 Ti",
      "type": "GPU"
    },
    "variant_key": {
      "model_publisher": "Example Org",
      "model_name": "Example-7B-Instruct",
      "quantization": "Q4_K_M",
      "format": "GGUF",
      "source": "https://replace-with-the-direct-model-variant-page"
    },
    "context_length": 8192,
    "peak_vram_gb": 6.5,
    "ram_gb": 10.2,
    "tokens_per_second": 42.3,
    "first_token_latency": 180,
    "test_environment": "Windows 11; llama.cpp build <commit>; CUDA <version>; GPU offload <value>; batch size <value>"
  }
]
```

在批量录入 benchmark 前，团队必须冻结 `first_token_latency` 的单位。建议统一使用毫秒（ms）；当前列名未附带单位，不能混用秒、毫秒或微秒。

## 5. 审核清单

标准化记录进入 Git 或数据库前，收集者应确认：

1. 页面来源可信且 URL 可访问；
2. 数值没有单位、千分位或文字混入；
3. `raw_value` 保留与来源相符的原文；
4. `normalized_value` 与 Schema 的数据类型一致；
5. 硬件、模型与变体的自然键可唯一定位；
6. 所有外键引用的对象在对应标准化文件中已存在；
7. benchmark 为真实测试，且含可复现的 `test_environment`；
8. 未将 `raw/` 中的未核验数据直接入库。

## 6. 导入器的预期行为

未来的 `python -m scripts.import_data` 应：

1. 读取 `data/standardized/` 的五个 JSON 文件；
2. 先校验 JSON 格式、必填字段、数值范围和 URL；
3. 按 `hardware`、`ai_model`、`model_variant`、`evidence`、`benchmark` 的顺序处理；
4. 根据自然键解析外键 UUID；
5. 使用单一数据库事务：任一记录无法解析或校验失败时，整批回滚；
6. 输出新增、跳过、失败记录数和可定位的错误信息；
7. 提供 `--dry-run`，在不写数据库的情况下完成校验。

导入器是后台维护工具，不是未来 SQL Agent 的能力；SQL Agent 仍应保持只读。
