"""Implementation of the UbuClean root actions.

Each function may only perform one action from the whitelist, validate its own
arguments, and never accept free-form commands.
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
    """Result of a single helper action that can always be serialized to JSON."""

    ok: bool = True
    freed_bytes: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """JSON-able representation."""
        return {
            "ok": self.ok,
            "freed_bytes": self.freed_bytes,
            "removed": self.removed,
            "errors": list(self.errors),
            "detail": self.detail,
        }


@dataclass
class HelperContext:
    """Helper dependencies so they can easily be swapped in tests."""

    runner: Runner = field(default_factory=SubprocessRunner)
    root: Path = Path("/")

    def path(self, relative: str | Path) -> Path:
        """Absolute path inside the root (for tests)."""
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
    """Run ``apt-get clean`` and count the bytes freed."""
    archives = ctx.path(ARCHIVES)
    before = path_size(archives)
    before_count = _count_files(archives)
    code, stdout, stderr = _run(ctx, ["apt-get", "clean"], timeout=300, env=_APT_ENV)
    after = path_size(archives)
    after_count = _count_files(archives)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"apt-get clean failed ({code})"])
    return ActionResult(
        freed_bytes=max(0, before - after),
        removed=max(0, before_count - after_count),
        detail="apt cache cleaned.",
    )


def autoremove(ctx: HelperContext) -> ActionResult:
    """Run ``apt-get autoremove``."""
    code, stdout, stderr = _run(
        ctx, ["apt-get", "-y", "autoremove"], timeout=900, env=_APT_ENV
    )
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"autoremove failed ({code})"])
    removed = sum(1 for line in stdout.splitlines() if line.startswith("Removing "))
    freed = _apt_freed_bytes(stdout + stderr)
    return ActionResult(
        freed_bytes=freed,
        removed=removed,
        detail="Unused packages removed via autoremove.",
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
    """Delete old kernel packages with running-kernel protection."""
    validated = [validate.kernel_package(package) for package in packages]
    if not validated:
        return ActionResult(ok=False, errors=["No kernel packages provided."])
    running = os.uname().release
    for package in validated:
        if running in package:
            return ActionResult(
                ok=False,
                errors=[f"The running kernel must not be deleted: {package}"],
            )
    installed = _installed_kernel_images(ctx)
    targeted = {validate.kernel_release(package) for package in validated if "linux-image-" in package}
    remaining = installed - targeted
    if targeted and len(remaining) < 2:
        return ActionResult(
            ok=False,
            errors=[
                "Filtering aborted: at least the running kernel and one spare kernel must remain."
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
            errors=[stderr.strip() or f"kernel purge failed ({code})"],
        )
    return ActionResult(
        freed_bytes=freed,
        removed=len(validated),
        detail=f"Old kernels deleted; running kernel {running} kept.",
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
    """Delete snap revisions that are already disabled."""
    parsed: list[tuple[str, str]] = [validate.snap_spec(spec) for spec in specs]
    if not parsed:
        return ActionResult(ok=False, errors=["No snap revisions provided."])
    disabled = _snap_disabled_specs(ctx)
    if disabled is None:
        return ActionResult(ok=False, errors=["Cannot read the snap list."])
    result = ActionResult(detail="Old snap revisions deleted.")
    for name, revision in parsed:
        spec = f"{name}:{revision}"
        if spec not in disabled:
            result.errors.append(f"Revision is not disabled/not found: {spec}")
            result.ok = False
            continue
        size = _snap_file_size(ctx, name, revision)
        code, _, stderr = _run(ctx, ["snap", "remove", name, "--revision", revision], timeout=300)
        if code != 0:
            result.errors.append(f"{spec}: {stderr.strip() or f'failed ({code})'}")
            result.ok = False
            continue
        result.removed += 1
        result.freed_bytes += size
    return result


def flatpak_unused(ctx: HelperContext) -> ActionResult:
    """Delete Flatpak runtimes that are not used."""
    code, stdout, stderr = _run(
        ctx,
        ["flatpak", "uninstall", "--unused", "--noninteractive", "-y"],
        timeout=900,
    )
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"flatpak failed ({code})"])
    removed = sum(1 for line in stdout.splitlines() if line.strip().startswith("Uninstalling"))
    return ActionResult(removed=removed, detail="Unused Flatpak runtimes cleaned.")


def journal_vacuum(ctx: HelperContext, span: str) -> ActionResult:
    """Vacuum journald entries older than the requested span."""
    validated = validate.journal_span(span)
    before = _disk_usage_bytes(ctx, ["journalctl", "--disk-usage"])
    code, stdout, stderr = _run(
        ctx,
        ["journalctl", f"--vacuum-time={validated}"],
        timeout=600,
    )
    after = _disk_usage_bytes(ctx, ["journalctl", "--disk-usage"])
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"vacuum failed ({code})"])
    return ActionResult(
        freed_bytes=max(0, before - after),
        detail=f"journald logs older than {validated} cleaned.",
    )


def clean_crash(ctx: HelperContext, names: list[str]) -> ActionResult:
    """Delete crash report files in /var/crash."""
    result = ActionResult(detail="Crash reports cleaned.")
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
            result.errors.append(f"Not a regular file: {path}")
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
    """Delete /tmp files that are old and not currently in use."""
    validated_days = validate.day_count(str(days))
    cutoff = time.time() - validated_days * 86400
    tmp_dir = ctx.path(TMP_DIR)
    result = ActionResult(detail=f"/tmp files older than {validated_days} days cleaned.")
    skipped = 0
    try:
        entries = list(os.scandir(tmp_dir))
    except OSError as exc:
        return ActionResult(ok=False, errors=[f"Cannot read {tmp_dir}: {exc}"])
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
        result.detail += f" {skipped} items skipped because they are in use."
    return result


def drop_caches(ctx: HelperContext, level: int) -> ActionResult:
    """Sync, then write the level to /proc/sys/vm/drop_caches."""
    validated = validate.cache_level(str(level))
    code, _, stderr = _run(ctx, ["sync"], timeout=120)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or "sync failed"])
    try:
        with ctx.path(DROP_CACHES).open("w", encoding="ascii") as handle:
            handle.write(str(validated))
    except OSError as exc:
        return ActionResult(ok=False, errors=[f"Cannot write drop_caches: {exc}"])
    return ActionResult(detail=f"Kernel caches dropped (level {validated}).")


def read_meminfo(path: Path | None = None) -> dict[str, int]:
    """Read /proc/meminfo into a dict of kB values."""
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
    """Refresh swap only when available RAM can hold the swap contents."""
    info = read_meminfo(ctx.path(MEMINFO) if meminfo is None else meminfo)
    if not info:
        return ActionResult(ok=False, errors=["Cannot read /proc/meminfo."])
    available = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    if swap_total == 0:
        return ActionResult(detail="Swap is unavailable; nothing needs refreshing.")
    if swap_used == 0:
        return ActionResult(detail="Swap is not in use; no refresh needed.")
    required = int(swap_used * require_ratio) + margin_kb
    if available < required:
        return ActionResult(
            ok=False,
            errors=[
                f"Not enough free RAM: {required // 1024} MB required, {available // 1024} MB available. "
                "Close some applications and try again."
            ],
        )
    code, _, stderr = _run(ctx, ["swapoff", "-a"], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or "swapoff failed"])
    code, _, stderr = _run(ctx, ["swapon", "-a"], timeout=900)
    if code != 0:
        _run(ctx, ["swapon", "-a"], timeout=900)
        return ActionResult(
            ok=False,
            errors=["Swap failed to be re-enabled. Check the system swap status immediately."],
        )
    return ActionResult(detail="Swap refreshed (swapoff -a && swapon -a).")


def apt_remove(ctx: HelperContext, package: str) -> ActionResult:
    """Remove a single non-core apt package."""
    validated = validate.removable_package(package)
    code, _, _ = _run(ctx, ["dpkg-query", "-W", "-f=${Status}", validated], timeout=30)
    if code != 0:
        return ActionResult(ok=False, errors=[f"Package is not installed: {validated}"])
    code, _, stderr = _run(ctx, ["apt-get", "remove", "-y", "--", validated], timeout=900, env=_APT_ENV)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"apt-get remove failed ({code})"])
    return ActionResult(removed=1, detail=f"Package {validated} removed.")


def snap_remove(ctx: HelperContext, name: str) -> ActionResult:
    """Remove a single snap."""
    validated = validate.snap_name(name)
    code, _, stderr = _run(ctx, ["snap", "remove", validated], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"snap remove failed ({code})"])
    return ActionResult(removed=1, detail=f"Snap {validated} removed.")


def flatpak_uninstall(ctx: HelperContext, ref: str) -> ActionResult:
    """Remove a single Flatpak application."""
    validated = validate.flatpak_ref(ref)
    code, _, stderr = _run(ctx, ["flatpak", "uninstall", "-y", validated], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"flatpak uninstall failed ({code})"])
    return ActionResult(removed=1, detail=f"Flatpak {validated} removed.")


def docker_prune(ctx: HelperContext, target: str) -> ActionResult:
    """Prune one type of Docker object (not ``system prune -a``)."""
    validated = validate.docker_target(target)
    commands = {
        "image": ["docker", "image", "prune", "-f"],
        "container": ["docker", "container", "prune", "-f"],
        "volume": ["docker", "volume", "prune", "-f"],
        "builder": ["docker", "builder", "prune", "-f"],
    }
    code, stdout, stderr = _run(ctx, commands[validated], timeout=900)
    if code != 0:
        return ActionResult(ok=False, errors=[stderr.strip() or f"docker prune failed ({code})"])
    freed = 0
    match = re.search(r"Total reclaimed space:\s*(.+)", stdout)
    if match:
        freed = parse_human_size(match.group(1))
    return ActionResult(
        freed_bytes=freed,
        removed=1,
        detail=stdout.strip()[-500:] or f"Docker {validated} pruned.",
    )
