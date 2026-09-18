#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hardquery_resolve.py —— 从 HardQuery 第三方硬件库解析型号详情页 URL（需联网）

用途：官方页抓取困难（典型为 Intel ARK 反爬）时，用 HardQuery 作为替代来源。
本脚本抓栏目页 → 抽型号详情链接 → 与 drafts 中硬件实体名匹配 → 输出映射供回填 sources。

用法（有网环境）：
  python data/scripts/hardquery_resolve.py --report                # 只打印匹配结果
  python data/scripts/hardquery_resolve.py --write-map data/raw/hardquery-url-map.json
  python data/scripts/hardquery_resolve.py --apply                 # 用映射更新 sources 中 hardquery 源 URL（谨慎）

说明：
  - 只统计匹配到的 URL，不改动官方来源（src:amd:* / src:nvidia:*）。
  - 若栏目页为 JS 渲染导致无链接，请改用浏览器另存页面到 data/raw/v3-pages/ 后再解析。
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DRAFT = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03"
HW = DRAFT / "hardware"
SRC = DRAFT / "sources"
RAW = BASE / "data" / "raw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
INDEXES = {
    "cpu": "https://hardquery.com/hardware/cpu/",
    "gpu": "https://hardquery.com/hardware/gpu/",
}


def fetch(url: str, timeout: int = 30, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def links(html: str) -> list:
    out = []
    for m in re.finditer(r'href="([^"]+)"[^>]*>([^<]{2,80})<', html):
        href, text = m.group(1), m.group(2).strip()
        if "/hardware/" in href and re.search(r"\d", text):
            out.append((href if href.startswith("http") else "https://hardquery.com" + href, text))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-map", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    # 待匹配实体（Intel 及未确认官方 URL 的）
    targets = {}
    for f in sorted(HW.glob("*.json")):
        try:
            p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
        except Exception:
            continue
        name = (p.get("identity") or {}).get("canonical_name")
        if not name or p.get("record_kind") != "product":
            continue
        targets[f.name] = name

    catalog = []
    for kind, url in INDEXES.items():
        print(f"[get ] {url}")
        try:
            html = fetch(url)
        except urllib.error.HTTPError as e:
            print(f"[http ] {kind}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {kind}: {type(e).__name__}: {e}")
            continue
        ls = links(html)
        print(f"[ok  ] {kind}: {len(ls)} links")
        catalog.extend(ls)

    norm_index = {norm(t): u for u, t in catalog}
    matches, missed = {}, []
    for fn, name in targets.items():
        key = norm(name)
        url = norm_index.get(key)
        if not url:
            for u, t in catalog:
                tn = norm(t)
                if tn.startswith(key) or key in tn:
                    url = u
                    break
        if url:
            matches[fn] = {"canonical_name": name, "hardquery_url": url}
        else:
            missed.append(name)

    print(f"matched {len(matches)} / {len(targets)} hardware entities")
    for fn, m in list(matches.items())[:10]:
        print("  ", m["canonical_name"], "->", m["hardquery_url"])
    if missed:
        print(f"unmatched ({len(missed)}):", ", ".join(missed[:15]), "..." if len(missed) > 15 else "")

    if args.write_map:
        Path(args.write_map).write_text(json.dumps(matches, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("map written:", args.write_map)

    if args.apply:
        changed = 0
        for f in SRC.glob("*.json"):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            p = b.get("payload") or {}
            if p.get("source_type") != "third_party_database":
                continue
            for fn, m in matches.items():
                if p.get("title", "").startswith(m["canonical_name"]):
                    if p.get("url") != m["hardquery_url"]:
                        p["url"] = m["hardquery_url"]
                        b["payload"] = p
                        b.setdefault("review_notes", []).append("2026-09-07 hardquery_resolve：更新为型号详情页 URL。")
                        f.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        changed += 1
        print("sources updated:", changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
