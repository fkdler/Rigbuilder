"""Generate committed data and decision-context JSON Schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.data_contracts.v3 import Bundle, ReleaseManifest
from app.schemas.decision_context import DecisionContext


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/v3/schemas"))
    parser.add_argument(
        "--decision-context-output",
        type=Path,
        default=Path("docs/API_documents/Decision_Context_V4.4.schema.json"),
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, model in (("bundle.schema.json", Bundle), ("release-manifest.schema.json", ReleaseManifest)):
        (args.output / name).write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    args.decision_context_output.parent.mkdir(parents=True, exist_ok=True)
    args.decision_context_output.write_text(
        json.dumps(DecisionContext.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
