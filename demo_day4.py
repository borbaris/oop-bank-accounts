from datetime import datetime, timedelta
from decimal import Decimal

from bank import (
    Bank,
    BankError,
    Client,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransientTransactionError,
)

EXTERNAL = "DE89370400440532013000"


class DemoClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class FlakyGateway:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, tx: Transaction) -> None:
        self.calls += 1
        if self.calls == 1:
            raise TransientTransactionError("внешний банк не ответил (таймаут)")


def money(value: Decimal) -> str:
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
    clock = DemoClock(datetime(2026, 11, 16, 10, 0))
    bank = Bank("Python Bank", clock=clock)
    queue = TransactionQueue(bank)
    processor = TransactionProcessor(bank, external_gateway=FlakyGateway())

    section("1. Клиенты и счета")
    bank.add_client(Client("Анна Котова", "1990-04-15", phone="+79991112233", client_id="CL-ANNA"),
                    "anna-secret")
    bank.add_client(Client("Олег Ли", "1985-12-01", phone="+79124445566", client_id="CL-OLEG"),
                    "oleg-pass-1")
    bank.authenticate_client("CL-ANNA", "anna-secret")
    bank.authenticate_client("CL-OLEG", "oleg-pass-1")

    a_rub = bank.open_account("CL-ANNA", balance=20_000)
    a_usd = bank.open_account("CL-ANNA", balance=500, currency="USD")
    a_sav = bank.open_account("CL-ANNA", "savings", balance=10_000, min_balance=5_000)
    o_prem = bank.open_account("CL-OLEG", "premium", balance=1_000)
    o_rub = bank.open_account("CL-OLEG")
    o_usd = bank.open_account("CL-OLEG", currency="USD")
    accounts = {"Анна RUB": a_rub, "Анна USD": a_usd, "Анна вклад": a_sav,
                "Олег Premium": o_prem, "Олег RUB": o_rub, "Олег USD": o_usd}
    for label, acc in accounts.items():
        print(f"  {label:>13}: {acc.masked_number} {money(acc.balance)} {acc.currency}")

    section("2. Создаём 10 транзакций")
    evening = datetime(2026, 11, 16, 18, 0)
    night = datetime(2026, 11, 17, 2, 0)
    plan = [
        ("Анне → Олегу, обычный", "CL-ANNA", a_rub, o_rub, 3_000, {}),
        ("во внешний банк (1%)", "CL-ANNA", a_rub, EXTERNAL, 5_000, {}),
        ("USD → RUB, конвертация", "CL-ANNA", a_usd, o_rub, 100, {}),
        ("Premium уходит в минус", "CL-OLEG", o_prem, a_rub, 4_000, {}),
        ("со вклада ниже минимума", "CL-ANNA", a_sav, a_rub, 6_000, {}),
        ("обычный счёт в минус", "CL-ANNA", a_rub, o_rub, 50_000, {"priority": "low"}),
        ("срочный", "CL-ANNA", a_rub, o_prem, 1_000, {"priority": "urgent"}),
        ("отложен на 18:00", "CL-OLEG", o_rub, a_rub, 500, {"scheduled_at": evening}),
        ("будет отменён", "CL-ANNA", a_rub, o_rub, 777, {}),
        ("ночной (02:00)", "CL-ANNA", a_rub, o_usd, 900, {"scheduled_at": night}),
    ]
    txs = []
    for i, (label, client, sender, receiver, amount, options) in enumerate(plan, start=1):
        number = receiver if isinstance(receiver, str) else receiver.account_number
        tx = bank.create_transfer(client, sender.account_number, number, amount,
                                  description=label, **options)
        queue.add(tx)
        txs.append(tx)
        print(f"  #{i:<2} {label:<26} {tx}")

    section("3. Отмена и попытки обойти правила")
    attempt("Олег отменяет чужую #9", lambda: queue.cancel("CL-OLEG", txs[8].transaction_id))
    attempt("Анна отменяет свою #9", lambda: queue.cancel("CL-ANNA", txs[8].transaction_id).status)
    forged = Transaction("internal_transfer", 10_000, "RUB", sender=o_prem.account_number,
                         receiver=a_rub.account_number, initiator_id="CL-OLEG",
                         created_at=clock.now)
    attempt("Самодельная транзакция в очередь", lambda: queue.add(forged))
    attempt("Анна платит с чужого счёта",
            lambda: bank.create_transfer("CL-ANNA", o_prem.account_number, a_rub.account_number, 1))
    print(f"  В очереди: {len(queue)}, готовы сейчас: {len(queue.ready())}, "
          f"отложены: {len(queue.deferred())}")

    def run(title: str) -> None:
        section(title)
        for tx in processor.process_queue(queue):
            print(f"  {tx.description:<26} {tx.status.value:<9}"
                  + (f" → повтор/перенос на {tx.scheduled_at:%d.%m %H:%M}" if not tx.is_final else "")
                  + (f" | {tx.failure_reason}" if tx.failure_reason else ""))

    run("4. Обработка в 10:00 (порядок: приоритет, потом очередь)")
    clock.now = datetime(2026, 11, 16, 10, 5)
    run("5. 10:05 — повтор внешнего перевода")
    clock.now = evening
    run("6. 18:00 — отложенный перевод")
    clock.now = night
    run("7. 02:00 — ночью ничего не списываем, переносим на 05:00")
    clock.now = datetime(2026, 11, 17, 5, 0)
    run("8. 05:00 — банк открылся")

    section("9. Итог по транзакциям")
    for i, tx in enumerate(txs, start=1):
        fee = f" комиссия {tx.fee}" if tx.fee else ""
        moved = (f" | списано {money(tx.debit_amount)}, зачислено {money(tx.credit_amount)}"
                 if tx.debit_amount is not None else "")
        print(f"  #{i:<2} {tx.status.value:<9} попыток {tx.attempts}{fee}{moved}")
    print(f"\n  История #2: {' → '.join(txs[1].history)}")

    section("10. Счета, доход банка, журнал ошибок")
    for label, acc in accounts.items():
        print(f"  {label:>13}: {money(acc.balance)} {acc.currency}")
    def by_currency(totals: dict) -> str:
        return ", ".join(f"{money(v)} {c}" for c, v in totals.items()) or "—"

    print(f"  Комиссии банка: {by_currency(processor.fee_income)}")
    print(f"  Ушло в другие банки: {by_currency(processor.external_outflow)}")
    print("  Журнал ошибок процессора:")
    for error in processor.errors:
        print(f"    {error}")
    print("  Подозрительные события:")
    for event in bank.suspicious_activities:
        print(f"    {event}")


if __name__ == "__main__":
    main()
