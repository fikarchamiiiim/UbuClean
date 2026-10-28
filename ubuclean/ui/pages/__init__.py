"""UbuClean pages."""

from ubuclean.ui.pages.base import BasePage
from ubuclean.ui.pages.clean import CleanPage
from ubuclean.ui.pages.disk import DiskPage
from ubuclean.ui.pages.history import HistoryPage
from ubuclean.ui.pages.home import HomePage
from ubuclean.ui.pages.memory import MemoryPage
from ubuclean.ui.pages.monitor import MonitorPage
from ubuclean.ui.pages.schedule import SchedulePage
from ubuclean.ui.pages.settings import SettingsPage
from ubuclean.ui.pages.startup import StartupPage

__all__ = [
    "BasePage",
    "CleanPage",
    "DiskPage",
    "HistoryPage",
    "HomePage",
    "MemoryPage",
    "MonitorPage",
    "SchedulePage",
    "SettingsPage",
    "StartupPage",
]
