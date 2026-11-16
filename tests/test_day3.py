"""Тесты дня 3. Запуск: python3 -m unittest discover -s tests -v"""

import unittest
from datetime import date, datetime
from decimal import Decimal

from bank import (
    AccessDeniedError,
    AccountNotFoundError,
    AccountStatus,
    ActivityKind,
    AuthenticationError,
    Bank,
    BankAccount,
    BankError,
    Client,
    ClientBlockedError,
    ClientError,
    ClientNotFoundError,
    ClientStatus,
    InvalidOperationError,
    InvestmentAccount,
    OperatingHoursError,
    PremiumAccount,
    SavingsAccount,
    UnderageClientError,
)

D = Decimal
DAY = datetime(2026, 11, 12, 14, 0)


class Clock:
    def __init__(self, now: datetime = DAY) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FastBank(Bank):
    # Медленный PBKDF2 в тестах не нужен — проверяем логику, а не стойкость.
    PASSWORD_ITERATIONS = 1_000


def make_client(name="Анна Котова", client_id="CL-ANNA", **kwargs) -> Client:
    kwargs.setdefault("phone", "+79991112233")
    return Client(name, kwargs.pop("birth_date", "1990-04-15"), client_id=client_id, **kwargs)


class ClientTests(unittest.TestCase):
    def test_creation_and_contacts(self):
        c = make_client(phone="+7 (999) 111-22-33", email="Anna@Example.com")
        self.assertEqual(c.full_name, "Анна Котова")
        self.assertEqual(c.client_id, "CL-ANNA")
        self.assertEqual(c.contacts, {"phone": "+79991112233", "email": "anna@example.com"})
        self.assertIs(c.status, ClientStatus.ACTIVE)
        self.assertEqual(c.account_numbers, ())

    def test_generated_id(self):
        c = Client("Олег", "1985-01-01", phone="+79990000000")
        self.assertRegex(c.client_id, r"^CL-[0-9A-F]{8}$")

    def test_age_boundary(self):
        birth = date(2000, 6, 15)
        c = Client("Взрослый", birth, phone="+79990000000", today=date(2018, 6, 15))
        self.assertEqual(c.age_on(date(2018, 6, 15)), 18)
        with self.assertRaises(UnderageClientError):
            Client("Почти", birth, phone="+79990000000", today=date(2018, 6, 14))

    def test_birth_date_validation(self):
        self.assertEqual(
            Client("А", datetime(1990, 1, 2, 10, 0), phone="+79990000000").birth_date,
            date(1990, 1, 2),
        )
        with self.assertRaises(ValueError):
            Client("А", "2999-01-01", phone="+79990000000")  # будущее
        with self.assertRaises(ValueError):
            Client("А", "15.04.1990", phone="+79990000000")
        with self.assertRaises(TypeError):
            Client("А", 1990, phone="+79990000000")

    def test_contact_and_name_validation(self):
        for kwargs in ({"phone": "123"}, {"phone": "+7999abc1234"}, {"email": "no-at-sign"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                make_client(**kwargs)
        with self.assertRaises(ValueError):
            make_client(name="   ")
        with self.assertRaises(ValueError):
            make_client(client_id="!!")

    def test_account_numbers_are_read_only(self):
        c = make_client()
        c.add_account_number("ABCD1234")
        self.assertEqual(c.account_numbers, ("ABCD1234",))
        with self.assertRaises(ValueError):
            c.add_account_number("ABCD1234")
        with self.assertRaises(AttributeError):
            c.account_numbers.append("X")  # кортеж


class BankTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.bank = FastBank("Test Bank", clock=self.clock)
        self.anna = self.bank.add_client(make_client(), "anna-pass")
        self.oleg = self.bank.add_client(make_client("Олег Ли", "CL-OLEG"), "oleg-pass")

    def login(self, client_id="CL-ANNA", password="anna-pass"):
        return self.bank.authenticate_client(client_id, password)

    def kinds(self):
        return [a.kind for a in self.bank.suspicious_activities]

    def block_anna(self):
        for _ in range(Bank.MAX_LOGIN_ATTEMPTS - 1):
            with self.assertRaises(AuthenticationError):
                self.login(password="wrong")
        with self.assertRaises(ClientBlockedError):
            self.login(password="wrong")


class AddClientTests(BankTestCase):
    def test_duplicate_and_bad_input(self):
        with self.assertRaises(ClientError):
            self.bank.add_client(make_client(), "another-pass")
        with self.assertRaises(TypeError):
            self.bank.add_client("Анна", "anna-pass")
        with self.assertRaises(ValueError):
            self.bank.add_client(make_client("Ник", "CL-NIK"), "12345")

    def test_password_is_not_stored_in_plain_text(self):
        salt, digest = self.bank._credentials["CL-ANNA"]
        self.assertNotIn(b"anna-pass", salt + digest)
        # одинаковые пароли — разные хеши из-за соли
        self.bank.add_client(make_client("Двойник", "CL-TWIN"), "anna-pass")
        self.assertNotEqual(self.bank._credentials["CL-TWIN"], self.bank._credentials["CL-ANNA"])

    def test_get_client(self):
        self.assertIs(self.bank.get_client("CL-ANNA"), self.anna)
        with self.assertRaises(ClientNotFoundError):
            self.bank.get_client("CL-NOBODY")


class AuthenticationTests(BankTestCase):
    def test_success(self):
        self.assertIs(self.login(), self.anna)
        self.assertTrue(self.bank.is_authenticated("CL-ANNA"))
        self.bank.logout("CL-ANNA")
        self.assertFalse(self.bank.is_authenticated("CL-ANNA"))

    def test_three_wrong_passwords_block_client(self):
        for remaining in (2, 1):
            with self.assertRaises(AuthenticationError) as ctx:
                self.login(password="wrong")
            self.assertIn(f"Осталось попыток: {remaining}", str(ctx.exception))
        with self.assertRaises(ClientBlockedError):
            self.login(password="wrong")

        self.assertTrue(self.anna.is_blocked)
        with self.assertRaises(ClientBlockedError):
            self.login()  # даже верный пароль не помогает
        self.assertEqual(
            self.kinds(),
            [ActivityKind.FAILED_LOGIN] * 3
            + [ActivityKind.CLIENT_BLOCKED, ActivityKind.BLOCKED_CLIENT_LOGIN],
        )

    def test_success_resets_counter(self):
        for _ in range(2):
            with self.assertRaises(AuthenticationError):
                self.login(password="wrong")
        self.login()
        for _ in range(2):
            with self.assertRaises(AuthenticationError):
                self.login(password="wrong")
        self.assertFalse(self.anna.is_blocked)

    def test_blocking_ends_session(self):
        self.login()
        self.block_anna()
        self.assertFalse(self.bank.is_authenticated("CL-ANNA"))

    def test_unknown_client(self):
        with self.assertRaises(AuthenticationError):
            self.bank.authenticate_client("CL-HACKER", "whatever")
        self.assertEqual(self.kinds(), [ActivityKind.UNKNOWN_CLIENT_LOGIN])

    def test_unblock(self):
        self.block_anna()
        self.bank.unblock_client("CL-ANNA")
        self.assertIs(self.login(), self.anna)

    def test_failures_counted_per_client(self):
        for _ in range(2):
            with self.assertRaises(AuthenticationError):
                self.login(password="wrong")
        with self.assertRaises(AuthenticationError):
            self.login("CL-OLEG", "wrong")
        self.assertFalse(self.anna.is_blocked)
        self.assertFalse(self.oleg.is_blocked)

    def test_password_type(self):
        with self.assertRaises(TypeError):
            self.bank.authenticate_client("CL-ANNA", None)


class AccountManagementTests(BankTestCase):
    def test_open_requires_login(self):
        with self.assertRaises(AccessDeniedError):
            self.bank.open_account("CL-ANNA")
        self.assertEqual(self.kinds(), [ActivityKind.UNAUTHENTICATED_OPERATION])
        with self.assertRaises(ClientNotFoundError):
            self.bank.open_account("CL-NOBODY")

    def test_open_each_type(self):
        self.login()
        expected = {
            "bank": BankAccount,
            "savings": SavingsAccount,
            "premium": PremiumAccount,
            "investment": InvestmentAccount,
        }
        for key, cls in expected.items():
            with self.subTest(type=key):
                account = self.bank.open_account("CL-ANNA", key, balance=100)
                self.assertIs(type(account), cls)
                self.assertEqual(account.owner.full_name, "Анна Котова")
        self.assertEqual(len(self.anna.account_numbers), 4)
        self.assertIs(type(self.bank.open_account("CL-ANNA", PremiumAccount)), PremiumAccount)

    def test_open_passes_options(self):
        self.login()
        acc = self.bank.open_account(
            "CL-ANNA", "savings", balance=1_000, currency="usd", min_balance=500
        )
        self.assertEqual(acc.currency, "USD")
        self.assertEqual(acc.min_balance, D("500.00"))
        self.assertIs(self.bank.get_account(acc.account_number), acc)
        self.assertIs(self.bank.get_account(acc.account_number.lower()), acc)

    def test_open_errors(self):
        self.login()
        with self.assertRaises(InvalidOperationError):
            self.bank.open_account("CL-ANNA", "crypto")
        self.bank.open_account("CL-ANNA", account_number="ABCD1234")
        with self.assertRaises(InvalidOperationError):
            self.bank.open_account("CL-ANNA", account_number="abcd1234")

    def test_close_own_account(self):
        self.login()
        acc = self.bank.open_account("CL-ANNA", balance=100)
        with self.assertRaises(InvalidOperationError):
            self.bank.close_account("CL-ANNA", acc.account_number)
        acc.withdraw(100)
        self.bank.close_account("CL-ANNA", acc.account_number)
        self.assertIs(acc.status, AccountStatus.CLOSED)
        self.assertIn(acc.account_number, self.anna.account_numbers)  # история сохраняется

    def test_cannot_touch_foreign_account(self):
        self.login("CL-OLEG", "oleg-pass")
        olegs = self.bank.open_account("CL-OLEG")
        self.login()
        with self.assertRaises(AccessDeniedError):
            self.bank.close_account("CL-ANNA", olegs.account_number)
        self.assertIs(olegs.status, AccountStatus.ACTIVE)
        self.assertEqual(self.kinds(), [ActivityKind.FOREIGN_ACCOUNT_ACCESS])

    def test_freeze_unfreeze(self):
        self.login()
        acc = self.bank.open_account("CL-ANNA", balance=100)
        self.bank.freeze_account(acc.account_number)
        self.assertIs(acc.status, AccountStatus.FROZEN)
        self.bank.unfreeze_account(acc.account_number)
        self.assertIs(acc.status, AccountStatus.ACTIVE)
        with self.assertRaises(AccountNotFoundError):
            self.bank.freeze_account("NOPE0000")


class OperatingHoursTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.acc = self.bank.open_account("CL-ANNA", balance=100)

    def at(self, hour, minute=0):
        self.clock.now = datetime(2026, 11, 13, hour, minute)

    def test_night_blocks_operations(self):
        for hour, minute in ((0, 0), (2, 30), (4, 59)):
            self.at(hour, minute)
            with self.subTest(time=f"{hour:02}:{minute:02}"):
                with self.assertRaises(OperatingHoursError):
                    self.bank.open_account("CL-ANNA")
                with self.assertRaises(OperatingHoursError):
                    self.bank.close_account("CL-ANNA", self.acc.account_number)

    def test_day_boundaries_allowed(self):
        for hour, minute in ((5, 0), (23, 59)):
            self.at(hour, minute)
            with self.subTest(time=f"{hour:02}:{minute:02}"):
                self.bank.open_account("CL-ANNA")

    def test_freeze_allowed_at_night_unfreeze_not(self):
        self.at(3)
        self.bank.freeze_account(self.acc.account_number)
        with self.assertRaises(OperatingHoursError):
            self.bank.unfreeze_account(self.acc.account_number)
        self.assertIs(self.acc.status, AccountStatus.FROZEN)
        self.at(5)
        self.bank.unfreeze_account(self.acc.account_number)
        self.assertIs(self.acc.status, AccountStatus.ACTIVE)

    def test_night_attempts_are_flagged(self):
        self.at(1, 15)
        with self.assertRaises(OperatingHoursError):
            self.bank.open_account("CL-ANNA")
        events = self.bank.get_suspicious_activities("CL-ANNA")
        self.assertEqual(events[-1].kind, ActivityKind.NIGHT_OPERATION)
        self.assertEqual(events[-1].timestamp, datetime(2026, 11, 13, 1, 15))


class MoneyOperationsSecurityTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.acc = self.bank.open_account("CL-ANNA", balance=1_000)
        self.sav = self.bank.open_account("CL-ANNA", "savings", balance=10_000, min_balance=1_000)
        self.inv = self.bank.open_account("CL-ANNA", "investment", balance=1_000)
        self.inv.invest("etf", 500)

    def money_actions(self):
        return {
            "deposit": lambda: self.acc.deposit(10),
            "withdraw": lambda: self.acc.withdraw(10),
            "invest": lambda: self.inv.invest("bonds", 10),
            "sell": lambda: self.inv.sell("etf", 10),
            "interest": self.sav.apply_monthly_interest,
            "close": lambda: self.acc.close(),
        }

    def snapshot(self):
        return (self.acc.balance, self.sav.balance, self.inv.balance, self.inv.portfolio)

    def test_night_blocks_direct_money_operations(self):
        self.clock.now = datetime(2026, 11, 13, 2, 0)
        before = self.snapshot()
        for name, action in self.money_actions().items():
            with self.subTest(op=name), self.assertRaises(OperatingHoursError):
                action()
        self.assertEqual(self.snapshot(), before)
        self.assertIn(ActivityKind.NIGHT_OPERATION, self.kinds())

    def test_day_operations_work(self):
        self.assertEqual(self.acc.deposit(10), D("1010.00"))
        self.assertEqual(self.sav.apply_monthly_interest(), D("100.00"))

    def test_blocked_client_cannot_use_old_account_object(self):
        self.block_anna()
        before = self.snapshot()
        for name, action in self.money_actions().items():
            if name == "interest":
                continue
            with self.subTest(op=name), self.assertRaises(ClientBlockedError):
                action()
        self.assertEqual(self.snapshot(), before)
        self.assertIn(ActivityKind.BLOCKED_CLIENT_OPERATION, self.kinds())

    def test_logout_also_closes_access(self):
        self.bank.logout("CL-ANNA")
        with self.assertRaises(AccessDeniedError):
            self.acc.withdraw(10)
        self.assertEqual(self.acc.balance, D("1000.00"))

    def test_interest_needs_no_login(self):
        self.bank.logout("CL-ANNA")
        self.assertEqual(self.sav.apply_monthly_interest(), D("100.00"))

    def test_unfreeze_directly_at_night_is_blocked(self):
        self.bank.freeze_account(self.acc.account_number)
        self.clock.now = datetime(2026, 11, 13, 3, 0)
        with self.assertRaises(OperatingHoursError):
            self.acc.unfreeze()
        self.assertIs(self.acc.status, AccountStatus.FROZEN)

    def test_security_cannot_be_replaced(self):
        with self.assertRaises(InvalidOperationError):
            self.acc.attach_security(lambda action, by_client: None)


class CloseWithRemainderTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.sav = self.bank.open_account("CL-ANNA", "savings", balance=10_000, min_balance=1_000)
        self.main = self.bank.open_account("CL-ANNA", balance=500)

    def test_savings_with_money_is_not_wiped(self):
        with self.assertRaises(InvalidOperationError):
            self.bank.close_account("CL-ANNA", self.sav.account_number)
        self.assertEqual(self.sav.balance, D("10000.00"))
        self.assertIs(self.sav.status, AccountStatus.ACTIVE)

    def test_remainder_is_transferred(self):
        moved = self.bank.close_account(
            "CL-ANNA", self.sav.account_number, transfer_to=self.main.account_number
        )
        self.assertEqual(moved, D("10000.00"))
        self.assertEqual(self.main.balance, D("10500.00"))
        self.assertEqual(self.sav.balance, D("0.00"))
        self.assertIs(self.sav.status, AccountStatus.CLOSED)
        self.assertEqual(self.bank.get_total_balance(), {"RUB": D("10500.00")})

    def test_bad_transfer_targets_change_nothing(self):
        self.login("CL-OLEG", "oleg-pass")
        olegs = self.bank.open_account("CL-OLEG")
        usd = self.bank.open_account("CL-ANNA", currency="USD")
        frozen = self.bank.open_account("CL-ANNA")
        self.bank.freeze_account(frozen.account_number)
        cases = {
            "чужой": (olegs.account_number, AccessDeniedError),
            "сам себе": (self.sav.account_number, InvalidOperationError),
            "другая валюта": (usd.account_number, InvalidOperationError),
            "заморожен": (frozen.account_number, InvalidOperationError),
            "не существует": ("NOPE0000", AccountNotFoundError),
        }
        for label, (target, error) in cases.items():
            with self.subTest(target=label), self.assertRaises(error):
                self.bank.close_account("CL-ANNA", self.sav.account_number, transfer_to=target)
        self.assertEqual(self.sav.balance, D("10000.00"))
        self.assertIs(self.sav.status, AccountStatus.ACTIVE)

    def test_overdraft_debt_blocks_closing(self):
        prem = self.bank.open_account("CL-ANNA", "premium", balance=100)
        prem.withdraw(1_000)
        with self.assertRaises(InvalidOperationError):
            self.bank.close_account(
                "CL-ANNA", prem.account_number, transfer_to=self.main.account_number
            )
        self.assertIs(prem.status, AccountStatus.ACTIVE)


class ReportsTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.login("CL-OLEG", "oleg-pass")
        self.maria = self.bank.add_client(make_client("Мария", "CL-MARIA"), "maria-pass")
        self.login("CL-MARIA", "maria-pass")

        self.a_rub = self.bank.open_account("CL-ANNA", balance=1_000)
        self.a_inv = self.bank.open_account("CL-ANNA", "investment", balance=500, currency="USD")
        self.a_inv.invest("bonds", 300)
        self.o_rub = self.bank.open_account("CL-OLEG", "premium", balance=5_000)
        self.o_usd = self.bank.open_account("CL-OLEG", balance=50, currency="USD")
        self.m_rub = self.bank.open_account("CL-MARIA", "savings", balance=1_000)

    def test_total_balance_per_currency_includes_portfolio(self):
        self.assertEqual(
            self.bank.get_total_balance(),
            {"RUB": D("7000.00"), "USD": D("550.00")},
        )

    def test_ranking(self):
        rub = self.bank.get_clients_ranking("RUB")
        # Анна и Мария по 1000 — при равенстве по алфавиту
        self.assertEqual(
            [(c.client_id, total) for c, total in rub],
            [("CL-OLEG", D("5000.00")), ("CL-ANNA", D("1000.00")), ("CL-MARIA", D("1000.00"))],
        )
        usd = self.bank.get_clients_ranking("USD")
        self.assertEqual([(c.client_id, t) for c, t in usd],
                         [("CL-ANNA", D("500.00")), ("CL-OLEG", D("50.00"))])

    def test_search(self):
        def numbers(**filters):
            return {a.account_number for a in self.bank.search_accounts(**filters)}

        self.assertEqual(numbers(client_id="CL-ANNA"),
                         {self.a_rub.account_number, self.a_inv.account_number})
        self.assertEqual(numbers(currency="usd"),
                         {self.a_inv.account_number, self.o_usd.account_number})
        self.assertEqual(numbers(account_type="bank"),  # только точный тип
                         {self.a_rub.account_number, self.o_usd.account_number})
        self.assertEqual(numbers(min_balance=1_000, max_balance=1_000),
                         {self.a_rub.account_number, self.m_rub.account_number})
        self.assertEqual(numbers(client_id="CL-OLEG", currency="RUB"),
                         {self.o_rub.account_number})
        self.bank.freeze_account(self.m_rub.account_number)
        self.assertEqual(numbers(status="frozen"), {self.m_rub.account_number})
        self.assertEqual(len(self.bank.search_accounts()), 5)
        with self.assertRaises(ClientNotFoundError):
            self.bank.search_accounts(client_id="CL-NOBODY")

    def test_str(self):
        self.assertEqual(
            str(self.bank),
            "Bank «Test Bank» | Клиентов: 3 | Счетов: 5 | Подозрительных событий: 0",
        )


class ExceptionHierarchyTests(unittest.TestCase):
    def test_everything_is_bank_error(self):
        for exc in (
            AccessDeniedError, AccountNotFoundError, AuthenticationError,
            ClientBlockedError, ClientNotFoundError, OperatingHoursError,
            UnderageClientError, InvalidOperationError,
        ):
            self.assertTrue(issubclass(exc, BankError), exc.__name__)


if __name__ == "__main__":
    unittest.main()
