"""UbuClean user configuration (``~/.config/ubuclean/config.json``)."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ubuclean import constants

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "tmp_days": constants.DEFAULT_TMP_DAYS,
    "journal_days": 14,
    "quarantine_days": constants.DEFAULT_QUARANTINE_DAYS,
    "developer_days": constants.DEFAULT_DEVELOPER_DAYS,
    "developer_roots": ["~/projects", "~/src", "~/code"],
    "drop_caches_level": 2,
    "schedule": {
        "enabled": False,
        "frequency": "weekly",
        "time": "09:00",
        "categories": ["apt-cache", "journal", "tmp", "trash", "user-cache"],
    },
    "notifications": {
        "enabled": True,
        "watcher_enabled": False,
        "disk_threshold": constants.DISK_THRESHOLD_PERCENT,
        "ram_threshold": constants.RAM_THRESHOLD_PERCENT,
        "ram_sustained_minutes": constants.RAM_SUSTAINED_MINUTES,
        "cooldown_hours": constants.NOTIFICATION_COOLDOWN_HOURS,
        "muted": False,
    },
}

_FREQUENCIES = ("daily", "weekly", "monthly")


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into ``base`` without modifying the input."""
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _as_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Convert to int and clamp to the allowed range."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _as_bool(value: object, default: bool) -> bool:
    """Loose conversion to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ya", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def sanitize_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and complete the raw configuration into a safe shape."""
    merged = _deep_merge(DEFAULT_CONFIG, raw or {})
    config: dict[str, Any] = {
        "version": 1,
        "tmp_days": _as_int(merged.get("tmp_days"), constants.DEFAULT_TMP_DAYS, 1, 365),
        "journal_days": _as_int(merged.get("journal_days"), 14, 1, 365),
        "quarantine_days": _as_int(
            merged.get("quarantine_days"), constants.DEFAULT_QUARANTINE_DAYS, 1, 365
        ),
        "developer_days": _as_int(
            merged.get("developer_days"), constants.DEFAULT_DEVELOPER_DAYS, 1, 3650
        ),
        "drop_caches_level": _as_int(merged.get("drop_caches_level"), 2, 1, 3),
    }
    roots = merged.get("developer_roots")
    if not isinstance(roots, list):
        roots = DEFAULT_CONFIG["developer_roots"]
    cleaned_roots = [
        str(root).strip()
        for root in roots[:20]
        if isinstance(root, str) and str(root).strip()
    ]
    config["developer_roots"] = cleaned_roots or list(DEFAULT_CONFIG["developer_roots"])

    schedule = merged.get("schedule") if isinstance(merged.get("schedule"), Mapping) else {}
    frequency = str(schedule.get("frequency", "weekly")).lower()
    if frequency not in _FREQUENCIES:
        frequency = "weekly"
    time_text = str(schedule.get("time", "09:00")).strip()
    if not _TIME_RE.fullmatch(time_text):
        time_text = "09:00"
    raw_categories = schedule.get("categories")
    if not isinstance(raw_categories, list):
        raw_categories = DEFAULT_CONFIG["schedule"]["categories"]
    categories: list[str] = []
    for category in raw_categories:
        if isinstance(category, str) and category and category not in categories:
            categories.append(category)
    config["schedule"] = {
        "enabled": _as_bool(schedule.get("enabled"), False),
        "frequency": frequency,
        "time": time_text,
        "categories": categories,
    }

    notifications = (
        merged.get("notifications")
        if isinstance(merged.get("notifications"), Mapping)
        else {}
    )
    config["notifications"] = {
        "enabled": _as_bool(notifications.get("enabled"), True),
        "watcher_enabled": _as_bool(notifications.get("watcher_enabled"), False),
        "disk_threshold": _as_int(
            notifications.get("disk_threshold"), constants.DISK_THRESHOLD_PERCENT, 50, 99
        ),
        "ram_threshold": _as_int(
            notifications.get("ram_threshold"), constants.RAM_THRESHOLD_PERCENT, 50, 99
        ),
        "ram_sustained_minutes": _as_int(
            notifications.get("ram_sustained_minutes"),
            constants.RAM_SUSTAINED_MINUTES,
            1,
            1440,
        ),
        "cooldown_hours": _as_int(
            notifications.get("cooldown_hours"),
            constants.NOTIFICATION_COOLDOWN_HOURS,
            1,
            168,
        ),
        "muted": _as_bool(notifications.get("muted"), False),
    }
    return config


class Config:
    """Configuration that is always validated on read and save."""

    def __init__(self, data: Mapping[str, Any] | None = None, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._data = sanitize_config(data)

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Read the configuration; a missing or corrupt file yields defaults."""
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, Mapping):
            raw = {}
        return cls(raw, path=path)

    @property
    def data(self) -> dict[str, Any]:
        """Copy of the configuration so callers cannot mutate internal state."""
        return copy.deepcopy(self._data)

    @property
    def schedule(self) -> dict[str, Any]:
        """The schedule configuration section."""
        return copy.deepcopy(self._data["schedule"])

    @property
    def notifications(self) -> dict[str, Any]:
        """The notifications configuration section."""
        return copy.deepcopy(self._data["notifications"])

    def get(self, key: str, default: Any = None) -> Any:
        """Read a top-level value."""
        return self._data.get(key, default)

    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """Merge changes, re-validate, and return the new data."""
        self._data = sanitize_config(_deep_merge(self._data, patch))
        return self.data

    def save(self) -> None:
        """Write the configuration atomically with 0600 permissions."""
        if self.path is None:
            raise ValueError("Config has no destination path.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".config-",
            delete=False,
        )
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(handle.name, 0o600)
            os.replace(handle.name, self.path)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
