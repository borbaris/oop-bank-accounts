from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, ClassVar

from .accounts import _CENT, AccountStatus, BankAccount, Currency
from .exceptions import (
    AccessDeniedError,
    AccountClosedError,
    AccountFrozenError,
    BankError,
    ClientBlockedError,
    InsufficientFundsError,
    TransactionError,
    TransientTransactionError,
    WithdrawalLimitExceededError,
)
from .exchange import DEMO_EXCHANGE_RATES, CurrencyConverter
from .premium import PremiumAccount
from .security import ActivityKind
from .transaction_queue import TransactionQueue
from .transactions import Transaction, TransactionType

if TYPE_CHECKING:
    from .bank_system import Bank

ExternalGateway = Callable[[Transaction], None]


@dataclass(frozen=True, slots=True)
class ProcessingError:
    timestamp: datetime
    transaction_id: str
    attempt: int
    error_type: str
    message: str
    retry_at: datetime | None = None

    def __str__(self) -> str:
        retry = f" повтор в {self.retry_at:%H:%M}" if self.retry_at else " отказ"
        return (
            f"[{self.timestamp:%d.%m %H:%M}] {self.transaction_id} попытка {self.attempt}: "
            f"{self.error_type}: {self.message}{retry}"
        )


class TransactionProcessor:
    EXTERNAL_FEE_RATE: ClassVar[Decimal] = Decimal("0.01")
    MAX_ATTEMPTS: ClassVar[int] = 3
    RETRY_DELAY: ClassVar[timedelta] = timedelta(minutes=5)

    def __init__(
        self,
        bank: Bank,
        *,
        converter: CurrencyConverter | None = None,
        external_gateway: ExternalGateway | None = None,
    ) -> None:
        self._bank = bank
        self._converter = converter or CurrencyConverter(DEMO_EXCHANGE_RATES)
        self._gateway = external_gateway
        self._fee_income: dict[Currency, Decimal] = {}
        self._external_outflow: dict[Currency, Decimal] = {}
        self._errors: list[ProcessingError] = []

    @property
    def fee_income(self) -> dict[Currency, Decimal]:
        return dict(self._fee_income)

    @property
    def external_outflow(self) -> dict[Currency, Decimal]:
        return dict(self._external_outflow)

    @property
    def errors(self) -> tuple[ProcessingError, ...]:
        return tuple(self._errors)

    def calculate_fee(self, tx: Transaction) -> Decimal:
        if tx.type is TransactionType.EXTERNAL_TRANSFER:
            return (tx.amount * self.EXTERNAL_FEE_RATE).quantize(_CENT, ROUND_HALF_UP)
        return Decimal("0.00")

    def process_queue(self, queue: TransactionQueue) -> list[Transaction]:
        handled = []
        while (tx := queue.pop_ready()) is not None:
            self.process(tx)
            if not tx.is_final:
                queue.add(tx)
            handled.append(tx)
        return handled

    def process(self, tx: Transaction) -> Transaction:
        now = self._bank.now()
        if not tx.is_ready(now):
            raise TransactionError(
                f"{tx.transaction_id} нельзя выполнить сейчас: статус {tx.status.value}, "
                f"запланирована на {tx.scheduled_at:%d.%m %H:%M}"
            )
        try:
            self._check_authorization(tx)
            if not self._bank.is_operating_hours(now):
                tx._reschedule(now, self._bank.next_opening(now), "ночью операции запрещены")
                return tx
            tx._start_attempt(now)
            self._execute(tx, now)
        except TransientTransactionError as exc:
            if tx.attempts < self.MAX_ATTEMPTS:
                retry_at = now + self.RETRY_DELAY * tx.attempts
                self._record_error(tx, now, exc, retry_at)
                tx._reschedule(now, retry_at, f"повтор после сбоя: {exc}")
            else:
                self._record_error(tx, now, exc)
                tx._fail(now, f"{exc} (исчерпано попыток: {self.MAX_ATTEMPTS})")
        except BankError as exc:
            self._record_error(tx, now, exc)
            tx._fail(now, str(exc))
        return tx

    def _check_authorization(self, tx: Transaction) -> None:
        if not self._bank.is_authorized_transaction(tx):
            self._bank.flag_suspicious(
                ActivityKind.FORGED_TRANSACTION, tx.initiator_id,
                f"попытка выполнить транзакцию {tx.transaction_id}, созданную не банком",
            )
            raise AccessDeniedError("Транзакция не создана банком")
        initiator = self._bank.get_client(tx.initiator_id)
        if initiator.is_blocked:
            self._bank.flag_suspicious(
                ActivityKind.BLOCKED_CLIENT_OPERATION, tx.initiator_id,
                f"транзакция {tx.transaction_id} заблокированного клиента",
            )
            raise ClientBlockedError(f"Клиент {tx.initiator_id} заблокирован")

    def _execute(self, tx: Transaction, now: datetime) -> None:
        # 1. Найти счета и проверить статусы.
        sender = self._bank.get_account(tx.sender)
        self._ensure_active(sender, "отправителя")
        receiver: BankAccount | None = None
        if tx.type is TransactionType.INTERNAL_TRANSFER:
            receiver = self._bank.get_account(tx.receiver)
            self._ensure_active(receiver, "получателя")

        # 2. Посчитать суммы: комиссия и конвертация.
        fee = self.calculate_fee(tx)
        principal = self._converter.convert(tx.amount, tx.currency, sender.currency)
        fee_charged = self._converter.convert(fee, tx.currency, sender.currency)
        total = principal + fee_charged
        credit = (
            self._converter.convert(tx.amount, tx.currency, receiver.currency)
            if receiver is not None else tx.amount
        )

        # 3. Правила — до того, как тронуть деньги.
        self._check_rules(sender, principal, total)

        # 4. Внешний банк. Может бросить TransientTransactionError — деньги ещё не тронуты.
        if receiver is None and self._gateway is not None:
            self._gateway(tx)

        # 5. Движение денег.
        sender._debit(total)
        if receiver is not None:
            try:
                receiver._credit(credit)
            except BankError:
                sender._credit(total)
                raise
        else:
            self._add(self._external_outflow, sender.currency, principal)
        if fee_charged:
            self._add(self._fee_income, sender.currency, fee_charged)

        tx._complete(now, fee=fee, debit_amount=total, credit_amount=credit)

    def _check_rules(self, sender: BankAccount, principal: Decimal, total: Decimal) -> None:
        if principal > sender.withdrawal_limit:
            raise WithdrawalLimitExceededError(
                limit=sender.withdrawal_limit, requested=principal, currency=sender.currency.value
            )
        if not isinstance(sender, PremiumAccount) and total > sender.balance:
            raise InsufficientFundsError(
                available=sender.balance, requested=total, currency=sender.currency.value,
                hint="уход в минус разрешён только Premium-счетам",
            )
        if total > sender.available_balance:
            raise InsufficientFundsError(
                available=sender.available_balance, requested=total,
                currency=sender.currency.value, hint="с учётом ограничений счёта",
            )

    @staticmethod
    def _ensure_active(account: BankAccount, role: str) -> None:
        if account.status is AccountStatus.FROZEN:
            raise AccountFrozenError(f"Счёт {role} {account.masked_number} заморожен")
        if account.status is AccountStatus.CLOSED:
            raise AccountClosedError(f"Счёт {role} {account.masked_number} закрыт")

    @staticmethod
    def _add(totals: dict[Currency, Decimal], currency: Currency, amount: Decimal) -> None:
        totals[currency] = totals.get(currency, Decimal("0.00")) + amount

    def _record_error(
        self, tx: Transaction, now: datetime, exc: Exception, retry_at: datetime | None = None
    ) -> None:
        self._errors.append(
            ProcessingError(now, tx.transaction_id, tx.attempts, type(exc).__name__, str(exc), retry_at)
        )
