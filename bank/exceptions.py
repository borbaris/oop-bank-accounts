from __future__ import annotations

from decimal import Decimal


class AccountError(Exception):
    """Базовое исключение для всех ошибок счёта."""


class AccountStatusError(AccountError):
    """Операция запрещена текущим статусом счёта."""


class AccountFrozenError(AccountStatusError):
    """Счёт заморожен — операции с деньгами запрещены."""


class AccountClosedError(AccountStatusError):
    """Счёт закрыт — любые изменения запрещены."""


class InvalidOperationError(AccountError):
    """Некорректная операция: неверная сумма, недопустимый переход статуса и т.п."""


class InsufficientFundsError(InvalidOperationError):
    """На счёте недостаточно средств для списания."""

    def __init__(
        self,
        available: Decimal,
        requested: Decimal,
        currency: str,
        hint: str | None = None,
    ) -> None:
        self.available = available
        self.requested = requested
        self.currency = currency
        self.hint = hint
        message = (
            f"Недостаточно средств: доступно {available} {currency}, "
            f"запрошено {requested} {currency}"
        )
        if hint:
            message += f" ({hint})"
        super().__init__(message)


class WithdrawalLimitExceededError(InvalidOperationError):
    """Сумма одного снятия больше лимита счёта."""

    def __init__(self, limit: Decimal, requested: Decimal, currency: str) -> None:
        self.limit = limit
        self.requested = requested
        self.currency = currency
        super().__init__(
            f"Превышен лимит снятия: максимум {limit} {currency} за операцию, "
            f"запрошено {requested} {currency}"
        )
