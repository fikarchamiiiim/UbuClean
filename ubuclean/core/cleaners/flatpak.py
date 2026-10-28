"""Cleaner for unused Flatpak runtimes and extensions."""

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
    """Cleans Flatpak runtimes that no application references."""

    category = get_category("flatpak-unused")

    def scan(self, ctx: Context) -> Preview:
        """Compare installed runtimes with the runtimes used by applications."""
        if ctx.runner.which("flatpak") is None:
            return self.new_preview(
                supported=False,
                notes=["Flatpak is not installed on this system."],
            )
        runtimes_result = ctx.runner.run(
            ["flatpak", "list", "--runtime", "--columns=ref,size"],
            timeout=120,
        )
        if runtimes_result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[runtimes_result.stderr.strip() or "flatpak list --runtime failed."],
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
                detail="Not used by any installed application",
            )
            for ref, size in unused
        ]
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Deleted directly by flatpak, not quarantined.",
            "Runtimes still used by applications will not be deleted.",
        ]
        if not items:
            notes.append("No unused Flatpak runtimes.")
        commands = [ctx.privileged.describe(["flatpak-unused"])] if items else []
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Run flatpak uninstall --unused through the helper."""
        if not preview.items:
            return CleanOutcome(category_id=self.category.id, detail="No unused runtimes.")
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=len(preview.items),
                detail="Preview: flatpak uninstall was not run.",
            )
        return self.helper_clean(ctx, ["flatpak-unused"], timeout=900)
