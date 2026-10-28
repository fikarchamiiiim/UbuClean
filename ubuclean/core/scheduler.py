"""Pembuatan dan pengelolaan systemd user timer untuk jadwal UbuClean."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ubuclean import constants
from ubuclean.core.runner import Runner, SubprocessRunner

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FREQUENCIES = ("daily", "weekly", "monthly")
CALENDAR_PREFIX = {
    "daily": "*-*-*",
    "weekly": "Mon *-*-*",
    "monthly": "*-*-01",
}

SERVICE_TEMPLATE = """[Unit]
Description=Pembersihan terjadwal UbuClean

[Service]
Type=oneshot
ExecStart={exec_start} --run-scheduled
Nice=10
IOSchedulingClass=idle
"""
TIMER_TEMPLATE = """[Unit]
Description=Timer pembersihan terjadwal UbuClean

[Timer]
OnCalendar={calendar} {time}:00
Persistent=true
RandomizedDelaySec=10m
Unit={service}

[Install]
WantedBy=timers.target
"""


@dataclass(frozen=True, slots=True)
class ScheduleStatus:
    """Status unit timer pengguna."""

    installed: bool
    enabled: bool
    active: bool
    message: str


def default_exec_start() -> str:
    """Perintah ExecStart untuk instalasi saat ini."""
    found = shutil.which("ubuclean")
    if found:
        return found
    sibling = Path(__file__).resolve().parents[2] / "bin" / "ubuclean"
    if sibling.is_file():
        return str(sibling)
    return f"{sys.executable} -m ubuclean"


def calendar_expression(frequency: str, time_text: str) -> str:
    """Validasi input jadwal dan hasilkan OnCalendar systemd."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"Frekuensi tidak valid: {frequency}")
    if not TIME_RE.fullmatch(time_text):
        raise ValueError(f"Waktu harus berformat HH:MM: {time_text}")
    return f"{CALENDAR_PREFIX[frequency]} {time_text}:00"


class Scheduler:
    """Membuat, menghapus, dan memeriksa systemd user timer UbuClean."""

    def __init__(
        self,
        home: Path,
        runner: Runner | None = None,
        *,
        exec_start: str | None = None,
        unit_dir: Path | None = None,
    ) -> None:
        self.home = Path(home)
        self.runner = runner or SubprocessRunner()
        self.exec_start = exec_start or default_exec_start()
        self.unit_dir = Path(unit_dir) if unit_dir else self.home / ".config/systemd/user"

    @property
    def service_path(self) -> Path:
        """Path unit service."""
        return self.unit_dir / constants.SCHEDULE_SERVICE_NAME

    @property
    def timer_path(self) -> Path:
        """Path unit timer."""
        return self.unit_dir / constants.SCHEDULE_TIMER_NAME

    def render_service(self) -> str:
        """Isi unit service."""
        return SERVICE_TEMPLATE.format(exec_start=self.exec_start)

    def render_timer(self, frequency: str, time_text: str) -> str:
        """Isi unit timer untuk frekuensi dan jam tertentu."""
        return TIMER_TEMPLATE.format(
            calendar=CALENDAR_PREFIX[frequency],
            time=time_text,
            service=constants.SCHEDULE_SERVICE_NAME,
        )

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".ubuclean-unit-",
            delete=False,
        )
        try:
            with handle:
                handle.write(content)
            os.chmod(handle.name, 0o644)
            os.replace(handle.name, path)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    def install(self, frequency: str, time_text: str) -> tuple[bool, str]:
        """Tulis unit, reload, lalu aktifkan timer."""
        try:
            calendar = calendar_expression(frequency, time_text)
            del calendar
        except ValueError as exc:
            return False, str(exc)
        try:
            self._write(self.service_path, self.render_service())
            self._write(self.timer_path, self.render_timer(frequency, time_text))
        except OSError as exc:
            return False, f"Gagal menulis unit systemd: {exc}"
        reload_result = self.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
        if reload_result.returncode != 0:
            return False, reload_result.stderr.strip() or "systemctl daemon-reload gagal."
        result = self.runner.run(
            ["systemctl", "--user", "enable", "--now", constants.SCHEDULE_TIMER_NAME],
            timeout=60,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "Gagal mengaktifkan timer."
            return False, message
        return True, "Jadwal pembersihan diaktifkan."

    def uninstall(self) -> tuple[bool, str]:
        """Nonaktifkan timer lalu hapus unit yang dibuat UbuClean."""
        errors: list[str] = []
        if self.timer_path.exists():
            result = self.runner.run(
                ["systemctl", "--user", "disable", "--now", constants.SCHEDULE_TIMER_NAME],
                timeout=60,
            )
            if result.returncode != 0 and "not loaded" not in (result.stderr or "").lower():
                errors.append(result.stderr.strip() or "Gagal menonaktifkan timer.")
        for path in (self.timer_path, self.service_path):
            try:
                if path.exists() and "UbuClean" in path.read_text(encoding="utf-8", errors="replace"):
                    path.unlink()
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        self.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
        if errors:
            return False, " ".join(errors)
        return True, "Jadwal pembersihan dihapus."

    def status(self) -> ScheduleStatus:
        """Status unit timer dan pesan ringkas."""
        installed = self.timer_path.exists() and self.service_path.exists()
        if not installed:
            return ScheduleStatus(False, False, False, "Jadwal belum diatur.")
        enabled_result = self.runner.run(
            ["systemctl", "--user", "is-enabled", constants.SCHEDULE_TIMER_NAME], timeout=20
        )
        active_result = self.runner.run(
            ["systemctl", "--user", "is-active", constants.SCHEDULE_TIMER_NAME], timeout=20
        )
        enabled = (enabled_result.stdout or "").strip() == "enabled"
        active = (active_result.stdout or "").strip() == "active"
        if enabled and active:
            message = "Timer aktif."
        elif enabled:
            message = "Timer diaktifkan tetapi belum berjalan."
        else:
            message = "Timer tidak aktif."
        return ScheduleStatus(True, enabled, active, message)
