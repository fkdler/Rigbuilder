"""Convert legacy standardized JSON to review-only V3 drafts.

No output from this command is eligible for import until a human moves it into
an accepted release and regenerates a manifest.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from app.data_contracts.v3 import Bundle


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(root: Path, name: str) -> list[dict]:
    return json.loads((root / name).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/standardized"))
    parser.add_argument("--output", type=Path, default=Path("data/v3/drafts/legacy"))
    args = parser.parse_args()
    hardware = load(args.input, "hardware.json")
    models = load(args.input, "ai_models.json")
    variants = load(args.input, "model_variants.json")
    evidence = load(args.input, "evidence.json")
    benchmarks = load(args.input, "benchmarks.json")

    def hardware_key(item: dict) -> str:
        return f"hardware:{slug(item['manufacturer'])}:{slug(item['name'])}"

    def model_key(publisher: str, name: str) -> str:
        return f"model:{slug(publisher)}:{slug(name)}"

    evidence_by_entity: dict[str, list[dict]] = defaultdict(list)
    sources: dict[str, dict] = {}
    for item in evidence:
        key_data = item["entity_key"]
        if item["entity_type"] == "hardware":
            entity_key = f"hardware:{slug(key_data['manufacturer'])}:{slug(key_data['name'])}"
        elif item["entity_type"] == "ai_model":
            entity_key = model_key(key_data["publisher"], key_data["name"])
        elif item["entity_type"] == "model_variant":
            entity_key = (f"variant:{slug(key_data['model_publisher'])}:{slug(key_data['model_name'])}:"
                          f"{slug(key_data['format'])}:{slug(key_data['quantization'])}")
        else:
            continue
        url = item["source_url"]
        source_key = "source:legacy:" + slug(urlparse(url).netloc + urlparse(url).path)[:180]
        sources.setdefault(source_key, {
            "schema_version": "3.0", "record_type": "source_document", "status": "pending",
            "payload": {"source_key": source_key, "title": f"Legacy source: {item.get('publisher') or urlparse(url).netloc}",
                "publisher": item.get("publisher"), "url": url, "source_type": "official" if item.get("provenance") == "official" else "community",
                "retrieved_at": "1970-01-01T00:00:00Z"},
            "review_notes": ["retrieved_at is an explicit sentinel; verify before acceptance"],
        })
        field_map = {"vram_gb": "gpu.vram_gib", "tdp_w": "gpu.board_power_w", "architecture": "gpu.architecture",
                     "memory_type": "gpu.memory_type", "parameter_b": "model.parameter_count",
                     "context_length": "model.context_length", "license": "model.license"}
        evidence_by_entity[entity_key].append({
            "source_key": source_key,
            "field_key": field_map.get(item["field"], f"legacy.{item['field']}"),
            "raw_value": item.get("raw_value"), "normalized_value": item.get("normalized_value"),
        })

    for index, bundle in enumerate(sources.values(), 1):
        Bundle.model_validate(bundle)
        dump(args.output / "sources" / f"{index:04d}.json", bundle)

    organizations = sorted({item["manufacturer"] for item in hardware} | {item["publisher"] for item in models})
    for name in organizations:
        bundle = {"schema_version": "3.0", "record_type": "organization", "status": "pending",
                  "payload": {"identity": {"entity_key": f"org:{slug(name)}", "canonical_name": name,
                  "entity_type": "organization", "recommendable": False}},
                  "review_notes": ["legacy organization stub; enrich before acceptance"]}
        Bundle.model_validate(bundle); dump(args.output / "organizations" / f"{slug(name)}.json", bundle)

    for item in hardware:
        key = hardware_key(item)
        kind = {"GPU": "desktop_gpu", "CPU": "cpu", "RAM": "memory", "SSD": "storage", "PSU": "psu"}.get(item["type"], "platform")
        payload: dict = {"identity": {"entity_key": key, "canonical_name": item["name"], "entity_type": "hardware",
                        "release_date": item.get("release_date")}, "hardware_type": kind,
                        "manufacturer_key": f"org:{slug(item['manufacturer'])}", "evidence": evidence_by_entity[key]}
        if kind.endswith("gpu"):
            payload["gpu_spec"] = {"architecture": item.get("architecture"),
                "vram_gib": item.get("vram_gb"), "memory_type": item.get("memory_type"),
                "board_power_w": item.get("tdp_w")}
        elif kind == "cpu":
            payload["cpu_spec"] = {"architecture": item.get("architecture"), "tdp_w": item.get("tdp_w")}
        bundle = {"schema_version": "3.0", "record_type": "hardware", "status": "pending", "payload": payload,
                  "review_notes": ["legacy normalized data; all fields require V3 review"]}
        Bundle.model_validate(bundle); dump(args.output / "hardware" / f"{slug(item['manufacturer']+'-'+item['name'])}.json", bundle)

    variants_by_model: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in variants:
        variants_by_model[(item["model_publisher"], item["model_name"])].append(item)
    for item in models:
        key = model_key(item["publisher"], item["name"])
        converted_variants = []
        for variant in variants_by_model[(item["publisher"], item["name"])]:
            variant_key = f"variant:{slug(item['publisher'])}:{slug(item['name'])}:{slug(variant['format'])}:{slug(variant['quantization'])}"
            converted_variants.append({"identity": {"entity_key": variant_key,
                "canonical_name": f"{item['name']} {variant['quantization']} {variant['format']}", "entity_type": "model_variant"},
                "quantization": variant["quantization"], "format": variant["format"],
                "quantization_method": variant["quantization"], "weight_format": variant["format"],
                "file_size_display": f"{variant['file_size_gb']} GB (legacy value; GB/GiB and precision unverified)",
                "source_url": variant.get("source"), "evidence": evidence_by_entity[variant_key]})
        capabilities = [{"capability_key": "vision", "status": "supported" if item.get("supports_vision") else "unknown"},
                        {"capability_key": "code", "status": "supported" if item.get("supports_code") else "unknown"}]
        bundle = {"schema_version": "3.0", "record_type": "model", "status": "pending",
                  "payload": {"identity": {"entity_key": key, "canonical_name": item["name"], "entity_type": "ai_model"},
                    "publisher_key": f"org:{slug(item['publisher'])}", "parameter_count": round(item["parameter_b"] * 1_000_000_000),
                    "total_parameters": round(item["parameter_b"] * 1_000_000_000), "model_type": "dense",
                    "context_length": item.get("context_length"), "context_length_tokens": item.get("context_length"), "license_name": item.get("license"),
                    "capabilities": capabilities, "variants": converted_variants, "evidence": evidence_by_entity[key]},
                  "review_notes": ["legacy false capability values became unknown", "variant file_size_bytes intentionally omitted pending verification"]}
        Bundle.model_validate(bundle); dump(args.output / "models" / f"{slug(item['publisher']+'-'+item['name'])}.json", bundle)

    report = {"status": "draft_only", "hardware": len(hardware), "models": len(models), "model_variants": len(variants),
              "evidence_input": len(evidence), "evidence_converted": sum(len(items) for items in evidence_by_entity.values()),
              "benchmarks": len(benchmarks), "source_drafts": len(sources),
              "warnings": ["No draft is importable", "false capabilities were converted to unknown",
                           "file_size_gb was retained as display text, never converted to exact bytes"]}
    dump(args.output / "conversion-report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
