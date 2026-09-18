"""Conversation-local model replacement state, distinct from user constraints."""
from contextvars import ContextVar
import re
from uuid import UUID

from sqlalchemy import select
from app.models import QueryJob


excluded_model_ids: ContextVar[tuple[str, ...]] = ContextVar("excluded_model_ids", default=())


def wants_another(message):
    text = message.strip()
    if re.search(r"(?:不|别|无需|不用)(?:要)?(?:再)?换|换(?:一?个)?(?:话题|问题)|换成|改成", text):
        return False
    return bool(re.search(
        r"换(?:一)?[个款种]|换另(?:一)?[个款种]|再(?:给我)?推荐(?:一)?[个款种]|"
        r"(?:推荐|找|选)(?:点|个|一款|一个)?(?:其他|别的|不同)|还有(?:没有)?(?:其他|别的)|"
        r"不要重复|别重复|不要(?:刚才|上次|之前)(?:的)?(?:那个|这款|模型)|"
        r"(?:another|different)\s+(?:one|model)|something else", text, re.I))


def model_replacement(message, original):
    if not original or not wants_another(message):
        return False
    from app.services.request_analysis import explicit_model_request
    # A purchase target in the new message takes precedence over model history.
    if re.search(r"(?:换|推荐|买|选)\s*(?:另)?(?:一)?[个款张台套]?\s*(?:显卡|GPU|CPU|处理器|电脑|主机|配置)", message, re.I):
        return False
    return explicit_model_request(original)


def canonical_ids(values):
    ids = set()
    for value in values or []:
        try:
            ids.add(str(UUID(str(value))))
        except (ValueError, TypeError, AttributeError):
            continue
    return sorted(ids)


def reference_from_job(job):
    request, payload = job.request_payload or {}, job.result_payload or {}
    intent = (request.get("routing_analysis") or {}).get("intent") or {}
    state = request.get("model_choice_state") or {}
    selected = []
    question = request.get("effective_message") or request.get("message", "")
    if payload.get("kind", "fusion") == "fusion" and payload.get("status") == "completed":
        result = payload.get("result") or {}
        question = ((result.get("trace") or {}).get("input_summary") or {}).get("anchor_question") or question
        candidates = result.get("top_k") or []
        presentation = payload.get("presentation") or payload.get("natural_language") or {}
        ids = {str(c.get("candidate_id")) for c in presentation.get("selections", [])}
        if not ids and candidates:
            ids = {str((presentation.get("primary") or candidates[0]).get("candidate_id"))}
        selected = [c["candidate_id"] for c in candidates
                    if c.get("candidate_type") == "ai_model" and str(c.get("candidate_id")) in ids]
    elif payload.get("kind") == "catalogue_advice":
        selected = payload.get("selected_model_ids") or []
    if not selected and intent.get("domain") != "ai_model" and not state:
        return None
    if payload.get("kind") == "chat":
        return None  # A chat/topic boundary must not inherit an old exclusion list.
    return {
        "question": question,
        "seen_ids": canonical_ids([*state.get("seen_ids", []), *selected]),
        "excluded_ids": canonical_ids(state.get("excluded_ids", [])),
    }


def load_model_choice_reference(session, conversation_id, anchor=None):
    if conversation_id is None:
        return None
    job = session.scalar(select(QueryJob).where(QueryJob.conversation_id == conversation_id,
        QueryJob.status == "completed").order_by(QueryJob.completed_at.desc(), QueryJob.created_at.desc()).limit(1))
    if job is not None:
        reference = reference_from_job(job)
        request = job.request_payload or {}
        # Backfill a pre-fix replacement chain once from completed jobs in this
        # conversation. New jobs carry cumulative state and need no history scan.
        if reference and "model_choice_state" not in request and (
            wants_another(request.get("message", "")) or
            (request.get("routing_analysis") or {}).get("continues_build")
        ):
            prior = session.scalars(select(QueryJob).where(QueryJob.conversation_id == conversation_id,
                QueryJob.status == "completed").order_by(QueryJob.completed_at.desc(), QueryJob.created_at.desc()).limit(50))
            seen = list(reference['seen_ids'])
            for row in prior:
                item = reference_from_job(row)
                if item is None:
                    break
                seen.extend(item['seen_ids'])
                old_request = row.request_payload or {}
                if 'model_choice_state' in old_request or not (
                    wants_another(old_request.get('message', '')) or
                    (old_request.get('routing_analysis') or {}).get('continues_build')
                ):
                    break
            reference['seen_ids'] = canonical_ids(seen)
        return reference
    if anchor and any(c.candidate_type == "ai_model" for c in anchor.selected):
        return {"question": anchor.question, "seen_ids": canonical_ids([
            c.candidate_id for c in anchor.selected if c.candidate_type == "ai_model"]), "excluded_ids": []}
    return None


def next_choice_state(message, reference, plan, continuation):
    from app.services.routing import is_explanation_followup
    if reference and is_explanation_followup(message):
        return {key: canonical_ids(reference.get(key, [])) for key in ('seen_ids', 'excluded_ids')}
    if not plan or plan.domain != "ai_model":
        return {}
    if not reference or not continuation:
        return {"seen_ids": [], "excluded_ids": []}
    seen = canonical_ids(reference.get("seen_ids", []))
    excluded = canonical_ids([*reference.get("excluded_ids", []), *(seen if wants_another(message) else [])])
    return {"seen_ids": seen, "excluded_ids": excluded}


def replacement_gap_answer():
    return ("已排除这轮对话中推荐过的模型。目前没有找到同时满足原有条件、且证据可核验的其他候选，"
            "因此不会重复推荐。你可以明确放宽模型系列、量化格式或资源限制后继续筛选。")
