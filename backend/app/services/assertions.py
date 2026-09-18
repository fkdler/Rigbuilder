"""Conservative surface-assertion guard, supplementing structured TruthVerifier.

This is not a general semantic parser. Unrestricted benchmark/comparison prose
is withheld; structured calculation assertions use verify_position instead.
"""
import re


def explanation_issue(text, facts):
    fields = {f["field_key"]: f for f in facts}
    if re.search(r"确保|保证|必然|不卡顿|畅玩|吊打|领先|优于|超过同|强于|性能排名|高端|旗舰|发布于|上市于|最新|刚发布|兼容性好|兼容性强|满足.*(?:多任务|办公).*需求", text, re.I):
        return "unsupported_comparison_time_or_guarantee"
    if re.search(r"流畅运行|足够.*算力|降低.*(?:噪音|噪声|电力消耗)|减少.*(?:噪音|噪声)", text):
        return "unmeasured_workload_or_system_behavior"
    if re.search(r"(?:含有|带有|自带|配备|内置|拥有|集成|使用).*核显|无需.*独显|不需要.*独显|直接.*显示器", text):
        if "cpu.integrated_gpu" not in fields:
            return "integrated_graphics_unverified"
    # Unit-bearing numbers must be tied to the correct field, not just occur
    # somewhere in the brief (24 cores is not evidence of 24W or 24GiB).
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(GiB|GB|W|瓦|核心|核|线程|threads?|cores?)", text, re.I):
        value, unit = float(match.group(1)), match.group(2).casefold()
        allowed = ("gpu.vram_gib",) if unit in {"gb", "gib"} else (
            ("cpu.cores_total",) if unit in {"核", "核心", "core", "cores"} else (
                ("cpu.threads",) if unit in {"线程", "thread", "threads"} else ("cpu.base_power_w", "gpu.board_power_w")))
        valid = False
        for field in allowed:
            try:
                valid |= field in fields and float(fields[field]["value"]) == value
            except (TypeError, ValueError):
                pass
        if not valid:
            return "property_value_mismatch"
    return None
