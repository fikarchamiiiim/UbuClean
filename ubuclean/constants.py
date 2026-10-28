"""UbuClean global constants."""

from __future__ import annotations

APP_ID = "com.ubuclean.App"
APP_NAME = "UbuClean"
VERSION = "1.0.0"

HELPER_NAME = "ubuclean-helper"
HELPER_PATH = f"/usr/lib/ubuclean/bin/{HELPER_NAME}"
POLKIT_ACTION = "com.ubuclean.helper.run"

DEFAULT_QUARANTINE_DAYS = 7
DEFAULT_TMP_DAYS = 7
DEFAULT_DEVELOPER_DAYS = 90

MONITOR_INTERVAL_SECONDS = 1.0
HISTORY_SECONDS = 60
TOP_PROCESS_LIMIT = 30

DISK_THRESHOLD_PERCENT = 90
RAM_THRESHOLD_PERCENT = 90
RAM_SUSTAINED_MINUTES = 5
NOTIFICATION_COOLDOWN_HOURS = 6

SCHEDULE_SERVICE_NAME = "ubuclean-clean.service"
SCHEDULE_TIMER_NAME = "ubuclean-clean.timer"
NOTIFIER_SERVICE_NAME = "ubuclean-notifier.service"

SYSTEMD_USER_DIR = "~/.config/systemd/user"
AUTOSTART_USER_DIR = "~/.config/autostart"
AUTOSTART_SYSTEM_DIR = "/etc/xdg/autostart"

WEB_BROWSER_CACHE_DIRS = (
    "mozilla/firefox",
    "google-chrome",
    "google-chrome-beta",
    "chromium",
    "chromium-browser",
)
