"""Тесты дня 1. Запуск: python3 -m unittest discover -s tests -v"""

import unittest
from decimal import Decimal

from bank import (
    AbstractAccount,
    AccountClosedError,
    AccountError,
    AccountFrozenError,
    AccountStatus,
    AccountStatusError,
    BankAccount,
    Currency,
    InsufficientFundsError,
    InvalidOperationError,
    Owner,
)


class AbstractAccountTests(unittest.TestCase):
    def test_cannot_instantiate_abstract_class(self):
        with self.assertRaises(TypeError):
            AbstractAccount("ABCD1234", "Иван")  # type: ignore[abstract]

    def test_subclass_without_all_abstract_methods_fails(self):
        class Partial(AbstractAccount):
            def deposit(self, amount):
                return Decimal(0)

        with self.assertRaises(TypeError):
            Partial("ABCD1234", "Иван")  # type: ignore[abstract]

    def test_bank_account_is_abstract_account(self):
        self.assertIsInstance(BankAccount("Иван"), AbstractAccount)


class CreationTests(unittest.TestCase):
    def test_defaults(self):
        acc = BankAccount("Иван Петров")
        self.assertEqual(acc.balance, Decimal("0.00"))
        self.assertIs(acc.currency, Currency.RUB)
        self.assertIs(acc.status, AccountStatus.ACTIVE)
        self.assertEqual(acc.owner, Owner("Иван Петров"))

    def test_auto_generated_number_is_short_uuid(self):
        acc = BankAccount("Иван")
        self.assertRegex(acc.account_number, r"^[0-9A-F]{12}$")

    def test_auto_generated_numbers_are_unique(self):
        numbers = {BankAccount("Иван").account_number for _ in range(1000)}
        self.assertEqual(len(numbers), 1000)

    def test_explicit_number_is_normalized(self):
        acc = BankAccount("Иван", account_number=" 4081 7810 abcd ")
        self.assertEqual(acc.account_number, "40817810ABCD")

    def test_invalid_account_numbers(self):
        for bad in ["", "123", "ABC-1234", "№12345", "X" * 35]:
            with self.subTest(number=bad), self.assertRaises(ValueError):
                BankAccount("Иван", account_number=bad)
        with self.assertRaises(TypeError):
            BankAccount("Иван", account_number=12345678)

    def test_owner_validation(self):
        self.assertEqual(BankAccount("  Иван   Петров ").owner.full_name, "Иван Петров")
        for bad in ["", "   "]:
            with self.subTest(owner=bad), self.assertRaises(ValueError):
                BankAccount(bad)
        with self.assertRaises(TypeError):
            BankAccount(None)
        with self.assertRaises(ValueError):
            Owner("Иван", email="not-an-email")

    def test_currency_parsing(self):
        self.assertIs(BankAccount("Иван", currency=" eur ").currency, Currency.EUR)
        for code in ["RUB", "USD", "EUR", "KZT", "CNY"]:
            self.assertEqual(BankAccount("Иван", currency=code).currency, code)
        for bad in ["GBP", "", 840]:
            with self.subTest(currency=bad), self.assertRaises(ValueError):
                BankAccount("Иван", currency=bad)

    def test_status_parsing(self):
        self.assertIs(BankAccount("Иван", status="FROZEN").status, AccountStatus.FROZEN)
        with self.assertRaises(ValueError):
            BankAccount("Иван", status="deleted")

    def test_initial_balance_validation(self):
        self.assertEqual(BankAccount("Иван", balance="100.5").balance, Decimal("100.50"))
        with self.assertRaises(InvalidOperationError):
            BankAccount("Иван", balance=-1)
        with self.assertRaises(InvalidOperationError):
            BankAccount("Иван", balance="abc")

    def test_closed_account_with_money_is_rejected(self):
        with self.assertRaises(InvalidOperationError):
            BankAccount("Иван", balance=10, status=AccountStatus.CLOSED)
        self.assertIs(BankAccount("Иван", status="closed").status, AccountStatus.CLOSED)

    def test_balance_and_status_are_read_only(self):
        acc = BankAccount("Иван", balance=100)
        with self.assertRaises(AttributeError):
            acc.balance = Decimal("1000000")  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            acc.status = AccountStatus.CLOSED  # type: ignore[misc]


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.acc = BankAccount("Иван", balance=100, currency="USD")

    def test_deposit(self):
        self.assertEqual(self.acc.deposit(50), Decimal("150.00"))
        self.assertEqual(self.acc.deposit("0.99"), Decimal("150.99"))
        self.assertEqual(self.acc.deposit(Decimal("0.01")), Decimal("151.00"))

    def test_float_amounts_have_no_binary_noise(self):
        acc = BankAccount("Иван")
        acc.deposit(0.1)
        acc.deposit(0.2)
        self.assertEqual(acc.balance, Decimal("0.30"))

    def test_withdraw(self):
        self.assertEqual(self.acc.withdraw(30), Decimal("70.00"))
        self.assertEqual(self.acc.withdraw(70), Decimal("0.00"))

    def test_insufficient_funds(self):
        with self.assertRaises(InsufficientFundsError) as ctx:
            self.acc.withdraw("100.01")
        self.assertEqual(ctx.exception.available, Decimal("100.00"))
        self.assertEqual(ctx.exception.requested, Decimal("100.01"))
        self.assertEqual(ctx.exception.currency, "USD")
        self.assertEqual(self.acc.balance, Decimal("100.00"))

    def test_invalid_amounts_are_rejected_and_balance_unchanged(self):
        bad_amounts = [
            0, -1, "-5", "0.00", "10.005", 0.001,
            True, None, [10], "abc", "", float("nan"), float("inf"), "Infinity",
        ]
        for op in (self.acc.deposit, self.acc.withdraw):
            for amount in bad_amounts:
                with self.subTest(op=op.__name__, amount=amount):
                    with self.assertRaises(InvalidOperationError):
                        op(amount)
                    self.assertEqual(self.acc.balance, Decimal("100.00"))


class StatusTests(unittest.TestCase):
    def test_frozen_account_blocks_money_operations(self):
        acc = BankAccount("Иван", balance=100, status=AccountStatus.FROZEN)
        with self.assertRaises(AccountFrozenError):
            acc.deposit(10)
        with self.assertRaises(AccountFrozenError):
            acc.withdraw(10)
        self.assertEqual(acc.balance, Decimal("100.00"))

    def test_status_checked_before_amount(self):
        acc = BankAccount("Иван", status="frozen")
        with self.assertRaises(AccountFrozenError):
            acc.deposit(-1)

    def test_freeze_unfreeze_cycle(self):
        acc = BankAccount("Иван", balance=100)
        acc.freeze()
        self.assertIs(acc.status, AccountStatus.FROZEN)
        with self.assertRaises(InvalidOperationError):
            acc.freeze()
        acc.unfreeze()
        self.assertIs(acc.status, AccountStatus.ACTIVE)
        with self.assertRaises(InvalidOperationError):
            acc.unfreeze()
        self.assertEqual(acc.deposit(1), Decimal("101.00"))

    def test_close_requires_zero_balance_and_active_status(self):
        acc = BankAccount("Иван", balance=100)
        with self.assertRaises(InvalidOperationError):
            acc.close()
        acc.freeze()
        with self.assertRaises(AccountFrozenError):
            acc.close()
        acc.unfreeze()
        acc.withdraw(100)
        acc.close()
        self.assertIs(acc.status, AccountStatus.CLOSED)

    def test_closed_account_is_terminal(self):
        acc = BankAccount("Иван", status="closed")
        for action in (
            lambda: acc.deposit(1),
            lambda: acc.withdraw(1),
            acc.freeze,
            acc.unfreeze,
            acc.close,
        ):
            with self.assertRaises(AccountClosedError):
                action()
        # чтение информации о закрытом счёте разрешено
        self.assertEqual(acc.get_account_info()["status"], "closed")


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.acc = BankAccount(
            Owner("Анна Котова", email="Anna@Example.com"),
            balance="1234567.8",
            currency="KZT",
            account_number="40817810099910004312",
        )

    def test_str(self):
        text = str(self.acc)
        self.assertEqual(
            text,
            "BankAccount | Клиент: Анна Котова | Счёт: ****4312 | "
            "Статус: active | Баланс: 1 234 567.80 KZT",
        )
        self.assertNotIn("40817810099910004312", text)

    def test_str_uses_subclass_name(self):
        class SavingsAccount(BankAccount):
            pass

        self.assertTrue(str(SavingsAccount("Иван")).startswith("SavingsAccount | "))

    def test_get_account_info(self):
        self.assertEqual(
            self.acc.get_account_info(),
            {
                "type": "BankAccount",
                "account_number": "40817810099910004312",
                "owner": "Анна Котова",
                "owner_email": "anna@example.com",
                "status": "active",
                "balance": Decimal("1234567.80"),
                "currency": "KZT",
                "withdrawal_limit": Decimal("100000.00"),
            },
        )


class ExceptionHierarchyTests(unittest.TestCase):
    def test_hierarchy(self):
        self.assertTrue(issubclass(AccountFrozenError, AccountStatusError))
        self.assertTrue(issubclass(AccountClosedError, AccountStatusError))
        self.assertTrue(issubclass(InsufficientFundsError, InvalidOperationError))
        for exc in (AccountStatusError, InvalidOperationError):
            self.assertTrue(issubclass(exc, AccountError))


if __name__ == "__main__":
    unittest.main()
