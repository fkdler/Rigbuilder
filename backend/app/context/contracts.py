from typing import Protocol, TypedDict
from uuid import UUID


class ChatMessage(TypedDict):
    role: str
    content: str


class ConversationContextStore(Protocol):
    """Storage boundary for V1.1 conversation context."""

    async def build_messages(self, conversation_id: UUID) -> list[ChatMessage]:
        """Return summary, preserved facts, and recent raw turns."""
        ...

    # TODO(V1.1-IMPLEMENT): add PostgreSQL-backed message/snapshot storage and
    # a JSON-schema-validated compressor before enabling multi-turn context.
