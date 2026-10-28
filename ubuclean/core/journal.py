"""Log riwayat aksi UbuClean (JSON Lines)."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class HistoryEntry:
    """Satu baris riwayat yang ditulis setelah aksi selesai."""

    action: str
    category: str
    label: str
    items: int = 0
    bytes: int = 0
    ok: bool = True
    dry_run: bool = False
    detail: str = ""
    quarantine_id: str | None = None
    errors: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Representasi JSON-able."""
        return {
            "ts": self.ts,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.ts)),
            "action": self.action,
            "category": self.category,
            "label": self.label,
            "items": self.items,
            "bytes": self.bytes,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "detail": self.detail,
            "quarantine_id": self.quarantine_id,
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HistoryEntry":
        """Bangun entri dari JSON, toleran terhadap field yang hilang."""
        return cls(
            action=str(data.get("action", "")),
            category=str(data.get("category", "")),
            label=str(data.get("label", "")),
            items=int(data.get("items", 0) or 0),
            bytes=int(data.get("bytes", 0) or 0),
            ok=bool(data.get("ok", True)),
            dry_run=bool(data.get("dry_run", False)),
            detail=str(data.get("detail", "")),
            quarantine_id=data.get("quarantine_id") or None,
            errors=[str(err) for err in data.get("errors", []) or []],
            ts=float(data.get("ts", time.time())),
        )


class HistoryLog:
    """Penulis dan pembaca log riwayat di ``~/.local/state/ubuclean/log``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def ensure(self) -> None:
        """Pastikan direktori dan file log ada dengan izin 0600."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            self.path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.path, 0o600)

    def record(self, entry: HistoryEntry) -> None:
        """Tambahkan satu entri ke log."""
        self.ensure()
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def record_many(self, entries: list[HistoryEntry]) -> None:
        """Tambahkan beberapa entri dalam satu penulisan."""
        if not entries:
            return
        self.ensure()
        lines = "".join(
            json.dumps(entry.to_dict(), ensure_ascii=False) + "\n" for entry in entries
        )
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(lines)

    def entries(self, limit: int = 500) -> list[HistoryEntry]:
        """Baca entri terbaru, mengabaikan baris rusak."""
        if not self.path.exists():
            return []
        result: list[HistoryEntry] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    try:
                        result.append(HistoryEntry.from_dict(data))
                    except (TypeError, ValueError):
                        continue
        except OSError:
            return []
        result.reverse()
        return result[:limit]

    def clear(self) -> None:
        """Kosongkan log riwayat."""
        with self._lock:
            if self.path.exists():
                self.path.write_text("", encoding="utf-8")

    def path_for_display(self) -> str:
        """Path log untuk ditampilkan di UI."""
        return str(self.path)
