#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jd_price_from_csv.py —— 把填好的待采价 CSV 直接转成价格数据

用法（本地即可，不需要联网）：
  1) 打开 data/v3/drafts/rigbuilder-pending-2026-09-03/_jd_price/to-collect-<日期>.csv
     （Excel 可直接编辑；也可另存为 xlsx 再导出 csv）
  2) 在京东逐个型号找到「全新正品单件」商品页，填写四列：
        SKU(填)      —— 商品页 URL 里的数字，如 https://item.jd.com/10001234567.html → 10001234567
        价格CNY(填)  —— 页面当前价（数字，可带小数）
        商品URL(填)  —— 完整商品页 URL（可选，缺省由 SKU 拼出）
        备注(填)     —— 店铺名 / "二手" 等（二手请在备注写清，会记为 current_used）
  3) 运行：
        python data/scripts/jd_price_from_csv.py --csv <csv路径> --report   # 先预览
        python data/scripts/jd_price_from_csv.py --csv <csv路径> --apply    # 生成数据
  生成结果与既有 80 条价格完全同构：
        prices/<型号>-price.json  +  sources/source-jd-<型号>-<sku>.json
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from jd_price_collect import load_hardware, write_bundles, existing_prices  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="待采价 CSV（已填写 SKU 与价格）")
    ap.add_argument("--report", action="store_true", help="只预览，不写盘")
    ap.add_argument("--apply", action="store_true", help="生成价格与来源数据")
    args = ap.parse_args()

    hw = load_hardware()
    have = existing_prices()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = []
    with open(args.csv, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            sku = (r.get("SKU(填)") or "").strip()
            price = (r.get("价格CNY(填)") or "").strip()
            if not sku or not price:
                continue
            rows.append(r)

    print(f"CSV 中已填写的型号：{len(rows)} 个")
    ok = skip = 0
    for r in rows:
        ek = (r.get("entity_key") or "").strip()
        name = (r.get("型号") or "").strip()
        sku = (r.get("SKU(填)") or "").strip()
        try:
            amount = float((r.get("价格CNY(填)") or "").strip())
        except ValueError:
            print(f"[skip] {name}: 价格不是数字")
            skip += 1
            continue
        if ek not in hw:
            print(f"[skip] {name}: entity_key 未在 hardware 中找到（{ek}）")
            skip += 1
            continue
        if ek in have:
            print(f"[warn] {name}: 已有价格（{have[ek].get('amount')}），将覆盖")
        note = (r.get("备注(填)") or "").strip()
        semantics = "current_used" if ("二手" in note or "used" in note.lower()) else "current_new"
        note_txt = note or None
        if args.report:
            print(f"[ok  ] {name:<32} ¥{amount:<10} sku={sku}  {semantics}  {note_txt or ''}")
        else:
            write_bundles({**hw[ek], "entity_key": ek}, sku, None, amount, semantics, note_txt, now)
            print(f"[写入] {name:<32} ¥{amount:<10} sku={sku}  {semantics}")
        ok += 1

    print(f"\n合计：可写入 {ok}，跳过 {skip}")
    if not args.apply and not args.report:
        print("（未写入；加 --apply 执行，或 --report 先预览）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
