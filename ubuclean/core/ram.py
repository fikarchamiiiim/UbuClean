"""Memory statistics and RAM cache cleaning operations."""

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
    """A sample of RAM, cache, and swap usage."""

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
        """Cache + buffers that can be freed."""
        return max(0, self.cached + self.buffers)

    def as_dict(self) -> dict[str, float | int]:
        """JSON-able representation for the CLI/UI."""
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
    """Read memory statistics from psutil."""
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
    """Result of the ``swapoff -a`` eligibility check."""

    can_refresh: bool
    swap_used: int
    available: int
    required: int
    message: str


@dataclass(slots=True)
class RamOutcome:
    """Result of a drop caches or swap refresh action."""

    ok: bool
    before: MemoryStats
    after: MemoryStats
    freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""
    dry_run: bool = False

    def as_dict(self) -> dict[str, object]:
        """JSON-able representation."""
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
    """Read the kB values from /proc/meminfo."""
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
    """Check whether free RAM is enough to hold the whole contents of swap."""
    info = read_meminfo(meminfo_path)
    available = info.get("MemAvailable", 0) * 1024
    swap_total = info.get("SwapTotal", 0) * 1024
    swap_free = info.get("SwapFree", 0) * 1024
    swap_used = max(0, swap_total - swap_free)
    if not info:
        return SwapEligibility(False, swap_used, available, 0, "Cannot read /proc/meminfo.")
    if swap_total == 0:
        return SwapEligibility(False, 0, available, 0, "Swap is not available on this system.")
    if swap_used == 0:
        return SwapEligibility(False, 0, available, 0, "Swap is not currently in use.")
    required = int(swap_used * SWAP_RATIO) + SWAP_MARGIN_KB * 1024
    if available < required:
        return SwapEligibility(
            False,
            swap_used,
            available,
            required,
            "Free RAM is not enough to hold the contents of swap. Close a few applications and try again.",
        )
    return SwapEligibility(
        True,
        swap_used,
        available,
        required,
        "Free RAM is enough to refresh swap.",
    )


class RamService:
    """Memory actions that use the root helper for drop caches/swap."""

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
        """Current memory statistics."""
        return self._stats()

    def swap_eligibility(self) -> SwapEligibility:
        """Check eligibility for a swap refresh."""
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
        """Sync, then drop the kernel cache at the selected level."""
        level = max(1, min(3, int(level)))
        before = self.stats()
        if self.ctx.dry_run:
            outcome = RamOutcome(
                ok=True,
                before=before,
                after=before,
                detail=f"Preview: drop_caches level {level} was not run.",
                dry_run=True,
            )
            self._log("ram", "Preview drop caches", 0, True, outcome.detail, [])
            return outcome
        try:
            self.ctx.privileged.run(["drop-caches", str(level)], timeout=180)
        except PrivilegedError as exc:
            outcome = RamOutcome(False, before, before, errors=[str(exc)], detail="Drop caches failed.")
            self._log("ram", "Drop caches", 0, False, outcome.detail, outcome.errors)
            return outcome
        after = self.stats()
        freed = max(0, before.reclaimable - after.reclaimable)
        outcome = RamOutcome(
            ok=True,
            before=before,
            after=after,
            freed_bytes=freed,
            detail=f"Kernel cache dropped (level {level}).",
        )
        self._log("ram", f"Drop caches level {level}", freed, True, outcome.detail, [])
        return outcome

    def swap_refresh(self) -> RamOutcome:
        """Refresh swap only when the available RAM is sufficient."""
        before = self.stats()
        eligibility = self.swap_eligibility()
        if not eligibility.can_refresh:
            outcome = RamOutcome(
                ok=False,
                before=before,
                after=before,
                errors=[eligibility.message],
                detail="Swap was not refreshed.",
            )
            self._log("swap", "Refresh swap", 0, False, outcome.detail, outcome.errors)
            return outcome
        if self.ctx.dry_run:
            outcome = RamOutcome(
                ok=True,
                before=before,
                after=before,
                detail="Preview: swap was not refreshed.",
                dry_run=True,
            )
            self._log("swap", "Preview refresh swap", 0, True, outcome.detail, [])
            return outcome
        try:
            self.ctx.privileged.run(["swap-refresh"], timeout=1200)
        except PrivilegedError as exc:
            outcome = RamOutcome(
                ok=False,
                before=before,
                after=self.stats(),
                errors=[str(exc)],
                detail="Swap refresh failed.",
            )
            self._log("swap", "Refresh swap", 0, False, outcome.detail, outcome.errors)
            return outcome
        after = self.stats()
        freed = max(0, before.swap_used - after.swap_used)
        outcome = RamOutcome(
            ok=True,
            before=before,
            after=after,
            freed_bytes=freed,
            detail="Swap refreshed.",
        )
        self._log("swap", "Refresh swap", freed, True, outcome.detail, [])
        return outcome


def now_seconds() -> float:
    """Current time (used by the UI for animations/cooldowns)."""
    return time.time()
