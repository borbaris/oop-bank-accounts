from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from .accounts import _CENT, AccountStatus, BankAccount, Currency, Owner
from .exceptions import InsufficientFundsError, InvalidOperationError


class AssetType(StrEnum):
    STOCKS = "stocks"
    BONDS = "bonds"
    ETF = "etf"

    @classmethod
    def parse(cls, value: AssetType | str) -> AssetType:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError:
                pass
        allowed = ", ".join(a.value for a in cls)
        raise InvalidOperationError(
            f"Недопустимый тип актива {value!r}. Допустимые: {allowed}"
        )


DEMO_GROWTH_RATES: dict[str, str] = {"stocks": "0.10", "bonds": "0.04", "etf": "0.07"}


@dataclass(frozen=True, slots=True)
class GrowthProjection:
    """Результат project_yearly_growth()."""

    by_asset: dict[AssetType, Decimal]
    total_growth: Decimal
    current_value: Decimal
    projected_value: Decimal


class InvestmentAccount(BankAccount):
    def __init__(
        self,
        owner: Owner | str,
        balance: Decimal | int | float | str = 0,
        currency: Currency | str = Currency.RUB,
        *,
        account_number: str | None = None,
        status: AccountStatus | str = AccountStatus.ACTIVE,
    ) -> None:
        super().__init__(owner, balance, currency, account_number, status)
        self._portfolio: dict[AssetType, Decimal] = {
            asset: Decimal("0.00") for asset in AssetType
        }

    @property
    def portfolio(self) -> dict[AssetType, Decimal]:
        """Копия портфеля — снаружи его не изменить."""
        return dict(self._portfolio)

    @property
    def invested_total(self) -> Decimal:
        return sum(self._portfolio.values(), Decimal("0.00"))

    @property
    def total_value(self) -> Decimal:
        return self._balance + self.invested_total
    
    def invest(self, asset_type: AssetType | str, amount: Any) -> Decimal:
        self._start_operation("Покупка актива")
        asset = AssetType.parse(asset_type)
        value = self._validate_amount(amount)
        self._ensure_funds(value, available=self._balance, hint="свободные средства")
        self._change_balance(-value, f"Покупка актива {asset.value}")
        self._portfolio[asset] += value
        return self._portfolio[asset]

    def sell(self, asset_type: AssetType | str, amount: Any) -> Decimal:
        self._start_operation("Продажа актива")
        asset = AssetType.parse(asset_type)
        value = self._validate_amount(amount)
        held = self._portfolio[asset]
        if value > held:
            raise InsufficientFundsError(
                available=held,
                requested=value,
                currency=self._currency.value,
                hint=f"вложено в {asset.value}",
            )
        self._portfolio[asset] -= value
        self._change_balance(value, f"Продажа актива {asset.value}")
        return self._portfolio[asset]

    def withdraw(self, amount: Decimal | int | float | str) -> Decimal:
        self._start_operation("Снятие")
        value = self._validate_amount(amount)
        self._check_withdrawal_limit(value)
        invested = self.invested_total
        self._ensure_funds(
            value,
            available=self._balance,
            hint=(
                f"снимать можно только свободные средства; в портфеле "
                f"{self._money(invested)} — сначала sell()"
                if invested
                else None
            ),
        )
        self._change_balance(-value, "Снятие")
        return self._balance

    def project_yearly_growth(
        self, growth_rates: Mapping[AssetType | str, Decimal | int | float | str]
    ) -> GrowthProjection:
        rates = self._parse_growth_rates(growth_rates)

        by_asset: dict[AssetType, Decimal] = {}
        for asset, held in self._portfolio.items():
            if held == 0:
                continue
            if asset not in rates:
                raise InvalidOperationError(
                    f"Не указана ставка для {asset.value}: в нём {self._money(held)}"
                )
            by_asset[asset] = (held * rates[asset]).quantize(_CENT, ROUND_HALF_UP)

        total_growth = sum(by_asset.values(), Decimal("0.00"))
        current = self.invested_total
        return GrowthProjection(
            by_asset=by_asset,
            total_growth=total_growth,
            current_value=current,
            projected_value=current + total_growth,
        )

    def _parse_growth_rates(self, growth_rates: Any) -> dict[AssetType, Decimal]:
        if not isinstance(growth_rates, Mapping):
            raise InvalidOperationError(
                "growth_rates должен быть словарём «тип актива → годовая ставка», "
                f"получено {type(growth_rates).__name__}"
            )
        rates: dict[AssetType, Decimal] = {}
        for key, raw_rate in growth_rates.items():
            asset = AssetType.parse(key)
            if asset in rates:
                raise InvalidOperationError(f"Ставка для {asset.value} указана дважды")
            rates[asset] = self._to_rate(
                raw_rate,
                field=f"Ставка {asset.value}",
                minimum=Decimal(-1),  # хуже, чем потерять всё, не бывает (с)
                maximum=Decimal(1),
            )
        return rates

    def _ensure_can_close(self) -> None:
        if self.invested_total:
            raise InvalidOperationError(
                f"Нельзя закрыть счёт {self.masked_number}: в портфеле "
                f"{self._money(self.invested_total)} — сначала продайте активы"
            )

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            portfolio={asset.value: held for asset, held in self._portfolio.items()},
            invested_total=self.invested_total,
            total_value=self.total_value,
        )
        return info

    def __str__(self) -> str:
        holdings = ", ".join(
            f"{asset.value} {self._format_money(held)}"
            for asset, held in self._portfolio.items()
            if held
        ) or "пусто"
        return (
            f"{super().__str__()} | "
            f"Портфель: {holdings} | "
            f"Всего: {self._format_money(self.total_value)} {self._currency.value}"
        )
