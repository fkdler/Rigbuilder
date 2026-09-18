"""Complete V3 Truth ORM.

The public application/audit tables intentionally remain in their existing
modules.  Every class below lives in the isolated ``truth`` schema.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

S = "truth"
UUIDType = PGUUID(as_uuid=True)


class CatalogEntity(Base):
    __tablename__ = "catalog_entity"; __table_args__ = (Index("ix_catalog_entity_type_name", "entity_type", "canonical_name"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    release_date: Mapped[date | None] = mapped_column(Date)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    recommendable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Organization(Base):
    __tablename__ = "organization"; __table_args__ = ({"schema": S},)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), primary_key=True)
    organization_type: Mapped[str | None] = mapped_column(String(64))
    website_url: Mapped[str | None] = mapped_column(Text)
    country_or_region: Mapped[str | None] = mapped_column(String(80))


class EntityAlias(Base):
    __tablename__ = "entity_alias"; __table_args__ = (UniqueConstraint("entity_id", "normalized_alias", name="uq_entity_alias_entity_normalized"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    alias_type: Mapped[str] = mapped_column(String(40), nullable=False, default="alternate")
    language: Mapped[str | None] = mapped_column(String(16))
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)


class FieldDefinition(Base):
    __tablename__ = "field_definition"; __table_args__ = ({"schema": S},)
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    field_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    canonical_unit: Mapped[str | None] = mapped_column(String(64))
    allowed_units: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applies_to_entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    cardinality: Mapped[str] = mapped_column(String(16), nullable=False, default="one")
    comparison_method: Mapped[str] = mapped_column(String(32), nullable=False, default="exact")
    tolerance: Mapped[Decimal | None] = mapped_column(Numeric)
    storage_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="attribute")
    storage_path: Mapped[str | None] = mapped_column(String(255))
    filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claimable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EntityAttribute(Base):
    __tablename__ = "entity_attribute"; __table_args__ = (
        UniqueConstraint("entity_id", "field_id", "qualifier_key", name="uq_entity_attribute_fact"),
        CheckConstraint("num_nonnulls(value_text,value_number,value_integer,value_boolean,value_date,value_json)<=1", name="one_typed_value"),
        Index("ix_entity_attribute_field_number", "field_id", "value_number"),
        Index("ix_entity_attribute_field_integer", "field_id", "value_integer"),
        {"schema": S},
    )
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), nullable=False, index=True)
    field_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.field_definition.id"), nullable=False, index=True)
    qualifier_key: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    value_number: Mapped[Decimal | None] = mapped_column(Numeric)
    value_integer: Mapped[int | None] = mapped_column(BigInteger)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_date: Mapped[date | None] = mapped_column(Date)
    value_json: Mapped[dict | list | None] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(String(64))
    value_status: Mapped[str] = mapped_column(String(24), nullable=False, default="known")
    qualifier: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Hardware(Base):
    __tablename__ = "hardware"; __table_args__ = (Index("ix_hardware_category_manufacturer", "category", "manufacturer_id"), Index("ix_hardware_category_segment", "category", "market_segment"), {"schema": S})
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), primary_key=True)
    manufacturer_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.organization.entity_id"))
    form_factor: Mapped[str | None] = mapped_column(String(80))
    record_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="product")
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    product_family: Mapped[str | None] = mapped_column(String(160))
    model_name: Mapped[str | None] = mapped_column(String(255))
    variant_name: Mapped[str | None] = mapped_column(String(160))
    market_segment: Mapped[str | None] = mapped_column(String(48))
    region_code: Mapped[str] = mapped_column(String(16), nullable=False, default="GLOBAL")
    official_product_id: Mapped[str | None] = mapped_column(String(120))


class CpuSpec(Base):
    __tablename__ = "cpu_spec"; __table_args__ = (Index("ix_cpu_spec_socket_cores", "socket", "cores_total"),
        CheckConstraint("cores_total IS NULL OR cores_total >= 0", name="cores_nonnegative"),
        CheckConstraint("threads IS NULL OR threads >= 0", name="threads_nonnegative"), {"schema": S})
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.hardware.entity_id", ondelete="CASCADE"), primary_key=True)
    architecture: Mapped[str | None] = mapped_column(String(120)); socket: Mapped[str | None] = mapped_column(String(80))
    threads: Mapped[int | None] = mapped_column(Integer)
    base_clock_mhz: Mapped[Decimal | None] = mapped_column(Numeric); boost_clock_mhz: Mapped[Decimal | None] = mapped_column(Numeric)
    memory_channels: Mapped[int | None] = mapped_column(Integer)
    codename: Mapped[str | None] = mapped_column(String(120)); cores_total: Mapped[int | None] = mapped_column(Integer)
    performance_cores: Mapped[int | None] = mapped_column(Integer); efficiency_cores: Mapped[int | None] = mapped_column(Integer)
    l2_cache_mib: Mapped[Decimal | None] = mapped_column(Numeric); l3_cache_mib: Mapped[Decimal | None] = mapped_column(Numeric)
    base_power_w: Mapped[Decimal | None] = mapped_column(Numeric); max_power_w: Mapped[Decimal | None] = mapped_column(Numeric)
    max_memory_gib: Mapped[Decimal | None] = mapped_column(Numeric); ecc_support: Mapped[bool | None] = mapped_column(Boolean)
    pcie_generation: Mapped[Decimal | None] = mapped_column(Numeric); pcie_lanes: Mapped[int | None] = mapped_column(Integer)
    integrated_gpu: Mapped[str | None] = mapped_column(String(160)); npu_model: Mapped[str | None] = mapped_column(String(160))


class GpuSpec(Base):
    __tablename__ = "gpu_spec"; __table_args__ = (Index("ix_gpu_spec_vram_power", "vram_gib", "board_power_w"),
        CheckConstraint("vram_gib IS NULL OR vram_gib >= 0", name="vram_nonnegative"),
        CheckConstraint("board_power_w IS NULL OR board_power_w >= 0", name="board_power_nonnegative"), {"schema": S})
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.hardware.entity_id", ondelete="CASCADE"), primary_key=True)
    architecture: Mapped[str | None] = mapped_column(String(120)); memory_type: Mapped[str | None] = mapped_column(String(80))
    interface: Mapped[str | None] = mapped_column(String(80))
    vram_gib: Mapped[Decimal | None] = mapped_column(Numeric); memory_bus_width_bit: Mapped[int | None] = mapped_column(Integer)
    memory_bandwidth_gb_s: Mapped[Decimal | None] = mapped_column(Numeric); ecc_support: Mapped[bool | None] = mapped_column(Boolean)
    pcie_generation: Mapped[Decimal | None] = mapped_column(Numeric); pcie_lanes: Mapped[int | None] = mapped_column(Integer)
    board_power_w: Mapped[Decimal | None] = mapped_column(Numeric)


class LaptopGpuSpec(Base):
    __tablename__ = "laptop_gpu_spec"; __table_args__ = (
        CheckConstraint("tgp_min_w IS NULL OR tgp_max_w IS NULL OR tgp_min_w <= tgp_max_w", name="tgp_range"), {"schema": S})
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.gpu_spec.hardware_id", ondelete="CASCADE"), primary_key=True)
    tgp_min_w: Mapped[Decimal | None] = mapped_column(Numeric); tgp_max_w: Mapped[Decimal | None] = mapped_column(Numeric)
    dynamic_boost_w: Mapped[Decimal | None] = mapped_column(Numeric)
    boost_clock_min_mhz: Mapped[int | None] = mapped_column(Integer); boost_clock_max_mhz: Mapped[int | None] = mapped_column(Integer)


class DatacenterGpuSpec(Base):
    __tablename__ = "datacenter_gpu_spec"; __table_args__ = ({"schema": S},)
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.gpu_spec.hardware_id", ondelete="CASCADE"), primary_key=True)
    mig_support: Mapped[bool | None] = mapped_column(Boolean); nvlink_support: Mapped[bool | None] = mapped_column(Boolean)
    nvlink_bandwidth_gb_s: Mapped[Decimal | None] = mapped_column(Numeric); display_enabled: Mapped[bool | None] = mapped_column(Boolean)
    max_power_w: Mapped[Decimal | None] = mapped_column(Numeric)


class MemorySpec(Base):
    __tablename__ = "memory_spec"; __table_args__ = ({"schema": S},)
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.hardware.entity_id", ondelete="CASCADE"), primary_key=True)
    speed_mt_s: Mapped[int | None] = mapped_column(Integer)
    ecc: Mapped[bool | None] = mapped_column(Boolean)
    memory_standard: Mapped[str | None] = mapped_column(String(80)); form_factor: Mapped[str | None] = mapped_column(String(80))
    capacity_gib: Mapped[Decimal | None] = mapped_column(Numeric); registered: Mapped[bool | None] = mapped_column(Boolean)
    voltage_v: Mapped[Decimal | None] = mapped_column(Numeric)


class StorageSpec(Base):
    __tablename__ = "storage_spec"; __table_args__ = ({"schema": S},)
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.hardware.entity_id", ondelete="CASCADE"), primary_key=True)
    interface: Mapped[str | None] = mapped_column(String(80))
    storage_type: Mapped[str | None] = mapped_column(String(80)); pcie_generation: Mapped[Decimal | None] = mapped_column(Numeric)
    capacity_gib: Mapped[Decimal | None] = mapped_column(Numeric); sequential_read_mb_s_class: Mapped[Decimal | None] = mapped_column(Numeric)
    sequential_write_mb_s_class: Mapped[Decimal | None] = mapped_column(Numeric); typical_use_case: Mapped[str | None] = mapped_column(String(160))


class PsuSpec(Base):
    __tablename__ = "psu_spec"; __table_args__ = ({"schema": S},)
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.hardware.entity_id", ondelete="CASCADE"), primary_key=True)
    rated_power_w: Mapped[int | None] = mapped_column(Integer)
    atx_standard: Mapped[str | None] = mapped_column(String(80)); efficiency_certification: Mapped[str | None] = mapped_column(String(80))
    connector_profile: Mapped[list | dict] = mapped_column(JSONB, nullable=False, default=list)


class PlatformSpec(Base):
    __tablename__ = "platform_spec"; __table_args__ = ({"schema": S},)
    hardware_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.hardware.entity_id", ondelete="CASCADE"), primary_key=True)
    socket: Mapped[str | None] = mapped_column(String(80)); chipset: Mapped[str | None] = mapped_column(String(80))
    memory_standard: Mapped[list] = mapped_column(JSONB, nullable=False, default=list); max_memory_gib: Mapped[Decimal | None] = mapped_column(Numeric)
    pcie_generation: Mapped[Decimal | None] = mapped_column(Numeric); overclock_support: Mapped[bool | None] = mapped_column(Boolean)


class CompatibilityEdge(Base):
    __tablename__ = "compatibility_edge"; __table_args__ = (UniqueConstraint("source_entity_id", "target_entity_id", "relation_key", name="uq_compatibility_edge_natural"),
        CheckConstraint("valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to", name="valid_range"),
        CheckConstraint("NOT evidence_required OR source_document_id IS NOT NULL OR (rule_key IS NOT NULL AND rule_version IS NOT NULL)", name="has_support"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    source_entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), nullable=False)
    target_entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), nullable=False)
    relation_key: Mapped[str] = mapped_column(String(80), nullable=False); status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    valid_from: Mapped[date | None] = mapped_column(Date); valid_to: Mapped[date | None] = mapped_column(Date)
    evidence_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_document_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.source_document.id"))
    rule_key: Mapped[str | None] = mapped_column(String(255)); rule_version: Mapped[str | None] = mapped_column(String(80))


class AIModel(Base):
    __tablename__ = "ai_model"; __table_args__ = (Index("ix_ai_model_parameters_context", "total_parameters", "context_length_tokens"),
        CheckConstraint("total_parameters IS NULL OR total_parameters >= 0", name="total_parameters_nonnegative"),
        CheckConstraint("active_parameters IS NULL OR total_parameters IS NULL OR active_parameters <= total_parameters", name="active_lte_total"), {"schema": S})
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), primary_key=True)
    publisher_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.organization.entity_id"))
    license_name: Mapped[str | None] = mapped_column(String(160))
    family: Mapped[str | None] = mapped_column(String(160)); release_version: Mapped[str | None] = mapped_column(String(80))
    model_type: Mapped[str | None] = mapped_column(String(32)); total_parameters: Mapped[int | None] = mapped_column(BigInteger)
    active_parameters: Mapped[int | None] = mapped_column(BigInteger); model_stage: Mapped[str | None] = mapped_column(String(32))
    context_length_tokens: Mapped[int | None] = mapped_column(BigInteger); commercial_use_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    official_repo_url: Mapped[str | None] = mapped_column(Text); official_model_card_url: Mapped[str | None] = mapped_column(Text)
    architecture_notes: Mapped[str | None] = mapped_column(Text)


class ModelCapability(Base):
    __tablename__ = "model_capability"; __table_args__ = (UniqueConstraint("model_id", "capability_key", name="uq_model_capability_natural"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); model_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.ai_model.entity_id", ondelete="CASCADE"), nullable=False)
    capability_key: Mapped[str] = mapped_column(String(120), nullable=False); support_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    notes: Mapped[str | None] = mapped_column(Text)


class ModelVariant(Base):
    __tablename__ = "model_variant"; __table_args__ = ({"schema": S},)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.ai_model.entity_id", ondelete="CASCADE"), nullable=False, index=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    maintainer_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.organization.entity_id"))
    quantization_method: Mapped[str | None] = mapped_column(String(80)); bits_per_weight: Mapped[Decimal | None] = mapped_column(Numeric)
    weight_format: Mapped[str | None] = mapped_column(String(80)); artifact_revision: Mapped[str | None] = mapped_column(String(120))
    official_status: Mapped[str | None] = mapped_column(String(32)); repository_url: Mapped[str | None] = mapped_column(Text)
    artifact_manifest: Mapped[list | dict | None] = mapped_column(JSONB); checksum: Mapped[str | None] = mapped_column(String(160))


class RuntimeSupportSnapshot(Base):
    __tablename__ = "runtime_support_snapshot"; __table_args__ = (UniqueConstraint("variant_id", "runtime_key", "checked_at", name="uq_runtime_support_snapshot_natural"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    variant_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.model_variant.entity_id", ondelete="CASCADE"), nullable=False)
    runtime_key: Mapped[str] = mapped_column(String(120), nullable=False); support_status: Mapped[str] = mapped_column(String(24), nullable=False)
    min_version: Mapped[str | None] = mapped_column(String(80))
    tested_version: Mapped[str | None] = mapped_column(String(80)); backend: Mapped[str | None] = mapped_column(String(80))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); notes: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.source_document.id"))


class Workload(Base):
    __tablename__ = "workload"; __table_args__ = ({"schema": S},)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    publisher_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.organization.entity_id"))
    workload_type: Mapped[str] = mapped_column(String(48), nullable=False); family: Mapped[str | None] = mapped_column(String(160))
    current_version: Mapped[str | None] = mapped_column(String(80)); engine: Mapped[str | None] = mapped_column(String(120))
    official_url: Mapped[str | None] = mapped_column(Text)


class WorkloadProfile(Base):
    __tablename__ = "workload_profile"; __table_args__ = (UniqueConstraint("workload_id", "profile_key", name="uq_workload_profile_natural"),
        CheckConstraint("valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to", name="valid_range"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); workload_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.workload.entity_id", ondelete="CASCADE"), nullable=False)
    profile_key: Mapped[str] = mapped_column(String(160), nullable=False); label: Mapped[str] = mapped_column(String(255), nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(120)); settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    valid_from: Mapped[date | None] = mapped_column(Date); valid_to: Mapped[date | None] = mapped_column(Date)
    source_document_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.source_document.id"))


class WorkloadRequirement(Base):
    __tablename__ = "workload_requirement"; __table_args__ = ({"schema": S},)
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); profile_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.workload_profile.id", ondelete="CASCADE"), nullable=False)
    component_role: Mapped[str] = mapped_column(String(48), nullable=False); operator: Mapped[str] = mapped_column(String(20), nullable=False)
    reference_entity_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id"))
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric); unit_key: Mapped[str | None] = mapped_column(String(64))
    value_text: Mapped[str | None] = mapped_column(Text); required_feature_key: Mapped[str | None] = mapped_column(String(255))
    raw_value: Mapped[str | None] = mapped_column(Text)


class SourceDocument(Base):
    __tablename__ = "source_document"; __table_args__ = ({"schema": S},)
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); source_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False); url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False); published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publisher_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.organization.entity_id"))
    source_tier: Mapped[str | None] = mapped_column(String(8)); archive_url: Mapped[str | None] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); market_region: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(16)); content_hash: Mapped[str | None] = mapped_column(String(160))
    availability_status: Mapped[str] = mapped_column(String(24), nullable=False, default="accessible")


class EvidenceClaim(Base):
    __tablename__ = "evidence_claim"; __table_args__ = (Index("ix_evidence_claim_lookup", "entity_id", "field_key", "review_status"), Index("ix_evidence_claim_source", "source_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("review_status IN ('pending','accepted','rejected','conflict')", name="review_status_valid"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); source_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.source_document.id"), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), nullable=False)
    field_key: Mapped[str] = mapped_column(String(255), ForeignKey("truth.field_definition.field_key"), nullable=False)
    normalized_value: Mapped[dict | str | int | float | bool | None] = mapped_column(JSONB); confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=1)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending"); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    unit_key: Mapped[str | None] = mapped_column(String(64)); raw_excerpt: Mapped[str | None] = mapped_column(Text)
    source_locator: Mapped[str | None] = mapped_column(Text); provenance_key: Mapped[str | None] = mapped_column(String(48))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class BenchmarkProtocol(Base):
    __tablename__ = "benchmark_protocol"; __table_args__ = (UniqueConstraint("protocol_key", "version", name="uq_benchmark_protocol_key_version"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); protocol_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); version: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workload_type: Mapped[str | None] = mapped_column(String(48)); method_document_url: Mapped[str | None] = mapped_column(Text)
    settings_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict); environment_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MetricDefinition(Base):
    __tablename__ = "metric_definition"; __table_args__ = ({"schema": S},)
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); metric_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False); value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    canonical_unit: Mapped[str | None] = mapped_column(String(64)); higher_is_better: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    comparison_scope: Mapped[str | None] = mapped_column(String(80)); active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_run"; __table_args__ = (Index("ix_benchmark_run_protocol_status_date", "protocol_id", "status", "test_date"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); run_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    protocol_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.benchmark_protocol.id"), nullable=False); workload_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.workload.entity_id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    environment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict); release_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.dataset_release.id"))
    source_document_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.source_document.id"))
    run_origin: Mapped[str | None] = mapped_column(String(32)); test_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="success"); settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    settings_hash: Mapped[str | None] = mapped_column(String(64)); sample_count: Mapped[int | None] = mapped_column(Integer)
    aggregation_method: Mapped[str | None] = mapped_column(String(32)); raw_result_url: Mapped[str | None] = mapped_column(Text)
    raw_result_hash: Mapped[str | None] = mapped_column(String(160)); notes: Mapped[str | None] = mapped_column(Text)


class BenchmarkSubject(Base):
    __tablename__ = "benchmark_subject"; __table_args__ = (UniqueConstraint("run_id", "entity_id", "role", name="uq_benchmark_subject_natural"), Index("ix_benchmark_subject_entity_role", "entity_id", "role"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); run_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.benchmark_run.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id"), nullable=False); role: Mapped[str] = mapped_column(String(80), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False); quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BenchmarkMetric(Base):
    __tablename__ = "benchmark_metric"; __table_args__ = (UniqueConstraint("run_id", "metric_id", "statistic", name="uq_benchmark_metric_natural"), CheckConstraint("num_nonnulls(value_number,value_integer,value_boolean,value_text)=1", name="one_metric_value"), Index("ix_benchmark_metric_metric_number", "metric_id", "value_number"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); run_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.benchmark_run.id", ondelete="CASCADE"), nullable=False)
    metric_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.metric_definition.id"), nullable=False); value_number: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String(64))
    value_integer: Mapped[int | None] = mapped_column(BigInteger); value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_text: Mapped[str | None] = mapped_column(Text); statistic: Mapped[str] = mapped_column(String(24), nullable=False, default="reported")
    sample_count: Mapped[int | None] = mapped_column(Integer)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshot"; __table_args__ = (UniqueConstraint("price_key", name="uq_price_snapshot_key"), Index("ix_price_snapshot_entity_observed", "entity_id", "observed_at"), Index("ix_price_snapshot_market", "entity_id", "market_region", "condition", "observed_at"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); price_key: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.catalog_entity.id", ondelete="CASCADE"), nullable=False); source_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.source_document.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False); availability: Mapped[str | None] = mapped_column(String(80))
    price_type: Mapped[str | None] = mapped_column(String(32)); market_region: Mapped[str | None] = mapped_column(String(32))
    condition: Mapped[str | None] = mapped_column(String(24)); seller_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.organization.entity_id"))
    notes: Mapped[str | None] = mapped_column(Text)


class RuleDefinition(Base):
    __tablename__ = "rule_definition"; __table_args__ = (UniqueConstraint("rule_key", "version", name="uq_rule_definition_key_version"), {"schema": S})
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); rule_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); version: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_type: Mapped[str | None] = mapped_column(String(48)); input_field_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    output_field_key: Mapped[str | None] = mapped_column(String(255)); implementation_ref: Mapped[str | None] = mapped_column(String(255))
    source_document_id: Mapped[UUID | None] = mapped_column(UUIDType, ForeignKey("truth.source_document.id")); active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DatasetRelease(Base):
    __tablename__ = "dataset_release"; __table_args__ = ({"schema": S},)
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); release_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()); description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    git_commit: Mapped[str | None] = mapped_column(String(64)); released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted")


class ImportBatch(Base):
    __tablename__ = "import_batch"; __table_args__ = ({"schema": S},)
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True); release_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("truth.dataset_release.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False); started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); status: Mapped[str] = mapped_column(String(24), nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    executed_by: Mapped[str | None] = mapped_column(String(255)); added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); error_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


TRUTH_TABLES = tuple(table for table in Base.metadata.sorted_tables if table.schema == S)
