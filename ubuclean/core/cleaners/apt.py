"""Cleaner for the apt cache and unused packages."""

from __future__ import annotations

import os
from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category
from ubuclean.core.util import parse_human_size

ARCHIVES = Path("/var/cache/apt/archives")


def parse_autoremove(output: str) -> tuple[list[str], int]:
    """Parse ``apt-get -s autoremove`` output into packages and an estimated byte count."""
    packages: list[str] = []
    collecting = False
    for line in output.splitlines():
        if line.startswith("The following packages will be REMOVED"):
            collecting = True
            continue
        if collecting:
            if not line.strip():
                break
            if line.startswith(" "):
                packages.extend(line.split())
                continue
            break
    freed = 0
    for line in output.splitlines():
        if "After this operation," in line and "disk space will be freed" in line:
            fragment = line.split("After this operation,", 1)[1].split("disk space", 1)[0]
            freed = parse_human_size(fragment)
            break
    return packages, freed


class AptCacheCleaner(BaseCleaner):
    """Cleans /var/cache/apt/archives through the helper."""

    category = get_category("apt-cache")

    def __init__(self, archives: Path | None = None) -> None:
        self.archives = Path(archives) if archives else ARCHIVES

    def scan(self, ctx: Context) -> Preview:
        """List the .deb packages in the apt cache."""
        if not self.archives.is_dir():
            return self.new_preview(notes=["apt cache directory not found."])
        items: list[PreviewItem] = []
        for directory in (self.archives, self.archives / "partial"):
            if not directory.is_dir():
                continue
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    if not entry.name.endswith(".deb"):
                        continue
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    items.append(
                        PreviewItem(
                            id=entry.path,
                            label=entry.name,
                            size=size,
                            detail="Deleted directly (no quarantine)",
                            path=Path(entry.path),
                        )
                    )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "The apt cache is deleted directly with apt-get clean; it does not go to quarantine.",
            "Installed packages are not affected.",
        ]
        commands = [ctx.privileged.describe(["clean-apt"])]
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Run apt-get clean through the helper."""
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=preview.item_count,
                detail="Preview: apt-get clean was not run.",
            )
        return self.helper_clean(ctx, ["clean-apt"])


class AptAutoremoveCleaner(BaseCleaner):
    """Removes unused dependencies with autoremove."""

    category = get_category("apt-autoremove")

    def scan(self, ctx: Context) -> Preview:
        """Simulate autoremove to see which packages would be deleted."""
        result = ctx.runner.run(
            ["apt-get", "-s", "autoremove"], timeout=120, env={"LC_ALL": "C", "LANG": "C"}
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return self.new_preview(
                supported=False,
                errors=[result.stderr.strip() or f"apt-get -s autoremove failed ({result.returncode})"],
            )
        packages, freed = parse_autoremove(output)
        items = [
            PreviewItem(id=package, label=package, size=0, detail="Will be deleted")
            for package in packages
        ]
        notes = [
            "Deleted directly by apt, not quarantined.",
            "Autoremove only removes dependencies that are no longer needed.",
        ]
        if packages and not freed:
            notes.append("apt does not provide a size estimate; the figure may show 0 B.")
        commands = [ctx.privileged.describe(["autoremove"])]
        return self.new_preview(
            items=items,
            notes=notes,
            commands=commands,
            estimated_bytes=freed if packages else 0,
        )

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Run apt-get autoremove through the helper."""
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=preview.total_bytes,
                removed=preview.item_count,
                detail="Preview: autoremove was not run.",
            )
        return self.helper_clean(ctx, ["autoremove"], timeout=900)
