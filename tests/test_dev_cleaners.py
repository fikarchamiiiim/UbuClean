"""Tes cleaner developer (cache dan folder proyek)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ubuclean.core.cleaners.dev_caches import DevCacheCleaner, cache_targets
from ubuclean.core.cleaners.dev_projects import DevProjectsCleaner, expand_roots
from ubuclean.core.context import Context


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_cache_targets_and_scan(home: Path, context: Context) -> None:
    make_file(home / ".cache/pip/wheels/a", "12345")
    make_file(home / ".npm/_cacache/content/a", "123")
    make_file(home / ".cargo/registry/cache/a", "12")
    cleaner = DevCacheCleaner(home=home, cache_dir=home / ".cache")
    preview = cleaner.scan(context)
    labels = {item.label: item.size for item in preview.items}
    assert labels["pip"] == 5
    assert labels["npm"] == 3
    assert labels["cargo"] == 2
    assert preview.total_bytes == 10
    assert all(item.path for item in preview.items)


def test_cache_clean_quarantines(home: Path, context: Context) -> None:
    source = make_file(home / ".cache/pip/wheels/a", "1234")
    cleaner = DevCacheCleaner(home=home, cache_dir=home / ".cache")
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context, preview)
    assert outcome.quarantined == 1
    assert outcome.freed_bytes == 4
    assert not source.exists()


def test_cache_missing(home: Path, context: Context) -> None:
    cleaner = DevCacheCleaner(home=home, cache_dir=home / ".cache")
    preview = cleaner.scan(context)
    assert preview.items == []
    assert any("Tidak ada" in note for note in preview.notes)


def test_expand_roots(home: Path) -> None:
    target = home / "projects"
    target.mkdir()
    roots = expand_roots(["~/projects", str(target), "relative", ""], home)
    assert roots == [target]


def test_project_markers_old_only(home: Path, context: Context) -> None:
    projects = home / "projects"
    old_project = projects / "old"
    new_project = projects / "new"
    old_marker = old_project / "node_modules"
    new_marker = new_project / "node_modules"
    make_file(old_marker / "pkg/index.js", "12345")
    make_file(new_marker / "pkg/index.js", "123")
    make_file(old_project / "target/debug/app", "123")
    make_file(old_project / "src/main.py", "1234567")
    old_time = time.time() - 200 * 86400
    new_time = time.time() - 2 * 86400
    for path in (old_marker, old_project / "target", old_project, old_project / "src"):
        os.utime(path, (old_time, old_time))
    for path in (new_marker, new_project):
        os.utime(path, (new_time, new_time))
    cleaner = DevProjectsCleaner(home=home, roots=[projects], days=90)
    preview = cleaner.scan(context)
    names = {item.path.name for item in preview.items}
    assert names == {"node_modules", "target"}
    assert preview.total_bytes == 8
    assert any("folder diperiksa" in note for note in preview.notes)


def test_project_cleanup_quarantines(home: Path, context: Context) -> None:
    projects = home / "projects"
    marker = projects / "old/__pycache__"
    make_file(marker / "m.pyc", "1234")
    old_time = time.time() - 200 * 86400
    os.utime(marker, (old_time, old_time))
    os.utime(projects / "old", (old_time, old_time))
    cleaner = DevProjectsCleaner(home=home, roots=[projects], days=90)
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context, preview)
    assert outcome.quarantined == 1
    assert not marker.exists()


def test_missing_roots(home: Path, context: Context) -> None:
    cleaner = DevProjectsCleaner(home=home, roots=[], days=90)
    preview = cleaner.scan(context)
    assert preview.items == []
    assert any("Belum ada folder induk" in note for note in preview.notes)


def test_scan_does_not_enter_markers(home: Path, context: Context) -> None:
    projects = home / "projects"
    marker = projects / "p/node_modules"
    make_file(marker / "nested/other/node_modules/x", "123")
    old_time = time.time() - 200 * 86400
    os.utime(marker, (old_time, old_time))
    os.utime(projects / "p", (old_time, old_time))
    cleaner = DevProjectsCleaner(home=home, roots=[projects], days=90)
    preview = cleaner.scan(context)
    assert len(preview.items) == 1
    assert preview.items[0].path == marker
