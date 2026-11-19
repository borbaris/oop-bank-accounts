from datetime import datetime, timedelta
from pathlib import Path

from bank import (
    AuditLevel,
    Bank,
    Client,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    client_risk_profile,
    error_statistics,
    suspicious_operations,
)

AUDIT_FILE = Path("demo_audit.jsonl")
EXTERNAL = "DE89370400440532013000"


class DemoClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    AUDIT_FILE.unlink(missing_ok=True)
    clock = DemoClock(datetime(2026, 11, 18, 12, 0))
    bank = Bank("Python Bank", clock=clock, audit_path=AUDIT_FILE)
    queue = TransactionQueue(bank)
    processor = TransactionProcessor(bank)

    for name, cid, pwd in (("Анна Котова", "CL-ANNA", "anna-secret"),
                           ("Олег Ли", "CL-OLEG", "oleg-pass-1")):
        bank.add_client(Client(name, "1990-01-01", phone="+79991112233", client_id=cid), pwd)
        bank.authenticate_client(cid, pwd)
    a_prem = bank.open_account("CL-ANNA", "premium", balance=1_000_000)
    a_rub = bank.open_account("CL-ANNA", balance=20_000)
    o_rub = bank.open_account("CL-OLEG", balance=10_000)

    def pay(label: str, client: str, sender, receiver, amount, **options):
        number = receiver if isinstance(receiver, str) else receiver.account_number
        tx = bank.create_transfer(client, sender.account_number, number, amount, **options)
        if tx.status is TransactionStatus.PENDING:
            queue.add(tx)
            processor.process_queue(queue)
        risk = bank.get_risk_assessment(tx.transaction_id)
        print(f"  {label:<34} {tx.status.value:<9} {risk}")
        return tx

    section("1. Обычные транзакции")
    pay("Анна → Олегу 1 500 (впервые)", "CL-ANNA", a_rub, o_rub, 1_500)
    clock.tick(15)
    pay("Анна → Олегу 2 000 (уже платила)", "CL-ANNA", a_rub, o_rub, 2_000)
    clock.tick(15)
    pay("Анна → себе 5 000", "CL-ANNA", a_rub, a_prem, 5_000)
    pay("Олег → Анне 300", "CL-OLEG", o_rub, a_rub, 300)

    section("2. Подозрительные: один фактор — пропуск с WARNING")
    clock.tick(15)
    pay("крупно себе 150 000", "CL-ANNA", a_prem, a_rub, 150_000)
    clock.tick(15)
    pay("крупно Олегу 120 000 (знакомый)", "CL-ANNA", a_prem, o_rub, 120_000)
    clock.tick(15)
    pay("ночной платёж на 03:00", "CL-ANNA", a_rub, a_prem, 500,
        scheduled_at=datetime(2026, 11, 19, 3, 0))

    section("3. Дробление: 5 мелких новым получателям за минуту")
    clock.tick(15)
    for i in range(5):
        pay(f"#{i + 1} → внешний ****810{i}", "CL-ANNA", a_rub, f"GB29NWBK6016133192810{i}", 200)

    section("4. Опасные комбинации — банк блокирует")
    clock.tick(15)
    pay("крупно + ночь + новый", "CL-ANNA", a_prem, EXTERNAL, 300_000,
        scheduled_at=datetime(2026, 11, 19, 2, 0))
    for i in range(4):
        pay(f"Олег → Анне 10 (#{i + 1})", "CL-OLEG", o_rub, a_rub, 10)
    pay("Олег: крупно + часто + новый", "CL-OLEG", o_rub, EXTERNAL, 100_000)
    print(f"  Баланс Premium Анны: {a_prem.balance}, Олега: {o_rub.balance} — не тронуты")

    section("5. Отчёт: подозрительные операции")
    for tx, risk in suspicious_operations(bank):
        print(f"  {risk.level.name:<6} {tx.amount:>10} {tx.currency} | {tx.status.value:<9} | "
              f"{'; '.join(risk.reasons)}")

    section("6. Отчёт: риск-профиль клиента")
    for cid in ("CL-ANNA", "CL-OLEG"):
        p = client_risk_profile(bank, cid)
        by_risk = ", ".join(f"{lvl.name} {n}" for lvl, n in p.by_risk.items())
        print(f"  {p.full_name}: риск {p.level.name} | транзакций {p.transactions} ({by_risk}) | "
              f"заблокировано {p.blocked_operations} | подозрительных событий {p.suspicious_events}")

    section("7. Отчёт: статистика ошибок")
    stats = error_statistics(bank, processor)
    print(f"  Транзакций {stats.total_transactions}: выполнено {stats.completed}, "
          f"отказов {stats.failed} ({stats.failure_rate}%), ждут в очереди {len(queue)}")
    print(f"  Ошибки по типам: {stats.errors_by_type}")
    print("  Записи аудита по уровням: "
          + ", ".join(f"{lvl.name} {n}" for lvl, n in stats.audit_by_level.items()))

    section("8. Аудит: фильтр ≥ ERROR и файл")
    for entry in bank.audit.filter(min_level=AuditLevel.ERROR):
        print(f"  {entry}")
    lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    print(f"\n  В файле {AUDIT_FILE}: {len(lines)} строк (в памяти {len(bank.audit)}). Последняя:")
    print(f"  {lines[-1]}")


if __name__ == "__main__":
    main()
