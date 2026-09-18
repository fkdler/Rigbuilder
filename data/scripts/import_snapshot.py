#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_snapshot.py —— 人工另存页面导入（解决脚本抓不到的站点）

适用场景：Intel（Akamai 反爬）、HardQuery（动态渲染）、京东（价格 JS 加载）等
脚本请求拿不到内容的页面 → 用浏览器打开并另存 HTML → 本脚本转成快照并回填来源字段。

用法（Windows PowerShell）：
  # 1) 先看待处理清单（含建议文件名）
  python data/scripts/import_snapshot.py --list-missing

  # 2) 浏览器打开 URL → Ctrl+S 另存为 HTML，放到 data/raw/manual-saves/
  #    文件名用清单里给出的建议名（= source_key 清洗后的名字），例如：
  #    data/raw/manual-saves/src_intel_cpu-sku-134586.html
  #
  # 3) 批量导入（自动匹配 source_key）
  python data/scripts/import_snapshot.py --dir data/raw/manual-saves

  # 4) 或指定单个文件与来源
  python data/scripts/import_snapshot.py --html data/raw/manual-saves/xxx.html --source-key src:intel:cpu-sku-134586

说明：
  - accessed_at 用导入时刻（真实访问时间）；content_hash 按另存文件内容计算；
  - review_notes 会记录"人工浏览器另存导入"，便于审核人区分自动抓取与人工另存。
"""
import argparse
import hashlib
import html as html_lib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DRAFT = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03"
SRC = DRAFT / "sources"
SNAP = BASE / "data" / "raw" / "v3-pages"
MANUAL = BASE / "data" / "raw" / "manual-saves"
PLACEHOLDER = "2026-09-03T00:00:00Z"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def to_text(raw: bytes) -> str:
    s = raw.decode("utf-8", errors="replace")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def key_to_filename(key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", key)


def load_sources():
    out = {}
    for f in SRC.glob("*.json"):
        try:
            b = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        k = (b.get("payload") or {}).get("source_key")
        if k:
            out[k] = f
    return out


def do_import(html_path: Path, source_key: str, sources, note_suffix=""):
    f = sources.get(source_key)
    if not f:
        print(f"[skip] 未知 source_key: {source_key}")
        return False
    raw = html_path.read_bytes()
    if len(raw) < 200:
        print(f"[warn] {html_path.name} 仅 {len(raw)}B，可能仍是空壳（建议确认页面已完整加载后再另存）")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    digest = hashlib.sha256(raw).hexdigest()
    SNAP.mkdir(parents=True, exist_ok=True)
    stem = key_to_filename(source_key)
    (SNAP / f"{stem}.html").write_bytes(raw)
    txt = to_text(raw)
    (SNAP / f"{stem}.txt").write_text(txt, encoding="utf-8")
    b = json.loads(f.read_text(encoding="utf-8"))
    p = b.get("payload") or {}
    p["accessed_at"] = stamp
    p["content_hash"] = digest
    p["availability_status"] = "accessible"
    b["payload"] = p
    b["review_notes"] = (b.get("review_notes") or []) + [
        f"{stamp[:10]} 人工浏览器另存导入（{html_path.name}，{len(raw)}B，文本 {len(txt)} 字）{note_suffix}"
    ]
    f.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok  ] {source_key}  <- {html_path.name}  ({len(raw)}B / txt {len(txt)} 字)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-missing", action="store_true", help="列出未抓来源与建议文件名")
    ap.add_argument("--dir", default=None, help="批量导入目录（文件名=清洗后的 source_key）")
    ap.add_argument("--html", default=None, help="单个 HTML 文件")
    ap.add_argument("--source-key", default=None)
    ap.add_argument("--out-list", default=None, help="把待处理清单写成 CSV")
    args = ap.parse_args()

    sources = load_sources()

    if args.list_missing:
        pend = []
        for k, f in sources.items():
            try:
                p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
            except Exception:
                continue
            if p.get("accessed_at") and p.get("accessed_at") != PLACEHOLDER:
                continue
            pend.append((k, p.get("url"), f.name))
        print(f"待人工处理来源：{len(pend)}")
        print(f"建议另存目录：{MANUAL}")
        print(f"{'source_key':<52} {'建议文件名':<52} URL")
        rows = []
        for k, url, fname in sorted(pend):
            suggested = key_to_filename(k) + ".html"
            print(f"{k:<52} {suggested:<52} {url}")
            rows.append([k, suggested, url, fname])
        if args.out_list:
            import csv
            with open(args.out_list, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["source_key", "建议另存文件名", "url", "source_file"])
                w.writerows(rows)
            print(f"\n清单已写入：{args.out_list}")
        return 0

    if args.html and args.source_key:
        return 0 if do_import(Path(args.html), args.source_key, sources) else 1

    if args.dir:
        d = Path(args.dir)
        if not d.exists():
            print(f"目录不存在：{d}（请先创建并放入另存的 HTML）")
            return 1
        files = sorted(list(d.glob("*.html")) + list(d.glob("*.htm")))
        if not files:
            print(f"{d} 下没有 .html 文件")
            return 1
        # 文件名 → source_key 反查
        by_stem = {key_to_filename(k).lower(): k for k in sources}
        ok = skip = 0
        for fp in files:
            sk = by_stem.get(fp.stem.lower())
            if not sk:
                # 尝试模糊匹配（去掉前缀差异）
                cands = [k for k in sources if key_to_filename(k).lower().endswith(fp.stem.lower())]
                sk = cands[0] if len(cands) == 1 else None
            if not sk:
                print(f"[skip] 无法匹配 source_key：{fp.name}（请用 --list-missing 里的建议文件名）")
                skip += 1
                continue
            ok += 1 if do_import(fp, sk, sources) else 0
        print(f"\n导入完成：ok={ok}, skipped={skip}")
        return 0

    ap.error("需要 --list-missing / --dir / (--html + --source-key)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
