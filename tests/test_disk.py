"""Tes pemindaian folder dan pencari duplikat."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from ubuclean.core.disk import (
    DuplicateScanResult,
    InvalidScanRoot,
    find_duplicates,
    scan_folder,
    validate_scan_root,
)


def make_file(path: Path, content: bytes | str = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode()
    path.write_bytes(content)
    return path


def test_scan_folder_sizes_and_children(tmp_path: Path) -> None:
    make_file(tmp_path / "a.txt", b"12345")
    make_file(tmp_path / "sub/b.txt", b"123")
    make_file(tmp_path / "sub/deep/c.txt", b"1")
    result = scan_folder(tmp_path, top_n=5)
    assert result.total_bytes == 9
    assert result.scanned_files == 3
    assert result.scanned_dirs == 2
    assert not result.cancelled
    assert {entry.path.name for entry in result.children} == {"a.txt", "sub"}
    assert result.children[0].path.name == "a.txt"
    sub_entry = next(entry for entry in result.children if entry.path.name == "sub")
    assert sub_entry.size == 4
    sizes = {entry.path.name: entry.size for entry in result.folders}
    assert sizes["sub"] == 4
    assert sizes["deep"] == 1
    assert result.files[0].path.name == "a.txt"


def test_scan_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = make_file(tmp_path.parent / "outside.txt", b"x" * 100)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(target)
    result = scan_folder(root)
    assert result.total_bytes == 0
    assert result.scanned_files == 0


def test_scan_skips_other_devices(tmp_path: Path) -> None:
    make_file(tmp_path / "a", b"1")
    result = scan_folder(tmp_path, same_device=True)
    assert result.total_bytes == 1


def test_scan_cancel(tmp_path: Path) -> None:
    for index in range(50):
        make_file(tmp_path / f"f{index}", b"x" * 10)
    cancel = threading.Event()

    def on_progress(_progress) -> None:
        cancel.set()

    result = scan_folder(tmp_path, cancel=cancel, progress=on_progress)
    assert result.cancelled is True


def test_scan_permission_error(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root dapat membaca semua folder")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    make_file(blocked / "secret", b"123")
    os.chmod(blocked, 0o000)
    try:
        result = scan_folder(tmp_path)
        assert result.errors >= 1
    finally:
        os.chmod(blocked, 0o755)


def test_validate_scan_root_rejects_bad_paths(tmp_path: Path) -> None:
    with pytest.raises(InvalidScanRoot):
        validate_scan_root("relative")
    with pytest.raises(InvalidScanRoot):
        validate_scan_root(tmp_path / "missing")
    with pytest.raises(InvalidScanRoot):
        validate_scan_root("/proc")
    file_path = make_file(tmp_path / "file", b"x")
    with pytest.raises(InvalidScanRoot):
        validate_scan_root(file_path)


def test_duplicates_three_stage(tmp_path: Path) -> None:
    make_file(tmp_path / "a/one.bin", b"same-content-123")
    make_file(tmp_path / "b/two.bin", b"same-content-123")
    make_file(tmp_path / "c/three.bin", b"same-content-123")
    make_file(tmp_path / "d/different.bin", b"different-cont")
    make_file(tmp_path / "e/same-size.bin", b"same-content-124")
    result = find_duplicates(tmp_path, min_size=1)
    assert len(result.groups) == 1
    group = result.groups[0]
    assert len(group.files) == 3
    assert group.size == 16
    assert group.wasted_bytes == 32
    assert result.total_wasted == 32
    assert result.scanned_files == 5


def test_duplicates_ignore_small_files(tmp_path: Path) -> None:
    make_file(tmp_path / "a", b"xx")
    make_file(tmp_path / "b", b"xx")
    result = find_duplicates(tmp_path, min_size=4096)
    assert result.groups == []
    make_file(tmp_path / "big1", b"x" * 5000)
    make_file(tmp_path / "big2", b"x" * 5000)
    result = find_duplicates(tmp_path, min_size=4096)
    assert len(result.groups) == 1


def test_duplicates_cancel(tmp_path: Path) -> None:
    for index in range(60):
        make_file(tmp_path / f"set{index}/a", b"x" * 5000)
        make_file(tmp_path / f"set{index}/b", b"x" * 5000)
    cancel = threading.Event()

    def on_progress(_progress) -> None:
        cancel.set()

    result = find_duplicates(tmp_path, cancel=cancel, min_size=1, progress=on_progress)
    assert result.cancelled is True


def test_duplicate_result_helpers(tmp_path: Path) -> None:
    result = DuplicateScanResult(root=tmp_path)
    assert result.total_wasted == 0
    assert result.duplicate_files == 0
    assert result.to_dict()["root"] == str(tmp_path)
