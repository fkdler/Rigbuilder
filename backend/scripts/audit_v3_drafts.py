"""Audit pending V3 bundles without changing or importing them.

The report is deliberately conservative: a record is only marked as a candidate
for review when its referenced source keys are present in the same draft tree.
It never changes ``status`` or review fields.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def audit(root: Path) -> dict[str, object]:
    bundles: list[tuple[Path, dict]] = []
    source_keys: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or "record_type" not in value:
            continue
        bundles.append((path, value))
        if value.get("record_type") == "source_document":
            payload = value.get("payload") or {}
            identity = payload.get("identity") or {}
            key = identity.get("entity_key") or payload.get("source_key")
            if key:
                source_keys.add(str(key))
    status = Counter(str(bundle.get("status", "missing")) for _, bundle in bundles)
    types = Counter(str(bundle.get("record_type")) for _, bundle in bundles)
    missing_sources: list[dict[str, object]] = []
    for path, bundle in bundles:
        refs: set[str] = set()
        payload = bundle.get("payload") or {}
        for item in payload.get("evidence", []) if isinstance(payload, dict) else []:
            if isinstance(item, dict) and item.get("source_key"):
                refs.add(str(item["source_key"]))
        missing = sorted(ref for ref in refs if ref not in source_keys)
        if missing:
            missing_sources.append({"path": path.relative_to(root).as_posix(), "missing_source_keys": missing})
    return {
        "root": str(root), "bundle_count": len(bundles),
        "status_counts": dict(status), "record_type_counts": dict(types),
        "source_document_keys": len(source_keys),
        "bundles_with_missing_sources": missing_sources,
        "dependency_complete_pending": sum(
            1 for path, bundle in bundles if str(bundle.get("status")) == "pending"
            and not any(item["path"] == path.relative_to(root).as_posix() for item in missing_sources)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(args.draft_dir.resolve())
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
