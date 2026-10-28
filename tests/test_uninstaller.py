"""Tes uninstaller dan pencarian sisa konfigurasi."""

from __future__ import annotations

import time
from pathlib import Path

from ubuclean.core.context import Context
from ubuclean.core.runner import CommandResult, FakeRunner
from ubuclean.core.uninstaller import (
    InstalledApp,
    UninstallerService,
    apt_protected_reason,
    installed_at_text,
    parse_dpkg_listing,
    parse_flatpak_listing,
    parse_snap_listing,
)


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_dpkg_listing(tmp_path: Path) -> None:
    info = tmp_path / "info"
    info.mkdir()
    make_file(info / "gimp.list", "")
    output = (
        "gimp\t2.10\t5000\tinstall ok installed\tGIMP image editor\n"
        "ubuntu-desktop\t1.0\t100\tinstall ok installed\tUbuntu Desktop\n"
        "old\t1.0\t100\tdeinstall ok config-files\tOld package\n"
    )
    apps = {app.id: app for app in parse_dpkg_listing(output, info)}
    assert set(apps) == {"gimp", "ubuntu-desktop"}
    assert apps["gimp"].size == 5000 * 1024
    assert apps["gimp"].installed_at is not None
    assert apps["gimp"].description == "GIMP image editor"
    assert apps["ubuntu-desktop"].protected is True


def test_apt_protected_reason() -> None:
    assert apt_protected_reason("ubuntu-desktop") is not None
    assert apt_protected_reason("linux-image-6.8.0-31-generic") is not None
    assert apt_protected_reason("gimp") is None


def test_parse_snap_listing(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snaps"
    make_file(snap_dir / "firefox_4530.snap", "12345")
    output = (
        "Name     Version  Rev   Tracking       Publisher   Notes\n"
        "firefox  128.0    4530  latest/stable  mozilla**   -\n"
        "core20   2024     2318  latest/stable  canonical** base\n"
        "bad\n"
    )
    apps = {app.id: app for app in parse_snap_listing(output, snap_dir)}
    assert set(apps) == {"firefox", "core20"}
    assert apps["firefox"].size == 5
    assert apps["firefox"].description == "latest/stable · mozilla**"
    assert apps["core20"].protected is True


def test_parse_flatpak_listing(home: Path) -> None:
    app_dir = home / ".local/share/flatpak/app/org.gnome.Builder"
    app_dir.mkdir(parents=True)
    output = (
        "org.gnome.Builder\tBuilder\t46.0\t1.2 GB\tflathub\n"
        "org.gnome.Platform\tPlatform\t46\t800 MB\tflathub\n"
    )
    apps = {app.id: app for app in parse_flatpak_listing(output, home)}
    assert apps["org.gnome.Builder"].size == int(1.2 * 1024**3)
    assert apps["org.gnome.Builder"].installed_at is not None
    assert apps["org.gnome.Platform"].protected is True


def test_list_packages(context: Context, fake_runner: FakeRunner) -> None:
    fake_runner.argv_responses[("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Installed-Size}\t${Status}\t${binary:Summary}\n")] = CommandResult(
        argv=("dpkg-query",), returncode=0, stdout="gimp\t2.10\t100\tinstall ok installed\tGIMP\n"
    )
    fake_runner.paths["snap"] = "/usr/bin/snap"
    fake_runner.responses["snap"] = CommandResult(
        argv=("snap",),
        returncode=0,
        stdout=(
            "Name     Version  Rev   Tracking  Publisher  Notes\n"
            "firefox  128.0    4530  stable    mozilla**  -\n"
        ),
    )
    fake_runner.paths["flatpak"] = "/usr/bin/flatpak"
    fake_runner.argv_responses[
        ("flatpak", "list", "--app", "--columns=application,name,version,size,origin")
    ] = CommandResult(
        argv=("flatpak",),
        returncode=0,
        stdout="org.gnome.Builder\tBuilder\t46\t1 GB\tflathub\n",
    )
    service = UninstallerService(context)
    packages = service.list_packages()
    assert {app.source for app in packages} == {"apt", "snap", "flatpak"}
    assert packages[0].name.lower() <= packages[-1].name.lower()


def test_dependent_count(context: Context, fake_runner: FakeRunner) -> None:
    fake_runner.responses["apt-cache"] = CommandResult(
        argv=("apt-cache",),
        returncode=0,
        stdout=(
            "gimp\nReverse Depends:\n  gimp-plugin-x\n  gimp-plugin-y\n  gimp-plugin-x\n"
        ),
    )
    service = UninstallerService(context)
    assert service.dependent_count("gimp") == 2
    assert service.dependent_count("../bad") == 0


def test_remove_protected_does_nothing(context: Context, fake_runner: FakeRunner) -> None:
    service = UninstallerService(context)
    app = InstalledApp(
        id="ubuntu-desktop",
        name="ubuntu-desktop",
        source="apt",
        protected_reason="Paket inti sistem",
    )
    ok, message = service.remove(app)
    assert not ok
    assert "inti" in message
    assert fake_runner.calls == []


def test_remove_apt_uses_helper(context: Context, fake_runner: FakeRunner) -> None:
    fake_runner.responses["pkexec"] = CommandResult(
        argv=("pkexec",),
        returncode=0,
        stdout='{"ok": true}',
    )
    service = UninstallerService(context)
    app = InstalledApp(id="gimp", name="gimp", source="apt")
    ok, message = service.remove(app)
    assert ok, message
    call = fake_runner.calls[-1]
    assert call[:3] == ("pkexec", "/usr/lib/ubuclean/bin/ubuclean-helper", "apt-remove")
    assert call[-1] == "gimp"


def test_remove_auth_error(context: Context, fake_runner: FakeRunner) -> None:
    fake_runner.responses["pkexec"] = CommandResult(argv=("pkexec",), returncode=126)
    service = UninstallerService(context)
    app = InstalledApp(id="gimp", name="gimp", source="apt")
    ok, message = service.remove(app)
    assert not ok
    assert "dibatalkan" in message.lower()


def test_find_leftovers(context: Context) -> None:
    gimp_config = make_file(context.paths.home / ".config/gimp/gtkrc", "x")
    gimp_cache = make_file(context.paths.home / ".cache/gimp/cache", "xx")
    flatpak_data = make_file(context.paths.home / ".local/share/org.gimp.GIMP/data", "xxx")
    unrelated = make_file(context.paths.home / ".config/other/file", "x")
    service = UninstallerService(context)
    app = InstalledApp(id="org.gimp.GIMP", name="GIMP", source="flatpak")
    leftovers = service.find_leftovers(app)
    paths = {item.path for item in leftovers}
    assert gimp_config.parent in paths
    assert gimp_cache.parent in paths
    assert flatpak_data.parent in paths
    assert unrelated.parent not in paths
    assert sum(item.size for item in leftovers) == 6


def test_quarantine_leftovers(context: Context) -> None:
    folder = context.paths.home / ".config/gimp"
    make_file(folder / "gtkrc", "1234")
    service = UninstallerService(context)
    app = InstalledApp(id="gimp", name="gimp", source="apt")
    entry, message = service.quarantine_leftovers(app, [folder])
    assert entry is not None
    assert entry.category_id == "uninstaller"
    assert not folder.exists()
    assert context.quarantine.get(entry.action_id) is not None
    assert "karantina" in message


def test_quarantine_leftovers_rejects_outside(context: Context) -> None:
    service = UninstallerService(context)
    app = InstalledApp(id="gimp", name="gimp", source="apt")
    entry, _message = service.quarantine_leftovers(app, [Path("/etc/passwd")])
    assert entry is None


def test_installed_at_text() -> None:
    assert installed_at_text(None) == "Tanggal tidak tersedia"
    assert "2026" in installed_at_text(time.mktime((2026, 1, 2, 0, 0, 0, 0, 0, -1)))
