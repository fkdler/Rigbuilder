"""Deterministic core-build assembly (Plan_V4.2).

A whole-machine request is answered as a *core build*: two core components taken from the
verified fusion candidates, plus supporting parts stated only as the specification tiers
this Catalogue actually holds.

Everything below is derivation over the catalogue, never a model's opinion:

* platform - the CPU's own ``socket`` column, matched against a ``platform`` tier;
* memory   - the DDR generation that platform implies;
* power    - the combined board power of the core components plus headroom, rounded up to
             the nearest ``psu`` tier.

``truth.compatibility_edge`` and ``truth.rule_definition`` are both empty in this Release,
so these rules live here and are marked ``database_derived`` instead of posing as verified
facts. Storage, coolers and cases are outside the displayed recommendation scope.

Nothing in this module may raise into the fusion path: a derivation that cannot be made is
a ``not_in_catalogue`` entry, and a request that is not a build simply returns ``None``.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.fusion import CoreBuild, CoreComponent, SupportingSpec
from app.services.routing import classify_request
from app.services.requirements import office_default

# Headroom over the combined board power of the core components.  Board power is not
# sustained system draw: it excludes transient spikes and everything on the board that is
# not the CPU or the GPU, and a supply run at its limit is louder and shorter-lived.
PSU_HEADROOM_W = 120

# Which memory generation a socket implies.  LGA1700 boards exist in both DDR4 and DDR5
# variants; naming DDR4 is the safe direction, because a DDR5 part is the one that would
# not fit.
_DDR_BY_SOCKET: dict[str, str] = {
    "AM5": "DDR5",
    "LGA1851": "DDR5",
    "AM4": "DDR4",
    "LGA1700": "DDR4",
}

_PSU_WATTS = re.compile(r"(\d+)\s*W", re.IGNORECASE)


def _category_by_entity(session: Session, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Map candidate UUID -> {entity_key, category} using one catalogue read.

    ``CandidateType`` is only hardware / model_variant / ai_model, so a CPU and a GPU are
    both plain "hardware" and the fusion result cannot say which is which.  Rather than
    widen the submission schema the model has to fill in, the category is read back from
    the catalogue after fusion.  Measured: component_profile_catalog carries every
    identity and shares its entity_id with cpu_catalog / gpu_catalog.
    """
    if not entity_ids:
        return {}
    placeholders = ", ".join(f":id{index}" for index in range(len(entity_ids)))
    parameters = {f"id{index}": value for index, value in enumerate(entity_ids)}
    rows = session.execute(
        text(
            "SELECT entity_id::text AS entity_id, entity_key, category "
            "FROM agent_catalog.component_profile_catalog "
            f"WHERE entity_id::text IN ({placeholders})"
        ),
        parameters,
    ).fetchall()
    return {
        str(row.entity_id): {"entity_key": row.entity_key, "category": row.category}
        for row in rows
    }


def _spec_row(session: Session, view: str, columns: str, entity_key: str) -> Any:
    return session.execute(
        text(f"SELECT {columns} FROM agent_catalog.{view} WHERE entity_key = :key"),
        {"key": entity_key},
    ).fetchone()


def _tier_names(session: Session, category: str) -> list[str]:
    rows = session.execute(
        text(
            "SELECT name FROM agent_catalog.component_profile_catalog "
            "WHERE category = :category AND record_kind = 'spec_profile' ORDER BY name"
        ),
        {"category": category},
    ).fetchall()
    return [str(row.name) for row in rows]


def _workload_requirement(session: Session, message: str) -> dict[str, str]:
    """Requirements of the one workload this message names, if the Release covers it.

    The Release holds thirteen game profiles and no local-model-deployment profile, so a
    request about anything else correctly finds nothing here and the parts it would have
    decided are reported as uncovered.
    """
    rows = session.execute(
        text(
            "SELECT w.name, w.family, w.entity_key, r.component_role, r.operator, "
            "r.numeric_value, r.unit_key, r.value_text, r.profile_key "
            "FROM agent_catalog.workload_catalog w "
            "JOIN agent_catalog.workload_requirement_catalog r "
            "ON r.workload_key = w.entity_key "
            "WHERE r.profile_key = 'recommended'"
        )
    ).fetchall()
    folded = message.casefold()
    matched_key = next(
        (
            row.entity_key for row in rows
            if any(
                needle and str(needle).casefold() in folded
                for needle in (row.name, row.family)
            )
        ),
        None,
    )
    if matched_key is None:
        return {}

    found: dict[str, str] = {}
    for row in rows:
        if row.entity_key != matched_key or row.component_role in found:
            continue
        if row.component_role == "ram" and row.numeric_value is not None:
            unit = (row.unit_key or "gib").upper()
            found["memory"] = f">= {row.numeric_value:g} {unit}"
        elif row.component_role == "storage" and row.value_text:
            found["storage"] = str(row.value_text)
    return found


def _platform_spec(session: Session, socket: str | None) -> SupportingSpec:
    if not socket:
        return SupportingSpec(
            role="platform", spec="未收录", basis="not_in_catalogue",
            note="选中的 CPU 没有 socket 值，无法推导平台档位。",
        )
    wanted = f"Socket {socket}"
    tiers = _tier_names(session, "platform")
    match = next((name for name in tiers if name.casefold() == wanted.casefold()), None)
    if match is None:
        return SupportingSpec(
            role="platform", spec="未收录", basis="not_in_catalogue",
            note=f"数据库未收录 {wanted} 对应的平台档位。",
        )
    return SupportingSpec(
        role="platform", spec=match, basis="database_derived",
        note=f"依据 CPU socket {socket} 推导。",
    )


def _memory_spec(session: Session, socket: str | None, requirement: str | None) -> SupportingSpec:
    generation = _DDR_BY_SOCKET.get((socket or "").upper())
    tiers = _tier_names(session, "memory")
    if generation is None:
        return SupportingSpec(
            role="memory", spec=requirement or "未收录", basis="not_in_catalogue",
            note="未知 socket 无法推导内存代际。",
        )
    matching = [name for name in tiers if name.upper().startswith(generation)]
    if not matching:
        return SupportingSpec(
            role="memory", spec=requirement or "未收录", basis="not_in_catalogue",
            note=f"数据库未收录 {generation} 内存档位。",
        )
    spec = f"{generation}，可用档位：" + " / ".join(matching)
    if requirement:
        return SupportingSpec(
            role="memory", spec=f"{spec}；容量需求 {requirement}",
            basis="database_requirement", note=f"容量来自该场景的官方推荐配置，代际由 {socket} 推导。",
        )
    return SupportingSpec(
        role="memory", spec=spec, basis="database_derived",
        note=f"依据 {socket} 平台推导代际；本场景的容量需求数据库未收录。",
    )


def _power_spec(session: Session, draw_w: float | None) -> SupportingSpec:
    if draw_w is None:
        return SupportingSpec(
            role="psu", spec="待补齐核心部件功耗后确定", basis="not_in_catalogue",
            note="缺少核心部件功耗，无法推导电源档位。",
        )
    required = draw_w + PSU_HEADROOM_W
    tiers = _tier_names(session, "psu")
    rated = sorted(
        (int(match.group(1)), name)
        for name in tiers
        for match in [_PSU_WATTS.search(name)]
        if match
    )
    chosen = next((name for watts, name in rated if watts >= required), None)
    if chosen is None:
        return SupportingSpec(
            role="psu", spec="未收录", basis="not_in_catalogue",
            note=f"整机估算功耗约 {required:g}W，超出数据库收录的电源档位上限。",
        )
    return SupportingSpec(
        role="psu", spec=chosen, basis="database_derived",
        note=f"核心部件功耗合计 {draw_w:g}W，加 {PSU_HEADROOM_W}W 余量后向上取档。",
    )


def assemble_core_build(
    *, candidates: list[Any], message: str, session: Session,
) -> CoreBuild | None:
    """Build the core-build view, or None when this request is not a build.

    Returns None for a single-component request so the existing single-candidate
    presentation is untouched.
    """
    usable = [item for item in candidates if getattr(item, "candidate_id", None) is not None]
    if not usable:
        return None
    # Multiple candidates of the same component type are alternatives, not an
    # implicit whole machine. Only the user's request may widen a component
    # recommendation into a build.
    if classify_request(message) != "bundle":
        return None

    categories = _category_by_entity(session, [str(item.candidate_id) for item in usable])
    by_role: dict[str, Any] = {}
    for item in usable:
        entry = categories.get(str(item.candidate_id))
        if not entry:
            continue
        role = str(entry["category"])
        # Candidates arrive in fused rank order, so the first of a role wins.
        by_role.setdefault(role, {"item": item, **entry})

    cpu = by_role.get("cpu")
    gpu = by_role.get("gpu")
    office = office_default(message)
    if office:
        gpu = None
    if cpu is None and gpu is None:
        return None

    core: list[CoreComponent] = []
    socket: str | None = None
    gpu_w: float | None = None
    cpu_w: float | None = None
    gaps: list[str] = []

    if gpu is not None:
        row = _spec_row(session, "gpu_catalog",
                        "vram_gib, board_power_w, architecture, memory_type", gpu["entity_key"])
        core.append(CoreComponent(role="gpu", candidate_id=gpu["item"].candidate_id,
                                  name=gpu["item"].canonical_name))
        if row is not None and row.board_power_w is not None:
            gpu_w = float(row.board_power_w)
    elif not office:
        gaps.append("本次没有通过验证的显卡候选，配置单缺少核心显卡。")

    if cpu is not None:
        row = _spec_row(session, "cpu_catalog",
                        "socket, cores_total, threads, base_power_w, max_power_w",
                        cpu["entity_key"])
        cpu_reasons: list[str] = []
        if office:
            cpu_reasons.append("普通办公以文档、多任务和基本显示为主，因此优先控制基础功耗，不为更多核心盲目升级；实际办公响应速度仍需测试。")
        core.append(CoreComponent(role="cpu", candidate_id=cpu["item"].candidate_id,
                                  name=cpu["item"].canonical_name, reasons=cpu_reasons))
        if row is not None:
            socket = row.socket
            cpu_w = row.max_power_w if row.max_power_w is not None else row.base_power_w
    else:
        gaps.append("本次没有通过验证的 CPU 候选，配置单缺少核心 CPU；平台与内存代际因此无法推导。")

    requirement = _workload_requirement(session, message)
    supporting = [
        _platform_spec(session, socket),
        _memory_spec(session, socket, requirement.get("memory")),
        _power_spec(session, float(cpu_w) + gpu_w if cpu_w is not None and gpu_w is not None else None),
    ]
    if office:
        verified_igpu = next((c.verification.canonical_value for c in getattr(cpu["item"], "claims", [])
            if c.claim.field_key == "cpu.integrated_gpu" and c.verification.status == "supported"
            and c.verification.valid_evidence_ids and c.verification.canonical_value), None) if cpu else None
        supporting.insert(0, SupportingSpec(role="display", spec=(f"使用已核验核显 {verified_igpu}" if verified_igpu
            else "优先核显方案；该 CPU 核显及主板视频输出待核验"), basis="database_derived" if verified_igpu else "not_in_catalogue"))
        if not verified_igpu:
            gaps.insert(0, "当前缺少核显证据，暂不能保证不加独显即可使用；确认 CPU 核显和主板视频输出后再购买。")
        supporting = [item.model_copy(update={"spec": item.spec + "；容量建议 16GB（通用建议）",
            "basis": "general_advice", "note": "容量为普通办公起点，非数据库工作负载实测；DDR 版本需匹配具体主板。"}) if item.role == "memory" else item for item in supporting]
    required = ["cpu"] if office else ["cpu", "gpu"]
    return CoreBuild(core=core, supporting=supporting, gaps=gaps, required_core_roles=required,
                     status="draft" if set(required).issubset({c.role for c in core}) else "partial")


__all__ = ["PSU_HEADROOM_W", "assemble_core_build"]
