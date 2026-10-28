"""Validasi argumen ketat untuk helper root UbuClean."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,120}$")
KERNEL_PACKAGE_RE = re.compile(
    r"^linux-(?:image|image-unsigned|headers|modules|modules-extra)-[0-9][0-9A-Za-z.+~-]{1,80}$"
)
KERNEL_PREFIX_RE = re.compile(r"^linux-(?:image-unsigned|image|headers|modules-extra|modules)-")
SNAP_SPEC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}:[0-9]{1,10}$")
SNAP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
FLATPAK_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,200}$")
JOURNAL_SPAN_RE = re.compile(r"^[1-9][0-9]{0,3}(?:d|w|M|y)$")
CRASH_NAME_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9._-]{0,200}$")

DOCKER_TARGETS = ("image", "container", "volume", "builder")

PROTECTED_PACKAGES = frozenset(
    {
        "ubuntu-desktop",
        "ubuntu-desktop-minimal",
        "ubuntu-standard",
        "ubuntu-minimal",
        "ubuntu-server",
        "gnome-shell",
        "gnome-session",
        "gdm3",
        "systemd",
        "systemd-sysv",
        "apt",
        "dpkg",
        "snapd",
        "flatpak",
        "xorg",
        "wayland",
        "ubuclean",
    }
)
PROTECTED_PREFIXES = (
    "linux-image",
    "linux-generic",
    "linux-headers-generic",
    "linux-signed",
    "linux-system",
)


class ValidationError(ValueError):
    """Argumen helper tidak valid."""


def package_name(value: str) -> str:
    """Validasi nama paket apt."""
    value = value.strip()
    if not PACKAGE_RE.fullmatch(value):
        raise ValidationError(f"Nama paket tidak valid: {value!r}")
    return value


def kernel_package(value: str) -> str:
    """Validasi nama paket kernel."""
    value = value.strip()
    if not KERNEL_PACKAGE_RE.fullmatch(value):
        raise ValidationError(f"Paket kernel tidak valid: {value!r}")
    return value


def kernel_release(package: str) -> str:
    """Ambil bagian versi dari nama paket kernel."""
    return KERNEL_PREFIX_RE.sub("", package)


def journal_span(value: str) -> str:
    """Validasi rentang waktu journalctl (contoh: ``14d``)."""
    value = value.strip()
    if not JOURNAL_SPAN_RE.fullmatch(value):
        raise ValidationError(f"Rentang journal tidak valid: {value!r}")
    return value


def day_count(value: str, *, maximum: int = 3650) -> int:
    """Validasi jumlah hari."""
    try:
        number = int(value)
    except ValueError as exc:
        raise ValidationError(f"Jumlah hari tidak valid: {value!r}") from exc
    if not 1 <= number <= maximum:
        raise ValidationError(f"Jumlah hari harus 1..{maximum}.")
    return number


def cache_level(value: str) -> int:
    """Validasi level drop_caches (1, 2, atau 3)."""
    try:
        level = int(value)
    except ValueError as exc:
        raise ValidationError(f"Level cache tidak valid: {value!r}") from exc
    if level not in (1, 2, 3):
        raise ValidationError("Level cache harus 1, 2, atau 3.")
    return level


def snap_spec(value: str) -> tuple[str, str]:
    """Validasi ``nama:revisi`` snap dan kembalikan komponennya."""
    value = value.strip()
    if not SNAP_SPEC_RE.fullmatch(value):
        raise ValidationError(f"Spesifikasi snap tidak valid: {value!r}")
    name, revision = value.split(":", 1)
    return name, revision


def snap_name(value: str) -> str:
    """Validasi nama snap."""
    value = value.strip()
    if not SNAP_NAME_RE.fullmatch(value):
        raise ValidationError(f"Nama snap tidak valid: {value!r}")
    return value


def flatpak_ref(value: str) -> str:
    """Validasi app id/ref flatpak."""
    value = value.strip()
    if not FLATPAK_REF_RE.fullmatch(value):
        raise ValidationError(f"Referensi flatpak tidak valid: {value!r}")
    return value


def crash_name(value: str) -> str:
    """Validasi nama file laporan crash (tanpa slash)."""
    value = value.strip()
    if not CRASH_NAME_RE.fullmatch(value) or value in {".", ".."}:
        raise ValidationError(f"Nama file crash tidak valid: {value!r}")
    return value


def docker_target(value: str) -> str:
    """Validasi target prune docker."""
    value = value.strip()
    if value not in DOCKER_TARGETS:
        raise ValidationError(f"Target docker tidak valid: {value!r}")
    return value


def removable_package(value: str) -> str:
    """Validasi paket untuk uninstall dan tolak paket inti sistem."""
    value = package_name(value)
    if value in PROTECTED_PACKAGES or value.startswith(PROTECTED_PREFIXES):
        raise ValidationError(f"Paket inti sistem tidak boleh dihapus: {value}")
    return value


def crash_path(name: str, root: Path = Path("/var/crash")) -> Path:
    """Bangun path crash yang sudah divalidasi di dalam ``root``."""
    safe_name = crash_name(name)
    root_resolved = root.resolve(strict=False)
    candidate = (root / safe_name).resolve(strict=False)
    if candidate.parent != root_resolved:
        raise ValidationError(f"Path crash keluar dari {root}: {name!r}")
    return candidate
