"""Tes scheduler systemd user dan mode pembersihan terjadwal."""

from __future__ import annotations

import json
from pathlib import Path

from ubuclean.core.context import Context
from ubuclean.core.runner import CommandResult, FakeRunner
from ubuclean.core.scheduled import run_scheduled
from ubuclean.core.scheduler import Scheduler, calendar_expression


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_calendar_expression() -> None:
    assert calendar_expression("daily", "09:30") == "*-*-* 09:30:00"
    assert calendar_expression("weekly", "07:05") == "Mon *-*-* 07:05:00"
    assert calendar_expression("monthly", "23:59") == "*-*-01 23:59:00"
    for frequency, time_text in (("hourly", "09:00"), ("daily", "25:00"), ("daily", "9:00")):
        try:
            calendar_expression(frequency, time_text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"harus ditolak: {frequency} {time_text}")


def test_render_units(tmp_path: Path, fake_runner: FakeRunner) -> None:
    scheduler = Scheduler(tmp_path, fake_runner, exec_start="/usr/bin/ubuclean")
    service = scheduler.render_service()
    timer = scheduler.render_timer("weekly", "09:00")
    assert "ExecStart=/usr/bin/ubuclean --run-scheduled" in service
    assert "OnCalendar=Mon *-*-* 09:00:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=ubuclean-clean.service" in timer


def test_install_and_status(tmp_path: Path, fake_runner: FakeRunner) -> None:
    fake_runner.argv_responses[("systemctl", "--user", "is-enabled", "ubuclean-clean.timer")] = CommandResult(
        argv=("systemctl",), returncode=0, stdout="enabled\n"
    )
    fake_runner.argv_responses[("systemctl", "--user", "is-active", "ubuclean-clean.timer")] = CommandResult(
        argv=("systemctl",), returncode=0, stdout="active\n"
    )
    scheduler = Scheduler(tmp_path, fake_runner, exec_start="/usr/bin/ubuclean")
    ok, message = scheduler.install("daily", "09:30")
    assert ok, message
    assert scheduler.service_path.exists()
    assert scheduler.timer_path.exists()
    assert "09:30:00" in scheduler.timer_path.read_text(encoding="utf-8")
    assert ("systemctl", "--user", "daemon-reload") in fake_runner.calls
    assert ("systemctl", "--user", "enable", "--now", "ubuclean-clean.timer") in fake_runner.calls
    status = scheduler.status()
    assert status.installed is True
    assert status.enabled is True
    assert status.active is True


def test_install_invalid_schedule(tmp_path: Path, fake_runner: FakeRunner) -> None:
    scheduler = Scheduler(tmp_path, fake_runner, exec_start="/usr/bin/ubuclean")
    ok, message = scheduler.install("yearly", "09:00")
    assert not ok
    assert "Frekuensi" in message
    assert not scheduler.timer_path.exists()


def test_uninstall_removes_owned_units(tmp_path: Path, fake_runner: FakeRunner) -> None:
    scheduler = Scheduler(tmp_path, fake_runner, exec_start="/usr/bin/ubuclean")
    scheduler.install("daily", "09:00")
    ok, _message = scheduler.uninstall()
    assert ok
    assert not scheduler.timer_path.exists()
    assert not scheduler.service_path.exists()
    assert ("systemctl", "--user", "disable", "--now", "ubuclean-clean.timer") in fake_runner.calls


def test_uninstall_keeps_foreign_files(tmp_path: Path, fake_runner: FakeRunner) -> None:
    scheduler = Scheduler(tmp_path, fake_runner, exec_start="/usr/bin/ubuclean")
    scheduler.unit_dir.mkdir(parents=True)
    scheduler.timer_path.write_text("[Unit]\nDescription=lain\n", encoding="utf-8")
    scheduler.service_path.write_text("[Unit]\nDescription=lain\n", encoding="utf-8")
    ok, _message = scheduler.uninstall()
    assert ok
    assert scheduler.timer_path.exists()
    assert scheduler.service_path.exists()


def test_status_not_installed(tmp_path: Path, fake_runner: FakeRunner) -> None:
    scheduler = Scheduler(tmp_path, fake_runner)
    status = scheduler.status()
    assert status.installed is False
    assert "belum" in status.message.lower()


def test_run_scheduled_filters_risky_and_cleans(context: Context) -> None:
    context.config.update(
        {"schedule": {"enabled": True, "categories": ["user-cache", "browser-cache", "kernels"]}}
    )
    source = make_file(context.paths.cache_dir / "app/data", "1234")
    outcome = run_scheduled(context, notify=False)
    assert "user-cache" in outcome.cleaned
    assert "browser-cache" in outcome.skipped
    assert "kernels" in outcome.skipped
    assert not source.exists()
    assert outcome.freed_bytes == 4
    assert outcome.report is not None
    entries = context.history.entries()
    assert any(entry.action == "scheduled" for entry in entries)
    assert any(entry.category == "user-cache" for entry in entries)


def test_run_scheduled_skips_privileged(context: Context, fake_runner: FakeRunner) -> None:
    context.config.update({"schedule": {"categories": ["apt-cache"]}})
    outcome = run_scheduled(context, notify=False)
    assert outcome.cleaned == []
    assert "apt-cache" in outcome.skipped
    assert not [call for call in fake_runner.calls if call[0] == "pkexec"]


def test_run_scheduled_notifies(context: Context, fake_runner: FakeRunner) -> None:
    context.config.update({"schedule": {"categories": ["user-cache"]}})
    make_file(context.paths.cache_dir / "a/x", "1")
    fake_runner.paths["notify-send"] = "/usr/bin/notify-send"
    outcome = run_scheduled(context, notify=True)
    assert outcome.notified is True
    assert any(call[0] == "notify-send" for call in fake_runner.calls)


def test_run_scheduled_without_categories(context: Context, fake_runner: FakeRunner) -> None:
    context.config.update({"schedule": {"categories": []}})
    outcome = run_scheduled(context, notify=False)
    assert outcome.cleaned == []
    assert outcome.skipped == []
    assert fake_runner.calls == []


def test_outcome_to_dict(context: Context) -> None:
    context.config.update({"schedule": {"categories": ["user-cache"]}})
    outcome = run_scheduled(context, notify=False)
    payload = json.loads(json.dumps(outcome.to_dict()))
    assert "freed_bytes" in payload
    assert payload["cleaned"] == ["user-cache"]
