from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, ClassVar

from .exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
    WithdrawalLimitExceededError,
)

_CENT = Decimal("0.01")
_ACCOUNT_NUMBER_RE = re.compile(r"[A-Z0-9]{4,34}")
SecurityCheck = Callable[[str, bool], None]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class BalanceEntry:
    timestamp: datetime
    operation: str
    change: Decimal
    balance: Decimal


class AccountStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"

    @classmethod
    def parse(cls, value: AccountStatus | str) -> AccountStatus:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError:
                pass
        allowed = ", ".join(s.value for s in cls)
        raise ValueError(f"Неизвестный статус {value!r}. Допустимые: {allowed}")


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    KZT = "KZT"
    CNY = "CNY"

    @classmethod
    def parse(cls, value: Currency | str) -> Currency:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().upper())
            except ValueError:
                pass
        allowed = ", ".join(c.value for c in cls)
        raise ValueError(f"Неподдерживаемая валюта {value!r}. Допустимые: {allowed}")


def normalize_account_number(number: Any) -> str:
    if not isinstance(number, str):
        raise TypeError(f"Номер счёта должен быть строкой, получено {type(number).__name__}")
    normalized = "".join(number.split()).upper()
    if not _ACCOUNT_NUMBER_RE.fullmatch(normalized):
        raise ValueError(
            f"Некорректный номер счёта {number!r}: нужно 4–34 латинских буквы/цифры"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class Owner:
    """Данные владельца счёта."""

    full_name: str
    email: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.full_name, str) or not self.full_name.strip():
            raise ValueError("Имя владельца должно быть непустой строкой")
        object.__setattr__(self, "full_name", " ".join(self.full_name.split()))

        if self.email is not None:
            if not isinstance(self.email, str) or not re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+", self.email.strip()
            ):
                raise ValueError(f"Некорректный email владельца: {self.email!r}")
            object.__setattr__(self, "email", self.email.strip().lower())

    def __str__(self) -> str:
        return self.full_name


class AbstractAccount(ABC):
    def __init__(
        self,
        account_number: str,
        owner: Owner | str,
        balance: Decimal | int | float | str = 0,
        status: AccountStatus | str = AccountStatus.ACTIVE,
    ) -> None:
        self._account_number = self._validate_account_number(account_number)
        self._owner = self._validate_owner(owner)
        self._balance = self._to_money(balance, field="Начальный баланс")
        if self._balance < 0:
            raise InvalidOperationError("Начальный баланс не может быть отрицательным")
        self._status = AccountStatus.parse(status)
        if self._status is AccountStatus.CLOSED and self._balance != 0:
            raise InvalidOperationError("Закрытый счёт не может иметь ненулевой баланс")
        self._security: SecurityCheck | None = None
        self._clock: Clock = datetime.now
        self._history = [
            BalanceEntry(self._clock(), "Открытие счёта", self._balance, self._balance)
        ]

    @property
    def account_number(self) -> str:
        return self._account_number

    @property
    def owner(self) -> Owner:
        return self._owner

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def balance_history(self) -> tuple[BalanceEntry, ...]:
        return tuple(self._history)

    @property
    def status(self) -> AccountStatus:
        return self._status

    @property
    def masked_number(self) -> str:
        return f"****{self._account_number[-4:]}"

    @abstractmethod
    def deposit(self, amount: Decimal | int | float | str) -> Decimal:
        """Пополнить счёт. Возвращает новый баланс."""

    @abstractmethod
    def withdraw(self, amount: Decimal | int | float | str) -> Decimal:
        """Снять со счёта. Возвращает новый баланс."""

    @abstractmethod
    def get_account_info(self) -> dict[str, Any]:
        """Сводка по счёту в виде словаря."""

    def freeze(self) -> None:
        self._ensure_not_closed()
        if self._status is AccountStatus.FROZEN:
            raise InvalidOperationError(f"Счёт {self.masked_number} уже заморожен")
        self._status = AccountStatus.FROZEN

    def unfreeze(self) -> None:
        self._check_security("Разморозка счёта", by_client=False)
        self._ensure_not_closed()
        if self._status is not AccountStatus.FROZEN:
            raise InvalidOperationError(f"Счёт {self.masked_number} не заморожен")
        self._status = AccountStatus.ACTIVE

    def close(self) -> None:
        """Закрыть пустой счёт. Если деньги есть — ошибка, ничего не теряется."""
        self._start_operation("Закрытие счёта")
        self._ensure_can_close()
        if self._balance != 0:
            raise InvalidOperationError(
                f"Нельзя закрыть счёт {self.masked_number} с ненулевым балансом "
                f"({self._balance}) — сначала снимите средства или используйте "
                f"close_with_payout()"
            )
        self._status = AccountStatus.CLOSED

    def close_with_payout(self) -> Decimal:
        """Закрыть счёт и выдать весь остаток (в т.ч. неснижаемый). Возвращает сумму.

        Деньги не пропадают: сумма возвращается вызывающему коду.
        В банке это делает Bank.close_account(..., transfer_to=...) — переводит
        её на другой счёт клиента.
        """
        self._start_operation("Закрытие счёта")
        self._ensure_can_close()
        if self._balance < 0:
            raise InvalidOperationError(
                f"Нельзя закрыть счёт {self.masked_number}: долг {-self._balance} — "
                f"сначала погасите его"
            )
        payout = self._balance
        self._change_balance(-payout, "Закрытие с выплатой остатка")
        self._status = AccountStatus.CLOSED
        return payout

    def attach_security(self, check: SecurityCheck, *, clock: Clock | None = None) -> None:
        """Банк подключает свою проверку (время, сессия, блокировка клиента).

        Подключить можно один раз — отключить или подменить нельзя.
        Счёт без банка (дни 1–2) работает без этой проверки.
        """
        if self._security is not None:
            raise InvalidOperationError(f"Счёт {self.masked_number} уже подключён к банку")
        self._security = check
        if clock is not None:
            self._clock = clock
            if len(self._history) == 1:
                self._history[0] = replace(self._history[0], timestamp=clock())

    def _change_balance(self, change: Decimal, operation: str) -> None:
        self._balance += change
        self._history.append(BalanceEntry(self._clock(), operation, change, self._balance))

    def _check_security(self, action: str, *, by_client: bool = True) -> None:
        if self._security is not None:
            self._security(action, by_client)

    def _start_operation(self, action: str, *, by_client: bool = True) -> None:
        """Вызывается первой строкой в каждом методе, который меняет счёт."""
        self._check_security(action, by_client=by_client)
        self._ensure_active()

    def _ensure_can_close(self) -> None:
        """Доп. условия закрытия для наследников (например, пустой портфель)."""

    def _ensure_not_closed(self) -> None:
        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(f"Счёт {self.masked_number} закрыт")

    def _ensure_active(self) -> None:
        self._ensure_not_closed()
        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError(
                f"Счёт {self.masked_number} заморожен: операции запрещены"
            )

    def _validate_amount(self, amount: Any) -> Decimal:
        """Сумма операции: число > 0, не больше 2 знаков после запятой."""
        value = self._to_money(amount, field="Сумма операции")
        if value <= 0:
            raise InvalidOperationError(
                f"Сумма операции должна быть положительной, получено {value}"
            )
        return value

    @staticmethod
    def _to_decimal(value: Any, *, field: str) -> Decimal:
        """Любое число (int/float/str/Decimal) -> конечный Decimal."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise InvalidOperationError(
                f"{field}: ожидалось число, получено {type(value).__name__}"
            )
        try:
            number = Decimal(str(value) if isinstance(value, float) else value)
        except (InvalidOperation, ValueError):
            raise InvalidOperationError(f"{field}: не число — {value!r}") from None
        if not number.is_finite():
            raise InvalidOperationError(f"{field}: недопустимое значение {value!r}")
        return number

    @classmethod
    def _to_rate(
        cls, value: Any, *, field: str, minimum: Decimal, maximum: Decimal
    ) -> Decimal:
        """Ставка задаётся долей: 0.10 = 10%."""
        rate = cls._to_decimal(value, field=field)
        if not minimum <= rate <= maximum:
            raise InvalidOperationError(
                f"{field}: ожидалась доля от {minimum} до {maximum} "
                f"(0.10 = 10%), получено {value!r}"
            )
        return rate

    @classmethod
    def _to_money(cls, value: Any, *, field: str) -> Decimal:
        amount = cls._to_decimal(value, field=field)
        try:
            quantized = amount.quantize(_CENT)
        except InvalidOperation:
            raise InvalidOperationError(f"{field}: слишком большое значение") from None
        if quantized != amount:
            raise InvalidOperationError(
                f"{field}: не больше 2 знаков после запятой, получено {value!r}"
            )
        return quantized

    @staticmethod
    def _validate_account_number(number: Any) -> str:
        return normalize_account_number(number)

    @staticmethod
    def _validate_owner(owner: Any) -> Owner:
        if isinstance(owner, Owner):
            return owner
        if isinstance(owner, str):
            return Owner(full_name=owner)
        raise TypeError(
            f"Владелец должен быть Owner или строкой, получено {type(owner).__name__}"
        )

    @staticmethod
    def _format_money(value: Decimal) -> str:
        return f"{value:,.2f}".replace(",", " ")

    @staticmethod
    def _format_rate(rate: Decimal) -> str:
        """Decimal("0.0125") -> "1.25%"."""
        return f"{(rate * 100).normalize():f}%"


class BankAccount(AbstractAccount):
    """Обычный банковский счёт в одной из поддерживаемых валют."""

    WITHDRAWAL_LIMIT: ClassVar[Decimal] = Decimal("100000.00")

    def __init__(
        self,
        owner: Owner | str,
        balance: Decimal | int | float | str = 0,
        currency: Currency | str = Currency.RUB,
        account_number: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
    ) -> None:
        self._currency = Currency.parse(currency)
        if account_number is None:
            account_number = self._generate_account_number()
        super().__init__(account_number, owner, balance, status)

    @staticmethod
    def _generate_account_number() -> str:
        """Короткий UUID: первые 12 hex-символов uuid4 в верхнем регистре."""
        return uuid.uuid4().hex[:12].upper()

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def withdrawal_limit(self) -> Decimal:
        return self.WITHDRAWAL_LIMIT

    @property
    def total_value(self) -> Decimal:
        return self._balance

    @property
    def available_balance(self) -> Decimal:
        return self._balance

    def deposit(self, amount: Decimal | int | float | str) -> Decimal:
        self._start_operation("Пополнение")
        value = self._validate_amount(amount)
        self._change_balance(value, "Пополнение")
        return self._balance

    def withdraw(self, amount: Decimal | int | float | str) -> Decimal:
        self._start_operation("Снятие")
        value = self._validate_amount(amount)
        self._check_withdrawal_limit(value)
        self._ensure_funds(value, available=self._balance)
        self._change_balance(-value, "Снятие")
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "account_number": self._account_number,
            "owner": self._owner.full_name,
            "owner_email": self._owner.email,
            "status": self._status.value,
            "balance": self._balance,
            "currency": self._currency.value,
            "withdrawal_limit": self.withdrawal_limit,
        }

    def _debit(self, amount: Decimal) -> None:
        self._start_operation("Списание по переводу", by_client=False)
        self._ensure_funds(amount, available=self.available_balance)
        self._change_balance(-amount, "Списание по переводу")

    def _credit(self, amount: Decimal) -> None:
        self._start_operation("Зачисление по переводу", by_client=False)
        self._change_balance(amount, "Зачисление по переводу")


    def _check_withdrawal_limit(self, amount: Decimal) -> None:
        if amount > self.withdrawal_limit:
            raise WithdrawalLimitExceededError(
                limit=self.withdrawal_limit, requested=amount, currency=self._currency.value
            )

    def _ensure_funds(
        self, required: Decimal, *, available: Decimal, hint: str | None = None
    ) -> None:
        if required > available:
            raise InsufficientFundsError(
                available=available,
                requested=required,
                currency=self._currency.value,
                hint=hint,
            )

    def _money(self, value: Decimal) -> str:
        return f"{self._format_money(value)} {self._currency.value}"

    def __str__(self) -> str:
        return (
            f"{type(self).__name__} | "
            f"Клиент: {self._owner.full_name} | "
            f"Счёт: {self.masked_number} | "
            f"Статус: {self._status.value} | "
            f"Баланс: {self._format_money(self._balance)} {self._currency.value}"
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(owner={self._owner.full_name!r}, "
            f"balance=Decimal('{self._balance}'), currency={self._currency.value!r}, "
            f"account_number={self._account_number!r}, status={self._status.value!r})"
        )