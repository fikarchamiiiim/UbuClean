"""Tes modul RAM (drop caches dan swap refresh)."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.context import Context
from ubuclean.core.ram import (
    MemoryStats,
    RamService,
    check_swap_eligibility,
)
from ubuclean.core.runner import CommandResult


def stats(
    *,
    cached: int = 1_000,
    buffers: int = 500,
    swap_used: int = 0,
    available: int = 8_000,
    used: int = 4_000,
    total: int = 12_000,
) -> MemoryStats:
    return MemoryStats(
        total=total,
        available=available,
        used=used,
        percent=round(used / total * 100, 1) if total else 0,
        cached=cached,
        buffers=buffers,
        swap_total=4_000,
        swap_used=swap_used,
        swap_percent=round(swap_used / 4_000 * 100, 1) if swap_used else 0,
    )


def write_meminfo(path: Path, *, available: int, swap_total: int, swap_free: int) -> None:
    path.write_text(
        f"MemAvailable: {available} kB\nSwapTotal: {swap_total} kB\nSwapFree: {swap_free} kB\n",
        encoding="utf-8",
    )


def test_stats_reclaimable() -> None:
    assert stats(cached=100, buffers=50).reclaimable == 150
    assert stats(cached=0, buffers=0).reclaimable == 0


def test_swap_eligibility_ok(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=2_000_000, swap_total=1_000_000, swap_free=200_000)
    result = check_swap_eligibility(meminfo)
    assert result.can_refresh is True
    assert result.swap_used == 800_000 * 1024
    assert result.required > result.swap_used


def test_swap_eligibility_insufficient(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=100_000, swap_total=1_000_000, swap_free=100_000)
    result = check_swap_eligibility(meminfo)
    assert result.can_refresh is False
    assert "not enough" in result.message.lower()


def test_swap_eligibility_unused(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=1_000_000, swap_total=1_000_000, swap_free=1_000_000)
    result = check_swap_eligibility(meminfo)
    assert result.can_refresh is False
    assert "not currently in use" in result.message


def test_swap_eligibility_missing(tmp_path: Path) -> None:
    result = check_swap_eligibility(tmp_path / "missing")
    assert result.can_refresh is False
    assert "meminfo" in result.message.lower()


def test_drop_caches_success(context: Context) -> None:
    before = stats(cached=1000, buffers=500)
    after = stats(cached=100, buffers=50)
    service = RamService(context, stats_provider=iter([before, after]).__next__)
    outcome = service.drop_caches(2)
    assert outcome.ok is True
    assert outcome.freed_bytes == 1350
    assert outcome.before.reclaimable == 1500
    assert outcome.after.reclaimable == 150
    assert context.history.entries()[0].action == "ram"


def test_drop_caches_dry_run(context: Context, fake_runner) -> None:
    service = RamService(context.with_dry_run(True), stats_provider=lambda: stats())
    outcome = service.drop_caches(3)
    assert outcome.ok is True
    assert outcome.dry_run is True
    assert outcome.freed_bytes == 0
    assert fake_runner.calls == []


def test_drop_caches_auth_cancelled(context: Context, fake_runner) -> None:
    fake_runner.responses["pkexec"] = CommandResult(argv=("pkexec",), returncode=126)
    service = RamService(context, stats_provider=lambda: stats())
    outcome = service.drop_caches(1)
    assert outcome.ok is False
    assert "cancelled" in outcome.errors[0].lower()
    assert context.history.entries()[0].ok is False


def test_swap_refresh_blocked_before_helper(
    context: Context, fake_runner, tmp_path: Path
) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=100_000, swap_total=1_000_000, swap_free=100_000)
    service = RamService(context, stats_provider=lambda: stats(swap_used=900_000), meminfo_path=meminfo)
    outcome = service.swap_refresh()
    assert outcome.ok is False
    assert "not enough" in outcome.errors[0].lower()
    assert fake_runner.calls == []


def test_swap_refresh_dry_run(context: Context, fake_runner, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=2_000_000, swap_total=1_000_000, swap_free=100_000)
    service = RamService(
        context.with_dry_run(True),
        stats_provider=lambda: stats(swap_used=900_000),
        meminfo_path=meminfo,
    )
    outcome = service.swap_refresh()
    assert outcome.ok is True
    assert outcome.dry_run is True
    assert fake_runner.calls == []


def test_swap_refresh_success(context: Context, fake_runner, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=2_000_000, swap_total=1_000_000, swap_free=100_000)
    service = RamService(
        context,
        stats_provider=lambda: stats(swap_used=900_000),
        meminfo_path=meminfo,
    )
    outcome = service.swap_refresh()
    assert outcome.ok is True
    assert fake_runner.calls[-1][-1] == "swap-refresh"
    assert context.history.entries()[0].category == "memory"


def test_swap_refresh_helper_failure(context: Context, fake_runner, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=2_000_000, swap_total=1_000_000, swap_free=100_000)
    fake_runner.responses["pkexec"] = CommandResult(argv=("pkexec",), returncode=1, stderr="gagal")
    service = RamService(context, stats_provider=lambda: stats(swap_used=900_000), meminfo_path=meminfo)
    outcome = service.swap_refresh()
    assert outcome.ok is False
    assert "gagal" in outcome.errors[0].lower()
