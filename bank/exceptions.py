from __future__ import annotations

from decimal import Decimal


class BankError(Exception):
    """Корень всех ошибок банковской системы."""


class AccountError(BankError):
    """Базовое исключение для всех ошибок счёта."""


class AccountNotFoundError(AccountError):
    """Счёта с таким номером нет в банке."""


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


class ClientError(BankError):
    """Базовое исключение для ошибок клиента."""


class UnderageClientError(ClientError):
    """Клиенту меньше 18 лет."""


class ClientNotFoundError(ClientError):
    """Клиента с таким ID нет в банке."""


class ClientBlockedError(ClientError):
    """Клиент заблокирован (например, после 3 неверных паролей)."""


class AuthenticationError(ClientError):
    """Неверный ID клиента или пароль."""


class AccessDeniedError(ClientError):
    """Нет входа в систему или попытка тронуть чужой счёт."""


class OperatingHoursError(BankError):
    """Операция запрещена в ночное время (00:00–05:00)."""


class TransactionError(BankError):
    """Ошибка транзакции: неверные данные, повторная отмена и т.п."""


class TransactionNotFoundError(TransactionError):
    """Транзакции с таким ID нет в очереди."""


class TransientTransactionError(TransactionError):
    """Временный сбой (например, внешний банк не ответил) — операцию можно повторить."""
