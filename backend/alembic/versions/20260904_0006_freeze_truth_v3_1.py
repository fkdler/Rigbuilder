"""freeze the canonical Truth Bundle and database contract at V3.1

Revision ID: 0006_freeze_truth_v3_1
Revises: 0005_rebuild_truth_v3
Create Date: 2026-09-04

This revision is intentionally allowed only before the first dataset Release.
It removes the temporary V2/V3 dual columns instead of guessing how to merge
already-published facts.
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0006_freeze_truth_v3_1"
down_revision = "0005_rebuild_truth_v3"
branch_labels = None
depends_on = None


VIEWS: dict[str, str] = {
    "gpu_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,h.category,h.market_segment,h.form_factor,o.entity_key AS manufacturer_key,
        g.architecture,g.vram_gib,g.memory_type,g.memory_bus_width_bit,g.memory_bandwidth_gb_s,g.board_power_w,e.release_date,e.lifecycle_status,e.recommendable
        FROM truth.catalog_entity e JOIN truth.hardware h ON h.entity_id=e.id JOIN truth.gpu_spec g ON g.hardware_id=e.id
        LEFT JOIN truth.catalog_entity o ON o.id=h.manufacturer_id""",
    "cpu_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,o.entity_key AS manufacturer_key,
        c.architecture,c.socket,c.cores_total,c.threads,c.base_clock_mhz,c.boost_clock_mhz,c.base_power_w,c.max_power_w,e.release_date,e.lifecycle_status,e.recommendable
        FROM truth.catalog_entity e JOIN truth.hardware h ON h.entity_id=e.id JOIN truth.cpu_spec c ON c.hardware_id=e.id
        LEFT JOIN truth.catalog_entity o ON o.id=h.manufacturer_id""",
    "component_profile_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,h.category,h.record_kind,h.product_family,h.model_name,h.variant_name,h.market_segment,h.form_factor,h.region_code,
        e.release_date,e.lifecycle_status,e.recommendable FROM truth.catalog_entity e JOIN truth.hardware h ON h.entity_id=e.id""",
    "model_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,o.entity_key AS publisher_key,
        m.family,m.model_type,m.total_parameters,m.active_parameters,m.context_length_tokens,m.model_stage,m.license_name,m.commercial_use_status,e.release_date,e.lifecycle_status,e.recommendable
        FROM truth.catalog_entity e JOIN truth.ai_model m ON m.entity_id=e.id LEFT JOIN truth.catalog_entity o ON o.id=m.publisher_id""",
    "model_variant_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,m.entity_key AS model_key,
        v.quantization_method,v.bits_per_weight,v.weight_format,v.file_size_bytes,v.artifact_revision,v.official_status,v.repository_url,v.checksum,e.recommendable
        FROM truth.catalog_entity e JOIN truth.model_variant v ON v.entity_id=e.id JOIN truth.catalog_entity m ON m.id=v.model_id""",
    "runtime_support_latest": """SELECT DISTINCT ON (r.variant_id,r.runtime_key) e.entity_key AS variant_key,r.runtime_key,r.support_status,r.min_version,r.tested_version,r.backend,r.checked_at,r.notes
        FROM truth.runtime_support_snapshot r JOIN truth.catalog_entity e ON e.id=r.variant_id ORDER BY r.variant_id,r.runtime_key,r.checked_at DESC""",
    "workload_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,w.workload_type,w.family,w.current_version,w.engine,w.official_url,w.description,e.recommendable
        FROM truth.catalog_entity e JOIN truth.workload w ON w.entity_id=e.id""",
    "workload_requirement_catalog": """SELECT e.entity_key AS workload_key,p.profile_key,p.label,p.version_label,r.component_role,r.operator,
        re.entity_key AS reference_entity_key,r.numeric_value,r.unit_key,r.value_text,r.required_feature_key,r.raw_value
        FROM truth.workload_requirement r JOIN truth.workload_profile p ON p.id=r.profile_id JOIN truth.catalog_entity e ON e.id=p.workload_id
        LEFT JOIN truth.catalog_entity re ON re.id=r.reference_entity_id""",
    "benchmark_result": """SELECT b.id AS benchmark_run_id,b.run_key,p.protocol_key,p.version,w.entity_key AS workload_key,b.started_at,
        s.entity_id,es.entity_key,s.role,m.metric_key,bm.value_number,bm.value_integer,bm.value_boolean,bm.value_text,bm.statistic,bm.unit,b.status,b.settings,b.environment
        FROM truth.benchmark_run b JOIN truth.benchmark_protocol p ON p.id=b.protocol_id
        LEFT JOIN truth.catalog_entity w ON w.id=b.workload_id JOIN truth.benchmark_subject s ON s.run_id=b.id
        JOIN truth.catalog_entity es ON es.id=s.entity_id JOIN truth.benchmark_metric bm ON bm.run_id=b.id JOIN truth.metric_definition m ON m.id=bm.metric_id
        WHERE b.status <> 'invalid'""",
    "price_latest": """SELECT DISTINCT ON (p.entity_id,p.currency,COALESCE(p.market_region,''),COALESCE(p.condition,''),COALESCE(p.price_type,'')) e.entity_key,p.observed_at,p.price_type,p.currency,p.amount,p.market_region,p.condition,p.availability
        FROM truth.price_snapshot p JOIN truth.catalog_entity e ON e.id=p.entity_id ORDER BY p.entity_id,p.currency,COALESCE(p.market_region,''),COALESCE(p.condition,''),COALESCE(p.price_type,''),p.observed_at DESC""",
    "fact_evidence": """SELECT c.id AS evidence_id,e.entity_key,c.entity_id,c.field_key,c.normalized_value,c.unit_key,c.raw_excerpt,c.source_locator,c.provenance_key,
        c.confidence,s.source_key,s.source_tier,s.title,s.url,s.accessed_at FROM truth.evidence_claim c
        JOIN truth.catalog_entity e ON e.id=c.entity_id JOIN truth.source_document s ON s.id=c.source_id WHERE c.review_status='accepted'""",
    "entity_attribute_catalog": """SELECT e.entity_key,a.entity_id,f.field_key,f.label,f.value_type,COALESCE(a.unit,f.canonical_unit) AS unit,
        a.qualifier_key,a.value_status,a.value_text,a.value_number,a.value_integer,a.value_boolean,a.value_date,a.value_json,a.qualifier
        FROM truth.entity_attribute a JOIN truth.catalog_entity e ON e.id=a.entity_id JOIN truth.field_definition f ON f.id=a.field_id""",
}


def _require_empty_truth() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM truth.catalog_entity")).scalar_one()
    releases = bind.execute(sa.text("SELECT count(*) FROM truth.dataset_release")).scalar_one()
    if count or releases:
        raise RuntimeError(
            "Truth V3.1 freeze must run before the first data Release; "
            f"found catalog_entity={count}, dataset_release={releases}"
        )


def upgrade() -> None:
    _require_empty_truth()

    for name in VIEWS:
        op.execute(f"DROP VIEW agent_catalog.{name}")

    op.drop_column("organization", "website", schema="truth")
    op.drop_column("organization", "country_code", schema="truth")

    op.drop_column("field_definition", "entity_type", schema="truth")
    op.alter_column("field_definition", "applies_to_entity_type", nullable=False, schema="truth")

    op.drop_constraint(op.f("ck_entity_attribute_one_typed_value"), "entity_attribute", schema="truth", type_="check")
    op.drop_column("entity_attribute", "value_string", schema="truth")
    op.create_check_constraint("one_typed_value", "entity_attribute",
        "num_nonnulls(value_text,value_number,value_integer,value_boolean,value_date,value_json)<=1", schema="truth")

    op.drop_index("ix_hardware_type_manufacturer", table_name="hardware", schema="truth")
    op.drop_column("hardware", "hardware_type", schema="truth")
    op.drop_column("hardware", "family", schema="truth")
    op.alter_column("hardware", "category", nullable=False, schema="truth")
    op.create_index("ix_hardware_category_manufacturer", "hardware", ["category", "manufacturer_id"], schema="truth")

    for column in ("cores", "tdp_w", "pcie_version", "integrated_graphics", "extra"):
        op.drop_column("cpu_spec", column, schema="truth")
    for column in ("chip", "vram_bytes", "memory_bus_bits", "bandwidth_gbps", "compute_units", "tensor_cores", "tdp_w", "extra"):
        op.drop_column("gpu_spec", column, schema="truth")
    for column in ("mux_required", "extra"):
        op.drop_column("laptop_gpu_spec", column, schema="truth")
    for column in ("passive_cooling", "partitioning", "interconnect", "ecc", "extra"):
        op.drop_column("datacenter_gpu_spec", column, schema="truth")
    for column in ("capacity_bytes", "memory_type", "modules", "extra"):
        op.drop_column("memory_spec", column, schema="truth")
    for column in ("capacity_bytes", "sequential_read_mbps", "sequential_write_mbps", "extra"):
        op.drop_column("storage_spec", column, schema="truth")
    for column in ("efficiency_rating", "form_factor", "connectors", "extra"):
        op.drop_column("psu_spec", column, schema="truth")
    for column in ("memory_types", "pcie_slots", "extra"):
        op.drop_column("platform_spec", column, schema="truth")

    op.alter_column("compatibility_edge", "relationship", new_column_name="relation_key", schema="truth")

    for column in ("model_family", "parameter_count", "context_length"):
        op.drop_column("ai_model", column, schema="truth")
    op.alter_column("model_capability", "status", new_column_name="support_status", schema="truth")
    for column in ("quantization", "format", "file_size_display", "recommended_vram_bytes", "recommended_ram_bytes", "source_url"):
        op.drop_column("model_variant", column, schema="truth")

    op.drop_constraint("uq_runtime_support_snapshot_natural", "runtime_support_snapshot", schema="truth", type_="unique")
    for column in ("entity_id", "runtime", "version", "observed_at", "details"):
        op.drop_column("runtime_support_snapshot", column, schema="truth")
    op.drop_constraint(op.f("fk_runtime_support_snapshot_variant_id_model_variant"),
        "runtime_support_snapshot", schema="truth", type_="foreignkey")
    op.alter_column("runtime_support_snapshot", "variant_id", nullable=False, schema="truth")
    op.alter_column("runtime_support_snapshot", "runtime_key", nullable=False, schema="truth")
    op.alter_column("runtime_support_snapshot", "support_status", type_=sa.String(length=24),
        existing_type=sa.String(length=32), existing_nullable=False, schema="truth")
    op.alter_column("runtime_support_snapshot", "checked_at", nullable=False, schema="truth")
    op.create_foreign_key(op.f("fk_runtime_support_snapshot_variant_id_model_variant"),
        "runtime_support_snapshot", "model_variant", ["variant_id"], ["entity_id"],
        source_schema="truth", referent_schema="truth", ondelete="CASCADE")
    op.create_unique_constraint("uq_runtime_support_snapshot_natural", "runtime_support_snapshot",
        ["variant_id", "runtime_key", "checked_at"], schema="truth")

    op.drop_column("workload", "category", schema="truth")
    op.alter_column("workload", "workload_type", nullable=False, schema="truth")
    op.drop_column("workload_profile", "parameters", schema="truth")

    for column in ("publisher", "retrieved_at", "content_sha256"):
        op.drop_column("source_document", column, schema="truth")
    op.alter_column("source_document", "accessed_at", nullable=False, schema="truth")

    for column in ("raw_value", "quote", "locator"):
        op.drop_column("evidence_claim", column, schema="truth")
    op.create_check_constraint("review_status_valid", "evidence_claim",
        "review_status IN ('pending','accepted','rejected','conflict')", schema="truth")

    op.drop_column("benchmark_protocol", "configuration_schema", schema="truth")
    op.drop_constraint(op.f("ck_benchmark_metric_one_metric_value"), "benchmark_metric", schema="truth", type_="check")
    op.drop_column("benchmark_metric", "value_string", schema="truth")
    op.create_check_constraint("one_metric_value", "benchmark_metric",
        "num_nonnulls(value_number,value_integer,value_boolean,value_text)=1", schema="truth")

    op.drop_column("price_snapshot", "region", schema="truth")
    op.drop_column("price_snapshot", "url", schema="truth")
    op.drop_column("rule_definition", "expression", schema="truth")

    for name, select_sql in VIEWS.items():
        op.execute(f"CREATE VIEW agent_catalog.{name} AS {select_sql}")

    # Recreating a view drops its grants. Restore the standard Agent role when
    # it already exists; role creation itself remains an explicit admin step.
    op.execute("""
        DO $do$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly') THEN
                GRANT SELECT ON ALL TABLES IN SCHEMA agent_catalog TO agent_readonly;
            END IF;
        END $do$
    """)


def downgrade() -> None:
    raise RuntimeError("V3.1 is the frozen pre-data contract; restore the verified pre-0006 backup")
