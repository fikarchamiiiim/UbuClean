"""Tes keamanan path dan penghapusan aman."""

from __future__ import annotations

from pathlib import Path

import pytest

from ubuclean.core.paths import (
    SafetyPolicy,
    UnsafePathError,
    iter_files,
    list_children,
    path_size,
    safe_remove,
)


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_allows_cache_file(home: Path, policy: SafetyPolicy) -> None:
    target = make_file(home / ".cache/app/data.bin", "12345")
    assert policy.is_allowed(target)
    result = safe_remove(target, policy)
    assert result.removed is True
    assert result.size == 5
    assert not target.exists()


def test_removes_directory_recursively(home: Path, policy: SafetyPolicy) -> None:
    root = home / ".cache/big"
    make_file(root / "a.txt", "abc")
    make_file(root / "sub/b.txt", "defg")
    result = safe_remove(root, policy)
    assert result.removed is True
    assert result.size == 7
    assert not root.exists()


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/etc/passwd",
        "/etc",
        "/boot/vmlinuz",
        "/usr/bin/python3",
        "/var/log/syslog",
        "/proc/self",
        "/root",
        "/home",
    ],
)
def test_denies_system_paths(policy: SafetyPolicy, target: str) -> None:
    with pytest.raises(UnsafePathError):
        policy.validate(Path(target))
    assert policy.is_allowed(Path(target)) is False


def test_denies_home_itself(home: Path, policy: SafetyPolicy) -> None:
    assert not policy.is_allowed(home)


@pytest.mark.parametrize(
    "name",
    ["Documents", "Pictures", "Videos", "Music", "Desktop", "Downloads", ".ssh", ".gnupg", ".config"],
)
def test_denies_personal_directories(home: Path, policy: SafetyPolicy, name: str) -> None:
    target = home / name / "file.txt"
    assert not policy.is_allowed(target)


def test_denies_allowed_root_itself(home: Path, policy: SafetyPolicy) -> None:
    assert not policy.is_allowed(home / ".cache")
    assert not policy.is_allowed(home / ".local/share/Trash")


def test_denies_relative_path(policy: SafetyPolicy) -> None:
    with pytest.raises(UnsafePathError):
        policy.validate(Path("relative/path"))


def test_denies_symlink_escape(home: Path, policy: SafetyPolicy) -> None:
    outside = Path("/etc/hostname")
    link = home / ".cache/evil"
    link.symlink_to(outside)
    assert not policy.is_allowed(link)
    result = safe_remove(link, policy)
    assert result.error
    assert link.is_symlink()
    assert outside.exists()


def test_denies_symlink_to_etc(home: Path, policy: SafetyPolicy) -> None:
    link = home / ".cache/etc-link"
    link.symlink_to("/etc")
    with pytest.raises(UnsafePathError):
        policy.validate(link)


def test_dry_run_preserves_files(home: Path, policy: SafetyPolicy) -> None:
    target = make_file(home / ".cache/keep.txt", "keep")
    result = safe_remove(target, policy, dry_run=True)
    assert result.simulated is True
    assert result.removed is False
    assert result.size == 4
    assert target.exists()


def test_missing_path_is_idempotent(home: Path, policy: SafetyPolicy) -> None:
    result = safe_remove(home / ".cache/tidak-ada", policy)
    assert result.removed is False
    assert result.error is None


def test_allows_tmp_paths(policy: SafetyPolicy) -> None:
    assert policy.is_allowed(Path("/tmp/ubuclean-not-real"))
    assert not policy.is_allowed(Path("/tmp"))


def test_path_size_ignores_symlinks(home: Path, policy: SafetyPolicy) -> None:
    root = home / ".cache/sizes"
    make_file(root / "a", "123")
    outside = home / ".cache/outside"
    make_file(outside, "should-not-count")
    (root / "link").symlink_to(outside)
    assert path_size(root) == 3


def test_iter_files_and_list_children(home: Path) -> None:
    root = home / ".cache/tree"
    make_file(root / "a", "1")
    make_file(root / "d/b", "22")
    files = sorted(path for path, _ in iter_files(root))
    assert [path.name for path in files] == ["a", "b"]
    children = list_children(root)
    assert children[0][0].name == "d"
    assert children[0][1] == 2


def test_extra_roots_are_allowed(home: Path, tmp_path: Path) -> None:
    extra = home / ".local/share/app-cache"
    make_file(extra / "file", "x")
    policy = SafetyPolicy.for_home(home, extra_roots=[extra])
    assert policy.is_allowed(extra / "file")
    assert not policy.is_allowed(extra)
