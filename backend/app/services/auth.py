"""Account, session and credential lifecycle (Plan_V4.5 §11.2 / §11.3 U2).

This module owns every rule about identity, and the API layer only translates its
outcomes into HTTP. Two properties are deliberate:

* **The actor is never taken from the request body.** Every call here receives a
  token or a session row that the server looked up. There is no ``user_id``
  parameter anywhere in the public surface, so no route can be tricked into
  acting as somebody else by supplying an ID.
* **Failures do not distinguish "no such account" from "wrong password".** Both
  return ``invalid_credentials``, and the missing-account branch still pays for a
  password verification, so response bodies and response time do not turn the
  login endpoint into an account-name oracle.

Login attempts are throttled in process. Plan_V4.5 §11.3 allows this ("single
worker, application-level, observable") because the deployment runs one uvicorn
worker and the point is to make online guessing expensive, not to be a
distributed rate limiter. It is deliberately two-keyed -- account *and* client --
because either key alone is trivially bypassed by varying the other one.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import (
    hash_password,
    needs_rehash,
    new_session_token,
    token_digest,
    verify_password,
)
from app.models import AuthSession, User

logger = logging.getLogger("rigbuilder.auth")

# Used only to equalise the cost of the "account does not exist" branch. Value is
# irrelevant: it can never match, because the salt is not in the database.
_DUMMY_HASH = (
    "pbkdf2_sha256$240000$AAAAAAAAAAAAAAAAAAAAAA$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_TRACKED_KEYS = 10_000


class AuthError(Exception):
    """A rejected identity operation, safe to describe to the caller."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def normalize_username(raw: str) -> str:
    """Trim and sanity-check a typed account name.

    Length limits are real (the column is 64) and control characters are rejected
    because they break logs and rendered UI. Nothing else is restricted -- no
    minimum length, no character class, no e-mail shape -- because the brief for
    this batch is explicitly "no restrictions yet".
    """
    if not isinstance(raw, str):
        raise AuthError("invalid_username", "用户名格式不正确。", 400)
    username = raw.strip()
    if not username:
        raise AuthError("invalid_username", "用户名不能为空。", 400)
    if len(username) > 64:
        raise AuthError("invalid_username", "用户名最多 64 个字符。", 400)
    if _CONTROL_CHARS.search(username):
        raise AuthError("invalid_username", "用户名不能包含控制字符。", 400)
    return username


class LoginThrottle:
    """Sliding-window failure counter with a hard cap on tracked keys."""

    def __init__(self, max_attempts: int = 8, window_seconds: int = 300) -> None:
        self.max_attempts = max(1, max_attempts)
        self.window_seconds = max(1, window_seconds)
        self._failures: OrderedDict[str, list[datetime]] = OrderedDict()

    def _recent(self, key: str, now: datetime) -> list[datetime]:
        cutoff = now - timedelta(seconds=self.window_seconds)
        kept = [moment for moment in self._failures.get(key, []) if moment > cutoff]
        if kept:
            self._failures[key] = kept
            self._failures.move_to_end(key)
        elif key in self._failures:
            del self._failures[key]
        return kept

    def retry_after(self, username: str, client: str | None) -> int | None:
        """Seconds to wait, or ``None`` when the caller may proceed."""
        now = _now()
        for key in self._keys(username, client):
            recent = self._recent(key, now)
            if len(recent) >= self.max_attempts:
                oldest = min(recent)
                remaining = self.window_seconds - int((now - oldest).total_seconds())
                return max(1, remaining)
        return None

    def record_failure(self, username: str, client: str | None) -> None:
        now = _now()
        for key in self._keys(username, client):
            self._recent(key, now)
            self._failures.setdefault(key, []).append(now)
            self._failures.move_to_end(key)
        while len(self._failures) > _MAX_TRACKED_KEYS:
            self._failures.popitem(last=False)

    def reset(self, username: str, client: str | None) -> None:
        for key in self._keys(username, client):
            self._failures.pop(key, None)

    @staticmethod
    def _keys(username: str, client: str | None) -> list[str]:
        keys = [f"user:{username.casefold()}"]
        if client:
            keys.append(f"client:{client}")
        return keys


@dataclass(frozen=True)
class IssuedSession:
    user: User
    token: str
    expires_at: datetime


def _configured_throttle() -> LoginThrottle:
    settings = get_settings()
    return LoginThrottle(
        max_attempts=settings.auth_login_max_attempts,
        window_seconds=settings.auth_login_window_seconds,
    )


class AuthService:
    def __init__(self, throttle: LoginThrottle | None = None) -> None:
        self._throttle = throttle or _configured_throttle()

    # ── accounts ─────────────────────────────────────────────────────────────

    def find_by_username(self, session: Session, username: str) -> User | None:
        return session.scalar(select(User).where(func.lower(User.username) == username.casefold()))

    def register(
        self,
        session: Session,
        username: str,
        password: str,
        *,
        client: str | None = None,
        user_agent: str | None = None,
        settings: Settings | None = None,
    ) -> IssuedSession:
        settings = settings or get_settings()
        if not settings.auth_allow_registration:
            raise AuthError(
                "registration_disabled", "当前部署已关闭自助注册，请联系管理员创建账号。", 403,
            )
        clean = normalize_username(username)
        if not password:
            raise AuthError("invalid_password", "密码不能为空。", 400)
        if len(password) > 256:
            raise AuthError("invalid_password", "密码最多 256 个字符。", 400)
        if self.find_by_username(session, clean) is not None:
            raise AuthError("username_taken", "该用户名已被占用。", 409)
        user = User(
            username=clean,
            password_hash=hash_password(password),
            role="user",
            status="active",
            last_login_at=_now(),
        )
        session.add(user)
        session.flush()
        issued = self._issue(session, user, user_agent=user_agent, settings=settings)
        session.commit()
        logger.info("auth.register user_id=%s username=%r client=%s", user.id, clean, client)
        return issued

    def login(
        self,
        session: Session,
        username: str,
        password: str,
        *,
        client: str | None = None,
        user_agent: str | None = None,
        settings: Settings | None = None,
    ) -> IssuedSession:
        settings = settings or get_settings()
        try:
            clean = normalize_username(username)
        except AuthError:
            # A malformed name is still a failed attempt for the client key.
            self._throttle.record_failure(str(username or ""), client)
            raise AuthError("invalid_credentials", "用户名或密码不正确。", 401) from None

        retry_after = self._throttle.retry_after(clean, client)
        if retry_after is not None:
            logger.warning("auth.login.throttled username=%r client=%s", clean, client)
            raise AuthError(
                "too_many_attempts",
                f"登录尝试过于频繁，请在 {retry_after} 秒后重试。",
                429,
            )

        user = self.find_by_username(session, clean)
        stored = user.password_hash if user is not None else _DUMMY_HASH
        password_ok = verify_password(password, stored)
        if user is None or not password_ok:
            self._throttle.record_failure(clean, client)
            logger.warning("auth.login.failed username=%r client=%s", clean, client)
            raise AuthError("invalid_credentials", "用户名或密码不正确。", 401)
        if user.status != "active":
            logger.warning("auth.login.disabled user_id=%s", user.id)
            raise AuthError("account_disabled", "该账号已被停用。", 403)

        self._throttle.reset(clean, client)
        # Opportunistic upgrade: a stored hash below the current work factor is
        # re-hashed here, where the plaintext is legitimately available.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = _now()
        issued = self._issue(session, user, user_agent=user_agent, settings=settings)
        session.commit()
        logger.info("auth.login.succeeded user_id=%s client=%s", user.id, client)
        return issued

    def resolve_token(self, session: Session, token: str) -> User:
        """Return the account behind a bearer token, or raise ``AuthError``."""
        if not token:
            raise AuthError("not_authenticated", "请先登录。", 401)
        try:
            digest = token_digest(token)
        except ValueError:
            raise AuthError("invalid_token", "登录状态无效，请重新登录。", 401) from None
        row = session.scalar(select(AuthSession).where(AuthSession.token_digest == digest))
        if row is None:
            raise AuthError("invalid_token", "登录状态无效，请重新登录。", 401)
        now = _now()
        if row.revoked_at is not None:
            raise AuthError("token_revoked", "登录状态已失效，请重新登录。", 401)
        if _aware(row.expires_at) <= now:
            raise AuthError("token_expired", "登录状态已过期，请重新登录。", 401)
        user = session.get(User, row.user_id)
        if user is None:
            raise AuthError("invalid_token", "登录状态无效，请重新登录。", 401)
        if user.status != "active":
            raise AuthError("account_disabled", "该账号已被停用。", 403)
        if row.last_used_at is None or (now - _aware(row.last_used_at)) > timedelta(minutes=5):
            row.last_used_at = now
        return user

    def logout(self, session: Session, token: str, *, reason: str = "logout") -> None:
        """Revoke one session. Idempotent: an unknown token is not an error."""
        try:
            digest = token_digest(token)
        except ValueError:
            return
        row = session.scalar(select(AuthSession).where(AuthSession.token_digest == digest))
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = _now()
        row.revoked_reason = reason
        session.commit()

    def revoke_all_sessions(
        self, session: Session, user_id: UUID, *, except_token: str | None = None, reason: str,
    ) -> int:
        """Revoke every session of an account, optionally sparing the caller's own.

        Called after a password change and (in a later batch) after disabling an
        account: §11.2 requires that these take effect immediately, which means
        the other devices must be cut off rather than left to expire naturally.
        """
        keep: str | None = None
        if except_token:
            try:
                keep = token_digest(except_token)
            except ValueError:
                keep = None
        statement = (
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now(), revoked_reason=reason)
        )
        if keep is not None:
            statement = statement.where(AuthSession.token_digest != keep)
        result = session.execute(statement)
        session.commit()
        return int(result.rowcount or 0)

    def rename(
        self, session: Session, user: User, new_username: str, current_password: str | None = None,
    ) -> User:
        if current_password is not None and not verify_password(current_password, user.password_hash):
            raise AuthError("invalid_credentials", "当前密码不正确。", 403)
        clean = normalize_username(new_username)
        if clean.casefold() != user.username.casefold():
            existing = self.find_by_username(session, clean)
            if existing is not None and existing.id != user.id:
                raise AuthError("username_taken", "该用户名已被占用。", 409)
        user.username = clean
        session.commit()
        logger.info("auth.username.changed user_id=%s", user.id)
        return user

    def change_password(
        self,
        session: Session,
        user: User,
        current_password: str,
        new_password: str,
        *,
        keep_token: str | None = None,
    ) -> User:
        if not verify_password(current_password, user.password_hash):
            raise AuthError("invalid_credentials", "当前密码不正确。", 403)
        if not new_password:
            raise AuthError("invalid_password", "新密码不能为空。", 400)
        if len(new_password) > 256:
            raise AuthError("invalid_password", "新密码最多 256 个字符。", 400)
        user.password_hash = hash_password(new_password)
        session.flush()
        revoked = self.revoke_all_sessions(
            session, user.id, except_token=keep_token, reason="password_changed",
        )
        logger.info("auth.password.changed user_id=%s revoked_other_sessions=%s", user.id, revoked)
        return user

    # ── internals ────────────────────────────────────────────────────────────

    def _issue(
        self, session: Session, user: User, *, user_agent: str | None, settings: Settings,
    ) -> IssuedSession:
        token = new_session_token()
        expires_at = _now() + timedelta(hours=max(1, settings.auth_session_ttl_hours))
        session.add(AuthSession(
            user_id=user.id,
            token_digest=token_digest(token),
            user_agent=(user_agent[:255] or None) if user_agent else None,
            expires_at=expires_at,
            last_used_at=_now(),
        ))
        session.flush()
        return IssuedSession(user=user, token=token, expires_at=expires_at)


auth_service = AuthService()


__all__ = ["AuthError", "AuthService", "IssuedSession", "LoginThrottle", "auth_service", "normalize_username"]
