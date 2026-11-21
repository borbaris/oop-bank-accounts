from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from .accounts import AccountStatus, BankAccount, Currency, normalize_account_number
from .audit import AuditLevel, AuditLog
from .client import Client
from .exceptions import (
    AccessDeniedError,
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    ClientError,
    ClientNotFoundError,
    InvalidOperationError,
    OperatingHoursError,
    TransactionError,
    TransactionNotFoundError,
)
from .investment import InvestmentAccount
from .premium import PremiumAccount
from .risk import RiskAnalyzer, RiskAssessment, RiskLevel
from .savings import SavingsAccount
from .security import (
    PBKDF2_ITERATIONS,
    ActivityKind,
    SuspiciousActivity,
    hash_password,
    validate_password,
    verify_password,
)
from .transactions import Transaction, TransactionPriority, TransactionType


class Bank:
    MAX_LOGIN_ATTEMPTS: ClassVar[int] = 3
    NIGHT_START: ClassVar[time] = time(0, 0)
    NIGHT_END: ClassVar[time] = time(5, 0)
    PASSWORD_ITERATIONS: ClassVar[int] = PBKDF2_ITERATIONS

    ACCOUNT_TYPES: ClassVar[dict[str, type[BankAccount]]] = {
        "bank": BankAccount,
        "savings": SavingsAccount,
        "premium": PremiumAccount,
        "investment": InvestmentAccount,
    }

    RISK_ANALYZER: ClassVar[type[RiskAnalyzer]] = RiskAnalyzer
    # Самые опасные события — CRITICAL в аудите, остальные подозрительные — WARNING.
    CRITICAL_ACTIVITIES: ClassVar[frozenset[ActivityKind]] = frozenset({
        ActivityKind.CLIENT_BLOCKED,
        ActivityKind.FORGED_TRANSACTION,
        ActivityKind.HIGH_RISK_OPERATION,
    })
    RISK_TO_AUDIT: ClassVar[dict[RiskLevel, AuditLevel]] = {
        RiskLevel.LOW: AuditLevel.INFO,
        RiskLevel.MEDIUM: AuditLevel.WARNING,
        RiskLevel.HIGH: AuditLevel.CRITICAL,
    }

    def __init__(
        self,
        name: str,
        *,
        clock: Callable[[], datetime] = datetime.now,
        audit_path: str | Path | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Название банка должно быть непустой строкой")
        self._name = name.strip()
        self._clock = clock

        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        self._account_owner: dict[str, str] = {}
        self._credentials: dict[str, tuple[bytes, bytes]] = {}
        self._failed_logins: dict[str, int] = {}
        self._sessions: set[str] = set()
        self._suspicious: list[SuspiciousActivity] = []
        self._transactions: dict[str, Transaction] = {}
        self._audit = AuditLog(audit_path, clock=clock)
        self._risk = self.RISK_ANALYZER(self)
        self._assessments: dict[str, RiskAssessment] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def audit(self) -> AuditLog:
        return self._audit

    @property
    def risk_assessments(self) -> tuple[RiskAssessment, ...]:
        return tuple(self._assessments.values())

    def get_risk_assessment(self, transaction_id: str) -> RiskAssessment:
        assessment = self._assessments.get(transaction_id)
        if assessment is None:
            raise TransactionNotFoundError(f"Нет оценки риска для {transaction_id!r}")
        return assessment

    def now(self) -> datetime:
        return self._clock()

    def is_operating_hours(self, at: datetime | None = None) -> bool:
        at = at or self._clock()
        return not (self.NIGHT_START <= at.time() < self.NIGHT_END)

    def next_opening(self, at: datetime | None = None) -> datetime:
        at = at or self._clock()
        opening = datetime.combine(at.date(), self.NIGHT_END)
        return opening if at < opening else opening + timedelta(days=1)

    def add_client(self, client: Client, password: str) -> Client:
        if not isinstance(client, Client):
            raise TypeError(f"Ожидался Client, получено {type(client).__name__}")
        if client.client_id in self._clients:
            raise ClientError(f"Клиент с ID {client.client_id} уже есть в банке")
        validate_password(password)

        self._credentials[client.client_id] = hash_password(
            password, iterations=self.PASSWORD_ITERATIONS
        )
        self._clients[client.client_id] = client
        self._failed_logins[client.client_id] = 0
        return client

    @property
    def clients(self) -> tuple[Client, ...]:
        return tuple(self._clients.values())

    def get_client(self, client_id: str) -> Client:
        client = self._clients.get(client_id)
        if client is None:
            raise ClientNotFoundError(f"Клиент {client_id!r} не найден")
        return client

    def unblock_client(self, client_id: str) -> None:
        client = self.get_client(client_id)
        client.unblock()
        self._failed_logins[client_id] = 0

    def authenticate_client(self, client_id: str, password: str) -> Client:
        if not isinstance(password, str):
            raise TypeError(f"Пароль должен быть строкой, получено {type(password).__name__}")
        client = self._clients.get(client_id)
        if client is None:
            self._flag(ActivityKind.UNKNOWN_CLIENT_LOGIN, client_id, "вход с несуществующим ID")
            raise AuthenticationError("Неверный ID клиента или пароль")

        if client.is_blocked:
            self._flag(ActivityKind.BLOCKED_CLIENT_LOGIN, client_id, "вход в заблокированный профиль")
            raise ClientBlockedError(f"Клиент {client_id} заблокирован — обратитесь в банк")

        salt, expected = self._credentials[client_id]
        if verify_password(password, salt, expected, self.PASSWORD_ITERATIONS):
            self._failed_logins[client_id] = 0
            self._sessions.add(client_id)
            return client

        self._failed_logins[client_id] += 1
        attempts = self._failed_logins[client_id]
        self._flag(
            ActivityKind.FAILED_LOGIN, client_id,
            f"неверный пароль, попытка {attempts}/{self.MAX_LOGIN_ATTEMPTS}",
        )
        if attempts >= self.MAX_LOGIN_ATTEMPTS:
            client.block()
            self._sessions.discard(client_id)
            self._flag(ActivityKind.CLIENT_BLOCKED, client_id, f"{attempts} неверных пароля подряд")
            raise ClientBlockedError(
                f"{attempts} неверных попытки — клиент {client_id} заблокирован"
            )
        raise AuthenticationError(
            f"Неверный ID клиента или пароль. Осталось попыток: "
            f"{self.MAX_LOGIN_ATTEMPTS - attempts}"
        )

    def logout(self, client_id: str) -> None:
        self._sessions.discard(client_id)

    def is_authenticated(self, client_id: str) -> bool:
        return client_id in self._sessions

    def open_account(
        self, client_id: str, account_type: str = "bank", **options: Any
    ) -> BankAccount:
        self._authorize(client_id, "Открытие счёта")
        client = self.get_client(client_id)
        account_class = self._resolve_account_type(account_type)

        account = account_class(client.owner, **options)
        if account.account_number in self._accounts:
            raise InvalidOperationError(f"Счёт {account.account_number} уже существует")

        account.attach_security(
            lambda action, by_client: self._authorize(client_id, action, by_client=by_client),
            clock=self._clock,
        )
        self._accounts[account.account_number] = account
        self._account_owner[account.account_number] = client_id
        client.add_account_number(account.account_number)
        return account

    def close_account(
        self, client_id: str, account_number: str, *, transfer_to: str | None = None
    ) -> Decimal:
        self._authorize(client_id, "Закрытие счёта")
        account = self._get_owned_account(client_id, account_number)

        if transfer_to is None:
            if account.balance > 0:
                raise InvalidOperationError(
                    f"На счёте {account.masked_number} остаток {account.balance} "
                    f"{account.currency} — укажите transfer_to=<номер вашего счёта>"
                )
            account.close()
            return Decimal("0.00")

        target = self._get_owned_account(client_id, transfer_to)
        if target is account:
            raise InvalidOperationError("Нельзя перевести остаток на закрываемый счёт")
        if target.currency is not account.currency:
            raise InvalidOperationError(
                f"Валюта счёта {target.masked_number} ({target.currency}) не совпадает "
                f"с закрываемым ({account.currency})"
            )
        if target.status is not AccountStatus.ACTIVE:
            raise InvalidOperationError(f"Счёт {target.masked_number} не активен")

        payout = account.close_with_payout()
        if payout > 0:
            target.deposit(payout)
        return payout

    def freeze_account(self, account_number: str) -> None:
        self.get_account(account_number).freeze()

    def unfreeze_account(self, account_number: str) -> None:
        self.get_account(account_number).unfreeze()

    def get_account(self, account_number: str) -> BankAccount:
        account = self._accounts.get(self._normalize_number(account_number))
        if account is None:
            raise AccountNotFoundError(f"Счёт {account_number!r} не найден")
        return account

    def search_accounts(
        self,
        *,
        client_id: str | None = None,
        account_type: str | type[BankAccount] | None = None,
        status: AccountStatus | str | None = None,
        currency: Currency | str | None = None,
        min_balance: Decimal | int | float | str | None = None,
        max_balance: Decimal | int | float | str | None = None,
    ) -> list[BankAccount]:
        if client_id is not None:
            self.get_client(client_id)
        wanted_type = None if account_type is None else self._resolve_account_type(account_type)
        wanted_status = None if status is None else AccountStatus.parse(status)
        wanted_currency = None if currency is None else Currency.parse(currency)
        low = None if min_balance is None else Decimal(str(min_balance))
        high = None if max_balance is None else Decimal(str(max_balance))

        found = []
        for number, account in self._accounts.items():
            if client_id is not None and self._account_owner[number] != client_id:
                continue
            if wanted_type is not None and type(account) is not wanted_type:
                continue
            if wanted_status is not None and account.status is not wanted_status:
                continue
            if wanted_currency is not None and account.currency is not wanted_currency:
                continue
            if low is not None and account.balance < low:
                continue
            if high is not None and account.balance > high:
                continue
            found.append(account)
        return found

    def get_total_balance(self) -> dict[Currency, Decimal]:
        totals: dict[Currency, Decimal] = {}
        for account in self._accounts.values():
            totals[account.currency] = (
                totals.get(account.currency, Decimal("0.00")) + account.total_value
            )
        return totals

    def get_clients_ranking(
        self, currency: Currency | str = Currency.RUB
    ) -> list[tuple[Client, Decimal]]:
        wanted = Currency.parse(currency)
        totals: dict[str, Decimal] = {}
        for number, account in self._accounts.items():
            if account.currency is not wanted:
                continue
            owner_id = self._account_owner[number]
            totals[owner_id] = totals.get(owner_id, Decimal("0.00")) + account.total_value

        ranking = [(self._clients[cid], total) for cid, total in totals.items()]
        ranking.sort(key=lambda item: (-item[1], item[0].full_name))
        return ranking

    @property
    def suspicious_activities(self) -> tuple[SuspiciousActivity, ...]:
        return tuple(self._suspicious)

    def get_suspicious_activities(self, client_id: str | None = None) -> list[SuspiciousActivity]:
        return [a for a in self._suspicious if client_id is None or a.client_id == client_id]

    def flag_suspicious(self, kind: ActivityKind, client_id: str | None, details: str) -> None:
        self._flag(kind, client_id, details)

    def create_transfer(
        self,
        client_id: str,
        sender: str,
        receiver: str,
        amount: Decimal | int | float | str,
        *,
        currency: Currency | str | None = None,
        priority: TransactionPriority | str = TransactionPriority.NORMAL,
        scheduled_at: datetime | None = None,
        description: str = "",
    ) -> Transaction:
        self._authorize(client_id, "Создание перевода")
        source = self._get_owned_account(client_id, sender)
        target = normalize_account_number(receiver)
        tx_type = (
            TransactionType.INTERNAL_TRANSFER if target in self._accounts
            else TransactionType.EXTERNAL_TRANSFER
        )
        now = self._clock()
        if scheduled_at is not None and scheduled_at < now:
            raise TransactionError(f"Нельзя запланировать перевод в прошлое ({scheduled_at})")

        tx = Transaction(
            tx_type, amount, currency or source.currency,
            sender=source.account_number, receiver=target, initiator_id=client_id,
            created_at=now, priority=priority, scheduled_at=scheduled_at,
            description=description,
        )
        self._transactions[tx.transaction_id] = tx
        self._check_risk(tx)
        return tx

    def _check_risk(self, tx: Transaction) -> None:
        """Каждая транзакция оценивается при создании. HIGH — сразу failed, деньги не тронуты."""
        assessment = self._risk.assess(tx)
        self._assessments[tx.transaction_id] = assessment
        self._audit.log(
            self.RISK_TO_AUDIT[assessment.level], "risk_assessment", str(assessment),
            client_id=tx.initiator_id, transaction_id=tx.transaction_id,
        )
        if assessment.level is RiskLevel.HIGH:
            reason = f"заблокировано риск-анализом: {'; '.join(assessment.reasons)}"
            tx._fail(self._clock(), reason)
            self._flag(ActivityKind.HIGH_RISK_OPERATION, tx.initiator_id,
                       f"{tx.transaction_id}: {reason}")

    def is_authorized_transaction(self, tx: Transaction) -> bool:
        return self._transactions.get(getattr(tx, "transaction_id", None)) is tx

    def get_transaction(self, transaction_id: str) -> Transaction:
        tx = self._transactions.get(transaction_id)
        if tx is None:
            raise TransactionNotFoundError(f"Транзакция {transaction_id!r} не найдена")
        return tx

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._transactions.values())

    def require_session(self, client_id: str, action: str = "операция") -> Client:
        return self._require_session(client_id, action)

    def _flag(self, kind: ActivityKind, client_id: str | None, details: str) -> None:
        self._suspicious.append(SuspiciousActivity(self._clock(), kind, client_id, details))
        level = AuditLevel.CRITICAL if kind in self.CRITICAL_ACTIVITIES else AuditLevel.WARNING
        self._audit.log(level, kind.value, details, client_id=client_id)

    def _ensure_operating_hours(self, action: str, client_id: str | None) -> None:
        now = self._clock()
        if not self.is_operating_hours(now):
            self._flag(ActivityKind.NIGHT_OPERATION, client_id, f"{action} в {now:%H:%M}")
            raise OperatingHoursError(
                f"{action}: операции недоступны с 00:00 до 05:00 (сейчас {now:%H:%M})"
            )

    def _authorize(self, client_id: str, action: str, *, by_client: bool = True) -> None:
        self._ensure_operating_hours(action, client_id)
        if by_client:
            self._require_session(client_id, action)

    def _require_session(self, client_id: str, action: str = "операция") -> Client:
        client = self.get_client(client_id)
        if client.is_blocked:
            self._flag(ActivityKind.BLOCKED_CLIENT_OPERATION, client_id, f"{action}: клиент заблокирован")
            raise ClientBlockedError(f"Клиент {client_id} заблокирован")
        if client_id not in self._sessions:
            self._flag(ActivityKind.UNAUTHENTICATED_OPERATION, client_id, f"{action} без входа")
            raise AccessDeniedError("Сначала войдите: authenticate_client()")
        return client

    def _get_owned_account(self, client_id: str, account_number: str) -> BankAccount:
        account = self.get_account(account_number)
        if self._account_owner[account.account_number] != client_id:
            self._flag(
                ActivityKind.FOREIGN_ACCOUNT_ACCESS, client_id,
                f"попытка доступа к чужому счёту {account.masked_number}",
            )
            raise AccessDeniedError(f"Счёт {account.masked_number} принадлежит другому клиенту")
        return account

    def _resolve_account_type(self, account_type: str | type[BankAccount]) -> type[BankAccount]:
        if isinstance(account_type, type) and account_type in self.ACCOUNT_TYPES.values():
            return account_type
        if isinstance(account_type, str) and account_type.strip().lower() in self.ACCOUNT_TYPES:
            return self.ACCOUNT_TYPES[account_type.strip().lower()]
        allowed = ", ".join(self.ACCOUNT_TYPES)
        raise InvalidOperationError(f"Неизвестный тип счёта {account_type!r}. Допустимые: {allowed}")

    @staticmethod
    def _normalize_number(account_number: Any) -> str:
        if not isinstance(account_number, str):
            raise TypeError(
                f"Номер счёта должен быть строкой, получено {type(account_number).__name__}"
            )
        return "".join(account_number.split()).upper()

    def __str__(self) -> str:
        return (
            f"Bank «{self._name}» | Клиентов: {len(self._clients)} | "
            f"Счетов: {len(self._accounts)} | Подозрительных событий: {len(self._suspicious)}"
        )
