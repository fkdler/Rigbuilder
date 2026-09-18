"""Remove one release's imported rows so the same package can be applied again.

Importer rows are append-only, which is what protects a measurement from being
silently rewritten -- but it also means a package that was written with a wrong
shape cannot simply be re-applied: the second run finds the run key already
present and skips it, leaving the bad rows in place.  Reverting a release means
removing exactly the rows it created, in foreign-key order, and dropping its
``dataset_release`` / ``import_batch`` bookkeeping so the key is free again.

Scoped by ``release_id``, so nothing outside the named release is touched.  This
is a write operation: it prints what it will delete and needs ``--apply``.

Usage::

    python backend/scripts/rollback_release.py --release-key rigbuilder-v3-9-... --dry-run
    python backend/scripts/rollback_release.py --release-key rigbuilder-v3-9-... --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql  # noqa: E402

from app.db.session import engine  # noqa: E402

# Child rows first: benchmark_metric and benchmark_subject point at benchmark_run,
# which points at dataset_release, so the delete order is the reverse of import.
DELETE_ORDER = [
    ("benchmark_metric", "run_id IN (SELECT id FROM truth.benchmark_run WHERE release_id = :rid)"),
    ("benchmark_subject", "run_id IN (SELECT id FROM truth.benchmark_run WHERE release_id = :rid)"),
    ("benchmark_run", "release_id = :rid"),
    ("import_batch", "release_id = :rid"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-key", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not (args.apply or args.dry_run):
        raise SystemExit("需要 --apply 或 --dry-run")

    with engine.begin() as conn:
        release_id = conn.execute(
            sql("SELECT id FROM truth.dataset_release WHERE release_key = :k"),
            {"k": args.release_key}).scalar_one_or_none()
        if release_id is None:
            raise SystemExit(f"未找到发布包：{args.release_key}")

        print(f"发布包 {args.release_key} (id={release_id})")
        for table, condition in DELETE_ORDER:
            count = conn.execute(
                sql(f"SELECT count(*) FROM truth.{table} WHERE {condition}"),
                {"rid": release_id}).scalar_one()
            print(f"   truth.{table:<20} 待删除 {count}")
            if args.apply and count:
                conn.execute(sql(f"DELETE FROM truth.{table} WHERE {condition}"), {"rid": release_id})

        if args.apply:
            conn.execute(sql("DELETE FROM truth.dataset_release WHERE id = :rid"), {"rid": release_id})
            print("   truth.dataset_release  已删除")
        else:
            print("   truth.dataset_release  待删除（--dry-run 未执行）")

    print("\n" + ("已回滚" if args.apply else "预演完成，未改动数据"))


if __name__ == "__main__":
    main()
