from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path


class AuditLevel(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2
    CRITICAL = 3


@dataclass(frozen=True, slots=True)
class AuditEntry:
    timestamp: datetime
    level: AuditLevel
    event: str
    message: str
    client_id: str | None = None
    transaction_id: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "level": self.level.name,
                "event": self.event,
                "message": self.message,
                "client_id": self.client_id,
                "transaction_id": self.transaction_id,
            },
            ensure_ascii=False,
        )

    def __str__(self) -> str:
        who = f" {self.client_id}" if self.client_id else ""
        tx = f" {self.transaction_id}" if self.transaction_id else ""
        return f"[{self.timestamp:%d.%m %H:%M}] {self.level.name:<8} {self.event}{who}{tx}: {self.message}"


class AuditLog:
    def __init__(
        self, path: str | Path | None = None, *, clock: Callable[[], datetime] = datetime.now
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._entries: list[AuditEntry] = []

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def log(
        self,
        level: AuditLevel,
        event: str,
        message: str,
        *,
        client_id: str | None = None,
        transaction_id: str | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(self._clock(), AuditLevel(level), event, message, client_id, transaction_id)
        self._entries.append(entry)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as file:
                file.write(entry.to_json() + "\n")
        return entry

    def filter(
        self,
        *,
        min_level: AuditLevel | None = None,
        event: str | None = None,
        client_id: str | None = None,
        transaction_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEntry]:
        return [
            e for e in self._entries
            if (min_level is None or e.level >= min_level)
            and (event is None or e.event == event)
            and (client_id is None or e.client_id == client_id)
            and (transaction_id is None or e.transaction_id == transaction_id)
            and (since is None or e.timestamp >= since)
            and (until is None or e.timestamp <= until)
        ]

    def __len__(self) -> int:
        return len(self._entries)
