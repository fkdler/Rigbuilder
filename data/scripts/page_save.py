#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
page_save.py —— A 类字段自动化"第 1 级"：抓官方页并存快照（半自动流水线的前半段，需联网）

做什么：
  1. 遍历 data/v3/drafts/rigbuilder-pending-2026-09-03/sources/*.json
  2. GET payload.url（官方/仓库页），成功后：
     - 快照存 data/raw/v3-pages/<source_key>.html 与 .txt
     - 回填该 source 的 accessed_at（真实抓取时间）、content_hash（SHA-256）
     - availability_status = accessible（失败可选标记 unavailable）
  3. 打印 done/skipped/failed

后半段（流水线第 2 级）：你跑完后把 data/raw/v3-pages/*.txt 交给我（放回工作区即可），
  由我解析文本 → 产出 CPU/GPU A 类字段候选值 + evidence raw_excerpt → 人工抽查。

用法（有网环境）：
  python data/scripts/page_save.py --dry-run --limit 3
  python data/scripts/page_save.py --skip-done --delay 1.0
  python data/scripts/page_save.py --force            # 忽略已抓的重新抓
  # gated HF 卡：$env:HF_TOKEN="hf_..."；网络受限可设 $env:https_proxy
"""
import argparse
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
SOURCES = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03" / "sources"
SNAP = BASE / "data" / "raw" / "v3-pages"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PLACEHOLDER = "2026-09-03T00:00:00Z"

def fetch(url: str, token: str | None, timeout: int = 30, tries: int = 3) -> bytes:
    last = None
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            if token and "huggingface.co" in url:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last

def to_text(raw: bytes) -> str:
    s = raw.decode("utf-8", errors="replace")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-done", action="store_true")
    ap.add_argument("--only-missing", action="store_true", help="只抓 accessed_at 仍为占位的来源")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()

    SNAP.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN")
    files = sorted(SOURCES.glob("*.json"))
    done = skipped = failed = 0
    for idx, f in enumerate(files):
        if args.limit and idx >= args.limit:
            break
        b = json.loads(f.read_text(encoding="utf-8"))
        pay = b.get("payload") or {}
        url = pay.get("url")
        if not url:
            skipped += 1
            continue
        if args.skip_done and not args.force and pay.get("accessed_at") and pay.get("accessed_at") != PLACEHOLDER:
            skipped += 1
            continue
        if args.only_missing and pay.get("accessed_at") and pay.get("accessed_at") != PLACEHOLDER:
            skipped += 1
            continue
        print(f"[get ] {f.name}  {url}")
        if args.dry_run:
            done += 1
            continue
        try:
            raw = fetch(url, token=token)
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"[http ] {f.name}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[fail] {f.name}: {type(e).__name__}: {e}")
            continue
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        digest = hashlib.sha256(raw).hexdigest()
        key = re.sub(r"[^a-zA-Z0-9_-]+", "_", pay.get("source_key") or f.name[:-5])
        (SNAP / f"{key}.html").write_bytes(raw)
        (SNAP / f"{key}.txt").write_text(to_text(raw), encoding="utf-8")
        pay["accessed_at"] = stamp
        pay["content_hash"] = digest
        pay["availability_status"] = "accessible"
        f.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        done += 1
        time.sleep(args.delay)

    print(f"summary: done={done} skipped={skipped} failed={failed}")
    print(f"snapshots at: {SNAP}")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
