"""Reset rows in the fixed V3 Truth table set, never schemas or app data."""

from __future__ import annotations

import argparse
import os
import sys

TRUTH_TABLES = (
    "benchmark_metric", "benchmark_subject", "benchmark_run", "benchmark_protocol", "metric_definition",
    "price_snapshot", "runtime_support_snapshot", "workload_requirement", "workload_profile", "workload",
    "evidence_claim", "source_document", "entity_attribute", "field_definition", "compatibility_edge",
    "laptop_gpu_spec", "datacenter_gpu_spec", "gpu_spec", "cpu_spec", "memory_spec", "storage_spec",
    "psu_spec", "platform_spec", "model_capability", "model_variant", "ai_model", "hardware", "organization",
    "entity_alias", "import_batch", "rule_definition", "dataset_release", "catalog_entity",
)
CONFIRMATION = "RESET_TRUTH_V3"


def sql() -> str:
    return "TRUNCATE TABLE " + ", ".join(f'truth."{name}"' for name in TRUTH_TABLES) + ";"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print SQL (default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    print(sql())
    if not args.apply: return 0
    if args.confirm != CONFIRMATION:
        print(f"Refused: --apply requires --confirm {CONFIRMATION}", file=sys.stderr); return 2
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from app.core.config import get_settings
            url = get_settings().database_url
        except Exception:
            print("DATABASE_URL is not configured", file=sys.stderr); return 2
    from sqlalchemy import create_engine, text
    with create_engine(url).begin() as connection: connection.execute(text(sql()))
    print("V3 Truth rows reset; schemas, views, public tables and Alembic history were preserved.")
    return 0


if __name__ == "__main__": raise SystemExit(main())
