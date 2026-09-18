from __future__ import annotations

import asyncio
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import Identity, current_identity, owned_conversation, owned_job
from app.db.session import SessionLocal, get_db_session
from app.models import QueryJob
from app.schemas.query import QueryJobRequest, QueryJobResponse, QueryTraceResponse
from app.services.auth import AuthError, auth_service
from app.services.jobs import (
    QueryJobNotFoundError, TERMINAL_STATUSES, query_job_manager, serialize_sse,
)

router = APIRouter(prefix="/api/query", tags=["query jobs"])

# How often a live SSE stream re-checks that the caller is still that same,
# still-valid account. Plan_V4.5 §11.3 U2 requires an expired or revoked token to
# stop *new* reads and stream continuation; checking on every 0.5 s poll would put
# a database round-trip in the hot loop for no extra safety, so it rides the
# existing heartbeat cadence instead.
_REAUTH_INTERVAL_SECONDS = 15


def _still_authorized(token: str, job_id: UUID) -> bool:
    with SessionLocal() as session:
        try:
            user = auth_service.resolve_token(session, token)
        except AuthError:
            return False
        job = session.get(QueryJob, job_id)
        return job is not None and job.owner_id == user.id


@router.post("/jobs", response_model=QueryJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: QueryJobRequest,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> QueryJobResponse:
    if payload.conversation_id is not None:
        # Checked here, before anything is queued: without this a caller could name
        # somebody else's conversation and the fusion run would write its messages
        # into that conversation.
        owned_conversation(session, identity.user, payload.conversation_id)
    return query_job_manager.submit(payload, identity.user.id)


@router.get("/jobs/{job_id}", response_model=QueryJobResponse)
def get_job(
    job_id: UUID,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> QueryJobResponse:
    owned_job(session, identity.user, job_id)
    try:
        return query_job_manager.get(job_id)
    except QueryJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Query job was not found.") from exc


@router.post("/jobs/{job_id}/cancel", response_model=QueryJobResponse)
def cancel_job(
    job_id: UUID,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> QueryJobResponse:
    owned_job(session, identity.user, job_id)
    try:
        return query_job_manager.cancel(job_id)
    except QueryJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Query job was not found.") from exc


@router.get("/jobs/{job_id}/trace", response_model=QueryTraceResponse)
def get_job_trace(
    job_id: UUID,
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> QueryTraceResponse:
    owned_job(session, identity.user, job_id)
    try:
        return query_job_manager.trace(job_id)
    except QueryJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Query job was not found.") from exc


@router.get("/jobs/{job_id}/events")
async def stream_events(
    job_id: UUID,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    identity: Identity = Depends(current_identity),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """Server-sent progress for one job.

    Authorised twice: once here through the normal identity dependency, then
    periodically inside the stream so a token revoked (or an account disabled)
    mid-flight stops the stream instead of delivering the rest of the run. The
    token is read from ``?token=`` because ``EventSource`` cannot set headers.
    """
    cursor = after
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="Last-Event-ID must be an integer.")
    owned_job(session, identity.user, job_id)

    async def event_stream():
        nonlocal cursor
        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            events = query_job_manager.events_after(job_id, cursor)
            for event in events:
                cursor = event.sequence
                yield serialize_sse(event)
            job = query_job_manager.get(job_id)
            if job.status in TERMINAL_STATUSES and not query_job_manager.events_after(job_id, cursor):
                break
            if time.monotonic() - last_heartbeat >= _REAUTH_INTERVAL_SECONDS:
                last_heartbeat = time.monotonic()
                if not _still_authorized(identity.token, job_id):
                    # The client sees the stream close and re-authenticates rather
                    # than continuing to receive another account's progress.
                    break
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
