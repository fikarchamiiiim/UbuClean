"""Creation and management of the systemd user timers for UbuClean schedules."""

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
Description=UbuClean scheduled cleaning

[Service]
Type=oneshot
ExecStart={exec_start} --run-scheduled
Nice=10
IOSchedulingClass=idle
"""
TIMER_TEMPLATE = """[Unit]
Description=UbuClean scheduled cleaning timer

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
    """Status of the user timer unit."""

    installed: bool
    enabled: bool
    active: bool
    message: str


def default_exec_start() -> str:
    """ExecStart command for the current installation."""
    found = shutil.which("ubuclean")
    if found:
        return found
    sibling = Path(__file__).resolve().parents[2] / "bin" / "ubuclean"
    if sibling.is_file():
        return str(sibling)
    return f"{sys.executable} -m ubuclean"


def calendar_expression(frequency: str, time_text: str) -> str:
    """Validate the schedule input and produce the systemd OnCalendar value."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"Invalid frequency: {frequency}")
    if not TIME_RE.fullmatch(time_text):
        raise ValueError(f"The time must use the HH:MM format: {time_text}")
    return f"{CALENDAR_PREFIX[frequency]} {time_text}:00"


class Scheduler:
    """Creates, removes, and inspects UbuClean systemd user timers."""

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
        """Path of the service unit."""
        return self.unit_dir / constants.SCHEDULE_SERVICE_NAME

    @property
    def timer_path(self) -> Path:
        """Path of the timer unit."""
        return self.unit_dir / constants.SCHEDULE_TIMER_NAME

    def render_service(self) -> str:
        """Contents of the service unit."""
        return SERVICE_TEMPLATE.format(exec_start=self.exec_start)

    def render_timer(self, frequency: str, time_text: str) -> str:
        """Contents of the timer unit for a given frequency and time."""
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
        """Write the units, reload, then enable the timer."""
        try:
            calendar = calendar_expression(frequency, time_text)
            del calendar
        except ValueError as exc:
            return False, str(exc)
        try:
            self._write(self.service_path, self.render_service())
            self._write(self.timer_path, self.render_timer(frequency, time_text))
        except OSError as exc:
            return False, f"Failed to write the systemd unit: {exc}"
        reload_result = self.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
        if reload_result.returncode != 0:
            return False, reload_result.stderr.strip() or "systemctl daemon-reload failed."
        result = self.runner.run(
            ["systemctl", "--user", "enable", "--now", constants.SCHEDULE_TIMER_NAME],
            timeout=60,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "Failed to enable the timer."
            return False, message
        return True, "Cleaning schedule enabled."

    def uninstall(self) -> tuple[bool, str]:
        """Disable the timer, then remove the units created by UbuClean."""
        errors: list[str] = []
        if self.timer_path.exists():
            result = self.runner.run(
                ["systemctl", "--user", "disable", "--now", constants.SCHEDULE_TIMER_NAME],
                timeout=60,
            )
            if result.returncode != 0 and "not loaded" not in (result.stderr or "").lower():
                errors.append(result.stderr.strip() or "Failed to disable the timer.")
        for path in (self.timer_path, self.service_path):
            try:
                if path.exists() and "UbuClean" in path.read_text(encoding="utf-8", errors="replace"):
                    path.unlink()
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        self.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
        if errors:
            return False, " ".join(errors)
        return True, "Cleaning schedule removed."

    def status(self) -> ScheduleStatus:
        """Timer unit status and a short message."""
        installed = self.timer_path.exists() and self.service_path.exists()
        if not installed:
            return ScheduleStatus(False, False, False, "No schedule has been set up yet.")
        enabled_result = self.runner.run(
            ["systemctl", "--user", "is-enabled", constants.SCHEDULE_TIMER_NAME], timeout=20
        )
        active_result = self.runner.run(
            ["systemctl", "--user", "is-active", constants.SCHEDULE_TIMER_NAME], timeout=20
        )
        enabled = (enabled_result.stdout or "").strip() == "enabled"
        active = (active_result.stdout or "").strip() == "active"
        if enabled and active:
            message = "Timer is active."
        elif enabled:
            message = "Timer is enabled but not running yet."
        else:
            message = "Timer is not active."
        return ScheduleStatus(True, enabled, active, message)
