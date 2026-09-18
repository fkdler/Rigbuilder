from dataclasses import dataclass
import time
from uuid import UUID

from app.context.service import conversation_context_service, consolidate_system_messages
from app.context.anchor import load_recommendation_anchor
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.inference.scheduler import inference_scheduler
from app.services.llm import LLMServiceError, generate_reply
from app.services.llm import complete_chat
from app.services.events import EventSink, emit


@dataclass(frozen=True)
class ChatTurnResult:
    conversation_id: UUID
    answer: str
    model: str
    context_compressed: bool


class ChatService:
    """Persist one shared conversation turn and run its full inference lifecycle serially."""

    @staticmethod
    async def reply(
        message: str, conversation_id: UUID | None, event_sink: EventSink | None = None,
        *, service_note: str | None = None, persist_user: bool = True,
    ) -> ChatTurnResult:
        # /api/chat has always been a direct LLM call. Keep optional context
        # compression instead of maintaining a second, subtly divergent path.
        queued_at = time.perf_counter()
        emit(event_sink, "model_loading", "model", "等待模型资源", status="running")

        def acquired() -> None:
            emit(event_sink, "model_ready", "model", "模型资源就绪", status="completed",
                 duration_ms=int((time.perf_counter() - queued_at) * 1000))

        return await inference_scheduler.run_serial(
            lambda: ChatService._run_turn(message, conversation_id, event_sink,
                service_note=service_note, persist_user=persist_user), on_acquired=acquired
        )

    @staticmethod
    async def _run_turn(
        message: str, conversation_id: UUID | None, event_sink: EventSink | None = None,
        *, service_note: str | None = None, persist_user: bool = True,
    ) -> ChatTurnResult:
        settings = get_settings()
        from app.inference.context_window import refresh_context_windows
        await refresh_context_windows(settings)
        with SessionLocal() as session:
            try:
                conversation = conversation_context_service.get_or_create_conversation(session, conversation_id)
                if persist_user:
                    conversation_context_service.append_message(session, conversation, "user", message)
                persisted_conversation_id = conversation.id
                session.commit()

                prepared = conversation_context_service.prepare_context(session, persisted_conversation_id, settings)
                context_compressed = False
                if prepared.compression_candidate is not None:
                    try:
                        compressed_text, _ = await generate_reply(
                            conversation_context_service.compression_prompt(prepared.compression_candidate)
                        )
                        summary = conversation_context_service.parse_summary(compressed_text)
                        if summary is not None:
                            conversation_context_service.save_snapshot(
                                session, persisted_conversation_id, prepared.compression_candidate, summary
                            )
                            session.commit()
                            prepared = conversation_context_service.prepare_context(session, persisted_conversation_id, settings)
                            context_compressed = True
                    except LLMServiceError:
                        # A failed optional compression must never discard the raw
                        # conversation or prevent the current user turn from running.
                        session.rollback()

                anchor = load_recommendation_anchor(session, persisted_conversation_id)
                messages = list(prepared.messages)
                if service_note:
                    messages.append({"role": "system", "content": service_note})
                if anchor is not None:
                    # Keep the immediate recommendation and user topic together; no stale prior topics.
                    messages = [m for m in messages if m["role"] == "system" and not m["content"].startswith("Conversation summary:")]
                    messages += [{"role": "system", "content": "The immediately preceding selected recommendation is "
                                 + anchor.context() + ". Answer the newest question about this selection unless the user changes topic. "
                                   "Do not substitute other products or revive older workloads. Historical facts were checked previously; no new DB verification occurs in FAST."},
                                 {"role": "user", "content": message}]
                messages.append({"role": "system", "content":
                    "For an actual hardware/model recommendation, use Chinese: 以下是为您推荐的硬件： (or 模型), "
                    "one selected item per line, then 推荐理由： as one causal paragraph, then 证据：. "
                    "Use only source URLs present in the supplied context; never invent links or claim unperformed verification. "
                    "Product existence, release status and current availability cannot be inferred from training memory. "
                    "Without current catalogue evidence, do not assert that a named product or series is unreleased or nonexistent. "
                    "Omit generic database-status badges and repetitive disclaimers. Keep concrete constraints and missing parts. "
                    "Do not recommend storage, coolers or cases, or mention their absence from the catalogue. "
                    "For ordinary questions and product introductions, answer directly without this recommendation template."})
                emit(event_sink, "llm_started", "llm", "生成快速回答", status="running")
                llm_result = await complete_chat(consolidate_system_messages(messages))
                answer, model = llm_result.content, llm_result.model
                emit(
                    event_sink, "llm_completed", "llm", "快速回答已生成", status="completed",
                    model=model, duration_ms=llm_result.duration_ms,
                    detail={
                        "prompt_tokens": llm_result.usage.get("prompt_tokens"),
                        "completion_tokens": llm_result.usage.get("completion_tokens"),
                        "total_tokens": llm_result.usage.get("total_tokens"), "protocol": "chat",
                    },
                )
                conversation = conversation_context_service.get_or_create_conversation(session, persisted_conversation_id)
                conversation_context_service.append_message(session, conversation, "assistant", answer)
                session.commit()
            except Exception:
                session.rollback()
                raise

        return ChatTurnResult(
            conversation_id=persisted_conversation_id,
            answer=answer,
            model=model,
            context_compressed=context_compressed,
        )
