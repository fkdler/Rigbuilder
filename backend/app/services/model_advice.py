"""Capability-first catalogue advice; never infer vision from a model's license."""
from sqlalchemy import select

from app.context.service import conversation_context_service
from app.db.session import SessionLocal
from app.models.truth_v3 import CatalogEntity, AIModel, ModelCapability, EvidenceClaim
from app.schemas.recommendation import Recommendation
from app.services.events import emit
from app.verification import TruthVerifier
from app.verification.repository import SqlAlchemyTruthRepository

CAPABILITY_LABELS = {"vision_input": "图像输入与理解", "image_generation": "图像生成", "audio_input": "语音输入/识别"}


def capability_claims(session, entity_id, capabilities):
    supported = set(session.scalars(select(ModelCapability.capability_key).where(
        ModelCapability.model_id == entity_id, ModelCapability.support_status == "supported",
        ModelCapability.capability_key.in_(capabilities))))
    if supported != capabilities:
        return []
    fields = {"model.capability." + key for key in capabilities}
    evidence = session.scalars(select(EvidenceClaim).where(EvidenceClaim.entity_id == entity_id,
        EvidenceClaim.review_status == "accepted", EvidenceClaim.field_key.in_(fields))).all()
    claims = [{"claim_type": "fact", "entity_id": str(entity_id), "field_key": e.field_key,
               "value": "supported", "value_type": "string", "evidence_ids": [str(e.id)]}
              for e in evidence if e.normalized_value == "supported"]
    return claims if {c["field_key"] for c in claims} == fields else []


def verified_capability_candidates(session, capabilities):
    from app.services.model_choices import excluded_model_ids
    repository = SqlAlchemyTruthRepository(session)
    if repository.latest_release_key() is None:
        return []
    rows = session.execute(select(CatalogEntity, AIModel).join(AIModel, AIModel.entity_id == CatalogEntity.id)
        .join(ModelCapability, ModelCapability.model_id == CatalogEntity.id)
        .where(CatalogEntity.recommendable.is_(True), ModelCapability.capability_key.in_(capabilities),
               ModelCapability.support_status == "supported",
               CatalogEntity.id.not_in(excluded_model_ids.get()))
        .order_by(AIModel.total_parameters.asc().nullslast(), CatalogEntity.canonical_name)).unique().all()
    choices = []
    for entity, model in rows:
        claims = capability_claims(session, entity.id, capabilities)
        if not claims:
            continue
        request = Recommendation.model_validate({"recommendations": [{"candidate_id": str(entity.id),
            "candidate_type": "ai_model", "name": entity.canonical_name, "score": 0,
            "reasons": ["Check the requested capability"], "claims": claims}]})
        checked = TruthVerifier(repository).verify(request).candidates[0]
        if checked.candidate_valid and checked.claims and all(c.status == "supported" and c.valid_evidence_ids for c in checked.claims):
            choices.append((entity, model, checked))
    return choices


def catalogue_gap_answer(capabilities):
    need = "、".join(CAPABILITY_LABELS[k] for k in sorted(capabilities))
    return (f"抱歉，目前数据库没有找到已核验{need}能力的合适模型。仅有名称、参数量或许可证，不能证明模型具备这项能力，所以这次不提供不匹配的型号。\n\n"
        "你可以到 Hugging Face（https://huggingface.co/models）或 ModelScope（https://modelscope.cn/models）查看开源模型。"
        "阅读官方模型卡，核对任务类型、许可证、所需推理框架及下载文件；看图理解与生成图片属于不同任务。\n\n"
        "如果补充显卡型号、显存和具体用途（例如图片问答、OCR 或绘图），就能进一步缩小选择范围。")


async def reply_model_advice(message, conversation_id, capabilities, event_sink=None):
    with SessionLocal() as session:
        conversation = conversation_context_service.get_or_create_conversation(session, conversation_id)
        conversation_context_service.append_message(session, conversation, "user", message)
        session.commit()
        emit(event_sink, "tool_started", "tool", "正在查询模型能力证据", status="running")
        from app.services.model_choices import excluded_model_ids, replacement_gap_answer
        excluded = set(excluded_model_ids.get())
        choices = [choice for choice in verified_capability_candidates(session, capabilities)
                   if str(getattr(choice[0], 'id', '')) not in excluded]
        emit(event_sink, "tool_completed", "tool", "模型能力证据查询完成", status="completed")
        emit(event_sink, "verification", "verification", "模型能力核对完成", status="completed")
        if not choices:
            answer = replacement_gap_answer() if excluded else catalogue_gap_answer(capabilities)
            kind, verification = "chat_fallback", "not_verified"
        else:
            need = "、".join(CAPABILITY_LABELS[k] for k in sorted(capabilities))
            lines = ["以下是为您推荐的模型："]
            lines.extend(f"- {entity.canonical_name}" for entity, _, _ in choices[:3])
            lines += [f"\n推荐理由：\n这些候选模型支持{need}，与本次任务所需的输入输出能力相符。选择部署版本时，应结合显存、量化格式和推理框架确定配套文件。", "\n证据："]
            for entity, model, checked in choices[:3]:
                lines.append(f"- {entity.canonical_name}：{need}"
                    + (f"；官方模型卡：{model.official_model_card_url}" if model.official_model_card_url else ""))
            answer = "\n".join(lines)
            kind, verification = "catalogue_advice", "capability_verified"
        conversation_context_service.append_message(session, conversation, "assistant", answer)
        session.commit()
        return {"kind": kind, "conversation_id": str(conversation.id), "answer": answer,
                "model": "", "context_compressed": False, "verification": verification,
                "selected_model_ids": [str(entity.id) for entity, _, _ in choices[:3] if getattr(entity, 'id', None)]}
