"""Cleaner cache developer (pip, npm, yarn, pnpm, cargo, gradle, Go)."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.registry import get_category


def cache_targets(home: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    """Daftar (label, path) cache developer yang umum."""
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
    """Membersihkan cache tool developer; semuanya opsional."""

    category = get_category("dev-caches")

    def __init__(self, home: Path | None = None, cache_dir: Path | None = None) -> None:
        self._home = Path(home) if home else None
        self._cache_dir = Path(cache_dir) if cache_dir else None

    def _targets(self, ctx: Context) -> list[tuple[str, Path]]:
        home = self._home or ctx.paths.home
        cache = self._cache_dir or ctx.paths.cache_dir
        return cache_targets(home, cache)

    def scan(self, ctx: Context) -> Preview:
        """Ukur setiap cache developer yang ada."""
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
            "Cache developer akan dibuat ulang saat tool dipakai kembali.",
            "Semua item dipindahkan ke karantina UbuClean.",
        ]
        if not items:
            notes.append("Tidak ada cache developer yang ditemukan.")
        return self.new_preview(items=items, notes=notes)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Karantina cache developer."""
        paths = [item.path for item in preview.items if item.path is not None]
        return self.remove_paths(ctx, paths)
