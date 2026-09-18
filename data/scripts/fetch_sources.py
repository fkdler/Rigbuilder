#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_sources.py —— 事实层来源抓取（sources/ 批量抓取 + 失败原因留档）

与 page_save.py 的区别：
  - 支持按域名筛选、失败原因结构化留档（HTTP 码/异常/耗时），便于定位像 Intel 这类反爬站点
  - 输出 data/raw/fetch-report-<date>.json，列出每个未成功来源的原因与建议
  - 默认只抓 accessed_at 仍为占位的来源（即"未抓/失败"的）

用法（有网环境）：
  python data/scripts/fetch_sources.py --report                    # 只列出待抓清单（不联网）
  python data/scripts/fetch_sources.py --fetch --limit 20          # 抓前 20 个
  python data/scripts/fetch_sources.py --fetch --domain amd.com --domain nvidia.com
  python data/scripts/fetch_sources.py --fetch --skip-intel        # 跳过已知反爬的 Intel 站点
  python data/scripts/fetch_sources.py --fetch --retry-failed 3

抓成功后：写 data/raw/v3-pages/<source_key>.html/.txt，回填 accessed_at / content_hash / availability_status。
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

try:  # Windows 控制台常为 GBK，避免非 ASCII 符号报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parents[2]
DRAFT = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03"
SRC = DRAFT / "sources"
SNAP = BASE / "data" / "raw" / "v3-pages"
RAW = BASE / "data" / "raw"
PLACEHOLDER = "2026-09-03T00:00:00Z"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# 已知反爬/需特殊处理的域名
TRICKY = {
    "www.intel.com": "Akamai 反爬：脚本请求多被拒；建议改用 HardQuery 第三方库或浏览器另存页面后放 data/raw/v3-pages/",
    "hardquery.com": "可能为动态渲染；若拿不到内容请浏览器另存",
    "support-leagueoflegends.riotgames.com": "Zendesk 站点，可能需 Accept-Language 或重试",
    "www.epicgames.com": "Cloudflare 保护，重试或换 UA",
}


def fetch(url: str, token: str | None, timeout: int = 30, tries: int = 3, insecure: bool = False):
    """返回 (raw_bytes, error_info, effective_url)；成功时 error_info 为 None"""
    # HuggingFace 国内镜像：抓取走镜像，来源仍记录原 URL
    hf_endpoint = os.environ.get("HF_ENDPOINT", "").rstrip("/")
    effective = url
    if hf_endpoint and "huggingface.co" in url:
        effective = url.replace("https://huggingface.co", hf_endpoint)
    if insecure:
        import ssl
        ctx = ssl._create_unverified_context()
    else:
        ctx = None
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    last = None
    for i in range(tries):
        t0 = time.time()
        try:
            req = urllib.request.Request(effective, headers=headers)
            if token and "huggingface" in effective:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read(), None, effective
        except urllib.error.HTTPError as e:
            last = {"error_type": "http", "http_code": e.code, "message": str(e.reason), "elapsed_s": round(time.time() - t0, 2)}
        except urllib.error.URLError as e:
            last = {"error_type": "url", "http_code": None, "message": str(e.reason), "elapsed_s": round(time.time() - t0, 2)}
        except Exception as e:  # noqa: BLE001
            last = {"error_type": type(e).__name__, "http_code": None, "message": str(e), "elapsed_s": round(time.time() - t0, 2)}
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return None, last, effective


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
    ap.add_argument("--report", action="store_true", help="只列出待抓清单")
    ap.add_argument("--fetch", action="store_true", help="执行抓取")
    ap.add_argument("--domain", action="append", default=[], help="只抓指定域名（可多次）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="已抓过的也重抓")
    ap.add_argument("--skip-intel", action="store_true", help="跳过 intel.com（已知反爬）")
    ap.add_argument("--insecure", action="store_true", help="跳过 SSL 证书校验（用于个别证书异常站点）")
    args = ap.parse_args()
    if not (args.report or args.fetch):
        ap.error("需要 --report 或 --fetch")

    SNAP.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN")
    if token:
        token = token.strip().strip('"').strip("'")
        if not token:
            token = None
        elif not token.isascii():
            print("[警告] HF_TOKEN 含非 ASCII 字符（占位符未替换？）→ 已忽略；公开仓库不需要 token。")
            token = None

    pending = []
    for f in sorted(SRC.glob("*.json")):
        try:
            b = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        p = b.get("payload") or {}
        url = p.get("url")
        if not url:
            continue
        done = p.get("accessed_at") and p.get("accessed_at") != PLACEHOLDER
        if done and not args.force:
            continue
        host = url.split("//")[-1].split("/")[0]
        if args.domain and not any(d in host for d in args.domain):
            continue
        if args.skip_intel and "intel.com" in host:
            continue
        pending.append((f, b, p, host, url))

    # 按域名汇总
    by_host = {}
    for _, _, _, host, _ in pending:
        by_host[host] = by_host.get(host, 0) + 1
    print(f"待抓取来源：{len(pending)}")
    for h, c in sorted(by_host.items(), key=lambda x: -x[1]):
        tip = TRICKY.get(h)
        print(f"  {h:<45} {c:>4}" + (f"   [!] {tip}" if tip else ""))
    if args.report and not args.fetch:
        return 0

    report = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": []}
    ok = fail = 0
    for idx, (f, b, p, host, url) in enumerate(pending):
        if args.limit and idx >= args.limit:
            break
        print(f"[get ] {f.name}  {url}")
        raw, err, effective = fetch(url, token=token, tries=args.tries, insecure=args.insecure)
        if err:
            fail += 1
            item = {"file": f.name, "source_key": p.get("source_key"), "url": url, "host": host, **err}
            tip = TRICKY.get(host)
            if tip:
                item["suggestion"] = tip
            if effective != url:
                item["effective_url"] = effective
            report["items"].append(item)
            print(f"[fail] {err.get('http_code') or err.get('error_type')}: {err.get('message')}")
            continue
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        digest = hashlib.sha256(raw).hexdigest()
        key = re.sub(r"[^a-zA-Z0-9_-]+", "_", p.get("source_key") or f.name[:-5])
        (SNAP / f"{key}.html").write_bytes(raw)
        (SNAP / f"{key}.txt").write_text(to_text(raw), encoding="utf-8")
        p["accessed_at"] = stamp
        p["content_hash"] = digest
        p["availability_status"] = "accessible"
        b["payload"] = p
        if effective != url:
            b["review_notes"] = (b.get("review_notes") or []) + [f"{stamp[:10]} 经镜像抓取：{effective}（来源登记 URL 保持原样 {url}）。"]
        f.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ok += 1
        print(f"[ok  ] {len(raw)} bytes, sha256={digest[:12]}…" + (f"  (via {effective.split('/')[2]})" if effective != url else ""))
        time.sleep(args.delay)

    report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["ok"] = ok
    report["failed"] = fail
    # 文件名带时间戳，避免同一日期多次运行互相覆盖
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = RAW / f"fetch-report-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nsummary: ok={ok} failed={fail}")
    print(f"report: {out}")
    print(f"snapshots: {SNAP}")
    if fail:
        print("\n失败原因汇总：")
        agg = {}
        for it in report["items"]:
            k = f"{it['host']} | {it.get('http_code') or it.get('error_type')}"
            agg[k] = agg.get(k, 0) + 1
        for k, c in sorted(agg.items(), key=lambda x: -x[1]):
            print(f"  {c:>4}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
