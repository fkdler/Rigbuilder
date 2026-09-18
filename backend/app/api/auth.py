"""Account endpoints: register, login, logout, profile and password (Plan_V4.5 §11).

Everything here is a thin translation layer over ``app.services.auth``. Two
details are deliberate:

* the actor always comes from ``current_identity`` (the server-resolved token),
  never from the request body, so no payload can name a different account;
* the token is returned **only** by register/login. Profile reads return the
  account, never the credential.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import Identity, current_identity
from app.db.session import get_db_session
from app.models import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    UpdateUsernameRequest,
    UserResponse,
)
from app.services.auth import AuthError, auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


def _raise(exc: AuthError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
    )


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _session_response(issued) -> SessionResponse:
    return SessionResponse(
        token=issued.token,
        expires_at=issued.expires_at,
        user=_user_response(issued.user),
    )


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request,
             session: Session = Depends(get_db_session)) -> SessionResponse:
    """Create an ordinary account and log it in. Administrators are provisioned offline."""
    try:
        issued = auth_service.register(
            session, payload.username, payload.password,
            client=_client(request), user_agent=request.headers.get("user-agent"),
        )
    except AuthError as exc:
        _raise(exc)
    return _session_response(issued)


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest, request: Request,
          session: Session = Depends(get_db_session)) -> SessionResponse:
    try:
        issued = auth_service.login(
            session, payload.username, payload.password,
            client=_client(request), user_agent=request.headers.get("user-agent"),
        )
    except AuthError as exc:
        _raise(exc)
    return _session_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(identity: Identity = Depends(current_identity),
           session: Session = Depends(get_db_session)) -> Response:
    """Revoke the calling session only; other devices stay logged in."""
    auth_service.logout(session, identity.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def read_me(identity: Identity = Depends(current_identity)) -> UserResponse:
    return _user_response(identity.user)


@router.patch("/me", response_model=UserResponse)
def update_username(payload: UpdateUsernameRequest,
                    identity: Identity = Depends(current_identity),
                    session: Session = Depends(get_db_session)) -> UserResponse:
    """Rename the authenticated account without changing its password."""
    current = session.get(User, identity.user.id)
    try:
        updated = auth_service.rename(session, current, payload.username)
    except AuthError as exc:
        _raise(exc)
    return _user_response(updated)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePasswordRequest,
                    identity: Identity = Depends(current_identity),
                    session: Session = Depends(get_db_session)) -> Response:
    """Change the password and cut off every *other* device.

    The calling session survives, so the user is not logged out of the browser
    they just used; every other token is revoked immediately rather than being
    left to expire (Plan_V4.5 §11.2).
    """
    current = session.get(User, identity.user.id)
    try:
        auth_service.change_password(
            session, current, payload.current_password, payload.new_password,
            keep_token=identity.token,
        )
    except AuthError as exc:
        _raise(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
