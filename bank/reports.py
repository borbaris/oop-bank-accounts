"""Отчёты аудита. Это функции, а не класс: у отчёта нет своего состояния —
он берёт данные банка и считает."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from .audit import AuditLevel
from .risk import RiskAssessment, RiskLevel
from .security import ActivityKind
from .transactions import Transaction, TransactionStatus

if TYPE_CHECKING:
    from .bank_system import Bank
    from .processor import TransactionProcessor


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
