"""Statistik memori dan operasi pembersihan cache RAM."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ubuclean.core.context import Context
from ubuclean.core.journal import HistoryEntry
from ubuclean.core.runner import PrivilegedError

MEMINFO = Path("/proc/meminfo")
SWAP_MARGIN_KB = 64 * 1024
SWAP_RATIO = 1.15


@dataclass(frozen=True, slots=True)
class MemoryStats:
    """Cuplikan penggunaan RAM, cache, dan swap."""

    total: int
    available: int
    used: int
    percent: float
    cached: int
    buffers: int
    swap_total: int
    swap_used: int
    swap_percent: float

    @property
    def reclaimable(self) -> int:
        """Cache + buffer yang bisa dibebaskan."""
        return max(0, self.cached + self.buffers)

    def as_dict(self) -> dict[str, float | int]:
        """Representasi JSON-able untuk CLI/UI."""
        return {
            "total": self.total,
            "available": self.available,
            "used": self.used,
            "percent": round(self.percent, 1),
            "cached": self.cached,
            "buffers": self.buffers,
            "reclaimable": self.reclaimable,
            "swap_total": self.swap_total,
            "swap_used": self.swap_used,
            "swap_percent": round(self.swap_percent, 1),
        }


def collect_memory_stats() -> MemoryStats:
    """Baca statistik memori dari psutil."""
    import psutil

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cached = int(getattr(memory, "cached", 0))
    buffers = int(getattr(memory, "buffers", 0))
    return MemoryStats(
        total=int(memory.total),
        available=int(memory.available),
        used=int(memory.used),
        percent=float(memory.percent),
        cached=cached,
        buffers=buffers,
        swap_total=int(swap.total),
        swap_used=int(swap.used),
        swap_percent=float(swap.percent),
    )


@dataclass(frozen=True, slots=True)
class SwapEligibility:
    """Hasil pemeriksaan kelayakan ``swapoff -a``."""

    can_refresh: bool
    swap_used: int
    available: int
    required: int
    message: str


@dataclass(slots=True)
class RamOutcome:
    """Hasil aksi drop caches atau swap refresh."""

    ok: bool
    before: MemoryStats
    after: MemoryStats
    freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""
    dry_run: bool = False

    def as_dict(self) -> dict[str, object]:
        """Representasi JSON-able."""
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "freed_bytes": self.freed_bytes,
            "errors": list(self.errors),
            "detail": self.detail,
        }


def read_meminfo(path: Path | None = None) -> dict[str, int]:
    """Baca nilai kB dari /proc/meminfo."""
    target = path or MEMINFO
    values: dict[str, int] = {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, raw = line.split(":", 1)
                parts = raw.strip().split()
                if not parts:
                    continue
                try:
                    values[key] = int(parts[0])
                except ValueError:
                    continue
    except OSError:
        return {}
    return values


def check_swap_eligibility(meminfo_path: Path | None = None) -> SwapEligibility:
    """Periksa apakah RAM bebas cukup menampung seluruh isi swap."""
    info = read_meminfo(meminfo_path)
    available = info.get("MemAvailable", 0) * 1024
    swap_total = info.get("SwapTotal", 0) * 1024
    swap_free = info.get("SwapFree", 0) * 1024
    swap_used = max(0, swap_total - swap_free)
    if not info:
        return SwapEligibility(False, swap_used, available, 0, "Tidak bisa membaca /proc/meminfo.")
    if swap_total == 0:
        return SwapEligibility(False, 0, available, 0, "Swap tidak tersedia di sistem ini.")
    if swap_used == 0:
        return SwapEligibility(False, 0, available, 0, "Swap tidak sedang dipakai.")
    required = int(swap_used * SWAP_RATIO) + SWAP_MARGIN_KB * 1024
    if available < required:
        return SwapEligibility(
            False,
            swap_used,
            available,
            required,
            "RAM bebas tidak cukup untuk menampung isi swap. Tutup beberapa aplikasi lalu coba lagi.",
        )
    return SwapEligibility(
        True,
        swap_used,
        available,
        required,
        "RAM bebas cukup untuk menyegarkan swap.",
    )


class RamService:
    """Aksi memori yang memakai helper root untuk drop caches/swap."""

    def __init__(
        self,
        ctx: Context,
        *,
        stats_provider: Callable[[], MemoryStats] = collect_memory_stats,
        meminfo_path: Path | None = None,
    ) -> None:
        self.ctx = ctx
        self._stats = stats_provider
        self._meminfo_path = meminfo_path

    def stats(self) -> MemoryStats:
        """Statistik memori saat ini."""
        return self._stats()

    def swap_eligibility(self) -> SwapEligibility:
        """Cek kelayakan swap refresh."""
        return check_swap_eligibility(self._meminfo_path)

    def _log(self, action: str, label: str, bytes_freed: int, ok: bool, detail: str, errors: list[str]) -> None:
        try:
            self.ctx.history.record(
                HistoryEntry(
                    action=action,
                    category="memory",
                    label=label,
                    bytes=bytes_freed,
                    ok=ok,
                    detail=detail,
                    errors=errors,
                )
            )
        except OSError:
            pass

    def drop_caches(self, level: int = 2) -> RamOutcome:
        """Sync lalu buang cache kernel pada level yang dipilih."""
        level = max(1, min(3, int(level)))
        before = self.stats()
        if self.ctx.dry_run:
            outcome = RamOutcome(
                ok=True,
                before=before,
                after=before,
                detail=f"Pratinjau: drop_caches level {level} tidak dijalankan.",
                dry_run=True,
            )
            self._log("ram", "Pratinjau drop caches", 0, True, outcome.detail, [])
            return outcome
        try:
            self.ctx.privileged.run(["drop-caches", str(level)], timeout=180)
        except PrivilegedError as exc:
            outcome = RamOutcome(False, before, before, errors=[str(exc)], detail="Drop caches gagal.")
            self._log("ram", "Drop caches", 0, False, outcome.detail, outcome.errors)
            return outcome
        after = self.stats()
        freed = max(0, before.reclaimable - after.reclaimable)
        outcome = RamOutcome(
            ok=True,
            before=before,
            after=after,
            freed_bytes=freed,
            detail=f"Cache kernel dibuang (level {level}).",
        )
        self._log("ram", f"Drop caches level {level}", freed, True, outcome.detail, [])
        return outcome

    def swap_refresh(self) -> RamOutcome:
        """Segarkan swap hanya jika RAM tersedia mencukupi."""
        before = self.stats()
        eligibility = self.swap_eligibility()
        if not eligibility.can_refresh:
            outcome = RamOutcome(
                ok=False,
                before=before,
                after=before,
                errors=[eligibility.message],
                detail="Swap tidak disegarkan.",
            )
            self._log("swap", "Segarkan swap", 0, False, outcome.detail, outcome.errors)
            return outcome
        if self.ctx.dry_run:
            outcome = RamOutcome(
                ok=True,
                before=before,
                after=before,
                detail="Pratinjau: swap tidak disegarkan.",
                dry_run=True,
            )
            self._log("swap", "Pratinjau segarkan swap", 0, True, outcome.detail, [])
            return outcome
        try:
            self.ctx.privileged.run(["swap-refresh"], timeout=1200)
        except PrivilegedError as exc:
            outcome = RamOutcome(
                ok=False,
                before=before,
                after=self.stats(),
                errors=[str(exc)],
                detail="Segarkan swap gagal.",
            )
            self._log("swap", "Segarkan swap", 0, False, outcome.detail, outcome.errors)
            return outcome
        after = self.stats()
        freed = max(0, before.swap_used - after.swap_used)
        outcome = RamOutcome(
            ok=True,
            before=before,
            after=after,
            freed_bytes=freed,
            detail="Swap disegarkan.",
        )
        self._log("swap", "Segarkan swap", freed, True, outcome.detail, [])
        return outcome


def now_seconds() -> float:
    """Waktu sekarang (dipakai UI untuk animasi/cooldown)."""
    return time.time()
