"""Тесты дня 4. Запуск: python3 -m unittest discover -s tests -v"""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from bank import (
    AccessDeniedError,
    ActivityKind,
    Bank,
    Currency,
    OperatingHoursError,
    Transaction,
    TransactionError,
    TransactionNotFoundError,
    TransactionPriority,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
    TransientTransactionError,
    AccountFrozenError,
    Client,
    InvalidOperationError,
)

D = Decimal
DAY = datetime(2026, 11, 16, 10, 0)
EXTERNAL = "DE89370400440532013000"  # счёт в другом банке


class Clock:
    def __init__(self, now: datetime = DAY) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FastBank(Bank):
    PASSWORD_ITERATIONS = 1_000


class FlakyGateway:
    """Внешний банк, который первые `failures` раз не отвечает."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    def __call__(self, tx: Transaction) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientTransactionError("внешний банк не ответил")


class Day4TestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.bank = FastBank("Test Bank", clock=self.clock)
        for name, cid, pwd in (("Анна Котова", "CL-ANNA", "anna-pass"),
                               ("Олег Ли", "CL-OLEG", "oleg-pass")):
            self.bank.add_client(Client(name, "1990-01-01", phone="+79991112233", client_id=cid), pwd)
            self.bank.authenticate_client(cid, pwd)

        self.a_rub = self.bank.open_account("CL-ANNA", balance=10_000)
        self.a_usd = self.bank.open_account("CL-ANNA", balance=1_000, currency="USD")
        self.a_sav = self.bank.open_account("CL-ANNA", "savings", balance=10_000, min_balance=2_000)
        self.o_prem = self.bank.open_account("CL-OLEG", "premium", balance=1_000)
        self.o_rub = self.bank.open_account("CL-OLEG")

        self.gateway = FlakyGateway()
        self.queue = TransactionQueue(self.bank)
        self.processor = TransactionProcessor(self.bank, external_gateway=self.gateway)

    def transfer(self, sender, receiver, amount, client="CL-ANNA", **kwargs):
        number = receiver if isinstance(receiver, str) else receiver.account_number
        return self.bank.create_transfer(client, sender.account_number, number, amount, **kwargs)

    def run_one(self, tx):
        self.queue.add(tx)
        self.processor.process_queue(self.queue)
        return tx

    def money(self, currency="RUB"):
        """Инвариант: счета + комиссии банка + ушедшее наружу."""
        cur = Currency(currency)
        on_accounts = sum(
            (a.balance for a in self.bank.search_accounts(currency=cur)), D("0.00")
        )
        return (on_accounts
                + self.processor.fee_income.get(cur, D("0.00"))
                + self.processor.external_outflow.get(cur, D("0.00")))

    def kinds(self):
        return [a.kind for a in self.bank.suspicious_activities]


class TransactionModelTests(Day4TestCase):
    def test_fields(self):
        tx = self.transfer(self.a_rub, self.o_rub, 500, description="за обед")
        self.assertRegex(tx.transaction_id, r"^TX-[0-9A-F]{10}$")
        self.assertIs(tx.type, TransactionType.INTERNAL_TRANSFER)
        self.assertEqual((tx.amount, tx.currency), (D("500.00"), Currency.RUB))
        self.assertEqual((tx.sender, tx.receiver), (self.a_rub.account_number, self.o_rub.account_number))
        self.assertEqual(tx.initiator_id, "CL-ANNA")
        self.assertIs(tx.status, TransactionStatus.PENDING)
        self.assertIs(tx.priority, TransactionPriority.NORMAL)
        self.assertEqual(tx.fee, D("0.00"))
        self.assertIsNone(tx.failure_reason)
        self.assertEqual((tx.created_at, tx.scheduled_at, tx.finished_at), (DAY, DAY, None))
        self.assertEqual(tx.to_dict()["status"], "pending")

    def test_external_type_by_receiver(self):
        tx = self.transfer(self.a_rub, EXTERNAL, 100)
        self.assertIs(tx.type, TransactionType.EXTERNAL_TRANSFER)
        self.assertEqual(tx.receiver, EXTERNAL)

    def test_order_data_is_read_only(self):
        tx = self.transfer(self.a_rub, self.o_rub, 500)
        for field in ("amount", "sender", "receiver", "currency", "status"):
            with self.subTest(field=field), self.assertRaises(AttributeError):
                setattr(tx, field, None)

    def test_validation(self):
        for amount in (0, -5, "1.001", "abc"):
            with self.subTest(amount=amount), self.assertRaises(InvalidOperationError):
                self.transfer(self.a_rub, self.o_rub, amount)
        with self.assertRaises(TransactionError):
            self.transfer(self.a_rub, self.a_rub, 10)
        with self.assertRaises(ValueError):
            self.transfer(self.a_rub, "плохой номер", 10)
        with self.assertRaises(ValueError):
            self.transfer(self.a_rub, self.o_rub, 10, priority="super")
        with self.assertRaises(TransactionError):
            self.transfer(self.a_rub, self.o_rub, 10, scheduled_at=DAY - timedelta(minutes=1))

    def test_creation_is_protected(self):
        with self.assertRaises(AccessDeniedError):  # чужой счёт
            self.transfer(self.o_prem, self.a_rub, 100)
        self.bank.logout("CL-ANNA")
        with self.assertRaises(AccessDeniedError):  # без входа
            self.transfer(self.a_rub, self.o_rub, 100)
        self.bank.authenticate_client("CL-ANNA", "anna-pass")
        self.clock.now = datetime(2026, 11, 17, 3, 0)
        with self.assertRaises(OperatingHoursError):  # ночью
            self.transfer(self.a_rub, self.o_rub, 100)


class QueueTests(Day4TestCase):
    def test_priority_then_fifo(self):
        low = self.queue.add(self.transfer(self.a_rub, self.o_rub, 1, priority="low"))
        n1 = self.queue.add(self.transfer(self.a_rub, self.o_rub, 2))
        urgent = self.queue.add(self.transfer(self.a_rub, self.o_rub, 3, priority="urgent"))
        n2 = self.queue.add(self.transfer(self.a_rub, self.o_rub, 4))
        high = self.queue.add(self.transfer(self.a_rub, self.o_rub, 5, priority=TransactionPriority.HIGH))
        self.assertEqual(self.queue.ready(), [urgent, high, n1, n2, low])
        self.assertEqual(self.processor.process_queue(self.queue), [urgent, high, n1, n2, low])
        self.assertEqual(len(self.queue), 0)

    def test_deferred(self):
        later = DAY + timedelta(hours=3)
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100, scheduled_at=later))
        self.assertEqual(self.queue.ready(), [])
        self.assertEqual(self.queue.deferred(), [tx])
        self.assertIsNone(self.queue.pop_ready())
        self.assertEqual(self.processor.process_queue(self.queue), [])
        with self.assertRaises(TransactionError):  # и напрямую раньше времени нельзя
            self.processor.process(tx)
        self.clock.now = later
        self.processor.process_queue(self.queue)
        self.assertIs(tx.status, TransactionStatus.COMPLETED)
        self.assertEqual(self.o_rub.balance, D("100.00"))

    def test_cancel_own(self):
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100))
        self.clock.now = DAY + timedelta(minutes=1)
        self.queue.cancel("CL-ANNA", tx.transaction_id)
        self.assertIs(tx.status, TransactionStatus.CANCELLED)
        self.assertEqual(tx.finished_at, DAY + timedelta(minutes=1))
        self.assertNotIn(tx, self.queue)
        self.assertEqual(self.processor.process_queue(self.queue), [])
        self.assertEqual(self.a_rub.balance, D("10000.00"))
        with self.assertRaises(TransactionNotFoundError):
            self.queue.cancel("CL-ANNA", tx.transaction_id)

    def test_cancel_is_protected(self):
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100))
        with self.assertRaises(AccessDeniedError):
            self.queue.cancel("CL-OLEG", tx.transaction_id)  # чужая
        self.assertIn(ActivityKind.FOREIGN_TRANSACTION_CANCEL, self.kinds())
        self.bank.logout("CL-ANNA")
        with self.assertRaises(AccessDeniedError):
            self.queue.cancel("CL-ANNA", tx.transaction_id)  # без входа
        self.assertIs(tx.status, TransactionStatus.PENDING)

    def test_forged_transaction_rejected(self):
        forged = Transaction(
            "internal_transfer", 5_000, "RUB",
            sender=self.o_prem.account_number, receiver=self.a_rub.account_number,
            initiator_id="CL-OLEG", created_at=DAY,
        )
        with self.assertRaises(AccessDeniedError):
            self.queue.add(forged)
        self.assertEqual(self.kinds(), [ActivityKind.FORGED_TRANSACTION])
        # и напрямую в процессор — отказ, деньги на месте
        self.processor.process(forged)
        self.assertIs(forged.status, TransactionStatus.FAILED)
        self.assertEqual(self.o_prem.balance, D("1000.00"))

    def test_processed_outside_queue_runs_once(self):
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100))
        self.processor.process(tx)  # мимо очереди
        self.assertEqual(self.processor.process_queue(self.queue), [])  # второй раз не выполнится
        self.assertEqual(len(self.queue), 0)
        self.assertEqual(self.a_rub.balance, D("9900.00"))

    def test_add_twice(self):
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100))
        with self.assertRaises(TransactionError):
            self.queue.add(tx)


class ProcessorTests(Day4TestCase):
    def test_internal_transfer(self):
        before = self.money()
        tx = self.run_one(self.transfer(self.a_rub, self.o_rub, 2_500))
        self.assertIs(tx.status, TransactionStatus.COMPLETED)
        self.assertEqual((self.a_rub.balance, self.o_rub.balance), (D("7500.00"), D("2500.00")))
        self.assertEqual((tx.fee, tx.debit_amount, tx.credit_amount), (D("0.00"), D("2500.00"), D("2500.00")))
        self.assertEqual((tx.attempts, tx.finished_at), (1, DAY))
        self.assertEqual(self.money(), before)

    def test_external_transfer_fee(self):
        before = self.money()
        tx = self.run_one(self.transfer(self.a_rub, EXTERNAL, 1_000))
        self.assertIs(tx.status, TransactionStatus.COMPLETED)
        self.assertEqual(tx.fee, D("10.00"))
        self.assertEqual(self.a_rub.balance, D("8990.00"))
        self.assertEqual(self.processor.fee_income, {Currency.RUB: D("10.00")})
        self.assertEqual(self.processor.external_outflow, {Currency.RUB: D("1000.00")})
        self.assertEqual(self.money(), before)

    def test_conversion(self):
        tx = self.run_one(self.transfer(self.a_usd, self.o_rub, 100))
        self.assertEqual(self.a_usd.balance, D("900.00"))
        self.assertEqual(self.o_rub.balance, D("9000.00"))  # 1 USD = 90 RUB
        self.assertEqual((tx.debit_amount, tx.credit_amount), (D("100.00"), D("9000.00")))

    def test_amount_in_other_currency(self):
        # Анна платит со своего рублёвого счёта 10 USD
        tx = self.run_one(self.transfer(self.a_rub, self.o_rub, 10, currency="USD"))
        self.assertEqual(self.a_rub.balance, D("9100.00"))
        self.assertEqual(tx.debit_amount, D("900.00"))

    def test_no_minus_for_regular_accounts(self):
        tx = self.run_one(self.transfer(self.a_rub, EXTERNAL, 9_950))  # + 99.50 комиссия > 10 000
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertIn("только Premium", tx.failure_reason)
        self.assertEqual(self.a_rub.balance, D("10000.00"))
        self.assertEqual(self.processor.fee_income, {})

    def test_premium_can_go_into_overdraft(self):
        tx = self.run_one(self.transfer(self.o_prem, self.a_rub, 1_500, client="CL-OLEG"))
        self.assertIs(tx.status, TransactionStatus.COMPLETED)
        self.assertEqual(self.o_prem.balance, D("-500.00"))
        too_much = self.run_one(self.transfer(self.o_prem, self.a_rub, 50_000, client="CL-OLEG"))
        self.assertIs(too_much.status, TransactionStatus.FAILED)  # дальше лимита овердрафта
        self.assertEqual(self.o_prem.balance, D("-500.00"))

    def test_savings_keeps_min_balance(self):
        tx = self.run_one(self.transfer(self.a_sav, self.a_rub, 8_001))
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertEqual(self.a_sav.balance, D("10000.00"))
        ok = self.run_one(self.transfer(self.a_sav, self.a_rub, 8_000))
        self.assertIs(ok.status, TransactionStatus.COMPLETED)

    def test_frozen_sender_or_receiver(self):
        for frozen in (self.a_rub, self.o_rub):
            with self.subTest(frozen=frozen.masked_number):
                tx = self.transfer(self.a_rub, self.o_rub, 100)
                self.bank.freeze_account(frozen.account_number)
                self.run_one(tx)
                self.assertIs(tx.status, TransactionStatus.FAILED)
                self.assertIn("заморожен", tx.failure_reason)
                self.bank.unfreeze_account(frozen.account_number)
        self.assertEqual((self.a_rub.balance, self.o_rub.balance), (D("10000.00"), D("0.00")))

    def test_withdrawal_limit(self):
        rich = self.bank.open_account("CL-ANNA", balance=500_000)
        tx = self.run_one(self.transfer(rich, self.o_rub, 150_000))
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertIn("лимит", tx.failure_reason)

    def test_retry_then_success(self):
        self.gateway.failures = 1
        tx = self.run_one(self.transfer(self.a_rub, EXTERNAL, 1_000))
        self.assertIs(tx.status, TransactionStatus.PENDING)
        self.assertEqual(tx.scheduled_at, DAY + TransactionProcessor.RETRY_DELAY)
        self.assertEqual(self.a_rub.balance, D("10000.00"))  # деньги не тронуты
        self.assertIn(tx, self.queue)
        [error] = self.processor.errors
        self.assertEqual((error.error_type, error.attempt), ("TransientTransactionError", 1))
        self.assertEqual(error.retry_at, tx.scheduled_at)
        self.assertTrue(tx.history[-1].startswith("[16.11 10:00] перенесена на 16.11 10:05"))

        self.clock.now = tx.scheduled_at
        self.processor.process_queue(self.queue)
        self.assertIs(tx.status, TransactionStatus.COMPLETED)
        self.assertEqual(tx.attempts, 2)
        self.assertEqual(self.a_rub.balance, D("8990.00"))

    def test_retries_exhausted(self):
        self.gateway.failures = 99
        tx = self.run_one(self.transfer(self.a_rub, EXTERNAL, 1_000))
        while not tx.is_final:
            self.clock.now = tx.scheduled_at
            self.processor.process_queue(self.queue)
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertEqual(tx.attempts, TransactionProcessor.MAX_ATTEMPTS)
        self.assertEqual(len(self.processor.errors), TransactionProcessor.MAX_ATTEMPTS)
        self.assertEqual(self.a_rub.balance, D("10000.00"))
        self.assertEqual(len(self.queue), 0)

    def test_night_defers_to_morning(self):
        night = datetime(2026, 11, 17, 2, 0)
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100, scheduled_at=night))
        self.clock.now = night
        self.processor.process_queue(self.queue)
        self.assertIs(tx.status, TransactionStatus.PENDING)
        self.assertEqual(tx.scheduled_at, datetime(2026, 11, 17, 5, 0))
        self.assertEqual(tx.attempts, 0)  # ночь — не попытка
        self.assertEqual(self.a_rub.balance, D("10000.00"))
        self.clock.now = datetime(2026, 11, 17, 5, 0)
        self.processor.process_queue(self.queue)
        self.assertIs(tx.status, TransactionStatus.COMPLETED)

    def test_blocked_initiator(self):
        tx = self.queue.add(self.transfer(self.a_rub, self.o_rub, 100))
        for _ in range(Bank.MAX_LOGIN_ATTEMPTS):
            try:
                self.bank.authenticate_client("CL-ANNA", "wrong")
            except Exception:
                pass
        self.processor.process_queue(self.queue)
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertEqual(self.a_rub.balance, D("10000.00"))
        self.assertIn(ActivityKind.BLOCKED_CLIENT_OPERATION, self.kinds())

    def test_closed_receiver(self):
        tx = self.transfer(self.a_rub, self.o_rub, 100)
        self.bank.close_account("CL-OLEG", self.o_rub.account_number)
        self.run_one(tx)
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertEqual(self.a_rub.balance, D("10000.00"))

    def test_rollback_if_credit_fails(self):
        tx = self.transfer(self.a_rub, self.o_rub, 100)

        def broken_credit(amount):
            raise AccountFrozenError("сбой зачисления")

        self.o_rub._credit = broken_credit  # имитируем сбой после списания
        before = self.money()
        self.run_one(tx)
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertEqual(self.a_rub.balance, D("10000.00"))
        self.assertEqual(self.money(), before)

    def test_final_transaction_cannot_change(self):
        tx = self.run_one(self.transfer(self.a_rub, self.o_rub, 100))
        with self.assertRaises(TransactionError):
            self.processor.process(tx)
        with self.assertRaises(TransactionError):
            self.queue.add(tx)


class TenTransactionsScenarioTests(Day4TestCase):
    """Задание: создать 10 транзакций, поместить в очередь, выполнить."""

    def test_ten_transactions(self):
        self.gateway.failures = 1
        later = DAY + timedelta(hours=2)
        frozen = self.bank.open_account("CL-OLEG", currency="USD")
        txs = [
            self.transfer(self.a_rub, self.o_rub, 1_000),                          # 1 ok
            self.transfer(self.a_rub, EXTERNAL, 2_000),                            # 2 сбой → повтор → ok
            self.transfer(self.a_usd, self.o_rub, 50),                             # 3 конвертация
            self.transfer(self.o_prem, self.a_rub, 3_000, client="CL-OLEG"),       # 4 овердрафт
            self.transfer(self.a_sav, self.a_rub, 9_000),                          # 5 мин. остаток → отказ
            self.transfer(self.a_rub, self.o_rub, 100_000),                        # 6 нет денег → отказ
            self.transfer(self.a_rub, self.o_prem, 500, priority="urgent"),        # 7 первым
            self.transfer(self.a_rub, self.o_rub, 300, scheduled_at=later),        # 8 отложен
            self.transfer(self.o_rub, self.a_rub, 100, client="CL-OLEG"),          # 9 отмена
            self.transfer(self.a_usd, frozen, 10),                                 # 10 счёт заморожен
        ]
        self.assertEqual(len(txs), 10)
        for tx in txs:
            self.queue.add(tx)
        self.queue.cancel("CL-OLEG", txs[8].transaction_id)
        self.bank.freeze_account(frozen.account_number)
        rub_before = self.money("RUB")

        first_round = self.processor.process_queue(self.queue)
        self.assertIs(first_round[0], txs[6])  # urgent первым
        self.clock.now = later
        self.processor.process_queue(self.queue)

        statuses = [tx.status for tx in txs]
        C, F, X = TransactionStatus.COMPLETED, TransactionStatus.FAILED, TransactionStatus.CANCELLED
        self.assertEqual(statuses, [C, C, C, C, F, F, C, C, X, F])
        self.assertEqual(len(self.queue), 0)
        self.assertEqual(txs[1].attempts, 2)
        # рубли: часть пришла из USD (#3), остальное только перемещалось
        self.assertEqual(self.money("RUB"), rub_before + D("4500.00"))


if __name__ == "__main__":
    unittest.main()
