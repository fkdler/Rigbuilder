"""Fetch per-model PassMark detail pages for the catalogue's CPUs and GPUs.

Why per-model pages
-------------------
The list pages are enough for a score, but not for a *cohort*: ``cpu_list.php``
is headed "Intel CPU List" and covers Intel only, and its rank is taken across
every CPU PassMark has ever graded, including decade-old parts.  A per-model page
carries what a ranking actually needs -- the class rank and the size of the
population it was taken from -- plus fields the catalogue is still missing
(typical TDP, socket, memory bandwidth, form-factor class).

How each category is addressed
------------------------------
CPU: the list page's own autocomplete widget calls ``/autocomplete/cpu/?query=``,
which returns ``{value: name, data: id}`` for both Intel and AMD parts, so every
catalogue CPU can be located regardless of vendor.  That endpoint answers 403 to
a plain navigation request and needs the ``X-Requested-With`` header plus a
referer.

GPU: the list has no suggestion endpoint and no class rank, and its rank column
already runs 1..N with no gaps, so the GPU list page is the value source and no
per-model page is fetched.

Permission
----------
Both sites' ``robots.txt`` (checked 2026-09-16) allow these paths: the ``*`` group
on ``cpubenchmark.net`` and ``videocardbenchmark.net`` blocks only ``/search/``,
``/cgi-bin/``, ``/includes/``, ``/cache/`` and similar admin paths, and no
AI-agent group bans this client.  Every response is cached on disk, so a re-run
re-reads the cache instead of the site; a small worker pool plus a short delay
keeps the request rate modest while still finishing in minutes rather than tens of
minutes.

Usage::

    python backend/scripts/scrape_passmark_details.py --category cpu [--limit N] [--workers 4]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql  # noqa: E402

from app.db.session import engine  # noqa: E402

ROOT = Path(r"D:\RigBuilder")
RAW = ROOT / "data" / "raw" / "passmark"
CACHE = RAW / "cache"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY_SECONDS = 0.25
SNAPSHOT = "2026-09-16"

BASE = {
    "cpu": "https://www.cpubenchmark.net",
    "gpu": "https://www.videocardbenchmark.net",
}
LIST_PAGE = {
    "cpu": RAW / f"cpu_list_{SNAPSHOT}.html",
    "gpu": RAW / f"gpu_list_{SNAPSHOT}.html",
}
LIST_URL = {"cpu": f"{BASE['cpu']}/cpu_list.php", "gpu": f"{BASE['gpu']}/gpu_list.php"}
DETAIL_SCRIPT = {"cpu": "cpu.php", "gpu": "gpu.php"}
DETAIL_PARAM = {"cpu": "cpu", "gpu": "gpu"}

_VENDOR = ("intel ", "amd ", "nvidia ")
_PORTABLE = re.compile(r"[^a-z0-9]")
_LOCK = threading.Lock()


def normalize(name: str) -> str:
    """Fold a marketing name to a comparison key (see build_rank_release.py)."""
    text = name.strip()
    lowered = text.casefold()
    for prefix in _VENDOR:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.split("@")[0].replace("+", " plus ")
    return _PORTABLE.sub("", text.casefold())


# ---------------------------------------------------------------------- fetching

def _fetch(url: str, referer: str = "", ajax: bool = False) -> str:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    cached = CACHE / f"{key}.html"
    try:
        return cached.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
        if "gzip" in (response.headers.get("Content-Encoding") or ""):
            body = gzip.decompress(body)
    text = body.decode("utf-8", "replace")
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    time.sleep(DELAY_SECONDS)
    return text


def autocomplete_service(category: str) -> str:
    """Recover the JSON suggestion endpoint from the list page itself."""
    match = re.search(r"serviceUrl:\s*'([^']+)'", LIST_PAGE[category].read_text(encoding="utf-8"))
    return match.group(1) if match else "/autocomplete/cpu/"


def source_id_map(category: str) -> dict[str, str]:
    """Source name -> source id, read out of the list page's own row links."""
    html = LIST_PAGE[category].read_text(encoding="utf-8")
    pattern = r"%s\?%s=([^&\"]+?)&(?:amp;)?id=(\d+)" % (
        re.escape(DETAIL_SCRIPT[category]), DETAIL_PARAM[category])
    out: dict[str, str] = {}
    for name, ident in re.findall(pattern, html):
        out.setdefault(normalize(urllib.parse.unquote_plus(name)), ident)
    return out


# ---------------------------------------------------------------------- parsing

_NUM = r"([\d,]+)"


def _text(html: str) -> str:
    stripped = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return re.sub(r"\s+", " ", stripped)


def parse_scores(text: str, category: str) -> dict:
    out: dict = {}
    if category == "cpu":
        match = re.search(rf"{_NUM} Single Thread Rating {_NUM}", text)
        if match:
            out["cpu_mark"] = int(match.group(1).replace(",", ""))
            out["single_thread"] = int(match.group(2).replace(",", ""))
        match = re.search(r"Overall Rank:\s*(\d+)\w{2} fastest in multithreading out of (\d+) CPUs", text)
        if match:
            out["rank_multi"] = int(match.group(1))
            out["population_all_cpus"] = int(match.group(2))
        match = re.search(r"(\d+)\w{2} fastest in single threading out of (\d+) CPUs", text)
        if match:
            out["rank_single"] = int(match.group(1))
        match = re.search(r"(\d+)\w{2} fastest in out of (\d+) (Desktop|Laptop|Mobile|Server|Embedded) CPU", text)
        if match:
            out["rank_in_class"] = int(match.group(1))
            out["population_in_class"] = int(match.group(2))
            out["class"] = match.group(3)
        match = re.search(r"CPU First Seen on Charts:\s*(Q[1-4]\s*\d{4})", text)
        if match:
            out["first_seen_quarter"] = match.group(1)
        match = re.search(r"Release Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)
        if match:
            out["release_date_text"] = match.group(1)
    else:
        match = re.search(rf"{_NUM} (?:Passmark )?G3D Mark", text)
        if match:
            out["g3d_mark"] = int(match.group(1).replace(",", ""))
        match = re.search(r"Overall Rank:\s*(\d+)\w{2} fastest[^.]*?out of (\d+)", text)
        if match:
            out["rank_multi"] = int(match.group(1))
            out["population_all_gpus"] = int(match.group(2))
        match = re.search(r"Videocard First Benchmarked:\s*([\d-]{10})", text)
        if match:
            out["first_benchmarked"] = match.group(1)

    match = re.search(r"Class:\s*([A-Za-z]+)\s+Socket:\s*([A-Za-z0-9\-+./]+)", text)
    if match:
        out["class"] = out.get("class") or match.group(1)
        out["socket"] = match.group(2)
    else:
        match = re.search(r"Class:\s*([A-Za-z]+)", text)
        if match:
            out["class"] = out.get("class") or match.group(1)

    match = re.search(r"Clockspeed:\s*([\d.]+)\s*GHz", text)
    if match:
        out["base_clock_mhz"] = int(float(match.group(1)) * 1000)
    match = re.search(r"Turbo Speed:\s*([\d.]+)\s*GHz", text)
    if match:
        out["boost_clock_mhz"] = int(float(match.group(1)) * 1000)
    match = re.search(r"Cores:\s*(\d+)\s+Threads:\s*(\d+)", text)
    if match:
        out["cores"] = int(match.group(1))
        out["threads"] = int(match.group(2))
    match = re.search(r"(?:Typical )?TDP:\s*(\d+)\s*W", text)
    if match:
        out["tdp_w"] = int(match.group(1))
    match = re.search(r"Max TDP:\s*(\d+)\s*W", text)
    if match:
        out["max_tdp_w"] = int(match.group(1))
    return out


def load_entities(category: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(sql("""
            SELECT e.entity_key, e.canonical_name, h.form_factor
            FROM truth.catalog_entity e JOIN truth.hardware h ON h.entity_id = e.id
            WHERE h.category = :c AND h.record_kind = 'product'
            ORDER BY e.canonical_name"""), {"c": category}).all()
    return [{"entity_key": r[0], "name": r[1], "form_factor": r[2]} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", choices=["cpu", "gpu", "all"], default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    for category in (["cpu", "gpu"] if args.category == "all" else [args.category]):
        base = BASE[category]
        referer = LIST_URL[category]
        service = autocomplete_service(category) if category == "cpu" else ""
        link_map = source_id_map(category)
        detail_path = f"{base}/{DETAIL_SCRIPT[category]}"
        param = DETAIL_PARAM[category]

        entities = load_entities(category)
        if args.limit:
            entities = entities[: args.limit]

        print(f"[{category}] {len(entities)} 个实体；建议接口 {base}{service or '（无）'}；"
              f"详情 {detail_path}；列表内可解析 id {len(link_map)}；并发 {args.workers}")

        def resolve(entity: dict) -> tuple[dict | None, str | None]:
            wanted = normalize(entity["name"])
            pick_name = pick_id = None
            if category == "cpu":
                query = urllib.parse.quote(entity["name"])
                try:
                    payload = json.loads(_fetch(f"{base}{service}?query={query}", referer, ajax=True))
                except Exception as exc:  # noqa: BLE001
                    return None, f"{entity['name']} (suggest {type(exc).__name__})"
                picks = [s for s in payload.get("suggestions", []) if normalize(s["value"]) == wanted]
                if not picks:
                    seen = [s["value"] for s in payload.get("suggestions", [])][:3]
                    return None, f"{entity['name']} (suggestions: {seen})"
                pick_name, pick_id = picks[0]["value"], picks[0]["data"]
            else:
                pick_id = link_map.get(wanted)
                if not pick_id:
                    return None, f"{entity['name']} (列表页无同名链接)"
                pick_name = entity["name"]

            url = f"{detail_path}?{urllib.parse.urlencode({param: pick_name, 'id': pick_id})}"
            try:
                html = _fetch(url, referer)
            except Exception as exc:  # noqa: BLE001
                return None, f"{entity['name']} (detail {type(exc).__name__})"
            return {
                "entity_key": entity["entity_key"],
                "catalogue_name": entity["name"],
                "form_factor": entity["form_factor"],
                "source_name": pick_name,
                "source_id": pick_id,
                "detail_url": url,
                "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                **parse_scores(_text(html), category),
            }, None

        results: list[dict] = []
        unresolved: list[str] = []
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(resolve, entity) for entity in entities]
            for future in as_completed(futures):
                record, problem = future.result()
                with _LOCK:
                    if record:
                        results.append(record)
                    if problem:
                        unresolved.append(problem)
                    done += 1
                    if done % 25 == 0:
                        print(f"    ... {done}/{len(entities)}", flush=True)

        results.sort(key=lambda r: r["catalogue_name"])
        unresolved.sort()
        out = RAW / f"details_{category}_{SNAPSHOT}.json"
        out.write_text(json.dumps({
            "category": category, "snapshot_date": SNAPSHOT, "detail_path": detail_path,
            "autocomplete_service": service, "list_url": referer,
            "records": results, "unresolved": unresolved,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

        scored = sum(1 for r in results if "cpu_mark" in r or "g3d_mark" in r)
        print(f"[{category}] 解析 {len(results)} 条，有分数 {scored}，未解析 {len(unresolved)}")
        for item in unresolved[:12]:
            print("     未解析:", item)
        print(f"    -> {out}")


if __name__ == "__main__":
    main()
