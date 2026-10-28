"""Tes cleaner kategori sistem dengan runner palsu."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

import pytest

from ubuclean.core.cleaners.apt import AptAutoremoveCleaner, AptCacheCleaner, parse_autoremove
from ubuclean.core.cleaners.crash import CrashCleaner
from ubuclean.core.cleaners.developer import DockerCleaner
from ubuclean.core.cleaners.flatpak import FlatpakCleaner
from ubuclean.core.cleaners.journald import JournalCleaner
from ubuclean.core.cleaners.kernels import KernelCleaner, version_sort_key
from ubuclean.core.cleaners.snap import SnapCleaner
from ubuclean.core.context import Context
from ubuclean.core.runner import CommandResult


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_autoremove() -> None:
    output = (
        "The following packages will be REMOVED:\n"
        "  libslirp0 slirp4netns\n"
        "0 upgraded, 0 newly installed, 2 to remove and 0 not upgraded.\n"
        "After this operation, 1,024 kB disk space will be freed.\n"
    )
    packages, freed = parse_autoremove(output)
    assert packages == ["libslirp0", "slirp4netns"]
    assert freed == 1024 * 1024


def test_parse_autoremove_without_size() -> None:
    packages, freed = parse_autoremove(
        "The following packages will be REMOVED:\n  liba\n0 upgraded, 0 newly installed\n"
    )
    assert packages == ["liba"]
    assert freed == 0


def test_apt_cache_scan(context: Context, tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    make_file(archives / "a.deb", "12345")
    make_file(archives / "partial/b.deb", "123")
    make_file(archives / "notes.txt", "ignored")
    preview = AptCacheCleaner(archives=archives).scan(context)
    assert preview.item_count == 2
    assert preview.total_bytes == 8


def test_apt_cache_dry_run(context: Context, tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    make_file(archives / "a.deb", "12345")
    cleaner = AptCacheCleaner(archives=archives)
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context.with_dry_run(True), preview)
    assert outcome.freed_bytes == 5
    assert (archives / "a.deb").exists()


def test_autoremove_scan(context: Context, fake_runner) -> None:
    fake_runner.responses["apt-get"] = CommandResult(
        argv=("apt-get",),
        returncode=0,
        stdout=(
            "The following packages will be REMOVED:\n"
            "  liba libb\n"
            "After this operation, 2,048 kB disk space will be freed.\n"
        ),
    )
    preview = AptAutoremoveCleaner().scan(context)
    assert preview.item_count == 2
    assert preview.total_bytes == 2048 * 1024
    assert preview.commands


def test_autoremove_scan_failure(context: Context, fake_runner) -> None:
    fake_runner.responses["apt-get"] = CommandResult(
        argv=("apt-get",), returncode=100, stderr="E: lock"
    )
    preview = AptAutoremoveCleaner().scan(context)
    assert preview.supported is False
    assert "lock" in preview.errors[0]


def kernel_output(releases: list[str], size_kb: int = 1000) -> str:
    lines: list[str] = []
    for release in releases:
        lines.append(f"linux-image-{release}\t{size_kb}\tinstall ok installed")
        lines.append(f"linux-headers-{release}\t100\tinstall ok installed")
    return "\n".join(lines) + "\n"


def test_kernel_scan_keeps_running_and_newest(context: Context, fake_runner) -> None:
    running = platform.release()
    fake_runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",),
        returncode=0,
        stdout=kernel_output([running, "6.9.0-1-generic", "6.8.0-40-generic"]),
    )
    preview = KernelCleaner().scan(context)
    protected = {item.id for item in preview.items if item.protected}
    candidates = {item.id for item in preview.items if not item.protected}
    assert f"linux-image-{running}" in protected
    assert "linux-image-6.9.0-1-generic" in protected
    assert candidates == {
        "linux-image-6.8.0-40-generic",
        "linux-headers-6.8.0-40-generic",
    }
    assert any("Running kernel" in note for note in preview.notes)
    assert preview.commands


def test_kernel_scan_no_candidates(context: Context, fake_runner) -> None:
    running = platform.release()
    fake_runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",),
        returncode=0,
        stdout=kernel_output([running, "6.9.0-1-generic"]),
    )
    preview = KernelCleaner().scan(context)
    assert not [item for item in preview.items if not item.protected]
    assert any("No old kernels" in note for note in preview.notes)
    assert preview.commands == []


def test_kernel_clean_passes_only_candidates(context: Context, fake_runner) -> None:
    running = platform.release()
    fake_runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",),
        returncode=0,
        stdout=kernel_output([running, "6.9.0-1-generic", "6.8.0-40-generic"]),
    )
    fake_runner.responses["pkexec"] = CommandResult(
        argv=("pkexec",),
        returncode=0,
        stdout=json.dumps({"ok": True, "freed_bytes": 1000, "removed": 2}),
    )
    cleaner = KernelCleaner()
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context, preview)
    assert outcome.freed_bytes == 1000
    call = fake_runner.calls[-1]
    assert call[:3] == ("pkexec", "/usr/lib/ubuclean/bin/ubuclean-helper", "purge-kernels")
    assert "linux-image-6.8.0-40-generic" in call
    assert f"linux-image-{running}" not in call


def test_kernel_dry_run(context: Context, fake_runner) -> None:
    running = platform.release()
    fake_runner.responses["dpkg-query"] = CommandResult(
        argv=("dpkg-query",),
        returncode=0,
        stdout=kernel_output([running, "6.9.0-1-generic", "6.8.0-40-generic"]),
    )
    cleaner = KernelCleaner()
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context.with_dry_run(True), preview)
    assert outcome.freed_bytes == 1126400
    assert outcome.removed == 2
    assert not [call for call in fake_runner.calls if call[0] == "pkexec"]


def test_version_sort_key() -> None:
    releases = ["6.8.0-31-generic", "6.10.0-1-generic", "6.8.0-9-generic"]
    assert sorted(releases, key=version_sort_key, reverse=True) == [
        "6.10.0-1-generic",
        "6.8.0-31-generic",
        "6.8.0-9-generic",
    ]


SNAP_LIST = """Name      Version  Rev   Tracking       Publisher   Notes
firefox   128.0    4530  latest/stable  mozilla**   -
firefox   127.0    4472  latest/stable  mozilla**   disabled
core20    2024     2318  latest/stable  canonical** base,disabled
"""


def test_snap_scan_only_disabled(context: Context, fake_runner) -> None:
    fake_runner.paths["snap"] = "/usr/bin/snap"
    fake_runner.responses["snap"] = CommandResult(
        argv=("snap",), returncode=0, stdout=SNAP_LIST
    )
    preview = SnapCleaner().scan(context)
    assert [item.id for item in preview.items] == ["firefox:4472", "core20:2318"]
    assert preview.commands


def test_snap_scan_missing(context: Context) -> None:
    preview = SnapCleaner().scan(context)
    assert preview.supported is False


def test_snap_clean_dry_run(context: Context, fake_runner) -> None:
    fake_runner.paths["snap"] = "/usr/bin/snap"
    fake_runner.responses["snap"] = CommandResult(argv=("snap",), returncode=0, stdout=SNAP_LIST)
    cleaner = SnapCleaner()
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context.with_dry_run(True), preview)
    assert outcome.removed == 2
    assert not [call for call in fake_runner.calls if call[0] == "pkexec"]


def test_flatpak_scan_unused_runtime(context: Context, fake_runner) -> None:
    fake_runner.paths["flatpak"] = "/usr/bin/flatpak"
    fake_runner.argv_responses[
        ("flatpak", "list", "--runtime", "--columns=ref,size")
    ] = CommandResult(
        argv=("flatpak", "list", "--runtime", "--columns=ref,size"),
        returncode=0,
        stdout=(
            "org.gnome.Platform/x86_64/46\t1.2 GB\n"
            "org.freedesktop.Platform/x86_64/23.08\t800 MB\n"
        ),
    )
    fake_runner.argv_responses[
        ("flatpak", "list", "--app", "--columns=runtime")
    ] = CommandResult(
        argv=("flatpak", "list", "--app", "--columns=runtime"),
        returncode=0,
        stdout="org.gnome.Platform/x86_64/46\n",
    )
    preview = FlatpakCleaner().scan(context)
    assert [item.id for item in preview.items] == ["org.freedesktop.Platform/x86_64/23.08"]
    assert preview.total_bytes == 800 * 1024**2


def test_journal_scan_and_dry_run(context: Context, fake_runner) -> None:
    fake_runner.paths["journalctl"] = "/usr/bin/journalctl"
    fake_runner.responses["journalctl"] = CommandResult(
        argv=("journalctl",),
        returncode=0,
        stdout="Archived and active journals take up 771.6M in the file system.\n",
    )
    cleaner = JournalCleaner()
    preview = cleaner.scan(context)
    assert preview.total_bytes == int(771.6 * 1024**2)
    outcome = cleaner.clean(context.with_dry_run(True), preview)
    assert outcome.detail.startswith("Preview")
    assert outcome.removed == 1


def test_crash_scan(tmp_path: Path, context: Context) -> None:
    crash_dir = tmp_path / "crash"
    make_file(crash_dir / "a.crash", "1234")
    make_file(crash_dir / "b.crash", "12")
    preview = CrashCleaner(crash_dir=crash_dir).scan(context)
    assert preview.total_bytes == 6
    assert preview.item_count == 2


def test_crash_scan_missing_dir(tmp_path: Path, context: Context) -> None:
    preview = CrashCleaner(crash_dir=tmp_path / "tidak-ada").scan(context)
    assert preview.items == []
    assert preview.notes


def test_docker_scan(context: Context, fake_runner) -> None:
    fake_runner.paths["docker"] = "/usr/bin/docker"
    fake_runner.argv_responses[("docker", "info", "--format", "{{.ServerVersion}}")] = CommandResult(
        argv=("docker",), returncode=0, stdout="29.5.3"
    )
    fake_runner.argv_responses[("docker", "system", "df", "--format", "{{json .}}")] = CommandResult(
        argv=("docker",),
        returncode=0,
        stdout=(
            '{"Type":"Images","TotalCount":"15","Active":"7","Size":"8.7GB","Reclaimable":"4.298GB (49%)"}\n'
            '{"Type":"Containers","TotalCount":"7","Active":"5","Size":"495.5MB","Reclaimable":"483.6MB (97%)"}\n'
            '{"Type":"Local Volumes","TotalCount":"206","Active":"7","Size":"751.2MB","Reclaimable":"0B"}\n'
        ),
    )
    preview = DockerCleaner().scan(context)
    assert {item.id for item in preview.items} == {"image", "container"}
    assert preview.total_bytes > 4 * 1024**3
    assert len(preview.commands) == 2


def test_docker_scan_inaccessible(context: Context, fake_runner) -> None:
    fake_runner.paths["docker"] = "/usr/bin/docker"
    fake_runner.argv_responses[("docker", "info", "--format", "{{.ServerVersion}}")] = CommandResult(
        argv=("docker",), returncode=1, stderr="permission denied"
    )
    preview = DockerCleaner().scan(context)
    assert preview.supported is False
    assert any("docker group" in note for note in preview.notes)


def test_docker_clean_dry_run(context: Context, fake_runner) -> None:
    fake_runner.paths["docker"] = "/usr/bin/docker"
    fake_runner.argv_responses[("docker", "info", "--format", "{{.ServerVersion}}")] = CommandResult(
        argv=("docker",), returncode=0, stdout="29.5.3"
    )
    fake_runner.argv_responses[("docker", "system", "df", "--format", "{{json .}}")] = CommandResult(
        argv=("docker",),
        returncode=0,
        stdout='{"Type":"Images","TotalCount":"1","Active":"0","Size":"1GB","Reclaimable":"1GB"}\n',
    )
    cleaner = DockerCleaner()
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context.with_dry_run(True), preview)
    assert outcome.removed == 1
    assert outcome.freed_bytes == 1024**3
    assert not fake_runner.calls[2:]
