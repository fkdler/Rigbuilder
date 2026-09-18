#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_extract.py —— 从已抓取快照中提取逐字证据，升级实体 evidence

做三件事（只加证据，不改事实值）：
  1) 找到每个已抓来源被哪些 Bundle 引用
  2) 按来源类型（AMD 官方页 / HuggingFace 模型卡 / Steam 游戏页）在快照文本中定位字段原文
  3) 为 Bundle 中【已填的字段】追加/更新 evidence：
       raw_excerpt = 页面逐字片段(snapshot 文本)
       normalized_value = Bundle 当前值（必须一致，否则记为冲突）
       review_status = pending（升级 accepted 由审核人决定）
  4) 输出报告：updated / no-match / conflict，冲突项绝不覆盖值

用法：
  python data/scripts/evidence_extract.py --report          # 只统计
  python data/scripts/evidence_extract.py --apply --kind amd
  python data/scripts/evidence_extract.py --apply --kind hf
  python data/scripts/evidence_extract.py --apply --kind game
  python data/scripts/evidence_extract.py --apply --all
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DRAFT = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03"
SRC = DRAFT / "sources"
SNAP = BASE / "data" / "raw" / "v3-pages"
RAW = BASE / "data" / "raw"
PLACEHOLDER = "2026-09-03T00:00:00Z"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def key_to_stem(key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", key)


def load_text(key: str):
    p = SNAP / (key_to_stem(key) + ".txt")
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def find_num_after(text: str, label: str, unit_map=None, window: int = 120):
    """在 label 之后 window 字符内找数字，返回 (数字, 逐字片段)"""
    i = text.find(label)
    if i < 0:
        return None, None
    seg = text[i:i + window]
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(MB|GB|GHz|MHz|W|GB/s|cores?|threads?)?", seg[len(label):])
    if not m:
        return None, seg.strip()[:100]
    val = float(m.group(1))
    unit = (m.group(2) or "").strip()
    return (val, unit, seg.strip()[:120]), seg.strip()[:120]


# ---------- AMD 官方页规则 ----------
def rules_amd(text, bundle):
    """返回 [(field_key, normalized_value, raw_excerpt)]"""
    out = []
    spec = bundle.get("payload", {}).get("cpu_spec") or bundle.get("payload", {}).get("gpu_spec") or {}
    p = bundle.get("payload", {})
    # L3 / L2 cache
    for label, field, key in (("L3 Cache", "l3_cache_mib", "l3_cache_mib"), ("L2 Cache", "l2_cache_mib", "l2_cache_mib")):
        r, excerpt = find_num_after(text, label)
        if r and spec.get(field) is not None and abs(r[0] - float(spec[field])) < 0.01 and r[1] == "MB":
            out.append((f"cpu_spec.{field}", spec[field], excerpt))
    # Default TDP
    r, excerpt = find_num_after(text, "Default TDP")
    if r and spec.get("base_power_w") is not None and abs(r[0] - float(spec["base_power_w"])) < 0.5:
        out.append(("cpu_spec.base_power_w", spec["base_power_w"], excerpt))
    # Max Boost Clock
    for label in ("Max. Boost Clock", "Max Boost Clock"):
        r, excerpt = find_num_after(text, label)
        if r and spec.get("boost_clock_mhz") is not None and r[1] == "GHz":
            if abs(r[0] * 1000 - float(spec["boost_clock_mhz"])) < 1:
                out.append(("cpu_spec.boost_clock_mhz", spec["boost_clock_mhz"], excerpt))
                break
    # Base Clock
    r, excerpt = find_num_after(text, "Base Clock")
    if r and spec.get("base_clock_mhz") is not None and r[1] == "GHz":
        if abs(r[0] * 1000 - float(spec["base_clock_mhz"])) < 1:
            out.append(("cpu_spec.base_clock_mhz", spec["base_clock_mhz"], excerpt))
    return out


# ---------- HuggingFace 模型卡规则（保守：只取页头元数据与本文档自身段落） ----------
def rules_hf(text, bundle):
    out = []
    p = bundle.get("payload", {})
    # License：页头元数据区（模型卡顶部 "License: xxx"），最可靠
    m = re.search(r"License:\s*([A-Za-z0-9.\-]+)", text)
    if m and p.get("license_name"):
        lic_page = m.group(1).lower().replace("-", "")
        lic_bundle = str(p["license_name"]).lower().replace("-", "")
        # 允许 Gemma/Llama 等自定义许可的宽松匹配（前 5 字符）
        if lic_page.startswith(lic_bundle[:5]) or lic_bundle.startswith(lic_page[:5]):
            out.append(("model.license_name", p["license_name"], m.group(0)))
    # 参数量/上下文不在此处提取：模型卡文本常含系列对比表，易张冠李戴；
    # 这两类字段请用 hf_fill.py --apply-params（读官方 config.json，权威且可核验）。
    return out


# ---------- AMD 官方页：空字段补全规则（label, 字段, 期望单位, 换算） ----------
AMD_FILL_RULES = [
    ("L2 Cache", "l2_cache_mib", "MB", 1.0),
    ("L3 Cache", "l3_cache_mib", "MB", 1.0),
    ("Default TDP", "base_power_w", "W", 1.0),
    ("Max. Boost Clock", "boost_clock_mhz", "GHz", 1000.0),
    ("Max Boost Clock", "boost_clock_mhz", "GHz", 1000.0),
    ("Base Clock", "base_clock_mhz", "GHz", 1000.0),
    ("Memory Channels", "memory_channels", None, 1.0),
    ("Max Memory Size", "max_memory_gib", "GB", 1.0),
]


def rules_amd_fill(text, bundle):
    """提取页面上有、而 Bundle 里为 null 的字段 -> [(field_key, value, excerpt)]"""
    spec = (bundle.get("payload") or {}).get("cpu_spec")
    if not spec:
        return []
    out = []
    for label, field, unit, mult in AMD_FILL_RULES:
        if spec.get(field) is not None:
            continue
        i = text.find(label)
        if i < 0:
            continue
        seg = text[i:i + 120].strip()
        after = text[i + len(label): i + len(label) + 60]
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(MB|GB|GHz|MHz|W)?", after)
        if not m:
            continue
        if unit and (m.group(2) or "") != unit:
            continue
        val = float(m.group(1)) * mult
        val = int(val) if val == int(val) else val
        out.append((f"cpu_spec.{field}", val, seg[:120]))
    return out


# ---------- NVIDIA 官方页规则（规格表多列，需按列对齐） ----------
NV_LABELS = [
    "AI TOPS", "NVIDIA CUDA Cores", "NVIDIA CUDA ® Cores", "Shader Cores",
    "Boost Clock (GHz)", "Boost Clock (MHz)", "Boost Clock",
    "Base Clock (GHz)", "Base Clock",
    "Memory Size", "Memory Type", "Standard Memory Configuration", "Standard Memory Config",
    "Memory Interface Width", "Memory Bandwidth",
    "Total Graphics Power (W)", "Required System Power (W)", "NVIDIA Architecture",
]


def _is_label(line: str) -> str:
    lab = line.replace(" ®", "").strip()
    for lb in NV_LABELS:
        if lab == lb.replace(" ®", "") or lab.startswith(lb.replace(" ®", "")):
            return lb
    return ""


def parse_nvidia_table(text):
    """返回 (列标题列表, {标签: [各列值]})；页面无规格表时返回 (None, None)
    定位方式：找"连续型号标题行，且其后紧跟规格字段标签"的位置（避免命中导航/产品卡片）。"""
    lines = [l.strip() for l in text.split("\n")]
    n_lines = len(lines)
    for i in range(n_lines):
        if not re.search(r"(GeForce\s+)?RTX\s*\d{3,4}", lines[i]) or _is_label(lines[i]):
            continue
        cols = []
        j = i
        while j < n_lines and re.search(r"(GeForce\s+)?RTX\s*\d{3,4}", lines[j]) and not _is_label(lines[j]):
            cols.append(lines[j])
            j += 1
        if not cols or j >= n_lines or not _is_label(lines[j]):
            continue
        n = len(cols)
        table = {}
        while j < n_lines:
            lab = _is_label(lines[j])
            if lab:
                table[lab] = lines[j + 1: j + 1 + n]
                j += 1 + n
            else:
                j += 1
        if table:
            return cols, table
    return None, None


def _nv_core(s: str):
    """提取 NVIDIA 型号核心：数字 + 后缀(ti/super 序列)；返回 (num, suffix_tuple) 或 None"""
    c = re.sub(r"[^a-z0-9]+", "", s.lower())
    m = re.search(r"rtx(\d{3,4})", c)
    if not m:
        return None
    num = m.group(1)
    rest = c[m.end():]
    sufs = []
    for _ in range(2):
        for suf in ("ti", "super"):
            if rest.startswith(suf):
                sufs.append(suf)
                rest = rest[len(suf):]
                break
        else:
            break
    return (num, tuple(sufs))


def _nv_cap(s: str):
    m = re.search(r"(\d+)\s*gb", re.sub(r"[^a-z0-9]+", "", s.lower()))
    return m.group(1) if m else None


def _nvidia_col_index(cols, name):
    """按型号核心(数字+Ti/Super 后缀)匹配列；名称含容量时优先容量一致的列，
    否则落到"无容量标注"的列（该列值可能是 '16 GB or 8 GB' 多容量写法）。
    注意：绝不能用前缀匹配，否则 'RTX 3060' 会命中 'RTX 3060 Ti'。"""
    tcore = _nv_core(name)
    tcap = _nv_cap(name)
    if not tcore:
        return None
    # 1) 核心 + 容量都匹配
    for idx, c in enumerate(cols):
        if _nv_core(c) == tcore and _nv_cap(c) == tcap:
            return idx
    # 2) 核心匹配且列未标容量（多容量列）
    for idx, c in enumerate(cols):
        if _nv_core(c) == tcore and _nv_cap(c) is None:
            return idx
    # 3) 目标未标容量：核心匹配即可
    if tcap is None:
        for idx, c in enumerate(cols):
            if _nv_core(c) == tcore:
                return idx
    return None


def rules_nvidia(text, bundle, fill_null=False):
    cols, table = parse_nvidia_table(text)
    if not cols:
        return []
    p = bundle.get("payload") or {}
    name = (p.get("identity") or {}).get("canonical_name") or ""
    idx = _nvidia_col_index(cols, name)
    if idx is None:
        return []
    gspec = p.get("gpu_spec") or {}
    lspec = p.get("laptop_gpu_spec") or {}
    out = []

    def val_of(label):
        vals = table.get(label)
        if not vals or idx >= len(vals):
            return None, None, None
        v = vals[idx]
        # 逐字行原文（完整，不截断），另附列定位说明
        full = label + " \n " + " \n ".join(vals)
        locator = f"NVIDIA 规格表｜列「{cols[idx]}」（第 {idx + 1}/{len(cols)} 列）"
        return v, full[:400], locator

    def add(field, value, label):
        v, ex, loc = val_of(label)
        if v is None:
            return False
        out.append((field, value, ex, loc))
        return True

    # 显存容量（处理 "16 GB or 8 GB" 多容量写法：只取与 Bundle 一致的那个）
    for lab in ("Memory Size", "Standard Memory Configuration", "Standard Memory Config"):
        v, ex, loc = val_of(lab)
        if v:
            caps = [int(x) for x in re.findall(r"([0-9]+)\s*GB", v)]
            if not caps:
                break
            cur = gspec.get("vram_gib")
            if cur is not None and cur in caps:
                out.append(("gpu.vram_gib", cur, ex, loc))
            elif cur is None and fill_null and len(caps) == 1:
                out.append(("gpu.vram_gib", caps[0], ex, loc))
            break
    # 显存类型
    v, ex, loc = val_of("Memory Type")
    if v:
        mt = "GDDR7" if "GDDR7" in v else ("GDDR6X" if "GDDR6X" in v else ("GDDR6" if "GDDR6" in v else None))
        if mt:
            if gspec.get("memory_type") is None and fill_null:
                out.append(("gpu.memory_type", mt, ex, loc))
            elif gspec.get("memory_type") == mt:
                out.append(("gpu.memory_type", mt, ex, loc))
    # 位宽
    v, ex, loc = val_of("Memory Interface Width")
    if v:
        m = re.search(r"([0-9]+)\s*-?bit", v)
        if m:
            want = int(m.group(1))
            if gspec.get("memory_bus_width_bit") is None and fill_null:
                out.append(("gpu.memory_bus_width_bit", want, ex, loc))
            elif gspec.get("memory_bus_width_bit") == want:
                out.append(("gpu.memory_bus_width_bit", want, ex, loc))
    # 带宽
    v, ex, loc = val_of("Memory Bandwidth")
    if v:
        m = re.search(r"([0-9]+)\s*GB/s", v)
        if m:
            want = int(m.group(1))
            if gspec.get("memory_bandwidth_gb_s") is None and fill_null:
                out.append(("gpu.memory_bandwidth_gb_s", want, ex, loc))
            elif gspec.get("memory_bandwidth_gb_s") == want:
                out.append(("gpu.memory_bandwidth_gb_s", want, ex, loc))
    # 整卡功耗
    v, ex, loc = val_of("Total Graphics Power (W)")
    if v:
        m = re.search(r"([0-9]+)", v)
        if m:
            want = int(m.group(1))
            if gspec.get("board_power_w") is None and fill_null:
                out.append(("gpu.board_power_w", want, ex, loc))
            elif gspec.get("board_power_w") == want:
                out.append(("gpu.board_power_w", want, ex, loc))
    # 笔记本加速频率区间（"1455 - 2040 MHz"）
    for lab in ("Boost Clock (MHz)", "Boost Clock"):
        v, ex, loc = val_of(lab)
        if v:
            m = re.search(r"([0-9]{3,4})\s*-\s*([0-9]{3,4})\s*MHz", v)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                if lspec.get("boost_clock_min_mhz") is None and fill_null:
                    out.append(("laptop_gpu.boost_clock_min_mhz", lo, ex, loc))
                elif lspec.get("boost_clock_min_mhz") == lo:
                    out.append(("laptop_gpu.boost_clock_min_mhz", lo, ex, loc))
                if lspec.get("boost_clock_max_mhz") is None and fill_null:
                    out.append(("laptop_gpu.boost_clock_max_mhz", hi, ex, loc))
                elif lspec.get("boost_clock_max_mhz") == hi:
                    out.append(("laptop_gpu.boost_clock_max_mhz", hi, ex, loc))
            break
    return out


# ---------- Intel 官方支持文章规则（Arc A 系列移动版规格，单行表格） ----------
def rules_intel(text, bundle, fill_null=False):
    """Intel 支持文章《Arc A-Series Mobile Graphics》规格表：
    列顺序 A350M/A370M/A550M/A730M/A770M，值以空格分隔。"""
    m = re.search(r"Intel Arc A-Series Mobile Graphics(.{0,2000}?)(?:Refer to|Related Products)", text, re.S)
    if not m:
        return []
    seg = re.sub(r"\s+", " ", m.group(1))
    cols = re.findall(r"A(\d{3})M", seg)
    if not cols:
        return []
    p = bundle.get("payload") or {}
    name = (p.get("identity") or {}).get("canonical_name") or ""
    mm = re.search(r"A(\d{3})M", name)
    if not mm or mm.group(1) not in cols:
        return []
    idx = cols.index(mm.group(1))
    n = len(cols)
    gspec = p.get("gpu_spec") or {}
    lspec = p.get("laptop_gpu_spec") or {}
    out = []
    loc = f"Intel 支持文章表格｜列「A{mm.group(1)}M」（第 {idx + 1}/{n} 列）"

    def grab(label, pattern):
        r = re.search(re.escape(label) + r"\s+((?:" + pattern + r"\s*){" + str(n) + r"})", seg)
        if not r:
            return None, None
        vals = re.findall(pattern, r.group(1))
        if len(vals) < n:
            return None, None
        return vals[idx], f"{label} " + " ".join(vals)

    def emit(field_key, value, excerpt):
        out.append((field_key, value, excerpt, loc))

    v, ex = grab("Memory (GDDR6)", r"(\d+GB)")
    if v and ex:
        want = int(re.search(r"(\d+)", v).group(1))
        if gspec.get("vram_gib") == want or (gspec.get("vram_gib") is None and fill_null):
            emit("gpu.vram_gib", want, ex)
    v, ex = grab("Memory Bus Width (GB/s)", r"(\d+-bit)")
    if v and ex:
        want = int(re.search(r"(\d+)", v).group(1))
        if gspec.get("memory_bus_width_bit") == want or (gspec.get("memory_bus_width_bit") is None and fill_null):
            emit("gpu.memory_bus_width_bit", want, ex)
    v, ex = grab("Graphics Power", r"(\d+-\d+W)")
    if v and ex:
        lo, hi = [int(x) for x in re.findall(r"\d+", v)]
        if lspec.get("tgp_min_w") == lo or (lspec.get("tgp_min_w") is None and fill_null):
            emit("laptop_gpu.tgp_min_w", lo, ex)
        if lspec.get("tgp_max_w") == hi or (lspec.get("tgp_max_w") is None and fill_null):
            emit("laptop_gpu.tgp_max_w", hi, ex)
    v, ex = grab("Graphics Clock (Mhz)", r"(\d+)")
    if v and ex:
        want = int(v)
        if lspec.get("boost_clock_max_mhz") == want or (lspec.get("boost_clock_max_mhz") is None and fill_null):
            out.append(("laptop_gpu.boost_clock_max_mhz", want, ex,
                        loc + "｜Intel 仅给出单一 Graphics Clock 值，记为 boost 上限"))
    return out


# ---------- Steam / 游戏需求页规则 ----------
def rules_game(text, bundle):
    out = []
    p = bundle.get("payload", {})
    i = text.find("System Requirements")
    if i < 0:
        i = text.find("系统需求")
    if i < 0:
        return out
    seg = text[i:i + 1600]
    for req in p.get("requirements", []):
        if req.get("operator") != "gte" or req.get("numeric_value") is None:
            continue
        role = (req.get("component_role") or "").upper()
        profile = req.get("profile_key") or "default"
        label = {"RAM": "Memory", "STORAGE": "Storage"}.get(role)  # 容量类最可靠
        if not label:
            continue
        j = seg.find(label)
        if j < 0:
            continue
        frag = seg[j:j + 120].strip()
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(GB|MB|TB)", frag)
        if not m:
            continue
        val = float(m.group(1))
        if m.group(2) == "TB":
            val *= 1024
        if abs(val - float(req["numeric_value"])) <= max(0.5, float(req["numeric_value"]) * 0.05):
            out.append((f"workload.requirement.{profile}.{role.lower()}", req["numeric_value"], frag[:120]))
    return out


RULES = {"amd": rules_amd, "hf": rules_hf, "game": rules_game}


def classify(key: str, host: str):
    if "amd.com" in host:
        return "amd"
    if "huggingface" in host:
        return "hf"
    if "nvidia.com" in host:
        return "nvidia"
    if "intel.com" in host:
        return "intel"
    if any(h in host for h in ("steampowered", "epicgames", "playvalorant", "riotgames", "heishenhua", "monsterhunter", "marvelrivals", "blizzard", "ea.com", "ubisoft")):
        return "game"
    return None


def load_bundles():
    out = {}
    for d in ("hardware", "models", "workloads"):
        for f in (DRAFT / d).glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            ek = (j.get("payload") or {}).get("identity", {}).get("entity_key")
            if ek:
                out[ek] = (f, j)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--kind", choices=["amd", "hf", "game", "nvidia", "intel"], default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--fill-null", action="store_true", help="用页面值补全 Bundle 中为 null 的字段（AMD 规格表）")
    args = ap.parse_args()

    # source_key -> (host, is_fetched)
    srcs = {}
    for f in SRC.glob("*.json"):
        try:
            p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
        except Exception:
            continue
        k = p.get("source_key")
        if not k:
            continue
        host = (p.get("url") or "").split("//")[-1].split("/")[0]
        fetched = bool(p.get("accessed_at") and p.get("accessed_at") != PLACEHOLDER)
        srcs[k] = (host, fetched, p.get("url"))

    # 引用关系：source_key -> [(bundle_file, bundle)]（同一 Bundle 只登记一次）
    bundles = load_bundles()
    refs = {}
    for ek, (f, j) in bundles.items():
        p = j.get("payload") or {}
        keys = set()
        for e in p.get("evidence") or []:
            if e.get("source_key"):
                keys.add(e["source_key"])
        for v in p.get("variants") or []:
            for e in v.get("evidence") or []:
                if e.get("source_key"):
                    keys.add(e["source_key"])
        for k in keys:
            refs.setdefault(k, []).append((f, j))

    stats = {"updated": 0, "no_match": 0, "conflict": 0, "no_snapshot": 0, "by_kind": {}}
    conflicts = []
    samples = []
    done_fields = set()   # (bundle 路径, field_key) 全局只处理一次
    for sk, (host, fetched, url) in sorted(srcs.items()):
        if not fetched:
            continue
        kind = classify(sk, host)
        if not kind:
            continue
        if args.kind and kind != args.kind:
            continue
        if not (args.all or args.kind):
            continue
        text = load_text(sk)
        if not text:
            stats["no_snapshot"] += 1
            continue
        targets = refs.get(sk) or []
        if not targets:
            continue
        stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
        for f, j in targets:
            if kind == "nvidia":
                found = rules_nvidia(text, j, fill_null=args.fill_null)
            elif kind == "intel":
                found = rules_intel(text, j, fill_null=args.fill_null)
            else:
                found = RULES[kind](text, j)
            # --fill-null：把页面上有、Bundle 为 null 的字段补上（仅 AMD 规则支持）
            if args.fill_null and kind == "amd":
                for field_key, value, excerpt in rules_amd_fill(text, j):
                    key = field_key.split(".", 1)[1]
                    j["payload"]["cpu_spec"][key] = value
                    found = found + [(field_key, value, excerpt)]
                    stats["filled_null"] = stats.get("filled_null", 0) + 1
            if not found:
                stats["no_match"] += 1
                continue
            p = j["payload"]
            evs = p.setdefault("evidence", [])
            seen_fields = set()
            SPEC_OF = {"cpu": "cpu_spec", "gpu": "gpu_spec", "laptop_gpu": "laptop_gpu_spec",
                       "memory": "memory_spec", "storage": "storage_spec", "psu": "psu_spec", "platform": "platform_spec"}
            for item in found:
                if len(item) == 4:
                    field_key, value, excerpt, locator = item
                else:
                    field_key, value, excerpt = item
                    locator = None
                if not excerpt or len(excerpt) < 3:
                    continue
                if field_key in seen_fields or (str(f), field_key) in done_fields:
                    continue
                seen_fields.add(field_key)
                done_fields.add((str(f), field_key))
                # --fill-null：页面有值而 Bundle 为 null 时写入
                parts = field_key.split(".")
                if args.fill_null and len(parts) == 2 and parts[0] in SPEC_OF:
                    spec = p.get(SPEC_OF[parts[0]])
                    if isinstance(spec, dict) and spec.get(parts[1]) is None:
                        spec[parts[1]] = value
                        stats["filled_null"] = stats.get("filled_null", 0) + 1
                existing = next((e for e in evs if e.get("field_key") == field_key), None)
                claim = {
                    "source_key": sk, "field_key": field_key, "normalized_value": value,
                    "provenance_key": "official", "raw_excerpt": excerpt,
                    "review_status": "pending", "reviewed_at": None,
                }
                if locator:
                    claim["notes"] = locator + "｜请核对：raw_excerpt 为页面该字段整行原文，本条目取其中该列的值"
                if existing:
                    if existing.get("normalized_value") != value:
                        stats["conflict"] += 1
                        conflicts.append({"source": sk, "bundle": str(f), "field": field_key,
                                          "bundle_value": existing.get("normalized_value"), "page_value": value})
                        continue
                    existing.update(claim)
                else:
                    evs.append(claim)
                stats["updated"] += 1
                if len(samples) < 60:
                    samples.append({"kind": kind, "source": sk, "bundle": f.name, "field": field_key,
                                    "value": value, "excerpt": excerpt})
            if args.apply:
                j["review_notes"] = (j.get("review_notes") or []) + [
                    f"{datetime.now(timezone.utc).date().isoformat()} evidence 提取：从快照 {key_to_stem(sk)}.txt 提取字段级逐字证据（pending，待审核）。"
                ]
                f.write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== 提取统计 ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if conflicts:
        out = RAW / "evidence-conflicts.json"
        out.write_text(json.dumps(conflicts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"冲突 {len(conflicts)} 条 -> {out}")
        for c in conflicts[:8]:
            print(f"  {c['field']}: bundle={c['bundle_value']} vs page={c['page_value']} ({Path(c['bundle']).name})")
    if not args.apply:
        print("\n=== 抽样（每类 6 条，人工核对 raw_excerpt 是否为页面逐字） ===")
        shown = {}
        for s in samples:
            k = s["kind"]
            if shown.get(k, 0) >= 6:
                continue
            shown[k] = shown.get(k, 0) + 1
            print(f"  [{k}] {s['bundle']} | {s['field']} = {s['value']}")
            print(f"        {s['excerpt'][:120]}")
        print("\n（未写入；加 --apply --kind amd|hf|game 或 --all 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
