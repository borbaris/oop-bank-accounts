"""Тесты дня 2. Запуск: python3 -m unittest discover -s tests -v"""

import unittest
from decimal import Decimal

from bank import (
    DEMO_GROWTH_RATES,
    AccountClosedError,
    AccountFrozenError,
    AccountStatus,
    AssetType,
    BankAccount,
    InsufficientFundsError,
    InvalidOperationError,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
    WithdrawalLimitExceededError,
)

D = Decimal


class BankAccountLimitTests(unittest.TestCase):
    def test_standard_withdrawal_limit(self):
        acc = BankAccount("Иван", balance=500_000)
        with self.assertRaises(WithdrawalLimitExceededError) as ctx:
            acc.withdraw("100000.01")
        self.assertEqual(ctx.exception.limit, D("100000.00"))
        self.assertEqual(acc.withdraw(100_000), D("400000.00"))


class SavingsAccountTests(unittest.TestCase):
    def setUp(self):
        self.acc = SavingsAccount(
            "Анна", balance=10_000, min_balance=1_000, monthly_rate="0.02"
        )

    def test_is_bank_account(self):
        self.assertIsInstance(self.acc, BankAccount)

    def test_withdraw_respects_min_balance(self):
        self.assertEqual(self.acc.available_balance, D("9000.00"))
        with self.assertRaises(InsufficientFundsError) as ctx:
            self.acc.withdraw("9000.01")
        self.assertEqual(ctx.exception.available, D("9000.00"))
        self.assertEqual(self.acc.withdraw(9_000), D("1000.00"))
        self.assertEqual(self.acc.available_balance, D("0.00"))

    def test_withdraw_still_checks_limit(self):
        acc = SavingsAccount("Анна", balance=500_000)
        with self.assertRaises(WithdrawalLimitExceededError):
            acc.withdraw(200_000)

    def test_apply_monthly_interest(self):
        self.assertEqual(self.acc.apply_monthly_interest(), D("200.00"))
        self.assertEqual(self.acc.balance, D("10200.00"))
        # сложный процент: второй месяц считается от нового баланса
        self.assertEqual(self.acc.apply_monthly_interest(), D("204.00"))

    def test_interest_is_rounded_to_cents(self):
        acc = SavingsAccount("Анна", balance="333.33", monthly_rate="0.015")
        self.assertEqual(acc.apply_monthly_interest(), D("5.00"))  # 4.99995 -> 5.00

    def test_zero_rate(self):
        acc = SavingsAccount("Анна", balance=100, monthly_rate=0)
        self.assertEqual(acc.apply_monthly_interest(), D("0.00"))

    def test_interest_blocked_on_frozen(self):
        self.acc.freeze()
        with self.assertRaises(AccountFrozenError):
            self.acc.apply_monthly_interest()

    def test_constructor_validation(self):
        bad_kwargs = [
            {"balance": 100, "min_balance": 1_000},
            {"min_balance": -1},
            {"monthly_rate": -0.01},
            {"monthly_rate": 2},
            {"monthly_rate": "abc"},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidOperationError):
                SavingsAccount("Анна", **kwargs)

    def test_close_pays_out_everything(self):
        self.acc.close()
        self.assertIs(self.acc.status, AccountStatus.CLOSED)
        self.assertEqual(self.acc.balance, D("0.00"))
        with self.assertRaises(AccountClosedError):
            self.acc.apply_monthly_interest()

    def test_info_and_str(self):
        info = self.acc.get_account_info()
        self.assertEqual(info["type"], "SavingsAccount")
        self.assertEqual(info["min_balance"], D("1000.00"))
        self.assertEqual(info["monthly_rate"], D("0.02"))
        self.assertEqual(info["available_balance"], D("9000.00"))
        text = str(self.acc)
        self.assertTrue(text.startswith("SavingsAccount | "))
        self.assertIn("Мин. остаток: 1 000.00", text)
        self.assertIn("Ставка: 2%/мес", text)


class PremiumAccountTests(unittest.TestCase):
    def setUp(self):
        self.acc = PremiumAccount(
            "Мария", balance=1_000, overdraft_limit=500, withdrawal_fee=10
        )

    def test_higher_limit_than_standard(self):
        self.assertGreater(PremiumAccount.WITHDRAWAL_LIMIT, BankAccount.WITHDRAWAL_LIMIT)
        acc = PremiumAccount("Мария", balance=300_000)
        self.assertEqual(acc.withdraw(200_000), D("99900.00"))  # 300k - 200k - 100 fee

    def test_custom_limit(self):
        acc = PremiumAccount("Мария", balance=1_000, withdrawal_limit=100)
        with self.assertRaises(WithdrawalLimitExceededError):
            acc.withdraw(101)

    def test_fee_is_charged(self):
        self.assertEqual(self.acc.withdraw(100), D("890.00"))

    def test_overdraft(self):
        self.assertEqual(self.acc.available_balance, D("1500.00"))
        self.assertEqual(self.acc.withdraw(1_490), D("-500.00"))  # 1490 + 10 fee
        self.assertTrue(self.acc.in_overdraft)
        with self.assertRaises(InsufficientFundsError):
            self.acc.withdraw("0.01")
        self.assertEqual(self.acc.balance, D("-500.00"))

    def test_fee_counts_against_available(self):
        with self.assertRaises(InsufficientFundsError) as ctx:
            self.acc.withdraw("1490.01")
        self.assertEqual(ctx.exception.requested, D("1500.01"))
        self.assertEqual(self.acc.balance, D("1000.00"))

    def test_deposit_repays_overdraft(self):
        self.acc.withdraw(1_200)
        self.assertEqual(self.acc.deposit(1_000), D("790.00"))
        self.assertFalse(self.acc.in_overdraft)

    def test_cannot_close_in_overdraft(self):
        self.acc.withdraw(1_200)
        with self.assertRaises(InvalidOperationError):
            self.acc.close()

    def test_defaults_and_validation(self):
        acc = PremiumAccount("Мария")
        self.assertEqual(acc.overdraft_limit, PremiumAccount.DEFAULT_OVERDRAFT_LIMIT)
        self.assertEqual(acc.withdrawal_fee, PremiumAccount.DEFAULT_WITHDRAWAL_FEE)
        self.assertEqual(PremiumAccount("Мария", withdrawal_fee=0).withdrawal_fee, D("0.00"))
        for kwargs in (
            {"overdraft_limit": -1},
            {"withdrawal_fee": -1},
            {"withdrawal_limit": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidOperationError):
                PremiumAccount("Мария", **kwargs)

    def test_frozen(self):
        self.acc.freeze()
        with self.assertRaises(AccountFrozenError):
            self.acc.withdraw(10)

    def test_info_and_str(self):
        info = self.acc.get_account_info()
        self.assertEqual(info["type"], "PremiumAccount")
        self.assertEqual(info["withdrawal_limit"], D("1000000.00"))
        self.assertEqual(info["overdraft_limit"], D("500.00"))
        self.assertEqual(info["withdrawal_fee"], D("10.00"))
        self.assertFalse(info["in_overdraft"])
        self.assertIn("Овердрафт: до 500.00", str(self.acc))
        self.assertIn("Комиссия: 10.00", str(self.acc))


class InvestmentAccountTests(unittest.TestCase):
    def setUp(self):
        self.acc = InvestmentAccount("Иван", balance=10_000)

    def test_invest_moves_cash_into_asset(self):
        self.assertEqual(self.acc.invest("stocks", 4_000), D("4000.00"))
        self.assertEqual(self.acc.invest(AssetType.STOCKS, 1_000), D("5000.00"))
        self.assertEqual(self.acc.balance, D("5000.00"))
        self.assertEqual(self.acc.invested_total, D("5000.00"))
        self.assertEqual(self.acc.total_value, D("10000.00"))

    def test_asset_type_validation(self):
        for bad in ("crypto", "", None, 42):
            with self.subTest(asset=bad), self.assertRaises(InvalidOperationError):
                self.acc.invest(bad, 100)
        self.assertEqual(self.acc.invest(" ETF ", 100), D("100.00"))

    def test_invest_amount_and_funds_validation(self):
        for bad in (0, -1, "abc"):
            with self.subTest(amount=bad), self.assertRaises(InvalidOperationError):
                self.acc.invest("bonds", bad)
        with self.assertRaises(InsufficientFundsError):
            self.acc.invest("bonds", "10000.01")
        self.assertEqual(self.acc.invested_total, D("0.00"))
        self.assertEqual(self.acc.balance, D("10000.00"))

    def test_sell(self):
        self.acc.invest("bonds", 3_000)
        self.assertEqual(self.acc.sell("bonds", 1_000), D("2000.00"))
        self.assertEqual(self.acc.balance, D("8000.00"))
        with self.assertRaises(InsufficientFundsError):
            self.acc.sell("bonds", 2_001)
        with self.assertRaises(InsufficientFundsError):
            self.acc.sell("etf", 1)

    def test_withdraw_only_free_cash(self):
        self.acc.invest("stocks", 8_000)
        with self.assertRaises(InsufficientFundsError) as ctx:
            self.acc.withdraw(3_000)
        self.assertIn("sell()", str(ctx.exception))
        self.assertEqual(self.acc.withdraw(2_000), D("0.00"))

    def test_portfolio_is_a_copy(self):
        self.acc.portfolio[AssetType.STOCKS] = D("1000000")
        self.assertEqual(self.acc.invested_total, D("0.00"))

    def test_project_yearly_growth_with_demo_rates(self):
        self.acc.invest("stocks", 5_000)
        self.acc.invest("bonds", 2_500)
        self.acc.invest("etf", 2_000)
        p = self.acc.project_yearly_growth(DEMO_GROWTH_RATES)
        self.assertEqual(
            p.by_asset,
            {AssetType.STOCKS: D("500.00"), AssetType.BONDS: D("100.00"), AssetType.ETF: D("140.00")},
        )
        self.assertEqual(p.total_growth, D("740.00"))
        self.assertEqual(p.current_value, D("9500.00"))
        self.assertEqual(p.projected_value, D("10240.00"))
        # прогноз не меняет портфель
        self.assertEqual(self.acc.invested_total, D("9500.00"))

    def test_growth_skips_empty_assets_and_accepts_negative_rates(self):
        self.acc.invest("bonds", 1_000)
        p = self.acc.project_yearly_growth({"bonds": -0.05})
        self.assertEqual(p.by_asset, {AssetType.BONDS: D("-50.00")})
        self.assertEqual(p.projected_value, D("950.00"))

    def test_growth_on_empty_portfolio(self):
        p = self.acc.project_yearly_growth({})
        self.assertEqual(p.total_growth, D("0.00"))
        self.assertEqual(p.by_asset, {})

    def test_growth_rates_validation(self):
        self.acc.invest("stocks", 1_000)
        bad_rates = [
            {"bonds": 0.04},                      # нет ставки для stocks
            {"stocks": 0.1, "crypto": 0.5},       # неизвестный актив
            {"stocks": 10},                       # 1000% — похоже на опечатку
            {"stocks": -1.5},                     # хуже -100% не бывает
            {"stocks": "abc"},
            {"stocks": 0.1, "STOCKS": 0.2},       # дубль после нормализации
            [("stocks", 0.1)],                    # не словарь
        ]
        for rates in bad_rates:
            with self.subTest(rates=rates), self.assertRaises(InvalidOperationError):
                self.acc.project_yearly_growth(rates)

    def test_frozen_blocks_trading(self):
        self.acc.invest("etf", 1_000)
        self.acc.freeze()
        for action in (
            lambda: self.acc.invest("etf", 1),
            lambda: self.acc.sell("etf", 1),
            lambda: self.acc.withdraw(1),
        ):
            with self.assertRaises(AccountFrozenError):
                action()
        # прогноз — только чтение, разрешён
        self.assertEqual(self.acc.project_yearly_growth({"etf": 0.07}).total_growth, D("70.00"))

    def test_close_requires_empty_portfolio(self):
        self.acc.invest("etf", 1_000)
        self.acc.withdraw(9_000)
        with self.assertRaises(InvalidOperationError):
            self.acc.close()
        self.acc.sell("etf", 1_000)
        self.acc.withdraw(1_000)
        self.acc.close()
        self.assertIs(self.acc.status, AccountStatus.CLOSED)

    def test_info_and_str(self):
        self.acc.invest("stocks", 1_500)
        info = self.acc.get_account_info()
        self.assertEqual(info["type"], "InvestmentAccount")
        self.assertEqual(info["portfolio"], {"stocks": D("1500.00"), "bonds": D("0.00"), "etf": D("0.00")})
        self.assertEqual(info["invested_total"], D("1500.00"))
        self.assertEqual(info["total_value"], D("10000.00"))
        text = str(self.acc)
        self.assertIn("Портфель: stocks 1 500.00", text)
        self.assertNotIn("bonds", text)
        self.assertIn("Всего: 10 000.00 RUB", text)


class PolymorphismTests(unittest.TestCase):
    def test_each_class_overrides_required_methods(self):
        for cls in (SavingsAccount, PremiumAccount, InvestmentAccount):
            for name in ("withdraw", "get_account_info", "__str__"):
                with self.subTest(cls=cls.__name__, method=name):
                    self.assertIn(name, cls.__dict__)

    def test_same_call_different_behaviour(self):
        accounts = [
            BankAccount("A", balance=1_000),
            SavingsAccount("B", balance=1_000, min_balance=800),
            PremiumAccount("C", balance=1_000, withdrawal_fee=50),
            InvestmentAccount("D", balance=1_000),
        ]
        accounts[3].invest("bonds", 700)

        results = []
        for acc in accounts:
            try:
                results.append(acc.withdraw(500))
            except InsufficientFundsError:
                results.append("insufficient")

        self.assertEqual(results, [D("500.00"), "insufficient", D("450.00"), "insufficient"])
        self.assertEqual(
            [str(a).split(" | ")[0] for a in accounts],
            ["BankAccount", "SavingsAccount", "PremiumAccount", "InvestmentAccount"],
        )


if __name__ == "__main__":
    unittest.main()
