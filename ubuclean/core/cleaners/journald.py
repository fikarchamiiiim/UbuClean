"""Cleaner for old journald logs."""

from __future__ import annotations

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category
from ubuclean.core.util import parse_human_size


class JournalCleaner(BaseCleaner):
    """Vacuum journald based on a day limit."""

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
        """Show the current journal usage as an upper bound."""
        if ctx.runner.which("journalctl") is None:
            return self.new_preview(
                supported=False,
                notes=["journalctl is unavailable on this system."],
            )
        days = self._days(ctx)
        usage = self._usage(ctx)
        items = [
            PreviewItem(
                id="journal",
                label=f"journald logs older than {days} days",
                size=usage,
                detail="Estimated upper bound; the actual size is measured during cleaning",
            )
        ]
        notes = [
            "Deleted directly with journalctl --vacuum-time; not quarantined.",
            f"The size above is the current journal total, not only the old logs.",
        ]
        commands = [ctx.privileged.describe(["journal-vacuum", f"{days}d"])]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Run the journald vacuum through the helper."""
        days = self._days(ctx)
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=1,
                detail="Preview: journalctl --vacuum-time was not run.",
            )
        return self.helper_clean(ctx, ["journal-vacuum", f"{days}d"], timeout=600)
