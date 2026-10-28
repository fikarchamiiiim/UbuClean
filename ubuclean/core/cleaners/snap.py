"""Cleaner for snap revisions that are already disabled."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import path_size
from ubuclean.core.registry import get_category

SNAP_DIR = Path("/var/lib/snapd/snaps")


class SnapCleaner(BaseCleaner):
    """Deletes old snap revisions that are no longer active."""

    category = get_category("snap-revisions")

    def _size(self, name: str, revision: str) -> int:
        return sum(path_size(path) for path in SNAP_DIR.glob(f"{name}_{revision}.snap"))

    def scan(self, ctx: Context) -> Preview:
        """List snap revisions with disabled status."""
        if ctx.runner.which("snap") is None:
            return self.new_preview(
                supported=False,
                notes=["Snap is not installed on this system."],
            )
        result = ctx.runner.run(["snap", "list", "--all"], timeout=60)
        if result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[result.stderr.strip() or f"snap list failed ({result.returncode})"],
            )
        items: list[PreviewItem] = []
        for line in result.stdout.splitlines()[1:]:
            columns = line.split()
            if len(columns) < 6:
                continue
            name, version, revision, tracking = columns[0], columns[1], columns[2], columns[3]
            notes_text = columns[-1]
            if "disabled" not in notes_text:
                continue
            items.append(
                PreviewItem(
                    id=f"{name}:{revision}",
                    label=f"{name} (revision {revision})",
                    size=self._size(name, revision),
                    detail=f"Version {version} · {tracking}",
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Old snap revisions are deleted directly with snap remove; not quarantined.",
            "Only revisions with disabled status are deleted.",
        ]
        if items and not any(item.size for item in items):
            notes.append("Revision size cannot be read; the figure may show 0 B.")
        commands = (
            [ctx.privileged.describe(["snap-old-revisions", *[item.id for item in items]])]
            if items
            else []
        )
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Delete the revisions listed in the preview."""
        specs = [item.id for item in preview.items]
        if not specs:
            return CleanOutcome(category_id=self.category.id, detail="No old snap revisions.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(specs),
                detail="Preview: snap remove was not run.",
            )
        return self.helper_clean(ctx, ["snap-old-revisions", *specs], timeout=900)
