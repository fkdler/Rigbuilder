"""Stable Agent View Registry for the V3 read surface."""

from __future__ import annotations

from typing import Any


def _columns(**items: str) -> list[dict[str, Any]]:
    return [{"name": name, "type": kind, "nullable": True, "primary_key": False,
             "foreign_key": False, "description": ""} for name, kind in items.items()]


VIEW_REGISTRY: dict[str, dict[str, Any]] = {
    "gpu_catalog": {"description": "Desktop, laptop and datacenter GPUs.", "columns": _columns(entity_id="UUID", entity_key="TEXT", name="TEXT", category="TEXT", market_segment="TEXT", form_factor="TEXT", manufacturer_key="TEXT", architecture="TEXT", vram_gib="NUMERIC", memory_type="TEXT", memory_bus_width_bit="INTEGER", memory_bandwidth_gb_s="NUMERIC", board_power_w="NUMERIC", release_date="DATE", lifecycle_status="TEXT", recommendable="BOOLEAN")},
    # integrated_gpu is last because migration 0008 appended it to the view's select
    # list; PostgreSQL maps CREATE OR REPLACE VIEW columns by position, so it could
    # not be inserted next to architecture without renaming the columns after it.
    "cpu_catalog": {"description": "CPU catalog and normalized CPU specifications.", "columns": _columns(entity_id="UUID", entity_key="TEXT", name="TEXT", manufacturer_key="TEXT", architecture="TEXT", socket="TEXT", cores_total="INTEGER", threads="INTEGER", base_clock_mhz="NUMERIC", boost_clock_mhz="NUMERIC", base_power_w="NUMERIC", max_power_w="NUMERIC", release_date="DATE", lifecycle_status="TEXT", recommendable="BOOLEAN", integrated_gpu="TEXT")},
    "component_profile_catalog": {"description": "All hardware component identities.", "columns": _columns(entity_id="UUID", entity_key="TEXT", name="TEXT", category="TEXT", record_kind="TEXT", product_family="TEXT", model_name="TEXT", variant_name="TEXT", market_segment="TEXT", form_factor="TEXT", region_code="TEXT", release_date="DATE", lifecycle_status="TEXT", recommendable="BOOLEAN")},
    "model_catalog": {"description": "Base AI model catalog.", "columns": _columns(entity_id="UUID", entity_key="TEXT", name="TEXT", publisher_key="TEXT", family="TEXT", model_type="TEXT", total_parameters="BIGINT", active_parameters="BIGINT", context_length_tokens="BIGINT", model_stage="TEXT", license_name="TEXT", commercial_use_status="TEXT", release_date="DATE", lifecycle_status="TEXT", recommendable="BOOLEAN")},
    "model_variant_catalog": {"description": "Downloadable model variants.", "columns": _columns(entity_id="UUID", entity_key="TEXT", name="TEXT", model_key="TEXT", quantization_method="TEXT", bits_per_weight="NUMERIC", weight_format="TEXT", file_size_bytes="BIGINT", artifact_revision="TEXT", official_status="TEXT", repository_url="TEXT", checksum="TEXT", recommendable="BOOLEAN")},
    "runtime_support_latest": {"description": "Latest third-party runtime-support declaration per model variant/runtime. Rows are not project execution tests: tested_version=NULL means no runtime version was verified here. Treat support_status as source-reported compatibility and keep throughput, stability and actual hardware fit unknown unless separately measured.", "columns": _columns(variant_key="TEXT", runtime_key="TEXT", support_status="TEXT", min_version="TEXT", tested_version="TEXT", backend="TEXT", checked_at="TIMESTAMPTZ", notes="TEXT")},
    "workload_catalog": {"description": "Workloads used for requirements and benchmarks.", "columns": _columns(entity_id="UUID", entity_key="TEXT", name="TEXT", workload_type="TEXT", family="TEXT", current_version="TEXT", engine="TEXT", official_url="TEXT", description="TEXT", recommendable="BOOLEAN")},
    "workload_requirement_catalog": {"description": "Normalized workload profile requirements.", "columns": _columns(workload_key="TEXT", profile_key="TEXT", label="TEXT", version_label="TEXT", component_role="TEXT", operator="TEXT", reference_entity_key="TEXT", numeric_value="NUMERIC", unit_key="TEXT", value_text="TEXT", required_feature_key="TEXT", raw_value="TEXT")},
    "benchmark_result": {"description": "Append-only benchmark records from project or third-party sources. Current hardware rows are published aggregate PassMark figures, not measurements run by this project. Inspect protocol_key, settings and environment before comparing values; query performance_metric when run_origin and source URL are required. A generic score does not prove performance in the user's workload.", "columns": _columns(benchmark_run_id="UUID", run_key="TEXT", protocol_key="TEXT", version="TEXT", workload_key="TEXT", started_at="TIMESTAMPTZ", entity_id="UUID", entity_key="TEXT", role="TEXT", metric_key="TEXT", value_number="NUMERIC", value_integer="BIGINT", value_boolean="BOOLEAN", value_text="TEXT", statistic="TEXT", unit="TEXT", status="TEXT", settings="JSONB", environment="JSONB")},
    "price_latest": {"description": "Latest price per entity, currency, region and condition.", "columns": _columns(entity_key="TEXT", observed_at="TIMESTAMPTZ", price_type="TEXT", currency="TEXT", amount="NUMERIC", market_region="TEXT", condition="TEXT", availability="TEXT")},
    "fact_evidence": {"description": "Accepted field-level evidence only.", "columns": _columns(evidence_id="UUID", entity_key="TEXT", entity_id="UUID", field_key="TEXT", normalized_value="JSONB", unit_key="TEXT", raw_excerpt="TEXT", source_locator="TEXT", provenance_key="TEXT", confidence="NUMERIC", source_key="TEXT", source_tier="TEXT", title="TEXT", url="TEXT", accessed_at="TIMESTAMPTZ")},
    "entity_attribute_catalog": {"description": "Qualified long-tail attributes. Multiple rows for one entity/field are intentional: always select qualifier_key and qualifier, require value_status='known', and match the requested quantization or deployment mode. qualifier_key='API' is cloud access, not local deployment. Deployment-memory values are third-party estimates unless their source says measured.", "columns": _columns(entity_key="TEXT", entity_id="UUID", field_key="TEXT", label="TEXT", value_type="TEXT", unit="TEXT", qualifier_key="TEXT", value_status="TEXT", value_text="TEXT", value_number="NUMERIC", value_integer="BIGINT", value_boolean="BOOLEAN", value_date="DATE", value_json="JSONB", qualifier="JSONB")},
    # The four views below are derived or policy surfaces, not measured facts, and
    # none of their columns is bound in EVIDENCE_FIELD_BY_VIEW_COLUMN.  A claim
    # about them is a `derived` claim with rule references; it can never be a
    # `fact` claim, because no Evidence backs an aggregator's index or a product
    # policy band.
    "performance_metric": {"description": "Published per-part benchmark figures, one row per part and metric. Current hardware values are PassMark aggregate scores/ranks with source URL and optional page hash; they are not measurements performed by this project and do not establish workload-specific FPS, latency or productivity.", "columns": _columns(entity_key="TEXT", name="TEXT", category="TEXT", form_factor="TEXT", metric_key="TEXT", label="TEXT", unit="TEXT", higher_is_better="BOOLEAN", value="NUMERIC", protocol_key="TEXT", protocol_version="TEXT", run_key="TEXT", test_date="DATE", run_origin="TEXT", source_key="TEXT", source_url="TEXT", environment="JSONB")},
    "performance_ranking": {"description": "Relative-performance metadata. score and market/class percentiles come from the published aggregate benchmark surface. index_100 and tier_label are performance-anchor-v1 product policy: hand-selected CPU/GPU anchors with linear interpolation, not publisher percentiles, not project measurements and not proof of a specific workload. catalogue_percentile is only this catalogue's distribution. The CPU/GPU index ratio is usable solely under an explicit balance_rule_catalog policy.", "columns": _columns(entity_key="TEXT", name="TEXT", category="TEXT", form_factor="TEXT", score="NUMERIC", alt_score="NUMERIC", market_rank="NUMERIC", market_population="NUMERIC", market_percentile="NUMERIC", class_rank="NUMERIC", class_population="NUMERIC", class_percentile="NUMERIC", index_100="NUMERIC", catalogue_percentile="NUMERIC", population_size="NUMERIC", cohort_size="BIGINT", tier_label="TEXT")},
    "balance_rule_catalog": {"description": "Pairing policy as data: for a use case, the acceptable band of gpu index / cpu index plus the minimum acceptable index per side. Product policy, versioned and auditable -- not a measurement and not a benchmark result.", "columns": _columns(rule_key="TEXT", use_case="TEXT", version="TEXT", cpu_metric_key="TEXT", gpu_metric_key="TEXT", ratio_min="NUMERIC", ratio_max="NUMERIC", cpu_index_min="NUMERIC", gpu_index_min="NUMERIC", gpu_requirement="TEXT", priority="TEXT", rationale="TEXT", active="BOOLEAN")},
    "entity_alias_catalog": {"description": "Alternative names other sources use for an entity, mapped onto that entity's key. Look-up metadata, not a truth claim: an alias says which entity a string refers to, never what that entity is like.", "columns": _columns(entity_key="TEXT", name="TEXT", entity_type="TEXT", alias="TEXT", alias_type="TEXT", language="TEXT", normalized_alias="TEXT")},
}

ALLOWED_TABLES = frozenset(VIEW_REGISTRY)
TABLE_SEMANTICS = {name: definition["description"] for name, definition in VIEW_REGISTRY.items()}
COLUMN_SEMANTICS = {name: {column["name"]: column["description"] for column in definition["columns"]} for name, definition in VIEW_REGISTRY.items()}
# Which evidence field_key a result column carries, keyed by (view, column).
#
# A bare column name cannot express this.  cpu_catalog and gpu_catalog both expose
# `architecture`, so the old flat {column: field_key} map certified a CPU's
# architecture with `gpu.architecture`; the reverse evidence lookup then searched
# the GPU field on a CPU entity, found nothing, and the column silently lost its
# binding.  That is exactly the case a CPU candidate needs, so the key has to
# carry the view.
#
# Coverage was also incomplete: every pair below is verified to exist in
# truth.field_definition, to have accepted rows in agent_catalog.fact_evidence, AND to
# be a column the view actually exposes.  The last condition is load-bearing and easy to
# get wrong: the Release carries evidence for gpu.pcie_generation, gpu.pcie_lanes and
# gpu.ecc_support, but gpu_catalog exposes none of those columns, so mapping them would
# add a field_key the Agent can never query.
EVIDENCE_FIELD_BY_VIEW_COLUMN: dict[tuple[str, str], str] = {
    ("gpu_catalog", "name"): "entity.canonical_name",
    ("gpu_catalog", "architecture"): "gpu.architecture",
    ("gpu_catalog", "vram_gib"): "gpu.vram_gib",
    ("gpu_catalog", "memory_type"): "gpu.memory_type",
    ("gpu_catalog", "board_power_w"): "gpu.board_power_w",
    ("gpu_catalog", "memory_bandwidth_gb_s"): "gpu.memory_bandwidth_gb_s",
    ("gpu_catalog", "memory_bus_width_bit"): "gpu.memory_bus_width_bit",
    ("cpu_catalog", "architecture"): "cpu.architecture",
    ("cpu_catalog", "name"): "entity.canonical_name",
    ("cpu_catalog", "socket"): "cpu.socket",
    ("cpu_catalog", "cores_total"): "cpu.cores_total",
    ("cpu_catalog", "threads"): "cpu.threads",
    ("cpu_catalog", "base_clock_mhz"): "cpu.base_clock_mhz",
    ("cpu_catalog", "boost_clock_mhz"): "cpu.boost_clock_mhz",
    ("cpu_catalog", "base_power_w"): "cpu.base_power_w",
    ("cpu_catalog", "integrated_gpu"): "cpu.integrated_gpu",
    ("model_catalog", "total_parameters"): "model.total_parameters",
    ("model_catalog", "name"): "entity.canonical_name",
    ("model_catalog", "active_parameters"): "model.active_parameters",
    ("model_catalog", "context_length_tokens"): "model.context_length_tokens",
    ("model_catalog", "license_name"): "model.license_name",
    ("model_variant_catalog", "file_size_bytes"): "variant.file_size_bytes",
    ("model_variant_catalog", "name"): "entity.canonical_name",
}


def evidence_fields_for_columns(
    columns: list[str], views: Any = None,
) -> dict[str, tuple[str, ...]]:
    """Map each result column to the evidence field_keys it may carry.

    Scoped to the statement's own views whenever they are known.  A statement that
    joins two views sharing a column (``cpu_catalog`` and ``gpu_catalog`` both expose
    ``architecture``) legitimately yields two candidates for that column, and both
    must be kept: evidence rows are keyed by ``(entity_id, field_key)`` and an entity
    only ever has evidence for its own category, so the row's entity selects the right
    one.  Narrowing the pair here would break the CPU case rather than protect it.
    """
    scoped = {str(view) for view in views} if views else None
    wanted = set(columns)
    mapping: dict[str, list[str]] = {}
    for (view, column), field_key in EVIDENCE_FIELD_BY_VIEW_COLUMN.items():
        if column not in wanted:
            continue
        if scoped is not None and view not in scoped:
            continue
        bucket = mapping.setdefault(column, [])
        if field_key not in bucket:
            bucket.append(field_key)
    return {column: tuple(keys) for column, keys in mapping.items()}


__all__ = [
    "VIEW_REGISTRY", "ALLOWED_TABLES", "TABLE_SEMANTICS", "COLUMN_SEMANTICS",
    "EVIDENCE_FIELD_BY_VIEW_COLUMN", "evidence_fields_for_columns",
]
