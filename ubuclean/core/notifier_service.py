"""systemd user service for the notification watcher."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ubuclean import constants
from ubuclean.core.runner import Runner, SubprocessRunner
from ubuclean.core.scheduler import default_exec_start

SERVICE_TEMPLATE = """[Unit]
Description=UbuClean notification watcher

[Service]
Type=simple
ExecStart={exec_start} --watch
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


@dataclass(frozen=True, slots=True)
class WatcherStatus:
    installed: bool
    active: bool
    message: str


class NotifierService:
    """Installs and controls ``ubuclean-notifier.service``."""

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
    def path(self) -> Path:
        """Path of the service unit."""
        return self.unit_dir / constants.NOTIFIER_SERVICE_NAME

    def render(self) -> str:
        """Contents of the service unit."""
        return SERVICE_TEMPLATE.format(exec_start=self.exec_start)

    def install(self) -> tuple[bool, str]:
        """Write the unit, then enable --now."""
        try:
            self.unit_dir.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.unit_dir,
                prefix=".ubuclean-notifier-",
                delete=False,
            )
            try:
                with handle:
                    handle.write(self.render())
                os.chmod(handle.name, 0o644)
                os.replace(handle.name, self.path)
            finally:
                if os.path.exists(handle.name):
                    os.unlink(handle.name)
        except OSError as exc:
            return False, f"Failed to write the unit: {exc}"
        self.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
        result = self.runner.run(
            ["systemctl", "--user", "enable", "--now", constants.NOTIFIER_SERVICE_NAME],
            timeout=60,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or "Failed to enable the watcher."
        return True, "Notification watcher active."

    def uninstall(self) -> tuple[bool, str]:
        """Disable the unit, then remove the one owned by UbuClean."""
        if self.path.exists():
            result = self.runner.run(
                ["systemctl", "--user", "disable", "--now", constants.NOTIFIER_SERVICE_NAME],
                timeout=60,
            )
            if result.returncode != 0 and "not loaded" not in (result.stderr or "").lower():
                return False, result.stderr.strip() or "Failed to disable the watcher."
            try:
                if "UbuClean" in self.path.read_text(encoding="utf-8", errors="replace"):
                    self.path.unlink()
            except OSError as exc:
                return False, f"Failed to remove the unit: {exc}"
        self.runner.run(["systemctl", "--user", "daemon-reload"], timeout=30)
        return True, "Notification watcher disabled."

    def status(self) -> WatcherStatus:
        """Status of the watcher unit."""
        installed = self.path.exists()
        result = self.runner.run(
            ["systemctl", "--user", "is-active", constants.NOTIFIER_SERVICE_NAME], timeout=20
        )
        active = (result.stdout or "").strip() == "active"
        if active:
            message = "Watcher is running."
        elif installed:
            message = "Unit is installed but not active."
        else:
            message = "The watcher has not been installed yet."
        return WatcherStatus(installed, active, message)
