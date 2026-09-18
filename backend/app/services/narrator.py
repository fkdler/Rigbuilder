"""Narrator: the conclusion-shaped, natural-language view of a fused result.

The Narrator reuses one of the resident fusion models (default agent-b, see
NARRATOR_PROFILE_ID).  It only receives a read-only brief assembled from
post-verification data and is strictly forbidden from changing candidates,
ranking, scores, facts, risks or evidence status.  Failures, timeouts and
invalid output always degrade to the deterministic template, never to a
half-authored result.

Plan_V4.1 changed *what* the narrator is asked to produce, not *what it may
change*.  Previously it was asked for a per-candidate explanation of the form
"why this candidate is recommended, 2-3 sentences", fed by a flat list of
``field_key: value`` pairs.  The measured result was a competent but lifeless
recitation of specifications -- the user's words: "有真理由但不够好，读起来生硬或
像罗列".  It now receives the user's question, the constraint verdicts, the
eliminated candidates and the dimensions the candidates actually differ on, and
it must produce a headline plus causal reasons that tie a verified fact to the
stated need.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.config import Settings, get_settings
from app.inference.profiles import get_profile
from app.schemas.fusion import (
    AlternativeNote,
    CoreBuild,
    FusionResult,
    NarratorCandidateText,
    NaturalLanguage,
    PrimaryRecommendation,
)
from app.services.llm import LLMServiceError, complete_chat
from app.services.timing import timing
from app.services.requirements import office_default
from app.services.assertions import explanation_issue

NARRATOR_SYSTEM_PROMPT = """You are RigBuilder's result narrator. Below is a read-only,
post-verification fusion outcome, plus the user's own question. Write the final
answer in Chinese, for a PC hardware buyer.

Hard rules - never break these:
- Never change candidates, their ranking, scores, credibility, facts or risk status.
- Never add a candidate that is absent from the brief.
- Every number you write must come from the brief; never invent, convert or round
  to a different value.
- Answer with exactly one JSON object, no markdown fences:
  {"headline": "<one sentence: what you recommend and for whom>",
   "primary": {"candidate_id": "<uuid from the brief>", "name": "<exact name from brief>", "reasons": ["<2-4 reasons>"]},
   "alternatives": [{"candidate_id": "<uuid from the brief>", "name": "<exact name from brief>", "note": "<one sentence: how it differs from the primary>"}],
   "caveats": ["<what limits this answer>"],
   "per_candidate": [{"candidate_id": "<uuid from the brief>", "explanation": "<2-3 sentences>"}]}
- primary must be the top candidate of the brief, and its candidate_id must be that
  candidate's id. per_candidate must contain exactly one entry per candidate listed
  in the brief.

How to write the reasons - this is the part that matters most:
- Write connected causal prose, with no repeated generic disclaimers. The UI adds one advice notice.
- Keep concrete missing parts or incompatibilities in caveats; do not replace evidence with warnings.
- Each reason must be a causal sentence joining a verified fact to the user's stated
  need, not a bare value. Write "显存 8GB 正好覆盖 1080p 网游的纹理需求，所以不必为更大的显存多花钱",
  not "显存 8GB，价格 2599". A reason that only lists specifications has failed.
- Use the dimensions the user actually asked about, and prefer the brief's
  "shared_dimensions": when several candidates are listed, say why the primary wins
  on those dimensions instead of reciting all of its specs.
- Never repeat the same number in two reasons, and never enumerate every verified
  claim: pick the two to four that actually decide the choice.
- With a single candidate, explain why it fits; do not invent a comparison.

Honesty about coverage:
- When model coverage is partial, state it plainly; never claim multi-model consensus
  the brief does not show.
- Move anything the brief marks missing or eliminated into caveats, with the reason
  it gives, so the user can see what this answer does not cover.
- Put the user's request for a whole machine into caveats when the brief shows only
  single components: say which parts the Catalogue can verify and which it cannot.
- When the brief carries a "core_build", this IS a whole-machine answer: name its core
  components together in the headline (for example "这套配置以 RTX 5070 Ti 显卡和
  Ryzen 7 9800X3D 为核心"). Its "supporting" entries are the backend's own derivation -
  you may name them, but never restate one as your own finding, never add a manufacturer
  or model number to one, and never contradict one. Anything in "gaps" belongs in caveats.
"""

# Kept for callers that reference the old name.
_MAX_NARRATED_CANDIDATES = 5
_NARRATOR_TIMEOUT = 60.0
# The conclusion shape carries more text than the old overview+per_candidate one
# (headline, reasons, alternatives, caveats and the per-candidate explanations),
# so the ceiling has to fit a complete JSON object: a truncated reply fails to
# parse and silently degrades to the template.
_NARRATOR_MAX_TOKENS = 1_400

# Digits that describe the user's requirement rather than a product fact. Kept
# narrow on purpose: a bare "60" could be a price, so only digits carrying a
# requirement unit are dropped.
_NON_FACT_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:p\b|hz\b|fps\b|帧|寸|英寸)", re.IGNORECASE)

_FIELD_LABELS: dict[str, str] = {
    "gpu.vram_gib": "显存",
    "gpu.board_power_w": "功耗",
    "gpu.architecture": "架构",
    "gpu.memory_type": "显存类型",
    "gpu.memory_bandwidth_gb_s": "显存带宽",
    "gpu.memory_bus_width_bit": "显存位宽",
    "gpu.pcie_generation": "PCIe 版本",
    "gpu.pcie_lanes": "PCIe 通道",
    "gpu.ecc_support": "ECC 支持",
    "cpu.cores_total": "核心数",
    "cpu.threads": "线程数",
    "cpu.socket": "插槽",
    "cpu.architecture": "架构",
    "cpu.base_clock_mhz": "基础频率",
    "cpu.boost_clock_mhz": "加速频率",
    "cpu.base_power_w": "基础功耗",
    "model.total_parameters": "参数量",
    "model.context_length_tokens": "上下文长度",
    "model.license_name": "许可证",
    "price": "价格",
}


def _extract_numbers(text: str) -> set[str]:
    """Numbers that could be a factual claim about a product.

    Resolution and refresh-rate notations describe the *requirement*, not a
    catalogue fact: "1080p" and "144Hz" cannot be "wrong" against the brief, but
    treating their digits as an unverifiable claim rejected otherwise-readable
    narrations and pushed them to the deterministic template -- part of why the
    measured output read as lifeless. Unit-bearing digits like "12GB" are still
    treated as claims and must appear in the brief.
    """
    cleaned = _NON_FACT_NUMBER.sub("", text)
    # 3A names a game category, not a product specification.
    cleaned = re.sub(r"(?<![\dA-Za-z])3[Aa](?=\s|游戏|大作|$)", "", cleaned)
    return set(re.findall(r"\d+(?:\.\d+)?", cleaned))


def _format_number(value: float) -> set[str]:
    if value == int(value):
        # "PCIe 4.0" is the same value as 4; accepting the decimal spelling costs
        # no protection and avoids rejecting a correct sentence.
        return {str(int(value)), f"{value:.0f}", f"{int(value)}.0"}
    return {f"{value:g}", f"{value:.1f}", f"{value:.2f}"}


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, Decimal):
        normalised = value.normalize()
        return f"{normalised:f}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _display_unit(unit: str | None) -> str:
    return {
        "gib": "GiB", "mib": "MiB", "w": "W", "gb_s": "GB/s",
        "mhz": "MHz", "ghz": "GHz", "cny": "CNY",
    }.get((unit or "").casefold(), unit or "")


def _label_for(field_key: str | None, claim_type: str) -> str:
    if field_key and field_key in _FIELD_LABELS:
        return _FIELD_LABELS[field_key]
    return field_key or claim_type


def _candidate_numbers(candidate: Any) -> set[str]:
    allowed = _extract_numbers(candidate.canonical_name)
    allowed |= _format_number(candidate.recommendation_score)
    allowed |= _format_number(candidate.credibility)
    allowed |= _format_number(candidate.fact_support * 100)
    for fused_claim in getattr(candidate, "claims", None) or []:
        if fused_claim.verification.status != "supported":
            continue
        if fused_claim.claim.field_key == "entity.canonical_name":
            continue
        value = fused_claim.verification.canonical_value
        if isinstance(value, (int, float, Decimal)):
            allowed |= _format_number(float(value))
        elif isinstance(value, str):
            allowed |= _extract_numbers(value)
    return allowed


def _overview_allowed(result: FusionResult) -> set[str]:
    """Numbers the global fields (headline, overview, caveats) may quote.

    ``getattr`` throughout: the narrator is also driven with lightweight result
    stand-ins in tests, and a missing ``failed_models`` must not turn a readable
    answer into an exception.
    """
    trace = result.trace
    allowed = _format_number(trace.agent_coverage * 100)
    allowed |= _format_number(len(getattr(trace, "participating_models", None) or []))
    allowed |= _format_number(len(getattr(trace, "failed_models", None) or []))
    allowed |= _format_number(len(getattr(result, "eliminated", None) or []))
    allowed |= _format_number(len(result.top_k[:_MAX_NARRATED_CANDIDATES]))
    for candidate in result.top_k[:_MAX_NARRATED_CANDIDATES]:
        allowed |= _extract_numbers(candidate.canonical_name)
        allowed |= _format_number(candidate.recommendation_score)
        allowed |= _format_number(candidate.credibility)
    return allowed


def _allowed_numbers(result: FusionResult) -> set[str]:
    """Compatibility helper returning the union of verified result numbers."""
    allowed = _overview_allowed(result)
    for candidate in result.top_k[:_MAX_NARRATED_CANDIDATES]:
        allowed |= _candidate_numbers(candidate)
    return allowed


def _numbers_are_faithful(narration: NaturalLanguage, result: FusionResult) -> bool:
    """Validate the global text and each candidate's own explanation."""
    candidates = result.top_k[:_MAX_NARRATED_CANDIDATES]
    overview_allowed = _overview_allowed(result)
    if any(number not in overview_allowed for number in _extract_numbers(narration.overview)):
        return False

    by_id = {str(candidate.candidate_id): candidate for candidate in candidates}
    all_names = {candidate.canonical_name for candidate in candidates}
    for item in narration.per_candidate:
        candidate = by_id.get(str(item.candidate_id))
        if candidate is None:
            return False
        allowed = _candidate_numbers(candidate)
        if any(number not in allowed for number in _extract_numbers(item.explanation)):
            return False
        # An explanation may name itself, but never another ranked candidate.
        if any(_names_candidate(item.explanation, name, candidate.canonical_name) for name in all_names):
            return False
    return True


def _names_candidate(text: str, name: str, own_name: str) -> bool:
    """Whether ``text`` refers to ``name`` rather than naming ``own_name``.

    Plain substring containment is wrong for product names: "GeForce RTX 4060" is
    a prefix of "GeForce RTX 4060 Ti", and the catalogue holds exactly such
    families (RTX 4070 / 4070 Super / 4070 Ti / 4070 Ti Super).  With substring
    matching, a candidate that named *itself* was rejected for naming a peer, which
    measured as the dominant cause of narration fallbacks: 71 of 73 stored fusion
    results carried the deterministic template instead of a model-authored answer.
    """
    if name == own_name:
        return False
    start = 0
    while True:
        index = text.find(name, start)
        if index < 0:
            return False
        if own_name.startswith(name) and text.startswith(own_name, index):
            # This occurrence is the head of our own longer name, not a reference
            # to the peer.
            start = index + len(own_name)
            continue
        return True


def _presentation_is_faithful(narration: NaturalLanguage, result: FusionResult) -> bool:
    """The conclusion must be about a candidate the brief actually contains.

    This is the guard that makes "the narrator may rephrase but never re-decide"
    enforceable rather than aspirational: a primary that names an unverified
    product, mislabels its canonical name, or quotes a number the truth check did
    not produce is rejected outright and the deterministic template is used.
    """
    candidates = result.top_k[:_MAX_NARRATED_CANDIDATES]
    by_id = {str(candidate.candidate_id): candidate for candidate in candidates}
    primary = narration.primary
    primary_id: str | None = None

    if primary is not None:
        candidate = by_id.get(str(primary.candidate_id))
        if candidate is None:
            return False
        if primary.name != candidate.canonical_name:
            return False
        allowed = _candidate_numbers(candidate)
        for reason in primary.reasons:
            if any(number not in allowed for number in _extract_numbers(reason)):
                return False
        primary_id = str(primary.candidate_id)
    elif candidates and narration.headline:
        # A headline without a primary is allowed only when there is nothing to
        # recommend; otherwise the model skipped the decision it was asked for.
        if any(candidate.canonical_name in narration.headline for candidate in candidates):
            return False

    for alternative in narration.alternatives:
        candidate = by_id.get(str(alternative.candidate_id))
        if candidate is None or str(alternative.candidate_id) == primary_id:
            return False
        allowed = _candidate_numbers(candidate)
        if primary_id is not None:
            allowed |= _candidate_numbers(by_id[primary_id])
        if any(number not in allowed for number in _extract_numbers(alternative.note)):
            return False
        if alternative.name != candidate.canonical_name:
            return False

    allowed_global = _overview_allowed(result)
    for caveat in narration.caveats:
        if any(number not in allowed_global for number in _extract_numbers(caveat)):
            return False
    return True


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    return stripped


def _supported_facts(candidate: Any) -> list[dict[str, str]]:
    # getattr throughout: `narrate_fusion` runs against lightweight result
    # stand-ins in tests, and a missing attribute here would be swallowed by its
    # defensive except clause and silently downgrade every answer to the template.
    facts: list[dict[str, str]] = []
    for fused_claim in getattr(candidate, "claims", None) or []:
        if fused_claim.verification.status != "supported":
            continue
        if fused_claim.claim.field_key == "entity.canonical_name":
            continue
        value = fused_claim.verification.canonical_value
        if value is None:
            continue
        facts.append({
            "field_key": fused_claim.claim.field_key or fused_claim.claim.claim_type,
            "label": _label_for(fused_claim.claim.field_key, fused_claim.claim.claim_type),
            "value": _format_scalar(value),
            "unit": _display_unit(fused_claim.verification.canonical_unit),
            "evidence_ids": [str(i) for i in getattr(fused_claim.verification, "valid_evidence_ids", [])],
        })
    priority = ["gpu.vram_gib", "gpu.board_power_w", "cpu.cores_total", "cpu.socket", "cpu.base_power_w"]
    return sorted(facts, key=lambda f: priority.index(f["field_key"]) if f["field_key"] in priority else len(priority))


def _shared_dimensions(result: FusionResult) -> dict[str, dict[str, str]]:
    """Field keys with a verified value on more than one candidate.

    These are the only dimensions on which a comparison can honestly be made, so
    the narrator is told about them explicitly instead of having to infer which
    numbers are comparable.
    """
    dimensions: dict[str, dict[str, str]] = {}
    for candidate in result.top_k[:_MAX_NARRATED_CANDIDATES]:
        for fact in _supported_facts(candidate):
            rendered = f"{fact['value']} {fact['unit']}".strip()
            dimensions.setdefault(fact["field_key"], {})[candidate.canonical_name] = rendered
    return {key: values for key, values in dimensions.items() if len(values) > 1}


def _brief(
    question: str, result: FusionResult, core_build: CoreBuild | None = None,
) -> dict[str, Any]:
    top = []
    for candidate in result.top_k[:_MAX_NARRATED_CANDIDATES]:
        top.append({
            "candidate_id": str(candidate.candidate_id),
            "name": candidate.canonical_name,
            "score": candidate.recommendation_score,
            "credibility": candidate.credibility,
            "fact_support": candidate.fact_support,
            "source_models": list(getattr(candidate, "source_models", None) or []),
            "verified_claims": _supported_facts(candidate),
            "constraint_verdicts": [
                {
                    "constraint_id": item.constraint_id,
                    "kind": item.kind,
                    "target": item.target,
                    "status": item.status,
                    "reason": item.reason_code,
                    "actual": _format_scalar(item.actual_value) if item.actual_value is not None else None,
                    "expected": _format_scalar(item.expected_value) if item.expected_value is not None else None,
                }
                for item in (getattr(candidate, "constraints", None) or [])
            ],
        })
    return {
        "question": question,
        "agent_coverage": result.trace.agent_coverage,
        "participating_models": list(getattr(result.trace, "participating_models", None) or []),
        "failed_models": list(getattr(result.trace, "failed_models", None) or []),
        "top_k": top,
        "eliminated": [
            {"name": candidate.canonical_name, "reasons": candidate.elimination_reasons}
            for candidate in (getattr(result, "eliminated", None) or [])[:_MAX_NARRATED_CANDIDATES]
        ],
        "shared_dimensions": _shared_dimensions(result),
        "release_key": result.release_key,
        # Present only for a whole-machine request.  The supporting specifications are the
        # backend's derivation, so the narrator may name them but must never restate one as
        # its own finding or add a manufacturer the catalogue does not hold.
        "core_build": (
            {
                "core": [{"role": item.role, "name": item.name} for item in core_build.core],
                "supporting": [
                    {"role": item.role, "spec": item.spec, "basis": item.basis}
                    for item in core_build.supporting
                ],
                "gaps": list(core_build.gaps),
            }
            if core_build is not None
            else None
        ),
    }


def _expected_ids(result: FusionResult) -> set[str]:
    return {str(candidate.candidate_id) for candidate in result.top_k[:_MAX_NARRATED_CANDIDATES]}


def _template_facts(candidate: Any, limit: int = 3) -> list[str]:
    facts = _supported_facts(candidate)
    rendered = [
        f"{fact['label']} {fact['value']} {fact['unit']}".strip()
        for fact in facts[:limit]
    ]
    return rendered


def _presentation_evidence(
    result: FusionResult, core_build: CoreBuild | None = None, limit: int = 6,
) -> list[str]:
    """Render a small evidence list from supported claims only.

    A whole-machine answer may contain both a CPU and a GPU even when their
    cross-category scores are not directly comparable, so include every core
    component carried by the assembler.  For a component answer, use the primary
    candidate only.  Agent/model names and internal score mechanics deliberately
    never enter this public contract.
    """
    candidate_ids = (
        {str(item.candidate_id) for item in core_build.core}
        if core_build is not None and core_build.core
        else ({str(c.candidate_id) for c in result.top_k}
              if (getattr(result.trace, 'input_summary', {}) or {}).get('requested_selection')
              else ({str(result.top_k[0].candidate_id)} if result.top_k else set()))
    )
    lines: list[str] = []
    seen: set[str] = set()
    for candidate in result.top_k:
        if str(candidate.candidate_id) not in candidate_ids:
            continue
        per_component_limit = max(1, limit // len(candidate_ids))
        for fact in _supported_facts(candidate)[:per_component_limit]:
            # The recommendation sentence already names the product; repeating
            # canonical-name evidence adds no decision value.
            if fact["field_key"] == "entity.canonical_name":
                continue
            value = f"{fact['value']} {fact['unit']}".strip()
            line = f"{candidate.canonical_name}：{fact['label']} {value}（数据库已核验）"
            if line not in seen:
                seen.add(line)
                lines.append(line)
            if len(lines) >= limit:
                return lines
    return lines


def _fallback_reasons(candidate: Any, question: str | None) -> list[str]:
    """Connect verified facts to the stated need without another LLM call."""
    facts = {item["field_key"]: item for item in _supported_facts(candidate)}
    need = (question or "").casefold()
    reasons: list[str] = []

    vram = facts.get("gpu.vram_gib")
    if vram and any(token in need for token in ("本地", "llm", "模型")):
        reasons.append(
            f"本地模型加载首先受显存容量约束；数据库核验该卡具备 "
            f"{vram['value']} {vram['unit']} 显存，因此把它作为主要筛选依据。"
        )
    elif vram and '显存' in need:
        reasons.append(f"数据库核验该卡具备 {vram['value']} {vram['unit']} 显存。")
    power = facts.get("gpu.board_power_w") or facts.get("cpu.base_power_w")
    if power and any(token in need for token in ("功耗", "省电", "能耗", "电源")):
        reasons.append(
            f"你明确关注功耗，数据库记录的对应功耗为 "
            f"{power['value']} {power['unit']}，该数值已进入本次权衡。"
        )
    price = facts.get("price")
    if price and any(token in need for token in ("预算", "价格", "性价比", "便宜")):
        reasons.append(
            f"数据库核验的当前价格为 {price['value']} {price['unit']}，"
            "推荐排序已把预算因素纳入。"
        )
    if not reasons:
        rendered = _template_facts(candidate)
        if rendered:
            reasons.append("数据库已核验其" + "、".join(rendered) + "，这些参数构成本次推荐的事实基础。")
    if not reasons:
        reasons.append("该型号已通过身份校验；具体适用性仍需结合用途与可验证规格判断。")
    return reasons[:4]


def _fallback_headline(first: Any, core_build: CoreBuild | None) -> str:
    """A core build gets a build-shaped headline; a single part keeps the old wording."""
    if core_build is not None and core_build.core:
        names = "、".join(item.name for item in core_build.core)
        if not set(core_build.required_core_roles).issubset({item.role for item in core_build.core}):
            return f"已核验的部分配置：{names}；核心部件尚未齐全。"
        return f"这套配置草案以 {names} 为核心；其余配件与兼容性按标注确认。"
    return f"本次推荐：{first.canonical_name}。"


def deterministic_narration(
    result: FusionResult, question: str | None = None, core_build: CoreBuild | None = None,
) -> NaturalLanguage:
    """Template narration when the Narrator model is unavailable or invalid.

    Upgraded in Plan_V4.1: the old template only restated the scores
    ("综合得分 84、可信度 82"), which is exactly the lifeless output the user
    reported.  It now leads with a headline and quotes the verified facts that
    were actually checked, so the fallback is at least factual rather than a
    score recital.

    Plan_V4.2: this degradation path is the one this deployment actually reaches, so a
    core build has to survive it.  It is carried through verbatim rather than
    re-derived here, and the headline names the build instead of the top single part.
    """
    from app.services.hardware_requirements import parse_requirements
    from app.schemas.fusion import SelectedRecommendation
    requirements = parse_requirements(question or '')
    multi = (getattr(result.trace, 'input_summary', {}) or {}).get('requested_selection')
    top = result.top_k[:10 if multi else _MAX_NARRATED_CANDIDATES]
    coverage = result.trace.agent_coverage
    if not top:
        from app.services.model_choices import excluded_model_ids, replacement_gap_answer
        if excluded_model_ids.get():
            return NaturalLanguage(headline=replacement_gap_answer(), overview=replacement_gap_answer())
        headline = "本次没有候选同时满足需求约束与数据库核验条件。"
        return NaturalLanguage(
            headline=headline,
            overview="没有找到可交付的候选；指定型号、搭配条件或可用证据可能存在缺口，不能擅自替换型号。",
            caveats=["没有符合全部条件的已核验候选；这不等于指定型号不存在。"],
            evidence=[],
            per_candidate=[],
            core_build=core_build,
        )

    first = top[0]
    overview = (
        f"{first.canonical_name} 已通过数据库事实校验，并在符合条件的候选中排序最优；"
        f"本次校验覆盖率约为 {coverage * 100:.0f}%。"
    )

    reasons = _fallback_reasons(first, question)
    if core_build and core_build.core:
        reasons = []
        for component in core_build.core:
            candidate = next((c for c in top if c.candidate_id == component.candidate_id), None)
            if candidate is not None:
                if office_default(question or "") and component.role == "cpu":
                    reasons.extend(component.reasons)
                    power = next((f for f in _supported_facts(candidate) if f["field_key"] == "cpu.base_power_w"), None)
                    if power:
                        reasons.append(f"该 CPU 已核验基础功耗 {power['value']} {power['unit']}；本次将基础功耗作为办公选型偏好，并结合平台与散热需求选择配套部件。")
                    continue
                facts = _template_facts(candidate)
                if facts:
                    verified = {f["field_key"]: f for f in _supported_facts(candidate)}
                    if component.role == "gpu" and "gpu.vram_gib" in verified and any(w in (question or "") for w in ("游戏", "网游")):
                        vram = verified["gpu.vram_gib"]
                        reasons.append(f"显卡选择 {component.name}：已核验 {vram['value']} {vram['unit']} 显存。游戏纹理会占用显存，具体画质仍需结合游戏和分辨率确定。")
                    elif component.role == "cpu" and "cpu.cores_total" in verified and any(w in (question or "") for w in ("游戏", "网游")):
                        cores = verified["cpu.cores_total"]
                        reasons.append(f"CPU 选择 {component.name}：已核验 {cores['value']} 核心，用于游戏逻辑与后台任务，作为处理器选型的规格依据。")
                    else:
                        reasons.append(f"{component.role.upper()} 选择 {component.name}：已核验" + "、".join(facts) + "。")
    satisfied = [item for item in getattr(first, "constraints", None) or [] if item.status == "satisfied"]
    if satisfied:
        reasons.append(f"它满足本次已确认的 {len(satisfied)} 项需求约束。")
    reasons = reasons[:4]

    alternatives: list[AlternativeNote] = []
    core_ids = {str(item.candidate_id) for item in core_build.core} if core_build else set()
    for other in top[1:3]:
        if str(other.candidate_id) in core_ids:
            continue
        alternatives.append(AlternativeNote(
            candidate_id=other.candidate_id,
            name=other.canonical_name,
            note=(
                "这是另一款通过当前事实校验的候选；排序不代表已验证性能或价格优势。"
            ),
        ))

    caveats: list[str] = list(core_build.gaps) if core_build else []
    if core_build and question and not office_default(question) and any(word in question for word in ("游戏", "网游")):
        caveats.append("这是待细化的核心配置草案；预算、具体游戏、分辨率及刷新率应按已提供的信息继续确认，规格核验不代表已验证游戏帧率。")
        if re.search(r"预算|价格|总价|多少钱|\d+\s*元", question):
            caveats.append("本次未核验整机各配件实时价格，不能确认总价或保证满足预算。")
        if re.search(r"安静|噪声|静音", question):
            caveats.append("缺少整机噪声测试和具体散热、机箱数据，不能保证静音效果。")
    failed = len(getattr(result.trace, "failed_models", None) or [])
    if failed:
        caveats.append(f"有 {failed} 个模型未参与本次融合，结论的覆盖度因此受限。")
    eliminated = getattr(result, "eliminated", None) or []
    if eliminated:
        caveats.append(f"另有 {len(eliminated)} 个候选未通过硬约束或事实校验，已被排除。")

    per_candidate = []
    for index, candidate in enumerate(top, start=1):
        support = candidate.fact_support * 100
        sources = "、".join(candidate.source_models) if candidate.source_models else "无"
        candidate_facts = _template_facts(candidate)
        fact_text = ("已验证：" + "、".join(candidate_facts) + "。") if candidate_facts else ""
        per_candidate.append(NarratorCandidateText(
            candidate_id=candidate.candidate_id,
            explanation=(
                f"第 {index} 名：{candidate.canonical_name}，融合综合得分 "
                f"{candidate.recommendation_score:.0f}，可信度 {candidate.credibility:.0f}，"
                f"事实支持度约 {support:.0f}%。{fact_text}主要依据来自 {sources} 的有效候选。"
            ),
        ))

    selections = []
    notice = None
    if multi:
        labels = {item['candidate_id']: item['label'] for item in multi['slots']}
        for candidate in top:
            label = labels.get(str(candidate.candidate_id), '')
            item_reasons = _fallback_reasons(candidate, question)
            if '性价比' in label:
                item_reasons.append('作为偏性价比的备选方向；缺少当前售价，尚不能验证实际性价比。')
            elif '高性能' in label:
                item_reasons.append('按库内性能分级选择较高档候选；不代表已验证具体游戏帧率。')
            selections.append(SelectedRecommendation(candidate_id=candidate.candidate_id,
                name=candidate.canonical_name, label=label, reasons=item_reasons[:4]))
        if len(selections) < requirements.count:
            notice = f'你要求 {requirements.count} 款，目前只能提供 {len(selections)} 款有依据的候选；其余名额暂缺，不用不符合条件的产品补齐。'
    return NaturalLanguage(
        headline=('本次推荐：' + '、'.join(c.canonical_name for c in top) + '。') if multi else _fallback_headline(first, core_build),
        selections=selections,
        selection_notice=notice,
        overview=overview,
        primary=PrimaryRecommendation(
            candidate_id=first.candidate_id,
            name=first.canonical_name,
            reasons=reasons,
        ),
        evidence=_presentation_evidence(result, core_build),
        alternatives=alternatives,
        caveats=caveats,
        per_candidate=per_candidate,
        core_build=core_build,
    )


def _parse_narration(content: str, result: FusionResult) -> NaturalLanguage | None:
    try:
        value = json.loads(_strip_fences(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    # Accept the pre-V4.1 shape (overview only) so an older prompt/model revision
    # cannot turn every answer into a fallback, but keep the overview filled
    # either way because existing callers read it.
    if "overview" not in value and isinstance(value.get("headline"), str):
        value = {**value, "overview": value["headline"]}
    try:
        narration = NaturalLanguage.model_validate(value)
    except Exception:
        return None
    # Every candidate needs exactly one explanation, but the order is not part of
    # the contract: requiring the ids to appear in rank order rejected narrations
    # that bound every explanation correctly, only in a different sequence.
    actual = [str(item.candidate_id) for item in narration.per_candidate]
    expected = [str(candidate.candidate_id) for candidate in result.top_k[:_MAX_NARRATED_CANDIDATES]]
    if len(actual) != len(expected) or set(actual) != set(expected):
        return None
    if not _numbers_are_faithful(narration, result):
        return None
    if not _presentation_is_faithful(narration, result):
        return None
    return narration


def _explanation_baseline(baseline, chosen):
    """Extra causal detail for evidence follow-ups, including when the LLM fails."""
    reasons = list(baseline.primary.reasons) if baseline.primary else []
    for candidate in chosen:
        facts = {f["field_key"]: f for f in _supported_facts(candidate)}
        power = facts.get("gpu.board_power_w")
        socket = facts.get("cpu.socket")
        if power:
            reasons.append(f"{candidate.canonical_name}：已核验板卡功耗 {power['value']} {power['unit']}，"
                "这为电源容量和机箱散热预留提供依据。它不是整机实测功耗，也不能单凭此值推断游戏表现或电费。")
        elif socket:
            reasons.append(f"{candidate.canonical_name}：已核验插槽 {socket['value']}，因此主板平台必须匹配该插槽；"
                "还需检查具体主板的处理器支持列表、BIOS 和内存支持，插槽相同本身不足以保证兼容。")
        elif facts:
            reasons.append(f"{candidate.canonical_name}：上述证据支持的是硬件规格。它们解释了选择时考虑的条件，"
                "但没有完成同预算产品的实测比较，不能由此断言它是最佳选择。")
    return baseline.model_copy(update={"primary": baseline.primary.model_copy(update={"reasons": reasons})}) if baseline.primary else baseline


async def _compact_narration(message, result, core_build, settings, profile, _repair=False):
    """Let the model explain the selected facts without retyping decisions or proofs."""
    baseline = deterministic_narration(result, message, core_build)
    if not result.top_k:
        return baseline
    core_ids = {str(c.candidate_id) for c in core_build.core} if core_build else set()
    chosen = [c for c in result.top_k if str(c.candidate_id) in core_ids] if core_ids else result.top_k[:1]
    slots = {f"c{index}": c for index, c in enumerate(chosen)}
    explaining = getattr(result.trace, "input_summary", {}).get("answer_purpose") == "explanation"
    if explaining:
        baseline = _explanation_baseline(baseline, chosen)
    reason_limit = 2 if explaining or not core_ids else 1
    properties = {key: {"type": "array", "minItems": 1, "maxItems": reason_limit,
                        "items": {"type": "string", "minLength": 1, "maxLength": 220}} for key in slots}
    schema = {"type": "object", "properties": properties, "required": list(slots), "additionalProperties": False}
    brief = {key: {"name": c.canonical_name, "verified_facts": _supported_facts(c)} for key,c in slots.items()}
    try:
        call = await complete_chat([
            {"role": "system", "content":
             "Your internal hardware knowledge may be outdated. Explain why the already-selected hardware fits the user's need, in concise Chinese. "
             + ("REWRITE: previous explanation contained ungrounded claims. Remove all unsupported properties, dates, comparisons and performance guarantees. " if _repair else "") +
             "Return only the requested JSON, within the reason count limit. "
             + ("Give two complementary explanations per component: relevance to the original need and the practical role of the verified specification. " if explaining else "Give one short causal reason per component, up to two for a single part. ") +
             "Use only supplied verified facts. Explain their relevance in connected prose, not just list values. Avoid generic disclaimers; the UI provides one advice notice. "
             "Do not rank again, add products, copy names, output scores, prices or benchmark/FPS claims. "
             "Omit storage, cooler and case recommendations and their catalogue-gap notices. "
             "Do not promise smooth gameplay without measurements. No Markdown. Missing facts are unknown. "
             "Do not infer smooth office operation, noise or actual electricity use from core count or rated power. "
             "For office, explain the conservative preference for modest core counts and base power; do not claim tested adequacy. "
             "Budget/game/resolution are unspecified unless the question says otherwise."},
            {"role": "user", "content": json.dumps({"question": message, "selected": brief}, ensure_ascii=False)}
        ], model_id=profile.model_id, base_url=profile.endpoint_url, response_schema=schema,
            max_tokens=650 if explaining else 450, timeout_seconds=min(12.0, float(getattr(settings, "llm_timeout_seconds", 120))))
        texts = json.loads(_strip_fences(call.content))
        if not isinstance(texts, dict) or set(texts) != set(slots):
            raise ValueError("Narration must cover exactly the selected components")
        rendered = []
        explanations = {}
        for key, candidate in slots.items():
            reasons = texts[key]
            if not isinstance(reasons, list) or not 1 <= len(reasons) <= reason_limit:
                raise ValueError("Invalid reason count")
            for reason in reasons:
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 220:
                    raise ValueError("Invalid reason")
                if any(n not in _candidate_numbers(candidate) for n in _extract_numbers(reason)):
                    raise ValueError("Unverified numeric claim")
                issue = explanation_issue(reason, _supported_facts(candidate))
                if issue:
                    raise ValueError(issue)
                if re.search(r"fps|帧率|跑分|实测|价格|售价|元|库存|score|agent-|综合得分|可信度|确保|保证|必然|未来兼容", reason, re.I):
                    raise ValueError("Unsupported measurement or internal detail")
                if any(_names_candidate(reason, c.canonical_name, candidate.canonical_name)
                       for c in result.top_k):
                    raise ValueError("Reason refers to another candidate")
            explanations[str(candidate.candidate_id)] = " ".join(reasons)
            rendered.extend([candidate.canonical_name + "：" + r for r in reasons] if core_ids else reasons)
        timing("narrator_validation", 0, status="accepted", mode="compact")
        return baseline.model_copy(update={
            "primary": baseline.primary.model_copy(update={"reasons": rendered}),
            "per_candidate": [item.model_copy(update={"explanation": explanations[str(item.candidate_id)]})
                              if str(item.candidate_id) in explanations else item for item in baseline.per_candidate],
        })
    except (LLMServiceError, ValueError, TypeError, KeyError) as exc:
        if not _repair and isinstance(exc, (ValueError, TypeError, KeyError)):
            timing("narrator_rewrite", 0, status="started", reason=type(exc).__name__)
            return await _compact_narration(message, result, core_build, settings, profile, _repair=True)
        timing("narrator_validation", 0, status="template_fallback", mode="compact", reason=str(exc) if type(exc) is ValueError else type(exc).__name__)
        return baseline


async def narrate_fusion(
    *,
    message: str,
    result: FusionResult,
    settings: Settings | None = None,
    core_build: CoreBuild | None = None,
    compact: bool = False,
) -> NaturalLanguage:
    """Produce natural language for a fused result with a guaranteed fallback."""
    from app.services.hardware_requirements import parse_requirements
    requirements = parse_requirements(message)
    if requirements.count > 1 or requirements.min_vram is not None or requirements.gpu_brands:
        return deterministic_narration(result, message, core_build)
    if result.top_k and getattr(result.top_k[0], 'candidate_type', None) == 'ai_model':
        return deterministic_narration(result, message, None)
    # Build specifications are rendered from verified fields. A free-form rewrite
    # can turn an observed 8-core count into a false 8-thread assertion.
    if core_build is not None:
        timing("narrator_validation", 0, status="verified_build_template")
        baseline = deterministic_narration(result, message, core_build)
        if (getattr(result.trace, "input_summary", {}) or {}).get("answer_purpose") == "explanation":
            core_ids = {str(c.candidate_id) for c in core_build.core}
            chosen = [c for c in result.top_k if str(c.candidate_id) in core_ids]
            return _explanation_baseline(baseline, chosen)
        return baseline
    settings = settings or get_settings()
    profile_id = str(getattr(settings, "narrator_profile_id", "agent-b") or "agent-b")
    try:
        profile = get_profile(profile_id, settings)
        if profile is None:
            return deterministic_narration(result, message, core_build)
        if compact:
            return await _compact_narration(message, result, core_build, settings, profile)
        payload = [
            {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "用户问题：" + message + "\n\n只读融合结果：\n" + json.dumps(
                    _brief(message, result, core_build), ensure_ascii=False
                ),
            },
        ]
        timeout = float(getattr(settings, "llm_timeout_seconds", 120.0) or 120.0)
        call = await complete_chat(
            payload,
            model_id=profile.model_id,
            base_url=profile.endpoint_url,
            max_tokens=_NARRATOR_MAX_TOKENS,
            timeout_seconds=min(_NARRATOR_TIMEOUT, timeout),
        )
        narration = _parse_narration(call.content, result)
        timing("narrator_validation", 0, status="accepted" if narration is not None else "template_fallback")
        if narration is not None:
            # The model-authored wording is kept, but the build is the backend's: it is
            # attached here so a narration can never alter, omit or invent it.
            return narration.model_copy(update={
                "core_build": core_build,
                "caveats": list(dict.fromkeys([*(core_build.gaps if core_build else []), *narration.caveats])),
                # Evidence is always backend-derived after verification; the
                # narrator can phrase reasons but cannot author proof.
                "evidence": _presentation_evidence(result, core_build),
            })
    except (LLMServiceError, AttributeError, TypeError, ValueError):
        pass
    return deterministic_narration(result, message, core_build)


__all__ = [
    "NarratorCandidateText",
    "deterministic_narration",
    "narrate_fusion",
]
