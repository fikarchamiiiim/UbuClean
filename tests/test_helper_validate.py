"""Tes validasi argumen helper (whitelist ketat)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ubuclean.helper import validate


@pytest.mark.parametrize(
    "value",
    ["firefox", "linux-image-6.8.0-31-generic", "ubuntu-desktop", "lib2to3"],
)
def test_valid_package_names(value: str) -> None:
    assert validate.package_name(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "-rf", "pkg name", "pkg;rm", "PKG", "../etc", "a" * 200],
)
def test_invalid_package_names(value: str) -> None:
    with pytest.raises(validate.ValidationError):
        validate.package_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "linux-image-6.8.0-31-generic",
        "linux-image-unsigned-6.8.0-31-generic",
        "linux-headers-6.8.0-31",
        "linux-modules-extra-6.8.0-31-generic",
    ],
)
def test_valid_kernel_packages(value: str) -> None:
    assert validate.kernel_package(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "linux-image-generic",
        "linux-image",
        "linux-image-",
        "linux-image-6.8.0-31-generic --force-yes",
        "linux-image-6.8.0-31-generic;reboot",
    ],
)
def test_invalid_kernel_packages(value: str) -> None:
    with pytest.raises(validate.ValidationError):
        validate.kernel_package(value)


def test_kernel_release() -> None:
    assert validate.kernel_release("linux-image-6.8.0-31-generic") == "6.8.0-31-generic"
    assert validate.kernel_release("linux-image-unsigned-6.8.0-31-generic") == "6.8.0-31-generic"


@pytest.mark.parametrize("value", ["7d", "2w", "1M", "3y", "14d"])
def test_valid_journal_spans(value: str) -> None:
    assert validate.journal_span(value) == value


@pytest.mark.parametrize("value", ["0d", "7", "7days", "-1d", "7d;rm -rf /", "d"])
def test_invalid_journal_spans(value: str) -> None:
    with pytest.raises(validate.ValidationError):
        validate.journal_span(value)


@pytest.mark.parametrize("value", ["1", "7", "3650"])
def test_valid_days(value: str) -> None:
    assert validate.day_count(value) == int(value)


@pytest.mark.parametrize("value", ["0", "-1", "3651", "abc", "1.5"])
def test_invalid_days(value: str) -> None:
    with pytest.raises(validate.ValidationError):
        validate.day_count(value)


def test_cache_level() -> None:
    assert validate.cache_level("3") == 3
    with pytest.raises(validate.ValidationError):
        validate.cache_level("0")
    with pytest.raises(validate.ValidationError):
        validate.cache_level("4")


def test_snap_spec() -> None:
    assert validate.snap_spec("firefox:4472") == ("firefox", "4472")
    for value in ("firefox", "firefox:", ":123", "Firefox:1", "firefox:abc", "../x:1"):
        with pytest.raises(validate.ValidationError):
            validate.snap_spec(value)


def test_crash_path_stays_in_root(tmp_path: Path) -> None:
    root = tmp_path / "crash"
    root.mkdir()
    assert validate.crash_path("_usr_bin_x.1000.crash", root) == (root / "_usr_bin_x.1000.crash").resolve()
    for name in ("../etc/passwd", "a/b", "", "..", "x;y"):
        with pytest.raises(validate.ValidationError):
            validate.crash_path(name, root)


def test_removable_package_blocks_system() -> None:
    for package in ("ubuntu-desktop", "gnome-shell", "linux-image-6.8.0-31-generic", "systemd"):
        with pytest.raises(validate.ValidationError):
            validate.removable_package(package)
    assert validate.removable_package("gimp") == "gimp"


def test_docker_target() -> None:
    assert validate.docker_target("image") == "image"
    with pytest.raises(validate.ValidationError):
        validate.docker_target("system")


def test_flatpak_ref_and_snap_name() -> None:
    assert validate.flatpak_ref("org.gnome.Builder") == "org.gnome.Builder"
    assert validate.snap_name("firefox") == "firefox"
    for value in ("--all", "a/b", ""):
        with pytest.raises(validate.ValidationError):
            validate.flatpak_ref(value)
