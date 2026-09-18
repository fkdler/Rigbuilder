"""Two-Agent orchestration and deterministic fusion service."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.agents.loop import (
    AgentLoopResult,
    ToolCallEvent,
    run_agent_loop,
    run_agent_loop_protocol,
)
from app.agents.prompts import build_protocol_system_prompt, build_system_prompt, build_focused_system_prompt
from app.agents.selection import fact_selection_roles
from app.agents.protocol.adapter import ModelHandle
from app.agents.states import AgentRunStatus
from app.context.service import SYSTEM_PROMPT, conversation_context_service, consolidate_system_messages
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.fusion import AgentFusionInput, FusionEngine, FusionError
from app.inference.profiles import (
    AGENT_COUNT,
    ModelProfile,
    build_profiles,
    settings_parallel_capable,
)
from app.inference.scheduler import inference_scheduler
from app.inference.router import loaded_first
from app.models import AgentMessage, AgentRun, ToolCall
from app.schemas.fusion import (
    ConfirmedConstraint,
    FusionAgentResult,
    FusionRunResponse,
    NaturalLanguage,
    PresentationResult,
)
from app.schemas.recommendation import Recommendation
from app.schemas.verification import RecommendationVerification
from app.services.llm import (
    LLMConfigurationError, LLMServiceError, complete_chat, generate_reply,
    native_capability, set_native_capability,
)
from app.services.build_assembler import assemble_core_build
from app.services.execution_intent import planned_messages
from app.services.events import EventSink, emit
from app.services.narrator import narrate_fusion
from app.services.presentation_sources import attach_sources
from app.services.routing import select_agent_models, requested_component_roles
from app.services.request_scope import check_requested_categories, select_role_coverage
from app.services.timing import phase, traced_request
from app.inference.http import inference_session
from app.tools.schema import inspect_database
from app.verification import TruthVerificationError, TruthVerifier
from app.verification.repository import SqlAlchemyTruthRepository


def _row_count(summary: str | None) -> int | None:
    if not summary or not summary.startswith("rows="):
        return None
    try:
        return int(summary.split()[0].split("=", 1)[1])
    except (ValueError, IndexError):
        return None


class FusionConfigurationError(LLMConfigurationError):
    pass


class FusionTimeoutError(RuntimeError):
    pass


class FusionDataError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class AgentWorkerOutcome:
    """Isolated result of one parallel Agent worker."""

    model_id: str
    run_id: UUID | None
    agent_result: FusionAgentResult
    fusion_input: AgentFusionInput | None


class FusionService:
    @staticmethod
    @traced_request
    @inference_session
    async def run(
        message: str,
        conversation_id: UUID | None,
        constraints: list[ConfirmedConstraint],
        top_k: int,
        event_sink: EventSink | None = None,
        request_id: UUID | None = None,
    ) -> FusionRunResponse:
        settings = get_settings()
        configured = settings.llm_test_model_id_list
        # Every verified request uses the same pair, including complex builds.
        if len(configured) != AGENT_COUNT:
            raise FusionConfigurationError("LLM_TEST_MODEL_IDS must contain exactly two model IDs.")
        if len(set(configured)) != len(configured):
            raise FusionConfigurationError("LLM_TEST_MODEL_IDS must contain distinct model IDs.")
        model_ids = select_agent_models(message, constraints, settings)
        if not model_ids:
            raise FusionConfigurationError("No Agent model could be selected for this request.")
        if not settings.llm_base_url.rstrip("/").startswith(("http://", "https://")):
            raise FusionConfigurationError("LLM_BASE_URL must start with http:// or https://")
        request_id = request_id or uuid4()
        queued_at = time.perf_counter()
        queue_duration = [0]
        emit(event_sink, "queued", "queue", "等待推理调度", status="queued")

        def acquired() -> None:
            queue_duration[0] = int((time.perf_counter() - queued_at) * 1000)
            emit(event_sink, "started", "queue", "已取得推理锁", status="completed",
                 duration_ms=queue_duration[0])
        try:
            async with asyncio.timeout(settings.fusion_total_timeout_seconds):
                return await inference_scheduler.run_serial(
                    lambda: FusionService._run(
                        message, conversation_id, constraints, top_k, request_id, model_ids,
                        event_sink, queue_duration[0]
                    ),
                    on_acquired=acquired,
                )
        except TimeoutError as exc:
            try:
                FusionService._mark_request_timed_out(request_id)
            except Exception:
                # A cleanup failure must not replace the actionable timeout
                # response with an unrelated 500 error.
                pass
            raise FusionTimeoutError(
                f"Fusion request exceeded the {settings.fusion_total_timeout_seconds:g}-second total timeout."
            ) from exc

    @staticmethod
    async def _run(
        message: str,
        conversation_id: UUID | None,
        constraints: list[ConfirmedConstraint],
        top_k: int,
        request_id: UUID,
        model_ids: list[str],
        event_sink: EventSink | None = None,
        queue_duration_ms: int = 0,
    ) -> FusionRunResponse:
        settings = get_settings()
        from app.inference.context_window import refresh_context_windows
        await refresh_context_windows(settings)
        configured_order = list(model_ids)
        if settings_parallel_capable(settings, model_ids):
            # Resident parallel path: one llama-server per selected profile.
            return await FusionService._run_parallel(
                message, conversation_id, constraints, top_k, request_id, configured_order,
                event_sink, queue_duration_ms, settings,
            )
        model_ids, router_warning = await loaded_first(model_ids)
        if router_warning:
            emit(event_sink, "router_warning", "model", "无法读取 Router 状态", status="warning",
                 summary="已按固定模型顺序继续。", detail={"warning": router_warning})
        emit(event_sink, "fusion_started", "fusion",
             f"启动 {len(model_ids)} 个 Agent 的可信融合", status="running",
             detail={"agent_count": len(model_ids), "models": list(model_ids)})
        with SessionLocal() as session:
            repository = SqlAlchemyTruthRepository(session)
            release_key = repository.latest_release_key()
            if release_key is None:
                raise FusionDataError("truth_release_unavailable", "No accepted Truth DB release is available.")

            conversation = conversation_context_service.get_or_create_conversation(session, conversation_id)
            conversation_context_service.append_message(session, conversation, "user", message)
            with phase("db_persist"):
                session.commit()
            with phase("context_prepare"):
                prepared = conversation_context_service.prepare_context(session, conversation.id, settings)
            system_prompt = build_system_prompt(inspect_database())
            base_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            for prepared_message in prepared.messages:
                if prepared_message["role"] == "system" and prepared_message["content"] == SYSTEM_PROMPT:
                    continue
                base_messages.append(prepared_message)
            roles = requested_component_roles(message)
            if roles:
                base_messages.append({"role": "system", "content": "Current requested component roles: " + ", ".join(sorted(roles)) + ". Historical recommendations do not override the current request. Query the catalogue for explicitly requested hardware or series first, then select compatible supporting parts around it. User-required parts take priority over default performance tiers or your preferred alternatives. If no verified compatible build satisfies them, explain the constraint conflict or evidence gap; never silently substitute parts or infer nonexistence from missing data."})
            if constraints:
                base_messages.append({"role": "system", "content": "Confirmed constraints: " + str([c.model_dump(mode="json") for c in constraints])})

            base_messages = planned_messages(base_messages, message)
            base_messages = consolidate_system_messages(base_messages)
            agent_results: list[FusionAgentResult] = []
            fusion_inputs: list[AgentFusionInput] = []
            for model_id in model_ids:
                agent_started_at = time.perf_counter()
                emit(event_sink, "model_loading", "model", "准备模型", status="running", model=model_id)
                emit(event_sink, "agent_started", "agent", "Agent 开始执行", status="running",
                     agent=model_id, model=model_id)
                run = AgentRun(
                    request_id=request_id,
                    conversation_id=conversation.id,
                    status=AgentRunStatus.RUNNING,
                    model_id=model_id,
                )
                session.add(run)
                session.flush()
                run_id = run.id
                with phase("db_persist"):
                    session.commit()
                sequence = [0]

                def record_message(role: str, content: str, token_count: int) -> None:
                    sequence[0] += 1
                    session.add(AgentMessage(
                        agent_run_id=run_id, sequence_no=sequence[0], role=role,
                        content=content, token_count=token_count,
                    ))
                    with phase("db_persist"):
                        session.commit()

                def record_tool_call(event: ToolCallEvent) -> None:
                    sequence[0] += 1
                    session.add(ToolCall(
                        agent_run_id=run_id, sequence_no=sequence[0], tool=event.tool,
                        arguments=event.arguments, query_id=event.query_id, success=event.success,
                        error_code=event.error_code, result_truncated=event.result_truncated,
                        duration_ms=event.duration_ms, result_summary=event.result_summary,
                    ))
                    with phase("db_persist"):
                        session.commit()
                    emit(
                        event_sink, "tool_completed", "tool", "数据库工具执行完成",
                        status="completed" if event.success else "failed", agent=model_id, model=model_id,
                        duration_ms=event.duration_ms, summary=event.result_summary,
                        detail={
                            "tool": event.tool, "purpose": "读取硬件与模型事实",
                            "row_count": _row_count(event.result_summary), "truncated": event.result_truncated,
                        },
                    )

                def record_tool_start(tool: str, _arguments: dict) -> None:
                    emit(
                        event_sink, "tool_started", "tool", "数据库工具开始执行", status="running",
                        agent=model_id, model=model_id,
                        detail={"tool": tool, "purpose": "读取硬件与模型事实"},
                    )

                llm_call_index = [0]

                def record_llm_call(call) -> None:
                    llm_call_index[0] += 1
                    if llm_call_index[0] == 1:
                        emit(event_sink, "model_ready", "model", "模型已就绪", status="completed",
                             agent=model_id, model=model_id, duration_ms=call.duration_ms)
                    emit(
                        event_sink, "llm_completed", "llm", "LLM 轮次完成", status="completed",
                        agent=model_id, model=model_id, duration_ms=call.duration_ms,
                        detail={
                            "round": llm_call_index[0], "prompt_tokens": call.usage.get("prompt_tokens"),
                            "completion_tokens": call.usage.get("completion_tokens"),
                            "total_tokens": call.usage.get("total_tokens"), "protocol": call.call_mode,
                        },
                    )

                try:
                    emit(event_sink, "llm_started", "llm", "LLM 开始生成", status="running",
                         agent=model_id, model=model_id)
                    async with asyncio.timeout(settings.agent_total_timeout_seconds):
                        capability = native_capability(model_id)
                        loop_result = await run_agent_loop(
                            messages=list(base_messages),
                            settings=settings,
                            llm=lambda messages, selected=model_id: generate_reply(messages, model_id=selected),
                            native_llm=lambda messages, selected=model_id, **kwargs: complete_chat(
                                messages, model_id=selected, **kwargs
                            ),
                            native_allowed=capability is not False,
                            evidence_session=session,
                            on_message=record_message,
                            on_tool_call=record_tool_call,
                            on_tool_start=record_tool_start,
                            on_llm_call=record_llm_call,
                        )
                        if capability is None:
                            tools_supported = bool(
                                loop_result.metrics.get(
                                    "native_tools_supported", loop_result.protocol == "native",
                                )
                            )
                            set_native_capability(model_id, tools_supported)
                            if not tools_supported:
                                emit(
                                    event_sink, "native_tool_fallback", "llm",
                                    "Native tools unavailable; legacy protocol enabled", status="warning",
                                    agent=model_id, model=model_id,
                                    summary="This model will not be probed again until the API process restarts.",
                                    detail={"protocol": "legacy"},
                                )
                except TimeoutError:
                    loop_result = _failed_result(model_id, "agent_timeout", "Agent run exceeded its total timeout.")
                except LLMServiceError as exc:
                    loop_result = _failed_result(model_id, getattr(exc, "code", "llm_error"), str(exc))

                recommendation: Recommendation | None = None
                verification = None
                verification_started = time.perf_counter()
                emit(event_sink, "verification", "verification", "Verifying facts", status="running",
                     agent=model_id, model=model_id)
                if loop_result.recommendation is not None:
                    try:
                        recommendation = Recommendation.model_validate(loop_result.recommendation)
                        verification = TruthVerifier(repository).verify(recommendation)
                        verification = check_requested_categories(verification, message, session)
                        if verification.release_key != release_key:
                            raise TruthVerificationError(
                                "truth_release_changed", "Truth DB release changed during the fusion request."
                            )
                    except TruthVerificationError as exc:
                        loop_result = _failed_result(model_id, exc.code, str(exc), loop_result)
                        recommendation = None
                        verification = None
                    except Exception:
                        loop_result = _failed_result(
                            model_id, "truth_verification_error", "Truth verification could not complete.", loop_result
                        )
                        recommendation = None
                        verification = None

                has_valid_candidate = bool(
                    verification is not None
                    and any(candidate.candidate_valid for candidate in verification.candidates)
                )
                if recommendation is not None and verification is not None and not has_valid_candidate:
                    loop_result = _failed_result(
                        model_id,
                        "no_verified_candidates",
                        "No recommendation candidate matched a valid entity in the Truth DB.",
                        loop_result,
                    )

                verification_duration_ms = int((time.perf_counter() - verification_started) * 1000)
                emit(
                    event_sink, "verification", "verification", "Fact verification completed",
                    status="completed" if has_valid_candidate else "failed",
                    agent=model_id, model=model_id, duration_ms=verification_duration_ms,
                    summary=("At least one candidate matched the Truth DB." if has_valid_candidate
                             else loop_result.error_message or "No valid structured recommendation to verify."),
                )

                run_row = session.get(AgentRun, run_id)
                completed = (
                    recommendation is not None
                    and verification is not None
                    and has_valid_candidate
                    and loop_result.status == "completed"
                )
                if run_row is not None:
                    run_row.status = AgentRunStatus.COMPLETED if completed else AgentRunStatus.FAILED
                    run_row.model_id = loop_result.model or model_id
                    run_row.error_code = loop_result.error_code
                    run_row.metrics = {
                        "rounds_used": loop_result.rounds_used,
                        "sql_calls_used": loop_result.sql_calls_used,
                        "agent_messages": len(loop_result.messages),
                        "tool_calls": len(loop_result.tool_calls),
                        "truth_verified": has_valid_candidate,
                        "fusion_request": True,
                        "protocol": loop_result.protocol,
                        "verification_duration_ms": verification_duration_ms,
                        "router_warning": router_warning,
                        "queue_duration_ms": queue_duration_ms,
                        "total_duration_ms": int((time.perf_counter() - agent_started_at) * 1000),
                        **loop_result.metrics,
                    }
                with phase("db_persist"):
                    session.commit()
                emit(
                    event_sink,
                    "agent_completed" if completed else "agent_failed",
                    "agent", "Agent 执行完成" if completed else "Agent 执行失败",
                    status="completed" if completed else "failed", agent=model_id, model=model_id,
                    duration_ms=int((time.perf_counter() - agent_started_at) * 1000),
                summary=loop_result.error_message if not completed else "推荐结果已通过结构校验与事实验证。",
                    detail={"round": loop_result.rounds_used, "tool_calls": len(loop_result.tool_calls),
                            "protocol": loop_result.protocol},
                )

                if completed:
                    fusion_inputs.append(AgentFusionInput(model_id, recommendation, verification, run_id))
                    agent_results.append(FusionAgentResult(
                        agent_run_id=run_id, model_id=model_id, response_model=loop_result.model,
                        status="completed", recommendation=recommendation, verification=verification,
                        raw_output=next((content for role, content in reversed(loop_result.messages) if role == "assistant"), None),
                    ))
                else:
                    agent_results.append(FusionAgentResult(
                        agent_run_id=run_id, model_id=model_id, response_model=loop_result.model,
                        status="failed", recommendation=recommendation, verification=verification,
                        error_code=loop_result.error_code, error=loop_result.error_message,
                        raw_output=next((content for role, content in reversed(loop_result.messages) if role == "assistant"), None),
                    ))

            if not fusion_inputs:
                return FusionRunResponse(
                    request_id=request_id,
                    conversation_id=conversation.id,
                    status="failed",
                    release_key=release_key,
                    agents=agent_results,
                    error=("No Agent produced a Truth-DB-valid candidate."
                           if any(item.error_code == "no_verified_candidates" for item in agent_results)
                           else "All configured Agents failed before fusion."),
                )

            if repository.latest_release_key() != release_key:
                raise FusionDataError(
                    "truth_release_changed", "Truth DB release changed during the fusion request."
                )

            order_index = {model_id: index for index, model_id in enumerate(configured_order)}
            fusion_inputs.sort(key=lambda item: order_index[item.model_id])
            failed_models = [
                model_id for model_id in configured_order
                if any(item.model_id == model_id and item.status == "failed" for item in agent_results)
            ]
            try:
                fusion_started = time.perf_counter()
                result = FusionEngine(repository).fuse(
                    fusion_inputs, constraints, top_k=10 if requested_component_roles(message) else top_k,
                    configured_agent_count=len(model_ids), failed_models=failed_models,
                )
            except FusionError as exc:
                return FusionRunResponse(
                    request_id=request_id, conversation_id=conversation.id, status="failed",
                    release_key=release_key, agents=agent_results, error=f"{exc.code}: {exc}",
                )

            result = select_role_coverage(result, message, top_k, session)
            fusion_duration_ms = int((time.perf_counter() - fusion_started) * 1000)
            emit(event_sink, "fusion_completed", "fusion", "可信融合完成", status="completed",
                 duration_ms=fusion_duration_ms, summary=f"融合 {len(fusion_inputs)} 个有效 Agent 结果。")
            for fusion_input in fusion_inputs:
                run_row = session.get(AgentRun, fusion_input.agent_run_id)
                if run_row is not None:
                    run_row.metrics = {**(run_row.metrics or {}), "fusion_duration_ms": fusion_duration_ms}

            conversation_context_service.append_message(
                session, conversation, "assistant", result.model_dump_json()
            )
            with phase("db_persist"):
                session.commit()
            with phase("build_assembly"):
                core_build = assemble_core_build(
                    candidates=result.top_k, message=message, session=session,
                )
            emit(event_sink, "narrator_started", "narrator", "正在整理推荐理由", status="running")
            with phase("narrator"):
                natural_language = await narrate_fusion(
                    message=message, result=result, settings=settings, core_build=core_build,
                )
            attach_sources(session, natural_language, result)
            emit(event_sink, "narrator_completed", "narrator", "推荐理由已整理", status="completed")
            return FusionRunResponse(
                request_id=request_id,
                conversation_id=conversation.id,
                status="completed",
                release_key=release_key,
                agents=agent_results,
                result=result,
                natural_language=natural_language,
                presentation=presentation_of(natural_language),
            )

    # ------------------------------------------------------------------ V3.3
    @staticmethod
    async def _agent_worker_parallel(
        *,
        model_id: str,
        profile: ModelProfile,
        request_id: UUID,
        conversation_id: UUID,
        base_messages: list[dict[str, str]],
        release_key: str,
        settings: Any,
        event_sink: EventSink | None,
        queue_duration_ms: int,
        message: str = "",
    ) -> AgentWorkerOutcome:
        """Run one isolated Agent on its own session and protocol handle.

        Messages, tool calls, SQL audit, metrics, evidence session and failures
        live entirely inside this worker; an exception here can never leak into
        another Agent's context or cancel the other two workers.
        """
        with SessionLocal() as session:
            repository = SqlAlchemyTruthRepository(session)
            agent_started_at = time.perf_counter()
            emit(event_sink, "model_loading", "model", "准备模型", status="running", model=model_id)
            emit(event_sink, "agent_started", "agent", "Agent 开始执行", status="running",
                 agent=model_id, model=model_id)
            run = AgentRun(
                request_id=request_id,
                conversation_id=conversation_id,
                status=AgentRunStatus.RUNNING,
                model_id=model_id,
            )
            session.add(run)
            session.flush()
            run_id = run.id
            with phase("db_persist"):
                session.commit()
            sequence = [0]

            def record_message(role: str, content: str, token_count: int) -> None:
                sequence[0] += 1
                session.add(AgentMessage(
                    agent_run_id=run_id, sequence_no=sequence[0], role=role,
                    content=content, token_count=token_count,
                ))
                with phase("db_persist"):
                    session.commit()

            def record_tool_call(event: ToolCallEvent) -> None:
                sequence[0] += 1
                session.add(ToolCall(
                    agent_run_id=run_id, sequence_no=sequence[0], tool=event.tool,
                    arguments=event.arguments, query_id=event.query_id, success=event.success,
                    error_code=event.error_code, result_truncated=event.result_truncated,
                    duration_ms=event.duration_ms, result_summary=event.result_summary,
                ))
                with phase("db_persist"):
                    session.commit()
                emit(
                    event_sink, "tool_completed", "tool", "数据库工具执行完成",
                    status="completed" if event.success else "failed", agent=model_id, model=model_id,
                    duration_ms=event.duration_ms, summary=event.result_summary,
                    detail={
                        "tool": event.tool, "purpose": "读取硬件与模型事实",
                        "row_count": _row_count(event.result_summary), "truncated": event.result_truncated,
                    },
                )

            def record_tool_start(tool: str, _arguments: dict) -> None:
                emit(
                    event_sink, "tool_started", "tool", "数据库工具开始执行", status="running",
                    agent=model_id, model=model_id,
                    detail={"tool": tool, "purpose": "读取硬件与模型事实"},
                )

            llm_call_index = [0]

            def record_llm_call(call) -> None:
                llm_call_index[0] += 1
                if llm_call_index[0] == 1:
                    emit(event_sink, "model_ready", "model", "模型已就绪", status="completed",
                         agent=model_id, model=model_id, duration_ms=call.duration_ms)
                emit(
                    event_sink, "llm_completed", "llm", "LLM 轮次完成", status="completed",
                    agent=model_id, model=model_id, duration_ms=call.duration_ms,
                    detail={
                        "round": llm_call_index[0], "prompt_tokens": call.usage.get("prompt_tokens"),
                        "completion_tokens": call.usage.get("completion_tokens"),
                        "total_tokens": call.usage.get("total_tokens"), "protocol": call.call_mode,
                    },
                )

            handle = ModelHandle(profile)
            try:
                emit(event_sink, "llm_started", "llm", "LLM 开始生成", status="running",
                     agent=model_id, model=model_id)
                async with asyncio.timeout(settings.agent_total_timeout_seconds):
                    loop_result = await run_agent_loop_protocol(
                        handle=handle,
                        profile=profile,
                        messages=list(base_messages),
                        settings=settings,
                        evidence_session=session,
                        on_message=record_message,
                        on_tool_call=record_tool_call,
                        on_tool_start=record_tool_start,
                        on_llm_call=record_llm_call,
                    )
            except TimeoutError:
                loop_result = _failed_result(model_id, "agent_timeout", "Agent run exceeded its total timeout.")
            except LLMServiceError as exc:
                loop_result = _failed_result(model_id, getattr(exc, "code", "llm_error"), str(exc))
            except Exception as exc:  # failure isolation
                loop_result = _failed_result(model_id, "agent_error", f"Agent failed unexpectedly: {exc}")

            recommendation: Recommendation | None = None
            verification: RecommendationVerification | None = None
            verification_started = time.perf_counter()
            emit(event_sink, "verification", "verification", "Verifying facts", status="running",
                 agent=model_id, model=model_id)
            if loop_result.recommendation is not None:
                try:
                    recommendation = Recommendation.model_validate(loop_result.recommendation)
                    verification = TruthVerifier(repository).verify(recommendation)
                    verification = check_requested_categories(verification, message, session)
                    if verification.release_key != release_key:
                        raise TruthVerificationError(
                            "truth_release_changed", "Truth DB release changed during the fusion request."
                        )
                except TruthVerificationError as exc:
                    loop_result = _failed_result(model_id, exc.code, str(exc), loop_result)
                    recommendation = None
                    verification = None
                except Exception:
                    loop_result = _failed_result(
                        model_id, "truth_verification_error", "Truth verification could not complete.", loop_result
                    )
                    recommendation = None
                    verification = None

            has_valid_candidate = bool(
                verification is not None
                and any(candidate.candidate_valid for candidate in verification.candidates)
            )
            if recommendation is not None and verification is not None and not has_valid_candidate:
                loop_result = _failed_result(
                    model_id,
                    "no_verified_candidates",
                    "No recommendation candidate matched a valid entity in the Truth DB.",
                    loop_result,
                )

            verification_duration_ms = int((time.perf_counter() - verification_started) * 1000)
            emit(
                event_sink, "verification", "verification", "Fact verification completed",
                status="completed" if has_valid_candidate else "failed",
                agent=model_id, model=model_id, duration_ms=verification_duration_ms,
                summary=("At least one candidate matched the Truth DB." if has_valid_candidate
                         else loop_result.error_message or "No valid structured recommendation to verify."),
            )

            completed = (
                recommendation is not None
                and verification is not None
                and has_valid_candidate
                and loop_result.status == "completed"
            )
            run_row = session.get(AgentRun, run_id)
            if run_row is not None:
                run_row.status = AgentRunStatus.COMPLETED if completed else AgentRunStatus.FAILED
                run_row.model_id = loop_result.model or model_id
                run_row.error_code = loop_result.error_code
                run_row.metrics = {
                    "rounds_used": loop_result.rounds_used,
                    "sql_calls_used": loop_result.sql_calls_used,
                    "agent_messages": len(loop_result.messages),
                    "tool_calls": len(loop_result.tool_calls),
                    "truth_verified": has_valid_candidate,
                    "fusion_request": True,
                    "parallel": True,
                    "protocol": loop_result.protocol,
                    "verification_duration_ms": verification_duration_ms,
                    "queue_duration_ms": queue_duration_ms,
                    "total_duration_ms": int((time.perf_counter() - agent_started_at) * 1000),
                    **loop_result.metrics,
                }
            with phase("db_persist"):
                session.commit()
            emit(
                event_sink,
                "agent_completed" if completed else "agent_failed",
                "agent", "Agent 执行完成" if completed else "Agent 执行失败",
                status="completed" if completed else "failed", agent=model_id, model=model_id,
                duration_ms=int((time.perf_counter() - agent_started_at) * 1000),
                summary=loop_result.error_message if not completed else "推荐结果已通过结构校验与事实验证。",
                detail={"round": loop_result.rounds_used, "tool_calls": len(loop_result.tool_calls),
                        "protocol": loop_result.protocol},
            )
            if completed and verification is not None and recommendation is not None:
                return AgentWorkerOutcome(
                    model_id=model_id,
                    run_id=run_id,
                    agent_result=FusionAgentResult(
                        agent_run_id=run_id, model_id=model_id, response_model=loop_result.model,
                        status="completed", recommendation=recommendation, verification=verification,
                        raw_output=next((content for role, content in reversed(loop_result.messages) if role == "assistant"), None),
                    ),
                    fusion_input=AgentFusionInput(model_id, recommendation, verification, run_id),
                )
            return AgentWorkerOutcome(
                model_id=model_id,
                run_id=run_id,
                agent_result=FusionAgentResult(
                    agent_run_id=run_id, model_id=model_id, response_model=loop_result.model,
                    status="failed", recommendation=recommendation, verification=verification,
                    error_code=loop_result.error_code, error=loop_result.error_message,
                    raw_output=next((content for role, content in reversed(loop_result.messages) if role == "assistant"), None),
                ),
                fusion_input=None,
            )

    @staticmethod
    async def _run_parallel(
        message: str,
        conversation_id: UUID | None,
        constraints: list[ConfirmedConstraint],
        top_k: int,
        request_id: UUID,
        model_ids: list[str],
        event_sink: EventSink | None = None,
        queue_duration_ms: int = 0,
        settings: Any = None,
    ) -> FusionRunResponse:
        """Concurrently execute two isolated Agents, then fuse deterministically.

        ``model_ids`` is the configured router order; every worker targets its
        own profile endpoint so a single Agent failure can never cancel the other
        worker (asyncio.gather(..., return_exceptions=True) + worker isolation).
        """
        settings = settings or get_settings()
        emit(event_sink, "fusion_started", "fusion",
             f"启动 {len(model_ids)} 个 Agent 的可信融合（并行）", status="running",
             detail={"agent_count": len(model_ids), "models": list(model_ids)})
        profiles = build_profiles(settings)
        with SessionLocal() as session:
            repository = SqlAlchemyTruthRepository(session)
            release_key = repository.latest_release_key()
            if release_key is None:
                raise FusionDataError("truth_release_unavailable", "No accepted Truth DB release is available.")
            conversation = conversation_context_service.get_or_create_conversation(session, conversation_id)
            conversation_context_service.append_message(session, conversation, "user", message)
            with phase("db_persist"):
                session.commit()
            with phase("context_prepare"):
                prepared = conversation_context_service.prepare_context(session, conversation.id, settings)
            system_prompt = build_protocol_system_prompt(inspect_database())
            roles = fact_selection_roles(prepared.messages)
            if roles and not settings.agent_terminal_tool_supported and not constraints:
                system_prompt = build_focused_system_prompt(inspect_database(), roles)
            base_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            for prepared_message in prepared.messages:
                if prepared_message["role"] == "system" and prepared_message["content"] == SYSTEM_PROMPT:
                    continue
                base_messages.append(prepared_message)
            roles = requested_component_roles(message)
            if roles:
                base_messages.append({"role": "system", "content": "Current requested component roles: " + ", ".join(sorted(roles)) + ". Historical recommendations do not override the current request. Query the catalogue for explicitly requested hardware or series first, then select compatible supporting parts around it. User-required parts take priority over default performance tiers or your preferred alternatives. If no verified compatible build satisfies them, explain the constraint conflict or evidence gap; never silently substitute parts or infer nonexistence from missing data."})
            if constraints:
                base_messages.append({"role": "system", "content": "Confirmed constraints: " + str([c.model_dump(mode="json") for c in constraints])})

            base_messages = planned_messages(base_messages, message)
            base_messages = consolidate_system_messages(base_messages)
            worker_profile: dict[str, ModelProfile] = {model_id: profile for model_id, profile in
                                                       zip(model_ids, profiles)}
            gathered = await asyncio.gather(
                *(
                    FusionService._agent_worker_parallel(
                        model_id=model_id,
                        profile=worker_profile[model_id],
                        request_id=request_id,
                        conversation_id=conversation.id,
                        base_messages=base_messages,
                        release_key=release_key,
                        settings=settings,
                        event_sink=event_sink,
                        queue_duration_ms=queue_duration_ms,
                        message=message,
                    )
                    for model_id in model_ids
                ),
                return_exceptions=True,
            )

            outcomes: list[AgentWorkerOutcome] = []
            for model_id, item in zip(model_ids, gathered):
                if isinstance(item, Exception):
                    outcome = AgentWorkerOutcome(
                        model_id=model_id,
                        run_id=None,
                        agent_result=FusionAgentResult(
                            agent_run_id=uuid4(), model_id=model_id, response_model=None,
                            status="failed", error_code="agent_error",
                            error=f"Parallel Agent failed outside its worker: {item}",
                        ),
                        fusion_input=None,
                    )
                else:
                    outcome = item
                outcomes.append(outcome)

            agent_results = [outcome.agent_result for outcome in outcomes]
            fusion_inputs = [outcome.fusion_input for outcome in outcomes if outcome.fusion_input is not None]

            if not fusion_inputs:
                return FusionRunResponse(
                    request_id=request_id,
                    conversation_id=conversation.id,
                    status="failed",
                    release_key=release_key,
                    agents=agent_results,
                    error=("No Agent produced a Truth-DB-valid candidate."
                           if any(item.agent_result.error_code == "no_verified_candidates" for item in outcomes)
                           else "All configured Agents failed before fusion."),
                )

            if repository.latest_release_key() != release_key:
                raise FusionDataError(
                    "truth_release_changed", "Truth DB release changed during the fusion request."
                )

            configured_order = list(model_ids)
            order_index = {model_id: index for index, model_id in enumerate(configured_order)}
            fusion_inputs.sort(key=lambda item: order_index[item.model_id])
            failed_models = [
                model_id for model_id in configured_order
                if any(item.agent_result.model_id == model_id and item.agent_result.status == "failed"
                       for item in outcomes)
            ]
            try:
                fusion_started = time.perf_counter()
                result = FusionEngine(repository).fuse(
                    fusion_inputs, constraints, top_k=10 if requested_component_roles(message) else top_k,
                    configured_agent_count=len(model_ids), failed_models=failed_models,
                )
            except FusionError as exc:
                return FusionRunResponse(
                    request_id=request_id, conversation_id=conversation.id, status="failed",
                    release_key=release_key, agents=agent_results, error=f"{exc.code}: {exc}",
                )

            result = select_role_coverage(result, message, top_k, session)
            fusion_duration_ms = int((time.perf_counter() - fusion_started) * 1000)
            emit(event_sink, "fusion_completed", "fusion", "可信融合完成", status="completed",
                 duration_ms=fusion_duration_ms, summary=f"融合 {len(fusion_inputs)} 个有效 Agent 结果。")
            for fusion_input in fusion_inputs:
                run_row = session.get(AgentRun, fusion_input.agent_run_id)
                if run_row is not None:
                    run_row.metrics = {**(run_row.metrics or {}), "fusion_duration_ms": fusion_duration_ms}
            conversation_context_service.append_message(
                session, conversation, "assistant", result.model_dump_json()
            )
            with phase("db_persist"):
                session.commit()
            with phase("build_assembly"):
                core_build = assemble_core_build(
                    candidates=result.top_k, message=message, session=session,
                )
            emit(event_sink, "narrator_started", "narrator", "正在整理推荐理由", status="running")
            with phase("narrator"):
                natural_language = await narrate_fusion(
                    message=message, result=result, settings=settings, core_build=core_build,
                    **({"compact": True} if fact_selection_roles(base_messages) and not settings.agent_terminal_tool_supported else {}),
                )
            attach_sources(session, natural_language, result)
            emit(event_sink, "narrator_completed", "narrator", "推荐理由已整理", status="completed")
            return FusionRunResponse(
                request_id=request_id,
                conversation_id=conversation.id,
                status="completed",
                release_key=release_key,
                agents=agent_results,
                result=result,
                natural_language=natural_language,
                presentation=presentation_of(natural_language),
            )

    @staticmethod
    def _mark_request_timed_out(request_id: UUID) -> None:
        """Close persisted RUNNING rows when the end-to-end budget cancels a run."""
        with SessionLocal() as session:
            runs = session.query(AgentRun).filter(AgentRun.request_id == request_id).all()
            for run in runs:
                if run.status == AgentRunStatus.RUNNING:
                    run.status = AgentRunStatus.FAILED
                    run.error_code = "fusion_timeout"
            with phase("db_persist"):
                session.commit()


def presentation_of(narration: NaturalLanguage) -> PresentationResult:
    """The conclusion view of a narration, for clients that only need the answer.

    ``NaturalLanguage`` is a superset of ``PresentationResult`` — it keeps
    ``overview`` and ``per_candidate`` because callers already depend on them — so
    this is a projection of one source of truth, not a second copy that could drift.
    """
    return PresentationResult(
        headline=narration.headline or narration.overview or "本次未生成自然语言结论。",
        primary=narration.primary,
        selections=narration.selections,
        selection_notice=narration.selection_notice,
        evidence=narration.evidence,
        sources=narration.sources,
        alternatives=narration.alternatives,
        caveats=narration.caveats,
        per_candidate=narration.per_candidate,
        # Carried through rather than recomputed: the narration already holds the
        # assembled build, so both client entry points project the same object.
        core_build=getattr(narration, "core_build", None),
    )


def _failed_result(
    model: str,
    code: str,
    message: str,
    previous: AgentLoopResult | None = None,
) -> AgentLoopResult:
    return AgentLoopResult(
        status="failed",
        recommendation=None,
        model=previous.model if previous is not None else model,
        rounds_used=previous.rounds_used if previous is not None else 0,
        sql_calls_used=previous.sql_calls_used if previous is not None else 0,
        error_code=code,
        error_message=message,
        messages=previous.messages if previous is not None else [],
        tool_calls=previous.tool_calls if previous is not None else [],
        protocol=previous.protocol if previous is not None else "legacy",
        metrics=previous.metrics if previous is not None else {},
    )


fusion_service = FusionService()

__all__ = [
    "FusionConfigurationError",
    "FusionDataError",
    "FusionTimeoutError",
    "FusionService",
    "fusion_service",
]
