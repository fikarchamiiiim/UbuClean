"""Cleaner revisi snap yang sudah disabled."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import path_size
from ubuclean.core.registry import get_category

SNAP_DIR = Path("/var/lib/snapd/snaps")


class SnapCleaner(BaseCleaner):
    """Menghapus revisi snap lama yang sudah tidak aktif."""

    category = get_category("snap-revisions")

    def _size(self, name: str, revision: str) -> int:
        return sum(path_size(path) for path in SNAP_DIR.glob(f"{name}_{revision}.snap"))

    def scan(self, ctx: Context) -> Preview:
        """Daftar revisi snap dengan status disabled."""
        if ctx.runner.which("snap") is None:
            return self.new_preview(
                supported=False,
                notes=["Snap tidak terpasang di sistem ini."],
            )
        result = ctx.runner.run(["snap", "list", "--all"], timeout=60)
        if result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[result.stderr.strip() or f"snap list gagal ({result.returncode})"],
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
                    label=f"{name} (revisi {revision})",
                    size=self._size(name, revision),
                    detail=f"Versi {version} · {tracking}",
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Revisi snap lama dihapus langsung dengan snap remove; tidak masuk karantina.",
            "Hanya revisi yang berstatus disabled yang dihapus.",
        ]
        if items and not any(item.size for item in items):
            notes.append("Ukuran revisi tidak dapat dibaca; angka bisa tampil 0 B.")
        commands = (
            [ctx.privileged.describe(["snap-old-revisions", *[item.id for item in items]])]
            if items
            else []
        )
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Hapus revisi yang terdaftar di preview."""
        specs = [item.id for item in preview.items]
        if not specs:
            return CleanOutcome(category_id=self.category.id, detail="Tidak ada revisi snap lama.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(specs),
                detail="Pratinjau: snap remove tidak dijalankan.",
            )
        return self.helper_clean(ctx, ["snap-old-revisions", *specs], timeout=900)
