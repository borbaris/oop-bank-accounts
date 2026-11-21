"""Тесты дня 7. Запуск: python3 -m unittest discover -s tests -v"""

import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import demo_day6
import demo_day7
from bank import (
    BankAccount,
    Bank,
    Client,
    InsufficientFundsError,
    InvestmentAccount,
    PremiumAccount,
    ReportBuilder,
    ReportKind,
    SavingsAccount,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
)

D = Decimal
DAY = datetime(2026, 11, 20, 10, 0)


class Clock:
    def __init__(self, now: datetime = DAY) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FastBank(Bank):
    PASSWORD_ITERATIONS = 1_000


def assert_history_matches_balance(test, account):
    history = account.balance_history
    test.assertEqual(history[0].operation, "Открытие счёта")
    test.assertEqual(history[0].change, history[0].balance)
    test.assertEqual(sum((e.change for e in history), D("0.00")), account.balance)
    test.assertEqual(history[-1].balance, account.balance)
    for before, after in zip(history, history[1:]):
        test.assertEqual(before.balance + after.change, after.balance)


class BalanceHistoryTests(unittest.TestCase):
    """Каждое изменение баланса попадает в историю — на ней строится график."""

    def test_every_operation_is_recorded(self):
        bank_acc = BankAccount("Анна", balance=1_000)
        bank_acc.deposit(500)
        bank_acc.withdraw(200)

        savings = SavingsAccount("Анна", balance=10_000, min_balance=1_000, monthly_rate="0.01")
        savings.withdraw(1_000)
        savings.apply_monthly_interest()

        premium = PremiumAccount("Анна", balance=100)
        premium.withdraw(1_000)  # в овердрафт, + комиссия 100

        invest = InvestmentAccount("Анна", balance=5_000)
        invest.invest("etf", 3_000)
        invest.sell("etf", 1_000)
        invest.withdraw(500)

        closing = SavingsAccount("Анна", balance=2_000, min_balance=500)
        closing.close_with_payout()

        expected = {
            bank_acc: ["Открытие счёта", "Пополнение", "Снятие"],
            savings: ["Открытие счёта", "Снятие", "Начисление процентов"],
            premium: ["Открытие счёта", "Снятие (вкл. комиссию 100.00)"],
            invest: ["Открытие счёта", "Покупка актива etf", "Продажа актива etf", "Снятие"],
            closing: ["Открытие счёта", "Закрытие с выплатой остатка"],
        }
        for account, operations in expected.items():
            with self.subTest(account=type(account).__name__):
                self.assertEqual([e.operation for e in account.balance_history], operations)
                assert_history_matches_balance(self, account)
        self.assertEqual(premium.balance_history[-1].change, D("-1100.00"))

    def test_failed_operation_leaves_no_trace(self):
        acc = BankAccount("Анна", balance=100)
        with self.assertRaises(InsufficientFundsError):
            acc.withdraw(1_000)
        self.assertEqual(len(acc.balance_history), 1)

    def test_history_is_read_only(self):
        acc = BankAccount("Анна", balance=100)
        with self.assertRaises(AttributeError):
            acc.balance_history.append(None)  # кортеж
        with self.assertRaises(AttributeError):
            acc.balance_history[0].balance = D("1000000")  # frozen dataclass

    def test_bank_accounts_use_bank_clock(self):
        clock = Clock()
        bank = FastBank("B", clock=clock)
        bank.add_client(Client("Анна", "1990-01-01", phone="+79991112233", client_id="CL-ANNA"), "password")
        bank.authenticate_client("CL-ANNA", "password")
        acc = bank.open_account("CL-ANNA", balance=100)
        clock.now = DAY + timedelta(hours=3)
        acc.deposit(50)
        self.assertEqual([e.timestamp for e in acc.balance_history], [DAY, DAY + timedelta(hours=3)])

    def test_demo_day_history_matches_every_balance(self):
        world = demo_day6.build_world(out=lambda line: None)
        demo_day6.simulate(world)
        for name, account in world.accounts.items():
            with self.subTest(account=name):
                assert_history_matches_balance(self, account)


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.bank = FastBank("Test Bank", clock=self.clock)
        for name, cid in (("Анна Котова", "CL-ANNA"), ("Олег Ли", "CL-OLEG")):
            self.bank.add_client(Client(name, "1990-01-01", phone="+79991112233", client_id=cid), "password")
            self.bank.authenticate_client(cid, "password")
        self.a_rub = self.bank.open_account("CL-ANNA", balance=10_000)
        self.a_usd = self.bank.open_account("CL-ANNA", balance=100, currency="USD")
        self.o_rub = self.bank.open_account("CL-OLEG", balance=50_000)

        self.queue = TransactionQueue(self.bank)
        self.processor = TransactionProcessor(self.bank)
        self.clock.now += timedelta(minutes=10)
        self.ok = self.pay(self.a_rub, self.o_rub, 1_000)                       # выполнена
        self.clock.now += timedelta(minutes=10)
        self.incoming = self.pay(self.o_rub, self.a_usd, 900, client="CL-OLEG")  # +10 USD Анне
        self.failed = self.pay(self.a_rub, self.o_rub, 99_999)                   # нет денег
        self.blocked = self.pay(self.a_usd, "DE89370400440532013000", 2_000,
                                scheduled_at=datetime(2026, 11, 21, 2, 0))       # HIGH риск
        self.builder = ReportBuilder(self.bank, self.processor)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def pay(self, sender, receiver, amount, client="CL-ANNA", **kwargs):
        number = receiver if isinstance(receiver, str) else receiver.account_number
        tx = self.bank.create_transfer(client, sender.account_number, number, amount, **kwargs)
        if tx.status is TransactionStatus.PENDING:
            self.queue.add(tx)
            self.processor.process_queue(self.queue)
        return tx


class ReportContentTests(ReportTestCase):
    def test_client_report(self):
        report = self.builder.client_report("CL-ANNA")
        self.assertIs(report.kind, ReportKind.CLIENT)
        s = report.summary
        self.assertEqual((s["client_id"], s["accounts"], s["transactions"]), ("CL-ANNA", 2, 4))
        # 9 000 RUB + 110 USD (= 9 900 RUB)
        self.assertEqual(s["total_rub"], D("18900.00"))
        self.assertEqual((s["incoming_rub"], s["outgoing_rub"]), (D("900.00"), D("1000.00")))
        self.assertEqual(s["risk_level"], "HIGH")
        self.assertEqual(len(report.tables["accounts"]), 2)
        self.assertEqual([r["direction"].value for r in report.tables["transactions"]],
                         ["out", "in", "out", "out"])
        self.assertEqual([r["level"] for r in report.tables["suspicious"]], ["HIGH"])

    def test_bank_report(self):
        report = self.builder.bank_report()
        s = report.summary
        self.assertEqual((s["clients"], s["accounts"], s["transactions"]), (2, 3, 4))
        self.assertEqual((s["completed"], s["failed"]), (2, 2))
        self.assertEqual(s["total_rub"], D("69000.00"))  # как на старте: 10 000 + 100 USD + 50 000
        self.assertEqual([r["client_id"] for r in report.tables["top_clients"]], ["CL-OLEG", "CL-ANNA"])

    def test_risk_report(self):
        report = self.builder.risk_report()
        s = report.summary
        self.assertEqual((s["assessed"], s["high_blocked"]), (4, 1))
        self.assertEqual(report.tables["suspicious_operations"][0]["transaction_id"],
                         self.blocked.transaction_id)
        profiles = {r["client_id"]: r["level"] for r in report.tables["client_profiles"]}
        self.assertEqual(profiles, {"CL-ANNA": "HIGH", "CL-OLEG": "LOW"})

    def test_text(self):
        text = self.builder.to_text(self.builder.client_report("CL-ANNA"))
        self.assertTrue(text.startswith("Отчёт по клиенту Анна Котова (CL-ANNA)"))
        for part in ("[accounts]", "[transactions]", "[suspicious]", "18 900.00"):
            self.assertIn(part, text)


class ExportTests(ReportTestCase):
    def test_json(self):
        report = self.builder.bank_report()
        path = self.builder.export_to_json(report, self.tmp / "nested" / "bank.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data, report.to_dict())
        self.assertEqual(data["summary"]["total_rub"], "69000.00")  # строкой, без потери копеек
        self.assertEqual(data["kind"], "bank")

    def test_csv(self):
        report = self.builder.client_report("CL-ANNA")
        paths = self.builder.export_to_csv(report, self.tmp)
        self.assertEqual([p.name for p in paths], [
            "client_CL-ANNA_summary.csv", "client_CL-ANNA_accounts.csv",
            "client_CL-ANNA_transactions.csv", "client_CL-ANNA_suspicious.csv",
        ])
        with paths[2].open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["amount"], "1000.00")
        self.assertEqual(rows[0]["status"], "completed")
        with paths[0].open(encoding="utf-8-sig", newline="") as file:
            summary = {r["metric"]: r["value"] for r in csv.DictReader(file)}
        self.assertEqual(summary["full_name"], "Анна Котова")

    def test_full_account_numbers_never_exported(self):
        for report in (self.builder.client_report("CL-ANNA"), self.builder.bank_report(),
                       self.builder.risk_report()):
            self.builder.export_to_json(report, self.tmp / f"{report.kind.value}.json")
            self.builder.export_to_csv(report, self.tmp)
        exported = "".join(p.read_text(encoding="utf-8-sig") for p in self.tmp.iterdir())
        for account in (self.a_rub, self.a_usd, self.o_rub):
            self.assertNotIn(account.account_number, exported)
            self.assertIn(account.masked_number, exported)

    def test_empty_table_still_writes_csv(self):
        report = self.builder.client_report("CL-OLEG")
        self.assertEqual(report.tables["suspicious"], [])
        paths = self.builder.export_to_csv(report, self.tmp)
        self.assertTrue((self.tmp / "client_CL-OLEG_suspicious.csv").exists())
        self.assertEqual(len(paths), 4)


class ChartTests(ReportTestCase):
    def test_pies(self):
        currency_ax = self.builder.currency_pie().axes[0]
        self.assertEqual(len(currency_ax.patches), 2)  # RUB, USD
        risk_ax = self.builder.risk_pie().axes[0]
        self.assertEqual(len(risk_ax.patches), 2)      # LOW, HIGH

    def test_status_bar(self):
        ax = self.builder.status_bar().axes[0]
        heights = [bar.get_height() for bar in ax.patches]
        self.assertEqual(heights, [0, 2, 2, 0])  # pending, completed, failed, cancelled

    def test_top_clients_bar(self):
        ax = self.builder.top_clients_bar().axes[0]
        widths = sorted(bar.get_width() for bar in ax.patches)
        self.assertEqual(widths, [18_900.0, 50_100.0])

    def test_balance_chart(self):
        ax = self.builder.balance_chart("CL-ANNA").axes[0]
        lines = ax.get_lines()
        self.assertEqual(len(lines), 3)  # 2 счёта + итог
        total = lines[-1]
        self.assertEqual(total.get_label(), "Итого")
        self.assertEqual(total.get_ydata()[0], 19_000.0)   # 10 000 + 100 USD
        self.assertEqual(total.get_ydata()[-1], 18_900.0)  # то же, что в отчёте

    def test_no_data(self):
        empty = ReportBuilder(FastBank("Empty"), self.processor)
        self.assertIn("нет данных", [t.get_text() for t in empty.risk_pie().axes[0].texts])
        self.bank.add_client(Client("Без Счетов", "1990-01-01", phone="+79990000000",
                                    client_id="CL-ZERO"), "password")
        chart = self.builder.balance_chart("CL-ZERO")
        self.assertIn("нет данных", [t.get_text() for t in chart.axes[0].texts])

    def test_save_charts(self):
        paths = self.builder.save_charts(self.tmp / "charts", client_ids=["CL-ANNA", "CL-OLEG"])
        self.assertEqual(len(paths), 6)
        for path in paths:
            with self.subTest(chart=path.name):
                self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
        self.assertIn("line_balance_CL-OLEG.png", [p.name for p in paths])


class DemoTests(unittest.TestCase):
    def test_demo_generates_everything(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            demo_day7.main(Path(tmp))
            files = {p.relative_to(tmp).as_posix() for p in Path(tmp).rglob("*") if p.is_file()}
        self.assertTrue({"json/client.json", "json/bank.json", "json/risk.json",
                         "csv/bank_summary.csv", "charts/line_balance_CL-ANNA.png"} <= files)
        self.assertEqual(sum(f.startswith("charts/") for f in files), 6)
        self.assertIn("Отчёт по рискам", out.getvalue())


if __name__ == "__main__":
    unittest.main()
