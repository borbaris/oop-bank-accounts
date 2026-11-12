from bank import (
    AccountError,
    AccountStatus,
    BankAccount,
    Currency,
    Owner,
)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def attempt(label: str, action) -> None:
    """Выполнить операцию и показать результат или пойманное исключение."""
    try:
        result = action()
        print(f"  ✅ {label}: OK -> {result}")
    except AccountError as exc:
        print(f"  ⛔ {label}: {type(exc).__name__}: {exc}")


def main() -> None:
    section("1. Создание счетов")
    alice = BankAccount(
        owner=Owner("Ваня Анисимов, ремоут легенда ", email="legenda@remote.com.ph"),
        balance=1000,
        currency="usd",
    )
    bob = BankAccount(
        owner="Борис Иванов",
        balance="50000.00",
        currency=Currency.RUB,
        account_number="67676767676767676767",
        status=AccountStatus.FROZEN,
    )
    print(f"  {alice}")
    print(f"  {bob}")
    print(f"  Номер Вани сгенерирован автоматически: {alice.account_number}")

    section("2. Операции над замороженным счётом")
    attempt("Пополнение Бориса на 100", lambda: bob.deposit(100))
    attempt("Снятие у Бориса 100", lambda: bob.withdraw(100))
    print(f"  Баланс Бориса не изменился: {bob.balance}")

    section("3. Валидные пополнение и снятие")
    attempt("Пополнение Вани на 250.50", lambda: alice.deposit("250.50"))
    attempt("Снятие у Вани 100", lambda: alice.withdraw(100))
    attempt("Пополнение 0.1, затем 0.2 (Decimal, без ошибок float)",
            lambda: (alice.deposit(0.1), alice.deposit(0.2))[-1])
    print(f"  {alice}")

    section("4. Некорректные операции")
    attempt("Снятие больше баланса", lambda: alice.withdraw(5_000))
    attempt("Отрицательная сумма", lambda: alice.deposit(-50))
    attempt("Нулевая сумма", lambda: alice.withdraw(0))
    attempt("Три знака после запятой", lambda: alice.deposit("10.005"))
    attempt("Сумма-строка не число", lambda: alice.deposit("сто рублей"))
    attempt("Сумма True", lambda: alice.deposit(True))

    section("5. Жизненный цикл статуса")
    attempt("Разморозка Бориса", bob.unfreeze)
    attempt("Теперь пополнение Бориса на 100", lambda: bob.deposit(100))
    attempt("Закрытие Бориса с ненулевым балансом", bob.close)
    attempt("Снятие всего баланса Бориса", lambda: bob.withdraw(bob.balance))
    attempt("Закрытие Бориса", bob.close)
    attempt("Пополнение закрытого счёта", lambda: bob.deposit(1))
    attempt("Заморозка закрытого счёта", bob.freeze)
    print(f"  {bob}")

    section("6. get_account_info()")
    for key, value in alice.get_account_info().items():
        print(f"  {key:>15}: {value}")

    section("7. Валидация входных данных конструктора")
    for label, kwargs in [
        ("Неизвестная валюта", {"owner": "X", "currency": "GBP"}),
        ("Пустой владелец", {"owner": "   "}),
        ("Отрицательный баланс", {"owner": "X", "balance": -1}),
        ("Короткий номер", {"owner": "X", "account_number": "12"}),
        ("Неизвестный статус", {"owner": "X", "status": "deleted"}),
    ]:
        try:
            BankAccount(**kwargs)
            print(f"  ❗ {label}: счёт создан, а не должен был")
        except (AccountError, ValueError, TypeError) as exc:
            print(f"  ⛔ {label}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
