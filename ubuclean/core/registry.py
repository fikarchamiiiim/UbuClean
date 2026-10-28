"""Registry kategori pembersihan UbuClean."""

from __future__ import annotations

from ubuclean.core.models import Category, Group

CATEGORIES: tuple[Category, ...] = (
    Category(
        id="apt-cache",
        label="Cache apt",
        description="Paket .deb yang sudah terpasang dan tidak lagi dibutuhkan.",
        group=Group.SYSTEM,
        icon="package-x-generic-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="apt-autoremove",
        label="Paket tak terpakai",
        description="Dependensi yang tidak lagi diperlukan oleh paket terpasang.",
        group=Group.SYSTEM,
        icon="system-software-update-symbolic",
        privileged=True,
        schedule_allowed=True,
    ),
    Category(
        id="kernels",
        label="Kernel lama",
        description="Kernel lama akan dihapus, kecuali kernel yang sedang berjalan dan satu cadangan.",
        group=Group.SYSTEM,
        icon="drive-harddisk-symbolic",
        privileged=True,
        risky=True,
        default_enabled=False,
        schedule_allowed=False,
    ),
    Category(
        id="snap-revisions",
        label="Revisi snap lama",
        description="Revisi snap yang sudah dinonaktifkan setelah pembaruan.",
        group=Group.SYSTEM,
        icon="application-x-executable-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="flatpak-unused",
        label="Runtime Flatpak tak terpakai",
        description="Runtime dan extension Flatpak yang tidak dipakai aplikasi terpasang.",
        group=Group.SYSTEM,
        icon="application-x-executable-symbolic",
        privileged=True,
        schedule_allowed=True,
    ),
    Category(
        id="journal",
        label="Log journald lama",
        description="Log sistem yang lebih lama dari batas waktu yang dipilih.",
        group=Group.SYSTEM,
        icon="text-x-generic-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="crash",
        label="Laporan crash",
        description="Laporan crash di /var/crash yang sudah tidak diperlukan.",
        group=Group.SYSTEM,
        icon="dialog-warning-symbolic",
        privileged=True,
        schedule_allowed=True,
    ),
    Category(
        id="tmp",
        label="File lama di /tmp",
        description="File sementara yang tidak dipakai dan lebih tua dari batas hari.",
        group=Group.SYSTEM,
        icon="folder-temp-symbolic",
        privileged=True,
        schedule_allowed=True,
        schedule_default=True,
    ),
    Category(
        id="user-cache",
        label="Cache aplikasi",
        description="Cache di ~/.cache, termasuk thumbnail. Disimpan di karantina sebelum dihapus.",
        group=Group.USER,
        icon="folder-symbolic",
        schedule_allowed=True,
        schedule_default=True,
        quarantine=True,
    ),
    Category(
        id="browser-cache",
        label="Cache browser",
        description="Cache Firefox, Chrome, dan Chromium. Profil dan riwayat tidak disentuh.",
        group=Group.USER,
        icon="web-browser-symbolic",
        risky=True,
        default_enabled=False,
        schedule_allowed=False,
        quarantine=True,
    ),
    Category(
        id="trash",
        label="Tong sampah",
        description="Isi Tong Sampah pengguna. Item dipindahkan ke karantina UbuClean.",
        group=Group.USER,
        icon="user-trash-symbolic",
        schedule_allowed=True,
        schedule_default=True,
        quarantine=True,
    ),
    Category(
        id="dev-docker",
        label="Docker",
        description="Image dangling, container berhenti, volume tak terpakai, dan build cache.",
        group=Group.DEVELOPER,
        icon="utilities-terminal-symbolic",
        privileged=True,
        default_enabled=False,
        schedule_allowed=True,
    ),
    Category(
        id="dev-caches",
        label="Cache developer",
        description="Cache pip, npm, yarn, pnpm, cargo, gradle, dan Go build.",
        group=Group.DEVELOPER,
        icon="folder-download-symbolic",
        default_enabled=False,
        schedule_allowed=True,
        quarantine=True,
    ),
    Category(
        id="dev-projects",
        label="Folder proyek lama",
        description="node_modules, __pycache__, target/, dan .venv pada proyek yang lama tidak disentuh.",
        group=Group.DEVELOPER,
        icon="folder-symbolic",
        default_enabled=False,
        schedule_allowed=True,
        quarantine=True,
    ),
)

CATEGORY_MAP: dict[str, Category] = {category.id: category for category in CATEGORIES}


def get_category(category_id: str) -> Category:
    """Ambil kategori berdasarkan id, ``KeyError`` bila tidak dikenal."""
    return CATEGORY_MAP[category_id]


def categories_for_group(group: Group) -> list[Category]:
    """Semua kategori dalam satu grup."""
    return [category for category in CATEGORIES if category.group is group]


def default_selected_ids() -> list[str]:
    """ID kategori yang tercentang saat pertama kali membuka pembersih."""
    return [category.id for category in CATEGORIES if category.default_enabled]


def scheduled_categories() -> list[Category]:
    """Kategori yang boleh ikut jadwal otomatis (tidak berisiko)."""
    return [
        category
        for category in CATEGORIES
        if category.schedule_allowed and not category.risky
    ]


def scheduled_ids() -> list[str]:
    """ID kategori yang boleh ikut jadwal otomatis."""
    return [category.id for category in scheduled_categories()]


def is_schedule_safe(category_id: str) -> bool:
    """True bila kategori boleh dijalankan otomatis tanpa interaksi."""
    category = CATEGORY_MAP.get(category_id)
    return bool(category and category.schedule_allowed and not category.risky)
