"""Pengelola aplikasi autostart dan systemd user service."""

from __future__ import annotations

import configparser
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from ubuclean import constants
from ubuclean.core.runner import Runner, SubprocessRunner

DESKTOP_SECTION = "Desktop Entry"
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +-]{0,180}\.desktop$")


@dataclass(frozen=True, slots=True)
class StartupEntry:
    """Satu entri autostart atau user service."""

    key: str
    name: str
    description: str
    command: str
    source: str
    enabled: bool
    path: Path | None = None
    is_system: bool = False
    unit: str | None = None
    overridden: bool = False
    filename: str = ""

    @property
    def is_systemd(self) -> bool:
        """True bila entri berasal dari systemd user service."""
        return self.unit is not None


def _read_desktop(path: Path) -> dict[str, str] | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return None
    if DESKTOP_SECTION not in parser:
        return None
    return {key: value for key, value in parser[DESKTOP_SECTION].items()}


def _desktop_enabled(values: dict[str, str]) -> bool:
    hidden = str(values.get("Hidden", "false")).strip().lower() in {"1", "true", "yes"}
    autostart = str(values.get("X-GNOME-Autostart-enabled", "true")).strip().lower()
    return (not hidden) and autostart not in {"0", "false", "no"}


def _entry_from_values(
    filename: str,
    values: dict[str, str],
    *,
    source: str,
    path: Path,
    is_system: bool,
    overridden: bool = False,
) -> StartupEntry:
    name = values.get("Name") or Path(filename).stem
    description = values.get("Comment") or values.get("GenericName") or ""
    command = values.get("Exec") or ""
    return StartupEntry(
        key=f"desktop:{filename}",
        name=name,
        description=description,
        command=command,
        source=source,
        enabled=_desktop_enabled(values),
        path=path,
        is_system=is_system,
        overridden=overridden,
        filename=filename,
    )


def _write_desktop(path: Path, updates: dict[str, str]) -> None:
    """Tulis file .desktop secara atomik sambil mempertahankan key lain."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    if path.exists():
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
    if DESKTOP_SECTION not in parser:
        parser[DESKTOP_SECTION] = {}
    for key, value in updates.items():
        parser[DESKTOP_SECTION][key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".ubuclean-autostart-",
        delete=False,
    )
    try:
        with handle:
            parser.write(handle, space_around_delimiters=False)
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


class StartupService:
    """Membaca dan mengubah autostart tanpa menghapus file asli."""

    def __init__(
        self,
        home: Path,
        runner: Runner | None = None,
        *,
        system_dir: Path = Path(constants.AUTOSTART_SYSTEM_DIR),
    ) -> None:
        self.home = Path(home)
        self.user_dir = self.home / ".config/autostart"
        self.system_dir = Path(system_dir)
        self.runner = runner or SubprocessRunner()

    # -- autostart -----------------------------------------------------
    def list_desktop_entries(self) -> list[StartupEntry]:
        """Gabungkan entri sistem dan pengguna; override pengguna menang."""
        merged: dict[str, StartupEntry] = {}
        for path in self._glob(self.system_dir):
            values = _read_desktop(path)
            if values is None:
                continue
            merged[path.name] = _entry_from_values(
                path.name, values, source="sistem", path=path, is_system=True
            )
        for path in self._glob(self.user_dir):
            values = _read_desktop(path)
            if values is None:
                continue
            if path.name in merged:
                base = merged[path.name]
                merged[path.name] = replace(
                    base,
                    enabled=_desktop_enabled(values),
                    path=base.path,
                    overridden=True,
                )
            else:
                merged[path.name] = _entry_from_values(
                    path.name, values, source="pengguna", path=path, is_system=False
                )
        return sorted(merged.values(), key=lambda entry: entry.name.lower())

    @staticmethod
    def _glob(directory: Path) -> list[Path]:
        try:
            return sorted(directory.glob("*.desktop"))
        except OSError:
            return []

    def set_desktop_enabled(self, entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
        """Aktifkan/nonaktifkan entri tanpa menghapus file asli."""
        if entry.is_systemd or not entry.filename:
            return False, "Entri bukan autostart desktop."
        if not _SAFE_FILENAME.fullmatch(entry.filename):
            return False, "Nama file autostart tidak valid."
        if entry.is_system:
            override = self.user_dir / entry.filename
            values = {
                "Hidden": "false" if enabled else "true",
                "X-GNOME-Autostart-enabled": "true" if enabled else "false",
            }
            if not override.exists():
                values.update(
                    {
                        "Type": "Application",
                        "Name": entry.name,
                        "Exec": entry.command or entry.name,
                        "X-UbuClean-Override": "true",
                    }
                )
            try:
                _write_desktop(override, values)
            except OSError as exc:
                return False, f"Gagal menulis override: {exc}"
            return True, "Override pengguna diperbarui."
        if entry.path is None:
            return False, "File autostart tidak ditemukan."
        try:
            _write_desktop(
                entry.path,
                {
                    "Hidden": "false" if enabled else "true",
                    "X-GNOME-Autostart-enabled": "true" if enabled else "false",
                },
            )
        except OSError as exc:
            return False, f"Gagal menulis file autostart: {exc}"
        return True, "Entri autostart diperbarui."

    # -- systemd -------------------------------------------------------
    def list_systemd_services(self) -> list[StartupEntry]:
        """Daftar user service systemd yang enabled."""
        result = self.runner.run(
            [
                "systemctl",
                "--user",
                "list-unit-files",
                "--type=service",
                "--state=enabled",
                "--no-legend",
                "--no-pager",
            ],
            timeout=20,
        )
        if result.returncode != 0:
            return []
        entries: list[StartupEntry] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts or not parts[0].endswith(".service"):
                continue
            unit = parts[0]
            description = self._unit_description(unit)
            fragment = self._unit_fragment(unit)
            entries.append(
                StartupEntry(
                    key=f"systemd:{unit}",
                    name=unit.removesuffix(".service"),
                    description=description,
                    command=fragment or f"systemctl --user start {unit}",
                    source="systemd",
                    enabled=True,
                    unit=unit,
                )
            )
        return sorted(entries, key=lambda entry: entry.name.lower())

    def _unit_description(self, unit: str) -> str:
        result = self.runner.run(
            ["systemctl", "--user", "show", "-p", "Description", "--value", unit],
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _unit_fragment(self, unit: str) -> str:
        result = self.runner.run(
            ["systemctl", "--user", "show", "-p", "FragmentPath", "--value", unit],
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def set_systemd_enabled(self, entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
        """Enable/disable user service systemd."""
        if not entry.unit:
            return False, "Unit systemd tidak valid."
        action = "enable" if enabled else "disable"
        result = self.runner.run(
            ["systemctl", "--user", action, "--now", entry.unit], timeout=60
        )
        if result.returncode != 0:
            message = result.stderr.strip() or f"systemctl {action} gagal."
            return False, message
        return True, f"{entry.unit} {'diaktifkan' if enabled else 'dinonaktifkan'}."

    def list_entries(self) -> list[StartupEntry]:
        """Gabungan autostart dan user service."""
        return self.list_desktop_entries() + self.list_systemd_services()

    def set_enabled(self, entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
        """Aktifkan/nonaktifkan entri apa pun."""
        if entry.is_systemd:
            return self.set_systemd_enabled(entry, enabled)
        return self.set_desktop_enabled(entry, enabled)
