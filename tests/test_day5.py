"""Тесты дня 5. Запуск: python3 -m unittest discover -s tests -v"""

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bank import (
    ActivityKind,
    AuditLevel,
    AuditLog,
    AuthenticationError,
    Bank,
    Client,
    RiskAnalyzer,
    RiskLevel,
    TransactionError,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    client_risk_profile,
    error_statistics,
    suspicious_operations,
)

D = Decimal
DAY = datetime(2026, 11, 18, 12, 0)
EXTERNAL = "DE89370400440532013000"


class Clock:
    def __init__(self, now: datetime = DAY) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


class FastBank(Bank):
    PASSWORD_ITERATIONS = 1_000


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "audit.jsonl"
        self.log = AuditLog(self.path, clock=self.clock)

    def test_levels_are_ordered(self):
        self.assertLess(AuditLevel.INFO, AuditLevel.WARNING)
        self.assertLess(AuditLevel.ERROR, AuditLevel.CRITICAL)

    def test_memory_and_file(self):
        self.log.log(AuditLevel.INFO, "login", "Анна вошла", client_id="CL-ANNA")
        self.clock.tick(1)
        self.log.log(AuditLevel.CRITICAL, "forged", "подделка", transaction_id="TX-1")
        self.assertEqual(len(self.log), 2)

        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)  # дописывает, а не перезаписывает
        first = json.loads(lines[0])
        self.assertEqual(first, {
            "timestamp": "2026-11-18T12:00:00", "level": "INFO", "event": "login",
            "message": "Анна вошла", "client_id": "CL-ANNA", "transaction_id": None,
        })
        self.assertIn("Анна", lines[0])  # кириллица читаемая, без А
        self.assertEqual(json.loads(lines[1])["level"], "CRITICAL")

    def test_without_path_only_memory(self):
        log = AuditLog()
        log.log(AuditLevel.INFO, "e", "m")
        self.assertEqual(len(log), 1)
        self.assertIsNone(log.path)

    def test_entries_cannot_be_changed(self):
        entry = self.log.log(AuditLevel.ERROR, "e", "m")
        with self.assertRaises(FrozenInstanceError):
            entry.level = AuditLevel.INFO
        with self.assertRaises(AttributeError):
            self.log.entries.append(entry)  # кортеж — копия, не сам журнал

    def test_filter(self):
        self.log.log(AuditLevel.INFO, "a", "1", client_id="CL-ANNA")
        self.clock.tick(10)
        self.log.log(AuditLevel.WARNING, "b", "2", client_id="CL-OLEG")
        self.clock.tick(10)
        self.log.log(AuditLevel.CRITICAL, "a", "3", client_id="CL-ANNA")

        def messages(**kwargs):
            return [e.message for e in self.log.filter(**kwargs)]

        self.assertEqual(messages(), ["1", "2", "3"])
        self.assertEqual(messages(min_level=AuditLevel.WARNING), ["2", "3"])
        self.assertEqual(messages(client_id="CL-ANNA"), ["1", "3"])
        self.assertEqual(messages(event="b"), ["2"])
        self.assertEqual(messages(since=DAY + timedelta(minutes=10)), ["2", "3"])
        self.assertEqual(messages(until=DAY + timedelta(minutes=10)), ["1", "2"])
        self.assertEqual(messages(client_id="CL-ANNA", min_level=AuditLevel.ERROR), ["3"])


class Day5TestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.audit_path = Path(tmp.name) / "audit.jsonl"
        self.bank = FastBank("Test Bank", clock=self.clock, audit_path=self.audit_path)
        for name, cid, pwd in (("Анна Котова", "CL-ANNA", "anna-pass"),
                               ("Олег Ли", "CL-OLEG", "oleg-pass")):
            self.bank.add_client(Client(name, "1990-01-01", phone="+79991112233", client_id=cid), pwd)
            self.bank.authenticate_client(cid, pwd)

        # Premium: лимит снятия 1 000 000, можно проверять крупные суммы.
        self.a_big = self.bank.open_account("CL-ANNA", "premium", balance=1_000_000)
        self.a_usd = self.bank.open_account("CL-ANNA", balance=5_000, currency="USD")
        self.a_rub = self.bank.open_account("CL-ANNA", balance=10_000)
        self.o_rub = self.bank.open_account("CL-OLEG", balance=10_000)
        self.queue = TransactionQueue(self.bank)
        self.processor = TransactionProcessor(self.bank)

    def transfer(self, sender, receiver, amount, client="CL-ANNA", **kwargs):
        number = receiver if isinstance(receiver, str) else receiver.account_number
        return self.bank.create_transfer(client, sender.account_number, number, amount, **kwargs)

    def pay(self, *args, **kwargs):
        """Создать и сразу выполнить."""
        tx = self.transfer(*args, **kwargs)
        if tx.status is TransactionStatus.PENDING:
            self.queue.add(tx)
            self.processor.process_queue(self.queue)
        return tx

    def risk(self, tx):
        return self.bank.get_risk_assessment(tx.transaction_id)


class RiskAnalyzerTests(Day5TestCase):
    def test_normal_payment_is_low(self):
        first = self.pay(self.a_rub, self.o_rub, 500)
        self.assertEqual((self.risk(first).level, self.risk(first).score), (RiskLevel.LOW, 1))
        self.assertEqual(self.risk(first).reasons, ("новый получатель ****" + self.o_rub.account_number[-4:],))

        self.clock.tick(15)
        second = self.pay(self.a_rub, self.o_rub, 500)
        self.assertEqual((self.risk(second).level, self.risk(second).score), (RiskLevel.LOW, 0))
        self.assertIs(second.status, TransactionStatus.COMPLETED)

    def test_own_account_is_not_new_receiver(self):
        tx = self.transfer(self.a_big, self.a_rub, 100)
        self.assertEqual(self.risk(tx).score, 0)

    def test_large_amount_alone_is_medium_and_passes(self):
        tx = self.pay(self.a_big, self.a_rub, 100_000)  # свой счёт — не новый
        self.assertIs(self.risk(tx).level, RiskLevel.MEDIUM)
        self.assertIn("крупная сумма", self.risk(tx).reasons[0])
        self.assertIs(tx.status, TransactionStatus.COMPLETED)
        warnings = self.bank.audit.filter(event="risk_assessment", min_level=AuditLevel.WARNING)
        self.assertEqual([e.transaction_id for e in warnings], [tx.transaction_id])

    def test_large_amount_in_other_currency(self):
        tx = self.transfer(self.a_usd, self.a_rub, 1_200)  # 1 200 USD = 108 000 RUB
        self.assertIn("108000", self.risk(tx).reasons[0])
        just_below = self.transfer(self.a_usd, self.a_rub, 1_111)  # 99 990 RUB
        self.assertEqual(self.risk(just_below).score, 0)

    def test_frequent_operations_window(self):
        txs = [self.transfer(self.a_rub, self.a_big, 10) for _ in range(5)]
        self.assertEqual([self.risk(t).score for t in txs], [0, 0, 0, 0, 2])

    def test_spaced_operations_are_not_frequent(self):
        scores = []
        for _ in range(5):
            scores.append(self.risk(self.transfer(self.a_rub, self.a_big, 10)).score)
            self.clock.tick(3)  # 0, 3, 6, 9, 12 мин — в окне 10 мин не больше 4
        self.assertEqual(scores, [0, 0, 0, 0, 0])

    def test_night_scheduled(self):
        night = datetime(2026, 11, 19, 3, 0)
        tx = self.transfer(self.a_rub, self.a_big, 100, scheduled_at=night)
        self.assertEqual(self.risk(tx).reasons, ("назначено на ночь (03:00)",))
        self.assertIs(self.risk(tx).level, RiskLevel.MEDIUM)

    def test_high_risk_blocked_at_creation(self):
        before = self.a_big.balance
        tx = self.transfer(self.a_big, self.o_rub, 200_000,
                           scheduled_at=datetime(2026, 11, 19, 2, 0))  # крупно + ночь + новый
        self.assertIs(self.risk(tx).level, RiskLevel.HIGH)
        self.assertEqual(self.risk(tx).score, 5)
        self.assertIs(tx.status, TransactionStatus.FAILED)
        self.assertIn("заблокировано риск-анализом", tx.failure_reason)
        self.assertEqual(self.a_big.balance, before)

        self.assertIn(ActivityKind.HIGH_RISK_OPERATION,
                      [a.kind for a in self.bank.suspicious_activities])
        critical = self.bank.audit.filter(min_level=AuditLevel.CRITICAL)
        self.assertEqual({e.event for e in critical}, {"risk_assessment", "high_risk_operation"})

    def test_blocked_transaction_cannot_be_forced(self):
        for _ in range(4):
            self.transfer(self.a_rub, self.a_big, 10)
        tx = self.transfer(self.a_big, self.o_rub, 150_000)  # крупно + часто + новый
        self.assertIs(tx.status, TransactionStatus.FAILED)
        with self.assertRaises(TransactionError):
            self.queue.add(tx)
        with self.assertRaises(TransactionError):
            self.processor.process(tx)
        self.assertEqual(self.o_rub.balance, D("10000.00"))

    def test_splitting_into_small_payments_is_flagged(self):
        # 5 мелких новым получателям вместо одного крупного
        receivers = [f"DE8937040044053201300{i}" for i in range(5)]
        txs = [self.transfer(self.a_rub, r, 100) for r in receivers]
        self.assertEqual([self.risk(t).level for t in txs][-1], RiskLevel.MEDIUM)  # часто + новый
        self.assertEqual(len(suspicious_operations(self.bank)), 1)

    def test_thresholds_are_configurable(self):
        class StrictAnalyzer(RiskAnalyzer):
            LARGE_AMOUNT_RUB = D("1000")

        class StrictBank(FastBank):
            RISK_ANALYZER = StrictAnalyzer

        bank = StrictBank("Strict", clock=self.clock)
        bank.add_client(Client("Анна", "1990-01-01", phone="+79991112233", client_id="CL-A"), "anna-pass")
        bank.authenticate_client("CL-A", "anna-pass")
        a, b = bank.open_account("CL-A", balance=5_000), bank.open_account("CL-A")
        tx = bank.create_transfer("CL-A", a.account_number, b.account_number, 2_000)
        self.assertIs(bank.get_risk_assessment(tx.transaction_id).level, RiskLevel.MEDIUM)


class AuditIntegrationTests(Day5TestCase):
    def test_bank_events_go_to_audit(self):
        with self.assertRaises(AuthenticationError):
            self.bank.authenticate_client("CL-OLEG", "wrong")
        [entry] = self.bank.audit.filter(event="failed_login")
        self.assertEqual((entry.level, entry.client_id), (AuditLevel.WARNING, "CL-OLEG"))

    def test_processor_events_go_to_audit(self):
        ok = self.pay(self.a_rub, self.a_big, 100)
        bad = self.pay(self.a_rub, self.a_big, 50_000)  # денег нет
        self.assertEqual([e.event for e in self.bank.audit.filter(transaction_id=ok.transaction_id)],
                         ["risk_assessment", "transaction_completed"])
        [error] = self.bank.audit.filter(transaction_id=bad.transaction_id, min_level=AuditLevel.ERROR)
        self.assertEqual(error.event, "transaction_failed")
        self.assertTrue(error.message.startswith("InsufficientFundsError"))

    def test_audit_file_has_all_entries(self):
        self.pay(self.a_rub, self.a_big, 100)
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len(self.bank.audit))
        self.assertEqual([json.loads(l)["event"] for l in lines],
                         [e.event for e in self.bank.audit.entries])


class ReportsTests(Day5TestCase):
    def setUp(self):
        super().setUp()
        self.normal = self.pay(self.a_rub, self.a_big, 100)                      # LOW
        self.medium = self.pay(self.a_big, self.a_rub, 120_000)                  # MEDIUM: крупно
        self.no_money = self.pay(self.a_rub, self.o_rub, 500_000)                # MEDIUM: крупно + новый, отказ по лимиту
        self.high = self.transfer(self.a_big, self.o_rub, 300_000,
                                  scheduled_at=datetime(2026, 11, 19, 1, 0))     # HIGH: блок
        self.oleg_tx = self.pay(self.o_rub, self.a_rub, 50, client="CL-OLEG")    # LOW

    def test_suspicious_operations(self):
        found = suspicious_operations(self.bank)
        self.assertEqual([tx for tx, _ in found], [self.high, self.no_money, self.medium])
        self.assertEqual([a.score for _, a in found], [5, 3, 2])

    def test_client_risk_profile(self):
        anna = client_risk_profile(self.bank, "CL-ANNA")
        self.assertIs(anna.level, RiskLevel.HIGH)
        self.assertEqual(anna.transactions, 4)
        self.assertEqual(anna.by_risk, {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 1})
        self.assertEqual(anna.blocked_operations, 1)

        oleg = client_risk_profile(self.bank, "CL-OLEG")
        self.assertIs(oleg.level, RiskLevel.LOW)
        with self.assertRaises(AuthenticationError):
            self.bank.authenticate_client("CL-OLEG", "wrong")
        oleg = client_risk_profile(self.bank, "CL-OLEG")
        self.assertEqual((oleg.level, oleg.failed_logins), (RiskLevel.MEDIUM, 1))

    def test_error_statistics(self):
        stats = error_statistics(self.bank, self.processor)
        self.assertEqual((stats.total_transactions, stats.completed, stats.failed), (5, 3, 2))
        self.assertEqual(stats.failure_rate, D("40.0"))
        self.assertEqual(stats.errors_by_type, {"WithdrawalLimitExceededError": 1, "HighRiskBlocked": 1})
        self.assertEqual(stats.audit_by_level[AuditLevel.ERROR], 1)
        self.assertEqual(stats.audit_by_level[AuditLevel.CRITICAL], 2)  # оценка + флаг


class NormalAndSuspiciousSetTests(Day5TestCase):
    """Задание: набор обычных и подозрительных транзакций."""

    def test_set(self):
        normal = []
        for amount in (100, 250, 300):
            normal.append(self.pay(self.a_rub, self.a_big, amount))
            self.clock.tick(5)
        normal.append(self.pay(self.o_rub, self.a_rub, 1_000, client="CL-OLEG"))
        self.clock.tick(15)

        suspicious = [
            self.pay(self.a_big, EXTERNAL, 150_000),                                     # крупно + новый
            self.pay(self.a_rub, self.a_big, 10, scheduled_at=datetime(2026, 11, 19, 4, 0)),  # ночь
            self.pay(self.a_big, self.o_rub, 500_000, scheduled_at=datetime(2026, 11, 19, 3, 0)),  # блок
        ]

        self.assertTrue(all(t.status is TransactionStatus.COMPLETED for t in normal))
        self.assertTrue(all(self.risk(t).level is RiskLevel.LOW for t in normal))
        self.assertEqual([self.risk(t).level for t in suspicious],
                         [RiskLevel.MEDIUM, RiskLevel.MEDIUM, RiskLevel.HIGH])
        self.assertIs(suspicious[0].status, TransactionStatus.COMPLETED)  # medium — пропускаем
        self.assertIs(suspicious[1].status, TransactionStatus.PENDING)    # ждёт утра
        self.assertIs(suspicious[2].status, TransactionStatus.FAILED)     # high — блок
        self.assertEqual(len(suspicious_operations(self.bank)), 3)


if __name__ == "__main__":
    unittest.main()
