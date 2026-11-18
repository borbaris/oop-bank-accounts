from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .accounts import _CENT, Currency

DEMO_EXCHANGE_RATES: dict[str, str] = {
    "RUB": "1",
    "USD": "90",
    "EUR": "100",
    "KZT": "0.18",
    "CNY": "12.5",
}


class CurrencyConverter:
    def __init__(self, rates_to_rub: Mapping[Currency | str, Decimal | int | str]) -> None:
        self._rates: dict[Currency, Decimal] = {}
        for code, raw in rates_to_rub.items():
            currency = Currency.parse(code)
            try:
                rate = Decimal(str(raw))
            except InvalidOperation:
                raise ValueError(f"Курс {currency}: не число — {raw!r}") from None
            if not rate.is_finite() or rate <= 0:
                raise ValueError(f"Курс {currency} должен быть положительным, получено {raw!r}")
            self._rates[currency] = rate

        missing = [c.value for c in Currency if c not in self._rates]
        if missing:
            raise ValueError(f"Нет курса для валют: {', '.join(missing)}")

    def rate(self, source: Currency | str, target: Currency | str) -> Decimal:
        """Сколько единиц target дают за 1 единицу source."""
        return self._rates[Currency.parse(source)] / self._rates[Currency.parse(target)]

    def convert(self, amount: Decimal, source: Currency | str, target: Currency | str) -> Decimal:
        source, target = Currency.parse(source), Currency.parse(target)
        if source is target:
            return amount
        rub = amount * self._rates[source]
        return (rub / self._rates[target]).quantize(_CENT, ROUND_HALF_UP)
