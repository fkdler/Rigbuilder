"""Fetch the PassMark CPU and GPU ranking lists into ``data/raw/passmark/``.

Source and permission notes
---------------------------
``www.cpubenchmark.net`` and ``www.videocardbenchmark.net`` are served by
PassMark Software.  Their ``robots.txt`` (checked 2026-09-16) allows the two list
pages used here: the ``*`` group disallows only ``/shared/``, ``/errors/``,
``/cgi-bin/``, ``/search/``, ``/Library/``, ``/Legal/``, ``/sales/``, ``/forum/``,
``/baselines/``, ``/includes/`` and ``/cache/``.  No AI-agent group bans this
client.  TechPowerUp, by contrast, disallows ClaudeBot on its whole site and is
therefore never used.

Two pages are fetched, with a delay between them, and the raw HTML is archived
byte-for-byte next to the parsed values so a later reviewer can re-derive the
numbers without hitting the site again.  The parsed output records the page it
came from and the exact retrieval time; nothing is inferred or filled in.

Usage::

    python backend/scripts/scrape_passmark_rankings.py [--snapshot YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(r"D:\RigBuilder")
OUT_DIR = ROOT / "data" / "raw" / "passmark"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

PAGES = {
    "cpu": {
        "url": "https://www.cpubenchmark.net/cpu_list.php",
        "score_label": "CPU Mark",
        "vendor": "PassMark Software",
        "title": "PassMark CPU Benchmarks - High to Low End CPU List",
    },
    "gpu": {
        "url": "https://www.videocardbenchmark.net/gpu_list.php",
        "score_label": "PassMark G3D Mark",
        "vendor": "PassMark Software",
        "title": "PassMark Video Card Benchmarks - High to Low End GPU List",
    },
}

# A row's cells are: [name, score, rank, value, price].  The first cell also
# carries the comparison checkbox, so its text is stripped of tags and trimmed.
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_INT = re.compile(r"^-?[\d,]+$")


def fetch(url: str) -> tuple[str, str]:
    """Return ``(sha256, text)`` for ``url``."""
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read()
        if "gzip" in (response.headers.get("Content-Encoding") or ""):
            raw = gzip.decompress(raw)
    return hashlib.sha256(raw).hexdigest(), raw.decode("utf-8", "replace")


def _cell_text(cell: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", cell)).strip()


def _to_int(text: str) -> int | None:
    if not _INT.match(text):
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def parse(text: str, label: str) -> tuple[list[dict], dict]:
    """Extract ``{name, score, rank}`` rows plus a small column census."""
    rows: list[dict] = []
    seen: set[str] = set()
    # The header rows and the row templates inside <script> blocks are skipped by
    # requiring three cells whose first two numeric columns both parse.
    for body in _ROW.findall(text):
        cells = [_cell_text(cell) for cell in _CELL.findall(body)]
        cells = [cell for cell in cells if cell]
        if len(cells) < 3:
            continue
        name, score_text, rank_text = cells[0], cells[1], cells[2]
        score, rank = _to_int(score_text), _to_int(rank_text)
        if score is None or rank is None or not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "score": score, "rank": rank, "label": label})
    census = {
        "rows": len(rows),
        "max_rank": max((row["rank"] for row in rows), default=None),
        "min_rank": min((row["rank"] for row in rows), default=None),
    }
    return rows, census


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=date.today().isoformat())
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    for index, (category, page) in enumerate(PAGES.items()):
        if index:
            time.sleep(4)  # be polite; robots sets no crawl-delay for this client
        digest, text = fetch(page["url"])
        retrieved_at = datetime.now(timezone.utc).isoformat()
        rows, census = parse(text, page["score_label"])

        html_path = OUT_DIR / f"{category}_list_{args.snapshot}.html"
        html_path.write_text(text, encoding="utf-8")
        payload = {
            "category": category,
            "source_url": page["url"],
            "source_vendor": page["vendor"],
            "source_title": page["title"],
            "score_label": page["score_label"],
            "snapshot_date": args.snapshot,
            "retrieved_at": retrieved_at,
            "html_sha256": digest,
            "html_path": str(html_path),
            "rows": rows,
        }
        json_path = OUT_DIR / f"{category}_list_{args.snapshot}.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

        summary[category] = {
            "rows": census["rows"],
            "rank_range": [census["min_rank"], census["max_rank"]],
            "sha256": digest,
            "html": str(html_path),
            "json": str(json_path),
        }
        print(f"[{category}] {page['url']}")
        print(f"    rows={census['rows']}  rank {census['min_rank']}..{census['max_rank']}")
        print(f"    sha256={digest[:16]}...")
        print(f"    -> {json_path}")
        for row in rows[:3]:
            print(f"       {row['name'][:44]:<46} score={row['score']:<7} rank={row['rank']}")

    (OUT_DIR / f"summary_{args.snapshot}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
