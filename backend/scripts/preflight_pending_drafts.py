"""只读预检：分析 data/v3/drafts 下全部 pending bundle 是否具备导入条件。

检查项：
  1. record_type 分布与 bundle 数量
  2. 被 Evidence 引用的 field_key 是否都已在 truth.field_definition 注册
  3. 被引用的 source_key 是否都已存在（或在本批内自带）
  4. 同一批内的自然键重复（import_v3 会因此拒绝整个发布包）
  5. 可推荐实体是否都带至少一条 evidence（import 硬性要求）
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text as sql

from app.db.session import engine

DRAFTS = Path(r"D:\RigBuilder\data\v3\drafts")

# 自然键字段：(record_type -> 取键函数说明)
NATURAL_KEY = {
    "hardware": "identity.entity_key",
    "model": "identity.entity_key + variants[].identity.entity_key",
    "organization": "identity.entity_key",
    "workload": "identity.entity_key",
    "source_document": "source_key",
    "field_definition": "field_key",
    "metric_definition": "metric_key",
    "benchmark_protocol": "protocol_key:version",
    "benchmark_run": "run_key",
    "price_snapshot": "price_key",
    "runtime_support": "snapshot_key",
    "rule_definition": "rule_key:version",
}


def load_files() -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(DRAFTS.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or "record_type" not in payload or "payload" not in payload:
            continue
        out.append((path, payload))
    return out


def main() -> None:
    files = load_files()
    print(f"待检 bundle：{len(files)} 个\n")

    kinds = Counter(f[1]["record_type"] for f in files)
    print("=== record_type 分布 ===")
    for kind, n in kinds.most_common():
        print(f"  {kind:<22} {n}")

    statuses = Counter(str(f[1].get("status")) for f in files)
    print("\n=== status 分布 ===")
    for st, n in statuses.most_common():
        print(f"  {st:<22} {n}")

    used_fields, used_sources = Counter(), Counter()
    wanted_entities, entities_with_evidence = set(), set()
    natural_keys = defaultdict(list)
    sample_shown = 0

    for path, bundle in files:
        p = bundle["payload"]
        rt = bundle["record_type"]

        if rt == "source_document":
            natural_keys[("source", p.get("source_key"))].append(path.name)

        identity = p.get("identity")
        if isinstance(identity, dict):
            natural_keys[("entity", identity.get("entity_key"))].append(path.name)
            if identity.get("recommendable"):
                wanted_entities.add(identity.get("entity_key"))
            for ref in p.get("evidence") or []:
                used_fields[ref.get("field_key")] += 1
                used_sources[ref.get("source_key")] += 1
                if ref.get("review_status") == "accepted":
                    entities_with_evidence.add(identity.get("entity_key"))
        if rt == "model":
            for variant in p.get("variants") or []:
                vident = variant.get("identity") or {}
                natural_keys[("entity", vident.get("entity_key"))].append(path.name)
                if vident.get("recommendable"):
                    wanted_entities.add(vident.get("entity_key"))
                for ref in variant.get("evidence") or []:
                    used_fields[ref.get("field_key")] += 1
                    used_sources[ref.get("source_key")] += 1
                    if ref.get("review_status") == "accepted":
                        entities_with_evidence.add(vident.get("entity_key"))
        if rt == "price_snapshot":
            natural_keys[("price", p.get("price_key"))].append(path.name)
        if rt == "field_definition":
            natural_keys[("field", p.get("field_key"))].append(path.name)
        if rt == "workload":
            for profile in p.get("profiles") or []:
                pass
            for ref in p.get("evidence") or []:
                used_fields[ref.get("field_key")] += 1
                used_sources[ref.get("source_key")] += 1

        if sample_shown < 2 and rt in {"hardware", "model"}:
            print(f"\n=== 样例 {rt}：{path.relative_to(DRAFTS)} ===")
            print(json.dumps(bundle, ensure_ascii=False)[:1100])
            sample_shown += 1

    with engine.connect() as conn:
        existing_fields = {r[0] for r in conn.execute(sql("SELECT field_key FROM truth.field_definition"))}
        existing_sources = {r[0] for r in conn.execute(sql("SELECT source_key FROM truth.source_document"))}

    batch_sources = {k[1] for k in natural_keys if k[0] == "source" and k[1]}
    missing_fields = sorted(f for f in used_fields if f not in existing_fields)
    missing_sources = sorted(s for s in used_sources if s and s not in existing_sources and s not in batch_sources)

    print("\n=== field_key 注册检查 ===")
    print(f"  被引用 {len(used_fields)} 个字段；库内已注册 {len(existing_fields)} 个")
    print(f"  ** 未注册（会直接导致导入失败）：{len(missing_fields)} 个 **")
    for f in missing_fields:
        print(f"     {f:<38} 被引用 {used_fields[f]} 次")

    print("\n=== source_key 检查 ===")
    print(f"  被引用 {len(used_sources)} 个来源；本批自带 {len(batch_sources)} 个；库内已有 {len(existing_sources)} 个")
    print(f"  ** 既不在库内也不在本批：{len(missing_sources)} 个 **")
    for s in missing_sources[:15]:
        print(f"     {s}")

    dups = {k: v for k, v in natural_keys.items() if len(v) > 1}
    print("\n=== 批内自然键重复 ===")
    print(f"  ** 重复 {len(dups)} 组（重复会让整个发布包被拒）**")
    for key, paths in list(dups.items())[:12]:
        print(f"     {key[0]}:{key[1]}  ← {paths[:3]}")

    no_evidence = sorted(wanted_entities - entities_with_evidence)
    print("\n=== 可推荐实体的 evidence 检查 ===")
    print(f"  可推荐实体 {len(wanted_entities)} 个；其中有 accepted evidence 的 {len(entities_with_evidence)} 个")
    print(f"  ** 缺 accepted evidence：{len(no_evidence)} 个（提交时 review_status 需一并改为 accepted）**")
    for e in no_evidence[:12]:
        print(f"     {e}")


if __name__ == "__main__":
    main()
