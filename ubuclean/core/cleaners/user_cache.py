"""User cache cleaner in ``~/.cache``."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.cleaners.common import browser_cache_dirs, dev_cache_dirs
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import list_children
from ubuclean.core.registry import get_category


class UserCacheCleaner(BaseCleaner):
    """Cleans the contents of ``~/.cache`` except caches owned by other categories."""

    category = get_category("user-cache")

    def scan(self, ctx: Context) -> Preview:
        """List the first-level entries in ~/.cache along with their sizes."""
        cache = ctx.paths.cache_dir
        if not cache.is_dir():
            return self.new_preview(notes=["~/.cache directory not found."])
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
                    detail="Moved to the UbuClean quarantine",
                    path=path,
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Items are moved to quarantine and can be restored before the retention period expires.",
            "Browser cache and developer cache have separate categories.",
        ]
        return self.new_preview(items=items, notes=notes)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Quarantine all cache entries that were previewed."""
        paths = [item.path for item in preview.items if item.path is not None]
        return self.remove_paths(ctx, paths)
