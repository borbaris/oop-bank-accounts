"""Тесты дня 6. Запуск: python3 -m unittest discover -s tests -v"""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

import demo_day6
from bank import (
    ActivityKind,
    AuditLevel,
    Bank,
    Client,
    Currency,
    Direction,
    RiskLevel,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransientTransactionError,
    client_history,
    reconcile,
    top_clients,
    total_balance,
    transaction_statistics,
)

D = Decimal
DAY = datetime(2026, 11, 20, 10, 0)
EXTERNAL = "DE89370400440532013000"


class Clock:
    def __init__(self, now: datetime = DAY) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FastBank(Bank):
    PASSWORD_ITERATIONS = 1_000


class Day6TestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.bank = FastBank("Test Bank", clock=self.clock)
        for name, cid in (("Анна Котова", "CL-ANNA"), ("Олег Ли", "CL-OLEG"), ("Ника Белова", "CL-NIKA")):
            self.bank.add_client(Client(name, "1990-01-01", phone="+79991112233", client_id=cid), "password")
            self.bank.authenticate_client(cid, "password")
        self.a_rub = self.bank.open_account("CL-ANNA", balance=10_000)
        self.a_usd = self.bank.open_account("CL-ANNA", balance=100, currency="USD")
        self.o_rub = self.bank.open_account("CL-OLEG", balance=50_000)
        self.n_rub = self.bank.open_account("CL-NIKA", balance=1_000)
        self.opening = self.bank.get_total_balance()
        self.fail_external = 0
        self.queue = TransactionQueue(self.bank)
        self.processor = TransactionProcessor(self.bank, external_gateway=self.gateway)

    def gateway(self, tx):
        if self.fail_external > 0:
            self.fail_external -= 1
            raise TransientTransactionError("не ответил")

    def pay(self, sender, receiver, amount, client="CL-ANNA", **kwargs):
        number = receiver if isinstance(receiver, str) else receiver.account_number
        tx = self.bank.create_transfer(client, sender.account_number, number, amount, **kwargs)
        if tx.status is TransactionStatus.PENDING:
            self.queue.add(tx)
            self.processor.process_queue(self.queue)
        return tx

    def events(self, tx):
        return [e.event for e in self.bank.audit.filter(transaction_id=tx.transaction_id)]


class QueueLoggingTests(Day6TestCase):
    def test_queued_logged_once_even_after_retry(self):
        self.fail_external = 1
        tx = self.pay(self.a_rub, EXTERNAL, 100)
        self.clock.now = tx.scheduled_at
        self.processor.process_queue(self.queue)
        self.assertEqual(self.events(tx), ["risk_assessment", "transaction_queued",
                                           "transaction_retry", "transaction_completed"])

    def test_night_deferral_logged(self):
        night = datetime(2026, 11, 21, 2, 0)
        tx = self.pay(self.a_rub, self.o_rub, 100, scheduled_at=night)
        self.clock.now = night
        self.processor.process_queue(self.queue)
        [deferred] = self.bank.audit.filter(event="transaction_deferred")
        self.assertEqual((deferred.transaction_id, deferred.level), (tx.transaction_id, AuditLevel.INFO))
        self.assertIn("21.11 05:00", deferred.message)

    def test_cancel_logged(self):
        tx = self.bank.create_transfer("CL-ANNA", self.a_rub.account_number, self.o_rub.account_number,
                                       100, scheduled_at=DAY + timedelta(hours=1))
        self.queue.add(tx)
        self.queue.cancel("CL-ANNA", tx.transaction_id, "передумала")
        [entry] = self.bank.audit.filter(event="transaction_cancelled")
        self.assertEqual((entry.transaction_id, entry.message), (tx.transaction_id, "передумала"))


class ReportTests(Day6TestCase):
    def test_top_clients_counts_all_currencies(self):
        # Анна: 10 000 RUB + 100 USD (= 9 000 RUB) = 19 000; Олег 50 000; Ника 1 000
        top = top_clients(self.bank, 2)
        self.assertEqual([(w.client.client_id, w.total_rub) for w in top],
                         [("CL-OLEG", D("50000.00")), ("CL-ANNA", D("19000.00"))])
        self.assertEqual(top[1].by_currency, {Currency.RUB: D("10000.00"), Currency.USD: D("100.00")})

    def test_top_clients_includes_client_without_accounts(self):
        self.bank.add_client(Client("Без Счетов", "1990-01-01", phone="+79990000000", client_id="CL-ZERO"),
                             "password")
        everyone = top_clients(self.bank, 10)
        self.assertEqual(everyone[-1].client.client_id, "CL-ZERO")
        self.assertEqual(everyone[-1].total_rub, D("0.00"))

    def test_total_balance(self):
        per_currency, in_rub = total_balance(self.bank)
        self.assertEqual(per_currency, {Currency.RUB: D("61000.00"), Currency.USD: D("100.00")})
        self.assertEqual(in_rub, D("70000.00"))

    def test_client_history_directions(self):
        out = self.pay(self.a_rub, self.o_rub, 100)
        own = self.pay(self.a_usd, self.a_rub, 10)
        incoming = self.pay(self.o_rub, self.a_rub, 50, client="CL-OLEG")
        failed_in = self.pay(self.n_rub, self.a_rub, 5_000, client="CL-NIKA")  # нет денег
        self.assertIs(failed_in.status, TransactionStatus.FAILED)

        history = client_history(self.bank, "CL-ANNA")
        self.assertEqual([(h.transaction, h.direction) for h in history],
                         [(out, Direction.OUTGOING), (own, Direction.OWN), (incoming, Direction.INCOMING)])
        # у Олега: его входящий от Анны и его исходящий Анне
        self.assertEqual([h.direction for h in client_history(self.bank, "CL-OLEG")],
                         [Direction.INCOMING, Direction.OUTGOING])

    def test_transaction_statistics(self):
        self.fail_external = 1
        retried = self.pay(self.a_rub, EXTERNAL, 1_000)
        self.clock.now = retried.scheduled_at
        self.processor.process_queue(self.queue)
        self.pay(self.a_usd, self.o_rub, 50)                               # 4 500 RUB
        self.pay(self.n_rub, self.a_rub, 5_000, client="CL-NIKA")          # отказ
        self.pay(self.o_rub, EXTERNAL, 150_000, client="CL-OLEG",
                 scheduled_at=datetime(2026, 11, 21, 2, 0))                # крупно+новый+ночь = HIGH, блок

        stats = transaction_statistics(self.bank, self.processor)
        self.assertEqual(stats.total, 4)
        self.assertEqual(stats.by_status[TransactionStatus.COMPLETED], 2)
        self.assertEqual(stats.by_status[TransactionStatus.FAILED], 2)
        self.assertEqual(stats.by_risk[RiskLevel.HIGH], 1)
        self.assertEqual(stats.completed_volume_rub, D("5500.00"))
        self.assertEqual(stats.average_completed_rub, D("2750.00"))
        self.assertEqual(stats.largest_completed.amount, D("50.00"))  # 50 USD = 4 500 RUB
        self.assertEqual(stats.retried, 1)
        self.assertEqual(stats.fee_income, {Currency.RUB: D("10.00")})


class ReconcileTests(Day6TestCase):
    def test_balanced_after_mixed_operations(self):
        self.pay(self.a_rub, self.o_rub, 1_000)
        self.pay(self.a_usd, self.o_rub, 10)            # конвертация
        self.pay(self.a_rub, EXTERNAL, 500)             # комиссия + ушло наружу
        self.pay(self.n_rub, self.a_rub, 9_999, client="CL-NIKA")  # отказ — ничего не двигает
        for currency, (expected, actual) in reconcile(self.bank, self.opening).items():
            with self.subTest(currency=currency):
                self.assertEqual(expected, actual)

    def test_detects_money_appearing_from_nowhere(self):
        self.a_rub._balance += D("1.00")  # «печатаем» рубль в обход транзакций
        expected, actual = reconcile(self.bank, self.opening)[Currency.RUB]
        self.assertEqual(actual - expected, D("1.00"))


class DemoScenarioTests(unittest.TestCase):
    """Сценарий из demo_day6 выполняет требования задания."""

    @classmethod
    def setUpClass(cls):
        cls.lines = []
        cls.world = demo_day6.build_world(out=cls.lines.append)
        demo_day6.simulate(cls.world)
        cls.bank = cls.world.bank

    def test_initialization_size(self):
        self.assertTrue(5 <= len(self.bank.clients) <= 10)
        self.assertTrue(10 <= len(self.world.accounts) <= 15)

    def test_transactions_count_and_mix(self):
        txs = self.bank.transactions
        self.assertTrue(30 <= len(txs) <= 50, len(txs))
        statuses = {tx.status for tx in txs}
        self.assertTrue({TransactionStatus.COMPLETED, TransactionStatus.FAILED,
                         TransactionStatus.CANCELLED} <= statuses)
        self.assertNotIn(TransactionStatus.PENDING, statuses)  # к утру всё разобрано
        self.assertEqual(len(self.world.queue), 0)
        self.assertGreaterEqual(len(self.world.rejected), 5)    # ошибки ещё до создания

    def test_suspicious_present(self):
        levels = {a.level for a in self.bank.risk_assessments}
        self.assertTrue({RiskLevel.MEDIUM, RiskLevel.HIGH} <= levels)
        kinds = {a.kind for a in self.bank.suspicious_activities}
        for kind in (ActivityKind.FORGED_TRANSACTION, ActivityKind.CLIENT_BLOCKED,
                     ActivityKind.FOREIGN_ACCOUNT_ACCESS, ActivityKind.NIGHT_OPERATION,
                     ActivityKind.BLOCKED_CLIENT_OPERATION):
            self.assertIn(kind, kinds)

    def test_log_shows_queue_execution_rejection(self):
        text = "\n".join(self.lines)
        for marker in ("📥 в очередь", "✅ выполнена", "🚫 отклонена", "🚫 не создана",
                       "🔁 сбой, повтор", "🌙 перенесена", "↩️  отменена", "⛔ блок, HIGH"):
            self.assertIn(marker, text)

    def test_money_reconciles(self):
        for currency, (expected, actual) in reconcile(self.bank, self.world.opening_balance).items():
            with self.subTest(currency=currency):
                self.assertEqual(expected, actual)

    def test_full_demo_runs(self):
        lines = []
        world = demo_day6.build_world(out=lines.append)
        demo_day6.simulate(world)
        demo_day6.show_client(world, "CL-ANNA")
        demo_day6.show_reports(world)
        self.assertNotIn("❌", "\n".join(lines))  # сверка без расхождений
        self.assertNotIn("‼", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
