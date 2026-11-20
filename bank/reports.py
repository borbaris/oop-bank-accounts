"""Отчёты аудита. Это функции, а не класс: у отчёта нет своего состояния —
он берёт данные банка и считает."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from .accounts import Currency
from .audit import AuditLevel
from .exchange import DEMO_EXCHANGE_RATES, CurrencyConverter
from .risk import RiskAssessment, RiskLevel
from .security import ActivityKind
from .transactions import Transaction, TransactionStatus, TransactionType

if TYPE_CHECKING:
    from .bank_system import Bank
    from .client import Client
    from .processor import TransactionProcessor

_ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class ClientRiskProfile:
    client_id: str
    full_name: str
    level: RiskLevel
    transactions: int
    by_risk: dict[RiskLevel, int]
    blocked_operations: int
    failed_logins: int
    suspicious_events: int
    is_blocked: bool


@dataclass(frozen=True, slots=True)
class ErrorStatistics:
    total_transactions: int
    completed: int
    failed: int
    cancelled: int
    failure_rate: Decimal
    errors_by_type: dict[str, int]
    audit_by_level: dict[AuditLevel, int]


def suspicious_operations(bank: Bank) -> list[tuple[Transaction, RiskAssessment]]:
    found = [
        (bank.get_transaction(a.transaction_id), a)
        for a in bank.risk_assessments
        if a.level >= RiskLevel.MEDIUM
    ]
    return sorted(found, key=lambda pair: -pair[1].score)


def client_risk_profile(bank: Bank, client_id: str) -> ClientRiskProfile:
    client = bank.get_client(client_id)
    assessments = [a for a in bank.risk_assessments if a.client_id == client_id]
    events = bank.get_suspicious_activities(client_id)

    by_risk = Counter(a.level for a in assessments)
    blocked = by_risk[RiskLevel.HIGH]
    if blocked or client.is_blocked:
        level = RiskLevel.HIGH
    elif by_risk[RiskLevel.MEDIUM] or events:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    return ClientRiskProfile(
        client_id=client_id,
        full_name=client.full_name,
        level=level,
        transactions=len(assessments),
        by_risk={lvl: by_risk[lvl] for lvl in RiskLevel},
        blocked_operations=blocked,
        failed_logins=sum(e.kind is ActivityKind.FAILED_LOGIN for e in events),
        suspicious_events=len(events),
        is_blocked=client.is_blocked,
    )


def error_statistics(bank: Bank, processor: TransactionProcessor) -> ErrorStatistics:
    statuses = Counter(tx.status for tx in bank.transactions)
    total = len(bank.transactions)
    failed = statuses[TransactionStatus.FAILED]
    rate = (Decimal(failed * 100) / total).quantize(Decimal("0.1"), ROUND_HALF_UP) if total else Decimal("0.0")

    errors = Counter(e.error_type for e in processor.errors)
    risk_blocked = sum(a.level is RiskLevel.HIGH for a in bank.risk_assessments)
    if risk_blocked:
        errors["HighRiskBlocked"] = risk_blocked

    levels = Counter(e.level for e in bank.audit.entries)
    return ErrorStatistics(
        total_transactions=total,
        completed=statuses[TransactionStatus.COMPLETED],
        failed=failed,
        cancelled=statuses[TransactionStatus.CANCELLED],
        failure_rate=rate,
        errors_by_type=dict(errors.most_common()),
        audit_by_level={lvl: levels[lvl] for lvl in AuditLevel},
    )


@dataclass(frozen=True, slots=True)
class ClientWealth:
    client: Client
    by_currency: dict[Currency, Decimal]
    total_rub: Decimal


def top_clients(
    bank: Bank, limit: int = 3, *, converter: CurrencyConverter | None = None
) -> list[ClientWealth]:
    converter = converter or CurrencyConverter(DEMO_EXCHANGE_RATES)
    holdings: dict[str, dict[Currency, Decimal]] = {c.client_id: {} for c in bank.clients}
    for currency in Currency:
        for client, total in bank.get_clients_ranking(currency):
            holdings[client.client_id][currency] = total

    wealth = [
        ClientWealth(
            client=client,
            by_currency=holdings[client.client_id],
            total_rub=sum(
                (converter.convert(v, c, Currency.RUB) for c, v in holdings[client.client_id].items()),
                _ZERO,
            ),
        )
        for client in bank.clients
    ]
    wealth.sort(key=lambda w: (-w.total_rub, w.client.full_name))
    return wealth[:limit]


def total_balance(
    bank: Bank, *, converter: CurrencyConverter | None = None
) -> tuple[dict[Currency, Decimal], Decimal]:
    converter = converter or CurrencyConverter(DEMO_EXCHANGE_RATES)
    per_currency = bank.get_total_balance()
    in_rub = sum((converter.convert(v, c, Currency.RUB) for c, v in per_currency.items()), _ZERO)
    return per_currency, in_rub


@dataclass(frozen=True, slots=True)
class TransactionStatistics:
    total: int
    by_status: dict[TransactionStatus, int]
    by_type: dict[TransactionType, int]
    by_risk: dict[RiskLevel, int]
    completed_volume_rub: Decimal
    average_completed_rub: Decimal
    largest_completed: Transaction | None
    retried: int
    fee_income: dict[Currency, Decimal]
    external_outflow: dict[Currency, Decimal]


def transaction_statistics(
    bank: Bank, processor: TransactionProcessor, *, converter: CurrencyConverter | None = None
) -> TransactionStatistics:
    converter = converter or CurrencyConverter(DEMO_EXCHANGE_RATES)
    txs = bank.transactions
    completed = [tx for tx in txs if tx.status is TransactionStatus.COMPLETED]
    in_rub = {tx.transaction_id: converter.convert(tx.amount, tx.currency, Currency.RUB) for tx in completed}
    volume = sum(in_rub.values(), _ZERO)
    average = (volume / len(completed)).quantize(Decimal("0.01"), ROUND_HALF_UP) if completed else _ZERO

    statuses = Counter(tx.status for tx in txs)
    types = Counter(tx.type for tx in txs)
    risks = Counter(a.level for a in bank.risk_assessments)
    return TransactionStatistics(
        total=len(txs),
        by_status={s: statuses[s] for s in TransactionStatus},
        by_type={t: types[t] for t in TransactionType},
        by_risk={r: risks[r] for r in RiskLevel},
        completed_volume_rub=volume,
        average_completed_rub=average,
        largest_completed=max(completed, key=lambda tx: in_rub[tx.transaction_id], default=None),
        retried=sum(tx.attempts > 1 for tx in txs),
        fee_income=processor.fee_income,
        external_outflow=processor.external_outflow,
    )


class Direction(StrEnum):
    OUTGOING = "out"
    INCOMING = "in"
    OWN = "own"


@dataclass(frozen=True, slots=True)
class HistoryItem:
    transaction: Transaction
    direction: Direction


def client_history(bank: Bank, client_id: str) -> list[HistoryItem]:
    client = bank.get_client(client_id)
    own = set(client.account_numbers)
    history = []
    for tx in bank.transactions:
        sent = tx.initiator_id == client_id
        received = tx.receiver in own
        if sent and received:
            history.append(HistoryItem(tx, Direction.OWN))
        elif sent:
            history.append(HistoryItem(tx, Direction.OUTGOING))
        elif received and tx.status is TransactionStatus.COMPLETED:
            history.append(HistoryItem(tx, Direction.INCOMING))
    history.sort(key=lambda item: item.transaction.created_at)
    return history


def reconcile(bank: Bank, opening: dict[Currency, Decimal]) -> dict[Currency, tuple[Decimal, Decimal]]:
    expected = dict(opening)
    for tx in bank.transactions:
        if tx.status is not TransactionStatus.COMPLETED:
            continue
        sender_currency = bank.get_account(tx.sender).currency
        expected[sender_currency] = expected.get(sender_currency, _ZERO) - tx.debit_amount
        if tx.type is TransactionType.INTERNAL_TRANSFER:
            receiver_currency = bank.get_account(tx.receiver).currency
            expected[receiver_currency] = expected.get(receiver_currency, _ZERO) + tx.credit_amount

    actual = bank.get_total_balance()
    return {
        currency: (expected.get(currency, _ZERO), actual.get(currency, _ZERO))
        for currency in Currency
        if currency in expected or currency in actual
    }
