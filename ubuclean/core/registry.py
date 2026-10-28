"""UbuClean cleaning category registry."""

from __future__ import annotations

from ubuclean.core.models import Category, Group

CATEGORIES: tuple[Category, ...] = (
    Category(
        id="apt-cache",
        label="APT cache",
        description=".deb packages that are already installed and no longer needed.",
        group=Group.SYSTEM,
        icon="package-x-generic-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="apt-autoremove",
        label="Unused packages",
        description="Dependencies that are no longer required by installed packages.",
        group=Group.SYSTEM,
        icon="system-software-update-symbolic",
        privileged=True,
        schedule_allowed=True,
    ),
    Category(
        id="kernels",
        label="Old kernels",
        description="Old kernels will be removed, except the running kernel and one fallback.",
        group=Group.SYSTEM,
        icon="drive-harddisk-symbolic",
        privileged=True,
        risky=True,
        default_enabled=False,
        schedule_allowed=False,
    ),
    Category(
        id="snap-revisions",
        label="Old snap revisions",
        description="Snap revisions that were disabled after an update.",
        group=Group.SYSTEM,
        icon="application-x-executable-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="flatpak-unused",
        label="Unused Flatpak runtimes",
        description="Flatpak runtimes and extensions that are not used by installed applications.",
        group=Group.SYSTEM,
        icon="application-x-executable-symbolic",
        privileged=True,
        schedule_allowed=True,
    ),
    Category(
        id="journal",
        label="Old journald logs",
        description="System logs older than the selected time limit.",
        group=Group.SYSTEM,
        icon="text-x-generic-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="crash",
        label="Crash reports",
        description="Crash reports in /var/crash that are no longer needed.",
        group=Group.SYSTEM,
        icon="dialog-warning-symbolic",
        privileged=True,
        schedule_allowed=True,
    ),
    Category(
        id="tmp",
        label="Old files in /tmp",
        description="Temporary files that are unused and older than the day limit.",
        group=Group.SYSTEM,
        icon="folder-temp-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="user-cache",
        label="Application cache",
        description="Cache in ~/.cache, including thumbnails. Kept in quarantine before deletion.",
        group=Group.USER,
        icon="folder-symbolic",
        schedule_allowed=True,
        schedule_default=True,
        quarantine=True,
    ),
    Category(
        id="browser-cache",
        label="Browser cache",
        description="Firefox, Chrome, and Chromium caches. Profiles and history are not touched.",
        group=Group.USER,
        icon="web-browser-symbolic",
        risky=True,
        default_enabled=False,
        schedule_allowed=False,
        quarantine=True,
    ),
    Category(
        id="trash",
        label="Trash",
        description="Contents of the user's Trash. Items are moved to the UbuClean quarantine.",
        group=Group.USER,
        icon="user-trash-symbolic",
        schedule_allowed=True,
        schedule_default=True,
        quarantine=True,
    ),
    Category(
        id="dev-docker",
        label="Docker",
        description="Dangling images, stopped containers, unused volumes, and build cache.",
        group=Group.DEVELOPER,
        icon="utilities-terminal-symbolic",
        privileged=True,
        default_enabled=False,
        schedule_allowed=True,
    ),
    Category(
        id="dev-caches",
        label="Developer caches",
        description="pip, npm, yarn, pnpm, cargo, gradle, and Go build caches.",
        group=Group.DEVELOPER,
        icon="folder-download-symbolic",
        default_enabled=False,
        schedule_allowed=True,
        quarantine=True,
    ),
    Category(
        id="dev-projects",
        label="Stale project folders",
        description="node_modules, __pycache__, target/, and .venv in projects not touched for a long time.",
        group=Group.DEVELOPER,
        icon="folder-symbolic",
        default_enabled=False,
        schedule_allowed=True,
        quarantine=True,
    ),
)

CATEGORY_MAP: dict[str, Category] = {category.id: category for category in CATEGORIES}


def get_category(category_id: str) -> Category:
    """Get a category by id, raises ``KeyError`` when unknown."""
    return CATEGORY_MAP[category_id]


def categories_for_group(group: Group) -> list[Category]:
    """All categories in one group."""
    return [category for category in CATEGORIES if category.group is group]


def default_selected_ids() -> list[str]:
    """IDs of the categories checked when the cleaner is first opened."""
    return [category.id for category in CATEGORIES if category.default_enabled]


def scheduled_categories() -> list[Category]:
    """Categories that may be part of the automatic schedule (not risky)."""
    return [
        category
        for category in CATEGORIES
        if category.schedule_allowed and not category.risky
    ]


def scheduled_ids() -> list[str]:
    """IDs of the categories allowed in the automatic schedule."""
    return [category.id for category in scheduled_categories()]


def is_schedule_safe(category_id: str) -> bool:
    """True when the category may run automatically without interaction."""
    category = CATEGORY_MAP.get(category_id)
    return bool(category and category.schedule_allowed and not category.risky)
