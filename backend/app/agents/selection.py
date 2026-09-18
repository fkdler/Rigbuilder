"""Compact fact-only selection over this run's returned, evidence-bound rows.

The LLM chooses/ranks IDs. Identity and Claims come from the tool response and
still pass the ordinary TruthVerifier; this does not add catalogue candidates.
Price/measurement requests keep the full Recommendation protocol.
"""
import json
import re

from pglast import ast, parse_sql
from pglast.stream import RawStream

from app.schemas.recommendation import Claim, Recommendation
from app.services.routing import requested_component_roles
from app.tools.contracts import Observation
from app.services.requirements import office_default, office_utility


def fact_selection_roles(messages):
    newest = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    demand = re.sub(r"(?:不要|不许|不能|不得)(?:编造|虚构|保证)[^，。；,;]*|(?:没有|无)(?:价格|帧率)[^，。；,;]*", "", newest)
    if re.search(r"预算|价格|多少钱|便宜|性价比|跑分|实测|帧率|benchmark|price|budget|fps|\d+\s*元", demand, re.I):
        return set()
    if any(m["role"] == "system" and "Confirmed" in m["content"] for m in messages):
        return set()
    return requested_component_roles(newest)


def bound_discovery_sql(sql: str) -> str:
    """Limit fact-discovery alternatives, without changing global SQL_MAX_ROWS."""
    try:
        statements = parse_sql(sql)
        if len(statements) != 1 or not isinstance(statements[0].stmt, ast.SelectStmt):
            return sql
        statement = statements[0].stmt
        limit = statement.limitCount
        if limit is None or (isinstance(limit, ast.A_Const) and
                             (limit.isnull or isinstance(limit.val, ast.Integer) and limit.val.ival > 5)):
            cap = parse_sql("SELECT 1 LIMIT 5")[0].stmt
            statement.limitCount = cap.limitCount
            statement.limitOption = cap.limitOption
            return RawStream()(statement)
    except Exception:
        pass  # The ordinary read-only validator remains authoritative.
    return sql



def remember_selection_rows(tool_result, rows):
    raw = (tool_result.data or {}).get("observation")
    if not raw:
        return
    observation = Observation.model_validate(raw)
    if not observation.success:
        return
    columns = observation.columns or []
    if "entity_id" not in columns or "name" not in columns:
        return
    evidence = {e["evidence_id"]: e for e in observation.evidence if e.get("accepted") is True}
    bindings = {b.get("entity_id"): b.get("fields", {}) for b in observation.row_bindings or []}
    decision_rows = {c.entity_id: c.model_dump(mode="json") for c in observation.decision_context.candidates} if observation.decision_context else {}
    for values in observation.rows or []:
        row = dict(zip(columns, values))
        identity = str(row["entity_id"])
        fields = bindings.get(identity, {})
        facts = []
        for field, binding in fields.items():
            proof = evidence.get(binding.get("evidence_id"))
            if (not proof or proof.get("entity_id") != identity or proof.get("field_key") != field
                    or proof.get("normalized_value") != binding.get("value")
                    or proof.get("unit") != binding.get("unit")):
                continue
            facts.append({"claim_type": "fact", "entity_id": identity, "field_key": field,
                          "value": binding["value"], "unit": binding.get("unit"),
                          "evidence_ids": [binding["evidence_id"]]})
        roles = {f["field_key"].split(".", 1)[0] for f in facts} & {"gpu", "cpu", "model"}
        if len(roles) != 1:
            continue
        role = roles.pop()
        item = rows.setdefault(identity, {"candidate_id": identity, "name": row["name"], "role": role, "facts": {}})
        if identity in decision_rows:
            item["decision_context"] = decision_rows[identity]
        for fact in facts:
            # Validate now as well as after selection; the final verifier is authoritative.
            fact["value_type"] = "boolean" if isinstance(fact["value"], bool) else "number" if isinstance(fact["value"], (int, float)) else "string"
            try:
                claim = Claim.model_validate(fact)
            except ValueError:
                continue
            item["facts"][fact["field_key"]] = claim.model_dump(mode="json")


def ready_for_selection(rows, roles):
    return bool(roles) and all(sum(r["role"] == role and bool(r["facts"]) for r in rows.values()) >= 1 for role in roles)


def selection_schema(rows, roles):
    def item_schema(role_ids):
        return {"type": "object", "additionalProperties": False,
                "required": ["candidate_id", "score", "reasons"],
                "properties": {"candidate_id": {"type": "string", "enum": role_ids},
                               "score": {"type": "integer", "minimum": 0, "maximum": 100},
                               "reasons": {"type": "array", "minItems": 1, "maxItems": 2,
                                           "items": {"type": "string"}}}}
    if roles == {"cpu", "gpu"}:
        # Separate enum slots make a CPU-only build or duplicate CPU impossible
        # for schema-compliant models; no extra repair round is necessary.
        return {"type": "object", "additionalProperties": False, "required": ["cpu", "gpu"],
                "properties": {role: item_schema([key for key,row in rows.items() if row["role"] == role])
                               for role in ("cpu", "gpu")}}
    ids = [key for key,row in rows.items() if row["role"] in roles]
    return {"type": "object", "additionalProperties": False, "required": ["recommendations"],
            "properties": {"recommendations": {"type": "array", "minItems": 1, "maxItems": 3,
                                                  "items": item_schema(ids)}}}


def selection_brief(rows, roles):
    return json.dumps([{"candidate_id": key, "name": row["name"], "role": row["role"],
                        "facts": {field: {"value": fact["value"], "unit": fact["unit"], "evidence_ids": fact["evidence_ids"]}
                                  for field, fact in row["facts"].items()},
                        "decision_context": row.get("decision_context")}
                       for key, row in rows.items() if row["role"] in roles], ensure_ascii=False, separators=(",", ":"))


def materialize_selection(content, rows, roles, message=""):
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("Selection must be an object")
    if office_default(message) and roles == {"cpu"}:
        # Keep selection within this run's tool-returned pool. A model's rank or
        # preference for a familiar i9 must not discard a better office option.
        eligible = [(key, office_utility(row["facts"])) for key,row in rows.items() if row["role"] == "cpu"]
        eligible = sorted([(key, score) for key,score in eligible if score is not None], key=lambda x: (-x[1], rows[x[0]]["name"].casefold(), x[0]))
        if eligible:
            payload = {"recommendations": [{"candidate_id": key, "score": round(score),
                "reasons": ["按普通办公适配政策权衡已核验基础功耗和核心数；不是实测性能排名。"]}
                for key,score in eligible[:3]]}
    if roles == {"cpu", "gpu"} and isinstance(payload, dict) and set(payload) == roles:
        for role in ("cpu", "gpu"):
            if rows[payload[role]["candidate_id"]]["role"] != role:
                raise ValueError("Selection slot category mismatch")
        payload = {"recommendations": [payload["cpu"], payload["gpu"]]}
    if not isinstance(payload.get("recommendations"), list) or not 1 <= len(payload["recommendations"]) <= 3:
        raise ValueError("Selection size outside protocol limits")
    selected = []
    seen = set()
    for item in payload["recommendations"]:
        identity = item["candidate_id"]
        if identity in seen:
            raise ValueError("Duplicate selection")
        row = rows[identity]
        if row["role"] not in roles:
            raise ValueError("Selection does not match requested category")
        seen.add(identity)
        priority = ("gpu.vram_gib", "gpu.board_power_w", "gpu.memory_bandwidth_gb_s", "gpu.architecture",
                    "gpu.memory_type", "cpu.cores_total", "cpu.threads", "cpu.socket", "cpu.base_power_w",
                    "cpu.integrated_gpu", "cpu.architecture", "cpu.boost_clock_mhz", "entity.canonical_name")
        facts = sorted(row["facts"].values(), key=lambda c: priority.index(c["field_key"]) if c["field_key"] in priority else 99)
        selected.append({"candidate_id": identity, "candidate_type": "ai_model" if row['role'] == 'model' else "hardware", "name": row["name"],
                         "score": item["score"], "reasons": item["reasons"], "claims": facts[:6]})
    selected_roles = {rows[item["candidate_id"]]["role"] for item in selected}
    # Do not silently turn a complete pool into an incomplete gaming build.
    if roles == {"cpu", "gpu"} and not roles.issubset(selected_roles):
        raise ValueError("Selection omitted a required core component")
    return Recommendation.model_validate({"recommendations": selected})


def preserve_requested_pool(recommendation, rows, message):
    """Retain observed alternatives for multi-pick policy selection after verification.

    Added entries carry no model score; every claim still goes through TruthVerifier.
    No catalogue read or unobserved product is introduced here.
    """
    from app.services.hardware_requirements import parse_requirements, meets_vram
    from app.services.hardware_intent import allows_candidate
    from app.schemas.recommendation import RecommendationItem
    requirements = parse_requirements(message)
    roles = requested_component_roles(message)
    if requirements.count < 2 or len(roles) != 1 or not roles <= {'cpu', 'gpu'}:
        return recommendation
    items = list(recommendation.recommendations)
    seen = {str(item.candidate_id) for item in items}
    for identity, row in rows.items():
        if identity in seen or row['role'] not in roles or not allows_candidate(message, row['role'], row['name']):
            continue
        vram = row['facts'].get('gpu.vram_gib', {})
        if row['role'] == 'gpu' and not meets_vram(requirements, vram.get('value'), vram.get('unit')):
            continue
        facts = sorted(row['facts'].values(), key=lambda f: f['field_key'] != 'gpu.vram_gib')
        if facts:
            items.append(RecommendationItem(candidate_id=identity, candidate_type='hardware', name=row['name'],
                score=0, reasons=['保留本次查询返回的候选，供核验后按用户要求分别选择。'], claims=facts[:6]))
            seen.add(identity)
    return recommendation.model_copy(update={'recommendations': items[:10]})
