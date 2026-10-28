"""Implementasi aksi root UbuClean.

Setiap fungsi hanya boleh melakukan satu tindakan dari whitelist, memvalidasi
argumennya sendiri, dan tidak pernah menerima perintah bebas.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from ubuclean.core.paths import path_size
from ubuclean.core.runner import Runner, SubprocessRunner
from ubuclean.core.util import parse_human_size
from ubuclean.helper import validate

ARCHIVES = Path("/var/cache/apt/archives")
SNAP_DIR = Path("/var/lib/snapd/snaps")
TMP_DIR = Path("/tmp")
CRASH_DIR = Path("/var/crash")
JOURNAL_DIR = Path("/var/log/journal")
MEMINFO = Path("/proc/meminfo")
DROP_CACHES = Path("/proc/sys/vm/drop_caches")

_SKIP_TMP_NAMES = {
    ".X11-unix",
    ".ICE-unix",
    ".XIM-unix",
    ".font-unix",
    ".Test-unix",
    "snap-private-tmp",
}
_SKIP_TMP_PREFIXES = ("systemd-private-", "snap-private-tmp", "pulse-")
_APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C", "LANG": "C"}
_C_ENV = {"LC_ALL": "C", "LANG": "C"}


@dataclass(slots=True)
class ActionResult:
    """Hasil satu aksi helper yang selalu bisa diserialisasi ke JSON."""

    ok: bool = True
    freed_bytes: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """Representasi JSON-able."""
        return {
            "ok": self.ok,
            "freed_bytes": self.freed_bytes,
            "removed": self.removed,
            "errors": list(self.errors),
            "detail": self.detail,
        }


@dataclass
class HelperContext:
    """Dependensi helper agar mudah diganti saat tes."""

    runner: Runner = field(default_factory=SubprocessRunner)
    root: Path = Path("/")

    def path(self, relative: str | Path) -> Path:
        """Path absolut di dalam root (untuk tes)."""
        return self.root / str(relative).lstrip("/")


def _run(
    ctx: HelperContext,
    argv: list[str],
    *,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    result = ctx.runner.run(argv, timeout=timeout, env=env)
    return result.returncode, result.stdout, result.stderr


def _count_files(root: Path) -> int:
    total = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        total += _count_files(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += 1
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _apt_freed_bytes(output: str) -> int:
    for line in output.splitlines():
        if "disk space will be freed" in line and "After this operation," in line:
            fragment = line.split("After this operation,", 1)[1].split("disk space", 1)[0].strip()
            return parse_human_size(fragment)
    return 0


def _disk_usage_bytes(ctx: HelperContext, argv: list[str]) -> int:
    code, stdout, _ = _run(ctx, argv, timeout=30, env=_C_ENV)
    if code != 0:
        return 0
    for line in stdout.splitlines():
        if "in the file system" in line:
            return parse_human_size(line)
    return 0


def clean_apt(ctx: HelperContext) -> ActionResult:
    """Jalankan ``apt-get clean`` dan hitung byte yang dibebaskan."""
    archives = ctx.path(ARCHIVES)
    before = path_size(archives)
    before_count = _count_files(archives)
    code, stdout, stderr = _run(ctx, ["apt-get", "clean"], timeout=300, env=_APT_ENV)
    after = path_size(archives)
    after_count = _count_files(archives)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"apt-get clean gagal ({code})"])
    return ActionResult(
        freed_bytes=max(0, before - after),
        removed=max(0, before_count - after_count),
        detail="Cache apt dibersihkan.",
    )


def autoremove(ctx: HelperContext) -> ActionResult:
    """Jalankan ``apt-get autoremove``."""
    code, stdout, stderr = _run(
        ctx, ["apt-get", "-y", "autoremove"], timeout=900, env=_APT_ENV
    )
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"autoremove gagal ({code})"])
    removed = sum(1 for line in stdout.splitlines() if line.startswith("Removing "))
    freed = _apt_freed_bytes(stdout + stderr)
    return ActionResult(
        freed_bytes=freed,
        removed=removed,
        detail="Paket tak terpakai dihapus via autoremove.",
    )


def _installed_kernel_images(ctx: HelperContext) -> set[str]:
    code, stdout, _ = _run(
        ctx,
        ["dpkg-query", "-W", "-f=${Package}\t${Status}\n", "linux-image-*"],
        timeout=30,
    )
    if code != 0:
        return set()
    releases: set[str] = set()
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        package, status = parts[0], parts[-1]
        if " installed" not in f" {status}":
            continue
        if not package.startswith("linux-image-"):
            continue
        releases.add(validate.kernel_release(package))
    return releases


def _package_sizes(ctx: HelperContext, packages: list[str]) -> int:
    if not packages:
        return 0
    code, stdout, _ = _run(
        ctx,
        ["dpkg-query", "-W", "-f=${Installed-Size}\t${Package}\n", *packages],
        timeout=30,
    )
    if code != 0 and not stdout:
        return 0
    total_kb = 0
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        try:
            total_kb += int(parts[0])
        except ValueError:
            continue
    return total_kb * 1024


def purge_kernels(ctx: HelperContext, packages: list[str]) -> ActionResult:
    """Hapus paket kernel lama dengan perlindungan kernel berjalan."""
    validated = [validate.kernel_package(package) for package in packages]
    if not validated:
        return ActionResult(ok=False, errors=["Tidak ada paket kernel yang diberikan."])
    running = os.uname().release
    for package in validated:
        if running in package:
            return ActionResult(
                ok=False,
                errors=[f"Kernel yang sedang berjalan tidak boleh dihapus: {package}"],
            )
    installed = _installed_kernel_images(ctx)
    targeted = {validate.kernel_release(package) for package in validated if "linux-image-" in package}
    remaining = installed - targeted
    if targeted and len(remaining) < 2:
        return ActionResult(
            ok=False,
            errors=[
                "Penyaringan dibatalkan: minimal kernel berjalan dan satu kernel cadangan harus tersisa."
            ],
        )
    freed = _package_sizes(ctx, validated)
    code, stdout, stderr = _run(
        ctx,
        ["apt-get", "purge", "-y", "--", *validated],
        timeout=900,
        env=_APT_ENV,
    )
    if code != 0:
        return ActionResult(
            freed_bytes=0,
            removed=0,
            ok=False,
            errors=[stderr.strip() or f"purge kernel gagal ({code})"],
        )
    return ActionResult(
        freed_bytes=freed,
        removed=len(validated),
        detail=f"Kernel lama dihapus; kernel berjalan {running} dipertahankan.",
    )


def _snap_disabled_specs(ctx: HelperContext) -> set[str] | None:
    code, stdout, _ = _run(ctx, ["snap", "list", "--all"], timeout=60)
    if code != 0:
        return None
    disabled: set[str] = set()
    for line in stdout.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 6:
            continue
        name, revision, notes = columns[0], columns[2], columns[-1]
        if "disabled" in notes:
            disabled.add(f"{name}:{revision}")
    return disabled


def _snap_file_size(ctx: HelperContext, name: str, revision: str) -> int:
    snap_dir = ctx.path(SNAP_DIR)
    candidates = list(snap_dir.glob(f"{name}_{revision}.snap"))
    return sum(path_size(path) for path in candidates)


def snap_old_revisions(ctx: HelperContext, specs: list[str]) -> ActionResult:
    """Hapus revisi snap yang sudah disabled."""
    parsed: list[tuple[str, str]] = [validate.snap_spec(spec) for spec in specs]
    if not parsed:
        return ActionResult(ok=False, errors=["Tidak ada revisi snap yang diberikan."])
    disabled = _snap_disabled_specs(ctx)
    if disabled is None:
        return ActionResult(ok=False, errors=["Tidak bisa membaca daftar snap."])
    result = ActionResult(detail="Revisi snap lama dihapus.")
    for name, revision in parsed:
        spec = f"{name}:{revision}"
        if spec not in disabled:
            result.errors.append(f"Revisi bukan disabled/tidak ditemukan: {spec}")
            result.ok = False
            continue
        size = _snap_file_size(ctx, name, revision)
        code, _, stderr = _run(ctx, ["snap", "remove", name, "--revision", revision], timeout=300)
        if code != 0:
            result.errors.append(f"{spec}: {stderr.strip() or f'gagal ({code})'}")
            result.ok = False
            continue
        result.removed += 1
        result.freed_bytes += size
    return result


def flatpak_unused(ctx: HelperContext) -> ActionResult:
    """Hapus runtime Flatpak yang tidak dipakai."""
    code, stdout, stderr = _run(
        ctx,
        ["flatpak", "uninstall", "--unused", "--noninteractive", "-y"],
        timeout=900,
    )
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"flatpak gagal ({code})"])
    removed = sum(1 for line in stdout.splitlines() if line.strip().startswith("Uninstalling"))
    return ActionResult(removed=removed, detail="Runtime Flatpak tak terpakai dibersihkan.")


def journal_vacuum(ctx: HelperContext, span: str) -> ActionResult:
    """Vakum journald lebih lama dari rentang yang diminta."""
    validated = validate.journal_span(span)
    before = _disk_usage_bytes(ctx, ["journalctl", "--disk-usage"])
    code, stdout, stderr = _run(
        ctx,
        ["journalctl", f"--vacuum-time={validated}"],
        timeout=600,
    )
    after = _disk_usage_bytes(ctx, ["journalctl", "--disk-usage"])
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"vacuum gagal ({code})"])
    return ActionResult(
        freed_bytes=max(0, before - after),
        detail=f"Log journald lebih lama dari {validated} dibersihkan.",
    )


def clean_crash(ctx: HelperContext, names: list[str]) -> ActionResult:
    """Hapus file laporan crash di /var/crash."""
    result = ActionResult(detail="Laporan crash dibersihkan.")
    for name in names:
        try:
            path = validate.crash_path(name, ctx.path(CRASH_DIR))
        except validate.ValidationError as exc:
            result.errors.append(str(exc))
            result.ok = False
            continue
        try:
            stat_result = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.errors.append(f"{path}: {exc}")
            result.ok = False
            continue
        if not stat.S_ISREG(stat_result.st_mode):
            result.errors.append(f"Bukan file biasa: {path}")
            result.ok = False
            continue
        size = stat_result.st_size
        try:
            path.unlink()
        except OSError as exc:
            result.errors.append(f"{path}: {exc}")
            result.ok = False
            continue
        result.removed += 1
        result.freed_bytes += size
    return result


def _in_use(ctx: HelperContext, path: Path) -> bool:
    fuser = ctx.runner.which("fuser")
    if not fuser:
        return False
    code, _, _ = _run(ctx, [fuser, "-s", "--", str(path)], timeout=3)
    return code == 0


def _delete_tmp_entry(path: Path) -> int:
    try:
        stat_result = os.lstat(path)
    except OSError:
        return 0
    size = path_size(path)
    try:
        if stat.S_ISDIR(stat_result.st_mode) and not stat.S_ISLNK(stat_result.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        return 0
    return size


def clean_tmp(ctx: HelperContext, days: int) -> ActionResult:
    """Hapus file /tmp yang lama dan tidak sedang dipakai."""
    validated_days = validate.day_count(str(days))
    cutoff = time.time() - validated_days * 86400
    tmp_dir = ctx.path(TMP_DIR)
    result = ActionResult(detail=f"File /tmp lebih tua dari {validated_days} hari dibersihkan.")
    skipped = 0
    try:
        entries = list(os.scandir(tmp_dir))
    except OSError as exc:
        return ActionResult(ok=False, errors=[f"Tidak bisa membaca {tmp_dir}: {exc}"])
    for entry in entries:
        name = entry.name
        if name in _SKIP_TMP_NAMES or name.startswith(_SKIP_TMP_PREFIXES):
            continue
        path = Path(entry.path)
        if os.path.ismount(path):
            continue
        try:
            stat_result = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat_result.st_mtime >= cutoff:
            continue
        if _in_use(ctx, path):
            skipped += 1
            continue
        size = _delete_tmp_entry(path)
        if size or not os.path.lexists(path):
            result.removed += 1
            result.freed_bytes += size
    if skipped:
        result.detail += f" {skipped} item dilewati karena sedang dipakai."
    return result


def drop_caches(ctx: HelperContext, level: int) -> ActionResult:
    """Sync lalu tulis level ke /proc/sys/vm/drop_caches."""
    validated = validate.cache_level(str(level))
    code, _, stderr = _run(ctx, ["sync"], timeout=120)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or "sync gagal"])
    try:
        with ctx.path(DROP_CACHES).open("w", encoding="ascii") as handle:
            handle.write(str(validated))
    except OSError as exc:
        return ActionResult(ok=False, errors=[f"Tidak bisa menulis drop_caches: {exc}"])
    return ActionResult(detail=f"Cache kernel dibuang (level {validated}).")


def read_meminfo(path: Path | None = None) -> dict[str, int]:
    """Baca /proc/meminfo menjadi dict nilai kB."""
    target = path or MEMINFO
    values: dict[str, int] = {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, raw = line.split(":", 1)
                parts = raw.strip().split()
                if not parts:
                    continue
                try:
                    values[key] = int(parts[0])
                except ValueError:
                    continue
    except OSError:
        return {}
    return values


def swap_refresh(
    ctx: HelperContext,
    meminfo: Path | None = None,
    *,
    require_ratio: float = 1.15,
    margin_kb: int = 64 * 1024,
) -> ActionResult:
    """Segarkan swap hanya bila RAM tersedia cukup menampung isi swap."""
    info = read_meminfo(ctx.path(MEMINFO) if meminfo is None else meminfo)
    if not info:
        return ActionResult(ok=False, errors=["Tidak bisa membaca /proc/meminfo."])
    available = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    if swap_total == 0:
        return ActionResult(detail="Swap tidak tersedia; tidak ada yang perlu disegarkan.")
    if swap_used == 0:
        return ActionResult(detail="Swap tidak sedang dipakai; tidak perlu disegarkan.")
    required = int(swap_used * require_ratio) + margin_kb
    if available < required:
        return ActionResult(
            ok=False,
            errors=[
                f"RAM bebas tidak cukup: butuh {required // 1024} MB, tersedia {available // 1024} MB. "
                "Tutup beberapa aplikasi lalu coba lagi."
            ],
        )
    code, _, stderr = _run(ctx, ["swapoff", "-a"], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or "swapoff gagal"])
    code, _, stderr = _run(ctx, ["swapon", "-a"], timeout=900)
    if code != 0:
        _run(ctx, ["swapon", "-a"], timeout=900)
        return ActionResult(
            ok=False,
            errors=["Swap gagal diaktifkan kembali. Segera periksa status swap sistem."],
        )
    return ActionResult(detail="Swap disegarkan (swapoff -a && swapon -a).")


def apt_remove(ctx: HelperContext, package: str) -> ActionResult:
    """Hapus satu paket apt non-inti."""
    validated = validate.removable_package(package)
    code, _, _ = _run(ctx, ["dpkg-query", "-W", "-f=${Status}", validated], timeout=30)
    if code != 0:
        return ActionResult(ok=False, errors=[f"Paket tidak terpasang: {validated}"])
    code, _, stderr = _run(ctx, ["apt-get", "remove", "-y", "--", validated], timeout=900, env=_APT_ENV)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"apt-get remove gagal ({code})"])
    return ActionResult(removed=1, detail=f"Paket {validated} dihapus.")


def snap_remove(ctx: HelperContext, name: str) -> ActionResult:
    """Hapus satu snap."""
    validated = validate.snap_name(name)
    code, _, stderr = _run(ctx, ["snap", "remove", validated], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"snap remove gagal ({code})"])
    return ActionResult(removed=1, detail=f"Snap {validated} dihapus.")


def flatpak_uninstall(ctx: HelperContext, ref: str) -> ActionResult:
    """Hapus satu aplikasi Flatpak."""
    validated = validate.flatpak_ref(ref)
    code, _, stderr = _run(ctx, ["flatpak", "uninstall", "-y", validated], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"flatpak uninstall gagal ({code})"])
    return ActionResult(removed=1, detail=f"Flatpak {validated} dihapus.")


def docker_prune(ctx: HelperContext, target: str) -> ActionResult:
    """Prune satu jenis objek Docker (bukan ``system prune -a``)."""
    validated = validate.docker_target(target)
    commands = {
        "image": ["docker", "image", "prune", "-f"],
        "container": ["docker", "container", "prune", "-f"],
        "volume": ["docker", "volume", "prune", "-f"],
        "builder": ["docker", "builder", "prune", "-f"],
    }
    code, stdout, stderr = _run(ctx, commands[validated], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"docker prune gagal ({code})"])
    freed = 0
    match = re.search(r"Total reclaimed space:\s*(.+)", stdout)
    if match:
        freed = parse_human_size(match.group(1))
    return ActionResult(
        freed_bytes=freed,
        removed=1,
        detail=stdout.strip()[-500:] or f"Docker {validated} dipangkas.",
    )
