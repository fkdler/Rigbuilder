"""Conversation list/detail/delete API (Plan V3.4, owner-scoped per Plan_V4.5 §11).

Server-truth sessions: the sidebar lists active conversations from PostgreSQL,
detail restores messages + jobs (including natural language and coverage), and
delete is a soft delete that preserves the audit chain.

Account boundary: every route takes the account from the server-resolved token and
reads only that account's rows. A conversation belonging to somebody else is
answered with 404, not 403 -- a 403 would confirm the UUID exists, and §11.4.1
rejects "UUIDs are hard to guess" as a reason to leak that. Conversations created
before accounts existed have a NULL owner and are therefore visible to nobody.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Identity, current_identity, owned_conversation
from app.context.service import ConversationNotFoundError, conversation_context_service
from app.db.session import get_db_session
from app.models import QueryJob, User
from app.schemas.conversations import (
    ConversationDetail,
    ConversationJobInfo,
    ConversationMessageInfo,
    ConversationSummary,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

_NON_TERMINAL_JOB_STATUSES = {"queued", "running", "cancel_requested"}


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
def create_conversation(
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> ConversationDetail:
    conversation = conversation_context_service.get_or_create_conversation(session, None)
    # Owner is written only here, from the authenticated identity. No request body
    # can name an owner, so no client can create a conversation for someone else.
    conversation.owner_id = identity.user.id
    session.commit()
    session.refresh(conversation)
    return ConversationDetail(id=conversation.id, status=conversation.status)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> list[ConversationSummary]:
    rows = conversation_context_service.list_conversations(
        session, owner_id=identity.user.id, limit=limit,
    )
    return [
        ConversationSummary(
            id=row["id"],
            status=row["status"],
            title=row["title"],
            last_message_summary=row["last_message_summary"],
            updated_at=row["updated_at"],
            running_job=row["running_job"],
            latest_job_status=row["latest_job_status"],
            active_job_id=row["active_job_id"],
        )
        for row in rows
    ]


def _load_owned(session: Session, user: User, conversation_id: UUID):
    try:
        return owned_conversation(session, user, conversation_id)
    except HTTPException as exc:
        # Keep the V3.4 contract for this surface: a missing conversation is a 404
        # with a plain string detail, which the existing frontend already handles.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found."
        ) from exc


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation_detail(
    conversation_id: UUID,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> ConversationDetail:
    conversation = _load_owned(session, identity.user, conversation_id)
    messages = conversation_context_service.get_conversation_messages(session, conversation_id)
    jobs = conversation_context_service.get_conversation_jobs(session, conversation_id)
    return ConversationDetail(
        id=conversation.id,
        status=conversation.status,
        messages=[
            ConversationMessageInfo(
                sequence_no=m.sequence_no,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in messages
        ],
        jobs=[_job_info(job) for job in jobs],
    )


@router.delete("/{conversation_id}", response_model=ConversationDetail)
def delete_conversation(
    conversation_id: UUID,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> ConversationDetail:
    conversation = _load_owned(session, identity.user, conversation_id)
    active_job = session.scalar(
        select(QueryJob.id).where(
            QueryJob.conversation_id == conversation_id,
            QueryJob.status.in_(_NON_TERMINAL_JOB_STATUSES),
        ).limit(1)
    )
    if active_job is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "conversation_has_active_job",
                "message": "Cancel the active job before deleting this conversation.",
                "job_id": str(active_job),
            },
        )
    try:
        conversation_context_service.soft_delete_conversation(session, conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found."
        ) from exc
    session.commit()
    return ConversationDetail(
        id=conversation.id,
        status="deleted",
        messages=[],
        jobs=[],
    )


def _job_info(job) -> ConversationJobInfo:
    payload = job.result_payload or {}
    nl = None
    coverage = None
    trace_summary = None
    agent_summary = []
    if payload.get("kind") == "fusion":
        nl = payload.get("natural_language")
        result = payload.get("result")
        if isinstance(result, dict):
            trace = result.get("trace")
            if isinstance(trace, dict):
                coverage = trace.get("agent_coverage")
                trace_summary = trace
        raw_agents = payload.get("agents")
        if isinstance(raw_agents, list):
            agent_summary = [
                {
                    "agent_run_id": item.get("agent_run_id"),
                    "model_id": item.get("model_id"),
                    "status": item.get("status"),
                    "error_code": item.get("error_code"),
                    "error": item.get("error"),
                }
                for item in raw_agents if isinstance(item, dict)
            ]
    request_payload = job.request_payload or {}
    return ConversationJobInfo(
        job_id=job.id,
        status=job.status,
        request_message=str(request_payload.get("message") or ""),
        resolved_mode=job.resolved_mode,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_code=job.error_code,
        error=job.error_message,
        result=payload or None,
        result_natural_language=nl,
        agent_coverage=coverage,
        agent_summary=agent_summary,
        trace_summary=trace_summary,
    )
