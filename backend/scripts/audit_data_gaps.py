"""只读空值与缺口审计：扫描 truth schema 的 NULL 分布、死列与实体级覆盖。

运行：PYTHONPATH=<backend> python scripts/audit_data_gaps.py
仅执行 SELECT，不修改任何数据。
"""
from __future__ import annotations

from sqlalchemy import text as sql

from app.db.session import engine

TABLES = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'truth' AND table_type = 'BASE TABLE'
ORDER BY table_name
"""

COLUMNS = """
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'truth' ORDER BY table_name, ordinal_position
"""

# 决策关键字段：这些字段缺失会直接影响推荐正确性
CRITICAL = {
    ("cpu_spec", "integrated_gpu"): "办公场景能否不用独显",
    ("cpu_spec", "socket"): "主板平台与内存代际推导",
    ("cpu_spec", "base_power_w"): "整机功耗与散热",
    ("cpu_spec", "max_power_w"): "峰值功耗",
    ("cpu_spec", "architecture"): "代际定位",
    ("cpu_spec", "pcie_generation"): "扩展能力",
    ("cpu_spec", "max_memory_gib"): "内存上限",
    ("gpu_spec", "vram_gib"): "能否装下模型",
    ("gpu_spec", "board_power_w"): "电源选型",
    ("gpu_spec", "memory_bandwidth_gb_s"): "显存带宽（推理速度关键）",
    ("gpu_spec", "architecture"): "代际定位",
    ("gpu_spec", "memory_type"): "显存类型",
    ("ai_model", "total_parameters"): "部署需求估算",
    ("ai_model", "context_length_tokens"): "可用上下文",
    ("ai_model", "license_name"): "商用可行性",
    ("ai_model", "commercial_use_status"): "商用状态",
    ("ai_model", "official_repo_url"): "权重来源",
    ("model_variant", "file_size_bytes"): "权重体积",
    ("model_variant", "quantization_method"): "量化档位",
    ("model_variant", "weight_format"): "运行时兼容性",
    ("model_variant", "repository_url"): "可下载性",
    ("hardware", "manufacturer_id"): "厂商归属",
    ("catalog_entity", "release_date"): "发布时间",
}

with engine.connect() as conn:
    tables = [r[0] for r in conn.execute(sql(TABLES))]
    columns = [(r[0], r[1]) for r in conn.execute(sql(COLUMNS))]

    counts = {}
    for name in tables:
        counts[name] = conn.execute(sql(f'SELECT count(*) FROM truth."{name}"')).scalar_one()

    print("=" * 78)
    print("一、空表（0 行）")
    print("=" * 78)
    empty = [t for t in tables if counts[t] == 0]
    for t in empty:
        print(f"  truth.{t}")
    print(f"  共 {len(empty)} / {len(tables)} 张表为空")

    print()
    print("=" * 78)
    print("二、整列全空（死列）")
    print("=" * 78)
    dead = []
    for name in tables:
        if counts[name] == 0:
            continue
        cols = [c for t, c in columns if t == name]
        if not cols:
            continue
        parts = [f'count("{c}")' for c in cols]
        row = conn.execute(sql(f'SELECT {", ".join(parts)} FROM truth."{name}"')).one()
        for col, nonnull in zip(cols, row):
            if nonnull == 0:
                dead.append((name, col))
    for name, col in dead:
        print(f"  truth.{name}.{col}")
    print(f"  共 {len(dead)} 个列在非空表里全为 NULL")

    print()
    print("=" * 78)
    print("三、决策关键字段的空缺")
    print("=" * 78)
    for (table, col), why in sorted(CRITICAL.items()):
        if counts.get(table, 0) == 0:
            print(f"  {table}.{col:<26} 整表为空    （{why}）")
            continue
        total = counts[table]
        nonnull = conn.execute(sql(f'SELECT count("{col}") FROM truth."{table}"')).scalar_one()
        missing = total - nonnull
        flag = "!!" if missing else "  "
        print(f"  {flag} {table}.{col:<26} 缺失 {missing:>3}/{total:<3}  （{why}）")

    print()
    print("=" * 78)
    print("四、实体级覆盖")
    print("=" * 78)
    queries = [
        ("硬件没有任何规格行",
         """SELECT count(*) FROM truth.hardware h
            WHERE NOT EXISTS (SELECT 1 FROM truth.cpu_spec s WHERE s.hardware_id = h.entity_id)
              AND NOT EXISTS (SELECT 1 FROM truth.gpu_spec g WHERE g.hardware_id = h.entity_id)
              AND NOT EXISTS (SELECT 1 FROM truth.memory_spec m WHERE m.hardware_id = h.entity_id)
              AND NOT EXISTS (SELECT 1 FROM truth.storage_spec st WHERE st.hardware_id = h.entity_id)
              AND NOT EXISTS (SELECT 1 FROM truth.psu_spec p WHERE p.hardware_id = h.entity_id)
              AND NOT EXISTS (SELECT 1 FROM truth.platform_spec pl WHERE pl.hardware_id = h.entity_id)"""),
        ("可推荐实体没有任何 accepted 证据",
         """SELECT count(*) FROM truth.catalog_entity e
            WHERE e.recommendable AND NOT EXISTS (
              SELECT 1 FROM truth.evidence_claim c
              WHERE c.entity_id = e.id AND c.review_status = 'accepted')"""),
        ("ai_model 没有任何 model_variant",
         """SELECT count(*) FROM truth.ai_model m
            WHERE NOT EXISTS (SELECT 1 FROM truth.model_variant v WHERE v.model_id = m.entity_id)"""),
        ("ai_model 没有任何 capability",
         """SELECT count(*) FROM truth.ai_model m
            WHERE NOT EXISTS (SELECT 1 FROM truth.model_capability c WHERE c.model_id = m.entity_id)"""),
        ("model_variant 没有 file_size / 量化 / 格式 任一",
         """SELECT count(*) FROM truth.model_variant
            WHERE file_size_bytes IS NULL OR quantization_method IS NULL OR weight_format IS NULL"""),
        ("CPU 没有核显 accepted 证据",
         """SELECT count(*) FROM truth.cpu_spec s
            WHERE NOT EXISTS (SELECT 1 FROM truth.evidence_claim c
              WHERE c.entity_id = s.hardware_id AND c.field_key = 'cpu.integrated_gpu'
                AND c.review_status = 'accepted')"""),
        ("CPU 没有任何功耗证据",
         """SELECT count(*) FROM truth.cpu_spec s
            WHERE NOT EXISTS (SELECT 1 FROM truth.evidence_claim c
              WHERE c.entity_id = s.hardware_id
                AND c.field_key IN ('cpu.base_power_w','cpu.max_power_w')
                AND c.review_status = 'accepted')"""),
        ("GPU 没有显存带宽证据",
         """SELECT count(*) FROM truth.gpu_spec s
            WHERE NOT EXISTS (SELECT 1 FROM truth.evidence_claim c
              WHERE c.entity_id = s.hardware_id AND c.field_key = 'gpu.memory_bandwidth_gb_s'
                AND c.review_status = 'accepted')"""),
        ("catalog_entity 生命周期仍是 unknown",
         "SELECT count(*) FROM truth.catalog_entity WHERE lifecycle_status = 'unknown'"),
        ("catalog_entity 没有 release_date",
         "SELECT count(*) FROM truth.catalog_entity WHERE release_date IS NULL"),
        ("source_document 没有来源层级 tier",
         "SELECT count(*) FROM truth.source_document WHERE source_tier IS NULL"),
        ("source_document 没有内容哈希",
         "SELECT count(*) FROM truth.source_document WHERE content_hash IS NULL"),
        ("evidence_claim 没有 source_locator",
         "SELECT count(*) FROM truth.evidence_claim WHERE source_locator IS NULL"),
        ("evidence_claim 没有 raw_excerpt",
         "SELECT count(*) FROM truth.evidence_claim WHERE raw_excerpt IS NULL"),
        ("evidence_claim 没有 collected_at",
         "SELECT count(*) FROM truth.evidence_claim WHERE collected_at IS NULL"),
        ("evidence_claim 置信度 < 1",
         "SELECT count(*) FROM truth.evidence_claim WHERE confidence < 1"),
        ("organization 没有官网",
         "SELECT count(*) FROM truth.organization WHERE website_url IS NULL"),
        ("price_snapshot 没有可用性字段",
         "SELECT count(*) FROM truth.price_snapshot WHERE availability IS NULL"),
    ]
    for label, q in queries:
        try:
            value = conn.execute(sql(q)).scalar_one()
        except Exception as exc:  # noqa: BLE001
            value = f"ERR {type(exc).__name__}"
        print(f"  {label:<44} {value}")

    print()
    print("=" * 78)
    print("五、evidence_claim 按 field_key 的分布（accepted）")
    print("=" * 78)
    rows = conn.execute(sql(
        """SELECT field_key, count(*) AS n, count(DISTINCT entity_id) AS entities
           FROM truth.evidence_claim WHERE review_status = 'accepted'
           GROUP BY field_key ORDER BY n DESC"""
    )).all()
    for field_key, n, entities in rows:
        print(f"  {field_key:<34} {n:>4} 条 / {entities:>3} 个实体")
    print(f"  共 {len(rows)} 个不同字段有 accepted 证据")
