from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field



class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    answer: str
    model: str
    context_compressed: bool


class ConversationMessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class ConversationResponse(BaseModel):
    id: UUID
    status: str
    messages: list[ConversationMessageResponse]
