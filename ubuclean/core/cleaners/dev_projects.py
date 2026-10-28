"""Cleaner folder proyek lama (node_modules, __pycache__, target, .venv)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import path_size
from ubuclean.core.registry import get_category

MARKERS = ("node_modules", "__pycache__", "target", ".venv")
SKIP_DIRS = {".git", ".hg", ".svn", ".cache"}
MAX_ITEMS = 150


def expand_roots(values: list[str], home: Path) -> list[Path]:
    """Expanded folder induk dari konfigurasi (``~`` mengikuti home pengguna)."""
    roots: list[Path] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        if text == "~":
            text = str(home)
        elif text.startswith("~/"):
            text = str(home) + text[1:]
        text = os.path.expandvars(text)
        path = Path(text)
        if not path.is_absolute():
            path = home / path
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


class DevProjectsCleaner(BaseCleaner):
    """Menemukan folder artefak build pada proyek yang lama tidak disentuh."""

    category = get_category("dev-projects")

    def __init__(
        self,
        home: Path | None = None,
        roots: list[Path] | None = None,
        days: int | None = None,
    ) -> None:
        self._home = Path(home) if home else None
        self._roots = [Path(root) for root in roots] if roots is not None else None
        self._days = days

    def _days_value(self, ctx: Context) -> int:
        if self._days is not None:
            return max(1, int(self._days))
        return int(ctx.config.get("developer_days", 90))

    def _roots_for(self, ctx: Context) -> list[Path]:
        if self._roots is not None:
            return [root for root in self._roots if root.is_dir()]
        home = self._home or ctx.paths.home
        values = list(ctx.config.get("developer_roots", []))
        return expand_roots(values, home)

    def scan(self, ctx: Context) -> Preview:
        """Telusuri folder induk dan kumpulkan marker build yang lama."""
        home = (self._home or ctx.paths.home).resolve()
        days = self._days_value(ctx)
        cutoff = time.time() - days * 86400
        roots = self._roots_for(ctx)
        items: list[PreviewItem] = []
        scanned_dirs = 0
        errors = 0
        for root in roots:
            if len(items) >= MAX_ITEMS:
                break
            for directory, dirnames, _filenames in os.walk(root, followlinks=False):
                if len(items) >= MAX_ITEMS:
                    break
                scanned_dirs += 1
                current = Path(directory)
                try:
                    if current.is_symlink():
                        dirnames[:] = []
                        continue
                except OSError:
                    continue
                kept: list[str] = []
                for name in dirnames:
                    candidate = current / name
                    if name in MARKERS:
                        try:
                            marker_mtime = candidate.stat().st_mtime
                            project_mtime = current.stat().st_mtime
                        except OSError:
                            errors += 1
                            continue
                        if marker_mtime >= cutoff or project_mtime >= cutoff:
                            continue
                        try:
                            resolved = candidate.resolve(strict=False)
                        except OSError:
                            continue
                        if home not in resolved.parents:
                            continue
                        if len(items) >= MAX_ITEMS:
                            break
                        items.append(
                            PreviewItem(
                                id=str(resolved),
                                label=str(resolved).replace(str(home), "~"),
                                size=path_size(resolved),
                                detail=f"Proyek tidak diubah sejak {days} hari",
                                path=resolved,
                            )
                        )
                        continue
                    if name in SKIP_DIRS or name.startswith("."):
                        continue
                    kept.append(name)
                dirnames[:] = kept
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            f"Memeriksa {len(roots)} folder induk; hanya marker build yang lebih tua dari {days} hari.",
            "Folder dipindahkan ke karantina UbuClean.",
        ]
        if not roots:
            notes.append(
                "Belum ada folder induk yang valid. Pilih folder proyek di Pengaturan atau pembersih developer."
            )
        if errors:
            notes.append(f"{errors} folder tidak dapat diperiksa.")
        if scanned_dirs:
            notes.append(f"{scanned_dirs} folder diperiksa.")
        return self.new_preview(items=items, notes=notes)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Karantina marker build yang dipilih."""
        paths = [item.path for item in preview.items if item.path is not None]
        return self.remove_paths(ctx, paths)
