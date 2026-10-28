"""Cleaner for the user's Trash."""

from __future__ import annotations

import configparser
from pathlib import Path
from urllib.parse import unquote

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import list_children, safe_remove_many
from ubuclean.core.registry import get_category


class TrashCleaner(BaseCleaner):
    """Empties the Trash through the UbuClean quarantine."""

    category = get_category("trash")

    def _files_dir(self, ctx: Context) -> Path:
        return ctx.paths.home / ".local/share/Trash/files"

    def _info_dir(self, ctx: Context) -> Path:
        return ctx.paths.home / ".local/share/Trash/info"

    def scan(self, ctx: Context) -> Preview:
        """List the Trash contents along with their sizes."""
        files_dir = self._files_dir(ctx)
        if not files_dir.is_dir():
            return self.new_preview(notes=["Trash is empty or not found."])
        items = []
        for path, size in list_children(files_dir):
            items.append(
                PreviewItem(
                    id=str(path),
                    label=path.name,
                    size=size,
                    detail="Moved to the UbuClean quarantine",
                    path=path,
                )
            )
        notes = [
            "Items are moved to the UbuClean quarantine so they can be restored.",
            "Restoring returns items to their original location when .trashinfo metadata is available.",
        ]
        return self.new_preview(items=items, notes=notes)

    def _original_paths(self, ctx: Context, sources: list[Path]) -> dict[str, Path]:
        """Read the original location from the .trashinfo file when present."""
        overrides: dict[str, Path] = {}
        info_dir = self._info_dir(ctx)
        for source in sources:
            info = info_dir / f"{source.name}.trashinfo"
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                with info.open("r", encoding="utf-8", errors="replace") as handle:
                    parser.read_file(handle)
                raw = parser.get("Trash Info", "Path", fallback="")
            except (OSError, configparser.Error):
                continue
            path = Path(unquote(raw))
            if raw and path.is_absolute():
                overrides[str(source)] = path
        return overrides

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Quarantine the Trash contents, then delete the .trashinfo metadata."""
        files = [item.path for item in preview.items if item.path is not None]
        if ctx.dry_run:
            return self.remove_paths(ctx, files)
        overrides = self._original_paths(ctx, files)
        entry = ctx.quarantine.quarantine(
            self.category.id,
            files,
            original_overrides=overrides,
        )
        if entry is None:
            return CleanOutcome(
                category_id=self.category.id,
                failed=len(files),
                errors=["No Trash items could be quarantined."],
            )
        outcome = CleanOutcome(
            category_id=self.category.id,
            freed_bytes=entry.total_bytes,
            removed=len(entry.items),
            failed=len(entry.errors),
            quarantined=len(entry.items),
            quarantined_bytes=entry.total_bytes,
            quarantine_id=entry.action_id,
            errors=list(entry.errors),
        )
        info_paths = [self._info_dir(ctx) / f"{path.name}.trashinfo" for path in files]
        results = safe_remove_many(info_paths, ctx.policy)
        failures = [result for result in results if result.error]
        outcome.failed += len(failures)
        outcome.errors.extend(result.error for result in failures if result.error)
        return outcome
