"""Tes konfigurasi UbuClean."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ubuclean.config import Config, DEFAULT_CONFIG, sanitize_config


def test_defaults_are_valid() -> None:
    config = sanitize_config(None)
    assert config["tmp_days"] == 7
    assert config["quarantine_days"] == 7
    assert config["drop_caches_level"] == 2
    assert config["schedule"]["frequency"] == "weekly"
    assert config["notifications"]["disk_threshold"] == 90


def test_clamping_and_validation() -> None:
    config = sanitize_config(
        {
            "tmp_days": 9999,
            "quarantine_days": -5,
            "developer_days": "abc",
            "drop_caches_level": 9,
            "schedule": {"frequency": "hourly", "time": "25:99", "categories": ["a", "a", 3]},
            "notifications": {
                "disk_threshold": 10,
                "ram_threshold": 200,
                "ram_sustained_minutes": 0,
                "cooldown_hours": 999,
            },
        }
    )
    assert config["tmp_days"] == 365
    assert config["quarantine_days"] == 1
    assert config["developer_days"] == 90
    assert config["drop_caches_level"] == 3
    assert config["schedule"]["frequency"] == "weekly"
    assert config["schedule"]["time"] == "09:00"
    assert config["schedule"]["categories"] == ["a"]
    assert config["notifications"]["disk_threshold"] == 50
    assert config["notifications"]["ram_threshold"] == 99
    assert config["notifications"]["ram_sustained_minutes"] == 1
    assert config["notifications"]["cooldown_hours"] == 168


def test_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.json"
    config = Config.load(path)
    config.update({"tmp_days": 14, "schedule": {"enabled": True, "frequency": "daily"}})
    config.save()
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    loaded = Config.load(path)
    assert loaded.get("tmp_days") == 14
    assert loaded.schedule["enabled"] is True
    assert loaded.schedule["frequency"] == "daily"
    assert loaded.schedule["time"] == "09:00"


def test_corrupt_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{rusak", encoding="utf-8")
    config = Config.load(path)
    assert config.get("quarantine_days") == DEFAULT_CONFIG["quarantine_days"]


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "missing.json")
    assert config.data["developer_roots"] == DEFAULT_CONFIG["developer_roots"]


def test_update_does_not_mutate_returned_copy(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "config.json")
    snapshot = config.data
    snapshot["tmp_days"] = 300
    assert config.get("tmp_days") == 7


def test_developer_roots_filtered() -> None:
    config = sanitize_config({"developer_roots": ["", "  ", "/home/x/projects", 5]})
    assert config["developer_roots"] == ["/home/x/projects"]


def test_notifications_bool_strings() -> None:
    config = sanitize_config({"notifications": {"muted": "true", "enabled": "no"}})
    assert config["notifications"]["muted"] is True
    assert config["notifications"]["enabled"] is False
