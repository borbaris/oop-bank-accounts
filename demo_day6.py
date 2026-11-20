from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from bank import (
    AuditEntry,
    AuditLevel,
    Bank,
    BankAccount,
    BankError,
    Client,
    Currency,
    Direction,
    RiskLevel,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransientTransactionError,
    client_history,
    client_risk_profile,
    error_statistics,
    reconcile,
    suspicious_operations,
    top_clients,
    total_balance,
    transaction_statistics,
)

DAY = date(2026, 11, 20)

# Внешние счета (в других банках).
EXT_UTILITIES = "RU02044525225000000000001"  # ЖКХ
EXT_US_SHOP = "US64SVBKUS6S3300958879"
EXT_SHAKY = "GB29NWBK60161331926819"         # банк, который один раз «моргнёт»
EXT_DOWN = "UA213223130000026007233566001"   # банк, который лежит весь день
EXT_SWISS = "CH9300762011623852957"
EXT_PHONE = "RU77000000000000000000777"


def at(hour: int, minute: int = 0, day: int = 0) -> datetime:
    return datetime.combine(DAY + timedelta(days=day), datetime.min.time()).replace(
        hour=hour, minute=minute
    )


def money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


class DemoClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class ExternalBankGateway:
    """Имитация других банков: часть «не отвечает» заданное число раз."""

    def __init__(self, outages: dict[str, int]) -> None:
        self._failures_left = dict(outages)

    def __call__(self, tx: Transaction) -> None:
        left = self._failures_left.get(tx.receiver, 0)
        if left > 0:
            self._failures_left[tx.receiver] = left - 1
            raise TransientTransactionError("банк получателя не ответил")


@dataclass
class World:
    """Всё, из чего состоит демонстрация. Тест получает тот же объект."""

    bank: Bank
    clock: DemoClock
    queue: TransactionQueue
    processor: TransactionProcessor
    accounts: dict[str, BankAccount]                      # короткое имя -> счёт
    passwords: dict[str, str]
    opening_balance: dict[Currency, Decimal] = field(default_factory=dict)
    rejected: list[tuple[str, BankError | ValueError | TypeError]] = field(default_factory=list)
    numbers: dict[str, int] = field(default_factory=dict)  # id транзакции -> её № в демо
    out: Callable[[str], None] = print

    def account_name(self, number: str) -> str:
        for name, acc in self.accounts.items():
            if acc.account_number == number:
                return name
        return f"внешний ****{number[-4:]}"


# --- инициализация -----------------------------------------------------------

CLIENTS = [
    # id, ФИО, дата рождения, телефон, пароль
    ("CL-ANNA", "Анна Котова", "1990-04-15", "+79991112233", "anna-secret"),
    ("CL-OLEG", "Олег Ли", "1985-12-01", "+79124445566", "oleg-pass-1"),
    ("CL-MARIA", "Мария Орлова", "2000-07-30", "+79035556677", "maria2000"),
    ("CL-IVAN", "Иван Петров", "1978-02-11", "+79161234567", "ivan-1978"),
    ("CL-DINA", "Дина Ахметова", "1995-09-03", "+77012345678", "dina-kz-95"),
    ("CL-SERGEY", "Сергей Ван", "1982-05-21", "+79265554433", "sergey-cny"),
    ("CL-NIKA", "Ника Белова", "2004-01-17", "+79057778899", "nika-2004"),
]

ACCOUNTS = [
    # имя в демо, клиент, тип, параметры
    ("Анна RUB", "CL-ANNA", "bank", {"balance": 150_000}),
    ("Анна вклад", "CL-ANNA", "savings", {"balance": 300_000, "min_balance": 50_000}),
    ("Анна инвест USD", "CL-ANNA", "investment", {"balance": 5_000, "currency": "USD"}),
    ("Олег Premium", "CL-OLEG", "premium", {"balance": 80_000}),
    ("Олег USD", "CL-OLEG", "bank", {"balance": 2_000, "currency": "USD"}),
    ("Мария RUB", "CL-MARIA", "bank", {"balance": 40_000}),
    ("Мария EUR", "CL-MARIA", "bank", {"balance": 1_500, "currency": "EUR"}),
    ("Иван RUB", "CL-IVAN", "bank", {"balance": 25_000}),
    ("Иван вклад", "CL-IVAN", "savings", {"balance": 60_000, "min_balance": 10_000}),
    ("Дина KZT", "CL-DINA", "bank", {"balance": 900_000, "currency": "KZT"}),
    ("Дина RUB", "CL-DINA", "bank", {"balance": 10_000}),
    ("Сергей Premium", "CL-SERGEY", "premium", {"balance": 500_000}),
    ("Сергей CNY", "CL-SERGEY", "bank", {"balance": 20_000, "currency": "CNY"}),
    ("Ника RUB", "CL-NIKA", "bank", {"balance": 3_000}),
]


def build_world(out: Callable[[str], None] = print) -> World:
    clock = DemoClock(at(8, 50))
    bank = Bank("Python Bank", clock=clock)
    gateway = ExternalBankGateway({EXT_SHAKY: 1, EXT_DOWN: 99})
    world = World(
        bank=bank, clock=clock, queue=TransactionQueue(bank),
        processor=TransactionProcessor(bank, external_gateway=gateway),
        accounts={}, passwords={}, out=out,
    )

    for client_id, name, birth, phone, password in CLIENTS:
        bank.add_client(Client(name, birth, phone=phone, client_id=client_id), password)
        bank.authenticate_client(client_id, password)
        world.passwords[client_id] = password

    # Ошибочные регистрации — банк их не пускает.
    for label, make in (
        ("Петя, 16 лет", lambda: Client("Петя Юный", "2010-03-01", phone="+79990000000")),
        ("телефон «123»", lambda: Client("Иван Неверный", "1990-01-01", phone="123")),
    ):
        try:
            make()
        except (BankError, ValueError) as exc:
            world.rejected.append((f"регистрация: {label}", exc))

    for name, client_id, kind, options in ACCOUNTS:
        world.accounts[name] = bank.open_account(client_id, kind, **options)

    # Снимок денег до начала дня — с ним сверимся в конце.
    world.opening_balance = bank.get_total_balance()
    return world


# --- живой лог из журнала аудита --------------------------------------------

EVENT_LABELS = {
    "transaction_queued": "📥 в очередь",
    "transaction_completed": "✅ выполнена",
    "transaction_failed": "🚫 отклонена",
    "transaction_retry": "🔁 сбой, повтор",
    "transaction_deferred": "🌙 перенесена",
    "transaction_cancelled": "↩️  отменена",
}
# Уже видно по строке риска — не дублируем.
HIDDEN_EVENTS = {"high_risk_operation"}


class AuditTail:
    """Печатает новые записи аудита с прошлого вызова (как `tail -f`)."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._printed = 0

    def flush(self) -> None:
        entries = self._world.bank.audit.entries
        for entry in entries[self._printed:]:
            line = self._format(entry)
            if line:
                self._world.out(line)
        self._printed = len(entries)

    def _format(self, e: AuditEntry) -> str | None:
        time = f"{e.timestamp:%H:%M}"
        if e.event == "risk_assessment":
            if e.level is AuditLevel.INFO:
                return None  # низкий риск — норма, не шумим
            icon = "⛔ блок, HIGH" if e.level is AuditLevel.CRITICAL else "⚠️  риск"
            return f"  {time} {icon:<15} {self._tx_label(e)} — {e.message}"
        if e.event in HIDDEN_EVENTS:
            return None
        if e.event in EVENT_LABELS:
            tx = self._world.bank.get_transaction(e.transaction_id)
            details = self._tx_details(tx) if e.event == "transaction_completed" else e.message
            if e.event == "transaction_queued":
                details = f"{money(tx.amount)} {tx.currency}, {tx.priority.name.lower()}"
                if tx.scheduled_at > tx.created_at:
                    details += f", к {tx.scheduled_at:%d.%m %H:%M}"
            return f"  {time} {EVENT_LABELS[e.event]:<15} {self._tx_label(e)} — {details}"
        return f"  {time} 🚨 {e.event:<13} {e.client_id or '—'}: {e.message}"

    def _tx_label(self, e: AuditEntry) -> str:
        tx = self._world.bank.get_transaction(e.transaction_id)
        return f"#{self._world.numbers[tx.transaction_id]:02} {tx.description}"

    def _tx_details(self, tx: Transaction) -> str:
        world = self._world
        sender = world.bank.get_account(tx.sender)
        text = f"{money(tx.debit_amount)} {sender.currency} с «{world.account_name(tx.sender)}»"
        receiver_name = world.account_name(tx.receiver)
        if tx.receiver in {a.account_number for a in world.accounts.values()}:
            receiver = world.bank.get_account(tx.receiver)
            text += f" → {money(tx.credit_amount)} {receiver.currency} на «{receiver_name}»"
        else:
            text += f" → {receiver_name}"
        if tx.fee:
            text += f" (комиссия {money(tx.fee)})"
        if tx.attempts > 1:
            text += f", с {tx.attempts}-й попытки"
        return text


# --- симуляция дня ----------------------------------------------------------

class Simulation:
    def __init__(self, world: World) -> None:
        self.w = world
        self.tail = AuditTail(world)

    def header(self, title: str) -> None:
        self.tail.flush()
        self.w.out(f"\n--- {title} ---")

    def wait_until(self, moment: datetime) -> None:
        self.tail.flush()
        self.w.clock.now = moment

    def pay(
        self, label: str, client_id: str, sender: str, receiver: str,
        amount: Decimal | int | str, *, gap: int = 2, **options,
    ) -> Transaction | None:
        """Клиент создаёт перевод. Ошибка при создании — отказ, транзакции нет."""
        self.tail.flush()
        self.w.clock.now += timedelta(minutes=gap)
        target = self.w.accounts[receiver].account_number if receiver in self.w.accounts else receiver
        try:
            tx = self.w.bank.create_transfer(
                client_id, self.w.accounts[sender].account_number, target, amount,
                description=label, **options,
            )
        except (BankError, ValueError, TypeError) as exc:
            self.reject(label, exc)
            return None
        self.w.numbers[tx.transaction_id] = len(self.w.numbers) + 1
        if tx.status is TransactionStatus.PENDING:  # HIGH риск уже отклонён при создании
            self.w.queue.add(tx)
        self.tail.flush()
        return tx

    def attempt(self, label: str, action: Callable[[], object]) -> None:
        """Любое другое действие, которое может быть отклонено."""
        self.tail.flush()
        try:
            action()
        except (BankError, ValueError, TypeError) as exc:
            self.reject(label, exc)
            return
        self.tail.flush()

    def reject(self, label: str, exc: BankError | ValueError | TypeError) -> None:
        self.w.rejected.append((label, exc))
        self.w.out(f"  {self.w.clock.now:%H:%M} 🚫 не создана    {label} — {type(exc).__name__}: {exc}")
        self.tail.flush()

    def process(self) -> None:
        self.tail.flush()
        self.w.out(f"  {self.w.clock.now:%H:%M} ⚙️  процессор разбирает очередь (готово: "
                   f"{len(self.w.queue.ready())}, ждут своего времени: {len(self.w.queue.deferred())})")
        self.w.processor.process_queue(self.w.queue)
        self.tail.flush()

    def login(self, client_id: str) -> None:
        self.w.bank.authenticate_client(client_id, self.w.passwords[client_id])


def simulate(world: World) -> None:
    s = Simulation(world)
    bank, out = world.bank, world.out

    # 09:00 ─ обычное утро
    s.wait_until(at(9, 0))
    s.header("09:00 Утро: зарплаты, бытовые платежи, обмен валют")
    s.pay("зарплата Анне", "CL-SERGEY", "Сергей Premium", "Анна RUB", 60_000)
    s.pay("зарплата Ивану", "CL-SERGEY", "Сергей Premium", "Иван RUB", 45_000)
    s.pay("зарплата Нике", "CL-SERGEY", "Сергей Premium", "Ника RUB", 30_000)
    s.pay("Анна вернула долг Марии", "CL-ANNA", "Анна RUB", "Мария RUB", 2_500)
    s.pay("Мария → Анне в USD", "CL-MARIA", "Мария EUR", "Анна инвест USD", 200)
    s.pay("Дина меняет тенге на рубли", "CL-DINA", "Дина KZT", "Дина RUB", 90_000)
    s.pay("Олег: покупка в US-магазине", "CL-OLEG", "Олег USD", EXT_US_SHOP, 300)
    s.pay("Иван: ЖКХ", "CL-IVAN", "Иван RUB", EXT_UTILITIES, 4_500)
    s.pay("Ника → Анне за билеты", "CL-NIKA", "Ника RUB", "Анна RUB", 1_000)
    s.pay("Анна со вклада себе", "CL-ANNA", "Анна вклад", "Анна RUB", 20_000)
    s.pay("Анна → Олегу, срочно", "CL-ANNA", "Анна RUB", "Олег Premium", 5_000, priority="urgent")
    s.pay("Ника → Ивану, не к спеху", "CL-NIKA", "Ника RUB", "Иван RUB", 400, priority="low")
    s.wait_until(at(9, 30))
    s.process()

    # 11:00 ─ ошибки
    s.wait_until(at(11, 0))
    s.header("11:00 Ошибки: неверные данные и нарушения правил")
    s.pay("Иван: сумма −500", "CL-IVAN", "Иван RUB", "Мария RUB", -500)
    s.pay("Иван: сумма 10.555", "CL-IVAN", "Иван RUB", "Мария RUB", "10.555")
    s.pay("Мария сама себе на тот же счёт", "CL-MARIA", "Мария RUB", "Мария RUB", 100)
    s.pay("Дина: номер «123»", "CL-DINA", "Дина RUB", "123", 100)
    s.pay("Иван: перевод «в прошлое»", "CL-IVAN", "Иван RUB", "Мария RUB", 100,
          scheduled_at=at(8, 0))
    s.pay("Ника платит со счёта Анны", "CL-NIKA", "Анна RUB", "Ника RUB", 10_000)
    bank.logout("CL-OLEG")
    s.pay("Олег после выхода", "CL-OLEG", "Олег Premium", "Ника RUB", 1_000)

    out("  ...а эти транзакции создадутся, но процессор их отклонит:")
    s.pay("Ника → Олегу больше, чем есть", "CL-NIKA", "Ника RUB", "Олег Premium", 50_000)
    s.pay("Иван со вклада ниже минимума", "CL-IVAN", "Иван вклад", "Иван RUB", 55_000)
    s.pay("Анна: 260 000 Марии со вклада", "CL-ANNA", "Анна вклад", "Мария RUB", 260_000)
    s.pay("Иван: больше лимита снятия", "CL-IVAN", "Иван RUB", "Дина RUB", 150_000)
    s.process()
    s.attempt("банк морозит вклад Ивана", lambda: bank.freeze_account(
        world.accounts["Иван вклад"].account_number))
    out(f"  {world.clock.now:%H:%M} 🧊 банк заморозил «Иван вклад» (проверка документов)")
    s.pay("Мария → на замороженный вклад", "CL-MARIA", "Мария RUB", "Иван вклад", 1_000)
    s.pay("Дина → в банк, который лежит", "CL-DINA", "Дина RUB", EXT_DOWN, 2_000)
    s.pay("Дина → в «моргающий» банк", "CL-DINA", "Дина RUB", EXT_SHAKY, 1_500)
    s.pay("Сергей: юани → евро Марии", "CL-SERGEY", "Сергей CNY", "Мария EUR", 1_000)
    s.wait_until(at(11, 30))
    s.process()

    # 13:00 ─ подозрительное
    s.wait_until(at(13, 0))
    s.header("13:00 Подозрительные операции")
    s.login("CL-OLEG")
    s.pay("Сергей: 250 000 в швейцарский банк", "CL-SERGEY", "Сергей Premium", EXT_SWISS, 250_000)
    out("  Олег дробит платёж: 5 переводов новым получателям за 5 минут")
    for i in range(1, 6):
        s.pay(f"Олег, дробление {i}/5", "CL-OLEG", "Олег Premium",
              f"KZ86125KZT50040010{i}", 9_000, gap=1)
    s.pay("Анна: 1 500 USD ночью новому", "CL-ANNA", "Анна инвест USD", EXT_SWISS, 1_500,
          scheduled_at=at(2, 0, day=1))

    forged = Transaction(
        "internal_transfer", 100_000, "RUB",
        sender=world.accounts["Сергей Premium"].account_number,
        receiver=world.accounts["Ника RUB"].account_number,
        initiator_id="CL-SERGEY", created_at=world.clock.now,
    )
    s.attempt("самодельная транзакция «от Сергея»", lambda: world.queue.add(forged))

    gift = s.pay("Анна: подарок Ивану к 17:00", "CL-ANNA", "Анна RUB", "Иван RUB", 3_000,
                 scheduled_at=at(17, 0))
    duplicate = s.pay("Анна: подарок Ивану (дубль)", "CL-ANNA", "Анна RUB", "Иван RUB", 3_000,
                      scheduled_at=at(17, 0))
    s.attempt("Ника отменяет чужой перевод", lambda: world.queue.cancel(
        "CL-NIKA", duplicate.transaction_id))
    s.attempt("Анна отменяет свой дубль", lambda: world.queue.cancel(
        "CL-ANNA", duplicate.transaction_id, "дубль"))
    s.pay("Мария → Нике к 18:00", "CL-MARIA", "Мария RUB", "Ника RUB", 500, scheduled_at=at(18, 0))
    s.pay("Дина → Сергею на 02:30 ночи", "CL-DINA", "Дина RUB", "Сергей Premium", 2_000,
          scheduled_at=at(2, 30, day=1))

    out("  Кто-то подбирает пароль Марии:")
    for _ in range(Bank.MAX_LOGIN_ATTEMPTS):
        s.attempt("вход в профиль Марии", lambda: bank.authenticate_client("CL-MARIA", "123456"))
    s.wait_until(at(13, 30))
    s.process()

    # 17:00 ─ вечер
    s.wait_until(at(17, 0))
    s.header("17:00 Вечер: отложенные переводы и обычные платежи")
    s.pay("Иван → Нике", "CL-IVAN", "Иван RUB", "Ника RUB", 700)
    s.pay("Ника: мобильная связь", "CL-NIKA", "Ника RUB", EXT_PHONE, 300)
    s.pay("Дина: тенге → доллары Олегу", "CL-DINA", "Дина KZT", "Олег USD", 90_000)
    s.pay("Олег → Анне в USD", "CL-OLEG", "Олег USD", "Анна инвест USD", 100)
    s.pay("Сергей меняет рубли на юани", "CL-SERGEY", "Сергей Premium", "Сергей CNY", 90_000)
    s.pay("Ника → Марии", "CL-NIKA", "Ника RUB", "Мария RUB", 1_000)
    s.process()
    s.wait_until(at(18, 0))
    s.process()
    if gift is not None and gift.status is not TransactionStatus.COMPLETED:
        out("  ‼ подарок Ивану не дошёл")

    # Ночь
    s.wait_until(at(2, 30, day=1))
    s.header("02:30 Ночь: банк закрыт")
    s.pay("Анна: ночной перевод", "CL-ANNA", "Анна RUB", "Мария RUB", 100, gap=0)
    s.process()

    s.wait_until(at(5, 0, day=1))
    s.header("05:00 Банк открылся")
    s.process()
    s.tail.flush()


# --- пользовательские сценарии ---------------------------------------------

def show_client(world: World, client_id: str) -> None:
    bank, out = world.bank, world.out
    client = bank.get_client(client_id)
    status = "🔒 заблокирован" if client.is_blocked else "активен"
    out(f"\n👤 {client.full_name} ({client_id}), {client.age} лет, {status}")

    out("  💰 Счета:")
    for number in client.account_numbers:
        acc = bank.get_account(number)
        out(f"     {type(acc).__name__:<17} {acc.masked_number}  "
            f"{money(acc.balance):>14} {acc.currency}  {acc.status.value}")

    out("  📜 История:")
    arrows = {Direction.OUTGOING: "→", Direction.INCOMING: "←", Direction.OWN: "⇄"}
    for item in client_history(bank, client_id):
        tx = item.transaction
        if item.direction is Direction.INCOMING:
            # Входящий: сколько пришло в валюте счёта клиента и от кого.
            sign, amount, other = "+", tx.credit_amount, tx.sender
            currency = bank.get_account(tx.receiver).currency
        else:
            sign, amount, other, currency = "−", tx.amount, tx.receiver, tx.currency
        line = (f"     {tx.created_at:%d.%m %H:%M} {arrows[item.direction]} "
                f"{sign}{money(amount):>12} {currency}"
                f"  {world.account_name(other):<20} {tx.status.value:<9}")
        if tx.failure_reason:
            reason = tx.failure_reason
            line += f" ({reason if len(reason) <= 70 else reason[:69] + '…'})"
        out(line)

    out("  ⚠️  Подозрительное:")
    risky = [(tx, a) for tx, a in suspicious_operations(bank) if a.client_id == client_id]
    for tx, assessment in risky:
        out(f"     #{world.numbers[tx.transaction_id]:02} {tx.description}: {assessment}")
    for event in bank.get_suspicious_activities(client_id):
        out(f"     {event}")
    if not risky and not bank.get_suspicious_activities(client_id):
        out("     —")
    profile = client_risk_profile(bank, client_id)
    out(f"  Итоговый риск клиента: {profile.level.name}")


# --- отчёты -----------------------------------------------------------------

def show_reports(world: World) -> None:
    bank, out = world.bank, world.out

    out("\n🏆 Топ-3 клиентов (все счета, в рублях по курсу)")
    for place, w in enumerate(top_clients(bank, 3), start=1):
        parts = ", ".join(f"{money(v)} {c}" for c, v in w.by_currency.items())
        out(f"  {place}. {w.client.full_name:<14} {money(w.total_rub):>14} RUB  ({parts})")

    stats = transaction_statistics(bank, world.processor)
    errors = error_statistics(bank, world.processor)
    out("\n📊 Статистика транзакций")
    out(f"  Создано: {stats.total} | "
        + ", ".join(f"{s.value} {n}" for s, n in stats.by_status.items()))
    out("  По типу: " + ", ".join(f"{t.value} {n}" for t, n in stats.by_type.items()))
    out("  По риску: " + ", ".join(f"{r.name} {n}" for r, n in stats.by_risk.items()))
    out(f"  Отказов: {errors.failure_rate}% | причины: "
        + ", ".join(f"{k} {v}" for k, v in errors.errors_by_type.items()))
    out(f"  Отклонено ещё до создания: {len(world.rejected)} попыток")
    out(f"  Оборот выполненных: {money(stats.completed_volume_rub)} RUB, "
        f"средний чек {money(stats.average_completed_rub)} RUB")
    if stats.largest_completed:
        big = stats.largest_completed
        out(f"  Самая крупная: #{world.numbers[big.transaction_id]:02} {big.description} "
            f"({money(big.amount)} {big.currency})")
    out(f"  Выполнено/отклонено после повторов: {stats.retried}")
    out("  Комиссии банка: " + (", ".join(f"{money(v)} {c}" for c, v in stats.fee_income.items()) or "—"))
    out("  Ушло в другие банки: "
        + (", ".join(f"{money(v)} {c}" for c, v in stats.external_outflow.items()) or "—"))

    per_currency, in_rub = total_balance(bank)
    out("\n💎 Общий баланс банка")
    for currency, amount in per_currency.items():
        out(f"  {currency}: {money(amount):>16}")
    out(f"  Всего в рублях по курсу: {money(in_rub)} RUB")

    out("\n🧮 Сверка: деньги не появились и не пропали")
    for currency, (expected, actual) in reconcile(bank, world.opening_balance).items():
        mark = "✅" if expected == actual else "❌"
        out(f"  {mark} {currency}: ожидалось {money(expected):>14}, на счетах {money(actual):>14}")

    out("\n📝 Журнал аудита по уровням: "
        + ", ".join(f"{lvl.name} {n}" for lvl, n in errors.audit_by_level.items()))


def main() -> None:
    world = build_world()
    bank, out = world.bank, world.out

    out("=" * 72)
    out(f" {bank.name}: один рабочий день, {DAY:%d.%m.%Y}")
    out("=" * 72)
    out(f"\n🏦 Клиентов: {len(bank.clients)}, счетов: {len(world.accounts)}")
    for name, acc in world.accounts.items():
        out(f"  {name:<16} {type(acc).__name__:<17} {acc.masked_number} "
            f"{money(acc.balance):>14} {acc.currency}")
    for label, exc in world.rejected:
        out(f"  🚫 {label} — {type(exc).__name__}: {exc}")

    simulate(world)

    out("\n" + "=" * 72)
    out(" Пользовательские сценарии")
    out("=" * 72)
    show_client(world, "CL-ANNA")
    show_client(world, "CL-MARIA")

    out("\n" + "=" * 72)
    out(" Отчёты")
    out("=" * 72)
    show_reports(world)


if __name__ == "__main__":
    main()
