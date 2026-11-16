from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

PASSWORD_MIN_LENGTH = 6
PBKDF2_ITERATIONS = 100_000


class ActivityKind(StrEnum):
    FAILED_LOGIN = "failed_login"
    CLIENT_BLOCKED = "client_blocked"
    BLOCKED_CLIENT_LOGIN = "blocked_client_login"
    UNKNOWN_CLIENT_LOGIN = "unknown_client_login"
    UNAUTHENTICATED_OPERATION = "unauthenticated_operation"
    BLOCKED_CLIENT_OPERATION = "blocked_client_operation"
    FOREIGN_ACCOUNT_ACCESS = "foreign_account_access"
    NIGHT_OPERATION = "night_operation"


@dataclass(frozen=True, slots=True)
class SuspiciousActivity:
    timestamp: datetime
    kind: ActivityKind
    client_id: str | None
    details: str

    def __str__(self) -> str:
        return (
            f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.kind.value}: "
            f"клиент {self.client_id or '—'} — {self.details}"
        )


def validate_password(password: object) -> str:
    if not isinstance(password, str):
        raise TypeError(f"Пароль должен быть строкой, получено {type(password).__name__}")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Пароль слишком короткий: минимум {PASSWORD_MIN_LENGTH} символов")
    return password


def hash_password(
    password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS
) -> tuple[bytes, bytes]:
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return salt, digest


def verify_password(
    password: str, salt: bytes, expected: bytes, iterations: int = PBKDF2_ITERATIONS
) -> bool:
    _, digest = hash_password(password, salt, iterations)
    return hmac.compare_digest(digest, expected)
