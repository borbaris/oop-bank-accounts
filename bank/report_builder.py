from __future__ import annotations

import csv
import json
from collections import Counter
from itertools import groupby
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from .accounts import Currency
from .exchange import DEMO_EXCHANGE_RATES, CurrencyConverter
from .reports import (
    Direction,
    client_history,
    client_risk_profile,
    error_statistics,
    suspicious_operations,
    top_clients,
    total_balance,
    transaction_statistics,
)
from .risk import RiskLevel
from .transactions import TransactionStatus

if TYPE_CHECKING:
    from .bank_system import Bank
    from .processor import TransactionProcessor


class ReportKind(StrEnum):
    CLIENT = "client"
    BANK = "bank"
    RISK = "risk"


Row = dict[str, Any]


@dataclass(frozen=True)
class Report:
    """Готовый отчёт: заголовок, сводка «показатель → значение» и таблицы."""

    kind: ReportKind
    title: str
    generated_at: datetime
    summary: dict[str, Any]
    tables: dict[str, list[Row]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Только JSON-совместимые значения: Decimal и даты — строками."""
        return {
            "kind": self.kind.value,
            "title": self.title,
            "generated_at": self.generated_at.isoformat(),
            "summary": {k: _plain(v) for k, v in self.summary.items()},
            "tables": {
                name: [{k: _plain(v) for k, v in row.items()} for row in rows]
                for name, rows in self.tables.items()
            },
        }


def _plain(value: Any) -> Any:
    """Decimal("10.50") -> "10.50" (строкой — чтобы не потерять копейки, как с float)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return "; ".join(str(_plain(v)) for v in value)
    return value


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _mask(number: str) -> str:
    return f"****{number[-4:]}"


class ReportBuilder:
    """Строит отчёты по клиенту, банку и рискам, экспортирует их и рисует графики.

    Номера счетов в отчётах замаскированы (****1234): отчёты уходят наружу.
    """

    def __init__(
        self,
        bank: Bank,
        processor: TransactionProcessor,
        *,
        converter: CurrencyConverter | None = None,
    ) -> None:
        self._bank = bank
        self._processor = processor
        self._converter = converter or CurrencyConverter(DEMO_EXCHANGE_RATES)

    # --- отчёты ---------------------------------------------------------------

    def client_report(self, client_id: str) -> Report:
        bank = self._bank
        client = bank.get_client(client_id)
        profile = client_risk_profile(bank, client_id)
        accounts = [bank.get_account(n) for n in client.account_numbers]
        history = client_history(bank, client_id)

        incoming = outgoing = Decimal("0.00")
        transactions: list[Row] = []
        for item in history:
            tx = item.transaction
            if item.direction is Direction.INCOMING:
                currency = bank.get_account(tx.receiver).currency
                amount, counterpart = tx.credit_amount, tx.sender
                incoming += self._rub(amount, currency)
            else:
                currency, amount, counterpart = tx.currency, tx.amount, tx.receiver
                if item.direction is Direction.OUTGOING and tx.status is TransactionStatus.COMPLETED:
                    outgoing += self._rub(amount, currency)
            transactions.append({
                "created_at": tx.created_at,
                "transaction_id": tx.transaction_id,
                "direction": item.direction,
                "amount": amount,
                "currency": currency,
                "counterpart": _mask(counterpart),
                "status": tx.status,
                "reason": tx.failure_reason or "",
            })

        total_rub = sum((self._rub(a.total_value, a.currency) for a in accounts), Decimal("0.00"))
        return Report(
            kind=ReportKind.CLIENT,
            title=f"Отчёт по клиенту {client.full_name} ({client_id})",
            generated_at=bank.now(),
            summary={
                "client_id": client_id,
                "full_name": client.full_name,
                "age": client.age,
                "status": client.status,
                "accounts": len(accounts),
                "total_rub": total_rub,
                "transactions": len(history),
                "incoming_rub": incoming,
                "outgoing_rub": outgoing,
                "risk_level": profile.level.name,
                "suspicious_events": profile.suspicious_events,
            },
            tables={
                "accounts": [
                    {
                        "account": a.masked_number,
                        "type": type(a).__name__,
                        "currency": a.currency,
                        "balance": a.balance,
                        "total_value": a.total_value,
                        "status": a.status,
                        "opened_at": a.balance_history[0].timestamp,
                    }
                    for a in accounts
                ],
                "transactions": transactions,
                "suspicious": [
                    {
                        "transaction_id": tx.transaction_id,
                        "level": a.level.name,
                        "score": a.score,
                        "reasons": a.reasons,
                        "status": tx.status,
                    }
                    for tx, a in suspicious_operations(bank)
                    if a.client_id == client_id
                ],
            },
        )

    def bank_report(self) -> Report:
        bank = self._bank
        stats = transaction_statistics(bank, self._processor, converter=self._converter)
        per_currency, total_rub = total_balance(bank, converter=self._converter)
        accounts = [bank.get_account(n) for c in bank.clients for n in c.account_numbers]
        types = Counter(type(a).__name__ for a in accounts)
        return Report(
            kind=ReportKind.BANK,
            title=f"Отчёт по банку «{bank.name}»",
            generated_at=bank.now(),
            summary={
                "bank": bank.name,
                "clients": len(bank.clients),
                "accounts": len(accounts),
                "total_rub": total_rub,
                "transactions": stats.total,
                "completed": stats.by_status[TransactionStatus.COMPLETED],
                "failed": stats.by_status[TransactionStatus.FAILED],
                "completed_volume_rub": stats.completed_volume_rub,
                "average_completed_rub": stats.average_completed_rub,
                "fee_income_rub": self._sum_rub(stats.fee_income),
                "external_outflow_rub": self._sum_rub(stats.external_outflow),
            },
            tables={
                "balances": [
                    {"currency": c, "amount": v, "amount_rub": self._rub(v, c)}
                    for c, v in per_currency.items()
                ],
                "top_clients": [
                    {"place": i, "client_id": w.client.client_id, "full_name": w.client.full_name,
                     "total_rub": w.total_rub}
                    for i, w in enumerate(top_clients(bank, len(bank.clients), converter=self._converter), 1)
                ],
                "transactions_by_status": [
                    {"status": s, "count": n} for s, n in stats.by_status.items()
                ],
                "transactions_by_type": [
                    {"type": t, "count": n} for t, n in stats.by_type.items()
                ],
                "accounts_by_type": [
                    {"type": t, "count": n} for t, n in sorted(types.items())
                ],
            },
        )

    def risk_report(self) -> Report:
        bank = self._bank
        errors = error_statistics(bank, self._processor)
        by_level = Counter(a.level for a in bank.risk_assessments)
        events = bank.suspicious_activities
        profiles = [client_risk_profile(bank, c.client_id) for c in bank.clients]
        return Report(
            kind=ReportKind.RISK,
            title=f"Отчёт по рискам «{bank.name}»",
            generated_at=bank.now(),
            summary={
                "assessed": len(bank.risk_assessments),
                "low": by_level[RiskLevel.LOW],
                "medium": by_level[RiskLevel.MEDIUM],
                "high_blocked": by_level[RiskLevel.HIGH],
                "suspicious_events": len(events),
                "blocked_clients": sum(p.is_blocked for p in profiles),
                "failure_rate_percent": errors.failure_rate,
            },
            tables={
                "suspicious_operations": [
                    {
                        "transaction_id": tx.transaction_id,
                        "client_id": a.client_id,
                        "amount": tx.amount,
                        "currency": tx.currency,
                        "level": a.level.name,
                        "score": a.score,
                        "reasons": a.reasons,
                        "status": tx.status,
                    }
                    for tx, a in suspicious_operations(bank)
                ],
                "client_profiles": [
                    {
                        "client_id": p.client_id,
                        "full_name": p.full_name,
                        "level": p.level.name,
                        "transactions": p.transactions,
                        "blocked_operations": p.blocked_operations,
                        "failed_logins": p.failed_logins,
                        "suspicious_events": p.suspicious_events,
                        "is_blocked": p.is_blocked,
                    }
                    for p in sorted(profiles, key=lambda p: (-p.level, p.full_name))
                ],
                "events_by_kind": [
                    {"kind": kind, "count": n}
                    for kind, n in Counter(e.kind for e in events).most_common()
                ],
                "events": [
                    {"timestamp": e.timestamp, "kind": e.kind, "client_id": e.client_id or "",
                     "details": e.details}
                    for e in events
                ],
            },
        )

    # --- форматы ----------------------------------------------------------------

    @staticmethod
    def to_text(report: Report) -> str:
        lines = [report.title, "=" * len(report.title),
                 f"Сформирован: {report.generated_at:%d.%m.%Y %H:%M}", ""]
        width = max(len(k) for k in report.summary)
        for key, value in report.summary.items():
            shown = _money(value) if isinstance(value, Decimal) else _plain(value)
            lines.append(f"  {key:<{width}}  {shown}")
        for name, rows in report.tables.items():
            lines += ["", f"[{name}] — строк: {len(rows)}"]
            lines += _text_table(rows) if rows else ["  —"]
        return "\n".join(lines)

    @staticmethod
    def export_to_json(report: Report, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def export_to_csv(report: Report, directory: str | Path) -> list[Path]:
        """CSV — это одна плоская таблица, поэтому файл на каждую таблицу отчёта
        плюс summary.csv. Кодировка utf-8-sig: так Excel правильно откроет кириллицу."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        prefix = report.kind.value
        if report.kind is ReportKind.CLIENT:
            prefix += f"_{report.summary['client_id']}"

        paths = [_write_csv(directory / f"{prefix}_summary.csv", ["metric", "value"],
                            [{"metric": k, "value": v} for k, v in report.summary.items()])]
        for name, rows in report.tables.items():
            columns = list(rows[0]) if rows else []
            paths.append(_write_csv(directory / f"{prefix}_{name}.csv", columns, rows))
        return paths

    # --- графики ----------------------------------------------------------------

    def currency_pie(self) -> Figure:
        """Круговая: из чего состоят деньги банка (всё в рублях по курсу)."""
        per_currency, _ = total_balance(self._bank, converter=self._converter)
        data = {c: self._rub(v, c) for c, v in per_currency.items() if v > 0}
        fig = Figure(figsize=(6, 6))
        ax = fig.add_subplot()
        ax.set_title("Деньги банка по валютам (в RUB)")
        if not data:
            return _no_data(fig, ax)
        ax.pie([float(v) for v in data.values()], labels=[c.value for c in data],
               autopct="%1.1f%%", startangle=90)
        return fig

    def risk_pie(self) -> Figure:
        """Круговая: доля транзакций по уровню риска."""
        counts = Counter(a.level for a in self._bank.risk_assessments)
        levels = [lvl for lvl in RiskLevel if counts[lvl]]
        fig = Figure(figsize=(6, 6))
        ax = fig.add_subplot()
        ax.set_title("Транзакции по уровню риска")
        if not levels:
            return _no_data(fig, ax)
        ax.pie([counts[lvl] for lvl in levels], labels=[lvl.name for lvl in levels],
               autopct="%1.0f%%", colors=[_RISK_COLORS[lvl] for lvl in levels], startangle=90)
        return fig

    def status_bar(self) -> Figure:
        """Столбчатая: сколько транзакций в каждом статусе."""
        stats = transaction_statistics(self._bank, self._processor, converter=self._converter)
        fig = Figure(figsize=(7, 4))
        ax = fig.add_subplot()
        bars = ax.bar([s.value for s in stats.by_status], list(stats.by_status.values()),
                      color=[_STATUS_COLORS[s] for s in stats.by_status])
        ax.bar_label(bars)
        ax.set_title("Транзакции по статусам")
        ax.set_ylabel("штук")
        return fig

    def top_clients_bar(self, limit: int = 5) -> Figure:
        """Столбчатая: капитал клиентов в рублях."""
        wealth = top_clients(self._bank, limit, converter=self._converter)
        fig = Figure(figsize=(8, 4))
        ax = fig.add_subplot()
        bars = ax.barh([w.client.full_name for w in wealth][::-1],
                       [float(w.total_rub) for w in wealth][::-1])
        ax.bar_label(bars, labels=[_money(w.total_rub) for w in wealth][::-1], padding=3)
        ax.set_title(f"Топ-{len(wealth)} клиентов по капиталу (RUB)")
        ax.xaxis.set_major_formatter(_THOUSANDS)
        ax.margins(x=0.25)
        return fig

    def balance_chart(self, client_id: str) -> Figure:
        """График движения баланса: каждый счёт клиента + итог, всё в рублях.

        Данные — из balance_history счетов, куда пишется КАЖДОЕ изменение баланса.
        """
        client = self._bank.get_client(client_id)
        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot()
        ax.set_title(f"Движение баланса: {client.full_name} (в RUB)")
        if not client.account_numbers:
            return _no_data(fig, ax)

        now = self._bank.now()
        events: list[tuple[datetime, str, Decimal]] = []  # (время, счёт, баланс в RUB после)
        for number in client.account_numbers:
            account = self._bank.get_account(number)
            points = [(e.timestamp, self._rub(e.balance, account.currency))
                      for e in account.balance_history]
            events += [(moment, number, rub) for moment, rub in points]
            points.append((now, points[-1][1]))  # дотягиваем линию до момента отчёта
            label = f"{type(account).__name__} {account.masked_number} ({account.currency})"
            ax.step([t for t, _ in points], [float(v) for _, v in points],
                    where="post", label=label, alpha=0.7)

        current: dict[str, Decimal] = {}
        times, totals = [], []
        for moment, group in groupby(sorted(events, key=lambda e: e[0]), key=lambda e: e[0]):
            for _, number, rub in group:
                current[number] = rub
            times.append(moment)
            totals.append(float(sum(current.values())))
        times.append(now)
        totals.append(totals[-1])
        ax.step(times, totals, where="post", label="Итого", color="black", linewidth=2.5)
        ax.yaxis.set_major_formatter(_THOUSANDS)
        ax.xaxis.set_major_formatter(DateFormatter("%d.%m %H:%M"))

        ax.set_ylabel("RUB")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        return fig

    def save_charts(
        self, directory: str | Path, *, client_ids: list[str] | None = None, dpi: int = 100
    ) -> list[Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if client_ids is None:
            best = top_clients(self._bank, 1, converter=self._converter)
            client_ids = [w.client.client_id for w in best]

        charts: dict[str, Figure] = {
            "pie_currencies": self.currency_pie(),
            "pie_risk_levels": self.risk_pie(),
            "bar_transaction_statuses": self.status_bar(),
            "bar_top_clients": self.top_clients_bar(),
        }
        for client_id in client_ids:
            charts[f"line_balance_{client_id}"] = self.balance_chart(client_id)

        paths = []
        for name, fig in charts.items():
            path = directory / f"{name}.png"
            fig.tight_layout()
            fig.savefig(path, dpi=dpi)
            paths.append(path)
        return paths

    def _rub(self, amount: Decimal, currency: Currency) -> Decimal:
        return self._converter.convert(amount, currency, Currency.RUB)

    def _sum_rub(self, totals: dict[Currency, Decimal]) -> Decimal:
        return sum((self._rub(v, c) for c, v in totals.items()), Decimal("0.00"))


_THOUSANDS = FuncFormatter(lambda value, _: f"{value:,.0f}".replace(",", " "))
_RISK_COLORS = {RiskLevel.LOW: "#4caf50", RiskLevel.MEDIUM: "#ffb300", RiskLevel.HIGH: "#e53935"}
_STATUS_COLORS = {
    TransactionStatus.PENDING: "#90a4ae",
    TransactionStatus.COMPLETED: "#4caf50",
    TransactionStatus.FAILED: "#e53935",
    TransactionStatus.CANCELLED: "#ffb300",
}


def _no_data(fig: Figure, ax: Any) -> Figure:
    ax.text(0.5, 0.5, "нет данных", ha="center", va="center", fontsize=14, color="gray")
    ax.set_axis_off()
    return fig


def _text_table(rows: list[Row]) -> list[str]:
    columns = list(rows[0])
    cells = [[_cell(row.get(c)) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]

    def line(values: list[str]) -> str:
        return "  " + " | ".join(v.ljust(w) for v, w in zip(values, widths))

    return [line(columns), "  " + "-+-".join("-" * w for w in widths), *map(line, cells)]


def _cell(value: Any) -> str:
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, datetime):
        return f"{value:%d.%m %H:%M}"
    text = str(_plain(value))
    return text if len(text) <= 60 else text[:59] + "…"


def _write_csv(path: Path, columns: list[str], rows: list[Row]) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _plain(v) for k, v in row.items()})
    return path
