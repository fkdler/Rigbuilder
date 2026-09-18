"""replace legacy Truth tables with the complete V3 catalog

Revision ID: 0005_rebuild_truth_v3
Revises: 0004_create_agent_logs
Create Date: 2026-09-02

This is intentionally a one-way data cutover.  Recovery is a pg_dump restore,
not downgrade. PostgreSQL runs the revision in one transaction.
"""

from alembic import context, op
import sqlalchemy as sa

from app.db.v3_schema_0005_snapshot import DDL

revision = "0005_rebuild_truth_v3"
down_revision = "20260831_0004"
branch_labels = None
depends_on = None

LEGACY_TABLES_DROP_ORDER = ("benchmark", "evidence", "model_variant", "ai_model", "hardware")

VIEWS: dict[str, str] = {
    "gpu_catalog": """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,h.hardware_type,o.entity_key AS manufacturer_key,
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
        FROM truth.runtime_support_snapshot r JOIN truth.catalog_entity e ON e.id=r.variant_id WHERE r.variant_id IS NOT NULL ORDER BY r.variant_id,r.runtime_key,r.checked_at DESC""",
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
    "price_latest": """SELECT DISTINCT ON (p.entity_id,p.currency,COALESCE(p.market_region,''),COALESCE(p.condition,''),COALESCE(p.price_type,'')) e.entity_key,p.observed_at,p.price_type,p.currency,p.amount,p.market_region,p.condition,p.availability,p.url
        FROM truth.price_snapshot p JOIN truth.catalog_entity e ON e.id=p.entity_id ORDER BY p.entity_id,p.currency,COALESCE(p.market_region,''),COALESCE(p.condition,''),COALESCE(p.price_type,''),p.observed_at DESC""",
    "fact_evidence": """SELECT c.id AS evidence_id,e.entity_key,c.entity_id,c.field_key,c.normalized_value,c.unit_key,c.raw_excerpt,c.source_locator,c.provenance_key,
        c.confidence,s.source_key,s.source_tier,s.title,s.url,s.publisher,s.accessed_at FROM truth.evidence_claim c
        JOIN truth.catalog_entity e ON e.id=c.entity_id JOIN truth.source_document s ON s.id=c.source_id WHERE c.review_status='accepted'""",
    "entity_attribute_catalog": """SELECT e.entity_key,a.entity_id,f.field_key,f.label,f.value_type,COALESCE(a.unit,f.canonical_unit) AS unit,
        a.qualifier_key,a.value_status,a.value_text,a.value_number,a.value_integer,a.value_boolean,a.value_date,a.value_json,a.qualifier
        FROM truth.entity_attribute a JOIN truth.catalog_entity e ON e.id=a.entity_id JOIN truth.field_definition f ON f.id=a.field_id""",
}


def _require_confirmation() -> None:
    value = context.get_x_argument(as_dictionary=True).get("allow_truth_rebuild", "").lower()
    if value != "true":
        raise RuntimeError("Truth V3 rebuild refused: pass -x allow_truth_rebuild=true")


def upgrade() -> None:
    _require_confirmation()
    bind = op.get_bind()
    if not context.is_offline_mode():
        conflicts = bind.execute(sa.text("""
            SELECT nspname FROM pg_namespace WHERE nspname IN ('truth','agent_catalog')
        """)).scalars().all()
        if conflicts:
            raise RuntimeError(f"V3 schema conflict: already present: {', '.join(sorted(conflicts))}")

    for table_name in LEGACY_TABLES_DROP_ORDER:
        op.drop_table(table_name)

    op.execute("CREATE SCHEMA truth")
    op.execute("CREATE SCHEMA agent_catalog")
    # This frozen DDL snapshot is deliberately independent of the live ORM so
    # future model changes cannot rewrite historical migration behavior.
    for statement in DDL:
        op.execute(sa.text(statement))
    for name, select_sql in VIEWS.items():
        op.execute(f"CREATE VIEW agent_catalog.{name} AS {select_sql}")


def downgrade() -> None:
    raise RuntimeError("0005 is a one-way data cutover; restore the verified pg_dump backup")
