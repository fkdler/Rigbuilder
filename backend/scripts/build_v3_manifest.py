"""Build an accepted-release manifest from explicitly listed bundle files."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from app.data_contracts.v3 import Bundle, ReleaseManifest, SCHEMA_VERSION


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", required=True, type=Path)
    parser.add_argument("--release-key", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--all", action="store_true", help="include every JSON Bundle under --release-dir")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    root = args.release_dir.resolve(); entries = []
    if args.all and args.files:
        raise SystemExit("--all cannot be combined with explicit files")
    raw_files = sorted(root.rglob("*.json")) if args.all else [Path(raw) for raw in args.files]
    raw_files = [path for path in raw_files if path.name != "manifest.json"]
    if not raw_files:
        raise SystemExit("at least one Bundle file is required")
    for raw in raw_files:
        path = raw.resolve()
        if root not in path.parents: raise SystemExit(f"file is outside release directory: {path}")
        if "drafts" in {part.lower() for part in path.parts}: raise SystemExit(f"draft cannot enter release: {path}")
        bundle = Bundle.model_validate_json(path.read_text(encoding="utf-8"))
        if bundle.status != "accepted": raise SystemExit(f"bundle is not accepted: {path}")
        entries.append({"path": path.relative_to(root).as_posix(), "sha256": sha256(path.read_bytes()).hexdigest(),
                        "record_type": bundle.record_type, "status": "accepted"})
    manifest = ReleaseManifest(schema_version=SCHEMA_VERSION, release_key=args.release_key, created_at=datetime.now(timezone.utc),
        description=args.description, files=entries)
    target = root / "manifest.json"; target.write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__": raise SystemExit(main())
