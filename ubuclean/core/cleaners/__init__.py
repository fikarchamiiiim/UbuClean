"""Registry of available cleaners."""

from __future__ import annotations

from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.cleaners.apt import AptAutoremoveCleaner, AptCacheCleaner
from ubuclean.core.cleaners.browser import BrowserCacheCleaner
from ubuclean.core.cleaners.crash import CrashCleaner
from ubuclean.core.cleaners.dev_caches import DevCacheCleaner
from ubuclean.core.cleaners.dev_projects import DevProjectsCleaner
from ubuclean.core.cleaners.developer import DockerCleaner
from ubuclean.core.cleaners.flatpak import FlatpakCleaner
from ubuclean.core.cleaners.journald import JournalCleaner
from ubuclean.core.cleaners.kernels import KernelCleaner
from ubuclean.core.cleaners.snap import SnapCleaner
from ubuclean.core.cleaners.tmp import TmpCleaner
from ubuclean.core.cleaners.trash import TrashCleaner
from ubuclean.core.cleaners.user_cache import UserCacheCleaner

CLEANER_CLASSES: dict[str, type[BaseCleaner]] = {
    AptCacheCleaner.category.id: AptCacheCleaner,
    AptAutoremoveCleaner.category.id: AptAutoremoveCleaner,
    BrowserCacheCleaner.category.id: BrowserCacheCleaner,
    CrashCleaner.category.id: CrashCleaner,
    DevCacheCleaner.category.id: DevCacheCleaner,
    DevProjectsCleaner.category.id: DevProjectsCleaner,
    DockerCleaner.category.id: DockerCleaner,
    FlatpakCleaner.category.id: FlatpakCleaner,
    JournalCleaner.category.id: JournalCleaner,
    KernelCleaner.category.id: KernelCleaner,
    SnapCleaner.category.id: SnapCleaner,
    TmpCleaner.category.id: TmpCleaner,
    TrashCleaner.category.id: TrashCleaner,
    UserCacheCleaner.category.id: UserCacheCleaner,
}


def create_cleaner(category_id: str) -> BaseCleaner | None:
    """Create a cleaner instance for the category, ``None`` if not available yet."""
    cleaner_class = CLEANER_CLASSES.get(category_id)
    if cleaner_class is None:
        return None
    return cleaner_class()


def available_category_ids() -> list[str]:
    """Categories that already have a cleaner."""
    return sorted(CLEANER_CLASSES)
