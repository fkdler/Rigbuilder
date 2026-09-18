"""Remove catalogue entries for parts that were never released.

Why a script rather than a one-off DELETE
-----------------------------------------
These rows were not wrong *values*, they were wrong *existence*: the SKUs were
added from a bulk source and never existed as shipping products.  The signal that
exposed them is indirect but strong -- PassMark, whose CPU list runs to 6006
graded parts, has no entry for any of them, and they were also the four CPUs in
the v3-5 backlog that carried **no evidence at all** (they were demoted to
not-recommendable rather than imported with a fabricated spec).

Confirmed non-existent by the user on 2026-09-16.  Only rows this script names are
touched, and it refuses to delete anything that has accumulated evidence, aliases,
attributes, benchmark results or references -- if one of these turns out to be real
after all, the guard stops the delete instead of silently discarding data.

Usage::

    python backend/scripts/remove_nonexistent_parts.py --dry-run
    python backend/scripts/remove_nonexistent_parts.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql  # noqa: E402

from app.db.session import engine  # noqa: E402

# (canonical_name, entity_key, why)
REMOVALS = [
    ("Ryzen 7 7800X", "hw:amd:ryzen-7-7800x:desktop:global",
     "AMD 未发布该型号（有 7800X3D，无 7800X）"),
    ("Ryzen 7 9700", "hw:amd:ryzen-7-9700:desktop:global",
     "AMD 未发布该型号（有 9700X，无 9700）"),
    ("Ryzen 7 9800X", "hw:amd:ryzen-7-9800x:desktop:global",
     "AMD 未发布该型号（有 9800X3D，无 9800X）"),
    ("Ryzen 9 9900", "hw:amd:ryzen-9-9900:desktop:global",
     "AMD 未发布该型号（有 9900X / 9900X3D，无 9900）"),
]

# Any of these being non-zero means the entity is referenced and must not be
# deleted blindly.
GUARDS = [
    ("truth.evidence_claim", "entity_id = ANY(:ids)"),
    ("truth.entity_alias", "entity_id = ANY(:ids)"),
    ("truth.entity_attribute", "entity_id = ANY(:ids)"),
    ("truth.benchmark_subject", "entity_id = ANY(:ids)"),
    ("truth.compatibility_edge", "source_entity_id = ANY(:ids) OR target_entity_id = ANY(:ids)"),
    ("truth.price_snapshot", "entity_id = ANY(:ids)"),
    ("truth.workload_requirement", "reference_entity_id = ANY(:ids)"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (args.apply or args.dry_run):
        raise SystemExit("需要 --apply 或 --dry-run")

    keys = [key for _, key, _ in REMOVALS]
    with engine.begin() as conn:
        found = conn.execute(sql("""
            SELECT e.id::text, e.entity_key, e.canonical_name, e.recommendable
            FROM truth.catalog_entity e WHERE e.entity_key = ANY(:keys)"""), {"keys": keys}).all()
        if not found:
            print("未找到任何目标实体，无事可做")
            return
        ids = [row[0] for row in found]
        by_key = {row[1]: row for row in found}

        print("待移除：")
        for name, key, why in REMOVALS:
            row = by_key.get(key)
            mark = "找到" if row else "**库中不存在**"
            print(f"   {name:<16} {mark:<14} {why}")

        print("\n引用守卫（必须全为 0）：")
        blocked = False
        for table, condition in GUARDS:
            count = conn.execute(sql(f"SELECT count(*) FROM {table} WHERE {condition}"),
                                  {"ids": ids}).scalar_one()
            print(f"   {table:<30} {count}")
            blocked = blocked or count > 0
        if blocked:
            raise SystemExit("有实体仍被引用，拒绝删除；请先人工确认这些引用。")

        spec_count = conn.execute(sql("SELECT count(*) FROM truth.cpu_spec WHERE hardware_id = ANY(:ids)"),
                                   {"ids": ids}).scalar_one()
        hw_count = conn.execute(sql("SELECT count(*) FROM truth.hardware WHERE entity_id = ANY(:ids)"),
                                {"ids": ids}).scalar_one()
        print(f"\n将删除：cpu_spec {spec_count} 行、hardware {hw_count} 行、catalog_entity {len(ids)} 行")
        if not args.apply:
            print("\n[dry-run] 未改动数据")
            return

        conn.execute(sql("DELETE FROM truth.cpu_spec WHERE hardware_id = ANY(:ids)"), {"ids": ids})
        conn.execute(sql("DELETE FROM truth.hardware WHERE entity_id = ANY(:ids)"), {"ids": ids})
        conn.execute(sql("DELETE FROM truth.catalog_entity WHERE id = ANY(:ids)"), {"ids": ids})

    with engine.connect() as conn:
        left = conn.execute(sql("SELECT count(*) FROM truth.catalog_entity WHERE entity_key = ANY(:keys)"),
                            {"keys": keys}).scalar_one()
        total = conn.execute(sql("SELECT count(*) FROM truth.catalog_entity")).scalar_one()
    print(f"\n已移除 {len(ids)} 个实体（剩余同名实体 {left}）。catalog_entity 现共 {total} 行。")


if __name__ == "__main__":
    main()
