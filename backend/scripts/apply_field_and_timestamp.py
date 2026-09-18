"""Register ``cpu.integrated_gpu`` and repair Evidence collection timestamps.

``collected_at`` is provenance, not a dataset-wide snapshot label.  Earlier
versions of this maintenance script overwrote every row with 2026-09-07, even
when a Release explicitly recorded a later collection time.  The repair below
replays the versioned Release bundles in chronological order and restores each
Evidence row to:

1. its explicit ``EvidenceRef.collected_at`` value, or
2. the source document's ``accessed_at`` value when the Evidence omitted one.

The second value is the closest auditable observation time available; no
invented global default is used.  The operation is repeatable and ``--dry-run``
reports the exact changes without writing them.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql

from app.db.session import engine
from app.data_contracts.v3 import (
    Bundle,
    ReleaseManifest,
    SourceDocumentPayload,
    stable_uuid,
    verify_manifest_file,
)
from scripts.import_v3 import _entity_fact_sets

FIELD_KEY = "cpu.integrated_gpu"
RELEASES_ROOT = Path(__file__).resolve().parents[2] / "data" / "v3" / "releases"


def _evidence_id(entity_key: str, ref: Any):
    fingerprint = sha256(
        json.dumps(ref.normalized_value, sort_keys=True, default=str).encode()
    ).hexdigest()[:20]
    return stable_uuid(
        f"evidence:{entity_key}:{ref.source_key}:{ref.field_key}:{fingerprint}"
    )


def release_evidence_times(
    releases_root: Path = RELEASES_ROOT,
) -> dict[Any, tuple[datetime, str]]:
    """Return the newest auditable timestamp for every Evidence natural key.

    Releases are replayed by ``created_at``.  Source documents in a Release are
    applied before that Release's entity bundles, so an Evidence row without an
    explicit timestamp receives the source access time that was current when the
    row was imported.  A later Release of the same Evidence key deliberately wins,
    matching the importer's deterministic upsert behaviour.
    """
    releases: list[tuple[datetime, Path, ReleaseManifest]] = []
    for manifest_path in releases_root.glob("*/manifest.json"):
        manifest = ReleaseManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        releases.append((manifest.created_at, manifest_path, manifest))

    source_times: dict[str, datetime] = {}
    evidence_times: dict[Any, tuple[datetime, str]] = {}
    for _, manifest_path, manifest in sorted(releases, key=lambda item: (item[0], item[2].release_key)):
        bundles: list[Bundle] = []
        for item in manifest.files:
            bundle_path = verify_manifest_file(manifest_path, item)
            bundles.append(Bundle.model_validate_json(bundle_path.read_text(encoding="utf-8")))

        for bundle in bundles:
            if isinstance(bundle.payload, SourceDocumentPayload):
                source_times[bundle.payload.source_key] = bundle.payload.accessed_at

        for bundle in bundles:
            for entity_key, _, refs in _entity_fact_sets(bundle.payload):
                for ref in refs:
                    observed = ref.collected_at or source_times.get(ref.source_key)
                    if observed is None:
                        continue
                    basis = "evidence.collected_at" if ref.collected_at else "source.accessed_at"
                    evidence_times[_evidence_id(entity_key, ref)] = (observed, basis)
    return evidence_times


def register_field(conn, apply: bool) -> None:
    print("=" * 70)
    print("任务 2：注册", FIELD_KEY)
    print("=" * 70)
    if conn.execute(sql("SELECT 1 FROM truth.field_definition WHERE field_key = :k"), {"k": FIELD_KEY}).first():
        print("  已存在，跳过")
        return

    template = conn.execute(sql(
        """SELECT applies_to_entity_type, indexed, cardinality, comparison_method, filterable, claimable
           FROM truth.field_definition WHERE field_key = 'cpu.architecture'"""
    )).mappings().first()
    if template is None:
        raise SystemExit("找不到用于对齐的模板字段 cpu.architecture")
    print(f"  对齐模板 cpu.architecture: {dict(template)}")

    values = {
        "id": stable_uuid("field:" + FIELD_KEY),
        "field_key": FIELD_KEY,
        "label": "集成显卡",
        "value_type": "string",
        "canonical_unit": None,
        "allowed_units": [],
        "description": "CPU 内置显示核心名称。NULL 表示未知，不等于无核显。",
        "applies_to_entity_type": template["applies_to_entity_type"],
        "cardinality": template["cardinality"],
        "comparison_method": template["comparison_method"],
        "storage_kind": "column",
        "storage_path": "cpu_spec.integrated_gpu",
        "indexed": template["indexed"],
        "filterable": template["filterable"],
        "claimable": template["claimable"],
        "active": True,
    }
    for key, value in values.items():
        print(f"    {key:<22} {value!r}")
    if not apply:
        print("  [dry-run] 未写入")
        return
    conn.execute(sql(
        """INSERT INTO truth.field_definition
           (id, field_key, label, value_type, canonical_unit, allowed_units, description,
            indexed, applies_to_entity_type, cardinality, comparison_method, tolerance,
            storage_kind, storage_path, filterable, claimable, active)
           VALUES (:id, :field_key, :label, :value_type, :canonical_unit,
                   CAST(:allowed_units AS jsonb), :description, :indexed, :applies_to_entity_type,
                   :cardinality, :comparison_method, NULL, :storage_kind, :storage_path,
                   :filterable, :claimable, :active)"""
    ), {**values, "allowed_units": "[]"})
    print("  已写入 field_definition")


def restore_collected_at(conn, apply: bool, releases_root: Path = RELEASES_ROOT) -> None:
    print()
    print("=" * 70)
    print("任务 3：按发布包与来源访问时间恢复 collected_at")
    print("=" * 70)
    release_times = release_evidence_times(releases_root)
    rows = conn.execute(sql("""
        SELECT ec.id, ec.collected_at, sd.accessed_at, sd.source_key
        FROM truth.evidence_claim ec
        JOIN truth.source_document sd ON sd.id = ec.source_id
        ORDER BY ec.id
    """)).mappings().all()

    updates: list[dict[str, Any]] = []
    unresolved: list[str] = []
    bases: Counter[str] = Counter()
    days: Counter[str] = Counter()
    for row in rows:
        restored = release_times.get(row["id"])
        if restored is None and row["accessed_at"] is not None:
            restored = (row["accessed_at"], "database source.accessed_at")
        if restored is None:
            unresolved.append(str(row["id"]))
            continue
        timestamp, basis = restored
        bases[basis] += 1
        days[timestamp.date().isoformat()] += 1
        if row["collected_at"] != timestamp:
            updates.append({"id": row["id"], "collected_at": timestamp})

    print(f"  Evidence 总数：{len(rows)}")
    print(f"  发布包可重放：{len(release_times)}；需更新：{len(updates)}；无法恢复：{len(unresolved)}")
    print("  恢复依据：", dict(sorted(bases.items())))
    print("  恢复后日期分布：", dict(sorted(days.items())))
    if unresolved:
        print("  无法恢复的 Evidence ID（前 10）：", ", ".join(unresolved[:10]))
    if not apply:
        print("  [dry-run] 未写入")
        return
    if updates:
        conn.execute(sql("""
            UPDATE truth.evidence_claim
            SET collected_at = :collected_at
            WHERE id = :id AND collected_at IS DISTINCT FROM :collected_at
        """), updates)
    after_null = conn.execute(sql(
        "SELECT count(*) FROM truth.evidence_claim WHERE collected_at IS NULL"
    )).scalar_one()
    print(f"  已恢复 {len(updates)} 行；剩余为空 {after_null}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply = not args.dry_run
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            register_field(conn, apply)
            restore_collected_at(conn, apply)
            if apply:
                transaction.commit()
                print("\n已提交")
            else:
                transaction.rollback()
                print("\n[dry-run] 已回滚")
        except Exception:
            transaction.rollback()
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
