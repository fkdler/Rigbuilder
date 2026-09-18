from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import Identity, current_identity, owned_conversation
from app.context.service import ConversationNotFoundError
from app.db.session import get_db_session
from app.schemas import (
    ChatRequest,
    ChatResponse,
)
from app.services.chat import ChatService
from app.services.llm import LLMConfigurationError, LLMServiceError
from app.services.token_ledger import meter_site_usage

router = APIRouter(prefix="/api", tags=["llm"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> ChatResponse:
    """Single-model fast answer.

    Kept behind the same account boundary as the job API: without this an
    unauthenticated caller could append messages to any conversation whose UUID it
    could name, and the conversation history is what the next Agent prompt is built
    from.
    """
    if payload.conversation_id is not None:
        owned_conversation(session, identity.user, payload.conversation_id)
    meter_context = meter_site_usage.set(True)
    try:
        result = await ChatService.reply(payload.message, payload.conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found.") from exc
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM is not configured. Set a valid LLM_BASE_URL in .env.",
        ) from exc
    except LLMServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The local inference server could not complete the request.",
        ) from exc
    finally:
        meter_site_usage.reset(meter_context)
    return ChatResponse(
        conversation_id=result.conversation_id,
        answer=result.answer,
        model=result.model,
        context_compressed=result.context_compressed,
    )
