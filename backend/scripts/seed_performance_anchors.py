"""Seed ``truth.performance_anchor`` -- the shared relative-performance scale.

Why anchors exist
-----------------
Comparing "how good is this CPU" with "how good is this GPU" needs both on one
axis.  Neither percentile available from the data works:

* against our own catalogue, the scale reflects catalogue composition (a catalogue
  full of high-end parts puts an i5-12400 at the bottom);
* against the publisher's population, it reflects *their* composition (thousands
  of integrated and decades-old GPUs push every discrete card into "flagship").

So the axis is anchored by hand instead: a handful of well-known parts per category
are placed at a chosen index, and every other score is linearly interpolated
between its two neighbouring anchors.  The anchors are what encode "what entry,
mainstream and flagship mean today" -- the exact judgement a model frozen at its
training cutoff cannot make and should not be asked to make.

These are **product policy, not measurements**: the indices are judgements, the
interpolation is arithmetic.  Versioned so a revision is visible, and never
evidence-backed.

Usage::

    python backend/scripts/seed_performance_anchors.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql  # noqa: E402

from app.data_contracts.v3 import stable_uuid  # noqa: E402
from app.db.session import engine  # noqa: E402

VERSION = "1"

# (category, passmark score, anchored index, rationale)
# The 0 and top rows are sentinels: they guarantee that every observed score falls
# inside exactly one band, so the view never has to guess at the ends.
ANCHORS = [
    ("cpu", 0, 0, "下限锚点：保证任何分数都落在某个区间内。"),
    ("cpu", 6000, 10, "低端双核/老平台档位。"),
    ("cpu", 12513, 26, "Core i3-12100 所在的入门桌面四核档位。"),
    ("cpu", 18820, 40, "Core i5-12400 所在的主流办公与入门游戏档位。"),
    ("cpu", 26967, 55, "Ryzen 5 7600 所在的主流游戏档位。"),
    ("cpu", 34000, 65, "Core i5-14600K 级：主流偏上的游戏档位。"),
    ("cpu", 51926, 78, "Core i7-14700K 级：高端游戏与生产力档位。"),
    ("cpu", 58226, 86, "Core i9-14900K 级：旗舰游戏档位。"),
    ("cpu", 70099, 97, "Ryzen 9 9950X3D：当前桌面多线程天花板。"),
    ("cpu", 200000, 100, "上限锚点：高于当前所有已收录型号。"),

    ("gpu", 0, 0, "下限锚点：保证任何分数都落在某个区间内。"),
    ("gpu", 2663, 6, "核显级（如 Radeon 660M），不具备独立游戏能力。"),
    ("gpu", 6000, 18, "入门独立显卡下限。"),
    ("gpu", 10738, 30, "GeForce RTX 3050 6GB：当代入门独显档位。"),
    ("gpu", 19488, 48, "GeForce RTX 4060：主流 1080p 档位。"),
    ("gpu", 29945, 66, "GeForce RTX 4070 SUPER：主流 1440p 档位。"),
    ("gpu", 34427, 78, "GeForce RTX 4080：高端档位。"),
    ("gpu", 39003, 97, "GeForce RTX 5090：当前消费级天花板。"),
    ("gpu", 100000, 100, "上限锚点：高于当前所有已收录型号。"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    rows = [{
        "id": stable_uuid(f"performance-anchor:{category}:{VERSION}:{score}"),
        "category": category, "score": score, "anchored_index": index,
        "rationale": rationale, "version": VERSION, "active": True, "created_at": now,
    } for category, score, index, rationale in ANCHORS]

    for row in rows:
        print(f"  {row['category']:<4} score={row['score']:>7}  →  index {row['anchored_index']:>5}")

    if args.dry_run:
        print(f"\n[dry-run] 未写入；共 {len(rows)} 个锚点")
        return

    inserted = updated = 0
    with engine.begin() as conn:
        for row in rows:
            exists = conn.execute(sql("""
                SELECT 1 FROM truth.performance_anchor WHERE category=:category AND version=:version AND score=:score
            """), {"category": row["category"], "version": row["version"], "score": row["score"]}).first()
            params = {k: row[k] for k in ("category", "score", "anchored_index", "rationale", "version", "active")}
            if exists:
                conn.execute(sql("""
                    UPDATE truth.performance_anchor SET anchored_index=:anchored_index, rationale=:rationale, active=:active
                    WHERE category=:category AND version=:version AND score=:score
                """), params)
                updated += 1
            else:
                conn.execute(sql("""
                    INSERT INTO truth.performance_anchor
                        (id, category, score, anchored_index, rationale, version, active, created_at)
                    VALUES (:id, :category, :score, :anchored_index, :rationale, :version, :active, :created_at)
                """), {**params, "id": row["id"], "created_at": row["created_at"]})
                inserted += 1
    print(f"\n已写入 truth.performance_anchor：新增 {inserted}，更新 {updated}")


if __name__ == "__main__":
    main()
