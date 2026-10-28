"""Cleaner cache pengguna di ``~/.cache``."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.cleaners.common import browser_cache_dirs, dev_cache_dirs
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import list_children
from ubuclean.core.registry import get_category


class UserCacheCleaner(BaseCleaner):
    """Membersihkan isi ``~/.cache`` kecuali cache milik kategori lain."""

    category = get_category("user-cache")

    def scan(self, ctx: Context) -> Preview:
        """Daftar entri tingkat pertama di ~/.cache beserta ukurannya."""
        cache = ctx.paths.cache_dir
        if not cache.is_dir():
            return self.new_preview(notes=["Direktori ~/.cache tidak ditemukan."])
        excluded: set[Path] = set()
        for path in browser_cache_dirs(cache):
            excluded.add(path)
            if path.parent != cache:
                excluded.add(path.parent)
        for path in dev_cache_dirs(ctx.paths.home, cache):
            excluded.add(path)
        items: list[PreviewItem] = []
        for path, size in list_children(cache):
            if path in excluded or path.name == "ubuclean":
                continue
            items.append(
                PreviewItem(
                    id=str(path),
                    label=str(path).replace(str(ctx.paths.home), "~"),
                    size=size,
                    detail="Dipindahkan ke karantina UbuClean",
                    path=path,
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Item dipindahkan ke karantina dan bisa dipulihkan sebelum masa retensi habis.",
            "Cache browser dan cache developer memiliki kategori terpisah.",
        ]
        return self.new_preview(items=items, notes=notes)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Karantina semua entri cache yang sudah dipreview."""
        paths = [item.path for item in preview.items if item.path is not None]
        return self.remove_paths(ctx, paths)
