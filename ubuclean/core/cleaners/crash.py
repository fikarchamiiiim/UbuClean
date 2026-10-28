"""Cleaner for crash reports in /var/crash."""

from __future__ import annotations

import os
from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category

CRASH_DIR = Path("/var/crash")


class CrashCleaner(BaseCleaner):
    """Deletes old crash report files."""

    category = get_category("crash")

    def __init__(self, crash_dir: Path | None = None) -> None:
        self.crash_dir = Path(crash_dir) if crash_dir else CRASH_DIR

    def scan(self, ctx: Context) -> Preview:
        """List the files in /var/crash that are visible to the user."""
        if not self.crash_dir.is_dir():
            return self.new_preview(notes=["/var/crash directory not found."])
        items: list[PreviewItem] = []
        denied = 0
        try:
            entries = os.scandir(self.crash_dir)
        except OSError as exc:
            return self.new_preview(
                supported=False,
                errors=[f"Cannot read /var/crash: {exc}"],
            )
        with entries:
            for entry in entries:
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    denied += 1
                    continue
                items.append(
                    PreviewItem(
                        id=entry.name,
                        label=entry.name,
                        size=size,
                        detail="Deleted directly (no quarantine)",
                    )
                )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = ["Crash reports are deleted directly; they are not quarantined."]
        if denied:
            notes.append(f"{denied} reports could not be inspected because permission was denied.")
        commands = (
            [ctx.privileged.describe(["clean-crash", *[item.id for item in items]])]
            if items
            else []
        )
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Delete the crash reports listed in the preview."""
        names = [item.id for item in preview.items]
        if not names:
            return CleanOutcome(category_id=self.category.id, detail="No crash reports.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(names),
                detail="Preview: crash reports were not deleted.",
            )
        return self.helper_clean(ctx, ["clean-crash", *names])
