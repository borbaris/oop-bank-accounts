from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from .exceptions import AccessDeniedError, TransactionError, TransactionNotFoundError
from .security import ActivityKind
from .transactions import Transaction, TransactionStatus

if TYPE_CHECKING:
    from .bank_system import Bank


class TransactionQueue:
    def __init__(self, bank: Bank) -> None:
        self._bank = bank
        self._items: dict[str, tuple[int, Transaction]] = {}
        self._counter = itertools.count()

    def add(self, tx: Transaction) -> Transaction:
        if not self._bank.is_authorized_transaction(tx):
            self._bank.flag_suspicious(
                ActivityKind.FORGED_TRANSACTION, getattr(tx, "initiator_id", None),
                "попытка добавить в очередь транзакцию, созданную не банком",
            )
            raise AccessDeniedError("Транзакция не создана банком — используйте Bank.create_transfer()")
        if tx.status is not TransactionStatus.PENDING:
            raise TransactionError(f"В очередь можно добавить только pending, а не {tx.status.value}")
        if tx.transaction_id in self._items:
            raise TransactionError(f"Транзакция {tx.transaction_id} уже в очереди")
        self._items[tx.transaction_id] = (next(self._counter), tx)
        return tx

    def cancel(
        self, client_id: str, transaction_id: str, reason: str = "отменена клиентом"
    ) -> Transaction:
        self._bank.require_session(client_id, "Отмена транзакции")
        tx = self.get(transaction_id)
        if tx.initiator_id != client_id:
            self._bank.flag_suspicious(
                ActivityKind.FOREIGN_TRANSACTION_CANCEL, client_id,
                f"попытка отменить чужую транзакцию {transaction_id}",
            )
            raise AccessDeniedError("Отменить можно только свою транзакцию")
        tx._cancel(self._bank.now(), reason)
        del self._items[transaction_id]
        return tx

    def get(self, transaction_id: str) -> Transaction:
        item = self._items.get(transaction_id)
        if item is None:
            raise TransactionNotFoundError(f"Транзакции {transaction_id!r} нет в очереди")
        return item[1]

    def pop_ready(self) -> Transaction | None:
        self._drop_finished()
        now = self._bank.now()
        ready = [item for item in self._items.values() if item[1].is_ready(now)]
        if not ready:
            return None
        _, tx = min(ready, key=self._order)
        del self._items[tx.transaction_id]
        return tx

    def ready(self) -> list[Transaction]:
        now = self._bank.now()
        items = sorted((i for i in self._items.values() if i[1].is_ready(now)), key=self._order)
        return [tx for _, tx in items]

    def deferred(self) -> list[Transaction]:
        now = self._bank.now()
        waiting = [tx for _, tx in self._items.values() if not tx.is_ready(now)]
        return sorted(waiting, key=lambda tx: tx.scheduled_at)

    def _drop_finished(self) -> None:
        for tx_id in [i for i, (_, tx) in self._items.items() if tx.is_final]:
            del self._items[tx_id]

    @staticmethod
    def _order(item: tuple[int, Transaction]) -> tuple[int, object, int]:
        number, tx = item
        return (-tx.priority, tx.scheduled_at, number)

    def __len__(self) -> int:
        self._drop_finished()
        return len(self._items)

    def __contains__(self, tx: object) -> bool:
        return isinstance(tx, Transaction) and tx.transaction_id in self._items
