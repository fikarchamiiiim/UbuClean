"""Cleaner for old files in /tmp (requires the root helper)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import path_size
from ubuclean.core.registry import get_category

SKIP_NAMES = {
    ".X11-unix",
    ".ICE-unix",
    ".XIM-unix",
    ".font-unix",
    ".Test-unix",
    "snap-private-tmp",
}
SKIP_PREFIXES = ("systemd-private-", "snap-private-tmp", "pulse-")


class TmpCleaner(BaseCleaner):
    """Deletes /tmp files that are unused and older than the day limit."""

    category = get_category("tmp")

    def __init__(self, tmp_dir: Path | None = None) -> None:
        self.tmp_dir = Path(tmp_dir) if tmp_dir else Path("/tmp")

    def _days(self, ctx: Context) -> int:
        return int(ctx.config.get("tmp_days", 7))

    def _in_use(self, ctx: Context, path: Path) -> bool:
        """Check file usage with fuser when available."""
        if ctx.runner.which("fuser") is None:
            return False
        result = ctx.runner.run(["fuser", "-s", "--", str(path)], timeout=3)
        return result.returncode == 0

    def scan(self, ctx: Context) -> Preview:
        """List the /tmp files that are older than the day limit and not in use."""
        days = self._days(ctx)
        cutoff = time.time() - days * 86400
        items: list[PreviewItem] = []
        skipped_in_use = 0
        unreadable = 0
        try:
            entries = list(os.scandir(self.tmp_dir))
        except OSError as exc:
            return self.new_preview(
                supported=False,
                errors=[f"Cannot read {self.tmp_dir}: {exc}"],
            )
        for entry in entries:
            name = entry.name
            if name in SKIP_NAMES or name.startswith(SKIP_PREFIXES):
                continue
            try:
                stat_result = entry.stat(follow_symlinks=False)
            except OSError:
                unreadable += 1
                continue
            if stat_result.st_mtime >= cutoff:
                continue
            path = Path(entry.path)
            if self._in_use(ctx, path):
                skipped_in_use += 1
                continue
            size = path_size(path)
            items.append(
                PreviewItem(
                    id=str(path),
                    label=str(path),
                    size=size,
                    detail=f"Unchanged for {days} days",
                    path=path,
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            f"Only files unchanged for more than {days} days and not currently in use.",
            "Files owned by other users are handled by the helper with the same checks.",
        ]
        if skipped_in_use:
            notes.append(f"{skipped_in_use} items skipped because they are in use.")
        if unreadable:
            notes.append(f"{unreadable} items could not be inspected (permission denied).")
        commands = [ctx.privileged.describe(["clean-tmp", str(days)])]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Run the /tmp cleanup through the helper."""
        days = self._days(ctx)
        if ctx.dry_run:
            outcome = CleanOutcome(category_id=self.category.id)
            outcome.freed_bytes = preview.total_bytes
            outcome.removed = preview.item_count
            outcome.detail = "Preview: the helper command was not run."
            return outcome
        return self.helper_clean(ctx, ["clean-tmp", str(days)])
