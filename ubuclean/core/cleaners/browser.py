"""Cleaner cache browser (opsional, tidak dicentang secara default)."""

from __future__ import annotations

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.cleaners.common import BROWSER_CACHE_RELATIVE
from ubuclean.core.context import Context
from ubuclean.core.models import CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import path_size
from ubuclean.core.registry import get_category

BROWSER_LABELS = {
    "mozilla/firefox": "Firefox",
    "google-chrome": "Google Chrome",
    "google-chrome-beta": "Google Chrome Beta",
    "chromium": "Chromium",
    "chromium-browser": "Chromium",
}


def running_browser_names() -> list[str]:
    """Nama proses browser yang sedang berjalan (best-effort)."""
    try:
        import psutil
    except ImportError:
        return []
    names = {"firefox", "chrome", "chromium", "google-chrome", "chromium-browser"}
    running: set[str] = set()
    try:
        for process in psutil.process_iter(["name"]):
            try:
                name = (process.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name in names:
                running.add(name)
    except (psutil.Error, OSError):
        return []
    return sorted(running)


class BrowserCacheCleaner(BaseCleaner):
    """Membersihkan cache browser tanpa menyentuh profil pengguna."""

    category = get_category("browser-cache")

    def scan(self, ctx: Context) -> Preview:
        """Daftar direktori cache browser yang ada."""
        cache = ctx.paths.cache_dir
        items: list[PreviewItem] = []
        for relative in BROWSER_CACHE_RELATIVE:
            path = cache / relative
            if not path.is_dir():
                continue
            items.append(
                PreviewItem(
                    id=str(path),
                    label=BROWSER_LABELS.get(relative, relative),
                    size=path_size(path),
                    detail=str(path).replace(str(ctx.paths.home), "~"),
                    path=path,
                )
            )
        notes = [
            "Hanya folder cache yang dihapus; profil, bookmark, dan riwayat tetap utuh.",
            "Kategori ini tidak pernah ikut pembersihan terjadwal.",
        ]
        running = running_browser_names()
        if running:
            notes.append(
                "Browser sedang berjalan (" + ", ".join(running) + "); tutup browser agar pembersihan optimal."
            )
        if not items:
            notes.append("Tidak ada cache browser yang ditemukan.")
        return self.new_preview(items=items, notes=notes)

    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Karantina folder cache browser."""
        paths = [item.path for item in preview.items if item.path is not None]
        return self.remove_paths(ctx, paths)
