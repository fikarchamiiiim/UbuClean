"""Cleaner runtime dan extension Flatpak yang tidak dipakai."""

from __future__ import annotations

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category
from ubuclean.core.util import parse_human_size


def _parse_flatpak_listing(output: str) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        columns = line.split("\t")
        ref = columns[0].strip()
        size = parse_human_size(columns[1]) if len(columns) > 1 else 0
        if ref:
            entries.append((ref, size))
    return entries


class FlatpakCleaner(BaseCleaner):
    """Membersihkan runtime Flatpak yang tidak direferensikan aplikasi."""

    category = get_category("flatpak-unused")

    def scan(self, ctx: Context) -> Preview:
        """Bandingkan runtime terpasang dengan runtime yang dipakai aplikasi."""
        if ctx.runner.which("flatpak") is None:
            return self.new_preview(
                supported=False,
                notes=["Flatpak tidak terpasang di sistem ini."],
            )
        runtimes_result = ctx.runner.run(
            ["flatpak", "list", "--runtime", "--columns=ref,size"],
            timeout=120,
        )
        if runtimes_result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[runtimes_result.stderr.strip() or "flatpak list --runtime gagal."],
            )
        used_result = ctx.runner.run(
            ["flatpak", "list", "--app", "--columns=runtime"],
            timeout=120,
        )
        used_refs = {
            line.strip()
            for line in (used_result.stdout or "").splitlines()
            if line.strip()
        }
        runtimes = _parse_flatpak_listing(runtimes_result.stdout)
        unused = [(ref, size) for ref, size in runtimes if ref not in used_refs]
        items = [
            PreviewItem(
                id=ref,
                label=ref,
                size=size,
                detail="Tidak dipakai aplikasi terpasang",
            )
            for ref, size in unused
        ]
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Dihapus langsung oleh flatpak, bukan karantina.",
            "Runtime yang masih dipakai aplikasi tidak akan dihapus.",
        ]
        if not items:
            notes.append("Tidak ada runtime Flatpak tak terpakai.")
        commands = [ctx.privileged.describe(["flatpak-unused"])] if items else []
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Jalankan flatpak uninstall --unused melalui helper."""
        if not preview.items:
            return CleanOutcome(category_id=self.category.id, detail="Tidak ada runtime tak terpakai.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(preview.items),
                detail="Pratinjau: flatpak uninstall tidak dijalankan.",
            )
        return self.helper_clean(ctx, ["flatpak-unused"], timeout=900)
