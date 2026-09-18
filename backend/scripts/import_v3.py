"""Validate or transactionally import one accepted Truth V3.2 release."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.data_contracts.v3 import (
    BenchmarkProtocolPayload, BenchmarkRunPayload, Bundle, EntityAliasPayload,
    FieldDefinitionPayload, HardwarePayload, MetricDefinitionPayload, ModelPayload,
    OrganizationPayload, PriceSnapshotPayload, ReleaseManifest, RuleDefinitionPayload,
    RuntimeSupportPayload, SourceDocumentPayload, WorkloadPayload, stable_uuid,
    verify_manifest_file,
)


@dataclass
class Report:
    mode: str
    release: str = ""
    added: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


def _offline_evidence_requirements(payload: Any) -> list[tuple[str, list[Any]]]:
    """Return recommendable entities that need one accepted Bundle Evidence.

    V3.2 treats an accepted source attached to an entity Bundle as support for
    all facts in that Bundle.  Explicit field-level claims remain stored and
    validated when supplied, but are no longer required for every non-null
    fact.
    """
    result: list[tuple[str, list[Any]]] = []
    identity = getattr(payload, "identity", None)
    if identity is not None and identity.recommendable:
        result.append((identity.entity_key, getattr(payload, "evidence", [])))
    if isinstance(payload, ModelPayload):
        for variant in payload.variants:
            if not variant.identity.recommendable: continue
            result.append((variant.identity.entity_key, variant.evidence))
    return result


def _fact_value(value: Any) -> Any:
    """Normalize JSON-compatible fact values for exact Evidence comparison."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return ("number", Decimal(str(value)).normalize())
    if isinstance(value, (date, datetime)):
        return ("text", value.isoformat())
    if isinstance(value, str):
        return ("text", value)
    if isinstance(value, dict):
        return ("object", tuple(sorted((key, _fact_value(item)) for key, item in value.items())))
    if isinstance(value, (list, tuple)):
        return ("array", tuple(_fact_value(item) for item in value))
    return ("text", str(value))


def _entity_fact_sets(payload: Any) -> list[tuple[str, dict[str, list[Any]], list[Any]]]:
    """Return claimable values and Evidence grouped by the entity they describe."""
    result: list[tuple[str, dict[str, list[Any]], list[Any]]] = []

    def collect(identity: Any, evidence: list[Any], values: dict[str, Any], attributes: list[Any] | None = None) -> None:
        facts: dict[str, list[Any]] = {}

        def add(field_key: str, value: Any) -> None:
            if value is not None:
                facts.setdefault(field_key, []).append(value)

        add("entity.canonical_name", identity.canonical_name)
        add("entity.release_date", identity.release_date)
        for field_key, value in values.items():
            add(field_key, value)
        for attribute in attributes or []:
            for name in ("value_text", "value_number", "value_integer", "value_boolean", "value_date", "value_json"):
                value = getattr(attribute, name)
                if value is not None:
                    add(attribute.field_key, value)
        result.append((identity.entity_key, facts, evidence))

    identity = getattr(payload, "identity", None)
    if isinstance(payload, OrganizationPayload):
        collect(identity, payload.evidence, {
            "organization.website_url": str(payload.website_url) if payload.website_url else None,
            "organization.organization_type": payload.organization_type,
            "organization.country_or_region": payload.country_or_region,
        })
    elif isinstance(payload, HardwarePayload):
        values = {
            "hardware.category": payload.category,
            "hardware.manufacturer": payload.manufacturer_key,
            "hardware.record_kind": payload.record_kind,
            "hardware.product_family": payload.product_family,
            "hardware.model_name": payload.model_name or identity.canonical_name,
            "hardware.variant_name": payload.variant_name,
            "hardware.market_segment": payload.market_segment,
            "hardware.form_factor": payload.form_factor,
            "hardware.region_code": payload.region_code,
            "hardware.official_product_id": payload.official_product_id,
        }
        for name, prefix in (("cpu_spec", "cpu"), ("gpu_spec", "gpu"), ("laptop_gpu_spec", "laptop_gpu"),
                             ("datacenter_gpu_spec", "datacenter_gpu"), ("memory_spec", "memory"),
                             ("storage_spec", "storage"), ("psu_spec", "psu"), ("platform_spec", "platform")):
            spec = getattr(payload, name)
            if spec is not None:
                for key, value in spec.model_dump(exclude_none=True, mode="python").items():
                    values[f"{prefix}.{key}"] = value
        collect(identity, payload.evidence, values, payload.attributes)
    elif isinstance(payload, ModelPayload):
        collect(identity, payload.evidence, {
            "model.publisher": payload.publisher_key,
            "model.family": payload.family,
            "model.model_type": payload.model_type,
            "model.total_parameters": payload.total_parameters,
            "model.active_parameters": payload.active_parameters,
            "model.context_length_tokens": payload.context_length_tokens,
            "model.license_name": payload.license_name,
            "model.model_stage": payload.model_stage,
            "model.commercial_use_status": payload.commercial_use_status,
            **{f"model.capability.{item.capability_key}": item.status for item in payload.capabilities},
        }, payload.attributes)
        for variant in payload.variants:
            collect(variant.identity, variant.evidence, {
                "variant.maintainer": variant.maintainer_key,
                "variant.quantization_method": variant.quantization_method,
                "variant.weight_format": variant.weight_format,
                "variant.bits_per_weight": variant.bits_per_weight,
                "variant.file_size_bytes": variant.file_size_bytes,
                "variant.repository_url": str(variant.repository_url) if variant.repository_url else None,
                "variant.artifact_revision": variant.artifact_revision,
                "variant.official_status": variant.official_status,
                "variant.checksum": variant.checksum,
            })
    elif isinstance(payload, WorkloadPayload):
        collect(identity, payload.evidence, {
            "workload.workload_type": payload.workload_type,
            "workload.publisher_id": payload.publisher_key,
            "workload.family": payload.family,
            "workload.current_version": payload.current_version,
            "workload.engine": payload.engine,
            "workload.official_url": str(payload.official_url) if payload.official_url else None,
        })
    return result


def _validate_evidence_matches(payload: Any) -> list[str]:
    errors: list[str] = []
    for entity_key, facts, refs in _entity_fact_sets(payload):
        for ref in refs:
            expected = facts.get(ref.field_key)
            if expected is None:
                continue
            if ref.normalized_value is None:
                errors.append(f"{entity_key} Evidence {ref.field_key} is missing normalized_value")
                continue
            if ref.review_status == "conflict":
                continue
            actual = _fact_value(ref.normalized_value)
            if all(actual != _fact_value(value) for value in expected):
                errors.append(
                    f"{entity_key} Evidence {ref.field_key} normalized_value does not match the Bundle fact"
                )
    return errors


def load_release(path: Path, report: Report) -> tuple[ReleaseManifest, list[tuple[Path, Bundle]]]:
    manifest = ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
    report.release = manifest.release_key
    bundles: list[tuple[Path, Bundle]] = []
    for item in manifest.files:
        try:
            file_path = verify_manifest_file(path, item)
            bundle = Bundle.model_validate_json(file_path.read_text(encoding="utf-8"))
            if bundle.status != "accepted":
                raise ValueError("only accepted bundles may appear in a release")
            if bundle.record_type != item.record_type:
                raise ValueError(f"manifest record_type {item.record_type} does not match bundle {bundle.record_type}")
            bundles.append((file_path, bundle))
            report.files.append({"path": item.path, "status": "validated", "record_type": item.record_type})
        except Exception as exc:
            report.rejected += 1
            report.errors.append(f"{item.path}: {exc}")
    if report.errors:
        raise ValueError("release validation failed")

    natural_keys: set[tuple[str, str]] = set()
    for file_path, bundle in bundles:
        p = bundle.payload
        keys: list[tuple[str, str]] = []
        identity = getattr(p, "identity", None)
        if identity: keys.append(("entity", identity.entity_key))
        if isinstance(p, ModelPayload): keys.extend(("entity", item.identity.entity_key) for item in p.variants)
        if isinstance(p, SourceDocumentPayload): keys.append(("source", p.source_key))
        if isinstance(p, FieldDefinitionPayload): keys.append(("field", p.field_key))
        if isinstance(p, MetricDefinitionPayload): keys.append(("metric", p.metric_key))
        if isinstance(p, BenchmarkProtocolPayload): keys.append(("protocol", f"{p.protocol_key}:{p.version}"))
        if isinstance(p, BenchmarkRunPayload): keys.append(("benchmark_run", p.run_key))
        if isinstance(p, PriceSnapshotPayload): keys.append(("price", p.price_key))
        if isinstance(p, RuntimeSupportPayload): keys.append(("runtime", p.snapshot_key))
        if isinstance(p, RuleDefinitionPayload): keys.append(("rule", f"{p.rule_key}:{p.version}"))
        if isinstance(p, EntityAliasPayload): keys.append(("alias", f"{p.entity_key}:{p.normalized_alias}"))
        for key in keys:
            if key in natural_keys: report.errors.append(f"{file_path.name}: duplicate release key {key[0]}:{key[1]}")
            natural_keys.add(key)
    if report.errors:
        report.rejected = len(report.errors)
        raise ValueError("release natural-key validation failed")

    fields = {b.payload.field_key: b.payload for _, b in bundles if isinstance(b.payload, FieldDefinitionPayload)}
    for file_path, bundle in bundles:
        payload = bundle.payload
        for entity_key, refs in _offline_evidence_requirements(payload):
            if not any(ref.review_status == "accepted" for ref in refs):
                report.errors.append(f"{file_path.name}: {entity_key} lacks accepted Bundle Evidence")
        report.errors.extend(f"{file_path.name}: {error}" for error in _validate_evidence_matches(payload))
        for attribute in getattr(payload, "attributes", []):
            definition = fields.get(attribute.field_key)
            if definition and attribute.unit and attribute.unit not in set(definition.allowed_units + ([definition.canonical_unit] if definition.canonical_unit else [])):
                report.errors.append(f"{file_path.name}: illegal unit {attribute.unit!r} for {attribute.field_key}")
    if report.errors:
        report.rejected = len(report.errors)
        raise ValueError("release reference validation failed")
    return manifest, bundles


def _values(model: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    values = model.model_dump(mode="python", exclude_none=True)
    for key in exclude or set(): values.pop(key, None)
    return values


def _upsert(session: Any, cls: Any, lookup: dict[str, Any], values: dict[str, Any], report: Report) -> Any:
    from sqlalchemy import select
    obj = session.scalar(select(cls).filter_by(**lookup))
    if obj is None:
        obj = cls(**lookup, **values); session.add(obj); session.flush(); report.added += 1
    else:
        changed = False
        for key, value in values.items():
            if getattr(obj, key) != value:
                setattr(obj, key, value); changed = True
        if changed: report.updated += 1
        else: report.skipped += 1
    return obj


def _append_only(session: Any, cls: Any, lookup: dict[str, Any], values: dict[str, Any], report: Report) -> Any:
    """Insert history once; an identical replay skips and mutation is refused."""
    from sqlalchemy import select
    obj = session.scalar(select(cls).filter_by(**lookup))
    if obj is None:
        obj = cls(**lookup, **values); session.add(obj); session.flush(); report.added += 1
        return obj
    changed = [key for key, value in values.items() if getattr(obj, key) != value]
    if changed: raise ValueError(f"append-only {cls.__tablename__} record changed: {changed}")
    report.skipped += 1
    return obj


def _entity(session: Any, identity: Any, report: Report) -> Any:
    from app.models.truth_v3 import CatalogEntity
    return _upsert(session, CatalogEntity, {"id": identity.id}, {
        "entity_key": identity.entity_key, "entity_type": identity.entity_type,
        "canonical_name": identity.canonical_name, "release_date": identity.release_date,
        "lifecycle_status": identity.lifecycle_status, "recommendable": identity.recommendable,
    }, report)


def _guard_update(session: Any, cls: Any, lookup: dict[str, Any], values: dict[str, Any],
                  evidence: list[Any], field_map: dict[str, str]) -> None:
    """Refuse an existing static-field change without accepted Bundle Evidence."""
    from sqlalchemy import select
    obj = session.scalar(select(cls).filter_by(**lookup))
    if obj is None: return
    changed = any(key in field_map and getattr(obj, key) != value for key, value in values.items())
    if changed and not any(item.review_status == "accepted" for item in evidence):
        raise ValueError("static update lacks accepted Bundle Evidence")


def _require_evidence(refs: list[Any], label: str) -> None:
    if not any(item.review_status == "accepted" for item in refs):
        raise ValueError(f"{label} lacks accepted Bundle Evidence")


def _actual_value_type(value: Any) -> str:
    from datetime import date as date_type
    from decimal import Decimal as decimal_type
    if isinstance(value, bool): return "boolean"
    if isinstance(value, int): return "integer"
    if isinstance(value, (float, decimal_type)): return "number"
    if isinstance(value, date_type): return "date"
    if isinstance(value, str): return "string"
    if isinstance(value, (dict, list)): return "json"
    return "unknown"


def _validate_object_schema(value: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    """Validate the controlled object subset used by benchmark protocols."""
    if not schema: return
    if schema.get("type", "object") != "object": raise ValueError(f"{label} schema must describe an object")
    missing = sorted(set(schema.get("required", [])) - set(value))
    if missing: raise ValueError(f"{label} is missing required keys: {missing}")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra: raise ValueError(f"{label} has undeclared keys: {extra}")
    json_types = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list}
    for key, item in value.items():
        expected = properties.get(key, {}).get("type")
        wrong_boolean_number = expected in {"number", "integer"} and isinstance(item, bool)
        if expected in json_types and (wrong_boolean_number or not isinstance(item, json_types[expected])):
            raise ValueError(f"{label}.{key} must be {expected}")


def _evidence(session: Any, entity_key: str, refs: list[Any], report: Report) -> None:
    from sqlalchemy import select
    from app.models.truth_v3 import EvidenceClaim, FieldDefinition, SourceDocument
    for ref in refs:
        source = session.scalar(select(SourceDocument).where(SourceDocument.source_key == ref.source_key))
        if source is None: raise ValueError(f"unknown source_key: {ref.source_key}")
        field_def = session.scalar(select(FieldDefinition).where(FieldDefinition.field_key == ref.field_key))
        if field_def is None: raise ValueError(f"unknown Evidence field_key: {ref.field_key}")
        if ref.normalized_value is not None:
            actual = _actual_value_type(ref.normalized_value)
            expected = field_def.value_type
            if not (expected == actual or (expected == "number" and actual == "integer") or (expected == "date" and actual == "string")):
                raise ValueError(f"Evidence {ref.field_key} expects {expected}, got {actual}")
        allowed_units = set(field_def.allowed_units or []) | ({field_def.canonical_unit} if field_def.canonical_unit else set())
        if ref.unit_key and ref.unit_key not in allowed_units: raise ValueError(f"illegal Evidence unit {ref.unit_key!r} for {ref.field_key}")
        fingerprint = sha256(json.dumps(ref.normalized_value, sort_keys=True, default=str).encode()).hexdigest()[:20]
        evidence_key = f"evidence:{entity_key}:{ref.source_key}:{ref.field_key}:{fingerprint}"
        _upsert(session, EvidenceClaim, {"id": stable_uuid(evidence_key)}, {
            "source_id": source.id, "entity_id": stable_uuid(entity_key), "field_key": ref.field_key,
            "normalized_value": ref.normalized_value, "confidence": ref.confidence, "review_status": ref.review_status,
            "unit_key": ref.unit_key, "raw_excerpt": ref.raw_excerpt, "source_locator": ref.source_locator,
            # Evidence-level time wins.  Older bundles legitimately omit it, in
            # which case the source access time is the auditable observation time.
            # Never leave provenance time empty or replace it with a release-wide
            # constant.
            "provenance_key": ref.provenance_key,
            "collected_at": ref.collected_at or source.accessed_at,
            "reviewed_at": ref.reviewed_at, "notes": ref.notes,
        }, report)


def _attributes(session: Any, entity_key: str, items: list[Any], evidence: list[Any], report: Report) -> None:
    from sqlalchemy import select
    from app.models.truth_v3 import EntityAttribute, FieldDefinition
    for item in items:
        field_def = session.scalar(select(FieldDefinition).where(FieldDefinition.field_key == item.field_key))
        if field_def is None: raise ValueError(f"unknown field_key: {item.field_key}")
        if field_def.storage_kind == "column": raise ValueError(f"fixed-column field cannot be duplicated as Attribute: {item.field_key}")
        allowed = set(field_def.allowed_units or []) | ({field_def.canonical_unit} if field_def.canonical_unit else set())
        if item.unit and item.unit not in allowed: raise ValueError(f"illegal unit {item.unit!r} for {item.field_key}")
        values = _values(item, {"field_key", "qualifier_key"})
        typed = {"value_text": "string", "value_number": "number", "value_integer": "integer",
                 "value_boolean": "boolean", "value_date": "date", "value_json": "json"}
        supplied_type = next((kind for key, kind in typed.items() if getattr(item, key) is not None), None)
        if supplied_type and not (field_def.value_type == supplied_type or (field_def.value_type == "number" and supplied_type == "integer")):
            raise ValueError(f"Attribute {item.field_key} expects {field_def.value_type}, got {supplied_type}")
        _guard_update(session, EntityAttribute,
            {"id": stable_uuid(f"attribute:{entity_key}:{item.field_key}:{item.qualifier_key}")}, values, evidence,
            {key: item.field_key for key in values})
        _upsert(session, EntityAttribute, {"id": stable_uuid(f"attribute:{entity_key}:{item.field_key}:{item.qualifier_key}")},
                {"entity_id": stable_uuid(entity_key), "field_id": field_def.id, "qualifier_key": item.qualifier_key, **values}, report)


def _spec(session: Any, cls: Any, entity_key: str, spec: Any, evidence: list[Any], prefix: str, report: Report) -> None:
    raw = spec.model_dump(mode="python", exclude_none=True)
    columns = {column.name for column in cls.__table__.columns} - {"hardware_id"}
    extra = {key: value for key, value in raw.items() if key not in columns}
    if extra:
        raise ValueError(f"{prefix}_spec has no database column for: {sorted(extra)}")
    known = dict(raw)
    field_map = {key: f"{prefix}.{key}" for key in known}
    _guard_update(session, cls, {"hardware_id": stable_uuid(entity_key)}, known, evidence, field_map)
    _upsert(session, cls, {"hardware_id": stable_uuid(entity_key)}, known, report)


def import_bundles(session: Any, manifest: ReleaseManifest, manifest_path: Path, bundles: list[tuple[Path, Bundle]], report: Report) -> None:
    from sqlalchemy import select
    from app.models.truth_v3 import (
        AIModel, BenchmarkMetric, BenchmarkProtocol, BenchmarkRun, BenchmarkSubject, CatalogEntity,
        CpuSpec, DatacenterGpuSpec, DatasetRelease, EntityAlias, FieldDefinition, GpuSpec, Hardware, ImportBatch,
        LaptopGpuSpec, MemorySpec, MetricDefinition, ModelCapability, ModelVariant, Organization,
        PlatformSpec, PriceSnapshot, PsuSpec, RuleDefinition, RuntimeSupportSnapshot, SourceDocument, StorageSpec,
        Workload, WorkloadProfile, WorkloadRequirement,
    )
    release_id = stable_uuid("release:" + manifest.release_key)
    manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()
    existing_release = session.scalar(select(DatasetRelease).where(DatasetRelease.release_key == manifest.release_key))
    if existing_release and existing_release.manifest_sha256 != manifest_hash:
        raise ValueError("release_key already exists with a different manifest hash")
    release = _upsert(session, DatasetRelease, {"id": release_id}, {"release_key": manifest.release_key,
        "manifest_sha256": manifest_hash, "created_at": manifest.created_at, "released_at": manifest.created_at,
        "git_commit": manifest.git_commit, "status": "accepted", "description": manifest.description}, report)
    batch = ImportBatch(id=uuid4(), release_id=release.id, mode=report.mode,
        started_at=datetime.now(timezone.utc), status="running", report={})
    session.add(batch); session.flush()

    priority = {"organization": 0, "field_definition": 0, "metric_definition": 0, "benchmark_protocol": 0,
                "source_document": 1, "hardware": 2, "model": 2, "workload": 2, "rule_definition": 3,
                "benchmark_run": 4, "price_snapshot": 4, "runtime_support": 4, "entity_alias": 5}
    for _, bundle in sorted(bundles, key=lambda pair: priority[pair[1].record_type]):
        p = bundle.payload
        if isinstance(p, SourceDocumentPayload):
            source_values = _values(p, {"source_key", "publisher_key", "archive_url"}) | {
                "source_key": p.source_key, "url": str(p.url), "archive_url": str(p.archive_url) if p.archive_url else None,
                "publisher_id": stable_uuid(p.publisher_key) if p.publisher_key else None,
            }
            _upsert(session, SourceDocument, {"id": stable_uuid("source:" + p.source_key)}, source_values, report)
        elif isinstance(p, FieldDefinitionPayload):
            _upsert(session, FieldDefinition, {"id": stable_uuid("field:" + p.field_key)}, _values(p, {"field_key"}) | {"field_key": p.field_key}, report)
        elif isinstance(p, MetricDefinitionPayload):
            _upsert(session, MetricDefinition, {"id": stable_uuid("metric:" + p.metric_key)}, _values(p, {"metric_key"}) | {"metric_key": p.metric_key}, report)
        elif isinstance(p, BenchmarkProtocolPayload):
            protocol_values = _values(p, {"method_document_url"}) | {"method_document_url": str(p.method_document_url) if p.method_document_url else None}
            _append_only(session, BenchmarkProtocol, {"id": stable_uuid(f"protocol:{p.protocol_key}:{p.version}")}, protocol_values, report)
        elif isinstance(p, EntityAliasPayload):
            entity = session.scalar(select(CatalogEntity).where(CatalogEntity.entity_key == p.entity_key))
            if not entity:
                raise ValueError(f"unknown alias entity_key: {p.entity_key}")
            _upsert(session, EntityAlias,
                {"id": stable_uuid(f"alias:{p.entity_key}:{p.normalized_alias}")},
                {"entity_id": entity.id, "alias": p.alias, "alias_type": p.alias_type,
                 "language": p.language, "normalized_alias": p.normalized_alias}, report)
        elif isinstance(p, RuleDefinitionPayload):
            source = session.scalar(select(SourceDocument).where(SourceDocument.source_key == p.source_key)) if p.source_key else None
            if p.source_key and not source: raise ValueError(f"unknown rule source_key: {p.source_key}")
            rule_values = _values(p, {"source_key"}) | {"source_document_id": source.id if source else None}
            _append_only(session, RuleDefinition, {"id": stable_uuid(f"rule:{p.rule_key}:{p.version}")}, rule_values, report)
        elif isinstance(p, OrganizationPayload):
            if p.identity.recommendable:
                _require_evidence(p.evidence, p.identity.entity_key)
            _guard_update(session, CatalogEntity, {"id": p.identity.id}, {"canonical_name": p.identity.canonical_name}, p.evidence, {"canonical_name": "entity.canonical_name"})
            _entity(session, p.identity, report)
            org_values = {"website_url": str(p.website_url) if p.website_url else None,
                "organization_type": p.organization_type, "country_or_region": p.country_or_region}
            _guard_update(session, Organization, {"entity_id": p.identity.id}, org_values, p.evidence,
                {key: f"organization.{key}" for key in org_values})
            _upsert(session, Organization, {"entity_id": p.identity.id}, org_values, report)
        elif isinstance(p, HardwarePayload):
            if p.identity.recommendable: _require_evidence(p.evidence, p.identity.entity_key)
            _guard_update(session, CatalogEntity, {"id": p.identity.id},
                {"canonical_name": p.identity.canonical_name, "release_date": p.identity.release_date}, p.evidence,
                {"canonical_name": "entity.canonical_name", "release_date": "entity.release_date"})
            _entity(session, p.identity, report)
            manufacturer_id = stable_uuid(p.manufacturer_key) if p.manufacturer_key else None
            hardware_values = {"category": p.category, "manufacturer_id": manufacturer_id,
                "product_family": p.product_family, "model_name": p.model_name or p.identity.canonical_name,
                "variant_name": p.variant_name, "form_factor": p.form_factor, "record_kind": p.record_kind,
                "market_segment": p.market_segment, "region_code": p.region_code, "official_product_id": p.official_product_id}
            hardware_field_map = {key: f"hardware.{key}" for key in hardware_values}
            hardware_field_map.update({"category": "hardware.category", "manufacturer_id": "hardware.manufacturer"})
            _guard_update(session, Hardware, {"entity_id": p.identity.id}, hardware_values, p.evidence, hardware_field_map)
            _upsert(session, Hardware, {"entity_id": p.identity.id}, hardware_values, report)
            for attr, cls, prefix in (("cpu_spec", CpuSpec, "cpu"), ("gpu_spec", GpuSpec, "gpu"),
                ("laptop_gpu_spec", LaptopGpuSpec, "laptop_gpu"), ("datacenter_gpu_spec", DatacenterGpuSpec, "datacenter_gpu"),
                ("memory_spec", MemorySpec, "memory"), ("storage_spec", StorageSpec, "storage"),
                ("psu_spec", PsuSpec, "psu"), ("platform_spec", PlatformSpec, "platform")):
                raw = getattr(p, attr)
                if raw is not None: _spec(session, cls, p.identity.entity_key, raw, p.evidence, prefix, report)
            _attributes(session, p.identity.entity_key, p.attributes, p.evidence, report)
        elif isinstance(p, ModelPayload):
            if p.identity.recommendable: _require_evidence(p.evidence, p.identity.entity_key)
            _guard_update(session, CatalogEntity, {"id": p.identity.id},
                {"canonical_name": p.identity.canonical_name, "release_date": p.identity.release_date}, p.evidence,
                {"canonical_name": "entity.canonical_name", "release_date": "entity.release_date"})
            _entity(session, p.identity, report)
            model_values = {"publisher_id": stable_uuid(p.publisher_key) if p.publisher_key else None,
                "family": p.family, "total_parameters": p.total_parameters, "active_parameters": p.active_parameters,
                "context_length_tokens": p.context_length_tokens,
                "license_name": p.license_name, "release_version": p.release_version, "model_type": p.model_type,
                "model_stage": p.model_stage, "commercial_use_status": p.commercial_use_status,
                "official_repo_url": str(p.official_repo_url) if p.official_repo_url else None,
                "official_model_card_url": str(p.official_model_card_url) if p.official_model_card_url else None,
                "architecture_notes": p.architecture_notes}
            model_field_map = {key: f"model.{key}" for key in model_values}
            model_field_map.update({"publisher_id": "model.publisher", "license_name": "model.license_name"})
            _guard_update(session, AIModel, {"entity_id": p.identity.id}, model_values, p.evidence, model_field_map)
            _upsert(session, AIModel, {"entity_id": p.identity.id}, model_values, report)
            for cap in p.capabilities:
                cap_values = {"model_id": p.identity.id, "capability_key": cap.capability_key,
                    "support_status": cap.status, "notes": cap.notes}
                _guard_update(session, ModelCapability, {"id": stable_uuid(f"capability:{p.identity.entity_key}:{cap.capability_key}")},
                    cap_values, p.evidence, {"support_status": f"model.capability.{cap.capability_key}", "notes": f"model.capability.{cap.capability_key}"})
                _upsert(session, ModelCapability, {"id": stable_uuid(f"capability:{p.identity.entity_key}:{cap.capability_key}")},
                    cap_values, report)
            for variant in p.variants:
                if variant.identity.recommendable: _require_evidence(variant.evidence, variant.identity.entity_key)
                _guard_update(session, CatalogEntity, {"id": variant.identity.id},
                    {"canonical_name": variant.identity.canonical_name, "release_date": variant.identity.release_date}, variant.evidence,
                    {"canonical_name": "entity.canonical_name", "release_date": "entity.release_date"})
                _entity(session, variant.identity, report)
                variant_values = {"model_id": p.identity.id,
                    **_values(variant, {"identity", "evidence", "maintainer_key", "repository_url"}),
                    "maintainer_id": stable_uuid(variant.maintainer_key) if variant.maintainer_key else None,
                    "repository_url": str(variant.repository_url) if variant.repository_url else None,
                    "artifact_manifest": [item.model_dump(mode="json") for item in variant.artifact_manifest] if variant.artifact_manifest else None}
                variant_field_map = {key: f"variant.{key}" for key in variant_values if key != "model_id"}
                _guard_update(session, ModelVariant, {"entity_id": variant.identity.id}, variant_values, variant.evidence, variant_field_map)
                _upsert(session, ModelVariant, {"entity_id": variant.identity.id}, variant_values, report)
            _attributes(session, p.identity.entity_key, p.attributes, p.evidence, report)
        elif isinstance(p, WorkloadPayload):
            if p.identity.recommendable: _require_evidence(p.evidence, p.identity.entity_key)
            _guard_update(session, CatalogEntity, {"id": p.identity.id},
                {"canonical_name": p.identity.canonical_name, "release_date": p.identity.release_date}, p.evidence,
                {"canonical_name": "entity.canonical_name", "release_date": "entity.release_date"})
            _entity(session, p.identity, report)
            workload_values = {"description": p.description,
                "publisher_id": stable_uuid(p.publisher_key) if p.publisher_key else None,
                "workload_type": p.workload_type, "family": p.family, "current_version": p.current_version,
                "engine": p.engine, "official_url": str(p.official_url) if p.official_url else None}
            _guard_update(session, Workload, {"entity_id": p.identity.id}, workload_values, p.evidence,
                {key: f"workload.{key}" for key in workload_values})
            _upsert(session, Workload, {"entity_id": p.identity.id}, workload_values, report)
            profiles = {}
            for profile in p.profiles:
                profile_id = stable_uuid(f"workload-profile:{p.identity.entity_key}:{profile.profile_key}"); profiles[profile.profile_key] = profile_id
                source = session.scalar(select(SourceDocument).where(SourceDocument.source_key == profile.source_key)) if profile.source_key else None
                if profile.source_key and not source: raise ValueError(f"unknown workload profile source_key: {profile.source_key}")
                profile_values = {"workload_id": p.identity.id, **_values(profile, {"source_key"}),
                    "source_document_id": source.id if source else None}
                _upsert(session, WorkloadProfile, {"id": profile_id}, profile_values, report)
            for index, req in enumerate(p.requirements):
                requirement_values = {"profile_id": profiles[req.profile_key], **_values(req, {"profile_key", "reference_entity_key"}),
                    "reference_entity_id": stable_uuid(req.reference_entity_key) if req.reference_entity_key else None}
                _upsert(session, WorkloadRequirement, {"id": stable_uuid(f"requirement:{p.identity.entity_key}:{index}")},
                    requirement_values, report)
        elif isinstance(p, BenchmarkRunPayload):
            protocol_id = stable_uuid(f"protocol:{p.protocol_key}:{p.protocol_version}")
            protocol = session.get(BenchmarkProtocol, protocol_id)
            if not protocol:
                raise ValueError(f"unknown benchmark protocol: {p.protocol_key}@{p.protocol_version}")
            run_id = stable_uuid("benchmark-run:" + p.run_key)
            source = session.scalar(select(SourceDocument).where(SourceDocument.source_key == p.source_key)) if p.source_key else None
            if p.source_key and not source: raise ValueError(f"unknown benchmark source_key: {p.source_key}")
            _validate_object_schema(p.settings, protocol.settings_schema, "benchmark settings")
            _validate_object_schema(p.environment, protocol.environment_schema, "benchmark environment")
            _append_only(session, BenchmarkRun, {"id": run_id}, {"run_key": p.run_key, "protocol_id": protocol.id,
                "workload_id": stable_uuid(p.workload_key) if p.workload_key else None, "started_at": p.started_at,
                "completed_at": p.completed_at, "environment": p.environment, "release_id": release_id,
                "source_document_id": source.id if source else None, "run_origin": p.run_origin, "test_date": p.test_date,
                "status": p.status, "settings": p.settings, "settings_hash": p.settings_hash, "sample_count": p.sample_count,
                "aggregation_method": p.aggregation_method, "raw_result_url": str(p.raw_result_url) if p.raw_result_url else None,
                "raw_result_hash": p.raw_result_hash, "notes": p.notes}, report)
            for subject in p.subjects:
                _append_only(session, BenchmarkSubject, {"id": stable_uuid(f"benchmark-subject:{p.run_key}:{subject.entity_key}:{subject.role}")},
                    {"run_id": run_id, "entity_id": stable_uuid(subject.entity_key), "role": subject.role,
                     "is_primary": subject.is_primary, "quantity": subject.quantity}, report)
            for metric in p.metrics:
                definition = session.scalar(select(MetricDefinition).where(MetricDefinition.metric_key == metric.metric_key))
                if not definition: raise ValueError(f"unknown metric_key: {metric.metric_key}")
                supplied_type = ("number" if metric.value_number is not None else "integer" if metric.value_integer is not None
                    else "boolean" if metric.value_boolean is not None else "string")
                if not (definition.value_type == supplied_type or (definition.value_type == "number" and supplied_type == "integer")):
                    raise ValueError(f"metric {metric.metric_key} expects {definition.value_type}, got {supplied_type}")
                if definition.canonical_unit and metric.unit != definition.canonical_unit:
                    raise ValueError(f"metric {metric.metric_key} requires unit {definition.canonical_unit!r}")
                _append_only(session, BenchmarkMetric, {"id": stable_uuid(f"benchmark-metric:{p.run_key}:{metric.metric_key}:{metric.statistic}")},
                    {"run_id": run_id, "metric_id": definition.id, **_values(metric, {"metric_key"})}, report)
        elif isinstance(p, PriceSnapshotPayload):
            source = session.scalar(select(SourceDocument).where(SourceDocument.source_key == p.source_key))
            if not source: raise ValueError(f"unknown source_key: {p.source_key}")
            _append_only(session, PriceSnapshot, {"id": stable_uuid("price:" + p.price_key)}, {**_values(p, {"price_key", "entity_key", "source_key", "seller_key"}),
                "price_key": p.price_key, "entity_id": stable_uuid(p.entity_key), "source_id": source.id,
                "seller_id": stable_uuid(p.seller_key) if p.seller_key else None}, report)
        elif isinstance(p, RuntimeSupportPayload):
            source = session.scalar(select(SourceDocument).where(SourceDocument.source_key == p.source_key)) if p.source_key else None
            if p.source_key and not source: raise ValueError(f"unknown runtime source_key: {p.source_key}")
            _append_only(session, RuntimeSupportSnapshot, {"id": stable_uuid("runtime:" + p.snapshot_key)}, {
                "support_status": p.support_status, "variant_id": stable_uuid(p.variant_key), "runtime_key": p.runtime_key,
                "min_version": p.min_version, "tested_version": p.tested_version, "backend": p.backend,
                "checked_at": p.checked_at, "notes": p.notes, "source_document_id": source.id if source else None,
            }, report)
    # Evidence is deliberately a final pass: definitions, sources and every
    # referenced entity now exist regardless of Manifest file ordering.
    for _, bundle in bundles:
        p = bundle.payload
        identity = getattr(p, "identity", None)
        if identity is not None:
            _evidence(session, identity.entity_key, getattr(p, "evidence", []), report)
        if isinstance(p, ModelPayload):
            for variant in p.variants:
                _evidence(session, variant.identity.entity_key, variant.evidence, report)
    batch.status = "succeeded"; batch.completed_at = datetime.now(timezone.utc); batch.report = report.as_dict()
    batch.added_count = report.added; batch.updated_count = report.updated; batch.skipped_count = report.skipped
    batch.rejected_count = report.rejected; batch.error_report = {"errors": report.errors}


def emit_report(report: Report, output: Path | None, text_output: Path | None = None) -> None:
    rendered = json.dumps(report.as_dict(), ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    if text_output:
        lines = [f"Truth V3.2 import report: {report.release or '<unresolved>'}", f"Mode: {report.mode}",
            f"Added: {report.added}", f"Updated: {report.updated}", f"Skipped: {report.skipped}",
            f"Rejected: {report.rejected}"]
        lines.extend(f"ERROR: {error}" for error in report.errors)
        text_output.parent.mkdir(parents=True, exist_ok=True)
        text_output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true"); mode.add_argument("--dry-run", action="store_true"); mode.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--text-report", type=Path)
    args = parser.parse_args()
    mode_name = "validate-only" if args.validate_only else "dry-run" if args.dry_run else "apply"
    report = Report(mode=mode_name)
    try:
        manifest, bundles = load_release(args.release, report)
        if args.validate_only:
            report.skipped = len(bundles); emit_report(report, args.report, args.text_report); return 0
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            from app.core.config import get_settings
            database_url = get_settings().database_url
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        engine = create_engine(database_url)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(bind=connection) as session:
                    import_bundles(session, manifest, args.release, bundles, report); session.flush()
                if args.apply: transaction.commit()
                else: transaction.rollback()
            except Exception:
                transaction.rollback(); raise
    except Exception as exc:  # operator-facing JSON report; transaction has already rolled back
        report.rejected += 1; report.errors.append(str(exc)); emit_report(report, args.report, args.text_report); return 1
    emit_report(report, args.report, args.text_report); return 0


if __name__ == "__main__":
    raise SystemExit(main())
