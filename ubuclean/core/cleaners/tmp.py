"""Cleaner file lama di /tmp (butuh helper root)."""

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
    """Menghapus file /tmp yang tidak dipakai dan lebih tua dari batas hari."""

    category = get_category("tmp")

    def __init__(self, tmp_dir: Path | None = None) -> None:
        self.tmp_dir = Path(tmp_dir) if tmp_dir else Path("/tmp")

    def _days(self, ctx: Context) -> int:
        return int(ctx.config.get("tmp_days", 7))

    def _in_use(self, ctx: Context, path: Path) -> bool:
        """Cek pemakaian file dengan fuser bila tersedia."""
        if ctx.runner.which("fuser") is None:
            return False
        result = ctx.runner.run(["fuser", "-s", "--", str(path)], timeout=3)
        return result.returncode == 0

    def scan(self, ctx: Context) -> Preview:
        """Daftar file /tmp yang lebih tua dari batas hari dan tidak dipakai."""
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
                errors=[f"Tidak bisa membaca {self.tmp_dir}: {exc}"],
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
                    detail=f"Tidak diubah sejak {days} hari",
                    path=path,
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            f"Hanya file yang tidak diubah lebih dari {days} hari dan tidak sedang dipakai.",
            "File milik pengguna lain diproses oleh helper dengan pemeriksaan yang sama.",
        ]
        if skipped_in_use:
            notes.append(f"{skipped_in_use} item dilewati karena sedang dipakai.")
        if unreadable:
            notes.append(f"{unreadable} item tidak bisa diperiksa (izin ditolak).")
        commands = [ctx.privileged.describe(["clean-tmp", str(days)])]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Jalankan pembersihan /tmp lewat helper."""
        days = self._days(ctx)
        if ctx.dry_run:
            outcome = CleanOutcome(category_id=self.category.id)
            outcome.freed_bytes = preview.total_bytes
            outcome.removed = preview.item_count
            outcome.detail = "Pratinjau: perintah helper tidak dijalankan."
            return outcome
        return self.helper_clean(ctx, ["clean-tmp", str(days)])
