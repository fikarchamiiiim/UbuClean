"""Cleaner laporan crash di /var/crash."""

from __future__ import annotations

import os
from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category

CRASH_DIR = Path("/var/crash")


class CrashCleaner(BaseCleaner):
    """Menghapus file laporan crash lama."""

    category = get_category("crash")

    def __init__(self, crash_dir: Path | None = None) -> None:
        self.crash_dir = Path(crash_dir) if crash_dir else CRASH_DIR

    def scan(self, ctx: Context) -> Preview:
        """Daftar file di /var/crash yang terlihat oleh pengguna."""
        if not self.crash_dir.is_dir():
            return self.new_preview(notes=["Direktori /var/crash tidak ditemukan."])
        items: list[PreviewItem] = []
        denied = 0
        try:
            entries = os.scandir(self.crash_dir)
        except OSError as exc:
            return self.new_preview(
                supported=False,
                errors=[f"Tidak bisa membaca /var/crash: {exc}"],
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
                        detail="Dihapus langsung (bukan karantina)",
                    )
                )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = ["Laporan crash dihapus langsung; tidak masuk karantina."]
        if denied:
            notes.append(f"{denied} laporan tidak bisa diperiksa karena izin ditolak.")
        commands = (
            [ctx.privileged.describe(["clean-crash", *[item.id for item in items]])]
            if items
            else []
        )
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Hapus laporan crash yang terdaftar di preview."""
        names = [item.id for item in preview.items]
        if not names:
            return CleanOutcome(category_id=self.category.id, detail="Tidak ada laporan crash.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(names),
                detail="Pratinjau: laporan crash tidak dihapus.",
            )
        return self.helper_clean(ctx, ["clean-crash", *names])
