#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jd_sku_search.py —— 京东 SKU 候选提取（半自动：浏览器搜索 + 脚本解析候选表）

为什么半自动：京东搜索页有风控，脚本直接请求常被拦；而且"哪个 SKU 才是全新正品单件"
必须人工判断（会混入二手/散片/套装/整机）。本脚本把候选整理成**可直接交给
jd_price_from_csv.py 的格式**，你只需删掉不要的行。

推荐工作流（批量）：
  1) 浏览器打开京东搜索页（关键词 = 型号名），例如
        https://search.jd.com/Search?keyword=Ryzen%205%207500X3D&enc=utf-8
     Ctrl+S 另存为「网页，仅 HTML」，存到 data/raw/jd-search/
     文件名用型号名或型号 slug 均可，例如：
        Ryzen 5 7500X3D.html   或   ryzen-5-7500x3d.html
     （脚本会据此自动匹配 hardware 里的 entity_key）

  2) 批量解析：
        python data/scripts/jd_sku_search.py --dir data/raw/jd-search --out data/raw/jd-search/candidates.csv
     · 每个候选一行，按「匹配度」降序排列，并标出疑似非全新正品的行
     · 表头与 jd_price_from_csv.py 兼容（额外 3 列仅作参考，不影响导入）

  3) 打开 candidates.csv：
     · 每个型号**只保留 1 行**（匹配=是 且不是二手/散片/套装/整机）→ 删掉其余行
     · 确认或填写「价格CNY(填)」（脚本抓到价的已预填）
     · 可在「备注(填)」写店铺名；二手请写"二手"

  4) 生成数据：
        python data/scripts/jd_price_from_csv.py --csv data/raw/jd-search/candidates.csv --report
        python data/scripts/jd_price_from_csv.py --csv data/raw/jd-search/candidates.csv --apply

单文件模式（不批量时）：
  python data/scripts/jd_sku_search.py --html x.html --model "Ryzen 5 7500X3D" --out y.csv
"""
import argparse
import csv
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parents[2]
HW_DIR = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03" / "hardware"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
EXCLUDE_HINTS = ["二手", "翻新", "拆机", "散片", "套装", "主板套装", "整机", "主机", "准系统", "闲鱼", "维修", "配件", "工包"]
CSV_HEADER = ["型号", "类别", "厂商", "entity_key", "SKU(填)", "价格CNY(填)", "商品URL(填)", "备注(填)",
              "候选标题", "店铺", "匹配", "排除词"]


def fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", h)


def parse_candidates(html: str):
    """从京东搜索页 HTML 提取候选：(sku, title, price, shop)"""
    out = {}
    for m in re.finditer(r"item\.jd\.com/(\d{6,})\.html", html):
        sku = m.group(1)
        seg = html[max(0, m.start() - 1500): m.start() + 2500]
        title = ""
        for tm in re.finditer(r"<em>(.*?)</em>", seg, re.S):
            t = html_to_text(tm.group(1)).strip()
            if len(t) > len(title):
                title = t
        if not title:
            title = html_to_text(seg)[:150]
        price = ""
        pm = (re.search(r'data-price="([\d.]+)"', seg) or re.search(r"<i>([\d.]+)</i>", seg)
              or re.search(r"￥\s*([\d.]+)", seg))
        if pm:
            price = pm.group(1)
        shop = ""
        sm = (re.search(r'class="curr-shop[^"]*"[^>]*>([^<]{2,30})<', seg)
              or re.search(r"店铺[:：]\s*([^<\s]{2,30})", seg))
        if sm:
            shop = sm.group(1).strip()
        if sku not in out or len(title) > len(out[sku]["title"]):
            out[sku] = {"sku": sku, "title": title, "price": price, "shop": shop}
    return list(out.values())


def load_hardware_index():
    """hardware 目录 → [{name, ek, cat, vendor, token}]"""
    items = []
    if not HW_DIR.exists():
        return items
    for f in sorted(HW_DIR.glob("*.json")):
        try:
            import json
            p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
        except Exception:  # noqa: BLE001
            continue
        if p.get("record_kind") != "product":
            continue
        ek = (p.get("identity") or {}).get("entity_key")
        name = (p.get("identity") or {}).get("canonical_name")
        if not ek or not name:
            continue
        items.append({"name": name, "ek": ek, "cat": p.get("category"),
                      "vendor": (p.get("manufacturer_key") or "").replace("org:", ""),
                      "token": model_token(name), "file": f.stem})
    return items


def model_token(name: str) -> str:
    """型号核心 token（用于在商品标题里辨认型号）：
    'Ryzen 5 7500X3D'→'7500x3d'、'Core i5-12400F'→'12400f'、
    'Core Ultra 5 225'→'225'、'GeForce RTX 5060 Ti 8GB'→'5060ti'、'Radeon RX 9070 GRE'→'9070'"""
    low = name.lower()
    m = re.search(r"(\d{3,5}[a-z0-9]*)", low)
    if not m:
        return re.sub(r"[^a-z0-9]", "", low)
    tok = m.group(1)
    # NVIDIA 常见写法：数字与 Ti/Super 之间有空格
    rest = low[m.end():]
    suf = re.match(r"\s*(ti|super)", rest)
    if suf:
        tok += suf.group(1)
    return tok


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_by_filename(stem: str, index):
    """文件名 → hardware 条目（先精确匹配类型名/slug，再退化到 token 包含）"""
    s = norm(stem)
    for it in index:
        if s == norm(it["name"]) or s == norm(it["file"]):
            return it
    cands = [it for it in index if norm(it["name"]).endswith(s) or norm(it["file"]).endswith(s)]
    if len(cands) == 1:
        return cands[0]
    cands = [it for it in index if s and (s in norm(it["name"]) or norm(it["name"]) in s)]
    return cands[0] if len(cands) == 1 else None


def score_title(title: str, item) -> str:
    t = norm(title)
    tok = item["token"]
    if not tok:
        return "低"
    if tok in t:
        return "高"
    # 退化：型号名的主要数字段命中
    nums = re.findall(r"\d{3,5}", norm(item["name"]))
    return "中" if any(n in t for n in nums) else "低"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="批量：解析目录下所有 .html（文件名=型号名或 slug）")
    ap.add_argument("--html", default=None, help="单个 HTML 文件")
    ap.add_argument("--model", default=None, help="配合 --html：型号名")
    ap.add_argument("--keyword", default=None, help="直接给关键词（脚本尝试请求，可能被风控）")
    ap.add_argument("--out", default=None, help="输出 CSV 路径")
    args = ap.parse_args()

    index = load_hardware_index()
    print(f"hardware 索引：{len(index)} 个可购型号")
    rows = []

    def handle(html: str, item, src: str):
        cands = parse_candidates(html)
        if not cands:
            print(f"  [warn] {src}: 未解析到候选（页面可能需要登录/是验证页）")
            return
        for c in cands:
            sc = score_title(c["title"], item) if item else "-"
            hint = ",".join([h for h in EXCLUDE_HINTS if h in c["title"]])
            rows.append({
                "型号": item["name"] if item else (args.model or src),
                "类别": item["cat"] if item else "",
                "厂商": item["vendor"] if item else "",
                "entity_key": item["ek"] if item else "",
                "SKU(填)": c["sku"],
                "价格CNY(填)": c["price"],
                "商品URL(填)": f"https://item.jd.com/{c['sku']}.html",
                "备注(填)": c["shop"],
                "候选标题": c["title"][:120],
                "店铺": c["shop"],
                "匹配": sc,
                "排除词": hint,
            })
        print(f"  {src}: 候选 {len(cands)} 个（高匹配 {sum(1 for r in rows[-len(cands):] if r['匹配']=='高')}）")

    if args.dir:
        d = Path(args.dir)
        files = sorted(list(d.glob("*.html")) + list(d.glob("*.htm")))
        if not files:
            print(f"{d} 下没有 HTML 文件")
            return 1
        print(f"批量解析 {len(files)} 个搜索页：")
        for fp in files:
            item = match_by_filename(fp.stem, index)
            if not item:
                print(f"  [skip] {fp.name}: 文件名无法匹配 hardware 型号（请改成型号名或 slug，如 'Ryzen 5 7500X3D.html'）")
                continue
            handle(fp.read_text(encoding="utf-8", errors="replace"), item, fp.name)
    elif args.html:
        item = None
        if args.model:
            item = next((it for it in index if norm(it["name"]) == norm(args.model)), None)
            if not item:
                item = {"name": args.model, "ek": "", "cat": "", "vendor": "", "token": model_token(args.model)}
        handle(Path(args.html).read_text(encoding="utf-8", errors="replace"), item, args.html)
    elif args.keyword:
        url = "https://search.jd.com/Search?keyword=" + urllib.parse.quote(args.keyword) + "&enc=utf-8"
        print(f"[get ] {url}")
        try:
            html = fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {type(e).__name__}: {e}")
            print("提示：京东搜索有风控，请用浏览器搜索 → Ctrl+S 另存 HTML → 用 --dir 或 --html。")
            return 1
        item = next((it for it in index if norm(it["name"]) == norm(args.keyword)), None)
        if not item:
            item = {"name": args.keyword, "ek": "", "cat": "", "vendor": "", "token": model_token(args.keyword)}
        handle(html, item, "keyword")
    else:
        ap.error("需要 --dir / --html / --keyword 之一")

    if not rows:
        print("没有候选可输出。")
        return 1

    # 排序：型号 → 匹配度高→低 → 排除词少→多
    order = {"高": 0, "中": 1, "低": 2, "-": 3}
    rows.sort(key=lambda r: (r["型号"], order.get(r["匹配"], 9), len(r["排除词"])))
    print(f"\n合计候选 {len(rows)} 行，涉及 {len({r['entity_key'] or r['型号'] for r in rows})} 个型号")
    print("提示：每个型号**只保留 1 行**（匹配=高、无排除词、价格正确），删掉其余后再跑 jd_price_from_csv.py")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_HEADER)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"CSV 已写入：{out}")
        print("下一步：")
        print(f"  python data/scripts/jd_price_from_csv.py --csv {out} --report")
        print(f"  python data/scripts/jd_price_from_csv.py --csv {out} --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
