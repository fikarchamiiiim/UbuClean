"""Shared services created once per application."""

from __future__ import annotations

from ubuclean.config import Config
from ubuclean.core.context import Context
from ubuclean.core.models import CleanReport, Preview
from ubuclean.core.paths import AppPaths
from ubuclean.core.ram import RamService
from ubuclean.core.monitor import MonitorSampler
from ubuclean.core.scan import CleanAssistant
from ubuclean.core.scheduler import Scheduler
from ubuclean.core.startup import StartupService
from ubuclean.core.uninstaller import UninstallerService


class Services:
    """Collection of core services used by the UI pages.

    All heavy operations are still run by the caller through worker threads;
    this class only holds session state.
    """

    def __init__(self) -> None:
        self.paths = AppPaths.default()
        try:
            self.paths.ensure_dirs()
        except OSError:
            pass
        self.config = Config.load(self.paths.config_file)
        self.context = Context.build(paths=self.paths, config=self.config)
        self.assistant = CleanAssistant(self.context)
        self.ram = RamService(self.context)
        self.monitor = MonitorSampler(disk_path="/")
        self.startup = StartupService(self.paths.home, self.context.runner)
        self.scheduler = Scheduler(self.paths.home, self.context.runner)
        self.uninstaller = UninstallerService(self.context)
        self.last_previews: dict[str, Preview] = {}
        self.last_report: CleanReport | None = None

    def save_config(self) -> None:
        """Save the user configuration."""
        try:
            self.config.save()
        except OSError:
            pass

    def purge_expired_quarantine(self) -> list[str]:
        """Delete expired quarantine entries (called from a background thread)."""
        return self.context.quarantine.purge_expired()
