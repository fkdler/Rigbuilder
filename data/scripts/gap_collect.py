#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gap_collect.py —— 缺口自动收集（与 page_save.py 配套）

做什么：
  1) scan：遍历 drafts/hardware/*.json，找出"官方来源缺失 + spec 字段空缺"的实体
     （默认只含本批"后缀矩阵补全/移动 GPU 后缀补全"标记的 39 个新文件；--all 放开全目录）
  2) 对每个实体产出候选官方 URL（AMD 模板已验证 / NVIDIA family·laptops 页 / Intel 因 ARK
     sku id 无法推导 → 标 needs-manual），检查 sources/ 是否已有同 URL
  3) --write-sources：把 URL 可确定且 sources 中尚不存在的来源写入
     sources/NNNN.json（沿用 0008.json 编号格式，从 111 起），pending、
     accessed_at=占位、content_hash=null → 之后跑 page_save.py --only-missing 抓取
  4) 输出 data/raw/gap-collect-plan-<date>.json（机器可读）与控制台摘要

用法（先本地）：
  python data/scripts/gap_collect.py --scan
  python data/scripts/gap_collect.py --scan --write-sources
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
HW = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03" / "hardware"
SRC = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03" / "sources"
RAW = BASE / "data" / "raw"
PLACEHOLDER = "2026-09-03T00:00:00Z"

SPEC_KEYS = {
    "cpu_spec": ["architecture", "codename", "cores_total", "threads", "performance_cores",
                 "efficiency_cores", "base_clock_mhz", "boost_clock_mhz", "l2_cache_mib",
                 "l3_cache_mib", "base_power_w", "max_power_w", "socket", "memory_channels",
                 "max_memory_gib", "ecc_support", "pcie_generation", "pcie_lanes",
                 "integrated_gpu", "npu_model"],
    "gpu_spec": ["architecture", "vram_gib", "memory_type", "memory_bus_width_bit",
                 "memory_bandwidth_gb_s", "ecc_support", "interface", "pcie_generation",
                 "pcie_lanes", "board_power_w"],
    "laptop_gpu_spec": ["tgp_min_w", "tgp_max_w", "boost_clock_min_mhz",
                        "boost_clock_max_mhz", "dynamic_boost_w"],
}

BATCH_MARK = ("后缀矩阵补全", "移动 GPU 后缀补全")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def amd_cpu_url(canonical: str) -> str:
    c = re.sub(r"^(amd|ryzen)\s+", "", canonical, flags=re.I)  # "Ryzen 7 5700X3D"
    slug = norm(c)                       # ryzen-7-5700x3d
    num = re.search(r"(\d{4})", slug)
    n = int(num.group(1)) if num else 0
    if n >= 9000:
        series = "9000-series"
    elif n >= 8000:
        series = "8000-series"           # 8600G / 8700G（AMD 官网段名 8000-series）
    elif n >= 7000:
        series = "7000-series"           # 7500F ~ 7950X3D
    else:
        series = "5000-series"           # 5600/5700/5800/5900/5950
    rest = re.sub(r"^ryzen-", "", slug)  # 7-5700x3d
    return f"https://www.amd.com/en/products/processors/desktops/ryzen/{series}/amd-ryzen-{rest}.html"


def amd_gpu_url(canonical: str) -> str:
    c = re.sub(r"^amd\s+", "", canonical, flags=re.I)
    c = re.sub(r"\s+\d+gb$", "", c, flags=re.I)  # 容量变体归一到官方同页
    slug = norm(c)                       # radeon-rx-7900-gre
    m = re.search(r"rx-(\d{4})", slug)
    series = {"6000": "6000-series", "7000": "7000-series", "9000": "9000-series"}.get(
        m.group(1) if m else "", "7000-series")
    return f"https://www.amd.com/en/products/graphics/desktops/radeon/{series}/amd-{slug}.html"


def nvidia_laptop_url(canonical: str) -> str | None:
    m = re.search(r"rtx-(\d{2})", norm(canonical))
    gen = m.group(1) if m else None
    return {
        "30": "https://www.nvidia.com/en-us/geforce/laptops/30-series/",
        "40": "https://www.nvidia.com/en-us/geforce/laptops/40-series/",
        "50": "https://www.nvidia.com/en-us/geforce/laptops/50-series/",
    }.get(gen)


def suggest_url(p: dict) -> dict | None:
    """返回 {url, key, title, mode:'auto'|'candidate'} 或 None（needs-manual）"""
    cat = p.get("category")
    ident = p.get("identity") or {}
    name = (p.get("canonical_name") or ident.get("canonical_name") or "").strip()
    ff = p.get("form_factor")
    n = norm(name)
    if cat == "cpu" and (n.startswith("amd-ryzen") or "ryzen" in n):
        url = amd_cpu_url(name)
        c = re.sub(r"^(amd|ryzen)\s+", "", name, flags=re.I)
        rest = re.sub(r"^ryzen-", "", norm(c))
        return {"url": url, "mode": "auto",
                "key": f"src:amd:cpu-amd-ryzen-{rest}",
                "title": f"AMD {c} Desktop Processor"}
    if cat == "gpu" and "radeon" in n:
        url = amd_gpu_url(name)
        c = re.sub(r"^amd\s+", "", name, flags=re.I)
        c = re.sub(r"\s+\d+gb$", "", c, flags=re.I)
        slug = norm(c).replace("radeon-", "")
        return {"url": url, "mode": "auto",
                "key": f"src:amd:gpu-{slug}",
                "title": f"{name} - AMD"}
    if cat == "gpu" and "geforce" in n:
        if ff == "laptop":
            url = nvidia_laptop_url(name)
            if not url:
                return None
            m = re.search(r"rtx-(\d{2})", n)
            return {"url": url, "mode": "candidate",
                    "key": f"src:nvidia:gpu-laptop-{m.group(1)}-series",
                    "title": f"GeForce RTX {m.group(1)} Series Laptops - NVIDIA"}
        if re.search(r"rtx-5060", n):  # 5060 / 5060 Ti 8GB / 16GB 同 family 页
            return {"url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5060-family/",
                    "mode": "candidate", "key": "src:nvidia:gpu-family-5060-5060ti",
                    "title": "GeForce RTX 5060 Family - NVIDIA"}
        # 其余 NVIDIA 桌面（3050 6GB 等）无独立官方规格页 → needs-manual
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="扫描并输出缺口 plan")
    ap.add_argument("--all", action="store_true", help="扫全部 hardware（默认只本批 39 新文件）")
    ap.add_argument("--write-sources", action="store_true", help="把 URL 可确定的缺失来源写入 sources/")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not args.scan:
        ap.error("需要 --scan")

    RAW.mkdir(exist_ok=True)
    existing_urls = set()
    if SRC.exists():
        for f in SRC.glob("*.json"):
            try:
                p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
                if p.get("url"):
                    existing_urls.add(p["url"])
            except Exception:
                pass

    plan = []
    files = sorted(HW.glob("*.json"))
    idx = 0
    for f in files:
        b = json.loads(f.read_text(encoding="utf-8"))
        p = (b.get("payload") or {})
        notes = " ".join(b.get("review_notes") or [])
        if not args.all and not any(mk in notes for mk in BATCH_MARK):
            continue
        idx += 1
        if args.limit and idx > args.limit:
            break
        name = (p.get("canonical_name") or (p.get("identity") or {}).get("canonical_name") or f.stem)
        # 空缺字段
        missing = []
        for sub, keys in SPEC_KEYS.items():
            spec = p.get(sub)
            if not spec:
                continue
            for k in keys:
                if k in spec and spec[k] is None:
                    missing.append(f"{sub}.{k}")
        if p.get("official_product_id") is None:
            missing.append("official_product_id")
        sug = suggest_url(p)
        row = {
            "file": f.name,
            "entity_key": (p.get("identity") or {}).get("entity_key") or p.get("entity_key"),
            "canonical_name": name,
            "category": p.get("category"),
            "form_factor": p.get("form_factor"),
            "missing_fields": missing,
            "missing_count": len(missing),
            "url_mode": (sug or {}).get("mode", "needs-manual"),
            "suggested_url": (sug or {}).get("url"),
            "suggested_source_key": (sug or {}).get("key"),
            "suggested_title": (sug or {}).get("title"),
            "url_already_in_sources": bool(sug and sug["url"] in existing_urls),
        }
        plan.append(row)

    today = date.today().isoformat()
    out = RAW / f"gap-collect-plan-{today}.json"
    out.write_text(json.dumps({"generated": today, "count": len(plan), "plan": plan},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"plan: {len(plan)} entities -> {out}")

    # 写来源（URL 可确定 + 尚未注册 + 已有 source_key 前缀规范）
    written = skipped_existing = 0
    if args.write_sources:
        next_num = 111
        nums = [int(x.stem) for x in SRC.glob("*.json") if x.stem.isdigit()]
        if nums:
            next_num = max(nums) + 1
        done = set()
        for r in plan:
            if not r["suggested_url"] or r["url_already_in_sources"]:
                continue
            key = r["suggested_source_key"]
            if key in done:
                continue
            # 防止未来重跑产生重复（用 url 兜底判断）
            if any(key == (json.loads(x.read_text(encoding="utf-8")).get("payload") or {}).get("source_key")
                   for x in SRC.glob("*.json")):
                skipped_existing += 1
                continue
            done.add(key)
            fname = SRC / f"{next_num:04d}.json"
            bundle = {
                "schema_version": "3.2",
                "record_type": "source_document",
                "status": "pending",
                "payload": {
                    "source_key": key,
                    "title": r["suggested_title"],
                    "url": r["suggested_url"],
                    "publisher_key": "org:" + r["suggested_url"].split("//")[1].split(".")[0],
                    "source_type": "official_page",
                    "source_tier": "A",
                    "published_at": None,
                    "market_region": None,
                    "language": None,
                    "availability_status": None,
                    "archive_url": None,
                    "accessed_at": PLACEHOLDER,
                    "content_hash": None,
                },
                "review_notes": [
                    f"gap_collect 自动注册（{today}）：URL 为规则模板{'/家族页候选' if r['url_mode']=='candidate' else ''}，"
                    "待 page_save.py --only-missing 抓取并回填 accessed_at/content_hash；"
                    "若页面 404 或未含目标型号规格，则人工更换官方替代来源。",
                ],
            }
            fname.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written += 1
            next_num += 1
        print(f"sources written: {written}  (skipped existing-key: {skipped_existing})")
        print("下一步：python data/scripts/page_save.py --only-missing --skip-done")

    auto = sum(1 for r in plan if r["url_mode"] == "auto")
    cand = sum(1 for r in plan if r["url_mode"] == "candidate")
    manual = sum(1 for r in plan if r["url_mode"] == "needs-manual")
    total_missing = sum(r["missing_count"] for r in plan)
    print(f"summary: entities={len(plan)} missing_fields={total_missing} "
          f"url[auto]={auto} url[candidate]={cand} needs-manual={manual}")
    if manual:
        print("needs-manual（无自动可推官方 URL，待人工/搜索核验）：")
        for r in plan:
            if r["url_mode"] == "needs-manual":
                print(f"  - {r['file']}  {r['canonical_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
