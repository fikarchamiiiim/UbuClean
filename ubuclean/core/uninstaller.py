"""Pengelola aplikasi terpasang (apt, snap, flatpak) dan sisa konfigurasi."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ubuclean.core.context import Context
from ubuclean.core.journal import HistoryEntry
from ubuclean.core.paths import path_size
from ubuclean.core.quarantine import QuarantineEntry
from ubuclean.core.runner import PrivilegedError, Runner
from ubuclean.core.util import format_date, parse_human_size
from ubuclean.helper import validate

DPKG_FORMAT = (
    "${Package}\t${Version}\t${Installed-Size}\t${Status}\t${binary:Summary}\n"
)
SNAP_PROTECTED_PREFIXES = ("core", "snapd", "gnome-", "bare")
LEFTOVER_ROOTS = (".config", ".cache", ".local/share")


@dataclass(frozen=True, slots=True)
class InstalledApp:
    """Satu aplikasi terpasang dari apt, snap, atau flatpak."""

    id: str
    name: str
    source: str
    version: str = ""
    size: int = 0
    installed_at: float | None = None
    description: str = ""
    icon_name: str = "application-x-executable-symbolic"
    protected_reason: str | None = None

    @property
    def protected(self) -> bool:
        """True bila paket tidak boleh dihapus."""
        return self.protected_reason is not None

    def as_dict(self) -> dict[str, object]:
        """Representasi JSON-able."""
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "version": self.version,
            "size": self.size,
            "installed_at": self.installed_at,
            "description": self.description,
            "protected": self.protected,
            "protected_reason": self.protected_reason,
        }


@dataclass(frozen=True, slots=True)
class Leftover:
    """Sisa konfigurasi/cache milik aplikasi."""

    path: Path
    size: int
    origin: str


def apt_protected_reason(package: str) -> str | None:
    """Alasan paket apt tidak boleh dihapus."""
    if package in validate.PROTECTED_PACKAGES:
        return "Paket inti sistem"
    if package.startswith(validate.PROTECTED_PREFIXES):
        return "Paket kernel/boot inti"
    return None


def parse_dpkg_listing(output: str, info_dir: Path = Path("/var/lib/dpkg/info")) -> list[InstalledApp]:
    """Parse output dpkg-query menjadi daftar aplikasi terpasang."""
    apps: list[InstalledApp] = []
    for line in output.splitlines():
        columns = line.split("\t")
        if len(columns) < 5:
            continue
        package, version, size_text, status, summary = columns[:5]
        if " installed" not in f" {status}" or "not-installed" in status:
            continue
        try:
            size = int(size_text) * 1024
        except ValueError:
            size = 0
        installed_at: float | None = None
        try:
            installed_at = (info_dir / f"{package}.list").stat().st_mtime
        except OSError:
            installed_at = None
        apps.append(
            InstalledApp(
                id=package,
                name=package,
                source="apt",
                version=version,
                size=size,
                installed_at=installed_at,
                description=summary.strip(),
                icon_name="package-x-generic-symbolic",
                protected_reason=apt_protected_reason(package),
            )
        )
    return apps


def parse_snap_listing(output: str, snap_dir: Path = Path("/var/lib/snapd/snaps")) -> list[InstalledApp]:
    """Parse output ``snap list``."""
    apps: list[InstalledApp] = []
    for line in output.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 6:
            continue
        name, version, revision, tracking, publisher = columns[:5]
        size = 0
        installed_at: float | None = None
        for candidate in snap_dir.glob(f"{name}_{revision}.snap"):
            try:
                stat_result = candidate.stat()
                size += stat_result.st_size
                installed_at = stat_result.st_mtime
            except OSError:
                continue
        protected = (
            "Snap inti sistem"
            if name.startswith(SNAP_PROTECTED_PREFIXES) or name == "bare"
            else None
        )
        apps.append(
            InstalledApp(
                id=name,
                name=name,
                source="snap",
                version=version,
                size=size,
                installed_at=installed_at,
                description=f"{tracking} · {publisher}",
                icon_name="application-x-executable-symbolic",
                protected_reason=protected,
            )
        )
    return apps


def parse_flatpak_listing(
    output: str,
    home: Path,
    system_dir: Path = Path("/var/lib/flatpak/app"),
) -> list[InstalledApp]:
    """Parse ``flatpak list --columns=application,name,version,size,origin``."""
    apps: list[InstalledApp] = []
    for line in output.splitlines():
        columns = line.split("\t")
        if len(columns) < 5:
            continue
        app_id, name, version, size_text, origin = columns[:5]
        size = parse_human_size(size_text)
        installed_at: float | None = None
        for root in (home / ".local/share/flatpak/app", system_dir):
            try:
                candidate = root / app_id
                if candidate.exists():
                    installed_at = candidate.stat().st_mtime
                    break
            except OSError:
                continue
        protected = "Runtime/platform inti" if ".Platform" in app_id or ".BaseApp" in app_id else None
        apps.append(
            InstalledApp(
                id=app_id,
                name=name or app_id,
                source="flatpak",
                version=version,
                size=size,
                installed_at=installed_at,
                description=origin,
                icon_name=app_id,
                protected_reason=protected,
            )
        )
    return apps


class UninstallerService:
    """Daftar paket, penghapusan lewat helper, dan pencarian sisa data."""

    def __init__(self, ctx: Context, runner: Runner | None = None) -> None:
        self.ctx = ctx
        self.runner = runner or ctx.runner

    # -- listing -------------------------------------------------------
    def list_apt(self) -> list[InstalledApp]:
        """Semua paket apt yang terpasang."""
        result = self.runner.run(["dpkg-query", "-W", f"-f={DPKG_FORMAT}"], timeout=120)
        if result.returncode != 0:
            return []
        return parse_dpkg_listing(result.stdout or "")

    def list_snap(self) -> list[InstalledApp]:
        """Semua snap yang terpasang."""
        if self.runner.which("snap") is None:
            return []
        result = self.runner.run(["snap", "list"], timeout=60)
        if result.returncode != 0:
            return []
        return parse_snap_listing(result.stdout or "")

    def list_flatpak(self) -> list[InstalledApp]:
        """Semua aplikasi Flatpak yang terpasang."""
        if self.runner.which("flatpak") is None:
            return []
        result = self.runner.run(
            [
                "flatpak",
                "list",
                "--app",
                "--columns=application,name,version,size,origin",
            ],
            timeout=120,
        )
        if result.returncode != 0:
            return []
        return parse_flatpak_listing(result.stdout or "", self.ctx.paths.home)

    def list_packages(self) -> list[InstalledApp]:
        """Gabungan apt, snap, dan flatpak."""
        apps = self.list_apt() + self.list_snap() + self.list_flatpak()
        return sorted(apps, key=lambda app: (app.name.lower(), app.source))

    # -- removal -------------------------------------------------------
    def dependent_count(self, package: str) -> int:
        """Perkiraan jumlah paket yang bergantung pada ``package``."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
            return 0
        result = self.runner.run(
            ["apt-cache", "rdepends", "--installed", package],
            timeout=60,
            env={"LC_ALL": "C", "LANG": "C"},
        )
        if result.returncode != 0:
            return 0
        lines = [
            line.strip()
            for line in (result.stdout or "").splitlines()
            if line.strip() and "Reverse Depends:" not in line and line.strip() != package
        ]
        return len(set(lines))

    def remove(self, app: InstalledApp) -> tuple[bool, str]:
        """Hapus aplikasi lewat aksi helper yang sesuai."""
        if app.protected:
            return False, app.protected_reason or "Paket inti sistem tidak boleh dihapus."
        if app.source == "apt":
            args = ["apt-remove", validate.removable_package(app.id)]
        elif app.source == "snap":
            args = ["snap-remove", validate.snap_name(app.id)]
        elif app.source == "flatpak":
            args = ["flatpak-uninstall", validate.flatpak_ref(app.id)]
        else:
            return False, "Sumber paket tidak dikenal."
        try:
            result = self.ctx.privileged.run(args, timeout=900)
        except PrivilegedError as exc:
            self._log_removal(app, False, str(exc))
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            self._log_removal(app, False, f"Penghapusan gagal: {exc}")
            return False, f"Penghapusan gagal: {exc}"
        self._log_removal(app, result.returncode == 0, f"{app.name} dihapus.")
        return result.returncode == 0, f"{app.name} dihapus."

    def _log_removal(self, app: InstalledApp, ok: bool, detail: str) -> None:
        try:
            self.ctx.history.record(
                HistoryEntry(
                    action="uninstall",
                    category=f"uninstall-{app.source}",
                    label=f"Hapus {app.name}",
                    items=1,
                    bytes=app.size,
                    ok=ok,
                    detail=detail,
                )
            )
        except OSError:
            pass

    # -- leftovers -----------------------------------------------------
    def _candidate_names(self, app: InstalledApp) -> set[str]:
        names = {app.id.lower(), app.name.lower()}
        if "." in app.id:
            names.add(app.id.rsplit(".", 1)[-1].lower())
        names.add(re.sub(r"[^a-z0-9.-]+", "-", app.name.lower()).strip("-"))
        names.discard("")
        return names

    def find_leftovers(self, app: InstalledApp) -> list[Leftover]:
        """Cari folder sisa di ~/.config, ~/.cache, dan ~/.local/share."""
        candidates = self._candidate_names(app)
        leftovers: list[Leftover] = []
        for relative in LEFTOVER_ROOTS:
            root = self.ctx.paths.home / relative
            if not root.is_dir():
                continue
            try:
                children = list(root.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_symlink():
                    continue
                if child.name.lower() not in candidates:
                    continue
                leftovers.append(Leftover(child, path_size(child), relative))
        leftovers.sort(key=lambda item: item.size, reverse=True)
        return leftovers

    def quarantine_leftovers(self, app: InstalledApp, paths: list[Path]) -> tuple[QuarantineEntry | None, str]:
        """Pindahkan sisa konfigurasi ke karantina (bisa dipulihkan)."""
        safe: list[Path] = []
        home = self.ctx.paths.home.resolve()
        for path in paths:
            try:
                resolved = path.resolve(strict=False)
            except OSError:
                continue
            if home not in resolved.parents:
                continue
            if any(
                resolved == (home / name).resolve(strict=False)
                or (home / name).resolve(strict=False) in resolved.parents
                for name in ("Documents", "Pictures", "Videos", "Music", "Desktop", "Downloads")
            ):
                continue
            safe.append(resolved)
        entry = self.ctx.quarantine.quarantine("uninstaller", safe)
        if entry is None:
            return None, "Tidak ada sisa yang bisa dikarantina."
        try:
            self.ctx.history.record(
                HistoryEntry(
                    action="uninstall-leftovers",
                    category="uninstaller",
                    label=f"Sisa {app.name}",
                    items=len(entry.items),
                    bytes=entry.total_bytes,
                    ok=True,
                    detail="Sisa konfigurasi/cache dipindahkan ke karantina.",
                    quarantine_id=entry.action_id,
                )
            )
        except OSError:
            pass
        return entry, f"{len(entry.items)} folder dipindahkan ke karantina."


def installed_at_text(timestamp: float | None) -> str:
    """Format tanggal pemasangan untuk UI."""
    if not timestamp:
        return "Tanggal tidak tersedia"
    return format_date(timestamp)
