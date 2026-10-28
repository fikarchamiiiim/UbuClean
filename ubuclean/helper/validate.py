"""Strict argument validation for the UbuClean root helper."""

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
    """Invalid helper argument."""


def package_name(value: str) -> str:
    """Validate an apt package name."""
    value = value.strip()
    if not PACKAGE_RE.fullmatch(value):
        raise ValidationError(f"Invalid package name: {value!r}")
    return value


def kernel_package(value: str) -> str:
    """Validate a kernel package name."""
    value = value.strip()
    if not KERNEL_PACKAGE_RE.fullmatch(value):
        raise ValidationError(f"Invalid kernel package: {value!r}")
    return value


def kernel_release(package: str) -> str:
    """Extract the version part from a kernel package name."""
    return KERNEL_PREFIX_RE.sub("", package)


def journal_span(value: str) -> str:
    """Validate a journalctl time span (for example ``14d``)."""
    value = value.strip()
    if not JOURNAL_SPAN_RE.fullmatch(value):
        raise ValidationError(f"Invalid journal span: {value!r}")
    return value


def day_count(value: str, *, maximum: int = 3650) -> int:
    """Validate a day count."""
    try:
        number = int(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid day count: {value!r}") from exc
    if not 1 <= number <= maximum:
        raise ValidationError(f"The day count must be 1..{maximum}.")
    return number


def cache_level(value: str) -> int:
    """Validate the drop_caches level (1, 2, or 3)."""
    try:
        level = int(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid cache level: {value!r}") from exc
    if level not in (1, 2, 3):
        raise ValidationError("The cache level must be 1, 2, or 3.")
    return level


def snap_spec(value: str) -> tuple[str, str]:
    """Validate a snap ``name:revision`` and return its components."""
    value = value.strip()
    if not SNAP_SPEC_RE.fullmatch(value):
        raise ValidationError(f"Invalid snap spec: {value!r}")
    name, revision = value.split(":", 1)
    return name, revision


def snap_name(value: str) -> str:
    """Validate a snap name."""
    value = value.strip()
    if not SNAP_NAME_RE.fullmatch(value):
        raise ValidationError(f"Invalid snap name: {value!r}")
    return value


def flatpak_ref(value: str) -> str:
    """Validate a Flatpak app id/ref."""
    value = value.strip()
    if not FLATPAK_REF_RE.fullmatch(value):
        raise ValidationError(f"Invalid flatpak ref: {value!r}")
    return value


def crash_name(value: str) -> str:
    """Validate a crash report file name (no slashes)."""
    value = value.strip()
    if not CRASH_NAME_RE.fullmatch(value) or value in {".", ".."}:
        raise ValidationError(f"Invalid crash file name: {value!r}")
    return value


def docker_target(value: str) -> str:
    """Validate a docker prune target."""
    value = value.strip()
    if value not in DOCKER_TARGETS:
        raise ValidationError(f"Invalid docker target: {value!r}")
    return value


def removable_package(value: str) -> str:
    """Validate a package for uninstall and reject core system packages."""
    value = package_name(value)
    if value in PROTECTED_PACKAGES or value.startswith(PROTECTED_PREFIXES):
        raise ValidationError(f"Core system packages must not be removed: {value}")
    return value


def crash_path(name: str, root: Path = Path("/var/crash")) -> Path:
    """Build a validated crash path inside ``root``."""
    safe_name = crash_name(name)
    root_resolved = root.resolve(strict=False)
    candidate = (root / safe_name).resolve(strict=False)
    if candidate.parent != root_resolved:
        raise ValidationError(f"Crash path escapes {root}: {name!r}")
    return candidate
