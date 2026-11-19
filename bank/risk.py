from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import IntEnum
from typing import TYPE_CHECKING, ClassVar

from .accounts import Currency
from .exchange import DEMO_EXCHANGE_RATES, CurrencyConverter
from .transactions import Transaction, TransactionStatus

if TYPE_CHECKING:
    from .bank_system import Bank


class RiskLevel(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    transaction_id: str
    client_id: str
    level: RiskLevel
    score: int
    reasons: tuple[str, ...]

    def __str__(self) -> str:
        why = "; ".join(self.reasons) or "нет факторов"
        return f"{self.level.name} ({self.score} б.): {why}"


class RiskAnalyzer:
    LARGE_AMOUNT_RUB: ClassVar[Decimal] = Decimal("100000")
    FREQUENT_COUNT: ClassVar[int] = 5
    FREQUENT_WINDOW: ClassVar[timedelta] = timedelta(minutes=10)

    LARGE_POINTS: ClassVar[int] = 2
    FREQUENT_POINTS: ClassVar[int] = 2
    NEW_RECEIVER_POINTS: ClassVar[int] = 1
    NIGHT_POINTS: ClassVar[int] = 2

    MEDIUM_FROM: ClassVar[int] = 2
    HIGH_FROM: ClassVar[int] = 4

    def __init__(self, bank: Bank, converter: CurrencyConverter | None = None) -> None:
        self._bank = bank
        self._converter = converter or CurrencyConverter(DEMO_EXCHANGE_RATES)

    def assess(self, tx: Transaction) -> RiskAssessment:
        factors = [
            self._large_amount(tx),
            self._frequent(tx),
            self._new_receiver(tx),
            self._night(tx),
        ]
        found = [f for f in factors if f is not None]
        score = sum(points for points, _ in found)
        return RiskAssessment(
            transaction_id=tx.transaction_id,
            client_id=tx.initiator_id,
            level=self._level(score),
            score=score,
            reasons=tuple(reason for _, reason in found),
        )

    def _level(self, score: int) -> RiskLevel:
        if score >= self.HIGH_FROM:
            return RiskLevel.HIGH
        if score >= self.MEDIUM_FROM:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _large_amount(self, tx: Transaction) -> tuple[int, str] | None:
        rub = self._converter.convert(tx.amount, tx.currency, Currency.RUB)
        if rub >= self.LARGE_AMOUNT_RUB:
            return self.LARGE_POINTS, f"крупная сумма ({rub} RUB ≥ {self.LARGE_AMOUNT_RUB})"
        return None

    def _frequent(self, tx: Transaction) -> tuple[int, str] | None:
        window_start = tx.created_at - self.FREQUENT_WINDOW
        recent = [
            t for t in self._bank.transactions
            if t.initiator_id == tx.initiator_id and window_start < t.created_at <= tx.created_at
        ]
        if len(recent) >= self.FREQUENT_COUNT:
            minutes = int(self.FREQUENT_WINDOW.total_seconds() // 60)
            return self.FREQUENT_POINTS, f"частые операции ({len(recent)} за {minutes} мин)"
        return None

    def _new_receiver(self, tx: Transaction) -> tuple[int, str] | None:
        own_accounts = self._bank.get_client(tx.initiator_id).account_numbers
        if tx.receiver in own_accounts:
            return None
        paid_before = any(
            t.initiator_id == tx.initiator_id
            and t.receiver == tx.receiver
            and t.status is TransactionStatus.COMPLETED
            for t in self._bank.transactions
        )
        if not paid_before:
            return self.NEW_RECEIVER_POINTS, f"новый получатель ****{tx.receiver[-4:]}"
        return None

    def _night(self, tx: Transaction) -> tuple[int, str] | None:
        if not self._bank.is_operating_hours(tx.scheduled_at):
            return self.NIGHT_POINTS, f"назначено на ночь ({tx.scheduled_at:%H:%M})"
        return None
