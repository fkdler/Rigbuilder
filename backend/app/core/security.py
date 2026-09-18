"""Password and session-token primitives for the local account system.

Plan_V4.5 §11.2 requires that a password is only ever stored as a strong hash and
that a session token is only ever stored as a digest. Both are implemented here
with the standard library alone (``hashlib`` / ``hmac`` / ``secrets``) so that
adding accounts does not add a runtime dependency to a deployment whose whole
value proposition is that it can be rebuilt offline.

PBKDF2-HMAC-SHA256 is used rather than Argon2/bcrypt for the same reason. It is
the same primitive Django shipped as its default for years and it is a real
key-derivation function, not a bare digest: the parameters are stored inside the
hash string, so the iteration count can be raised later without invalidating
existing accounts -- ``needs_rehash`` reports when a stored hash is below the
current target and the caller can silently upgrade it at the next successful
login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 240_000
_SALT_BYTES = 16
# 32 random bytes. ``token_urlsafe`` renders them as 43 characters, and the
# digest of that string is what reaches the database.
TOKEN_BYTES = 32
MIN_PASSWORD_LENGTH = 1


class PasswordFormatError(ValueError):
    """The stored hash is not a value this module produced."""


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    salt: bytes | None = None,
) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt>$<digest>``.

    The encoding is self-describing on purpose: an operator who wants to raise
    the work factor can do it by changing the default here, and every stored
    hash stays verifiable because it carries its own parameters.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("password must not be empty")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    salt = salt or secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64_encode(salt)}${_b64_encode(digest)}"


def _parse(stored: str) -> tuple[str, int, bytes, bytes]:
    if not isinstance(stored, str):
        raise PasswordFormatError("stored hash must be a string")
    parts = stored.split("$")
    if len(parts) != 4:
        raise PasswordFormatError("stored hash must have four fields")
    algorithm, raw_iterations, raw_salt, raw_digest = parts
    try:
        iterations = int(raw_iterations)
        salt = _b64_decode(raw_salt)
        digest = _b64_decode(raw_digest)
    except (ValueError, TypeError) as exc:
        raise PasswordFormatError("stored hash is not decodable") from exc
    if iterations <= 0 or not salt or not digest:
        raise PasswordFormatError("stored hash has empty parameters")
    return algorithm, iterations, salt, digest


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored hash.

    Any malformed or foreign hash returns ``False`` instead of raising: a corrupt
    row must deny access, not break the login endpoint.
    """
    if not isinstance(password, str) or not password:
        return False
    try:
        algorithm, iterations, salt, expected = _parse(stored)
    except PasswordFormatError:
        return False
    if algorithm != ALGORITHM:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str, *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """True when a stored hash uses an older algorithm or a lower work factor."""
    try:
        algorithm, stored_iterations, _salt, _digest = _parse(stored)
    except PasswordFormatError:
        return True
    return algorithm != ALGORITHM or stored_iterations < iterations


def new_session_token() -> str:
    """A fresh opaque bearer token. This value is shown to the client exactly once."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    """Hex SHA-256 of a bearer token -- the only form persisted.

    A plain digest is enough here (unlike for passwords) because the token is 256
    bits of CSPRNG output with no guessable structure, so there is no dictionary
    to attack; the digest exists so a database leak does not hand out live
    sessions.
    """
    if not isinstance(token, str) or not token:
        raise ValueError("token must be a non-empty string")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "ALGORITHM",
    "DEFAULT_ITERATIONS",
    "MIN_PASSWORD_LENGTH",
    "TOKEN_BYTES",
    "PasswordFormatError",
    "hash_password",
    "needs_rehash",
    "new_session_token",
    "token_digest",
    "verify_password",
]
