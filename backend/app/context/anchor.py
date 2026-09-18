"""Conversation-local recommendation references; no global or cross-user memory."""
from dataclasses import dataclass
import json
from uuid import UUID

from sqlalchemy import select

from app.models import ConversationMessage, QueryJob
from app.schemas.fusion import FusionResult, CoreBuild


@dataclass(frozen=True)
class RecommendationAnchor:
    question: str
    result: FusionResult
    core_build: CoreBuild | None

    @property
    def selected(self):
        ids = {c.candidate_id for c in self.core_build.core} if self.core_build else {self.result.top_k[0].candidate_id}
        return [c for c in self.result.top_k if c.candidate_id in ids]

    def context(self):
        return json.dumps({"original_request": self.question, "selected_components": [
            {"name": c.canonical_name, "candidate_id": str(c.candidate_id), "verified_facts": [
                {"field": x.claim.field_key, "value": x.verification.canonical_value, "unit": x.verification.canonical_unit}
                for x in c.claims if x.verification.status == "supported"][:6]} for c in self.selected
        ]}, ensure_ascii=False, default=str, separators=(",", ":"))


def anchor_from_payload(payload, question):
    if not isinstance(payload, dict) or payload.get("kind", "fusion") != "fusion" or payload.get("status") != "completed":
        return None
    try:
        result = FusionResult.model_validate(payload["result"])
        if not result.top_k:
            return None
        raw_build = (payload.get("presentation") or payload.get("natural_language") or {}).get("core_build")
        build = CoreBuild.model_validate(raw_build) if raw_build else None
        if build and (not build.core or not {c.candidate_id for c in build.core}.issubset({c.candidate_id for c in result.top_k})):
            return None
        question = result.trace.input_summary.get("anchor_question") or question
        return RecommendationAnchor(question, result, build)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def load_recommendation_anchor(session, conversation_id: UUID | None):
    if conversation_id is None:
        return None
    # Inspect the latest completed answer, not the latest globally successful recommendation.
    job = session.scalar(select(QueryJob).where(QueryJob.conversation_id == conversation_id,
        QueryJob.status == "completed").order_by(QueryJob.completed_at.desc(), QueryJob.created_at.desc()).limit(1))
    if job is not None:
        request = job.request_payload or {}
        return anchor_from_payload(job.result_payload, request.get("effective_message") or request.get("message", ""))
    # Legacy/direct FusionService callers store raw FusionResult without a Job.
    messages = list(session.scalars(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id).order_by(ConversationMessage.sequence_no.desc()).limit(12)))
    answer = next((m for m in messages if m.role == "assistant"), None)
    if answer is None:
        return None
    try:
        raw = json.loads(answer.content)
        result = FusionResult.model_validate(raw)
    except (ValueError, TypeError):
        return None
    question = next((m.content for m in messages if m.role == "user" and m.sequence_no < answer.sequence_no), "")
    from app.services.build_assembler import assemble_core_build
    question = result.trace.input_summary.get("anchor_question") or question
    build = assemble_core_build(candidates=result.top_k, message=question, session=session)
    return RecommendationAnchor(question, result, build) if result.top_k else None


def load_previous_build_request(session, conversation_id: UUID | None):
    """Keep user requirements after a catalogue miss, without inventing an anchor."""
    if conversation_id is None:
        return None
    job = session.scalar(select(QueryJob).where(QueryJob.conversation_id == conversation_id,
        QueryJob.status == "completed").order_by(QueryJob.completed_at.desc(), QueryJob.created_at.desc()).limit(1))
    if job is None or job.resolved_mode != "fusion":
        return None
    request = job.request_payload or {}
    return request.get("effective_message") or request.get("message")
