"""List of cache paths owned by other categories (avoids double counting)."""

from __future__ import annotations

from pathlib import Path

from ubuclean import constants

BROWSER_CACHE_RELATIVE = tuple(constants.WEB_BROWSER_CACHE_DIRS)

DEV_CACHE_RELATIVE = (
    "pip",
    "yarn",
    "pnpm",
    "go-build",
    "uv",
    "pre-commit",
)

DEV_CACHE_ABSOLUTE_RELATIVE = (
    (".npm", "_cacache"),
    (".cargo", "registry", "cache"),
    (".gradle", "caches"),
    (".local/share/pnpm", "store"),
)


def browser_cache_dirs(cache_dir: Path) -> list[Path]:
    """Browser cache directories present in ``~/.cache``."""
    return [cache_dir / relative for relative in BROWSER_CACHE_RELATIVE if (cache_dir / relative).exists()]


def dev_cache_dirs(home: Path, cache_dir: Path) -> list[Path]:
    """Developer cache directories located outside ~/.cache."""
    result = [cache_dir / relative for relative in DEV_CACHE_RELATIVE if (cache_dir / relative).exists()]
    for parts in DEV_CACHE_ABSOLUTE_RELATIVE:
        candidate = home.joinpath(*parts)
        if candidate.exists() and candidate not in result:
            result.append(candidate)
    return result
