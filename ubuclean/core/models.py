"""Model data yang dipakai bersama oleh core, CLI, dan UI."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Group(str, Enum):
    """Kelompok tampilan kategori pembersihan."""

    SYSTEM = "system"
    USER = "user"
    DEVELOPER = "developer"


@dataclass(frozen=True, slots=True)
class Category:
    """Metadata satu kategori pembersihan.

    ``privileged`` menandai operasi yang membutuhkan helper root, ``risky``
    menandai kategori yang tidak boleh ikut jadwal otomatis dan tidak
    dicentang secara default, sedangkan ``quarantine`` menandai item yang
    dipindahkan ke karantina sebelum dihapus permanen.
    """

    id: str
    label: str
    description: str
    group: Group
    icon: str = "user-trash-symbolic"
    privileged: bool = False
    risky: bool = False
    default_enabled: bool = True
    quarantine: bool = False
    schedule_allowed: bool = True
    schedule_default: bool = False


@dataclass(frozen=True, slots=True)
class PreviewItem:
    """Satu baris hasil pindai (file, paket, atau revisi)."""

    id: str
    label: str
    size: int = 0
    detail: str = ""
    path: Path | None = None
    protected: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Representasi JSON-able untuk mode CLI."""
        return {
            "id": self.id,
            "label": self.label,
            "size": self.size,
            "detail": self.detail,
            "path": str(self.path) if self.path else None,
            "protected": self.protected,
        }


@dataclass(slots=True)
class Preview:
    """Hasil pindai satu kategori, sekaligus token izin eksekusi."""

    category_id: str
    items: list[PreviewItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands: list[tuple[str, ...]] = field(default_factory=list)
    supported: bool = True
    estimated_bytes: int | None = None
    scanned_at: float = field(default_factory=time.time)
    token: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def total_bytes(self) -> int:
        """Total ukuran item, atau estimasi agregat bila disediakan."""
        if self.estimated_bytes is not None:
            return max(0, self.estimated_bytes)
        return sum(item.size for item in self.items if not item.protected)

    @property
    def item_count(self) -> int:
        """Jumlah item yang akan dibersihkan."""
        return sum(1 for item in self.items if not item.protected)

    def to_dict(self) -> dict[str, Any]:
        """Representasi JSON-able untuk mode CLI dan dry-run."""
        return {
            "category": self.category_id,
            "token": self.token,
            "supported": self.supported,
            "total_bytes": self.total_bytes,
            "estimated_bytes": self.estimated_bytes,
            "item_count": self.item_count,
            "notes": list(self.notes),
            "errors": list(self.errors),
            "commands": [list(cmd) for cmd in self.commands],
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(slots=True)
class CleanOutcome:
    """Hasil eksekusi satu kategori."""

    category_id: str
    freed_bytes: int = 0
    removed: int = 0
    failed: int = 0
    quarantined: int = 0
    quarantined_bytes: int = 0
    quarantine_id: str | None = None
    errors: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True bila tidak ada item yang gagal."""
        return self.failed == 0

    @property
    def immediate_bytes(self) -> int:
        """Byte yang benar-benar hilang dari disk saat ini."""
        return max(0, self.freed_bytes - self.quarantined_bytes)

    def merge(self, other: "CleanOutcome") -> None:
        """Gabungkan hasil lain ke dalam hasil ini."""
        self.freed_bytes += other.freed_bytes
        self.removed += other.removed
        self.failed += other.failed
        self.quarantined += other.quarantined
        self.quarantined_bytes += other.quarantined_bytes
        self.errors.extend(other.errors)
        if other.quarantine_id:
            self.quarantine_id = other.quarantine_id

    def to_dict(self) -> dict[str, Any]:
        """Representasi JSON-able untuk log dan CLI."""
        return {
            "category": self.category_id,
            "freed_bytes": self.freed_bytes,
            "removed": self.removed,
            "failed": self.failed,
            "quarantined": self.quarantined,
            "quarantined_bytes": self.quarantined_bytes,
            "immediate_bytes": self.immediate_bytes,
            "quarantine_id": self.quarantine_id,
            "errors": list(self.errors),
            "detail": self.detail,
        }


@dataclass(slots=True)
class CleanReport:
    """Ringkasan seluruh eksekusi pembersihan."""

    outcomes: list[CleanOutcome] = field(default_factory=list)
    dry_run: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def freed_bytes(self) -> int:
        """Total byte yang dibebaskan."""
        return sum(outcome.freed_bytes for outcome in self.outcomes)

    @property
    def failures(self) -> int:
        """Total item yang gagal diproses."""
        return sum(outcome.failed for outcome in self.outcomes)

    @property
    def errors(self) -> list[str]:
        """Kumpulan pesan error dari semua kategori."""
        return [error for outcome in self.outcomes for error in outcome.errors]

    def to_dict(self) -> dict[str, Any]:
        """Representasi JSON-able untuk CLI dan log."""
        return {
            "dry_run": self.dry_run,
            "freed_bytes": self.freed_bytes,
            "failures": self.failures,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }
