from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from .accounts import Owner
from .exceptions import UnderageClientError

_CLIENT_ID_RE = re.compile(r"[A-Z0-9-]{4,32}")
_PHONE_RE = re.compile(r"\+?\d{10,15}")


class ClientStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"


class Client:
    MIN_AGE = 18

    def __init__(
        self,
        full_name: str,
        birth_date: date | str,
        *,
        phone: str,
        email: str | None = None,
        client_id: str | None = None,
        today: date | None = None,
    ) -> None:
        self._owner = Owner(full_name, email)
        self._birth_date = self._parse_birth_date(birth_date)
        self._phone = self._validate_phone(phone)
        self._client_id = (
            self._generate_client_id() if client_id is None
            else self._validate_client_id(client_id)
        )
        self._status = ClientStatus.ACTIVE
        self._account_numbers: list[str] = []

        age = self.age_on(today or date.today())
        if age < 0:
            raise ValueError(f"Дата рождения {self._birth_date} в будущем")
        if age < self.MIN_AGE:
            raise UnderageClientError(
                f"Клиенту {self.full_name} {age} лет — обслуживание с {self.MIN_AGE}"
            )

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def full_name(self) -> str:
        return self._owner.full_name

    @property
    def email(self) -> str | None:
        return self._owner.email

    @property
    def phone(self) -> str:
        return self._phone

    @property
    def contacts(self) -> dict[str, str | None]:
        return {"phone": self._phone, "email": self._owner.email}

    @property
    def owner(self) -> Owner:
        return self._owner

    @property
    def birth_date(self) -> date:
        return self._birth_date

    @property
    def age(self) -> int:
        return self.age_on(date.today())

    @property
    def status(self) -> ClientStatus:
        return self._status

    @property
    def is_blocked(self) -> bool:
        return self._status is ClientStatus.BLOCKED

    @property
    def account_numbers(self) -> tuple[str, ...]:
        return tuple(self._account_numbers)

    def age_on(self, day: date) -> int:
        years = day.year - self._birth_date.year
        if (day.month, day.day) < (self._birth_date.month, self._birth_date.day):
            years -= 1
        return years

    def block(self) -> None:
        self._status = ClientStatus.BLOCKED

    def unblock(self) -> None:
        self._status = ClientStatus.ACTIVE

    def add_account_number(self, account_number: str) -> None:
        if account_number in self._account_numbers:
            raise ValueError(f"Счёт {account_number} уже привязан к клиенту")
        self._account_numbers.append(account_number)

    @staticmethod
    def _parse_birth_date(value: Any) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                raise ValueError(
                    f"Дата рождения {value!r}: ожидался формат ГГГГ-ММ-ДД"
                ) from None
        raise TypeError(
            f"Дата рождения должна быть date или строкой, получено {type(value).__name__}"
        )

    @staticmethod
    def _validate_phone(value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(f"Телефон должен быть строкой, получено {type(value).__name__}")
        normalized = re.sub(r"[\s()\-]", "", value)
        if not _PHONE_RE.fullmatch(normalized):
            raise ValueError(f"Некорректный телефон {value!r}: нужно 10–15 цифр")
        return normalized

    @staticmethod
    def _validate_client_id(value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(f"ID клиента должен быть строкой, получено {type(value).__name__}")
        normalized = value.strip().upper()
        if not _CLIENT_ID_RE.fullmatch(normalized):
            raise ValueError(f"Некорректный ID клиента {value!r}: 4–32 символа A-Z, 0-9, -")
        return normalized

    @staticmethod
    def _generate_client_id() -> str:
        return f"CL-{uuid.uuid4().hex[:8].upper()}"

    def __str__(self) -> str:
        return (
            f"Client | {self.full_name} | ID: {self._client_id} | "
            f"Статус: {self._status.value} | Счетов: {len(self._account_numbers)}"
        )

    def __repr__(self) -> str:
        return f"Client(full_name={self.full_name!r}, client_id={self._client_id!r})"
