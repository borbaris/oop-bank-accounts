from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .accounts import _CENT, AccountStatus, BankAccount, Currency, Owner
from .exceptions import InvalidOperationError


class SavingsAccount(BankAccount):
    def __init__(
        self,
        owner: Owner | str,
        balance: Decimal | int | float | str = 0,
        currency: Currency | str = Currency.RUB,
        *,
        min_balance: Decimal | int | float | str = 0,
        monthly_rate: Decimal | int | float | str = Decimal("0.01"),
        account_number: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
    ) -> None:
        super().__init__(owner, balance, currency, account_number, status)

        self._min_balance = self._to_money(min_balance, field="Минимальный остаток")
        if self._min_balance < 0:
            raise InvalidOperationError("Минимальный остаток не может быть отрицательным")

        self._monthly_rate = self._to_rate(
            monthly_rate, field="Месячная ставка", minimum=Decimal(0), maximum=Decimal(1)
        )

        if self._status is not AccountStatus.CLOSED and self._balance < self._min_balance:
            raise InvalidOperationError(
                f"Начальный баланс {self._money(self._balance)} меньше "
                f"минимального остатка {self._money(self._min_balance)}"
            )

    @property
    def min_balance(self) -> Decimal:
        return self._min_balance

    @property
    def monthly_rate(self) -> Decimal:
        return self._monthly_rate

    @property
    def available_balance(self) -> Decimal:
        """Сколько можно снять, не нарушив минимальный остаток."""
        return max(self._balance - self._min_balance, Decimal("0.00"))

    def withdraw(self, amount: Decimal | int | float | str) -> Decimal:
        self._ensure_active()
        value = self._validate_amount(amount)
        self._check_withdrawal_limit(value)
        self._ensure_funds(
            value,
            available=self.available_balance,
            hint=f"неснижаемый остаток {self._money(self._min_balance)}",
        )
        self._balance -= value
        return self._balance

    def apply_monthly_interest(self) -> Decimal:
        """Начислить проценты за месяц. Возвращает начисленную сумму."""
        self._ensure_active()
        interest = (self._balance * self._monthly_rate).quantize(_CENT, ROUND_HALF_UP)
        self._balance += interest
        return interest

    def close(self) -> None:
        """Закрыть вклад: весь остаток, включая неснижаемый, выплачивается клиенту.

        Без этого переопределения счёт с min_balance > 0 нельзя было бы закрыть:
        базовый close() требует нулевой баланс, а withdraw() не даёт опуститься
        ниже минимума.
        """
        self._ensure_active()
        self._balance = Decimal("0.00")
        self._status = AccountStatus.CLOSED

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            min_balance=self._min_balance,
            available_balance=self.available_balance,
            monthly_rate=self._monthly_rate,
        )
        return info

    def __str__(self) -> str:
        return (
            f"{super().__str__()} | "
            f"Мин. остаток: {self._format_money(self._min_balance)} | "
            f"Ставка: {self._format_rate(self._monthly_rate)}/мес"
        )
