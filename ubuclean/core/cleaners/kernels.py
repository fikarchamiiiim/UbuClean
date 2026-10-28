"""Cleaner kernel lama dengan perlindungan kernel berjalan dan cadangan."""

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
    """Satu paket kernel yang terpasang."""

    name: str
    release: str
    size: int


def version_sort_key(release: str) -> tuple[tuple[int, object], ...]:
    """Kunci pengurutan versi kernel (angka dibanding angka)."""
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
    """Memilih kernel lama yang aman dihapus."""

    category = get_category("kernels")

    def _query(self, ctx: Context) -> list[KernelPackage]:
        result = ctx.runner.run(
            ["dpkg-query", "-W", f"-f={DPKG_FORMAT}", *KERNEL_PATTERNS],
            timeout=60,
        )
        return _parse_dpkg(result.stdout or "")

    def scan(self, ctx: Context) -> Preview:
        """Pilih kandidat kernel: sisakan kernel berjalan dan satu cadangan."""
        packages = self._query(ctx)
        if not packages:
            return self.new_preview(
                notes=[
                    "Tidak ada paket kernel yang dikelola dpkg di sistem ini.",
                    "Kernel yang sedang berjalan tidak pernah dihapus.",
                ]
            )
        running = platform.release()
        images = sorted(
            {package.release for package in packages if package.name.startswith("linux-image-")},
            key=version_sort_key,
            reverse=True,
        )
        if not images:
            return self.new_preview(notes=["Tidak ada paket linux-image yang terpasang."])
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
                            detail="Dilindungi (kernel berjalan/cadangan)",
                            protected=True,
                        )
                    )
                else:
                    items.append(
                        PreviewItem(
                            id=package.name,
                            label=package.name,
                            size=package.size,
                            detail="Akan dihapus",
                        )
                    )
                    candidate_packages.append(package.name)
        notes = [
            f"Kernel berjalan: {running} (tidak pernah dihapus).",
            f"{len(keep)} kernel dipertahankan sebagai kernel berjalan/cadangan.",
            "Dihapus langsung dengan apt, bukan karantina.",
        ]
        if not candidates:
            notes.append("Tidak ada kernel lama yang aman dihapus saat ini.")
        if running not in images:
            notes.append(
                "Kernel berjalan tidak terdeteksi sebagai paket dpkg; pembersihan dibatasi untuk keamanan."
            )
        commands = (
            [ctx.privileged.describe(["purge-kernels", *candidate_packages])]
            if candidate_packages
            else []
        )
        return self.new_preview(items=items, notes=notes, commands=commands)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Hapus paket kernel kandidat lewat helper."""
        packages = [
            item.id for item in preview.items if not item.protected and item.id
        ]
        if not packages:
            return CleanOutcome(
                category_id=self.category.id,
                detail="Tidak ada kernel lama yang aman dihapus.",
            )
        if ctx.dry_run:
            return CleanOutcome(
                category_id=self.category.id,
                freed_bytes=sum(item.size for item in preview.items if not item.protected),
                removed=len(packages),
                detail="Pratinjau: purge kernel tidak dijalankan.",
            )
        return self.helper_clean(ctx, ["purge-kernels", *packages], timeout=900)
