"""Tes aksi helper root tanpa menyentuh sistem nyata."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from ubuclean.helper import actions, validate
from ubuclean.helper.actions import HelperContext
from ubuclean.helper.__main__ import main as helper_main
from ubuclean.core.runner import CommandResult, FakeRunner


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class DeletingAptRunner(FakeRunner):
    """FakeRunner yang meniru efek apt-get clean."""

    def __init__(self, archives: Path) -> None:
        super().__init__()
        self.archives = archives

    def run(self, argv, *, timeout=60.0, env=None):
        result = super().run(argv, timeout=timeout, env=env)
        if tuple(argv[:2]) == ("apt-get", "clean"):
            shutil.rmtree(self.archives, ignore_errors=True)
            self.archives.mkdir(parents=True, exist_ok=True)
        return result


class SequenceRunner(FakeRunner):
    """Kembalikan respons berurutan untuk perintah yang sama."""

    def __init__(self, sequence: list[CommandResult]) -> None:
        super().__init__()
        self.sequence = list(sequence)

    def run(self, argv, *, timeout=60.0, env=None):
        result = super().run(argv, timeout=timeout, env=env)
        if self.sequence:
            return self.sequence.pop(0)
        return result


def test_clean_apt_measures_freed(tmp_path: Path) -> None:
    archives = tmp_path / "var/cache/apt/archives"
    make_file(archives / "a.deb", "12345")
    make_file(archives / "partial/b.deb", "123")
    ctx = HelperContext(root=tmp_path, runner=DeletingAptRunner(archives))
    result = actions.clean_apt(ctx)
    assert result.ok
    assert result.freed_bytes == 8
    assert result.removed == 2


def test_autoremove_parses_output(tmp_path: Path) -> None:
    runner = FakeRunner(
        responses={
            "apt-get": CommandResult(
                argv=("apt-get",),
                returncode=0,
                stdout=(
                    "The following packages will be REMOVED:\n"
                    "  libslirp0 slirp4netns\n"
                    "0 upgraded, 0 newly installed, 2 to remove and 0 not upgraded.\n"
                    "After this operation, 1,024 kB disk space will be freed.\n"
                    "Removing libslirp0 (1.2.1)\n"
                    "Removing slirp4netns (1.2.1)\n"
                ),
            )
        }
    )
    result = actions.autoremove(HelperContext(root=tmp_path, runner=runner))
    assert result.ok
    assert result.removed == 2
    assert result.freed_bytes == 1024 * 1024


def kernel_dpkg_output(releases: list[str], size_kb: int = 1000) -> str:
    lines = []
    for release in releases:
        lines.append(f"linux-image-{release}\t{size_kb}\tinstall ok installed")
        lines.append(f"linux-headers-{release}\t100\tinstall ok installed")
    return "\n".join(lines) + "\n"


def test_purge_kernels_success(tmp_path: Path) -> None:
    running = os.uname().release
    releases = ["6.8.0-40-generic", "6.8.0-41-generic", running]

    class KernelRunner(FakeRunner):
        def run(self, argv, *, timeout=60.0, env=None):
            if argv and argv[0] == "dpkg-query" and "Installed-Size" in str(argv[2]):
                return CommandResult(
                    argv=tuple(argv),
                    returncode=0,
                    stdout="1000\tlinux-image-6.8.0-40-generic\n",
                )
            return super().run(argv, timeout=timeout, env=env)

    runner = KernelRunner()
    runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",), returncode=0, stdout=kernel_dpkg_output(releases)
    )
    ctx = HelperContext(root=tmp_path, runner=runner)
    result = actions.purge_kernels(ctx, ["linux-image-6.8.0-40-generic"])
    assert result.ok
    assert result.removed == 1
    assert result.freed_bytes == 1000 * 1024
    purge_calls = [call for call in runner.calls if call[:3] == ("apt-get", "purge", "-y")]
    assert purge_calls and purge_calls[0][-1] == "linux-image-6.8.0-40-generic"
    assert "--" in purge_calls[0]


def test_purge_kernels_refuses_running(tmp_path: Path) -> None:
    running = os.uname().release
    runner = FakeRunner()
    ctx = HelperContext(root=tmp_path, runner=runner)
    result = actions.purge_kernels(ctx, [f"linux-image-{running}"])
    assert not result.ok
    assert "running kernel" in result.errors[0].lower()
    assert not runner.calls


def test_purge_kernels_requires_backup(tmp_path: Path) -> None:
    running = os.uname().release
    runner = FakeRunner()
    runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",),
        returncode=0,
        stdout=kernel_dpkg_output([running, "6.8.0-40-generic"]),
    )
    ctx = HelperContext(root=tmp_path, runner=runner)
    result = actions.purge_kernels(ctx, ["linux-image-6.8.0-40-generic"])
    assert not result.ok
    assert "spare kernel" in result.errors[0]
    purge_calls = [call for call in runner.calls if call[:3] == ("apt-get", "purge", "-y")]
    assert not purge_calls


def test_purge_kernels_rejects_meta_and_injection(tmp_path: Path) -> None:
    ctx = HelperContext(root=tmp_path, runner=FakeRunner())
    with pytest.raises(validate.ValidationError):
        actions.purge_kernels(ctx, ["linux-image-generic"])
    with pytest.raises(validate.ValidationError):
        actions.purge_kernels(ctx, ["linux-image-6.8.0-31-generic --force-yes"])


SNAP_LIST = """Name      Version  Rev   Tracking       Publisher   Notes
firefox   128.0    4530  latest/stable  mozilla**   -
firefox   127.0    4472  latest/stable  mozilla**   disabled
core20    2024     2318  latest/stable  canonical** base,disabled
"""


def test_snap_old_revisions(tmp_path: Path) -> None:
    snap_dir = tmp_path / "var/lib/snapd/snaps"
    make_file(snap_dir / "firefox_4472.snap", "12345")
    runner = FakeRunner()
    runner.argv_responses[("snap", "list", "--all")] = CommandResult(
        argv=("snap", "list", "--all"), returncode=0, stdout=SNAP_LIST
    )
    ctx = HelperContext(root=tmp_path, runner=runner)
    result = actions.snap_old_revisions(ctx, ["firefox:4472"])
    assert result.ok
    assert result.removed == 1
    assert result.freed_bytes == 5


def test_snap_old_revisions_rejects_active(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.argv_responses[("snap", "list", "--all")] = CommandResult(
        argv=("snap", "list", "--all"), returncode=0, stdout=SNAP_LIST
    )
    ctx = HelperContext(root=tmp_path, runner=runner)
    result = actions.snap_old_revisions(ctx, ["firefox:4530"])
    assert not result.ok
    assert "disabled" in result.errors[0]


def test_journal_vacuum_measures(tmp_path: Path) -> None:
    runner = SequenceRunner(
        [
            CommandResult(argv=("journalctl",), returncode=0, stdout="Archived and active journals take up 1.0G in the file system."),
            CommandResult(argv=("journalctl",), returncode=0, stdout=(
                "Vacuuming done, freed 100.0M of archived journals from /var/log/journal.\n"
            )),
            CommandResult(argv=("journalctl",), returncode=0, stdout="Archived and active journals take up 924.0M in the file system."),
        ]
    )
    result = actions.journal_vacuum(HelperContext(root=tmp_path, runner=runner), "14d")
    assert result.ok
    assert result.freed_bytes == 100 * 1024 * 1024


def test_clean_crash(tmp_path: Path) -> None:
    crash = tmp_path / "var/crash"
    report = make_file(crash / "_usr_bin_x.1000.crash", "abcdef")
    other = make_file(crash / "keep.txt", "123")
    ctx = HelperContext(root=tmp_path, runner=FakeRunner())
    result = actions.clean_crash(ctx, ["_usr_bin_x.1000.crash"])
    assert result.ok
    assert result.removed == 1
    assert result.freed_bytes == 6
    assert not report.exists()
    assert other.exists()


def test_clean_crash_refuses_symlink(tmp_path: Path) -> None:
    crash = tmp_path / "var/crash"
    crash.mkdir(parents=True)
    link = crash / "evil.crash"
    link.symlink_to("/etc/hostname")
    ctx = HelperContext(root=tmp_path, runner=FakeRunner())
    result = actions.clean_crash(ctx, ["evil.crash"])
    assert not result.ok
    assert link.is_symlink()


def test_clean_crash_ignores_path_traversal(tmp_path: Path) -> None:
    ctx = HelperContext(root=tmp_path, runner=FakeRunner())
    result = actions.clean_crash(ctx, ["../etc/passwd"])
    assert not result.ok
    assert result.errors


def test_clean_tmp(tmp_path: Path) -> None:
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    old = make_file(tmp_dir / "old.log", "12345")
    recent = make_file(tmp_dir / "recent.log", "1")
    private = make_file(tmp_dir / "systemd-private-abc/x", "1")
    directory = tmp_dir / "olddir"
    make_file(directory / "a", "123")
    now = time.time()
    for path in (old, directory, private, private.parent):
        os.utime(path, (now - 10 * 86400, now - 10 * 86400))
    runner = FakeRunner()
    result = actions.clean_tmp(HelperContext(root=tmp_path, runner=runner), 7)
    assert result.ok
    assert not old.exists()
    assert not directory.exists()
    assert recent.exists()
    assert private.exists()
    assert result.freed_bytes == 8


def test_clean_tmp_skips_in_use(tmp_path: Path) -> None:
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    old = make_file(tmp_dir / "busy", "123")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    runner = FakeRunner(paths={"fuser": "/usr/bin/fuser"})
    runner.argv_responses[("/usr/bin/fuser", "-s", "--", str(old))] = CommandResult(
        argv=("fuser",), returncode=0
    )
    result = actions.clean_tmp(HelperContext(root=tmp_path, runner=runner), 7)
    assert result.ok
    assert old.exists()
    assert "in use" in result.detail


def test_drop_caches(tmp_path: Path) -> None:
    target = tmp_path / "proc/sys/vm/drop_caches"
    target.parent.mkdir(parents=True)
    target.write_text("0", encoding="ascii")
    runner = FakeRunner()
    result = actions.drop_caches(HelperContext(root=tmp_path, runner=runner), 3)
    assert result.ok
    assert target.read_text(encoding="ascii") == "3"
    assert ("sync",) in runner.calls


def write_meminfo(path: Path, available: int, total: int, free: int) -> None:
    path.write_text(
        f"MemAvailable: {available} kB\nSwapTotal: {total} kB\nSwapFree: {free} kB\n",
        encoding="utf-8",
    )


def test_swap_refresh_insufficient_ram(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=500_000, total=2_000_000, free=0)
    runner = FakeRunner()
    result = actions.swap_refresh(HelperContext(root=tmp_path, runner=runner), meminfo)
    assert not result.ok
    assert "not enough" in result.errors[0].lower()
    assert not runner.calls


def test_swap_refresh_success(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=4_000_000, total=2_000_000, free=1_000_000)
    runner = FakeRunner()
    result = actions.swap_refresh(HelperContext(root=tmp_path, runner=runner), meminfo)
    assert result.ok
    assert ("swapoff", "-a") in runner.calls
    assert ("swapon", "-a") in runner.calls


def test_swap_refresh_swapon_failure(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=4_000_000, total=2_000_000, free=1_000_000)
    runner = FakeRunner()
    runner.argv_responses[("swapon", "-a")] = CommandResult(
        argv=("swapon", "-a"), returncode=1, stderr="gagal"
    )
    result = actions.swap_refresh(HelperContext(root=tmp_path, runner=runner), meminfo)
    assert not result.ok
    assert "re-enabled" in result.errors[0]
    assert runner.calls.count(("swapon", "-a")) == 2


def test_swap_refresh_noop_when_unused(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available=4_000_000, total=2_000_000, free=2_000_000)
    runner = FakeRunner()
    result = actions.swap_refresh(HelperContext(root=tmp_path, runner=runner), meminfo)
    assert result.ok
    assert not runner.calls


def test_apt_remove_blocks_protected(tmp_path: Path) -> None:
    ctx = HelperContext(root=tmp_path, runner=FakeRunner())
    with pytest.raises(validate.ValidationError):
        actions.apt_remove(ctx, "ubuntu-desktop")


def test_apt_remove_requires_installed(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",), returncode=1, stderr="not installed"
    )
    result = actions.apt_remove(HelperContext(root=tmp_path, runner=runner), "gimp")
    assert not result.ok
    assert "not installed" in result.errors[0]


def test_apt_remove_success(tmp_path: Path) -> None:
    runner = FakeRunner()
    result = actions.apt_remove(HelperContext(root=tmp_path, runner=runner), "gimp")
    assert result.ok
    remove_calls = [call for call in runner.calls if call[:3] == ("apt-get", "remove", "-y")]
    assert remove_calls and remove_calls[0][-1] == "gimp" and "--" in remove_calls[0]


def test_docker_prune_parses_reclaimed(tmp_path: Path) -> None:
    runner = FakeRunner(
        responses={
            "docker": CommandResult(
                argv=("docker",),
                returncode=0,
                stdout="Deleted Images:\nuntagged: x\n\nTotal reclaimed space: 1.5GB\n",
            )
        }
    )
    result = actions.docker_prune(HelperContext(root=tmp_path, runner=runner), "image")
    assert result.ok
    assert result.freed_bytes == int(1.5 * 1024**3)
    assert runner.calls[0] == ("docker", "image", "prune", "-f")


def test_helper_main_requires_root(capsys) -> None:
    assert os.geteuid() != 0
    code = helper_main(["clean-apt"])
    assert code == 77
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
