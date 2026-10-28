"""Developer cache cleaner (pip, npm, yarn, pnpm, cargo, gradle, Go)."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category


def cache_targets(home: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    """List of common developer cache (label, path) pairs."""
    return [
        ("pip", cache_dir / "pip"),
        ("npm", home / ".npm/_cacache"),
        ("yarn", cache_dir / "yarn"),
        ("pnpm", home / ".local/share/pnpm/store"),
        ("pnpm cache", cache_dir / "pnpm"),
        ("cargo", home / ".cargo/registry/cache"),
        ("gradle", home / ".gradle/caches"),
        ("Go build", cache_dir / "go-build"),
        ("uv", cache_dir / "uv"),
        ("pre-commit", cache_dir / "pre-commit"),
    ]


class DevCacheCleaner(BaseCleaner):
    """Cleans developer tool caches; all of them are optional."""

    category = get_category("dev-caches")

    def __init__(self, home: Path | None = None, cache_dir: Path | None = None) -> None:
        self._home = Path(home) if home else None
        self._cache_dir = Path(cache_dir) if cache_dir else None

    def _targets(self, ctx: Context) -> list[tuple[str, Path]]:
        home = self._home or ctx.paths.home
        cache = self._cache_dir or ctx.paths.cache_dir
        return cache_targets(home, cache)

    def scan(self, ctx: Context) -> Preview:
        """Measure each developer cache that exists."""
        items: list[PreviewItem] = []
        for label_text, path in self._targets(ctx):
            if not path.is_dir():
                continue
            items.append(
                PreviewItem(
                    id=str(path),
                    label=label_text,
                    size=self.item(path).size,
                    detail=str(path).replace(str(ctx.paths.home), "~"),
                    path=path,
                )
            )
        items.sort(key=lambda item: item.size, reverse=True)
        notes = [
            "Developer caches will be recreated when the tool is used again.",
            "All items are moved to the UbuClean quarantine.",
        ]
        if not items:
            notes.append("No developer caches found.")
        return self.new_preview(items=items, notes=notes)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Quarantine developer caches."""
        paths = [item.path for item in preview.items if item.path is not None]
        return self.remove_paths(ctx, paths)
