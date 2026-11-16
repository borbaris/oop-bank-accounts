from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from .accounts import AccountStatus, BankAccount, Currency, Owner
from .exceptions import InvalidOperationError


class PremiumAccount(BankAccount):
    WITHDRAWAL_LIMIT: ClassVar[Decimal] = Decimal("1000000.00")  # у BankAccount 100 000
    DEFAULT_OVERDRAFT_LIMIT: ClassVar[Decimal] = Decimal("50000.00")
    DEFAULT_WITHDRAWAL_FEE: ClassVar[Decimal] = Decimal("100.00")

    def __init__(
        self,
        owner: Owner | str,
        balance: Decimal | int | float | str = 0,
        currency: Currency | str = Currency.RUB,
        *,
        overdraft_limit: Decimal | int | float | str | None = None,
        withdrawal_fee: Decimal | int | float | str | None = None,
        withdrawal_limit: Decimal | int | float | str | None = None,
        account_number: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
    ) -> None:
        super().__init__(owner, balance, currency, account_number, status)

        self._overdraft_limit = self._non_negative(
            self.DEFAULT_OVERDRAFT_LIMIT if overdraft_limit is None else overdraft_limit,
            field="Лимит овердрафта",
        )
        self._withdrawal_fee = self._non_negative(
            self.DEFAULT_WITHDRAWAL_FEE if withdrawal_fee is None else withdrawal_fee,
            field="Комиссия за снятие",
        )
        self._withdrawal_limit = self._to_money(
            self.WITHDRAWAL_LIMIT if withdrawal_limit is None else withdrawal_limit,
            field="Лимит снятия",
        )
        if self._withdrawal_limit <= 0:
            raise InvalidOperationError("Лимит снятия должен быть положительным")

    def _non_negative(self, value: Any, *, field: str) -> Decimal:
        money = self._to_money(value, field=field)
        if money < 0:
            raise InvalidOperationError(f"{field} не может быть отрицательным")
        return money

    @property
    def withdrawal_limit(self) -> Decimal:
        return self._withdrawal_limit

    @property
    def overdraft_limit(self) -> Decimal:
        return self._overdraft_limit

    @property
    def withdrawal_fee(self) -> Decimal:
        return self._withdrawal_fee

    @property
    def available_balance(self) -> Decimal:
        """Свои деньги + овердрафт."""
        return self._balance + self._overdraft_limit

    @property
    def in_overdraft(self) -> bool:
        return self._balance < 0

    def withdraw(self, amount: Decimal | int | float | str) -> Decimal:
        self._start_operation("Снятие")
        value = self._validate_amount(amount)
        self._check_withdrawal_limit(value)
        total = value + self._withdrawal_fee
        self._ensure_funds(
            total,
            available=self.available_balance,
            hint=(
                f"{self._money(value)} + комиссия {self._money(self._withdrawal_fee)}, "
                f"овердрафт до {self._money(self._overdraft_limit)}"
            ),
        )
        self._balance -= total
        return self._balance

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            overdraft_limit=self._overdraft_limit,
            withdrawal_fee=self._withdrawal_fee,
            available_balance=self.available_balance,
            in_overdraft=self.in_overdraft,
        )
        return info

    def __str__(self) -> str:
        return (
            f"{super().__str__()} | "
            f"Овердрафт: до {self._format_money(self._overdraft_limit)} | "
            f"Комиссия: {self._format_money(self._withdrawal_fee)} | "
            f"Лимит: {self._format_money(self._withdrawal_limit)}"
        )
