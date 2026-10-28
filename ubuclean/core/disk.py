"""Analisis folder dan pencari file duplikat (tanpa menghapus otomatis)."""

from __future__ import annotations

import hashlib
import heapq
import os
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PSEUDO_FS = {"/proc", "/sys", "/dev", "/run"}
PARTIAL_CHUNK = 4096
FULL_CHUNK = 1024 * 1024
SKIP_DIR_NAMES = {".git"}


class InvalidScanRoot(ValueError):
    """Root pemindaian tidak aman atau tidak valid."""


@dataclass(frozen=True, slots=True)
class DiskEntry:
    """Satu file atau folder dengan ukuran."""

    path: Path
    size: int
    is_dir: bool


@dataclass(frozen=True, slots=True)
class ScanProgress:
    """Progres pemindaian folder."""

    files: int = 0
    dirs: int = 0
    size: int = 0
    current: str = ""


@dataclass(slots=True)
class FolderScanResult:
    """Hasil pemindaian satu folder."""

    root: Path
    total_bytes: int = 0
    files: list[DiskEntry] = field(default_factory=list)
    folders: list[DiskEntry] = field(default_factory=list)
    children: list[DiskEntry] = field(default_factory=list)
    elapsed: float = 0.0
    scanned_files: int = 0
    scanned_dirs: int = 0
    errors: int = 0
    cancelled: bool = False

    def to_dict(self) -> dict[str, object]:
        """Representasi JSON-able."""
        return {
            "root": str(self.root),
            "total_bytes": self.total_bytes,
            "elapsed": round(self.elapsed, 2),
            "scanned_files": self.scanned_files,
            "scanned_dirs": self.scanned_dirs,
            "errors": self.errors,
            "cancelled": self.cancelled,
            "files": [
                {"path": str(item.path), "size": item.size} for item in self.files
            ],
            "folders": [
                {"path": str(item.path), "size": item.size} for item in self.folders
            ],
        }


ProgressFn = Callable[[ScanProgress], None]
DuplicateProgressFn = Callable[["DuplicateProgress"], None]


def validate_scan_root(root: Path | str) -> Path:
    """Validasi folder yang boleh dipindai (bukan pseudo filesystem)."""
    path = Path(root)
    if not path.is_absolute():
        raise InvalidScanRoot("Folder harus berupa path absolut.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise InvalidScanRoot(f"Folder tidak dapat diakses: {exc}") from exc
    if not resolved.is_dir():
        raise InvalidScanRoot("Path bukan folder.")
    if str(resolved) in PSEUDO_FS:
        raise InvalidScanRoot("Pseudo filesystem tidak boleh dipindai.")
    return resolved


def scan_folder(
    root: Path | str,
    *,
    cancel: threading.Event | None = None,
    progress: ProgressFn | None = None,
    top_n: int = 25,
    same_device: bool = True,
) -> FolderScanResult:
    """Hitung ukuran folder secara rekursif tanpa mengikuti symlink.

    Fungsi ini aman dijalankan di thread pekerja. ``progress`` dipanggil
    secara berkala, dan ``cancel.set()`` menghentikan pemindaian.
    """
    started = time.monotonic()
    root_path = validate_scan_root(root)
    result = FolderScanResult(root=root_path)
    cancel = cancel or threading.Event()
    try:
        root_device = root_path.stat().st_device  # type: ignore[attr-defined]
    except AttributeError:
        root_device = root_path.stat().st_dev
    folder_heap: list[tuple[int, str]] = []
    file_heap: list[tuple[int, str]] = []
    last_progress = 0.0
    bytes_seen = 0

    def report(current: Path, force: bool = False) -> None:
        nonlocal last_progress
        if progress is None:
            return
        now = time.monotonic()
        if not force and now - last_progress < 0.15:
            return
        last_progress = now
        try:
            progress(
                ScanProgress(
                    files=result.scanned_files,
                    dirs=result.scanned_dirs,
                    size=bytes_seen,
                    current=str(current),
                )
            )
        except Exception:
            pass

    def walk(directory: Path, depth: int) -> int:
        nonlocal bytes_seen
        if cancel.is_set():
            return 0
        total = 0
        try:
            entries = os.scandir(directory)
        except OSError:
            result.errors += 1
            return 0
        with entries:
            for entry in entries:
                if cancel.is_set():
                    return total
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError:
                    result.errors += 1
                    continue
                if stat.S_ISLNK(stat_result.st_mode):
                    continue
                path = Path(entry.path)
                if stat.S_ISDIR(stat_result.st_mode):
                    if same_device and stat_result.st_dev != root_device:
                        continue
                    result.scanned_dirs += 1
                    size = walk(path, depth + 1)
                    if cancel.is_set():
                        return total
                    total += size
                    if depth == 0:
                        result.children.append(DiskEntry(path, size, True))
                    heapq.heappush(folder_heap, (size, str(path)))
                    if len(folder_heap) > top_n:
                        heapq.heappop(folder_heap)
                elif stat.S_ISREG(stat_result.st_mode):
                    result.scanned_files += 1
                    bytes_seen += stat_result.st_size
                    total += stat_result.st_size
                    if depth == 0:
                        result.children.append(DiskEntry(path, stat_result.st_size, False))
                    heapq.heappush(file_heap, (stat_result.st_size, str(path)))
                    if len(file_heap) > top_n:
                        heapq.heappop(file_heap)
                report(path)
        report(directory, force=True)
        return total

    result.total_bytes = walk(root_path, 0)
    result.elapsed = time.monotonic() - started
    result.cancelled = cancel.is_set()
    result.folders = [
        DiskEntry(Path(path), size, True)
        for size, path in sorted(folder_heap, key=lambda item: item[0], reverse=True)
    ]
    result.files = [
        DiskEntry(Path(path), size, False)
        for size, path in sorted(file_heap, key=lambda item: item[0], reverse=True)
    ]
    result.children.sort(key=lambda item: item.size, reverse=True)
    report(root_path, force=True)
    return result


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Sekelompok file identik berdasarkan hash penuh."""

    digest: str
    size: int
    files: tuple[Path, ...]

    @property
    def wasted_bytes(self) -> int:
        """Ruang yang bisa dihemat bila menyisakan satu file."""
        return self.size * max(0, len(self.files) - 1)


@dataclass(frozen=True, slots=True)
class DuplicateProgress:
    """Progres pencarian duplikat."""

    stage: str
    processed: int
    total: int
    current: str = ""


@dataclass(slots=True)
class DuplicateScanResult:
    """Hasil pencarian file duplikat."""

    root: Path
    groups: list[DuplicateGroup] = field(default_factory=list)
    scanned_files: int = 0
    candidate_files: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    elapsed: float = 0.0

    @property
    def total_wasted(self) -> int:
        """Total byte yang bisa dihemat."""
        return sum(group.wasted_bytes for group in self.groups)

    @property
    def duplicate_files(self) -> int:
        """Jumlah file yang tergabung dalam grup duplikat."""
        return sum(len(group.files) for group in self.groups)

    def to_dict(self) -> dict[str, object]:
        """Representasi JSON-able."""
        return {
            "root": str(self.root),
            "groups": [
                {
                    "digest": group.digest,
                    "size": group.size,
                    "wasted": group.wasted_bytes,
                    "files": [str(path) for path in group.files],
                }
                for group in self.groups
            ],
            "total_wasted": self.total_wasted,
            "scanned_files": self.scanned_files,
            "cancelled": self.cancelled,
            "elapsed": round(self.elapsed, 2),
        }


def _iter_files(
    root: Path,
    cancel: threading.Event,
    errors: list[str],
    progress: DuplicateProgressFn | None,
) -> list[tuple[int, Path]]:
    files: list[tuple[int, Path]] = []
    scanned = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        if cancel.is_set():
            break
        dirnames[:] = [
            name
            for name in dirnames
            if not Path(directory, name).is_symlink()
            and name not in SKIP_DIR_NAMES
            and not (Path(directory) == Path("/") and name in {"proc", "sys", "dev", "run"})
        ]
        for filename in filenames:
            if cancel.is_set():
                break
            path = Path(directory, filename)
            try:
                stat_result = os.lstat(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if not stat.S_ISREG(stat_result.st_mode):
                continue
            files.append((stat_result.st_size, path))
            scanned += 1
            if progress is not None and scanned % 500 == 0:
                try:
                    progress(DuplicateProgress("size", scanned, 0, str(path)))
                except Exception:
                    pass
    return files


def _hash_file(path: Path, *, partial: bool) -> str | None:
    digest = hashlib.blake2b(digest_size=16)
    try:
        with path.open("rb") as handle:
            if partial:
                head = handle.read(PARTIAL_CHUNK)
                digest.update(head)
                try:
                    handle.seek(-PARTIAL_CHUNK, os.SEEK_END)
                except OSError:
                    pass
                digest.update(handle.read(PARTIAL_CHUNK))
            else:
                while True:
                    chunk = handle.read(FULL_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def find_duplicates(
    root: Path | str,
    *,
    cancel: threading.Event | None = None,
    progress: DuplicateProgressFn | None = None,
    min_size: int = 4096,
    max_groups: int = 500,
) -> DuplicateScanResult:
    """Cari file identik: ukuran → hash parsial → hash penuh."""
    started = time.monotonic()
    root_path = validate_scan_root(root)
    cancel = cancel or threading.Event()
    result = DuplicateScanResult(root=root_path)
    files = _iter_files(root_path, cancel, result.errors, progress)
    result.scanned_files = len(files)
    result.cancelled = cancel.is_set()
    if result.cancelled:
        result.elapsed = time.monotonic() - started
        return result

    by_size: dict[int, list[Path]] = {}
    for size, path in files:
        if size < min_size:
            continue
        by_size.setdefault(size, []).append(path)
    size_groups = {size: paths for size, paths in by_size.items() if len(paths) > 1}
    result.candidate_files = sum(len(paths) for paths in size_groups.values())
    if not size_groups:
        result.elapsed = time.monotonic() - started
        return result

    processed = 0
    total = result.candidate_files
    by_partial: dict[tuple[int, str], list[Path]] = {}
    for size, paths in size_groups.items():
        for path in paths:
            if cancel.is_set():
                result.cancelled = True
                result.elapsed = time.monotonic() - started
                return result
            digest = _hash_file(path, partial=True)
            processed += 1
            if digest is None:
                result.errors.append(f"{path}: tidak bisa membaca file")
                continue
            by_partial.setdefault((size, digest), []).append(path)
            if progress is not None and processed % 50 == 0:
                try:
                    progress(DuplicateProgress("partial", processed, total, str(path)))
                except Exception:
                    pass

    partial_groups = {key: paths for key, paths in by_partial.items() if len(paths) > 1}
    processed = 0
    total = sum(len(paths) for paths in partial_groups.values())
    by_full: dict[tuple[int, str], list[Path]] = {}
    for (size, _partial), paths in partial_groups.items():
        for path in paths:
            if cancel.is_set():
                result.cancelled = True
                result.elapsed = time.monotonic() - started
                return result
            digest = _hash_file(path, partial=False)
            processed += 1
            if digest is None:
                result.errors.append(f"{path}: tidak bisa membaca file")
                continue
            by_full.setdefault((size, digest), []).append(path)
            if progress is not None and processed % 20 == 0:
                try:
                    progress(DuplicateProgress("full", processed, total, str(path)))
                except Exception:
                    pass

    groups = [
        DuplicateGroup(digest=digest, size=size, files=tuple(sorted(paths)))
        for (size, digest), paths in by_full.items()
        if len(paths) > 1
    ]
    groups.sort(key=lambda group: group.wasted_bytes, reverse=True)
    result.groups = groups[:max_groups]
    result.cancelled = cancel.is_set()
    result.elapsed = time.monotonic() - started
    return result
