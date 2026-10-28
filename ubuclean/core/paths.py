"""Validasi path dan penghapusan aman.

Semua penghapusan di UbuClean harus melewati :class:`SafetyPolicy` dan
:func:`safe_remove`. Kebijakan ini sengaja konservatif: hanya direktori cache
pengguna, Trash, karantina UbuClean, ``/tmp``, dan ``/var/crash`` yang boleh
disentuh.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


class UnsafePathError(ValueError):
    """Dilempar ketika path berada di luar whitelist atau dilindungi."""


HOME_PERSONAL_DIRS = (
    "Documents",
    "Pictures",
    "Videos",
    "Music",
    "Desktop",
    "Downloads",
    "Public",
    "Templates",
)

HOME_PROTECTED_DIRS = (
    ".ssh",
    ".gnupg",
    ".config",
    ".mozilla",
    ".password-store",
)

DENIED_ABSOLUTE = (
    Path("/"),
    Path("/bin"),
    Path("/sbin"),
    Path("/lib"),
    Path("/lib64"),
    Path("/etc"),
    Path("/boot"),
    Path("/usr"),
    Path("/var"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/run"),
    Path("/root"),
    Path("/home"),
    Path("/opt"),
    Path("/srv"),
    Path("/snap"),
)


def _normalise(path: Path) -> Path:
    """Resolve path secara longgar tanpa mewajibkan keberadaan file."""
    try:
        return Path(path).resolve(strict=False)
    except OSError:
        return Path(os.path.abspath(str(path)))


@dataclass(frozen=True)
class AppPaths:
    """Lokasi direktori dan file milik UbuClean untuk satu pengguna."""

    home: Path
    config_dir: Path
    state_dir: Path
    data_dir: Path
    cache_dir: Path
    quarantine_dir: Path
    log_file: Path
    config_file: Path

    @classmethod
    def for_home(cls, home: Path) -> "AppPaths":
        """Bangun himpunan path dari direktori home pengguna."""
        home = _normalise(home)
        config_dir = home / ".config/ubuclean"
        state_dir = home / ".local/state/ubuclean"
        data_dir = home / ".local/share/ubuclean"
        return cls(
            home=home,
            config_dir=config_dir,
            state_dir=state_dir,
            data_dir=data_dir,
            cache_dir=home / ".cache",
            quarantine_dir=data_dir / "quarantine",
            log_file=state_dir / "log",
            config_file=config_dir / "config.json",
        )

    @classmethod
    def default(cls) -> "AppPaths":
        """Path untuk pengguna yang sedang menjalankan aplikasi."""
        return cls.for_home(Path.home())

    def ensure_dirs(self) -> None:
        """Buat direktori state/data/config dengan izin ketat."""
        for directory, mode in (
            (self.config_dir, 0o700),
            (self.state_dir, 0o700),
            (self.data_dir, 0o700),
            (self.quarantine_dir, 0o700),
        ):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.stat().st_mode & 0o077:
                os.chmod(directory, mode)


@dataclass(frozen=True)
class RemovalResult:
    """Hasil satu operasi penghapusan path."""

    path: Path
    removed: bool = False
    size: int = 0
    error: str | None = None
    simulated: bool = False


@dataclass(frozen=True)
class SafetyPolicy:
    """Whitelist path yang boleh dihapus."""

    home: Path
    extra_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "home", _normalise(self.home))
        object.__setattr__(self, "extra_roots", tuple(Path(p) for p in self.extra_roots))

    @classmethod
    def for_home(cls, home: Path, extra_roots: Iterable[Path] = ()) -> "SafetyPolicy":
        """Buat kebijakan untuk home tertentu (dipakai juga oleh tes)."""
        return cls(home=home, extra_roots=tuple(extra_roots))

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        """Direktori yang isinya boleh dihapus (bukan direktori itu sendiri)."""
        home = self.home
        roots = [
            home / ".cache",
            home / ".local/share/Trash",
            home / ".local/share/ubuclean/quarantine",
            Path("/tmp"),
            Path("/var/crash"),
        ]
        roots.extend(self.extra_roots)
        return tuple(_normalise(root) for root in roots)

    def validate(self, path: Path | str) -> Path:
        """Validasi ``path`` dan kembalikan path asli yang aman dihapus.

        Pemeriksaan memakai hasil resolve symlink, sedangkan penghapusan tetap
        dilakukan pada path asli agar symlink tidak diikuti.
        """
        raw = Path(path)
        if "\x00" in str(raw):
            raise UnsafePathError("Path mengandung karakter NUL.")
        if not raw.is_absolute():
            raise UnsafePathError(f"Path harus absolut: {raw}")
        resolved = _normalise(raw)
        if resolved == self.home:
            raise UnsafePathError("Direktori home tidak boleh dihapus.")
        for name in HOME_PERSONAL_DIRS:
            protected = _normalise(self.home / name)
            if resolved == protected or protected in resolved.parents:
                raise UnsafePathError(f"Data pribadi dilindungi: {raw}")
        for name in HOME_PROTECTED_DIRS:
            protected = _normalise(self.home / name)
            if resolved == protected or protected in resolved.parents:
                raise UnsafePathError(f"Data konfigurasi dilindungi: {raw}")
        for root in self.allowed_roots:
            if resolved == root:
                raise UnsafePathError(f"Direktori induk tidak boleh dihapus: {raw}")
        for root in self.allowed_roots:
            if root in resolved.parents:
                return raw
        for denied in DENIED_ABSOLUTE:
            if resolved == denied or denied in resolved.parents:
                raise UnsafePathError(f"Lokasi sistem dilindungi: {raw}")
        raise UnsafePathError(f"Path di luar whitelist UbuClean: {raw}")

    def is_allowed(self, path: Path | str) -> bool:
        """True bila path lolos validasi."""
        try:
            self.validate(path)
        except UnsafePathError:
            return False
        return True


def _lstat(path: Path) -> os.stat_result | None:
    """lstat yang tidak melempar error untuk path hilang."""
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def path_size(path: Path) -> int:
    """Hitung ukuran file/direktori tanpa mengikuti symlink."""
    stat_result = _lstat(path)
    if stat_result is None:
        return 0
    mode = stat_result.st_mode
    if stat.S_ISREG(mode):
        return stat_result.st_size
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        return 0
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        total += path_size(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def iter_files(root: Path, *, limit: int | None = None) -> Iterator[tuple[Path, int]]:
    """Hasilkan ``(path, size)`` untuk file di dalam ``root`` tanpa symlink."""
    if limit is not None and limit <= 0:
        return
    count = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        for item in iter_files(Path(entry.path), limit=None):
                            yield item
                            count += 1
                            if limit is not None and count >= limit:
                                return
                    elif entry.is_file(follow_symlinks=False):
                        yield Path(entry.path), entry.stat(follow_symlinks=False).st_size
                        count += 1
                        if limit is not None and count >= limit:
                            return
                except OSError:
                    continue
    except OSError:
        return


def list_children(root: Path) -> list[tuple[Path, int]]:
    """Daftar entri tingkat pertama beserta ukurannya, terbesar lebih dulu."""
    entries: list[tuple[Path, int]] = []
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                try:
                    entries.append((Path(entry.path), path_size(Path(entry.path))))
                except OSError:
                    continue
    except OSError:
        return []
    entries.sort(key=lambda item: item[1], reverse=True)
    return entries


def safe_remove(
    path: Path,
    policy: SafetyPolicy,
    *,
    dry_run: bool = False,
) -> RemovalResult:
    """Hapus satu path setelah divalidasi, tanpa menghentikan pemanggil.

    Error izin, file yang sedang dipakai, atau filesystem read-only
    dikembalikan sebagai :class:`RemovalResult` dengan ``error`` terisi.
    """
    try:
        checked = policy.validate(path)
    except UnsafePathError as exc:
        return RemovalResult(path=Path(path), error=str(exc))
    stat_result = _lstat(checked)
    if stat_result is None:
        return RemovalResult(path=checked)
    size = path_size(checked)
    if dry_run:
        return RemovalResult(path=checked, size=size, simulated=True)
    try:
        if stat.S_ISDIR(stat_result.st_mode) and not stat.S_ISLNK(stat_result.st_mode):
            shutil.rmtree(checked)
        else:
            checked.unlink()
    except FileNotFoundError:
        return RemovalResult(path=checked, size=size)
    except OSError as exc:
        return RemovalResult(path=checked, size=0, error=f"{checked}: {exc.strerror or exc}")
    return RemovalResult(path=checked, removed=True, size=size)


def safe_remove_many(
    paths: Iterable[Path],
    policy: SafetyPolicy,
    *,
    dry_run: bool = False,
) -> list[RemovalResult]:
    """Hapus banyak path; satu kegagalan tidak menghentikan sisanya."""
    return [safe_remove(path, policy, dry_run=dry_run) for path in paths]
