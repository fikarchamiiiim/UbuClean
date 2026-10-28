"""Smart notifications based on disk and RAM thresholds."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ubuclean.core.context import Context
from ubuclean.core.util import format_percent

DISK_PATH = "/"


@dataclass(frozen=True, slots=True)
class NotificationDecision:
    """A single notification that is worth sending."""

    kind: str
    title: str
    body: str

    @property
    def actions(self) -> tuple[str, str]:
        """ID and label of the main action."""
        return ("clean", "Clean now") if self.kind == "disk" else ("monitor", "Open monitor")


class Notifier:
    """Watches disk/RAM and sends notifications with a cooldown."""

    def __init__(
        self,
        ctx: Context,
        *,
        clock: Callable[[], float] = time.time,
        disk_path: str | Path = DISK_PATH,
        state_file: Path | None = None,
    ) -> None:
        self.ctx = ctx
        self.clock = clock
        self.disk_path = str(disk_path)
        self.state_file = state_file or (ctx.paths.state_dir / "notifier.json")
        self._ram_since: float | None = None
        self._last_notify: dict[str, float] = {}
        self._load_state()

    # -- state ---------------------------------------------------------
    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            last = data.get("last_notify")
            if isinstance(last, dict):
                for key, value in last.items():
                    try:
                        self._last_notify[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue

    def _save_state(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.state_file.parent, 0o700)
            payload = json.dumps({"last_notify": self._last_notify}, ensure_ascii=False)
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.state_file.parent,
                prefix=".notifier-",
                delete=False,
            )
            try:
                with handle:
                    handle.write(payload)
                os.chmod(handle.name, 0o600)
                os.replace(handle.name, self.state_file)
            finally:
                if os.path.exists(handle.name):
                    os.unlink(handle.name)
        except OSError:
            pass

    # -- evaluation ----------------------------------------------------
    def evaluate(self, disk_percent: float, ram_percent: float) -> NotificationDecision | None:
        """Decide whether a notification needs to be sent right now."""
        settings = self.ctx.config.notifications
        now = self.clock()
        cooldown = int(settings.get("cooldown_hours", 6)) * 3600
        if not settings.get("enabled", True) or settings.get("muted", False):
            self._ram_since = None
            return None

        last_disk = self._last_notify.get("disk", 0.0)
        if (
            disk_percent >= float(settings.get("disk_threshold", 90))
            and now - last_disk >= cooldown
        ):
            self._last_notify["disk"] = now
            self._save_state()
            return NotificationDecision(
                kind="disk",
                title=f"Disk {format_percent(disk_percent)} full",
                body="Open UbuClean to scan for cache and junk that can be freed.",
            )

        threshold = float(settings.get("ram_threshold", 90))
        if ram_percent >= threshold:
            if self._ram_since is None:
                self._ram_since = now
            sustained = int(settings.get("ram_sustained_minutes", 5)) * 60
            last_ram = self._last_notify.get("ram", 0.0)
            if now - self._ram_since >= sustained and now - last_ram >= cooldown:
                self._last_notify["ram"] = now
                self._ram_since = None
                self._save_state()
                return NotificationDecision(
                    kind="ram",
                    title=f"RAM {format_percent(ram_percent)} in use",
                    body=(
                        "RAM usage has been high for several minutes. "
                        "Check the processes in the Monitor or drop the cache that has built up again."
                    ),
                )
        else:
            self._ram_since = None
        return None

    def check(self) -> NotificationDecision | None:
        """Read the system metrics and then evaluate the thresholds."""
        try:
            import psutil

            disk = psutil.disk_usage(self.disk_path)
            memory = psutil.virtual_memory()
        except Exception:
            return None
        return self.evaluate(disk.percent, memory.percent)

    # -- delivery ------------------------------------------------------
    def notify(self, decision: NotificationDecision) -> bool:
        """Send the desktop notification with two action buttons."""
        if self.ctx.runner.which("notify-send") is None:
            return False
        action_id, action_label = decision.actions
        result = self.ctx.runner.run(
            [
                "notify-send",
                "--app-name=UbuClean",
                "--icon=com.ubuclean.App",
                "--expire-time=60000",
                f"--action={action_id}={action_label}",
                "--action=open=Open UbuClean",
                decision.title,
                decision.body,
            ],
            timeout=70,
        )
        if result.returncode != 0:
            return False
        chosen = (result.stdout or "").strip().splitlines()
        if chosen:
            self._handle_action(chosen[0])
        return True

    def _handle_action(self, action_id: str) -> None:
        page = {"clean": "clean", "monitor": "monitor"}.get(action_id, "home")
        executable = shutil.which("ubuclean")
        argv = (
            [executable, "--page", page]
            if executable
            else [sys.executable, "-m", "ubuclean", "--page", page]
        )
        try:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass

    def watch(self, stop: threading.Event | None = None, *, interval: float = 60.0) -> None:
        """Watch loop; stops once ``stop`` is set."""
        stop = stop or threading.Event()
        while not stop.is_set():
            decision = self.check()
            if decision is not None:
                self.notify(decision)
            stop.wait(interval)
