"""Tes unit pemantau notifikasi."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.notifier_service import NotifierService
from ubuclean.core.runner import CommandResult, FakeRunner


def test_install_writes_unit(home: Path, fake_runner: FakeRunner) -> None:
    service = NotifierService(home, fake_runner, exec_start="/usr/bin/ubuclean")
    ok, message = service.install()
    assert ok, message
    assert service.path.exists()
    content = service.path.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/ubuclean --watch" in content
    assert ("systemctl", "--user", "enable", "--now", "ubuclean-notifier.service") in fake_runner.calls


def test_status(home: Path, fake_runner: FakeRunner) -> None:
    service = NotifierService(home, fake_runner, exec_start="/usr/bin/ubuclean")
    status = service.status()
    assert status.installed is False
    assert "not been installed" in status.message.lower()
    fake_runner.argv_responses[
        ("systemctl", "--user", "is-active", "ubuclean-notifier.service")
    ] = CommandResult(argv=("systemctl",), returncode=0, stdout="active\n")
    service.install()
    status = service.status()
    assert status.installed is True
    assert status.active is True


def test_uninstall_removes_owned_unit(home: Path, fake_runner: FakeRunner) -> None:
    service = NotifierService(home, fake_runner, exec_start="/usr/bin/ubuclean")
    service.install()
    ok, _message = service.uninstall()
    assert ok
    assert not service.path.exists()


def test_uninstall_keeps_foreign_unit(home: Path, fake_runner: FakeRunner) -> None:
    service = NotifierService(home, fake_runner, exec_start="/usr/bin/ubuclean")
    service.unit_dir.mkdir(parents=True)
    service.path.write_text("[Unit]\nDescription=lain\n", encoding="utf-8")
    ok, _message = service.uninstall()
    assert ok
    assert service.path.exists()
