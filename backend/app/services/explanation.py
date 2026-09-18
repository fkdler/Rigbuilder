"""Re-read evidence for an existing choice without replacing it or reranking."""
import time
import asyncio
from uuid import UUID

from app.agents.selection import remember_selection_rows
from app.context.service import conversation_context_service
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.inference.http import inference_session
from app.inference.scheduler import inference_scheduler
from app.schemas.fusion import FusedClaim, FusionRunResponse
from app.schemas.recommendation import Recommendation
from app.services.build_assembler import _category_by_entity, assemble_core_build
from app.services.events import emit
from app.services.fusion import FusionDataError, presentation_of
from app.services.narrator import narrate_fusion
from app.services.presentation_sources import attach_sources
from app.services.timing import traced_request, phase
from app.tools.contracts import ToolResult
from app.tools.database import query_database
from app.verification import TruthVerifier
from app.verification.repository import SqlAlchemyTruthRepository


def recheck_anchor(anchor, session, event_sink=None):
    repository = SqlAlchemyTruthRepository(session)
    if repository.latest_release_key() != anchor.result.release_key:
        raise FusionDataError("anchor_evidence_changed", "The anchored release has changed.")
    selected = anchor.selected
    if not selected:
        raise FusionDataError("anchor_evidence_changed", "No anchored selection.")
    categories = _category_by_entity(session, [str(c.candidate_id) for c in selected])
    rows = {}
    for c in selected:
        role = categories.get(str(c.candidate_id), {}).get("category")
        columns = {"gpu": "vram_gib,board_power_w,architecture,memory_type,memory_bandwidth_gb_s",
                   "cpu": "cores_total,threads,socket,base_power_w,max_power_w,architecture"}.get(role)
        if not columns:
            raise FusionDataError("anchor_evidence_changed", "Unsupported anchored entity.")
        emit(event_sink, "tool_started", "tool", "正在读取上次配置的数据库证据", status="running")
        start = time.perf_counter()
        # candidate_id is a validated UUID; the ordinary SQL validator and read-only account apply.
        observation = query_database(f"SELECT entity_id,entity_key,name,{columns} FROM {role}_catalog "
                                     f"WHERE entity_id = '{c.candidate_id}' AND recommendable = true LIMIT 1",
                                     evidence_session=session)
        remember_selection_rows(ToolResult(tool="query_database", success=observation.success,
            data={"observation": observation.model_dump(mode="json")}), rows)
        emit(event_sink, "tool_completed", "tool", "配置证据读取完成", status="completed" if observation.success else "failed",
             duration_ms=(time.perf_counter() - start) * 1000)
    payload = {"recommendations": []}
    for c in selected:
        row = rows.get(str(c.candidate_id))
        if not row or not row["facts"]:
            raise FusionDataError("anchor_evidence_changed", "No accepted evidence for the anchored entity.")
        payload["recommendations"].append({"candidate_id": str(c.candidate_id), "candidate_type": c.candidate_type,
            "name": row["name"], "score": 0, "reasons": ["Recheck the previous selection"],
            "claims": list(row["facts"].values())[:6]})
    emit(event_sink, "verification", "verification", "正在核对原配置的事实证据", status="running")
    with phase("explanation_verification"):
        verification = TruthVerifier(repository).verify(Recommendation.model_validate(payload))
    verified = {c.candidate_id: c for c in verification.candidates}
    refreshed = []
    for c in selected:
        checked = verified.get(c.candidate_id)
        if (checked is None or not checked.candidate_valid or checked.canonical_name != c.canonical_name
                or not any(x.status == "supported" and x.claim.claim_type == "fact"
                           and x.claim.field_key != "entity.canonical_name" and x.valid_evidence_ids
                           for x in checked.claims)
                or any(x.status != "supported" for x in checked.claims)):
            raise FusionDataError("anchor_evidence_changed", "Anchored evidence could not be verified.")
        refreshed.append(c.model_copy(update={"fact_support": checked.fact_support,
            "proof_coverage": checked.proof_coverage, "information_completeness": checked.information_completeness, "claims": [FusedClaim(claim=x.claim, verification=x,
            source_models=[]) for x in checked.claims]}))
    if repository.latest_release_key() != anchor.result.release_key:
        raise FusionDataError("anchor_evidence_changed", "Release changed during verification.")
    emit(event_sink, "verification", "verification", "原配置证据核对完成", status="completed")
    # Scores/coverage describe the previous decision, never a new model vote.
    trace = anchor.result.trace.model_copy(update={"input_summary": {
        **anchor.result.trace.input_summary, "answer_purpose": "explanation", "anchor_question": anchor.question,
        "ranking_recomputed": False, "evidence_rechecked": True, "scores_scope": "previous_recommendation"}})
    return anchor.result.model_copy(update={"top_k": refreshed, "eliminated": [], "trace": trace})


@traced_request
@inference_session
async def explain_recommendation(message: str, conversation_id: UUID, anchor, event_sink=None, request_id=None):
    async def run():
        settings = get_settings()
        with SessionLocal() as session:
            conversation = conversation_context_service.get_or_create_conversation(session, conversation_id)
            conversation_context_service.append_message(session, conversation, "user", message)
            session.commit()
            result = recheck_anchor(anchor, session, event_sink)
            build = assemble_core_build(candidates=result.top_k, message=anchor.question, session=session) if anchor.core_build else None
            emit(event_sink, "narrator_started", "narrator", "正在解释原配置与使用需求的关系", status="running")
            with phase("narrator"):
                narration = await narrate_fusion(message="原始需求：" + anchor.question + "\n追问：" + message
                    + "\n解释刚才选中的同一配置。不得换型号、引入新用途；没有实测证据就说明无法证明游戏帧率。",
                    result=result, settings=settings, core_build=build, compact=True)
            attach_sources(session, narration, result)
            narration = narration.model_copy(update={
                "headline": "进一步解释上次配置（本轮已重新查询并核验数据库证据）。",
                "alternatives": [],
                "caveats": list(dict.fromkeys(["本轮保留上次选择并重新核对证据，没有重新推荐或排序。",
                    "规格证据不能单独证明具体游戏帧率或性价比；仍需预算、游戏和分辨率，以及相应实测或价格数据。",
                    *[c for c in narration.caveats if "模型未参与" not in c and "候选未通过" not in c]])),
            })
            conversation_context_service.append_message(session, conversation, "assistant", result.model_dump_json())
            session.commit()
            emit(event_sink, "narrator_completed", "narrator", "补充解释已整理", status="completed")
            return FusionRunResponse(request_id=request_id, conversation_id=conversation_id, status="completed",
                release_key=result.release_key, agents=[], result=result, natural_language=narration,
                presentation=presentation_of(narration), answer_purpose="explanation")
    emit(event_sink, "queued", "queue", "正在等待核验资源", status="queued")
    async with asyncio.timeout(float(get_settings().fusion_total_timeout_seconds)):
        return await inference_scheduler.run_serial(run)
