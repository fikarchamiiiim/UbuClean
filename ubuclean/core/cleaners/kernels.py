"""Old kernel cleaner with running-kernel and spare-kernel protection."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category
from ubuclean.helper import validate

DPKG_FORMAT = "${Package}\t${Installed-Size}\t${Status}\n"
KERNEL_PATTERNS = (
    "linux-image-*",
    "linux-image-unsigned-*",
    "linux-headers-*",
    "linux-modules-*",
    "linux-modules-extra-*",
)


@dataclass(frozen=True, slots=True)
class KernelPackage:
    """A single installed kernel package."""

    name: str
    release: str
    size: int


def version_sort_key(release: str) -> tuple[tuple[int, object], ...]:
    """Kernel version sort key (numbers compared as numbers)."""
    parts = re.split(r"[.\-]", release)
    key: list[tuple[int, object]] = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part)))
        else:
            key.append((0, part))
    return tuple(key)


def _parse_dpkg(output: str) -> list[KernelPackage]:
    packages: list[KernelPackage] = []
    seen: set[str] = set()
    for line in output.splitlines():
        columns = line.split("\t")
        if len(columns) != 3:
            continue
        name, size_text, status = columns
        if " installed" not in f" {status}":
            continue
        if name in seen:
            continue
        try:
            validate.kernel_package(name)
        except validate.ValidationError:
            continue
        seen.add(name)
        try:
            size = int(size_text) * 1024
        except ValueError:
            size = 0
        packages.append(
            KernelPackage(name=name, release=validate.kernel_release(name), size=size)
        )
    return packages


class KernelCleaner(BaseCleaner):
    """Selects old kernels that are safe to delete."""

    category = get_category("kernels")

    def _query(self, ctx: Context) -> list[KernelPackage]:
        result = ctx.runner.run(
            ["dpkg-query", "-W", f"-f={DPKG_FORMAT}", *KERNEL_PATTERNS],
            timeout=60,
        )
        return _parse_dpkg(result.stdout or "")

    def scan(self, ctx: Context) -> Preview:
        """Pick kernel candidates: keep the running kernel and one spare."""
        packages = self._query(ctx)
        if not packages:
            return self.new_preview(
                notes=[
                    "No dpkg-managed kernel packages on this system.",
                    "The running kernel is never deleted.",
                ]
            )
        running = platform.release()
        images = sorted(
            {package.release for package in packages if package.name.startswith("linux-image-")},
            key=version_sort_key,
            reverse=True,
        )
        if not images:
            return self.new_preview(notes=["No linux-image packages are installed."])
        keep: set[str] = set()
        if running in images:
            keep.add(running)
        keep.add(images[0])
        if len(keep) < 2:
            for release in images[1:]:
                if release not in keep:
                    keep.add(release)
                    break
        candidates = [release for release in images if release not in keep]
        items: list[PreviewItem] = []
        candidate_packages: list[str] = []
        for release in images:
            matching = [package for package in packages if package.release == release]
            for package in matching:
                if release in keep:
                    items.append(
                        PreviewItem(
                            id=package.name,
                            label=package.name,
                            size=package.size,
                            detail="Protected (running/spare kernel)",
                            protected=True,
                        )
                    )
                else:
                    items.append(
                        PreviewItem(
                            id=package.name,
                            label=package.name,
                            size=package.size,
                            detail="Will be deleted",
                        )
                    )
                    candidate_packages.append(package.name)
        notes = [
            f"Running kernel: {running} (never deleted).",
            f"{len(keep)} kernels kept as running/spare kernels.",
            "Deleted directly with apt, not quarantined.",
        ]
        if not candidates:
            notes.append("No old kernels are safe to delete right now.")
        if running not in images:
            notes.append(
                "The running kernel was not detected as a dpkg package; cleaning is limited for safety."
            )
        commands = (
            [ctx.privileged.describe(["purge-kernels", *candidate_packages])]
            if candidate_packages
            else []
        )
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Delete the candidate kernel packages through the helper."""
        packages = [
            item.id for item in preview.items if not item.protected and item.id
        ]
        if not packages:
            return CleanOutcome(
                category_id=self.category.id,
                detail="No old kernels are safe to delete.",
            )
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=sum(item.size for item in preview.items if not item.protected),
                removed=len(packages),
                detail="Preview: kernel purge was not run.",
            )
        return self.helper_clean(ctx, ["purge-kernels", *packages], timeout=900)
