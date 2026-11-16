from datetime import datetime

from bank import Bank, BankError, Client


class DemoClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def money(value) -> str:
    return f"{value:,.2f}".replace(",", " ")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def attempt(label: str, action) -> None:
    try:
        result = action()
        shown = "" if result is None else f" -> {result}"
        print(f"  ✅ {label}: OK{shown}")
    except (BankError, ValueError, TypeError) as exc:
        print(f"  ⛔ {label}: {type(exc).__name__}: {exc}")


def main() -> None:
    clock = DemoClock(datetime(2026, 11, 12, 14, 30))
    bank = Bank("Python Bank", clock=clock)

    section("1. Клиенты")
    anna = Client("Анна Котова", "1990-04-15", phone="+7 (999) 111-22-33",
                  email="anna@example.com", client_id="CL-ANNA")
    oleg = Client("Олег Ли", "1985-12-01", phone="+7 912 444 55 66", client_id="CL-OLEG")
    maria = Client("Мария Орлова", "2000-07-30", phone="89035556677",
                   email="maria@example.com", client_id="CL-MARIA")
    for client in (anna, oleg, maria):
        print(f"  {client} | возраст {client.age} | контакты {client.contacts}")

    attempt("Клиент 16 лет", lambda: Client("Петя Юный", "2010-01-01", phone="+79990000000"))
    attempt("Плохой телефон", lambda: Client("Иван", "1990-01-01", phone="123"))

    section("2. Регистрация в банке")
    attempt("Анна", lambda: bank.add_client(anna, "anna-secret"))
    attempt("Олег", lambda: bank.add_client(oleg, "oleg-pass-1"))
    attempt("Мария", lambda: bank.add_client(maria, "maria2000"))
    attempt("Анна ещё раз", lambda: bank.add_client(anna, "anna-secret"))
    attempt("Короткий пароль", lambda: bank.add_client(
        Client("Ник", "1999-09-09", phone="+79991112233"), "123"))

    section("3. Вход и блокировка после 3 ошибок")
    attempt("Анна, верный пароль", lambda: bank.authenticate_client("CL-ANNA", "anna-secret"))
    attempt("Олег, верный пароль", lambda: bank.authenticate_client("CL-OLEG", "oleg-pass-1"))
    for i in range(1, 4):
        attempt(f"Мария, неверный пароль #{i}",
                lambda: bank.authenticate_client("CL-MARIA", "wrong"))
    attempt("Мария, уже верный пароль", lambda: bank.authenticate_client("CL-MARIA", "maria2000"))
    attempt("Несуществующий клиент", lambda: bank.authenticate_client("CL-HACKER", "123456"))

    section("4. Открытие счетов")
    a1 = bank.open_account("CL-ANNA", "bank", balance=40_000)
    a2 = bank.open_account("CL-ANNA", "savings", balance=500_000, min_balance=50_000,
                           monthly_rate="0.01")
    a3 = bank.open_account("CL-ANNA", "investment", balance=2_000, currency="USD")
    a3.invest("etf", 1_500)
    o1 = bank.open_account("CL-OLEG", "premium", balance=300_000)
    o2 = bank.open_account("CL-OLEG", "bank", balance=1_000, currency="USD")
    for acc in (a1, a2, a3, o1, o2):
        print(f"  {acc}")
    attempt("Мария (заблокирована) открывает счёт", lambda: bank.open_account("CL-MARIA"))
    bank.logout("CL-OLEG")
    attempt("Олег после выхода открывает счёт", lambda: bank.open_account("CL-OLEG"))
    attempt("Неизвестный тип счёта",
            lambda: bank.open_account("CL-ANNA", "crypto"))
    print(f"  Счета Анны: {anna.account_numbers}")

    section("5. Заморозка, закрытие, чужие счета")
    bank.authenticate_client("CL-OLEG", "oleg-pass-1")
    attempt("Заморозить счёт Олега", lambda: bank.freeze_account(o1.account_number))
    attempt("Снять с замороженного", lambda: o1.withdraw(100))
    attempt("Разморозить", lambda: bank.unfreeze_account(o1.account_number))
    attempt("Анна закрывает счёт Олега", lambda: bank.close_account("CL-ANNA", o2.account_number))
    attempt("Анна закрывает свой счёт с деньгами",
            lambda: bank.close_account("CL-ANNA", a1.account_number))
    attempt("Анна снимает всё", lambda: a1.withdraw(a1.balance))
    attempt("Анна закрывает пустой счёт", lambda: bank.close_account("CL-ANNA", a1.account_number))
    attempt("Несуществующий счёт", lambda: bank.freeze_account("NOPE0000"))

    section("6. Ночной запрет 00:00–05:00")
    clock.now = datetime(2026, 11, 13, 2, 30)
    attempt("02:30 — открыть счёт", lambda: bank.open_account("CL-ANNA"))
    attempt("02:30 — заморозить (защитное действие, можно)",
            lambda: bank.freeze_account(a2.account_number))
    attempt("02:30 — разморозить", lambda: bank.unfreeze_account(a2.account_number))
    clock.now = datetime(2026, 11, 13, 5, 0)
    clock.now = datetime(2026, 11, 13, 2, 30)
    attempt("02:30 — пополнить напрямую a3.deposit(100)", lambda: a3.deposit(100))
    attempt("02:30 — проценты по вкладу", a2.apply_monthly_interest)
    clock.now = datetime(2026, 11, 13, 5, 0)
    attempt("05:00 — разморозить", lambda: bank.unfreeze_account(a2.account_number))

    section("7. Заблокированный клиент и старый объект счёта")
    for i in range(1, 4):
        attempt(f"Олег, неверный пароль #{i}",
                lambda: bank.authenticate_client("CL-OLEG", "wrong"))
    attempt("Олег снимает через сохранённый o1", lambda: o1.withdraw(1_000))
    attempt("Олег пополняет o2", lambda: o2.deposit(10))
    attempt("Разблокировать Олега", lambda: bank.unblock_client("CL-OLEG"))

    section("8. Закрытие вклада с остатком")
    a4 = bank.open_account("CL-ANNA", "bank")
    attempt("Закрыть вклад без transfer_to", lambda: bank.close_account("CL-ANNA", a2.account_number))
    print(f"  Вклад после попытки: {money(a2.balance)} — деньги на месте")
    attempt("Остаток на счёт в USD", lambda: bank.close_account(
        "CL-ANNA", a2.account_number, transfer_to=a3.account_number))
    attempt("Закрыть вклад, остаток → новый счёт", lambda: bank.close_account(
        "CL-ANNA", a2.account_number, transfer_to=a4.account_number))
    print(f"  Новый счёт: {a4}")

    section("9. Поиск счетов")
    queries = {
        "все счета Анны": {"client_id": "CL-ANNA"},
        "в долларах": {"currency": "USD"},
        "закрытые": {"status": "closed"},
        "premium": {"account_type": "premium"},
        "баланс от 100 000": {"min_balance": 100_000},
    }
    for label, filters in queries.items():
        found = bank.search_accounts(**filters)
        numbers = ", ".join(f"{type(a).__name__} {a.masked_number}" for a in found) or "—"
        print(f"  {label:>20}: {numbers}")

    section("10. Итоги банка")
    for currency, total in bank.get_total_balance().items():
        print(f"  Всего в {currency}: {money(total)}")
    for currency in ("RUB", "USD"):
        print(f"  Рейтинг клиентов ({currency}):")
        for place, (client, total) in enumerate(bank.get_clients_ranking(currency), start=1):
            print(f"    {place}. {client.full_name} — {money(total)}")

    section("11. Журнал подозрительных действий")
    for event in bank.suspicious_activities:
        print(f"  {event}")

    section("12. Разблокировка сотрудником")
    attempt("Разблокировать Марию", lambda: bank.unblock_client("CL-MARIA"))
    attempt("Мария входит", lambda: bank.authenticate_client("CL-MARIA", "maria2000"))
    print(f"\n  {bank}")


if __name__ == "__main__":
    main()
