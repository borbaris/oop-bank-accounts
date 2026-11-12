"""День 2 — продвинутые типы счетов.

Запуск: python3 demo_day2.py
"""

from bank import (
    DEMO_GROWTH_RATES,
    AccountError,
    BankAccount,
    InvestmentAccount,
    Owner,
    PremiumAccount,
    SavingsAccount,
)


def money(value) -> str:
    return f"{value:,.2f}".replace(",", " ")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def attempt(label: str, action) -> None:
    """Выполнить операцию и показать результат или пойманное исключение."""
    try:
        result = action()
        print(f"  ✅ {label}: OK -> {result}")
    except AccountError as exc:
        print(f"  ⛔ {label}: {type(exc).__name__}: {exc}")


def demo_savings() -> None:
    section("1. SavingsAccount — неснижаемый остаток и проценты")
    anna = SavingsAccount(
        Owner("Анна Котова", email="anna@example.com"),
        balance=100_000,
        min_balance=10_000,
        monthly_rate="0.015",
    )
    oleg = SavingsAccount("Олег Ли", balance=500, currency="USD", monthly_rate=0.005)
    print(f"  {anna}")
    print(f"  {oleg}")

    attempt("Анна снимает 50 000", lambda: anna.withdraw(50_000))
    attempt("Анна снимает 45 000 (ниже минимума)", lambda: anna.withdraw(45_000))
    for month in range(1, 4):
        attempt(f"Проценты Анны, месяц {month}", anna.apply_monthly_interest)
    attempt("Проценты Олега", oleg.apply_monthly_interest)
    attempt("Открыть вклад с балансом ниже минимума",
            lambda: SavingsAccount("Х", balance=100, min_balance=1000))
    attempt("Заморозить Олега и начислить проценты",
            lambda: (oleg.freeze(), oleg.apply_monthly_interest()))
    attempt("Закрыть вклад Анны (остаток выплачивается)", anna.close)
    print(f"  {anna}")


def demo_premium() -> None:
    section("2. PremiumAccount — лимиты, овердрафт, комиссия")
    vip = PremiumAccount("Мария Орлова", balance=200_000)
    biz = PremiumAccount(
        "ООО Ромашка", balance=5_000, currency="EUR",
        overdraft_limit=2_000, withdrawal_fee="2.50", withdrawal_limit=50_000,
    )
    print(f"  {vip}")
    print(f"  {biz}")

    attempt("Мария снимает 150 000 (обычному счёту нельзя > 100 000)",
            lambda: vip.withdraw(150_000))
    attempt("Мария снимает 90 000 — уходит в овердрафт", lambda: vip.withdraw(90_000))
    print(f"  В овердрафте: {vip.in_overdraft}, доступно: {vip.available_balance}")
    attempt("Мария снимает ещё 10 000 (за пределом овердрафта)",
            lambda: vip.withdraw(10_000))
    attempt("Мария частично гасит минус: +20 000", lambda: vip.deposit(20_000))
    attempt("ООО снимает 60 000 (лимит 50 000)", lambda: biz.withdraw(60_000))
    attempt("ООО снимает 6 900 (5 000 + овердрафт 2 000 − комиссия)",
            lambda: biz.withdraw(6_900))
    attempt("Закрыть ООО с минусом", biz.close)
    print(f"  {vip}")
    print(f"  {biz}")


def demo_investment() -> None:
    section("3. InvestmentAccount — портфель и прогноз")
    ivan = InvestmentAccount("Иван Петров", balance=1_000_000)
    lena = InvestmentAccount("Лена Ким", balance=3_000, currency="USD")

    attempt("Иван: 500 000 в stocks", lambda: ivan.invest("stocks", 500_000))
    attempt("Иван: 300 000 в bonds", lambda: ivan.invest("bonds", 300_000))
    attempt("Иван: 150 000 в ETF", lambda: ivan.invest("ETF", 150_000))
    attempt("Иван: 10 000 в crypto", lambda: ivan.invest("crypto", 10_000))
    attempt("Иван: -5 в bonds", lambda: ivan.invest("bonds", -5))
    attempt("Иван: 100 000 в etf (свободно 50 000)", lambda: ivan.invest("etf", 100_000))
    attempt("Иван снимает 80 000 (свободно 50 000)", lambda: ivan.withdraw(80_000))
    attempt("Иван продаёт 30 000 bonds", lambda: ivan.sell("bonds", 30_000))
    attempt("Иван продаёт 999 999 stocks", lambda: ivan.sell("stocks", 999_999))
    print(f"  {ivan}")

    print(f"\n  Прогноз на год, ставки: {DEMO_GROWTH_RATES}")
    projection = ivan.project_yearly_growth(DEMO_GROWTH_RATES)
    for asset, growth in projection.by_asset.items():
        held = ivan.portfolio[asset]
        print(f"    {asset.value:>6}: {money(held):>12} -> +{money(growth)}")
    print(f"    Итого: {money(projection.current_value)} -> {money(projection.projected_value)}"
          f" (+{money(projection.total_growth)})")

    attempt("Прогноз без ставки для etf",
            lambda: ivan.project_yearly_growth({"stocks": 0.1, "bonds": 0.04}))
    attempt("Прогноз со ставкой 10 вместо 0.10",
            lambda: ivan.project_yearly_growth({**DEMO_GROWTH_RATES, "stocks": 10}))
    attempt("Лена: 2 000 в etf", lambda: lena.invest("etf", 2_000))
    attempt("Прогноз Лены", lambda: lena.project_yearly_growth(DEMO_GROWTH_RATES).total_growth)
    attempt("Закрыть Лену с активами", lena.close)


def demo_polymorphism() -> None:
    section("4. Полиморфизм — один код, разное поведение")
    accounts: list[BankAccount] = [
        BankAccount("Обычный Клиент", balance=1_000),
        SavingsAccount("Вкладчик", balance=1_000, min_balance=800),
        PremiumAccount("Премиум", balance=1_000, withdrawal_fee=50),
        InvestmentAccount("Инвестор", balance=1_000),
    ]
    accounts[3].invest("bonds", 700)

    for acc in accounts:
        # Один и тот же вызов — каждый класс решает по-своему.
        attempt(f"{type(acc).__name__:>17}.withdraw(500)", lambda acc=acc: acc.withdraw(500))

    print()
    for acc in accounts:
        print(f"  {acc}")

    print()
    for acc in accounts:
        info = acc.get_account_info()
        extra = sorted(set(info) - set(BankAccount("x").get_account_info()))
        print(f"  {info['type']:>17}: доп. поля -> {extra or '—'}")


def main() -> None:
    demo_savings()
    demo_premium()
    demo_investment()
    demo_polymorphism()


if __name__ == "__main__":
    main()
