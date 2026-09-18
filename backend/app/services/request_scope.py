"""Deterministic scope and pairing gates for verified recommendations.

For a whole-machine gaming request, one verified CPU plus one verified GPU is
not enough: they must also form a pair allowed by the versioned balance-rule
policy.  This post-fusion gate is deliberately independent of an individual
model's prose selection, so a familiar entry CPU cannot be paired with a
flagship-class GPU merely because both facts were verified.
"""
from __future__ import annotations

import re

from sqlalchemy import text

from app.services.build_assembler import _category_by_entity
from app.services.routing import requested_component_roles, requested_model_capabilities
from app.services.requirements import office_default, office_utility
from app.services.hardware_intent import allows_candidate, sku_requests
from app.services.hardware_requirements import parse_requirements, verified_requirement_issue


def _gaming_rule_key(message: str) -> str | None:
    """Choose the applicable, versioned gaming policy without inventing a SKU.

    A user who asks for a top/flagship gaming build has stated a high-tier
    preference even if they did not spell out a resolution.  ``gaming-4k`` is
    the most conservative existing high-tier policy, while explicit resolution
    and esports requests take their own policy rows.
    """
    value = re.sub(r"(?:不玩|不打|无需|不需要)(?:游戏|网游)|(?:no|not)\s+gaming", "", message.casefold())
    from app.services.execution_intent import execution_intent
    plan = execution_intent.get()
    if plan and plan.workload == "gaming" and not re.search(r"游戏|网游|3a|gaming|game", value):
        value += " gaming"
    if not re.search(r"游戏|网游|3a|gaming|game", value):
        return None
    if re.search(r"电竞|高刷|high.?refresh|esports", value):
        return "esports-highrefresh"
    if re.search(r"4k|2160p|顶尖|顶级|旗舰|极致|发烧|ultimate|flagship", value):
        return "gaming-4k"
    if re.search(r"1440p|2k", value):
        return "gaming-1440p"
    return "gaming-1080p"


def _active_balance_rule(session, rule_key: str) -> dict | None:
    row = session.execute(text("""
        SELECT rule_key, version, ratio_min, ratio_max, cpu_index_min, gpu_index_min,
               gpu_requirement, priority, rationale
        FROM agent_catalog.balance_rule_catalog
        WHERE rule_key = :rule_key AND active
    """), {"rule_key": rule_key}).mappings().first()
    return dict(row) if row is not None else None


def _performance_indices(session, categories: dict[str, dict]) -> dict[str, float]:
    """Read the policy index for candidates already verified by fusion."""
    keys = sorted({entry["entity_key"] for entry in categories.values() if entry.get("entity_key")})
    if not keys:
        return {}
    placeholders = ", ".join(f":key{index}" for index in range(len(keys)))
    rows = session.execute(text(
        "SELECT entity_key, index_100 FROM agent_catalog.performance_ranking "
        f"WHERE entity_key IN ({placeholders})"
    ), {f"key{index}": value for index, value in enumerate(keys)}).fetchall()
    return {str(row.entity_key): float(row.index_100) for row in rows if row.index_100 is not None}


def _mainstream_keys(session, categories):
    keys = sorted({entry['entity_key'] for entry in categories.values() if entry.get('entity_key')})
    if not keys:
        return set()
    placeholders = ', '.join(f':key{i}' for i in range(len(keys)))
    rows = session.execute(text('SELECT entity_key FROM agent_catalog.performance_ranking '
        f"WHERE entity_key IN ({placeholders}) AND tier_label IN ('mid', 'high')"),
        {f'key{i}': key for i, key in enumerate(keys)}).fetchall()
    return {str(row.entity_key) for row in rows}


def _pair_meets_rule(cpu_index: float | None, gpu_index: float | None, rule: dict) -> bool:
    if cpu_index is None or gpu_index is None or cpu_index <= 0:
        return False
    cpu_min, gpu_min = rule.get("cpu_index_min"), rule.get("gpu_index_min")
    if cpu_min is not None and cpu_index < float(cpu_min):
        return False
    if gpu_min is not None and gpu_index < float(gpu_min):
        return False
    ratio = gpu_index / cpu_index
    low, high = rule.get("ratio_min"), rule.get("ratio_max")
    return (low is None or ratio >= float(low)) and (high is None or ratio <= float(high))


def _gaming_pair(result, message: str, session):
    """Return the strongest policy-valid CPU/GPU pair from fused candidates.

    The full fused pool, including candidates initially below ``top_k``, is used
    because they are already truth-verified.  This lets a policy-valid CPU win
    over a model's arbitrary preference for an i3 without manufacturing a new
    candidate or bypassing verification.
    """
    rule_key = _gaming_rule_key(message)
    if rule_key is None or session is None:
        return None, None, None, None
    pool = [candidate for candidate in [*result.top_k, *getattr(result, "eliminated", [])]
            if not getattr(candidate, "elimination_reasons", [])]
    unique = {str(candidate.candidate_id): candidate for candidate in pool}
    candidates = list(unique.values())
    categories = _category_by_entity(session, [str(candidate.candidate_id) for candidate in candidates])
    rule = _active_balance_rule(session, rule_key)
    indices = _performance_indices(session, categories)
    if rule is None:
        return None, None, None, None
    candidates = [candidate for candidate in candidates
                  if allows_candidate(message, categories.get(str(candidate.candidate_id), {}).get("category"), candidate.canonical_name)
                  and not any(part in categories.get(str(candidate.candidate_id), {}).get("entity_key", "") for part in (":laptop:", ":mobile:"))]
    cpus = [candidate for candidate in candidates if categories.get(str(candidate.candidate_id), {}).get("category") == "cpu"]
    gpus = [candidate for candidate in candidates if categories.get(str(candidate.candidate_id), {}).get("category") == "gpu"]
    pairs = []
    for cpu in cpus:
        cpu_index = indices.get(categories[str(cpu.candidate_id)]["entity_key"])
        for gpu in gpus:
            gpu_index = indices.get(categories[str(gpu.candidate_id)]["entity_key"])
            if _pair_meets_rule(cpu_index, gpu_index, rule):
                pairs.append((cpu, gpu, cpu_index, gpu_index))
    if not pairs:
        return rule, None, None, indices

    from app.services.hardware_intent import performance_priority
    premium = performance_priority(message)
    if premium:
        pairs.sort(key=lambda item: (-item[3], -item[2], str(item[1].candidate_id), str(item[0].candidate_id)))
    else:
        pairs.sort(key=lambda item: (-(item[0].recommendation_score + item[1].recommendation_score),
                                    -item[3], -item[2], str(item[1].candidate_id), str(item[0].candidate_id)))
    return rule, *pairs[0], indices


def _with_gaming_trace(result, *, rule: dict, status: str, cpu=None, gpu=None,
                       cpu_index: float | None = None, gpu_index: float | None = None):
    details = {"rule_key": rule["rule_key"], "rule_version": rule["version"], "status": status}
    if cpu is not None and gpu is not None:
        details.update({"cpu_entity_id": str(cpu.candidate_id), "cpu_index_100": cpu_index,
                        "gpu_entity_id": str(gpu.candidate_id), "gpu_index_100": gpu_index,
                        "gpu_cpu_ratio": round(gpu_index / cpu_index, 3)})
    trace = result.trace.model_copy(update={
        "formula": {**result.trace.formula, "scenario_policy": "balance-rule-v1"},
        "input_summary": {**result.trace.input_summary, "gaming_balance": details},
        "tie_break_order": ["gaming_balance_policy", *result.trace.tie_break_order],
    })
    return trace


def check_requested_categories(verification, message, session):
    capabilities = requested_model_capabilities(message)
    if capabilities:
        verification = check_model_capabilities(verification, capabilities, session)
    from app.services.model_choices import excluded_model_ids
    excluded = set(excluded_model_ids.get())
    if excluded:
        verification = verification.model_copy(update={'candidates': [
            c.model_copy(update={'candidate_valid': False,
                'candidate_reason_codes': [*c.candidate_reason_codes, 'previously_recommended_model']})
            if str(c.candidate_id) in excluded else c for c in verification.candidates]})
    roles = requested_component_roles(message)
    if not roles:
        return verification
    if roles == {'model'}:
        return verification.model_copy(update={'candidates': [c if c.candidate_type == 'ai_model' else c.model_copy(update={
            'candidate_valid': False, 'candidate_reason_codes': [*c.candidate_reason_codes, 'requested_category_mismatch']})
            for c in verification.candidates]})
    categories = _category_by_entity(session, [str(c.candidate_id) for c in verification.candidates])
    candidates = []
    for candidate in verification.candidates:
        category = categories.get(str(candidate.candidate_id), {}).get("category")
        name = candidate.canonical_name or ""
        issue = verified_requirement_issue(message, category, candidate)
        if issue:
            candidate = candidate.model_copy(update={
                'candidate_valid': False,
                'candidate_reason_codes': [*candidate.candidate_reason_codes, issue],
            })
        if not allows_candidate(message, category, name):
            candidate = candidate.model_copy(update={
                "candidate_valid": False,
                "candidate_reason_codes": [*candidate.candidate_reason_codes, "explicit_sku_mismatch"],
            })
        entity_key = categories.get(str(candidate.candidate_id), {}).get("entity_key", "")
        if _gaming_rule_key(message) and any(part in entity_key for part in (":laptop:", ":mobile:")):
            candidate = candidate.model_copy(update={
                "candidate_valid": False,
                "candidate_reason_codes": [*candidate.candidate_reason_codes, "desktop_form_factor_required"],
            })
        if category not in roles:
            candidate = candidate.model_copy(update={
                "candidate_valid": False,
                "candidate_reason_codes": [*candidate.candidate_reason_codes, "requested_category_mismatch"],
            })
        candidates.append(candidate)
    return verification.model_copy(update={"candidates": candidates})


def select_role_coverage(result, message, top_k, session):
    """Keep both core roles when a global top-k would otherwise retain only CPUs."""
    roles = requested_component_roles(message)
    requirements = parse_requirements(message)
    if len(roles) == 1 and roles <= {'cpu', 'gpu'} and requirements.count > 1:
        role = next(iter(roles))
        pool = list({str(c.candidate_id): c for c in [*result.top_k, *result.eliminated]
                     if not c.elimination_reasons and allows_candidate(message, role, c.canonical_name)
                     and not verified_requirement_issue(message, role, c)}.values())
        categories = _category_by_entity(session, [str(c.candidate_id) for c in pool])
        pool = [c for c in pool if categories.get(str(c.candidate_id), {}).get('category') == role]
        slots = []
        selected = []
        if requirements.split_priorities:
            indices = _performance_indices(session, categories)
            mainstream = _mainstream_keys(session, categories)
            ranked = [c for c in pool if categories[str(c.candidate_id)]['entity_key'] in indices]
            ranked.sort(key=lambda c: (indices[categories[str(c.candidate_id)]['entity_key']], c.canonical_name))
            value_pick = next((c for c in ranked if categories[str(c.candidate_id)]['entity_key'] in mainstream), None)
            if value_pick:
                selected.append(value_pick)
                slots.append({'candidate_id': str(value_pick.candidate_id), 'label': '性价比方向（待核对售价）'})
            if ranked:
                if value_pick is None or indices[categories[str(ranked[-1].candidate_id)]['entity_key']] > indices[categories[str(value_pick.candidate_id)]['entity_key']]:
                    selected.append(ranked[-1])
                    slots.append({'candidate_id': str(ranked[-1].candidate_id), 'label': '高性能方向'})
        else:
            selected = pool[:min(10, requirements.count)]
        summary = {'requested': requirements.count, 'returned': len(selected), 'slots': slots}
        trace = result.trace.model_copy(update={'input_summary': {**result.trace.input_summary, 'requested_selection': summary}})
        return result.model_copy(update={'top_k': selected, 'trace': trace})
    if office_default(message) and roles == {"cpu"}:
        candidates = []
        audit = {}
        for candidate in result.top_k:
            facts = {c.claim.field_key: c.verification.canonical_value for c in candidate.claims
                     if c.verification.status == "supported" and c.verification.valid_evidence_ids}
            fit = office_utility(facts)
            if fit is None:
                fit = 0
            has_preferences = any(c.kind == "preference" for c in candidate.constraints)
            # Truth/hard constraints already gated these candidates. A known
            # fit difference outranks consensus and self-rated model scores.
            score = .8 * fit + .2 * candidate.preference_utility * 100 if has_preferences else fit
            audit[str(candidate.candidate_id)] = {"office_fit": fit, "score": round(score, 2)}
            candidates.append(candidate.model_copy(update={"recommendation_score": round(score, 2)}))
        candidates.sort(key=lambda c: (-c.recommendation_score, c.canonical_name.casefold(), str(c.candidate_id)))
        trace = result.trace.model_copy(update={"formula": {**result.trace.formula,
            "scenario_policy": "office-fit-v1", "scenario_score": "0.8*office_fit+0.2*confirmed_preference if present; otherwise office_fit",
            "office_fit": "100*(0.70*min(1,65/base_power_w)+0.30*min(1,cores/4)*min(1,8/cores)); policy, not benchmark"},
            "input_summary": {**result.trace.input_summary, "scenario_scores": audit},
            "tie_break_order": ["scenario_score_desc", "canonical_name_asc", "candidate_id_asc"]})
        return result.model_copy(update={"top_k": candidates[:top_k], "trace": trace})
    if roles != {"cpu", "gpu"}:
        return result.model_copy(update={"top_k": result.top_k[:top_k]})

    # A gaming build is a coupled decision, not two independent component
    # recommendations.  This runs after fact verification and fusion, so it
    # can only retain already verified candidates.  In particular, an entry
    # CPU plus a high-end GPU must never reach the core-build assembler merely
    # because it happened to be the first CPU/GPU pair in global fusion order.
    rule, cpu, gpu, *pair_data = _gaming_pair(result, message, session)
    if rule is not None:
        if cpu is not None and gpu is not None:
            cpu_index, gpu_index = pair_data[:2]
            trace = _with_gaming_trace(
                result, rule=rule, status="selected", cpu=cpu, gpu=gpu,
                cpu_index=cpu_index, gpu_index=gpu_index,
            )
            return result.model_copy(update={"top_k": [gpu, cpu], "trace": trace})

        # Keep a verified user-pinned CPU when no GPU can satisfy pairing policy.
        # Never resurrect candidates rejected for Truth or hard constraints.
        pool = [c for c in result.top_k if not getattr(c, "elimination_reasons", [])]
        categories = _category_by_entity(session, [str(c.candidate_id) for c in pool])
        indices = pair_data[0] if pair_data else {}
        required_cpu = sku_requests(message)["cpu"]["required"]
        role = "cpu" if required_cpu else "gpu"
        candidates = [c for c in pool
                      if categories.get(str(c.candidate_id), {}).get("category") == role
                      and allows_candidate(message, role, c.canonical_name)
                      and not any(part in categories.get(str(c.candidate_id), {}).get("entity_key", "") for part in (":laptop:", ":mobile:"))]
        candidates.sort(key=lambda c: (-indices.get(categories[str(c.candidate_id)]["entity_key"], -1),
                                       -c.recommendation_score, str(c.candidate_id)))
        trace = _with_gaming_trace(result, rule=rule, status="no_valid_pair")
        return result.model_copy(update={"top_k": candidates[:1], "trace": trace})

    categories = _category_by_entity(session, [str(c.candidate_id) for c in result.top_k])
    selected = []
    for role in ("gpu", "cpu"):
        candidate = next((c for c in result.top_k
                          if categories.get(str(c.candidate_id), {}).get("category") == role), None)
        if candidate is not None:
            selected.append(candidate)
    ids = {c.candidate_id for c in selected}
    selected.extend(c for c in result.top_k if c.candidate_id not in ids)
    keep = {c.candidate_id for c in selected[:max(2, top_k)]}
    # Preserve global fusion order/scores; build assembly independently groups roles.
    return result.model_copy(update={"top_k": [c for c in result.top_k if c.candidate_id in keep]})


def check_model_capabilities(verification, capabilities, session):
    from app.services.model_advice import capability_claims
    from app.schemas.recommendation import Recommendation
    from app.verification import TruthVerifier
    from app.verification.repository import SqlAlchemyTruthRepository
    candidates = []
    for candidate in verification.candidates:
        claims = capability_claims(session, candidate.candidate_id, capabilities) if candidate.candidate_type == "ai_model" else []
        if not claims:
            candidates.append(candidate.model_copy(update={"candidate_valid": False,
                "candidate_reason_codes": [*candidate.candidate_reason_codes, "required_model_capability_unverified"]}))
            continue
        payload = Recommendation.model_validate({"recommendations": [{"candidate_id": str(candidate.candidate_id),
            "candidate_type": "ai_model", "name": candidate.canonical_name or "model", "score": 0,
            "reasons": ["Capability gate"], "claims": [*[x.claim.model_dump(mode="json") for x in candidate.claims], *claims]}]})
        checked = TruthVerifier(SqlAlchemyTruthRepository(session)).verify(payload).candidates[0]
        required = {"model.capability." + cap for cap in capabilities}
        proven = {c.claim.field_key for c in checked.claims if c.status == "supported" and c.valid_evidence_ids}
        if not candidate.candidate_valid or not required.issubset(proven):
            checked = checked.model_copy(update={"candidate_valid": False,
                "candidate_reason_codes": [*candidate.candidate_reason_codes, "required_model_capability_unverified"]})
        candidates.append(checked)
    return verification.model_copy(update={"candidates": candidates})
