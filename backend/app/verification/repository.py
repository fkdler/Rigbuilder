"""Read-only repository used by deterministic truth verification.

The verifier never executes model-authored SQL. Fixed-column storage paths are
resolved through the ORM whitelist below and Attribute/Benchmark/Evidence data
is queried through typed SQLAlchemy expressions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.truth_v3 import (
    AIModel,
    BenchmarkMetric,
    BenchmarkRun,
    BenchmarkSubject,
    CatalogEntity,
    CpuSpec,
    DatacenterGpuSpec,
    DatasetRelease,
    EntityAttribute,
    EvidenceClaim,
    FieldDefinition,
    GpuSpec,
    Hardware,
    LaptopGpuSpec,
    MemorySpec,
    MetricDefinition,
    ModelVariant,
    Organization,
    PlatformSpec,
    PsuSpec,
    RuleDefinition,
    RuntimeSupportSnapshot,
    StorageSpec,
    Workload,
)


@dataclass(frozen=True)
class CandidateRecord:
    id: UUID
    entity_type: str
    canonical_name: str
    recommendable: bool


@dataclass(frozen=True)
class FieldRecord:
    id: UUID
    field_key: str
    applies_to_entity_type: str
    value_type: str
    canonical_unit: str | None
    allowed_units: tuple[str, ...]
    comparison_method: str
    tolerance: Any | None
    storage_kind: str
    storage_path: str | None
    cardinality: str
    filterable: bool
    claimable: bool
    active: bool


@dataclass(frozen=True)
class StoredValue:
    found: bool
    value: Any | None = None
    unit: str | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    id: UUID
    entity_id: UUID
    field_key: str
    normalized_value: Any | None
    unit: str | None
    review_status: str


@dataclass(frozen=True)
class MetricRecord:
    id: UUID
    metric_key: str
    value_type: str
    canonical_unit: str | None
    active: bool


@dataclass(frozen=True)
class BenchmarkValue:
    run_id: UUID
    run_exists: bool
    run_status: str | None = None
    subject_matches: bool = False
    metric_found: bool = False
    value: Any | None = None
    unit: str | None = None
    statistic: str | None = None


@dataclass(frozen=True)
class RuleRecord:
    rule_key: str
    version: str
    implementation_ref: str | None
    input_field_keys: tuple[str, ...]
    active: bool


@dataclass(frozen=True)
class LatestPrice:
    found: bool
    amount: Any | None = None
    currency: str | None = None
    snapshot_id: UUID | None = None
    source_id: UUID | None = None
    source_key: str | None = None
    source_url: str | None = None
    observed_at: Any | None = None
    market_region: str | None = None
    condition: str | None = None
    price_type: str | None = None


@dataclass(frozen=True)
class CompatibilityRecord:
    found: bool
    status: str | None = None


@dataclass(frozen=True)
class RuntimeSupportRecord:
    found: bool
    status: str | None = None


class TruthRepository(Protocol):
    def latest_release_key(self) -> str | None: ...
    def candidate(self, entity_id: UUID) -> CandidateRecord | None: ...
    def field(self, field_key: str) -> FieldRecord | None: ...
    def field_value(self, entity_id: UUID, field: FieldRecord, qualifier_key: str) -> StoredValue: ...
    def evidence(self, evidence_ids: set[UUID]) -> list[EvidenceRecord]: ...
    def metric(self, metric_key: str) -> MetricRecord | None: ...
    def benchmark_value(self, run_id: UUID, entity_id: UUID, metric: MetricRecord, statistic: str) -> BenchmarkValue: ...
    def rule(self, rule_key: str, version: str) -> RuleRecord | None: ...
    def latest_price(self, entity_id: UUID, currency: str, region: str | None, condition: str | None, price_type: str | None) -> LatestPrice: ...
    def compatibility(self, entity_id: UUID, other_entity_id: UUID, relation_key: str, direction: str) -> CompatibilityRecord: ...
    def runtime_support(self, entity_id: UUID, runtime_key: str) -> RuntimeSupportRecord: ...


_COLUMN_MODELS: dict[str, tuple[type, str]] = {
    "catalog_entity": (CatalogEntity, "id"),
    "organization": (Organization, "entity_id"),
    "hardware": (Hardware, "entity_id"),
    "cpu_spec": (CpuSpec, "hardware_id"),
    "gpu_spec": (GpuSpec, "hardware_id"),
    "laptop_gpu_spec": (LaptopGpuSpec, "hardware_id"),
    "datacenter_gpu_spec": (DatacenterGpuSpec, "hardware_id"),
    "memory_spec": (MemorySpec, "hardware_id"),
    "storage_spec": (StorageSpec, "hardware_id"),
    "psu_spec": (PsuSpec, "hardware_id"),
    "platform_spec": (PlatformSpec, "hardware_id"),
    "ai_model": (AIModel, "entity_id"),
    "model_variant": (ModelVariant, "entity_id"),
    "workload": (Workload, "entity_id"),
}


def _typed_value(row: Any) -> Any:
    for name in ("value_number", "value_integer", "value_boolean", "value_text", "value_date", "value_json"):
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


# Vendor and marketing words that a model may add or omit around the canonical name.
# Measured: agent-c submitted "NVIDIA GeForce RTX 3060 Ti" while the Release stores
# "GeForce RTX 3060 Ti", so a literal match would never find it.
_NAME_NOISE = frozenset({
    "nvidia", "amd", "intel", "geforce", "radeon", "arc", "the", "gpu", "graphics",
    "card", "series", "edition",
})


def normalise_entity_name(name: str) -> str:
    """Reduce a product name to comparable tokens for a unique-match lookup."""
    tokens = re.findall(r"[a-z0-9]+", (name or "").casefold())
    kept = [token for token in tokens if token not in _NAME_NOISE]
    return " ".join(kept)


class SqlAlchemyTruthRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest_release_key(self) -> str | None:
        return self.session.scalar(
            select(DatasetRelease.release_key)
            .where(DatasetRelease.status == "accepted")
            .order_by(DatasetRelease.applied_at.desc(), DatasetRelease.release_key.desc())
            .limit(1)
        )

    def candidate(self, entity_id: UUID) -> CandidateRecord | None:
        row = self.session.get(CatalogEntity, entity_id)
        if row is None:
            return None
        return CandidateRecord(row.id, row.entity_type, row.canonical_name, row.recommendable)

    def recommendable_names(self) -> dict[str, list[CandidateRecord]]:
        """Every recommendable entity grouped by its normalised canonical name.

        Measured: all 95 recommendable entities have a distinct canonical name, so a
        candidate the Agent names exactly identifies one entity. That makes it provable
        to repair a candidate whose id is wrong but whose name is right, instead of
        letting Truth verification reject the whole candidate as candidate_not_found.
        """
        rows = self.session.scalars(
            select(CatalogEntity).where(CatalogEntity.recommendable.is_(True))
        ).all()
        grouped: dict[str, list[CandidateRecord]] = {}
        for row in rows:
            key = normalise_entity_name(row.canonical_name or "")
            if not key:
                continue
            grouped.setdefault(key, []).append(
                CandidateRecord(row.id, row.entity_type, row.canonical_name, row.recommendable)
            )
        return grouped

    def candidate_by_key(self, entity_key: str) -> CandidateRecord | None:
        row = self.session.scalar(
            select(CatalogEntity).where(CatalogEntity.entity_key == entity_key)
        )
        if row is None:
            return None
        return CandidateRecord(row.id, row.entity_type, row.canonical_name, row.recommendable)

    def field(self, field_key: str) -> FieldRecord | None:
        row = self.session.scalar(select(FieldDefinition).where(FieldDefinition.field_key == field_key))
        if row is None:
            return None
        return FieldRecord(
            row.id, row.field_key, row.applies_to_entity_type, row.value_type, row.canonical_unit,
            tuple(row.allowed_units or []), row.comparison_method, row.tolerance,
            row.storage_kind, row.storage_path, row.cardinality, row.filterable, row.claimable, row.active,
        )

    def field_value(self, entity_id: UUID, field: FieldRecord, qualifier_key: str) -> StoredValue:
        if field.storage_kind == "attribute":
            row = self.session.scalar(
                select(EntityAttribute).where(
                    EntityAttribute.entity_id == entity_id,
                    EntityAttribute.field_id == field.id,
                    EntityAttribute.qualifier_key == qualifier_key,
                )
            )
            if row is None or row.value_status != "known":
                return StoredValue(found=False)
            return StoredValue(found=True, value=_typed_value(row), unit=row.unit or field.canonical_unit)

        if field.storage_kind != "column" or not field.storage_path:
            return StoredValue(found=False)
        parts = field.storage_path.split(".")
        if len(parts) != 2 or parts[0] not in _COLUMN_MODELS:
            raise ValueError("unsupported_storage_path")
        model, entity_column_name = _COLUMN_MODELS[parts[0]]
        value_column = getattr(model, parts[1], None)
        entity_column = getattr(model, entity_column_name)
        if value_column is None or not hasattr(value_column, "property"):
            raise ValueError("unsupported_storage_path")
        value = self.session.scalar(select(value_column).where(entity_column == entity_id))
        return StoredValue(found=value is not None, value=value, unit=field.canonical_unit)

    def evidence(self, evidence_ids: set[UUID]) -> list[EvidenceRecord]:
        if not evidence_ids:
            return []
        rows = self.session.scalars(select(EvidenceClaim).where(EvidenceClaim.id.in_(evidence_ids))).all()
        return [
            EvidenceRecord(row.id, row.entity_id, row.field_key, row.normalized_value, row.unit_key, row.review_status)
            for row in rows
        ]

    def metric(self, metric_key: str) -> MetricRecord | None:
        row = self.session.scalar(select(MetricDefinition).where(MetricDefinition.metric_key == metric_key))
        if row is None:
            return None
        return MetricRecord(row.id, row.metric_key, row.value_type, row.canonical_unit, row.active)

    def benchmark_value(self, run_id: UUID, entity_id: UUID, metric: MetricRecord, statistic: str) -> BenchmarkValue:
        run = self.session.get(BenchmarkRun, run_id)
        if run is None:
            return BenchmarkValue(run_id=run_id, run_exists=False)
        subject_matches = self.session.scalar(
            select(BenchmarkSubject.id).where(BenchmarkSubject.run_id == run_id, BenchmarkSubject.entity_id == entity_id).limit(1)
        ) is not None
        measurement = self.session.scalar(
            select(BenchmarkMetric).where(
                BenchmarkMetric.run_id == run_id,
                BenchmarkMetric.metric_id == metric.id,
                BenchmarkMetric.statistic == statistic,
            )
        )
        if measurement is None:
            return BenchmarkValue(run_id, True, run.status, subject_matches, False)
        return BenchmarkValue(
            run_id, True, run.status, subject_matches, True,
            _typed_value(measurement), measurement.unit or metric.canonical_unit, measurement.statistic,
        )

    def rule(self, rule_key: str, version: str) -> RuleRecord | None:
        row = self.session.scalar(
            select(RuleDefinition).where(RuleDefinition.rule_key == rule_key, RuleDefinition.version == version)
        )
        if row is None:
            return None
        return RuleRecord(row.rule_key, row.version, row.implementation_ref, tuple(row.input_field_keys or []), row.active)

    def latest_price(self, entity_id: UUID, currency: str, region: str | None, condition: str | None, price_type: str | None) -> LatestPrice:
        from app.models.truth_v3 import PriceSnapshot, SourceDocument

        conditions = [PriceSnapshot.entity_id == entity_id, PriceSnapshot.currency == currency]
        if region is not None: conditions.append(PriceSnapshot.market_region == region)
        if condition is not None: conditions.append(PriceSnapshot.condition == condition)
        if price_type is not None: conditions.append(PriceSnapshot.price_type == price_type)
        row = self.session.scalar(select(PriceSnapshot).where(*conditions).order_by(PriceSnapshot.observed_at.desc()).limit(1))
        if row is None:
            return LatestPrice(False)
        source = self.session.get(SourceDocument, row.source_id)
        return LatestPrice(
            True, row.amount, row.currency, row.id,
            row.source_id,
            source.source_key if source is not None else None,
            source.url if source is not None else None,
            row.observed_at,
            row.market_region,
            row.condition,
            row.price_type,
        )

    def compatibility(self, entity_id: UUID, other_entity_id: UUID, relation_key: str, direction: str) -> CompatibilityRecord:
        from app.models.truth_v3 import CompatibilityEdge

        if direction == "outgoing":
            pair = (CompatibilityEdge.source_entity_id == entity_id, CompatibilityEdge.target_entity_id == other_entity_id)
        else:
            pair = (CompatibilityEdge.source_entity_id == other_entity_id, CompatibilityEdge.target_entity_id == entity_id)
        today = date.today()
        row = self.session.scalar(select(CompatibilityEdge).where(
            *pair,
            CompatibilityEdge.relation_key == relation_key,
            or_(CompatibilityEdge.valid_from.is_(None), CompatibilityEdge.valid_from <= today),
            or_(CompatibilityEdge.valid_to.is_(None), CompatibilityEdge.valid_to >= today),
        ).limit(1))
        return CompatibilityRecord(False) if row is None else CompatibilityRecord(True, row.status)

    def runtime_support(self, entity_id: UUID, runtime_key: str) -> RuntimeSupportRecord:
        row = self.session.scalar(
            select(RuntimeSupportSnapshot)
            .where(RuntimeSupportSnapshot.variant_id == entity_id, RuntimeSupportSnapshot.runtime_key == runtime_key)
            .order_by(RuntimeSupportSnapshot.checked_at.desc())
            .limit(1)
        )
        return RuntimeSupportRecord(False) if row is None else RuntimeSupportRecord(True, row.support_status)


__all__ = [
    "TruthRepository", "SqlAlchemyTruthRepository", "CandidateRecord", "FieldRecord", "StoredValue",
    "EvidenceRecord", "MetricRecord", "BenchmarkValue", "RuleRecord", "LatestPrice",
    "CompatibilityRecord", "RuntimeSupportRecord",
]
