"""Identity and resource-authorization dependencies shared by every router.

Plan_V4.5 §11.3 U2 requires resource-level authorization rather than "is this
request logged in" checks sprinkled over the routes. Two pieces implement that:

``current_identity``
    Resolves the bearer token to a ``User`` and hands the caller both the account
    and the raw token (the token is needed to revoke *this* session while sparing
    it during a password change).

``require_owner_*``
    Small helpers the routers call with the row they already loaded. They all fail
    with **404, not 403**, when a resource belongs to somebody else: a 403 would
    confirm that the UUID exists, and §11.4.1 explicitly says "UUID is hard to
    guess" is not an accepted reason to leak that.

Where the token is read from, and why all three places:

* ``Authorization: Bearer`` -- the normal case;
* ``?token=`` -- ``EventSource`` cannot set request headers, so the SSE stream has
  no other way to authenticate. It is read from ``request.query_params`` instead
  of being declared on every route, so the OpenAPI document does not advertise
  putting a token in a URL for endpoints that never need it;
* a cookie -- so an operator can hit the API from a browser tab without a client.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.models import Conversation, QueryJob, User
from app.services.auth import AuthError, auth_service

TOKEN_COOKIE = "rigbuilder_session"


@dataclass(frozen=True)
class Identity:
    user: User
    token: str


def _read_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    query_token = request.query_params.get("token")
    if query_token:
        return query_token
    return request.cookies.get(TOKEN_COOKIE) or ""


def current_identity(
    request: Request,
    session: Session = Depends(get_db_session),
) -> Identity:
    token = _read_token(request)
    try:
        user = auth_service.resolve_token(session, token)
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    # Persists the best-effort ``last_used_at`` touch; a no-op otherwise.
    session.commit()
    return Identity(user=user, token=token)


def current_user(identity: Identity = Depends(current_identity)) -> User:
    return identity.user


def require_admin(user: User = Depends(current_user)) -> User:
    """The operator console is not a user-facing surface (Plan_V4.5 §11.1)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"code": "admin_required", "message": "该功能仅对管理员开放。"},
        )
    return user


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": f"{what}_not_found", "message": "未找到该资源，或它不属于当前账号。"},
    )


def owned_conversation(session: Session, user: User, conversation_id) -> Conversation:
    """Load a conversation only when the current account owns it.

    ``owner_id IS NULL`` means "created before accounts existed" and is treated as
    owned by nobody -- §11.2 forbids silently assigning shared history to an
    account, and the three candidate policies (claim, admin assignment, permanent
    non-public) are still open. Owned-by-nobody therefore reads as not-found.
    """
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.status == "deleted":
        raise _not_found("conversation")
    if conversation.owner_id != user.id:
        raise _not_found("conversation")
    return conversation


def owned_job(session: Session, user: User, job_id) -> QueryJob:
    job = session.get(QueryJob, job_id)
    if job is None or job.owner_id != user.id:
        raise _not_found("query_job")
    return job


__all__ = [
    "TOKEN_COOKIE",
    "Identity",
    "current_identity",
    "current_user",
    "owned_conversation",
    "owned_job",
    "require_admin",
]
