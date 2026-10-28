"""Tes pengelola startup (autostart + systemd user service)."""

from __future__ import annotations

from pathlib import Path

from ubuclean.core.runner import CommandResult, FakeRunner
from ubuclean.core.startup import StartupEntry, StartupService, _read_desktop


def make_desktop(path: Path, name: str, exec_line: str, *, hidden: bool = False, comment: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name}",
        f"Exec={exec_line}",
    ]
    if comment:
        lines.append(f"Comment={comment}")
    if hidden:
        lines.append("Hidden=true")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def service(home: Path, system_dir: Path) -> StartupService:
    return StartupService(home, FakeRunner(), system_dir=system_dir)


def test_parse_desktop_and_enabled(tmp_path: Path) -> None:
    path = make_desktop(tmp_path / "a.desktop", "Aplikasi A", "app-a --start", comment="Deskripsi")
    values = _read_desktop(path)
    assert values is not None
    assert values["Name"] == "Aplikasi A"
    assert values["Exec"] == "app-a --start"


def test_list_merges_and_override(home: Path, tmp_path: Path) -> None:
    system_dir = tmp_path / "system"
    make_desktop(system_dir / "app.desktop", "Sistem App", "system-app", comment="Sistem")
    make_desktop(home / ".config/autostart/app.desktop", "Sistem App", "system-app", hidden=True)
    make_desktop(home / ".config/autostart/user.desktop", "User App", "user-app --flag")
    startup = service(home, system_dir)
    entries = {entry.filename: entry for entry in startup.list_desktop_entries()}
    assert set(entries) == {"app.desktop", "user.desktop"}
    assert entries["app.desktop"].enabled is False
    assert entries["app.desktop"].overridden is True
    assert entries["app.desktop"].is_system is True
    assert entries["app.desktop"].name == "Sistem App"
    assert entries["user.desktop"].source == "pengguna"
    assert entries["user.desktop"].command == "user-app --flag"


def test_toggle_user_entry_keeps_file(home: Path, tmp_path: Path) -> None:
    system_dir = tmp_path / "system"
    path = make_desktop(home / ".config/autostart/app.desktop", "App", "app --start")
    startup = service(home, system_dir)
    entry = startup.list_desktop_entries()[0]
    ok, _message = startup.set_enabled(entry, False)
    assert ok
    assert path.exists()
    values = _read_desktop(path)
    assert values is not None
    assert values["Hidden"] == "true"
    assert values["Exec"] == "app --start"

    refreshed = startup.list_desktop_entries()[0]
    assert refreshed.enabled is False
    ok, _message = startup.set_enabled(refreshed, True)
    assert ok
    values = _read_desktop(path)
    assert values is not None
    assert values["Hidden"] == "false"


def test_toggle_system_entry_creates_override(home: Path, tmp_path: Path) -> None:
    system_dir = tmp_path / "system"
    system_path = make_desktop(system_dir / "app.desktop", "App Sistem", "system-app", comment="Asli")
    startup = service(home, system_dir)
    entry = startup.list_desktop_entries()[0]
    ok, message = startup.set_enabled(entry, False)
    assert ok, message
    assert system_path.exists()
    override = home / ".config/autostart/app.desktop"
    assert override.exists()
    values = _read_desktop(override)
    assert values is not None
    assert values["Hidden"] == "true"
    assert values["Exec"] == "system-app"
    assert values["X-UbuClean-Override"] == "true"

    refreshed = startup.list_desktop_entries()[0]
    assert refreshed.enabled is False
    assert refreshed.overridden is True
    ok, _message = startup.set_enabled(refreshed, True)
    assert ok
    values = _read_desktop(override)
    assert values is not None
    assert values["Hidden"] == "false"


def test_rejects_unsafe_filename(home: Path, tmp_path: Path) -> None:
    startup = service(home, tmp_path)
    entry = StartupEntry(
        key="desktop:../../evil.desktop",
        name="Evil",
        description="",
        command="evil",
        source="pengguna",
        enabled=True,
        filename="../../evil.desktop",
    )
    ok, message = startup.set_enabled(entry, False)
    assert not ok
    assert "tidak valid" in message.lower()


def test_systemd_listing(home: Path, tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.argv_responses[
        (
            "systemctl",
            "--user",
            "list-unit-files",
            "--type=service",
            "--state=enabled",
            "--no-legend",
            "--no-pager",
        )
    ] = CommandResult(
        argv=("systemctl",),
        returncode=0,
        stdout="app.service enabled enabled\nbackup.service enabled disabled\n",
    )
    runner.argv_responses[
        ("systemctl", "--user", "show", "-p", "Description", "--value", "app.service")
    ] = CommandResult(argv=("systemctl",), returncode=0, stdout="Aplikasi latar\n")
    runner.argv_responses[
        ("systemctl", "--user", "show", "-p", "FragmentPath", "--value", "app.service")
    ] = CommandResult(argv=("systemctl",), returncode=0, stdout="/home/u/.config/systemd/user/app.service\n")
    startup = StartupService(home, runner, system_dir=tmp_path)
    entries = startup.list_systemd_services()
    assert [entry.unit for entry in entries] == ["app.service", "backup.service"]
    assert entries[0].description == "Aplikasi latar"
    assert entries[0].command.endswith("app.service")
    assert entries[0].source == "systemd"


def test_systemd_toggle(home: Path, tmp_path: Path) -> None:
    runner = FakeRunner()
    startup = StartupService(home, runner, system_dir=tmp_path)
    entry = StartupEntry(
        key="systemd:app.service",
        name="app",
        description="",
        command="app",
        source="systemd",
        enabled=True,
        unit="app.service",
    )
    ok, _message = startup.set_enabled(entry, False)
    assert ok
    assert ("systemctl", "--user", "disable", "--now", "app.service") in runner.calls
    assert startup.set_enabled(entry, True)[0] is True
    assert ("systemctl", "--user", "enable", "--now", "app.service") in runner.calls


def test_systemd_toggle_failure(home: Path, tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.responses["systemctl"] = CommandResult(
        argv=("systemctl",), returncode=1, stderr="Unit not found"
    )
    startup = StartupService(home, runner, system_dir=tmp_path)
    entry = StartupEntry(
        key="systemd:x.service",
        name="x",
        description="",
        command="x",
        source="systemd",
        enabled=True,
        unit="x.service",
    )
    ok, message = startup.set_enabled(entry, True)
    assert not ok
    assert "Unit not found" in message
