from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Any

from .accounts import AbstractAccount, Currency, normalize_account_number
from .exceptions import InvalidOperationError, TransactionError


class TransactionType(StrEnum):
    INTERNAL_TRANSFER = "internal_transfer"
    EXTERNAL_TRANSFER = "external_transfer"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransactionPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3

    @classmethod
    def parse(cls, value: TransactionPriority | str | int) -> TransactionPriority:
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and value.strip().upper() in cls.__members__:
            return cls[value.strip().upper()]
        allowed = ", ".join(p.name.lower() for p in cls)
        raise ValueError(f"Неизвестный приоритет {value!r}. Допустимые: {allowed}")


_FINAL = (TransactionStatus.COMPLETED, TransactionStatus.FAILED, TransactionStatus.CANCELLED)


class Transaction:
    def __init__(
        self,
        tx_type: TransactionType | str,
        amount: Decimal | int | float | str,
        currency: Currency | str,
        *,
        sender: str,
        receiver: str,
        initiator_id: str,
        created_at: datetime,
        priority: TransactionPriority | str = TransactionPriority.NORMAL,
        scheduled_at: datetime | None = None,
        description: str = "",
    ) -> None:
        self._id = f"TX-{uuid.uuid4().hex[:10].upper()}"
        self._type = TransactionType(tx_type)
        self._amount = AbstractAccount._to_money(amount, field="Сумма перевода")
        if self._amount <= 0:
            raise InvalidOperationError(f"Сумма перевода должна быть положительной: {amount!r}")
        self._currency = Currency.parse(currency)
        self._sender = normalize_account_number(sender)
        self._receiver = normalize_account_number(receiver)
        if self._sender == self._receiver:
            raise TransactionError("Отправитель и получатель совпадают")
        self._initiator_id = initiator_id
        self._priority = TransactionPriority.parse(priority)
        self._description = str(description).strip()

        if not isinstance(created_at, datetime):
            raise TypeError("created_at должен быть datetime")
        self._created_at = created_at
        if scheduled_at is not None and not isinstance(scheduled_at, datetime):
            raise TypeError("scheduled_at должен быть datetime или None")
        self._scheduled_at = scheduled_at or created_at

        self._status = TransactionStatus.PENDING
        self._failure_reason: str | None = None
        self._fee = Decimal("0.00")
        self._debit_amount: Decimal | None = None
        self._credit_amount: Decimal | None = None
        self._attempts = 0
        self._last_attempt_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._history: list[str] = []
        self._log(created_at, "создана")

    @property
    def transaction_id(self) -> str:
        return self._id

    @property
    def type(self) -> TransactionType:
        return self._type

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def sender(self) -> str:
        return self._sender

    @property
    def receiver(self) -> str:
        return self._receiver

    @property
    def initiator_id(self) -> str:
        return self._initiator_id

    @property
    def priority(self) -> TransactionPriority:
        return self._priority

    @property
    def description(self) -> str:
        return self._description

    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def fee(self) -> Decimal:
        return self._fee

    @property
    def debit_amount(self) -> Decimal | None:
        return self._debit_amount

    @property
    def credit_amount(self) -> Decimal | None:
        return self._credit_amount

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def is_final(self) -> bool:
        return self._status in _FINAL

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def scheduled_at(self) -> datetime:
        """Не раньше этого времени транзакцию можно выполнять."""
        return self._scheduled_at

    @property
    def last_attempt_at(self) -> datetime | None:
        return self._last_attempt_at

    @property
    def finished_at(self) -> datetime | None:
        return self._finished_at

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(self._history)

    def is_ready(self, now: datetime) -> bool:
        return self._status is TransactionStatus.PENDING and self._scheduled_at <= now

    def _reschedule(self, now: datetime, until: datetime, reason: str) -> None:
        self._ensure_pending()
        self._scheduled_at = until
        self._log(now, f"перенесена на {until:%d.%m %H:%M}: {reason}")

    def _start_attempt(self, at: datetime) -> None:
        self._ensure_pending()
        self._attempts += 1
        self._last_attempt_at = at

    def _complete(
        self, at: datetime, *, fee: Decimal, debit_amount: Decimal, credit_amount: Decimal
    ) -> None:
        self._ensure_pending()
        self._fee = fee
        self._debit_amount = debit_amount
        self._credit_amount = credit_amount
        self._finish(at, TransactionStatus.COMPLETED, "выполнена")

    def _fail(self, at: datetime, reason: str) -> None:
        self._ensure_pending()
        self._failure_reason = reason
        self._finish(at, TransactionStatus.FAILED, f"отказ: {reason}")

    def _cancel(self, at: datetime, reason: str) -> None:
        self._ensure_pending()
        self._failure_reason = reason
        self._finish(at, TransactionStatus.CANCELLED, f"отменена: {reason}")

    def _finish(self, at: datetime, status: TransactionStatus, note: str) -> None:
        self._status = status
        self._finished_at = at
        self._log(at, note)

    def _ensure_pending(self) -> None:
        if self.is_final:
            raise TransactionError(
                f"Транзакция {self._id} уже завершена (статус {self._status.value})"
            )

    def _log(self, at: datetime, text: str) -> None:
        self._history.append(f"[{at:%d.%m %H:%M}] {text}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "type": self._type.value,
            "amount": self._amount,
            "currency": self._currency.value,
            "fee": self._fee,
            "sender": self._sender,
            "receiver": self._receiver,
            "initiator_id": self._initiator_id,
            "priority": self._priority.name.lower(),
            "status": self._status.value,
            "failure_reason": self._failure_reason,
            "attempts": self._attempts,
            "created_at": self._created_at,
            "scheduled_at": self._scheduled_at,
            "finished_at": self._finished_at,
        }

    def __str__(self) -> str:
        amount = f"{self._amount:,.2f}".replace(",", " ")
        text = (
            f"{self._id} | {self._type.value} | {amount} {self._currency.value} | "
            f"****{self._sender[-4:]} → ****{self._receiver[-4:]} | "
            f"{self._priority.name.lower()} | {self._status.value}"
        )
        if self._fee:
            text += f" | комиссия {self._fee}"
        if self._failure_reason:
            text += f" | {self._failure_reason}"
        return text

    def __repr__(self) -> str:
        return f"<Transaction {self._id} {self._status.value}>"
