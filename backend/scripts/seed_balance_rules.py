"""Seed ``truth.balance_rule`` -- the pairing policy, as data.

Why this is a table and not prompt text
---------------------------------------
"Is this CPU/GPU pair lopsided?" is a deterministic question once both parts sit on
one scale.  ``performance_ranking.index_100`` supplies that scale as versioned
product policy: hand-selected CPU/GPU anchors with linear interpolation.  It is
not a publisher percentile or a percentile inside this catalogue.  CPU/GPU values
are comparable only for applying the balance policies defined below; that ratio
is not an empirical cross-category benchmark.
The remaining judgement -- how much GPU a given use case wants relative to CPU --
is *product policy*.  Written here it is versioned, auditable and testable; left
to a 4B model it is re-guessed on every request, which is exactly how an office
build ended up with a discrete GPU and a gaming build with none.

Honesty about what these numbers are
------------------------------------
The bands below are the project's own starting policy, chosen so that each use case
gets a *direction* and a *tolerance* rather than a single magic ratio.  They are
not measured thresholds, not vendor guidance, and not derived from the benchmark
data -- they are calibrated by hand and are expected to be tuned once evaluation
runs can compare them against outcomes.  That is why every row carries its
rationale and a version, and why the view exposes them as policy values.

Usage::

    python backend/scripts/seed_balance_rules.py [--dry-run]
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
CPU_METRIC = "passmark.cpu.mark"
GPU_METRIC = "passmark.gpu.g3d_mark"

# rule_key, use_case, ratio_min, ratio_max, cpu_index_min, gpu_index_min,
# gpu_requirement, priority, rationale
RULES = [
    (
        "office-integrated", "office", None, None, None, None,
        "integrated_ok", "balanced",
        "普通办公不构成搭配问题：显示由核显承担，是否够用取决于 cpu.integrated_gpu 而非显卡性能。"
        "因此不设 GPU/CPU 比例带，也不设最低显卡档位。"
        "CPU 端的最低档位由 office-fit 政策（功耗与核心数）决定，这里不重复。",
    ),
    (
        "general-productivity", "general", 0.3, 2.0, 25.0, None,
        "preferred", "balanced",
        "日常多任务、轻度内容消费：独立显卡可有可无，有则不应显著弱于 CPU，"
        "否则独显的存在没有意义。下界 0.3 只排除明显失衡的搭配；上界 1.6 避免为用不到的显卡性能付费。",
    ),
    (
        "gaming-1080p", "gaming-1080p", 0.8, 2.0, 40.0, 40.0,
        "required", "balanced",
        "1080p 主流游戏：CPU 与 GPU 都会成为瓶颈，两者档位接近时整机最均衡。"
        "带宽 0.8–1.6 表示 GPU 档位可以略强于 CPU（多数 1080p 场景 GPU 先到瓶颈），"
        "但不接受 CPU 明显拖后腿（下界）。",
    ),
    (
        "gaming-1440p", "gaming-1440p", 1.0, 2.6, 45.0, 55.0,
        "required", "gpu_bound",
        "1440p 高画质：分辨率上升后 GPU 负担上升更快，GPU 档位应高于 CPU。"
        "带宽下界 1.0 表示不接受 GPU 弱于 CPU；上界 2.0 以上通常意味着 CPU 已经被浪费。",
    ),
    (
        "gaming-4k", "gaming-4k", 1.0, 3.2, 45.0, 70.0,
        "required", "gpu_bound",
        "4K 高画质：几乎完全由 GPU 决定帧数，CPU 只要不成为硬瓶颈。"
        "GPU 档位应显著高于 CPU，故带宽整体上移；此时把钱花在 CPU 上通常是错配。",
    ),
    (
        "esports-highrefresh", "esports", 0.6, 1.5, 55.0, 40.0,
        "required", "cpu_bound",
        "高帧率竞技类：帧数上限常由 CPU 与内存延迟决定，GPU 反而先够用。"
        "这是唯一允许 GPU 档位低于 CPU 的场景（下界 0.6），但 CPU 必须处在较高档位。",
    ),
    (
        "local-llm", "local-llm", None, None, 20.0, 55.0,
        "required", "gpu_bound",
        "本地大模型推理：约束是显存容量与带宽，不是 CPU 性能——CPU 仅影响卸载部分。"
        "因此不设 GPU/CPU 比例带（比例在这里是没有意义的指标），"
        "改用 GPU 最低档位与显存要求表达；模型侧的显存是否够用由 model_variant 与 GPU 显存另行判定。",
    ),
    (
        "rendering-3d", "rendering", 0.7, 2.2, 50.0, 50.0,
        "required", "balanced",
        "渲染与 3D 制作：CPU 参与场景构建与部分渲染器，GPU 参与预览与实际渲染，两者都吃。"
        "带宽居中，避免任一方向明显短腿。",
    ),
    (
        "video-encoding", "video", 0.5, 2.0, 45.0, 35.0,
        "preferred", "balanced",
        "视频剪辑与转码：硬件编码器让中低档显卡也很有价值，故 GPU 下界较低；"
        "但时间线预览与多轨合成仍吃 CPU，故要求 CPU 档位不低于中等。",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    rows = []
    for rule_key, use_case, ratio_min, ratio_max, cpu_min, gpu_min, requirement, priority, rationale in RULES:
        rows.append({
            "id": stable_uuid(f"balance-rule:{rule_key}:{VERSION}"),
            "rule_key": rule_key, "use_case": use_case, "version": VERSION,
            "cpu_metric_key": CPU_METRIC, "gpu_metric_key": GPU_METRIC,
            "ratio_min": ratio_min, "ratio_max": ratio_max,
            "cpu_index_min": cpu_min, "gpu_index_min": gpu_min,
            "gpu_requirement": requirement, "priority": priority,
            "rationale": rationale, "active": True, "created_at": now,
        })

    for row in rows:
        band = "—" if row["ratio_min"] is None else f"{row['ratio_min']}–{row['ratio_max']}"
        print(f"  {row['rule_key']:<22} {row['use_case']:<14} 带宽={band:<10} "
              f"cpu>={row['cpu_index_min'] or '—':<5} gpu>={row['gpu_index_min'] or '—':<5} "
              f"{row['gpu_requirement']:<14} {row['priority']}")

    if args.dry_run:
        print(f"\n[dry-run] 未写入；共 {len(rows)} 条规则")
        return

    inserted = updated = 0
    with engine.begin() as conn:
        for row in rows:
            exists = conn.execute(sql("SELECT 1 FROM truth.balance_rule WHERE rule_key=:k AND version=:v"),
                                  {"k": row["rule_key"], "v": row["version"]}).first()
            keys = ("rule_key", "use_case", "version", "cpu_metric_key", "gpu_metric_key",
                    "ratio_min", "ratio_max", "cpu_index_min", "gpu_index_min",
                    "gpu_requirement", "priority", "rationale", "active")
            params = {key: row[key] for key in keys}
            if exists:
                conn.execute(sql("""
                    UPDATE truth.balance_rule SET use_case=:use_case, cpu_metric_key=:cpu_metric_key,
                        gpu_metric_key=:gpu_metric_key, ratio_min=:ratio_min, ratio_max=:ratio_max,
                        cpu_index_min=:cpu_index_min, gpu_index_min=:gpu_index_min,
                        gpu_requirement=:gpu_requirement, priority=:priority,
                        rationale=:rationale, active=:active
                    WHERE rule_key=:rule_key AND version=:version"""), params)
                updated += 1
            else:
                conn.execute(sql("""
                    INSERT INTO truth.balance_rule
                        (id, rule_key, use_case, version, cpu_metric_key, gpu_metric_key,
                         ratio_min, ratio_max, cpu_index_min, gpu_index_min,
                         gpu_requirement, priority, rationale, active, created_at)
                    VALUES (:id, :rule_key, :use_case, :version, :cpu_metric_key, :gpu_metric_key,
                            :ratio_min, :ratio_max, :cpu_index_min, :gpu_index_min,
                            :gpu_requirement, :priority, :rationale, :active, :created_at)"""),
                             {**params, "id": row["id"], "created_at": row["created_at"]})
                inserted += 1
    print(f"\n已写入 truth.balance_rule：新增 {inserted}，更新 {updated}")


if __name__ == "__main__":
    main()
