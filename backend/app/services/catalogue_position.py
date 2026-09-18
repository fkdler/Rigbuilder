"""Evidence-bound specification positions. Never manufacture performance rankings."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import select, text

from app.models.truth_v3 import CatalogEntity, CpuSpec, GpuSpec, EvidenceClaim, SourceDocument, DatasetRelease
from app.services.requirements import office_utility

POLICY = "catalogue-position-v2"
FIELDS = {
    "cpu": {"architecture": None, "cores_total": None, "base_power_w": "w", "integrated_gpu": None},
    "gpu": {"architecture": None, "vram_gib": "gib", "board_power_w": "w"},
}


def _same(a, b):
    if a is None or b is None or isinstance(a, bool) != isinstance(b, bool):
        return False
    try:
        return float(a) == float(b)
    except (ValueError, TypeError):
        return str(a).casefold().strip() == str(b).casefold().strip()


def positions(records):
    """Pure calculation over already validated inputs, suitable for replay."""
    out = {}
    for record in records:
        category, facts = record["category"], record["facts"]
        architecture = facts.get(category + ".architecture", {}).get("value")
        peers = [r for r in records if architecture is not None and r["category"] == category
                 and r["facts"].get(category + ".architecture", {}).get("value") == architecture]
        dimensions = {}
        for field, fact in facts.items():
            if field.endswith(("architecture", "integrated_gpu")):
                continue
            sample = [r for r in peers if field in r["facts"] and r["facts"][field]["unit"] == fact["unit"]]
            if len(sample) < 2:
                continue
            value = float(fact["value"])
            inputs = [{"entity_id": r["entity_id"], "value": r["facts"][field]["value"],
                       "evidence_ids": sorted(set(r["facts"][field]["evidence_ids"] +
                           r["facts"][category + ".architecture"]["evidence_ids"]))} for r in sample]
            inputs.sort(key=lambda r: r["entity_id"])
            dimensions[field] = {"rank": 1 + sum(float(i["value"]) > value for i in inputs),
                "percentile": round(sum(float(i["value"]) < value for i in inputs) / (len(inputs)-1), 4),
                "sample_count": len(inputs), "direction": "higher_value", "inputs": inputs,
                "calculation_id": sha256(json.dumps([POLICY, field, architecture, inputs], sort_keys=True).encode()).hexdigest()[:20]}
        performance = record.get("performance")
        label = "库内同架构规格位置；只描述规格分布，不代表性能、市场档次或适用性"
        out[record["entity_id"]] = {**record, "position": {
            "basis": "specification_position", "cohort": [category, architecture], "generation_basis": "architecture_proxy",
            "cohort_size": len(peers), "dimensions": dimensions,
            "label": label},
            "performance": performance,
            "office_fit": {"policy_version": "office-fit-v1", "score": office_utility(facts) if category == "cpu" else None,
                           "meaning": "政策适配分，不是实测性能或性价比分"}}
    return out


def load_positions(session):
    """Read-only calculation snapshot, cached only within this DB session."""
    cached = session.info.get(POLICY)
    if cached is not None:
        return cached
    release = session.scalar(select(DatasetRelease).where(DatasetRelease.status == "accepted")
        .order_by(DatasetRelease.applied_at.desc(), DatasetRelease.release_key.desc()).limit(1))
    if release is None:
        return {"policy_version": POLICY, "release_key": None, "data_cutoff": None, "candidates": {}}
    now = datetime.now(timezone.utc)
    records, dates = [], []
    for category, model in (("cpu", CpuSpec), ("gpu", GpuSpec)):
        specs = session.execute(select(model, CatalogEntity).join(CatalogEntity, CatalogEntity.id == model.hardware_id)
                                .where(CatalogEntity.recommendable.is_(True))).all()
        ids = [entity.id for _, entity in specs]
        evidence = session.execute(select(EvidenceClaim, SourceDocument).join(SourceDocument, SourceDocument.id == EvidenceClaim.source_id)
            .where(EvidenceClaim.entity_id.in_(ids), EvidenceClaim.review_status == "accepted",
                   EvidenceClaim.field_key.in_([category + "." + field for field in FIELDS[category]]))).all()
        by_key = {}
        for proof, source in evidence:
            observed = proof.collected_at or source.accessed_at
            if observed and observed > now:
                continue
            by_key.setdefault((proof.entity_id, proof.field_key), []).append((proof, observed))
        for spec, entity in specs:
            facts, missing = {}, []
            for attr, unit in FIELDS[category].items():
                key = category + "." + attr
                value = getattr(spec, attr)
                matching = [(p, d) for p,d in by_key.get((entity.id, key), [])
                            if p.unit_key == unit and _same(value, p.normalized_value)]
                # Conflicting accepted records cannot establish a calculation input.
                if not matching or len(matching) != len(by_key.get((entity.id, key), [])):
                    missing.append(key)
                    continue
                seen = [d for _,d in matching if d]
                cutoff = min(seen).astimezone(timezone.utc).isoformat() if len(seen) == len(matching) else None
                if cutoff:
                    dates.append(cutoff)
                facts[key] = {"value": float(value) if unit or isinstance(value, (int,float)) else value,
                    "unit": unit, "evidence_ids": sorted(str(p.id) for p,_ in matching),
                    "observed_at": cutoff, "confidence": min(float(p.confidence) for p,_ in matching)}
            records.append({"entity_id": str(entity.id), "category": category, "facts": facts,
                "missing_fields": missing + ["release_date"],
                "confidence": {"meaning": "required fact evidence coverage, not model certainty",
                               "coverage": len(facts)/len(FIELDS[category])}})
    # Published aggregate scores plus the project's policy-anchored index ride
    # alongside evidence-backed facts instead of being disguised as facts.  A
    # candidate without that surface keeps benchmark.performance in missing_fields.
    performance_by_entity = {}
    for row in session.execute(text("""
            SELECT e.id::text AS entity_id, r.score, r.index_100, r.tier_label, r.population_size,
                   r.cohort_size, r.market_percentile, r.class_percentile, s.source_key
            FROM agent_catalog.performance_ranking r
            JOIN truth.catalog_entity e ON e.entity_key = r.entity_key
            LEFT JOIN LATERAL (
                SELECT pm.source_key FROM agent_catalog.performance_metric pm
                WHERE pm.entity_key = r.entity_key LIMIT 1
            ) s ON true""")):
        performance_by_entity[row.entity_id] = {
            "basis": "policy_anchored_index", "policy_version": "performance-anchor-v1",
            "source_key": row.source_key,
            "score": float(row.score), "index_100": float(row.index_100),
            "tier_label": row.tier_label,
            "population_size": float(row.population_size) if row.population_size is not None else None,
            "cohort_size": int(row.cohort_size),
            "market_percentile": float(row.market_percentile) if row.market_percentile is not None else None,
            "class_percentile": float(row.class_percentile) if row.class_percentile is not None else None,
            "meaning": ("index_100 与 tier_label 是本项目对发布方原始分数进行人工锚定和线性插值得到的产品政策；"
                        "不是发布方百分位、不是本项目实测，也不直接代表具体工作负载表现。"
                        "market_percentile/class_percentile 是独立展示的发布方排名派生值；"
                        "catalogue cohort_size 与发布方 population_size 不是同一总体。"),
        }
    for record in records:
        performance = performance_by_entity.get(record["entity_id"])
        record["performance"] = performance
        if performance is None:
            record["missing_fields"] = list(record["missing_fields"]) + ["benchmark.performance"]
    result = {"policy_version": POLICY, "release_key": release.release_key,
              "snapshot_applied_at": release.applied_at.isoformat(),
              "data_cutoff": min(dates) if dates else None, "candidates": positions(records)}
    session.info[POLICY] = result
    return result


def tool_context(session, ids):
    if session is None or not hasattr(session, "info"):
        return None
    snapshot = load_positions(session)
    # Shared cohort inputs occur once, rather than once per returned candidate.
    calculations, candidates = {}, []
    for identity in ids:
        record = snapshot["candidates"].get(str(identity))
        if record is None:
            continue
        dimensions = {}
        for field, dimension in record["position"]["dimensions"].items():
            calculations[dimension["calculation_id"]] = {"field_key": field,
                "input_evidence_ids": sorted({e for i in dimension["inputs"] for e in i["evidence_ids"]})}
            dimensions[field] = {k:v for k,v in dimension.items() if k != "inputs"}
        candidates.append({**record, "position": {**record["position"], "dimensions": dimensions}})
    return {**snapshot, "candidates": candidates, "calculations": calculations}


def verify_position(assertion, snapshot):
    """Exact replay gate for derived assertions; no LLM-supplied score is trusted."""
    if assertion.get("policy_version") != POLICY or assertion.get("release_key") != snapshot.get("release_key"):
        return False
    canonical = snapshot.get("candidates", {}).get(assertion.get("entity_id"))
    if not canonical:
        return False
    expected = canonical["position"]["dimensions"].get(assertion.get("field_key"))
    return bool(expected and assertion.get("calculation") == expected)
