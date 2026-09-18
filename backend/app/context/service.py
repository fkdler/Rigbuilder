"""PostgreSQL-backed conversation storage and bounded prompt assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.context.contracts import ChatMessage
from app.core.config import Settings
from app.models import Conversation, ConversationContextSnapshot, ConversationMessage, UserConstraint
from app.models.query_job import QueryJob
from app.inference.token_estimation import estimate_tokens
from app.inference.context_window import effective_context_size


SYSTEM_PROMPT = """You are RigBuilder's fast-answer assistant.

Scope: general explanation and conversation about PC hardware and local AI
deployment. Be concise, state assumptions, and never invent benchmark data. Use
the provided conversation context as prior user information, but treat the newest
user message as the request to answer.

Hard limits - this path has NO database and NO web access:
- You may give a clearly labelled general-knowledge suggestion, including a
  specific product or model name, when the user asks for advice without DB lookup.
  Prefix it with "基于通用知识，未查询 Truth DB" and do not present it as verified.
- Never quote a price, a release status, an availability or a current
  specification for a named product.
- For current prices, stock, benchmarks, or exact specifications, state that they
  were not checked and avoid precise unverified numbers.
- Do not use Markdown tables or Markdown headings: this interface renders plain
  text, so they reach the user as raw punctuation. Short paragraphs and simple
  "-" lines only.

When the newest message asks what you can do, answer that capability question
only; do not continue or speculate about an earlier hardware request."""

SUMMARY_KEYS = (
    "user_goal",
    "hard_constraints",
    "soft_preferences",
    "existing_hardware",
    "confirmed_facts",
    "rejected_options",
    "pending_questions",
    "agent_progress",
)


class ConversationNotFoundError(ValueError):
    """The supplied conversation ID does not exist."""


@dataclass(frozen=True)
class CompressionCandidate:
    snapshot: ConversationContextSnapshot | None
    messages: list[ConversationMessage]


@dataclass(frozen=True)
class PreparedContext:
    messages: list[ChatMessage]
    compression_candidate: CompressionCandidate | None


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _cap_message_content(content: str, max_chars: int, conversation_id: UUID) -> str:
    """Shorten an oversized historical message for prompt use, and say so in the text.

    A conversation turn is stored in full so the UI can show it, but replaying it verbatim into
    the next Agent prompt is what made multi-turn unusable: measured, one stored fusion result
    was ~14 966 characters (~3 700 tokens) and the Agent can only hold 14 336 tokens in total.
    The shortened form keeps the head of the message and states plainly that it was shortened,
    so the model does not mistake a partial record for the whole one. Messages that already fit
    are returned unchanged, so a short conversation is composed exactly as before.
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    return (
        content[:max_chars]
        + f"\n[... this earlier message was shortened for context; "
          f"{len(content)} characters total, first {max_chars} kept. Do not treat this as "
          f"the complete text, and re-query if you need a fact that is not shown.]"
    )


def _message_as_chat(message: ConversationMessage, max_chars: int | None = None) -> ChatMessage:
    """Render a stored message for a prompt, optionally in shortened form.

    The stored row is never modified: shortening applies only to the copy handed to a model,
    so the conversation the UI reads back keeps the full text.
    """
    content = _history_content(message)
    if max_chars is None:
        return {"role": message.role, "content": content}
    return {
        "role": message.role,
        "content": _cap_message_content(content, max_chars, message.conversation_id),
    }


def consolidate_system_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Qwen3.5 accepts system instructions only at the beginning of the turn list."""
    systems = [m["content"] for m in messages if m["role"] == "system"]
    return ([{"role": "system", "content": "\n\n".join(systems)}] if systems else []) + [
        m for m in messages if m["role"] != "system"
    ]


def _history_content(message: ConversationMessage) -> str:
    """Project old Fusion JSON into factual context without changing stored history."""
    if message.role != "assistant":
        return message.content
    try:
        result = json.loads(message.content)
    except (ValueError, TypeError):
        return message.content
    if not isinstance(result, dict) or not isinstance(result.get("top_k"), list) or "release_key" not in result:
        return message.content
    candidates = []
    for item in result["top_k"][:6]:
        if not isinstance(item, dict):
            continue
        facts = []
        claims = item.get("claims")
        for claim in claims if isinstance(claims, list) else []:
            if not isinstance(claim, dict):
                continue
            proof = claim.get("verification") or {}
            if not isinstance(proof, dict):
                continue
            if proof.get("status") == "supported":
                source = claim.get("claim") or {}
                if not isinstance(source, dict):
                    continue
                facts.append({"field": source.get("field_key") or source.get("metric_key"),
                              "value": proof.get("canonical_value"), "unit": proof.get("canonical_unit")})
        candidates.append({"candidate_id": item.get("candidate_id"), "name": item.get("canonical_name"),
                           "facts": facts[:6]})
    return "Previous verified candidates (history only; answer the newest request): " + _serialize({
        "release_key": result["release_key"], "candidates": candidates,
    })


class ConversationContextService:
    """Keeps raw history immutable and treats snapshots as replaceable derivatives."""

    def get_or_create_conversation(self, session: Session, conversation_id: UUID | None) -> Conversation:
        if conversation_id is None:
            conversation = Conversation()
            session.add(conversation)
            session.flush()
            return conversation
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or conversation.status == "deleted":
            raise ConversationNotFoundError(f"Conversation {conversation_id} was not found")
        return conversation

    def append_message(self, session: Session, conversation: Conversation, role: str, content: str) -> ConversationMessage:
        last_sequence = session.scalar(
            select(func.max(ConversationMessage.sequence_no)).where(ConversationMessage.conversation_id == conversation.id)
        )
        message = ConversationMessage(
            conversation_id=conversation.id,
            sequence_no=(last_sequence or 0) + 1,
            role=role,
            content=content,
            token_count=estimate_tokens(content),
        )
        session.add(message)
        conversation.updated_at = func.now()
        session.flush()
        return message

    def get_conversation_messages(self, session: Session, conversation_id: UUID) -> list[ConversationMessage]:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} was not found")
        return list(
            session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.sequence_no)
            )
        )

    def list_conversations(
        self, session: Session, owner_id: UUID | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Active conversations ordered by updated_at desc, with title + running job.

        ``owner_id`` is the account boundary (Plan_V4.5 §11.2): the sidebar must
        only ever show this account's conversations, and a conversation whose owner
        is NULL is shown to nobody. ``None`` therefore means "no ownership filter"
        and exists only for the internal/test path -- every API caller passes the
        authenticated account id, because passing ``None`` there would list other
        accounts' conversations.
        """
        statement = select(Conversation).where(Conversation.status != "deleted")
        if owner_id is not None:
            statement = statement.where(Conversation.owner_id == owner_id)
        conversations = session.scalars(
            statement.order_by(Conversation.updated_at.desc()).limit(limit)
        ).all()
        result: list[dict[str, Any]] = []
        for conversation in conversations:
            first_user = session.scalar(
                select(ConversationMessage.content)
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.role == "user",
                )
                .order_by(ConversationMessage.sequence_no)
                .limit(1)
            )
            last_message = session.scalar(
                select(ConversationMessage.content)
                .where(ConversationMessage.conversation_id == conversation.id)
                .order_by(ConversationMessage.sequence_no.desc())
                .limit(1)
            )
            latest_job = session.scalar(
                select(QueryJob)
                .where(QueryJob.conversation_id == conversation.id)
                .order_by(QueryJob.created_at.desc())
                .limit(1)
            )
            active_job_id = session.scalar(
                select(QueryJob.id)
                .where(
                    QueryJob.conversation_id == conversation.id,
                    QueryJob.status.in_(["queued", "running", "cancel_requested"]),
                )
                .order_by(QueryJob.created_at.desc())
                .limit(1)
            )
            result.append({
                "id": conversation.id,
                "status": conversation.status,
                "title": (first_user or "New conversation")[:120],
                "last_message_summary": (last_message or first_user or "New conversation")[:160],
                "updated_at": conversation.updated_at,
                "running_job": active_job_id is not None,
                "latest_job_status": latest_job.status if latest_job is not None else None,
                "active_job_id": active_job_id,
            })
        return result

    def soft_delete_conversation(self, session: Session, conversation_id: UUID) -> bool:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} was not found")
        if conversation.status == "deleted":
            return False
        conversation.status = "deleted"
        session.flush()
        return True

    def get_conversation_jobs(self, session: Session, conversation_id: UUID) -> list[QueryJob]:
        return list(
            session.scalars(
                select(QueryJob)
                .where(QueryJob.conversation_id == conversation_id)
                .order_by(QueryJob.created_at.desc())
            )
        )

    def _latest_snapshot(self, session: Session, conversation_id: UUID) -> ConversationContextSnapshot | None:
        return session.scalar(
            select(ConversationContextSnapshot)
            .where(ConversationContextSnapshot.conversation_id == conversation_id)
            .order_by(ConversationContextSnapshot.version.desc())
            .limit(1)
        )

    def _constraints_message(self, session: Session, conversation_id: UUID) -> ChatMessage | None:
        constraints = list(
            session.scalars(
                select(UserConstraint)
                .where(UserConstraint.conversation_id == conversation_id, UserConstraint.status == "confirmed")
                .order_by(UserConstraint.constraint_key)
            )
        )
        if not constraints:
            return None
        payload = {constraint.constraint_key: constraint.value for constraint in constraints}
        return {"role": "system", "content": f"Confirmed user constraints:\n{_serialize(payload)}"}

    @staticmethod
    def _history_token_budget(settings: Settings) -> int:
        """How much conversation history may be carried into an Agent prompt.

        Bounded by the smallest window in play, not only by ``llm_context_window_tokens``.

        Measured: the compression ratio applied to that key alone (32 768 x 0.70 = 22 937) is far
        above what an Agent can hold. A resident llama-server runs ``--ctx-size 16384`` and the
        Agent reserves ``prompt_reserved_completion_tokens`` (2 048), leaving 14 336, so a
        history allowance of 22 937 could never be reached by the check that rejects the request
        - it was a limit larger than the wall. The second question of a conversation reached a
        9 181-token history and every Agent failed as ``context_budget_exceeded`` in about three
        seconds before doing any work, while that turn's own tool results were only ~2 000
        tokens; the SystemAssembler prompt is a further ~4 665. Taking the minimum keeps the old
        behaviour whenever ``llm_context_window_tokens`` is the smaller value, and otherwise
        stops the budget from exceeding the window that will actually reject the request.
        """
        limits: list[int] = []
        if settings.llm_context_window_tokens is not None:
            limits.append(int(settings.llm_context_window_tokens))
        limits.append(effective_context_size(settings))
        window = min(limits)
        agent_capacity = max(
            1, window - int(settings.prompt_reserved_completion_tokens),
        )
        return max(1, int(agent_capacity * settings.context_compression_ratio))

    def _compose_messages(
        self,
        session: Session,
        conversation_id: UUID,
        snapshot: ConversationContextSnapshot | None,
        raw_messages: list[ConversationMessage],
        settings: Settings,
    ) -> list[ChatMessage]:
        system_messages: list[ChatMessage] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if snapshot is not None:
            system_messages.append({"role": "system", "content": f"Conversation summary:\n{_serialize(snapshot.summary)}"})
        constraint_message = self._constraints_message(session, conversation_id)
        if constraint_message is not None:
            system_messages.append(constraint_message)

        recent_limit = max(2, settings.context_recent_turns * 2)
        selected = raw_messages[-recent_limit:]
        max_message_chars: int | None = None
        if settings.llm_context_window_tokens is not None:
            # Hard ceiling on the history allowance. _history_token_budget already takes the
            # smallest window in play, but the Agent prompt also has to hold the system prompt
            # (~4 665 real tokens) and this turn's own tool results, so history cannot claim the
            # whole remainder or the third question of a conversation fails the preflight in
            # seconds without doing any work. Measured: with the full remainder as history,
            # turn three of a conversation ended as context_budget_exceeded for all Agents;
            # at this fraction, four consecutive turns completed.
            agent_capacity = max(
                1,
                effective_context_size(settings) - int(settings.prompt_reserved_completion_tokens),
            )
            budget = min(self._history_token_budget(settings), int(agent_capacity * 0.35))
            # Bound each historical turn before measuring it with the shared estimator.
            max_message_chars = max(200, budget // 4 * 3)
            fixed_cost = sum(estimate_tokens(message["content"]) for message in system_messages)
            fitted: list[ConversationMessage] = []
            used = fixed_cost
            for raw_message in reversed(selected):
                cost = estimate_tokens(
                    _cap_message_content(_history_content(raw_message), max_message_chars, conversation_id)
                )
                if fitted and used + cost > budget:
                    break
                fitted.append(raw_message)
                used += cost
            selected = list(reversed(fitted))
        return [
            *system_messages,
            *(_message_as_chat(message, max_message_chars) for message in selected),
        ]

    def prepare_context(self, session: Session, conversation_id: UUID, settings: Settings) -> PreparedContext:
        snapshot = self._latest_snapshot(session, conversation_id)
        covered_through = snapshot.covered_through_sequence if snapshot is not None else 0
        raw_messages = list(
            session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id, ConversationMessage.sequence_no > covered_through)
                .order_by(ConversationMessage.sequence_no)
            )
        )
        candidate: CompressionCandidate | None = None
        if settings.llm_context_window_tokens is not None:
            base_messages = self._compose_messages(session, conversation_id, snapshot, raw_messages, settings)
            estimated = sum(estimate_tokens(message["content"]) for message in base_messages)
            threshold = self._history_token_budget(settings)
            recent_limit = max(2, settings.context_recent_turns * 2)
            compactable = raw_messages[:-recent_limit]
            if estimated > threshold and compactable:
                candidate = CompressionCandidate(snapshot=snapshot, messages=compactable)
        return PreparedContext(messages=self._compose_messages(session, conversation_id, snapshot, raw_messages, settings), compression_candidate=candidate)

    def compression_prompt(self, candidate: CompressionCandidate) -> list[ChatMessage]:
        previous_summary = candidate.snapshot.summary if candidate.snapshot is not None else None
        transcript = [{"role": message.role, "content": message.content} for message in candidate.messages]
        instructions = {
            "task": "Compress prior conversation context without changing confirmed facts.",
            "required_json_keys": list(SUMMARY_KEYS),
            "previous_summary": previous_summary,
            "messages_to_merge": transcript,
            "rules": [
                "Return exactly one JSON object and no Markdown.",
                "Use arrays for list fields and strings for user_goal and agent_progress.",
                "Do not invent facts or constraints.",
            ],
        }
        return [
            {"role": "system", "content": "You produce loss-aware structured conversation summaries."},
            {"role": "user", "content": _serialize(instructions)},
        ]

    def parse_summary(self, raw: str) -> dict[str, Any] | None:
        candidate = raw.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`").removeprefix("json").strip()
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict) or set(value) != set(SUMMARY_KEYS):
            return None
        if not isinstance(value["user_goal"], str) or not isinstance(value["agent_progress"], str):
            return None
        if any(not isinstance(value[key], list) for key in SUMMARY_KEYS if key not in {"user_goal", "agent_progress"}):
            return None
        return value

    def save_snapshot(self, session: Session, conversation_id: UUID, candidate: CompressionCandidate, summary: dict[str, Any]) -> ConversationContextSnapshot:
        last_message = candidate.messages[-1]
        previous_version = session.scalar(
            select(func.max(ConversationContextSnapshot.version)).where(ConversationContextSnapshot.conversation_id == conversation_id)
        )
        snapshot = ConversationContextSnapshot(
            conversation_id=conversation_id,
            version=(previous_version or 0) + 1,
            covered_through_message_id=last_message.id,
            covered_through_sequence=last_message.sequence_no,
            summary=summary,
            token_count=estimate_tokens(_serialize(summary)),
        )
        session.add(snapshot)
        session.flush()
        return snapshot


conversation_context_service = ConversationContextService()
