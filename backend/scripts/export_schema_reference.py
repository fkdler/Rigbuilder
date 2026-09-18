"""生成 truth 库的表/字段参考文档（只读，不改数据库）。

用途：把当前 PostgreSQL 的实际结构 + 中文语义导出成 Markdown，便于评审与后续修补。
运行：PYTHONPATH=<backend> python scripts/export_schema_reference.py
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sqlalchemy import text as sql

from app.db.session import engine

OUT = Path(r"D:\RigBuilder\docs\Data_documents\DB_Schema_Reference.md")

OBJECTS = """
SELECT c.table_schema, c.table_name, c.table_type
FROM information_schema.tables c
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY c.table_schema, c.table_type, c.table_name
"""

COLUMNS = """
SELECT table_schema, table_name, column_name, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name, ordinal_position
"""

PK = """
SELECT tc.table_schema, tc.table_name, kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
"""

FK = """
SELECT tc.table_schema, tc.table_name, kcu.column_name,
       ccu.table_schema AS ref_schema, ccu.table_name AS ref_table
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
"""

# ---------------------------------------------------------------- 语义（人工）
PURPOSE: dict[str, str] = {
    # ---- truth 核心身份与词典 ----
    "truth.catalog_entity": "**所有实体的统一身份表**。硬件、模型、组织、工作负载都先在这里登记一行，再由各自的扩展表补规格。`entity_type` 区分类型，`entity_key` 全局唯一，`recommendable` 决定它能否出现在候选里，`lifecycle_status` 表示生命周期。",
    "truth.organization": "厂商 / 发布方的扩展信息，1:1 挂在 `catalog_entity` 上：组织类型、官网、国家或地区。",
    "truth.entity_alias": "实体的别名与归一化别名，供模型写出的名称做唯一匹配用。**当前 0 行**，所以名称匹配只能靠 `canonical_name` 加噪声词剔除。",
    "truth.field_definition": "**字段词典，也是 TruthVerifier 的取值路由表。** 每个 `field_key` 在这里定义类型、单位、比较方法，以及取值从哪里读：`storage_kind=column` 走 `storage_path` 指定的固定列，`storage_kind=attribute` 走 `entity_attribute`。还有 `filterable`（可否作为查询条件）与 `claimable`（可否被证据支撑）。**当前只有 24 条**，是词典层面的主要瓶颈。",
    "truth.entity_attribute": "长尾类型化属性，一行一个字段值。六个类型列互斥（数据库有约束保证一次只有一个非空），`value_status` 区分已知与未知，`qualifier_key` 支持同字段多取值。",
    # ---- 硬件 ----
    "truth.hardware": "硬件实体的扩展：`category`（cpu / gpu / memory / storage / psu / platform）、**`record_kind` 区分 `product`（真实零件）与 `spec_profile`（规格档位）**、厂商、产品族、型号、变体名、市场段、地区。当前内存/电源/存储/平台全是 `spec_profile`，这就是「整机拼不出真实零件」的数据根源。",
    "truth.cpu_spec": "CPU 规格，1:1 扩展自 `hardware`：架构、插槽、核心/线程、基准与加速频率、P/E 核拆分、二三级缓存、基准与最大功耗、内存通道与上限、ECC、PCIe 代数与通道、**核显名称**、NPU 型号。40 行中核显只有 12 行有值。",
    "truth.gpu_spec": "GPU 规格：架构、显存类型/容量、位宽、带宽、ECC、PCIe、板卡功耗。桌面、移动、数据中心共用这一层。",
    "truth.laptop_gpu_spec": "移动 GPU 的补充：TGP 区间、动态加速功耗、加速频率区间。**当前 0 行**。",
    "truth.datacenter_gpu_spec": "数据中心 GPU 的补充：MIG、NVLink 及其带宽、是否有显示输出、最大功耗。**当前 0 行**。",
    "truth.memory_spec": "内存规格：速率、ECC、内存标准、板型、容量、是否寄存器、电压。**当前只有 4 行，且是规格档位不是具体型号**。",
    "truth.storage_spec": "存储规格：接口、类型、PCIe 代数、容量、顺序读写的档位值、典型用途。**3 行，同样是档位**。",
    "truth.psu_spec": "电源规格：额定功率、ATX 标准、能效认证、供电接口档案（JSONB）。**3 行档位**。",
    "truth.platform_spec": "平台 / 主板档位：插槽、芯片组、支持的内存标准（JSONB）、最大内存、PCIe 代数、是否支持超频。**4 行档位，没有真实主板型号**。",
    "truth.compatibility_edge": "**实体间的兼容关系边**：源实体 → 目标实体 + 关系键 + 状态 + 条件（JSONB）+ 有效期 + 证据或规则引用。数据库约束要求「需要证据的边」必须带来源文档或规则键版本。**当前 0 行**，所以兼容性只能写死在代码里。",
    # ---- 模型侧 ----
    "truth.ai_model": "基础模型：发布方、许可名称、家族、版本、模型类型、总参数量、激活参数量（MoE）、上下文长度、商业使用状态、官方仓库与模型卡 URL、架构备注。59 行。",
    "truth.model_capability": "模型能力矩阵：`capability_key` + `support_status`。当前 69 行，覆盖 text_input / vision_input / audio_input / image_generation / reasoning。",
    "truth.model_variant": "**可下载的模型变体，一个量化档一行**：权重格式、量化方法、每权重比特数、文件体积、artifact 版本、官方状态、仓库地址、产物清单、校验和。42 行。",
    "truth.runtime_support_snapshot": "某变体在某运行时（llama.cpp / ollama 等）的支持快照：状态、最低版本、实测版本、后端、检查时间、来源。**当前 0 行**，所以「这个量化档在哪个运行时能跑」无法回答。",
    "truth.workload": "工作负载实体：类型、家族、当前版本、引擎、官网、描述。13 行。",
    "truth.workload_profile": "工作负载的档位配置：`profile_key` + 版本标签 + settings（JSONB）+ 有效期。26 行。",
    "truth.workload_requirement": "档位下的组件角色要求：角色 + 操作符 + 数值/单位，或文本值/特性键。122 行，是「该场景需要什么」的结构化表达。",
    # ---- 证据与来源 ----
    "truth.source_document": "**来源文档**：`source_key` 唯一、标题、URL、类型、发布时间、**来源层级 tier**、归档 URL、访问时间、市场地区、语言、内容哈希、可用状态。239 行。证据的可靠性由这里定级。",
    "truth.evidence_claim": "**字段级证据声明**：来源 + 实体 + `field_key` + 归一化值 + 置信度 + 审核状态（pending / accepted / rejected / conflict）+ 单位 + 原文摘录 + 定位符 + provenance + 采集与审核时间。**TruthVerifier 只认 accepted。当前 985 行全是 accepted，没有一条 pending 或 rejected**，说明审核状态机实际上从未被真正使用。",
    # ---- 跑分与价格 ----
    "truth.benchmark_protocol": "跑分协议：协议键 + 版本 + 描述 + 工作负载类型 + 方法文档 + 设置/环境 schema。**0 行**。",
    "truth.metric_definition": "指标词典：指标键、类型、单位、是否越大越好、比较范围。**0 行**。",
    "truth.benchmark_run": "一次跑分执行：协议、工作负载、起止时间、环境、设置与设置哈希、样本数、聚合方法、原始结果 URL/哈希、状态、来源。**0 行**。",
    "truth.benchmark_subject": "跑分涉及的实体与角色（主/次、数量）。**0 行**。",
    "truth.benchmark_metric": "跑分指标取值：run + metric + 统计口径 → 数值/单位/样本数。**0 行**。",
    "truth.price_snapshot": "价格快照：实体 + 来源 + 观测时间 + 币种 + 金额 + 可用性 + 价格类型 + 地区 + 成色 + 卖家。80 行。",
    "truth.rule_definition": "规则定义：规则键 + 版本 + 类型 + 输入字段 + 输出字段 + 实现引用 + 来源文档。**0 行**。",
    # ---- 发布与导入 ----
    "truth.dataset_release": "数据集发布包：`release_key` 唯一 + manifest 哈希 + 创建与应用时间 + git commit + 状态。3 行，对应 data/v3/releases 下三个已接受发布。",
    "truth.import_batch": "导入批次：发布包 + 模式（validate / dry-run / apply）+ 起止时间 + 状态 + 报告 + 增删改与拒绝计数 + 错误报告。3 行。",
    # ---- 视图 ----
    "agent_catalog.cpu_catalog": "CPU 的型号级只读视图，给模型查的。含名称、厂商、架构、插槽、核心/线程、频率、功耗、发布日期、生命周期、可推荐。**注意：不暴露 `integrated_gpu`**，所以办公场景最需要的核显这一列模型根本查不到。",
    "agent_catalog.gpu_catalog": "桌面 / 移动 / 数据中心 GPU 视图：名称、类别、市场段、形态、厂商、架构、显存容量与类型、位宽、带宽、板卡功耗、发布日期、生命周期。",
    "agent_catalog.component_profile_catalog": "**所有硬件身份的通用视图**，`record_kind` 能把 `product` 与 `spec_profile` 分开。后端用它的 `category` 反查候选类别。",
    "agent_catalog.model_catalog": "基础模型视图：发布方、家族、类型、总/激活参数量、上下文长度、阶段、许可、商业使用状态、发布日期。",
    "agent_catalog.model_variant_catalog": "模型变体视图：量化方法、每权重比特、权重格式、文件体积、artifact 版本、官方状态、仓库地址、校验和。",
    "agent_catalog.runtime_support_latest": "每个变体/运行时组合的最新支持快照。**0 行**（源表为空）。",
    "agent_catalog.workload_catalog": "工作负载视图：类型、家族、当前版本、引擎、官网、描述。",
    "agent_catalog.workload_requirement_catalog": "归一化后的工作负载要求：工作负载 + 档位 + 角色 + 操作符 + 数值/单位 + 特性键。",
    "agent_catalog.benchmark_result": "追加型实测结果视图，一行一个指标取值。**0 行**。",
    "agent_catalog.price_latest": "每个实体/币种/地区/成色组合的最新价格。",
    "agent_catalog.fact_evidence": "**仅包含 accepted 的字段级证据**，带来源层级、标题、URL 与访问时间。这是叙事断言能引用的唯一证据面。",
    "agent_catalog.entity_attribute_catalog": "类型化长尾属性视图，满足「不在固定列里、但有值」的字段。",
    # ---- 应用侧 ----
    "public.conversation": "会话。前端终端的一个对话线程。",
    "public.conversation_message": "会话消息（用户与助手轮次）。",
    "public.conversation_context_snapshot": "会话上下文快照，用于跨轮上下文压缩。**0 行**。",
    "public.query_job": "查询任务。`/api/query/jobs` 的一次请求，含状态、模式（chat / fusion）与结果。",
    "public.query_event": "查询事件流，前端 SSE 进度与阶段展示的数据来源。",
    "public.agent_run": "单个 Agent 的执行记录：模型、轮次、耗时、状态，用于审计与计时。",
    "public.agent_message": "Agent 会话消息明细。",
    "public.tool_call": "工具调用记录（主要是模型自写的 SQL 与执行结果摘要），用于审计。",
    "public.user_constraint": "用户约束（预算、偏好等）。**0 行**。",
    "public.alembic_version": "数据库迁移版本号。",
}

# 需要额外说明的列
COLUMN_NOTES: dict[tuple[str, str], str] = {
    ("truth.hardware", "record_kind"): "product=真实零件；spec_profile=规格档位（非具体型号）",
    ("truth.field_definition", "storage_kind"): "column=读固定列；attribute=读 entity_attribute",
    ("truth.field_definition", "storage_path"): "形如 cpu_spec.integrated_gpu",
    ("truth.entity_attribute", "value_status"): "known / unknown，unknown 不算已知值",
    ("truth.evidence_claim", "review_status"): "只有 accepted 能通过 TruthVerifier",
    ("truth.source_document", "source_tier"): "来源可靠性分级",
    ("truth.catalog_entity", "recommendable"): "false 的实体不进入候选",
    ("truth.benchmark_metric", "statistic"): "聚合口径，如 reported / median",
}

TYPE_ZH = {
    "uuid": "UUID", "text": "文本", "varchar": "字符串", "int4": "整数", "int8": "长整数",
    "numeric": "数值", "bool": "布尔", "date": "日期", "timestamptz": "时间戳",
    "jsonb": "JSONB", "timestamp": "时间戳",
}

# 标题行已经写了行数，这里去掉用途句尾重复的行数声明
_DUP_TAIL = re.compile(r"(?:\*\*当前 \d+ 行\*\*|\d+ 行)[。.]?\s*$")


def _purpose(qualified: str) -> str:
    text = PURPOSE.get(qualified, "（用途待补）")
    return _DUP_TAIL.sub("", text).rstrip()


def main() -> None:
    with engine.connect() as conn:
        objects = [dict(r._mapping) for r in conn.execute(sql(OBJECTS))]
        columns = [dict(r._mapping) for r in conn.execute(sql(COLUMNS))]
        pks = {(r[0], r[1], r[2]) for r in conn.execute(sql(PK))}
        fks = {(r[0], r[1], r[2]): f"{r[3]}.{r[4]}" for r in conn.execute(sql(FK))}
        rows = {}
        for obj in objects:
            q = f'"{obj["table_schema"]}"."{obj["table_name"]}"'
            try:
                rows[(obj["table_schema"], obj["table_name"])] = conn.execute(sql(f"SELECT count(*) FROM {q}")).scalar_one()
            except Exception:  # noqa: BLE001
                rows[(obj["table_schema"], obj["table_name"])] = None

    grouped: dict[tuple[str, str], list[dict]] = {}
    for col in columns:
        grouped.setdefault((col["table_schema"], col["table_name"]), []).append(col)

    lines: list[str] = []
    add = lines.append
    today = dt.date.today().isoformat()
    add("# RigBuilder 数据库表与字段参考")
    add("")
    add(f"生成日期：{today}。生成方式：只读读取 PostgreSQL 的 information_schema 与各表行数，**未修改任何数据**。")
    add("")
    add("数据库：`rigbuilder`（本机 PostgreSQL 5432）。三个 schema 分工：")
    add("")
    add("| schema | 作用 | 对象数 |")
    add("|---|---|---|")
    for schema, desc in (
        ("truth", "**真值层**。实体、规格、证据、跑分、发布包的权威存储。写入必须经发布包流程。"),
        ("agent_catalog", "**只读查询面**。模型能看到的视图，`ALLOWED_TABLES` 白名单就是这一层。"),
        ("public", "**应用层**。会话、任务、事件、Agent 运行与工具调用记录。"),
    ):
        n = sum(1 for o in objects if o["table_schema"] == schema)
        add(f"| `{schema}` | {desc} | {n} |")
    add("")
    add("---")
    add("")
    for schema, title in (("truth", "一、truth：真值层"), ("agent_catalog", "二、agent_catalog：只读查询面"), ("public", "三、public：应用层")):
        add(f"## {title}")
        add("")
        subset = [o for o in objects if o["table_schema"] == schema]
        for obj in subset:
            key = (schema, obj["table_name"])
            qualified = f'{schema}.{obj["table_name"]}'
            kind = "视图" if "VIEW" in obj["table_type"] else "表"
            n = rows.get(key)
            add(f"### `{qualified}`")
            add("")
            add(f"{kind}，**当前 {n} 行**。" + _purpose(qualified))
            add("")
            cols = grouped.get(key, [])
            if cols:
                add("| 字段 | 类型 | 空值 | 约束 | 说明 |")
                add("|---|---|---|---|---|")
                for col in cols:
                    marks = []
                    if (schema, obj["table_name"], col["column_name"]) in pks:
                        marks.append("主键")
                    ref = fks.get((schema, obj["table_name"], col["column_name"]))
                    if ref:
                        marks.append(f"→ {ref}")
                    note = COLUMN_NOTES.get((qualified, col["column_name"]), "")
                    add(
                        f'| `{col["column_name"]}` | {TYPE_ZH.get(col["udt_name"], col["udt_name"])} '
                        f'| {"是" if col["is_nullable"] == "YES" else "否"} | {" ".join(marks)} | {note} |'
                    )
            add("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {OUT}（{len(lines)} 行，{len(objects)} 个对象）")


if __name__ == "__main__":
    main()
