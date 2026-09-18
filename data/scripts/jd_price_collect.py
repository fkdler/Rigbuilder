#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jd_price_collect.py —— 京东价格采集（仿照原有 prices/ + sources/source-jd-* 格式）

数据格式与既有 80 条价格完全一致：
  prices/<硬件文件名>-price.json          → record_type: price_snapshot
  sources/source-jd-<型号slug>-<sku>.json → record_type: source_document (retailer, tier C)

三种用法（有网环境）：
  1) 生成待采清单（本地即可）：
       python data/scripts/jd_price_collect.py --plan
     扫描 hardware/ 全部可购型号（cpu/gpu/memory/storage/psu），标注已采/未采，输出
     _jd_price/plan-<日期>.json；新增的 94 个硬件会出现在"未采"里，需要你补 SKU/URL。

  2) 用你提供的价格直接生成数据（推荐，等价于原先的用户提供方式）：
       python data/scripts/jd_price_collect.py --import-user data/raw/jd-prices-user.json
     JSON 格式：[{"name","entity_key","sku","url","amount","semantics":"current_new|current_used","note"}]

  3) 用已有 SKU 自动抓价：
       python data/scripts/jd_price_collect.py --fetch --from-plan _jd_price/plan-<日期>.json
     价格来源：京东价格接口 p.3.cn/prices/mgets?skuIds=J_<sku>（失败则提示人工提供）。

结果同时写入 _jd_price/progress-<日期>.json，便于核对。
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DRAFT = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03"
HW = DRAFT / "hardware"
PRICES = DRAFT / "prices"
SRC = DRAFT / "sources"
JD_DIR = DRAFT / "_jd_price"
RAW = BASE / "data" / "raw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
CN = timezone(timedelta(hours=8))
BUYABLE = {"cpu", "gpu", "memory", "storage", "psu"}
MFR_PREFIX = ("nvidia-", "amd-", "intel-", "kingston-", "corsair-", "samsung-", "crucial-", "wd-", "seagate-")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def model_slug(file_stem: str) -> str:
    for p in MFR_PREFIX:
        if file_stem.startswith(p):
            return file_stem[len(p):]
    return file_stem


def load_hardware():
    out = {}
    for f in sorted(HW.glob("*.json")):
        try:
            p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
        except Exception:
            continue
        if p.get("category") not in BUYABLE or p.get("record_kind") != "product":
            continue
        ek = (p.get("identity") or {}).get("entity_key")
        if not ek:
            continue
        out[ek] = {"file": f.name, "stem": f.stem, "name": p["identity"]["canonical_name"],
                   "category": p["category"], "mfr": (p.get("manufacturer_key") or "").replace("org:", "")}
    return out


def existing_prices():
    """返回 {entity_key: {amount, source_key, sku, observed_at}}"""
    out = {}
    for f in PRICES.glob("*-price.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8")).get("payload") or {}
        except Exception:
            continue
        sku = ""
        m = re.search(r"-(\d{6,})", p.get("source_key") or "")
        if m:
            sku = m.group(1)
        out[p.get("entity_key")] = {"amount": p.get("amount"), "source_key": p.get("source_key"), "sku": sku,
                                    "observed_at": p.get("observed_at"), "file": f.name}
    return out


def fetch_price(sku: str, timeout: int = 20):
    """京东价格接口；返回 (amount, error)"""
    url = f"https://p.3.cn/prices/mgets?skuIds=J_{sku}&type=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": f"https://item.jd.com/{sku}.html"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", errors="replace")
        data = json.loads(txt)
        if data and isinstance(data, list) and data[0].get("p"):
            return float(data[0]["p"]), None
        return None, "价格字段为空（可能下架或需登录）"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def write_bundles(hw, sku, title, amount, semantics, note, accessed_at):
    today = datetime.now(CN)
    month = today.strftime("%Y-%m")
    observed = today.isoformat(timespec="seconds")
    mslug = model_slug(hw["stem"])
    src_key = f"source:jd:{mslug}-{sku}"
    price_key = f"price:{hw['entity_key'] if 'entity_key' in hw else ''}:cn:{month}"
    # 源文件
    src_bundle = {
        "schema_version": "3.2", "record_type": "source_document", "status": "pending",
        "payload": {
            "source_key": src_key, "title": title or f"{hw['name']} 京东商品页",
            "url": f"https://item.jd.com/{sku}.html", "source_type": "retailer", "source_tier": "C",
            "publisher_key": None, "published_at": None, "market_region": "CN", "language": "zh-CN",
            "availability_status": "accessible", "archive_url": None,
            "accessed_at": accessed_at, "content_hash": hashlib.sha256(f"{sku}:{amount}".encode()).hexdigest(),
        },
        "review_notes": [f"京东标价 SKU {sku}；页面标价作为 {semantics}。"],
    }
    (SRC / f"source-jd-{mslug}-{sku}.json").write_text(json.dumps(src_bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 价格文件
    price_bundle = {
        "schema_version": "3.2", "record_type": "price_snapshot", "status": "pending",
        "payload": {
            "price_key": f"price:{hw['entity_key']}:cn:{month}",
            "entity_key": hw["entity_key"],
            "source_key": src_key, "observed_at": observed, "currency": "CNY", "amount": amount,
            "availability": "in_stock",
            "price_type": "current_used" if semantics == "current_used" else "current_new",
            "market_region": "CN", "condition": "used" if semantics == "current_used" else "new",
            "seller_key": None, "notes": note or None,
        },
        "review_notes": [f"{'用户提供' if semantics.startswith('user') else '脚本抓取'}；{note or ''} 京东页面价。".strip()],
    }
    (PRICES / f"{hw['stem']}-price.json").write_text(json.dumps(price_bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return src_key


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="生成待采清单")
    ap.add_argument("--import-user", default=None, help="从用户提供的 JSON 生成价格数据")
    ap.add_argument("--fetch", action="store_true", help="用已有 SKU 自动抓价")
    ap.add_argument("--refetch", action="store_true", help="重抓已采价格（价格更新/验证接口）")
    ap.add_argument("--from-plan", default=None, help="配合 --fetch：读计划里的 sku/url")
    ap.add_argument("--limit", type=int, default=0, help="限制处理数量（测试用）")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    hw = load_hardware()
    have = existing_prices()
    JD_DIR.mkdir(exist_ok=True)
    today = datetime.now(CN).date().isoformat()

    if args.plan:
        rows = []
        for ek, h in hw.items():
            done = ek in have
            rows.append({"entity_key": ek, "file": h["file"], "name": h["name"], "category": h["category"],
                         "mfr": h["mfr"], "priced": done,
                         "amount": (have.get(ek) or {}).get("amount"),
                         "sku": (have.get(ek) or {}).get("sku") or None})
        out = JD_DIR / f"plan-{today}.json"
        out.write_text(json.dumps({"generated": today, "total": len(rows),
                                   "priced": sum(1 for r in rows if r["priced"]),
                                   "unpriced": sum(1 for r in rows if not r["priced"]),
                                   "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        missing = [r for r in rows if not r["priced"]]
        print(f"可购型号 {len(rows)}，已采价 {len(rows)-len(missing)}，未采价 {len(missing)}")
        print(f"计划已写入：{out}")
        print("未采价型号（需补 SKU/URL 或价格）：")
        for r in missing[:40]:
            print(f"  {r['category']:<8} {r['name']:<32} {r['file']}")
        if len(missing) > 40:
            print(f"  ... 另有 {len(missing)-40} 个")
        return 0

    if args.import_user:
        items = json.loads(Path(args.import_user).read_text(encoding="utf-8"))
        if isinstance(items, dict):
            items = items.get("items") or []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ok = miss = 0
        for it in items:
            ek = it.get("entity_key")
            sku = str(it.get("sku") or "")
            amount = it.get("amount")
            if ek not in hw or not sku or amount is None:
                miss += 1
                print(f"[skip] {it.get('name')} (缺 entity/sku/amount)")
                continue
            write_bundles({**hw[ek], "entity_key": ek}, sku, it.get("title"), amount,
                          it.get("semantics") or "current_new", it.get("note"), now)
            ok += 1
            print(f"[ok  ] {hw[ek]['name']}  ¥{amount}  sku={sku}")
        print(f"imported: {ok}, skipped: {miss}")
        return 0

    if args.refetch:
        targets = []
        for f in sorted(PRICES.glob("*-price.json")):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            p = b.get("payload") or {}
            ek = p.get("entity_key")
            m = re.search(r"-(\d{6,})", p.get("source_key") or "")
            if ek in hw and m:
                targets.append({"price_file": f, "bundle": b, "sku": m.group(1), **hw[ek], "entity_key": ek})
        print(f"待重抓 {len(targets)} 个（验证接口/更新价格）")
        if args.limit:
            targets = targets[:args.limit]
            print(f"按 --limit 只处理前 {len(targets)} 个")
        now_cn = datetime.now(CN).isoformat(timespec="seconds")
        ok = fail = unchanged = 0
        for t in targets:
            amount, err = fetch_price(t["sku"])
            if err:
                fail += 1
                print(f"[fail] {t['name']} sku={t['sku']}: {err}")
                continue
            old = (t["bundle"].get("payload") or {}).get("amount")
            if old is not None and abs(float(old) - amount) < 0.01:
                unchanged += 1
                print(f"[same] {t['name']}  ¥{amount}")
                continue
            p = t["bundle"]["payload"]
            p["amount"] = amount
            p["observed_at"] = now_cn
            t["bundle"]["review_notes"] = (t["bundle"].get("review_notes") or []) + \
                [f"2026-09-14 脚本重抓更新：{old} -> {amount}（京东价格接口）。"]
            t["price_file"].write_text(json.dumps(t["bundle"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            ok += 1
            print(f"[upd ] {t['name']}  ¥{old} -> ¥{amount}")
            time.sleep(args.delay)
        print(f"summary: updated={ok} unchanged={unchanged} failed={fail}")
        return 0

    if args.fetch:
        plan_path = Path(args.from_plan) if args.from_plan else None
        targets = []
        if plan_path and plan_path.exists():
            for r in json.loads(plan_path.read_text(encoding="utf-8")).get("rows", []):
                if r.get("sku") and not r.get("priced"):
                    targets.append(r)
        else:
            for ek, v in have.items():
                if ek in hw and v.get("sku"):
                    targets.append({**hw[ek], "entity_key": ek, "sku": v["sku"]})
        print(f"待抓价 {len(targets)} 个")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ok = fail = 0
        for t in targets:
            amount, err = fetch_price(t["sku"])
            if err:
                fail += 1
                print(f"[fail] {t['name']} sku={t['sku']}: {err}")
                continue
            write_bundles(t, t["sku"], None, amount, "current_new", "脚本抓取", now)
            ok += 1
            print(f"[ok  ] {t['name']}  ¥{amount}  sku={t['sku']}")
            time.sleep(args.delay)
        print(f"summary: ok={ok} failed={fail}")
        return 0

    ap.error("需要 --plan / --import-user / --fetch 之一")
    return 2


if __name__ == "__main__":
    sys.exit(main())
