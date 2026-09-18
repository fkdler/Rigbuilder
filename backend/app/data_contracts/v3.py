"""RigBuilder Truth Bundle V3.2 contract.

This module deliberately has no dependency on Settings or the database.  It is
safe to import from collection machines and CI jobs without a ``.env`` file.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

SCHEMA_VERSION = "3.2"
ENTITY_NAMESPACE = "https://rigbuilder.local/entity/"


def stable_uuid(entity_key: str) -> UUID:
    """Return the only supported V3 identity algorithm."""
    return uuid5(NAMESPACE_URL, ENTITY_NAMESPACE + entity_key)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EntityIdentity(StrictModel):
    entity_key: str = Field(min_length=3, pattern=r"^[a-z0-9][a-z0-9._:/-]+$")
    canonical_name: str = Field(min_length=1)
    entity_type: Literal[
        "organization", "hardware", "ai_model", "model_variant", "workload"
    ]
    release_date: date | None = None
    lifecycle_status: Literal["announced", "upcoming", "active", "legacy", "discontinued", "unknown"] = "unknown"
    recommendable: bool = True

    @property
    def id(self) -> UUID:
        return stable_uuid(self.entity_key)


class EvidenceRef(StrictModel):
    source_key: str
    field_key: str
    normalized_value: Any | None = None
    raw_excerpt: str | None = None
    source_locator: str | None = None
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    unit_key: str | None = None
    provenance_key: str | None = None
    review_status: Literal["pending", "accepted", "rejected", "conflict"]
    collected_at: datetime | None = None
    reviewed_at: datetime | None = None
    notes: str | None = None


class AttributeValue(StrictModel):
    field_key: str
    qualifier_key: str = "default"
    value_number: Decimal | None = None
    value_boolean: bool | None = None
    value_date: date | None = None
    value_json: Any | None = None
    value_integer: int | None = None
    value_text: str | None = None
    unit: str | None = None
    value_status: Literal["known", "text_only", "unknown", "not_applicable"] = "known"
    qualifier: dict[str, Any] | None = None

    @model_validator(mode="after")
    def one_value(self) -> "AttributeValue":
        values = [self.value_text, self.value_number, self.value_integer, self.value_boolean, self.value_date, self.value_json]
        count = sum(value is not None for value in values)
        if self.value_status in {"known", "text_only"} and count != 1:
            raise ValueError("known/text_only attributes require exactly one typed value")
        if self.value_status in {"unknown", "not_applicable"} and count != 0:
            raise ValueError("unknown/not_applicable attributes cannot carry a value")
        return self


class SourceDocumentPayload(StrictModel):
    source_key: str
    title: str
    url: HttpUrl
    source_type: Literal[
        "official", "official_page", "vendor", "datasheet", "model_card", "paper",
        "review", "community", "measurement", "project_measurement",
        "independent_benchmark", "retailer", "derived_rule",
        # Aggregator spec databases (the review backlog cites hardquery.com).  They are
        # neither a vendor page nor a community post, and their tier is already recorded
        # as C by ``source_tier``, so the type must exist to describe them honestly.
        "third_party_database",
    ]
    published_at: datetime | None = None
    accessed_at: datetime
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    publisher_key: str | None = None
    source_tier: Literal["A", "B", "C", "D"] | None = None
    archive_url: HttpUrl | None = None
    market_region: str | None = None
    language: str | None = None
    availability_status: Literal["accessible", "archived", "unavailable"] = "accessible"


class FieldDefinitionPayload(StrictModel):
    field_key: str
    label: str
    applies_to_entity_type: str
    value_type: Literal["string", "number", "integer", "boolean", "date", "json"]
    canonical_unit: str | None = None
    description: str = ""
    allowed_units: list[str] = Field(default_factory=list)
    indexed: bool = False
    cardinality: Literal["one", "many"] = "one"
    comparison_method: Literal["exact", "tolerance", "set_contains", "range", "text_only"] = "exact"
    tolerance: Decimal | None = None
    storage_kind: Literal["column", "attribute"] = "attribute"
    storage_path: str | None = None
    filterable: bool = False
    claimable: bool = True
    active: bool = True


class MetricDefinitionPayload(StrictModel):
    metric_key: str
    label: str
    value_type: Literal["number", "integer", "string", "boolean"] = "number"
    canonical_unit: str | None = None
    higher_is_better: bool | None = None
    description: str = ""
    comparison_scope: str | None = None
    active: bool = True


class OrganizationPayload(StrictModel):
    identity: EntityIdentity
    website_url: HttpUrl | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    organization_type: str | None = None
    country_or_region: str | None = None

    @model_validator(mode="after")
    def identity_type(self) -> "OrganizationPayload":
        if self.identity.entity_type != "organization":
            raise ValueError("organization identity.entity_type must be organization")
        return self


class CpuSpecInput(StrictModel):
    architecture: str | None = None
    codename: str | None = None
    cores_total: int | None = Field(default=None, ge=0)
    threads: int | None = Field(default=None, ge=0)
    performance_cores: int | None = Field(default=None, ge=0)
    efficiency_cores: int | None = Field(default=None, ge=0)
    base_clock_mhz: int | None = Field(default=None, ge=0)
    boost_clock_mhz: int | None = Field(default=None, ge=0)
    l2_cache_mib: Decimal | None = Field(default=None, ge=0)
    l3_cache_mib: Decimal | None = Field(default=None, ge=0)
    base_power_w: Decimal | None = Field(default=None, ge=0)
    max_power_w: Decimal | None = Field(default=None, ge=0)
    socket: str | None = None
    memory_channels: int | None = Field(default=None, ge=0)
    max_memory_gib: Decimal | None = Field(default=None, ge=0)
    ecc_support: bool | None = None
    pcie_generation: Decimal | None = Field(default=None, ge=0)
    pcie_lanes: int | None = Field(default=None, ge=0)
    integrated_gpu: str | None = None
    npu_model: str | None = None


class GpuSpecInput(StrictModel):
    architecture: str | None = None
    vram_gib: Decimal | None = Field(default=None, ge=0)
    memory_type: str | None = None
    memory_bus_width_bit: int | None = Field(default=None, ge=0)
    memory_bandwidth_gb_s: Decimal | None = Field(default=None, ge=0)
    ecc_support: bool | None = None
    interface: str | None = None
    pcie_generation: Decimal | None = Field(default=None, ge=0)
    pcie_lanes: int | None = Field(default=None, ge=0)
    board_power_w: Decimal | None = Field(default=None, ge=0)


class LaptopGpuSpecInput(StrictModel):
    tgp_min_w: Decimal | None = Field(default=None, ge=0)
    tgp_max_w: Decimal | None = Field(default=None, ge=0)
    boost_clock_min_mhz: int | None = Field(default=None, ge=0)
    boost_clock_max_mhz: int | None = Field(default=None, ge=0)
    dynamic_boost_w: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_ranges(self) -> "LaptopGpuSpecInput":
        if self.tgp_min_w is not None and self.tgp_max_w is not None and self.tgp_min_w > self.tgp_max_w:
            raise ValueError("tgp_min_w cannot exceed tgp_max_w")
        if self.boost_clock_min_mhz is not None and self.boost_clock_max_mhz is not None and self.boost_clock_min_mhz > self.boost_clock_max_mhz:
            raise ValueError("boost_clock_min_mhz cannot exceed boost_clock_max_mhz")
        return self


class DatacenterGpuSpecInput(StrictModel):
    mig_support: bool | None = None
    nvlink_support: bool | None = None
    nvlink_bandwidth_gb_s: Decimal | None = Field(default=None, ge=0)
    display_enabled: bool | None = None
    max_power_w: Decimal | None = Field(default=None, ge=0)


class MemorySpecInput(StrictModel):
    memory_standard: str | None = None
    form_factor: str | None = None
    capacity_gib: Decimal | None = Field(default=None, ge=0)
    speed_mt_s: int | None = Field(default=None, ge=0)
    ecc: bool | None = None
    registered: bool | None = None
    voltage_v: Decimal | None = Field(default=None, ge=0)


class StorageSpecInput(StrictModel):
    storage_type: str | None = None
    interface: str | None = None
    pcie_generation: Decimal | None = Field(default=None, ge=0)
    capacity_gib: Decimal | None = Field(default=None, ge=0)
    sequential_read_mb_s_class: Decimal | None = Field(default=None, ge=0)
    sequential_write_mb_s_class: Decimal | None = Field(default=None, ge=0)
    typical_use_case: str | None = None


class ConnectorProfileItem(StrictModel):
    connector_type: str
    count: int = Field(ge=1)
    required: bool = True


class PsuSpecInput(StrictModel):
    atx_standard: str | None = None
    rated_power_w: int | None = Field(default=None, ge=0)
    efficiency_certification: str | None = None
    connector_profile: list[ConnectorProfileItem] = Field(default_factory=list)


class MemoryStandardItem(StrictModel):
    standard: str
    max_speed_mt_s: int | None = Field(default=None, ge=0)


class PlatformSpecInput(StrictModel):
    chipset: str | None = None
    socket: str | None = None
    memory_standard: list[MemoryStandardItem] = Field(default_factory=list)
    max_memory_gib: Decimal | None = Field(default=None, ge=0)
    pcie_generation: Decimal | None = Field(default=None, ge=0)
    overclock_support: bool | None = None


class HardwarePayload(StrictModel):
    identity: EntityIdentity
    category: Literal["cpu", "gpu", "memory", "storage", "psu", "platform"]
    manufacturer_key: str | None = None
    form_factor: str | None = None
    record_kind: Literal["product", "spec_profile"] = "product"
    product_family: str | None = None
    model_name: str | None = None
    variant_name: str | None = None
    market_segment: str | None = None
    region_code: str = "GLOBAL"
    official_product_id: str | None = None
    cpu_spec: CpuSpecInput | None = None
    gpu_spec: GpuSpecInput | None = None
    laptop_gpu_spec: LaptopGpuSpecInput | None = None
    datacenter_gpu_spec: DatacenterGpuSpecInput | None = None
    memory_spec: MemorySpecInput | None = None
    storage_spec: StorageSpecInput | None = None
    psu_spec: PsuSpecInput | None = None
    platform_spec: PlatformSpecInput | None = None
    attributes: list[AttributeValue] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity_type(self) -> "HardwarePayload":
        if self.identity.entity_type != "hardware":
            raise ValueError("hardware identity.entity_type must be hardware")
        expected = {
            "cpu": self.cpu_spec,
            "gpu": self.gpu_spec,
            "memory": self.memory_spec,
            "storage": self.storage_spec,
            "psu": self.psu_spec,
            "platform": self.platform_spec,
        }
        if expected[self.category] is None:
            raise ValueError(f"hardware category {self.category} requires its matching spec object")
        populated = {
            "cpu": self.cpu_spec,
            "gpu": self.gpu_spec,
            "memory": self.memory_spec,
            "storage": self.storage_spec,
            "psu": self.psu_spec,
            "platform": self.platform_spec,
        }
        unrelated = [name for name, spec in populated.items() if name != self.category and spec is not None]
        if unrelated:
            raise ValueError(f"hardware category {self.category} cannot include specs for {unrelated}")
        if self.category != "gpu" and (self.laptop_gpu_spec is not None or self.datacenter_gpu_spec is not None):
            raise ValueError("laptop/datacenter GPU specs require category gpu")
        if self.laptop_gpu_spec is not None and self.datacenter_gpu_spec is not None:
            raise ValueError("one GPU entity cannot be both laptop and datacenter")
        return self


class CapabilityInput(StrictModel):
    capability_key: str
    status: Literal["supported", "unsupported", "partial", "unknown"] = "unknown"
    notes: str | None = None


class VariantInput(StrictModel):
    identity: EntityIdentity
    file_size_bytes: int | None = Field(default=None, ge=0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    maintainer_key: str | None = None
    quantization_method: str | None = None
    bits_per_weight: Decimal | None = None
    weight_format: str | None = None
    artifact_revision: str | None = None
    official_status: Literal["official", "community", "converted_by_project"] | None = None
    repository_url: HttpUrl | None = None
    artifact_manifest: list["ArtifactFileInput"] | None = None
    checksum: str | None = None

    @model_validator(mode="after")
    def identity_type(self) -> "VariantInput":
        if self.identity.entity_type != "model_variant":
            raise ValueError("variant identity.entity_type must be model_variant")
        return self


class ArtifactFileInput(StrictModel):
    role: Literal["weights", "config", "tokenizer", "projector", "adapter", "metadata", "other"]
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    checksum: str | None = None
    required: bool = True


class ModelPayload(StrictModel):
    identity: EntityIdentity
    publisher_key: str | None = None
    license_name: str | None = None
    capabilities: list[CapabilityInput] = Field(default_factory=list)
    variants: list[VariantInput] = Field(default_factory=list)
    attributes: list[AttributeValue] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    family: str | None = None
    release_version: str | None = None
    # ``diffusion`` is the architecture of the image/video generators already in the
    # review backlog (FLUX.1, Lumina, Stable Diffusion), which are not dense or MoE
    # transformers.  Refusing the value would drop 20 real models.
    model_type: Literal["dense", "moe", "diffusion"] | None = None
    total_parameters: int | None = Field(default=None, ge=0)
    active_parameters: int | None = Field(default=None, ge=0)
    # ``model_stage`` carries the training stage for text models and the primary task
    # for everything else.  The backlog uses the task vocabulary for multimodal,
    # speech, embedding and vision models, so both vocabularies are accepted rather
    # than silently discarding 54 reviewed models.
    model_stage: Literal[
        "base", "instruct", "chat", "reasoning",
        "vision_language", "text_to_image", "image_editing", "depth_estimation",
        "text_to_video", "text_image_to_video", "text_to_speech", "speech_recognition",
        "object_detection", "segmentation", "image_to_3d", "embedding", "reranker",
    ] | None = None
    context_length_tokens: int | None = Field(default=None, ge=0)
    commercial_use_status: Literal["allowed", "restricted", "unknown"] = "unknown"
    official_repo_url: HttpUrl | None = None
    official_model_card_url: HttpUrl | None = None
    architecture_notes: str | None = None

    @model_validator(mode="after")
    def identity_type(self) -> "ModelPayload":
        if self.identity.entity_type != "ai_model":
            raise ValueError("model identity.entity_type must be ai_model")
        return self


class WorkloadProfileInput(StrictModel):
    profile_key: str
    label: str
    version_label: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    valid_from: date | None = None
    valid_to: date | None = None
    source_key: str | None = None


class WorkloadRequirementInput(StrictModel):
    profile_key: str
    component_role: Literal["cpu", "gpu", "ram", "storage", "os", "feature"]
    operator: Literal["eq", "gte", "one_of", "text_only"]
    reference_entity_key: str | None = None
    numeric_value: Decimal | None = None
    unit_key: str | None = None
    value_text: str | None = None
    required_feature_key: str | None = None
    raw_value: str | None = None

    @model_validator(mode="after")
    def requirement_value(self) -> "WorkloadRequirementInput":
        values = (
            self.reference_entity_key,
            self.numeric_value,
            self.value_text,
            self.required_feature_key,
        )
        if self.operator == "text_only" and self.value_text is None:
            raise ValueError("text_only workload requirements require value_text")
        if all(value is None for value in values):
            raise ValueError("workload requirements require a normalized reference, number, feature, or text value")
        return self


class WorkloadPayload(StrictModel):
    identity: EntityIdentity
    workload_type: Literal["game", "engineering", "productivity", "benchmark_suite"]
    description: str = ""
    profiles: list[WorkloadProfileInput] = Field(default_factory=list)
    requirements: list[WorkloadRequirementInput] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    publisher_key: str | None = None
    family: str | None = None
    current_version: str | None = None
    engine: str | None = None
    official_url: HttpUrl | None = None

    @model_validator(mode="after")
    def identity_type(self) -> "WorkloadPayload":
        if self.identity.entity_type != "workload":
            raise ValueError("workload identity.entity_type must be workload")
        return self


class BenchmarkProtocolPayload(StrictModel):
    protocol_key: str
    name: str
    version: str
    description: str = ""
    workload_type: str | None = None
    method_document_url: HttpUrl | None = None
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    environment_schema: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class BenchmarkSubjectInput(StrictModel):
    entity_key: str
    role: str
    is_primary: bool = False
    quantity: int = Field(default=1, ge=1)


class BenchmarkMetricInput(StrictModel):
    metric_key: str
    value_number: Decimal | None = None
    unit: str | None = None
    value_integer: int | None = None
    value_boolean: bool | None = None
    value_text: str | None = None
    statistic: Literal["reported", "median", "mean", "p95", "min", "max"] = "reported"
    sample_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def metric_value(self) -> "BenchmarkMetricInput":
        if sum(v is not None for v in (self.value_number, self.value_integer, self.value_boolean, self.value_text)) != 1:
            raise ValueError("exactly one metric value must be supplied")
        return self


class BenchmarkRunPayload(StrictModel):
    run_key: str
    protocol_key: str
    protocol_version: str
    workload_key: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    subjects: list[BenchmarkSubjectInput]
    metrics: list[BenchmarkMetricInput]
    evidence: list[EvidenceRef] = Field(default_factory=list)
    source_key: str | None = None
    run_origin: Literal["project", "independent", "official"] | None = None
    test_date: date | None = None
    status: Literal["success", "partial", "oom", "failed", "invalid"] = "success"
    settings: dict[str, Any] = Field(default_factory=dict)
    settings_hash: str | None = None
    sample_count: int | None = Field(default=None, ge=1)
    aggregation_method: Literal["median", "mean", "p95", "reported"] | None = None
    raw_result_url: HttpUrl | None = None
    raw_result_hash: str | None = None
    notes: str | None = None


class RuntimeSupportPayload(StrictModel):
    snapshot_key: str
    support_status: Literal["supported", "unsupported", "partial", "unknown"]
    variant_key: str
    runtime_key: str
    min_version: str | None = None
    tested_version: str | None = None
    backend: str | None = None
    notes: str | None = None
    source_key: str | None = None
    checked_at: datetime


class PriceSnapshotPayload(StrictModel):
    price_key: str
    entity_key: str
    source_key: str
    observed_at: datetime
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal = Field(ge=0)
    availability: str | None = None
    price_type: Literal["msrp", "launch", "current_new", "current_used"] | None = None
    market_region: str | None = None
    condition: Literal["new", "used", "refurbished"] | None = None
    seller_key: str | None = None
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must be uppercase ISO 4217")
        return value


class RuleDefinitionPayload(StrictModel):
    rule_key: str
    name: str
    version: str
    description: str = ""
    rule_type: str | None = None
    input_field_keys: list[str] = Field(default_factory=list)
    output_field_key: str | None = None
    implementation_ref: str | None = None
    source_key: str | None = None
    active: bool = True


class EntityAliasPayload(StrictModel):
    """A name another source uses for an entity we already own.

    Aliases are look-up metadata, not truth claims: they say "this string refers
    to that entity", never "this entity has this property".  They therefore carry
    no Evidence and are not available as claim backing.  They exist because every
    external source spells the same part differently -- PassMark writes
    ``Intel Core i5-12400 @ 2.50GHz``, the catalogue writes ``Core i5-12400`` --
    and a lookup that has to guess with ``LIKE`` is a lookup that silently misses.
    """

    entity_key: str
    alias: str
    alias_type: Literal[
        "alternate", "short", "vendor_full", "source_name", "repository", "runtime_tag", "localized"
    ] = "alternate"
    language: str | None = None
    normalized_alias: str
    source_key: str | None = None


Payload = Annotated[
    SourceDocumentPayload
    | FieldDefinitionPayload
    | MetricDefinitionPayload
    | OrganizationPayload
    | HardwarePayload
    | ModelPayload
    | WorkloadPayload
    | BenchmarkProtocolPayload
    | BenchmarkRunPayload
    | RuntimeSupportPayload
    | PriceSnapshotPayload
    | RuleDefinitionPayload
    | EntityAliasPayload,
    Field(union_mode="left_to_right"),
]


RECORD_TYPES = Literal[
    "source_document", "field_definition", "metric_definition", "organization",
    "hardware", "model", "workload", "benchmark_protocol", "benchmark_run",
    "price_snapshot", "runtime_support", "rule_definition", "entity_alias",
]


class Bundle(StrictModel):
    schema_version: Literal["3.2"] = SCHEMA_VERSION
    record_type: RECORD_TYPES
    status: Literal["accepted", "pending", "rejected"] = "pending"
    payload: Payload
    review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def matching_payload(self) -> "Bundle":
        expected = {
            "source_document": SourceDocumentPayload, "field_definition": FieldDefinitionPayload,
            "metric_definition": MetricDefinitionPayload, "organization": OrganizationPayload,
            "hardware": HardwarePayload, "model": ModelPayload, "workload": WorkloadPayload,
            "benchmark_protocol": BenchmarkProtocolPayload, "benchmark_run": BenchmarkRunPayload,
            "price_snapshot": PriceSnapshotPayload, "runtime_support": RuntimeSupportPayload,
            "rule_definition": RuleDefinitionPayload, "entity_alias": EntityAliasPayload,
        }[self.record_type]
        if not isinstance(self.payload, expected):
            raise ValueError(f"payload does not match record_type {self.record_type}")
        refs = list(getattr(self.payload, "evidence", []))
        if isinstance(self.payload, ModelPayload):
            refs.extend(ref for variant in self.payload.variants for ref in variant.evidence)
        if self.status == "accepted":
            invalid = sorted({ref.review_status for ref in refs if ref.review_status not in {"accepted", "conflict"}})
            if invalid:
                raise ValueError(f"accepted bundles cannot contain unreviewed Evidence statuses: {invalid}")
        return self


class ManifestFile(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_type: RECORD_TYPES
    status: Literal["accepted"] = "accepted"

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or "drafts" in {p.lower() for p in path.parts}:
            raise ValueError("manifest paths must be relative accepted release paths")
        return value.replace("\\", "/")


class ReleaseManifest(StrictModel):
    schema_version: Literal["3.2"] = SCHEMA_VERSION
    release_key: str
    created_at: datetime
    description: str = ""
    git_commit: str | None = None
    files: list[ManifestFile]

    @model_validator(mode="after")
    def unique_paths(self) -> "ReleaseManifest":
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest file paths must be unique")
        return self


def verify_manifest_file(manifest_path: Path, item: ManifestFile) -> Path:
    release_root = manifest_path.resolve().parent
    candidate = (release_root / item.path).resolve()
    if release_root not in candidate.parents:
        raise ValueError(f"manifest path escapes release directory: {item.path}")
    digest = sha256(candidate.read_bytes()).hexdigest()
    if digest != item.sha256:
        raise ValueError(f"SHA-256 mismatch for {item.path}: expected {item.sha256}, got {digest}")
    return candidate
