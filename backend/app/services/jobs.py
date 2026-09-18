from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update

from app.db.session import SessionLocal
from app.models import AgentRun, Conversation, QueryEvent, QueryJob, ToolCall
from app.schemas.query import (
    AgentTraceResponse,
    QueryJobRequest,
    QueryJobResponse,
    QueryTraceResponse,
    ToolTraceResponse,
)
from app.schemas.fusion import FusionRunResponse
from app.services.chat import ChatService
from app.services.events import emit
from app.services.stream_usage import usage_sink
from app.services.token_ledger import meter_site_usage
from app.services.fusion import FusionTimeoutError, fusion_service
from app.services.routing import requested_component_roles, resolve_query_mode, is_explanation_followup, requested_model_capabilities
from app.context.anchor import load_recommendation_anchor, anchor_from_payload, load_previous_build_request
from app.services.requirements import continue_request
from app.services.request_analysis import analyze_request
from app.services.execution_intent import ExecutionIntent, execution_intent
from app.services.model_choices import (load_model_choice_reference, next_choice_state,
                                       excluded_model_ids, replacement_gap_answer)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class QueryJobNotFoundError(LookupError):
    pass


class QueryJobManager:
    """Owns the job lifecycle.

    ``get``, ``cancel``, ``trace`` and ``events_after`` take a bare job id and do
    **not** check ownership: they are the mechanism, not the guard. Every HTTP
    caller must first resolve the row through ``app.api.deps.owned_job``, which is
    what turns "this job exists" into "this job belongs to the caller". Keeping the
    split explicit means a new route gets one obvious place to add the check.
    """

    def __init__(self) -> None:
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    def recover_after_restart(self) -> int:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            jobs = session.scalars(select(QueryJob).where(QueryJob.status.in_(["queued", "running", "cancel_requested"]))).all()
            for job in jobs:
                job.status = "failed"
                job.error_code = "server_restarted"
                job.error_message = "The API server restarted before this task completed."
                job.completed_at = now
                self._append_event(session, job.id, {
                    "event_type": "job_failed", "phase": "job", "status": "failed",
                    "title": "服务已重启", "summary": "任务未自动重放，请重新提交。",
                })
            session.commit()
            return len(jobs)

    def submit(self, payload: QueryJobRequest, owner_id: UUID) -> QueryJobResponse:
        """Queue a job for the authenticated account.

        ``owner_id`` is required rather than optional on purpose: a job with no
        owner is invisible to every account, so omitting it would be a silent
        data-loss bug instead of an error.
        """
        with SessionLocal() as session:
            anchor = load_recommendation_anchor(session, payload.conversation_id)
            model_reference = load_model_choice_reference(session, payload.conversation_id, anchor)
            previous_request = (anchor.question if anchor else
                model_reference['question'] if model_reference else load_previous_build_request(session, payload.conversation_id))
            resolved = resolve_query_mode(payload.message, payload.mode, bool(payload.constraints), has_recommendation=anchor is not None)
            job = QueryJob(
                owner_id=owner_id,
                conversation_id=payload.conversation_id,
                requested_mode=payload.mode,
                resolved_mode=resolved,
                request_payload={**payload.model_dump(mode="json"), "model_choice_reference": model_reference, "previous_build_request": previous_request, "recommendation_reference": ({
                    "kind": "fusion", "status": "completed", "result": anchor.result.model_dump(mode="json"),
                    "presentation": {"core_build": anchor.core_build.model_dump(mode="json") if anchor.core_build else None},
                    "question": anchor.question,
                } if anchor is not None else None)},
            )
            session.add(job)
            session.flush()
            self._append_event(session, job.id, {
                "event_type": "queued", "phase": "queue", "status": "queued",
                "title": "任务已排队", "summary": "等待请求分析与最终路由。",
            })
            session.commit()
            response = self._response(session, job)
        task = asyncio.create_task(self._execute(job.id, payload), name=f"query-job-{job.id}")
        self._tasks[job.id] = task

        def completed(done_task: asyncio.Task[None], job_id: UUID = job.id) -> None:
            self._tasks.pop(job_id, None)
            if done_task.cancelled():
                self._mark_cancelled(job_id)

        task.add_done_callback(completed)
        return response

    async def _execute(self, job_id: UUID, payload: QueryJobRequest) -> None:
        started = datetime.now(timezone.utc)
        with SessionLocal() as session:
            job = session.get(QueryJob, job_id)
            if job is None or job.status == "cancel_requested":
                return
            job.status = "running"
            request_id = job.request_id
            job.started_at = started
            job.heartbeat_at = started
            self._append_event(session, job_id, {
                "event_type": "started", "phase": "job", "status": "running",
                "title": "开始执行", "summary": "正在等待推理调度。",
            })
            session.commit()

        def sink(event: dict[str, Any]) -> None:
            with SessionLocal() as event_session:
                self._append_event(event_session, job_id, self._sanitize_event(event))
                job_row = event_session.get(QueryJob, job_id)
                if job_row is not None:
                    job_row.heartbeat_at = datetime.now(timezone.utc)
                event_session.commit()

        usage_context = usage_sink.set(sink)
        meter_context = meter_site_usage.set(True)
        intent_context = execution_intent.set(None)
        exclusion_context = excluded_model_ids.set(())
        try:
            from app.inference.context_window import refresh_context_windows
            from app.core.config import get_settings
            await refresh_context_windows(get_settings(), force=True)
            with SessionLocal() as session:
                job_row = session.get(QueryJob, job_id)
                resolved = job_row.resolved_mode
                model_reference = (job_row.request_payload or {}).get("model_choice_reference")
                reference = (job_row.request_payload or {}).get("recommendation_reference")
                anchor = anchor_from_payload(reference, reference.get("question", "")) if reference else None
                previous_request = anchor.question if anchor else (job_row.request_payload or {}).get("previous_build_request")
            emit(sink, "routing_started", "routing", "正在分析请求与对话衔接", status="running")
            resolved, continuation, analysis = await analyze_request(
                payload.message, payload.mode, bool(payload.constraints), original=previous_request)
            effective_message = continue_request(payload.message, previous_request, continuation)
            from app.services.requirements import bind_referenced_components
            plan = ExecutionIntent.model_validate(analysis["intent"]) if analysis.get("intent") else None
            if not plan or plan.domain != 'ai_model':
                effective_message = bind_referenced_components(payload.message, effective_message, anchor)
            execution_intent.set(plan)
            choice_state = next_choice_state(payload.message, model_reference, plan, continuation)
            excluded_model_ids.set(tuple(choice_state.get('excluded_ids', [])))
            with SessionLocal() as session:
                job_row = session.get(QueryJob, job_id)
                job_row.resolved_mode = resolved
                job_row.request_payload = {**job_row.request_payload, "routing_analysis": analysis,
                                           "effective_message": effective_message, "model_choice_state": choice_state}
                session.commit()
            emit(sink, "routing_completed", "routing", "请求分析完成", status="completed", detail=analysis)
            result_error: str | None = None
            result_error_code: str | None = None
            if resolved == "chat":
                result = await ChatService.reply(payload.message, payload.conversation_id, event_sink=sink)
                result_payload = {
                    "kind": "chat", "conversation_id": str(result.conversation_id), "answer": result.answer,
                    "model": result.model, "context_compressed": result.context_compressed,
                    "fusion_available": True,
                }
                conversation_id = result.conversation_id
            elif plan and plan.task == "introduce" and not payload.constraints:
                from app.services.product_info import introduce_product
                result_payload = await introduce_product(payload.message, payload.conversation_id, plan,
                    anchor=anchor, event_sink=sink)
                conversation_id = UUID(result_payload["conversation_id"])
            elif requested_model_capabilities(effective_message) and not payload.constraints:
                from app.services.model_advice import reply_model_advice
                result_payload = await reply_model_advice(effective_message, payload.conversation_id,
                    requested_model_capabilities(effective_message), event_sink=sink)
                conversation_id = UUID(result_payload["conversation_id"])
            else:
                if anchor is not None and is_explanation_followup(payload.message) and not payload.constraints:
                    from app.services.explanation import explain_recommendation
                    result = await explain_recommendation(payload.message, payload.conversation_id, anchor,
                                                          event_sink=sink, request_id=request_id)
                else:
                    try:
                        result = await fusion_service.run(
                            effective_message, payload.conversation_id, payload.constraints, payload.top_k,
                            event_sink=sink, request_id=request_id,
                        )
                    except FusionTimeoutError:
                        # Preserve the conversation created before timeout, so
                        # fallback sees the original request without duplicating it.
                        with SessionLocal() as session:
                            run = session.scalar(select(AgentRun).where(AgentRun.request_id == request_id).limit(1))
                            conv = payload.conversation_id or (run.conversation_id if run else None)
                        if conv is None:
                            raise
                        result = FusionRunResponse(request_id=request_id, conversation_id=conv,
                            status="failed", agents=[], error="本次数据库核验超时。")
                if plan and plan.domain == 'ai_model' and result.result is not None:
                    from app.services.local_models import enrich_model_presentation
                    with SessionLocal() as session:
                        for presentation in (result.presentation, result.natural_language):
                            if presentation is not None:
                                enrich_model_presentation(session, presentation, result.result, effective_message)
                result_payload = {"kind": "fusion", **result.model_dump(mode="json")}
                conversation_id = result.conversation_id
                if result.status == "failed":
                    result_error = result.error or "Fusion failed"
                    agent_codes = [agent.error_code for agent in result.agents if agent.error_code]
                    infrastructure_failure = agent_codes and all(code in {
                        "router_unavailable", "model_load_failed", "router_http_error",
                    } for code in agent_codes)
                    if infrastructure_failure:
                        result_error = "本地推理服务当前不可用，请检查模型服务后重试。"
                    elif plan and plan.domain == 'ai_model':
                        answer = ('当前目录没有找到同时满足这些型号、量化或资源条件且有可核对证据的模型候选。'
                                  '这不代表模型不存在或无法运行。请确认模型名称、量化格式与可用显存／内存；'
                                  '也可以放宽其中一项条件后继续筛选。')
                        if excluded_model_ids.get():
                            answer = replacement_gap_answer()
                        result_payload = {'kind': 'chat_fallback', 'conversation_id': str(conversation_id),
                                          'answer': answer, 'model': '', 'context_compressed': False,
                                          'verification': 'not_verified'}
                        result_error = None
                    elif requested_component_roles(effective_message) or analysis["requires_database"]:
                        # A failed verified hardware request must not become an
                        # unconstrained LLM parts list with invented prices/specs.
                        from app.services.hardware_intent import sku_requests
                        pinned = [spec["required"] for spec in sku_requests(payload.message).values() if spec["required"]]
                        named = "（指定型号：" + "、".join(pinned) + "）" if pinned else ""
                        answer = ("本次未能得到满足约束的已核验配置" + named + "。"
                                  "不会擅自替换指定型号，也不能据此断言型号不存在。"
                                  "请核对型号写法并补充预算、用途和分辨率；已有条件会保留。"
                                  "价格、帧率、噪声和完整兼容性未核验，暂不能保证预算或性能。")
                        result_payload = {"kind": "chat_fallback", "conversation_id": str(conversation_id),
                                          "answer": answer, "model": "", "context_compressed": False,
                                          "verification": "not_verified"}
                        result_error = None
                    else:
                        # A recommendation remains useful when catalogue verification
                        # cannot produce a publishable candidate.  Ask the fast path
                        # once and label the result as unverified instead of exposing
                        # internal Agent failure text to the user.
                        try:
                            emit(sink, "fallback_started", "chat", "正在整理通用建议", status="running")
                            fallback = await ChatService.reply(payload.message, conversation_id, event_sink=sink,
                                persist_user=False, service_note=(
                                    "The database verification did not find a publishable match for the newest request. "
                                    "Explain the catalogue/evidence gap in Chinese. Do not invent a matching product or claim verification. "
                                    "Give practical next steps; for models suggest official model cards on Hugging Face or ModelScope, "
                                    "checking the requested modality, hardware needs, runtime and license. Do not reinterpret an unrelated past recommendation as the current match."))
                            result_payload = {
                                "kind": "chat_fallback", "conversation_id": str(fallback.conversation_id),
                                "answer": fallback.answer, "model": fallback.model,
                                "context_compressed": fallback.context_compressed,
                                "verification": "not_verified",
                            }
                            conversation_id = fallback.conversation_id
                            result_error = None
                        except Exception:
                            result_error = "推荐服务暂时无法完成，请稍后重试。"
                    if result_error:
                        result_payload = {"kind": "chat_fallback", "conversation_id": str(conversation_id),
                                          "answer": result_error, "verification": "not_verified",
                                          "model": "", "context_compressed": False}
            with SessionLocal() as session:
                job = session.get(QueryJob, job_id)
                if job is None:
                    return
                job.status = "failed" if result_error else "completed"
                job.conversation_id = conversation_id
                job.result_payload = result_payload
                self._claim_conversation(session, conversation_id, job.owner_id)
                if result_error:
                    job.error_code = result_error_code or "fusion_failed"
                    job.error_message = result_error
                job.completed_at = datetime.now(timezone.utc)
                self._append_event(session, job_id, {
                    "event_type": "job_failed" if result_error else "job_completed",
                    "phase": "job", "status": job.status,
                    "title": "任务失败" if result_error else "任务完成",
                    "summary": result_error or "结果已保存。",
                    "duration_ms": (job.completed_at - started).total_seconds() * 1000,
                })
                session.commit()
        except asyncio.CancelledError:
            self._mark_cancelled(job_id)
            raise
        except Exception as exc:
            provider_code = getattr(exc, "code", None)
            known_provider_codes = {
                "router_unavailable", "router_http_error", "model_load_failed",
                "llm_timeout", "llm_transport_error", "llm_invalid_response", "anchor_evidence_changed",
            }
            code = (
                provider_code if provider_code in known_provider_codes
                else "agent_timeout" if isinstance(exc, (TimeoutError, FusionTimeoutError))
                else "job_failed"
            )
            name = type(exc).__name__.lower()
            if code == "job_failed" and "router" in name:
                code = "router_unavailable"
            elif code == "job_failed" and "llm" in name:
                code = "model_or_llm_error"
            safe_messages = {
                "anchor_evidence_changed": "上次配置的部分证据已变更或无法核验，请重新查询配置；当前不能把旧证据作为新结论。",
                "agent_timeout": "The Agent exceeded its execution time budget.",
                "router_unavailable": "The inference Router is unavailable.",
                "router_http_error": "The inference Router rejected the model request.",
                "model_load_failed": "The configured model failed to load on the inference Router.",
                "llm_timeout": "The inference request exceeded its response timeout.",
                "llm_transport_error": "The connection to the inference Router failed.",
                "llm_invalid_response": "The inference Router returned an invalid response.",
                "model_or_llm_error": "The model could not load or complete the request.",
                "job_failed": "服务暂时无法完成本次请求，请稍后重试；目前不能据此给出可靠推荐。",
            }
            with SessionLocal() as session:
                job = session.get(QueryJob, job_id)
                if job is not None and job.status != "cancelled":
                    job.status = "failed"
                    job.error_code = code
                    job.error_message = safe_messages[code]
                    job.completed_at = datetime.now(timezone.utc)
                    self._append_event(session, job_id, {
                        "event_type": "job_failed", "phase": "job", "status": "failed",
                        "title": "任务失败", "summary": job.error_message,
                    })
                    session.commit()

        finally:
            usage_sink.reset(usage_context)
            meter_site_usage.reset(meter_context)
            execution_intent.reset(intent_context)
            excluded_model_ids.reset(exclusion_context)

    @staticmethod
    def _claim_conversation(session, conversation_id: UUID | None, owner_id: UUID | None) -> None:
        """Attach a conversation created by this request to the requesting account.

        The fusion/chat services create the conversation themselves and only learn
        the account at this boundary, so ownership is filled in here. The update is
        guarded by ``owner_id IS NULL``, which makes it strictly additive:

        * a conversation this request just created is owner-less, so it is claimed;
        * a conversation that already had an owner keeps it, because the write is a
          no-op -- so this can never move somebody else's data;
        * a conversation that survives a cancellation before this point keeps its
          NULL owner, i.e. stays non-public. Losing a half-written session is the
          conservative failure, and Plan_V4.5 §11.2 prefers it to guessing.
        """
        if conversation_id is None or owner_id is None:
            return
        session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.owner_id.is_(None))
            .values(owner_id=owner_id)
        )

    def cancel(self, job_id: UUID) -> QueryJobResponse:
        with SessionLocal() as session:
            job = session.get(QueryJob, job_id)
            if job is None:
                raise QueryJobNotFoundError
            if job.status not in TERMINAL_STATUSES:
                job.status = "cancel_requested"
                self._append_event(session, job_id, {
                    "event_type": "cancel_requested", "phase": "job", "status": "running",
                    "title": "正在取消", "summary": "已向运行任务发送取消信号。",
                })
                session.commit()
            task = self._tasks.get(job_id)
            if task is not None and not task.done():
                task.cancel()
            elif job.status == "cancel_requested":
                self._mark_cancelled(job_id)
                session.refresh(job)
            return self._response(session, job)

    def _mark_cancelled(self, job_id: UUID) -> None:
        with SessionLocal() as session:
            job = session.get(QueryJob, job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return
            job.status = "cancelled"
            job.error_code = "task_cancelled"
            job.error_message = "The task was cancelled by the user."
            job.completed_at = datetime.now(timezone.utc)
            self._append_event(session, job_id, {
                "event_type": "job_cancelled", "phase": "job", "status": "cancelled",
                "title": "任务已取消", "summary": "推理任务已停止。",
            })
            session.commit()

    def get(self, job_id: UUID) -> QueryJobResponse:
        with SessionLocal() as session:
            job = session.get(QueryJob, job_id)
            if job is None:
                raise QueryJobNotFoundError
            return self._response(session, job)

    def events_after(self, job_id: UUID, after: int) -> list[QueryEvent]:
        with SessionLocal() as session:
            if session.get(QueryJob, job_id) is None:
                raise QueryJobNotFoundError
            return list(session.scalars(
                select(QueryEvent).where(QueryEvent.job_id == job_id, QueryEvent.sequence > after)
                .order_by(QueryEvent.sequence)
            ).all())

    def trace(self, job_id: UUID) -> QueryTraceResponse:
        """Return a persisted, user-safe execution trace without model reasoning."""
        with SessionLocal() as session:
            job = session.get(QueryJob, job_id)
            if job is None:
                raise QueryJobNotFoundError
            runs = list(session.scalars(
                select(AgentRun)
                .where(AgentRun.request_id == job.request_id)
                .order_by(AgentRun.created_at, AgentRun.id)
            ).all())
            result_agents = {
                str(item.get("agent_run_id")): item
                for item in (job.result_payload or {}).get("agents", [])
                if isinstance(item, dict) and item.get("agent_run_id")
            }
            agents: list[AgentTraceResponse] = []
            for run in runs:
                calls = list(session.scalars(
                    select(ToolCall)
                    .where(ToolCall.agent_run_id == run.id)
                    .order_by(ToolCall.sequence_no)
                ).all())
                evidence_ids: set[UUID] = set()
                public_agent = result_agents.get(str(run.id), {})
                verification = public_agent.get("verification") if isinstance(public_agent, dict) else None
                if isinstance(verification, dict):
                    for candidate in verification.get("candidates", []):
                        if not isinstance(candidate, dict):
                            continue
                        for claim in candidate.get("claims", []):
                            if not isinstance(claim, dict):
                                continue
                            for raw_id in claim.get("valid_evidence_ids", []):
                                try:
                                    evidence_ids.add(UUID(str(raw_id)))
                                except (TypeError, ValueError):
                                    continue
                tool_rows: list[ToolTraceResponse] = []
                for call in calls:
                    arguments: dict[str, Any] = {}
                    if call.tool == "query_database" and call.success:
                        sql = call.arguments.get("sql") if isinstance(call.arguments, dict) else None
                        if isinstance(sql, str):
                            arguments["sql"] = sql
                    tool_rows.append(ToolTraceResponse(
                        sequence=call.sequence_no,
                        tool=call.tool,
                        arguments=arguments,
                        query_id=call.query_id,
                        success=call.success,
                        error_code=call.error_code,
                        truncated=call.result_truncated,
                        duration_ms=call.duration_ms,
                        result_summary=call.result_summary,
                    ))
                agents.append(AgentTraceResponse(
                    agent_run_id=run.id,
                    model_id=run.model_id,
                    status=run.status,
                    error_code=run.error_code,
                    metrics=run.metrics or {},
                    evidence_ids=sorted(evidence_ids, key=str),
                    tools=tool_rows,
                ))
            return QueryTraceResponse(job_id=job.id, request_id=job.request_id, agents=agents)

    @staticmethod
    def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
        allowed = {"event_type", "phase", "status", "agent", "model", "title", "summary", "duration_ms"}
        safe = {key: value for key, value in event.items() if key in allowed}
        detail = event.get("detail")
        if isinstance(detail, dict):
            detail_allowed = {"tool", "purpose", "table", "row_count", "truncated", "round", "tool_calls",
                              "prompt_tokens", "completion_tokens", "total_tokens", "protocol", "warning", "call_id",
                              # How many Agents this request was worth, and which ones
                              # (Plan_V4.1): the decision is a policy input, so it has to
                              # be visible in the trace rather than inferred afterwards.
                              "agent_count", "models", "source", "model", "requires_database", "continues_build",
                              "model_requires_database", "model_continues_build", "intent"}
            safe["detail"] = {key: value for key, value in detail.items() if key in detail_allowed}
        return safe

    @staticmethod
    def _append_event(session, job_id: UUID, event: dict[str, Any]) -> None:
        # Lock the parent row while allocating the next sequence so cancellation
        # cannot race a running task's event callback.
        session.execute(select(QueryJob.id).where(QueryJob.id == job_id).with_for_update())
        sequence = int(session.scalar(select(func.coalesce(func.max(QueryEvent.sequence), 0)).where(QueryEvent.job_id == job_id)) or 0) + 1
        session.add(QueryEvent(
            job_id=job_id, sequence=sequence, event_type=event["event_type"], phase=event["phase"],
            status=event.get("status", "completed"), agent=event.get("agent"), model=event.get("model"),
            title=event["title"], summary=event.get("summary"), detail=event.get("detail"),
            duration_ms=event.get("duration_ms"),
        ))

    @staticmethod
    def _response(session, job: QueryJob) -> QueryJobResponse:
        last_sequence = int(session.scalar(
            select(func.coalesce(func.max(QueryEvent.sequence), 0)).where(QueryEvent.job_id == job.id)
        ) or 0)
        return QueryJobResponse(
            id=job.id, request_id=job.request_id, conversation_id=job.conversation_id,
            requested_mode=job.requested_mode, resolved_mode=job.resolved_mode, status=job.status,
            result=job.result_payload, error_code=job.error_code, error=job.error_message,
            last_sequence=last_sequence, created_at=job.created_at, started_at=job.started_at,
            completed_at=job.completed_at,
        )


query_job_manager = QueryJobManager()


def serialize_sse(event: QueryEvent) -> str:
    payload = {
        "sequence": event.sequence, "event_type": event.event_type, "phase": event.phase,
        "status": event.status, "agent": event.agent, "model": event.model, "title": event.title,
        "summary": event.summary, "detail": event.detail, "duration_ms": event.duration_ms,
        "created_at": event.created_at.isoformat(),
    }
    return f"id: {event.sequence}\nevent: execution\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["QueryJobManager", "QueryJobNotFoundError", "TERMINAL_STATUSES", "query_job_manager", "serialize_sse"]
