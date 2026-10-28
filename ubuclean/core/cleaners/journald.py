"""Cleaner log journald lama."""

from __future__ import annotations

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category
from ubuclean.core.util import parse_human_size


class JournalCleaner(BaseCleaner):
    """Vakum journald berdasarkan batas hari."""

    category = get_category("journal")

    def _days(self, ctx: Context) -> int:
        return int(ctx.config.get("journal_days", 14))

    def _usage(self, ctx: Context) -> int:
        result = ctx.runner.run(
            ["journalctl", "--disk-usage"], timeout=30, env={"LC_ALL": "C", "LANG": "C"}
        )
        if result.returncode != 0:
            return 0
        for line in result.stdout.splitlines():
            if "in the file system" in line:
                return parse_human_size(line)
        return 0

    def scan(self, ctx: Context) -> Preview:
        """Tampilkan pemakaian journal saat ini sebagai batas atas."""
        if ctx.runner.which("journalctl") is None:
            return self.new_preview(
                supported=False,
                notes=["journalctl tidak tersedia di sistem ini."],
            )
        days = self._days(ctx)
        usage = self._usage(ctx)
        items = [
            PreviewItem(
                id="journal",
                label=f"Log journald lebih lama dari {days} hari",
                size=usage,
                detail="Perkiraan batas atas; ukuran sebenarnya dihitung saat pembersihan",
            )
        ]
        notes = [
            "Dihapus langsung dengan journalctl --vacuum-time; tidak masuk karantina.",
            f"Ukuran di atas adalah total journal saat ini, bukan hanya log lama.",
        ]
        commands = [ctx.privileged.describe(["journal-vacuum", f"{days}d"])]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Jalankan vacuum journald melalui helper."""
        days = self._days(ctx)
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=1,
                detail="Pratinjau: journalctl --vacuum-time tidak dijalankan.",
            )
        return self.helper_clean(ctx, ["journal-vacuum", f"{days}d"], timeout=600)
