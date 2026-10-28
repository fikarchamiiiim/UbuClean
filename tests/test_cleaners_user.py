"""Tes cleaner user-level dan pipeline scan/clean."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ubuclean.core.cleaners.browser import BrowserCacheCleaner
from ubuclean.core.cleaners.tmp import TmpCleaner
from ubuclean.core.cleaners.trash import TrashCleaner
from ubuclean.core.cleaners.user_cache import UserCacheCleaner
from ubuclean.core.context import Context
from ubuclean.core.models import Category, CleanOutcome, Preview
from ubuclean.core.registry import get_category
from ubuclean.core.runner import CommandResult
from ubuclean.core.scan import CleanAssistant, PreviewRequired


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_user_cache_scan_excludes_other_categories(context: Context) -> None:
    cache = context.paths.cache_dir
    make_file(cache / "app/data.bin", "12345")
    make_file(cache / "google-chrome/Cache/x", "abc")
    make_file(cache / "pip/wheels/x", "abcd")
    make_file(cache / "mozilla/firefox/prof/cache2/x", "ab")
    make_file(cache / "yarn/v6/x", "abc")
    preview = UserCacheCleaner().scan(context)
    paths = {item.path for item in preview.items}
    assert cache / "app" in paths
    assert cache / "google-chrome" not in paths
    assert cache / "pip" not in paths
    assert cache / "mozilla" not in paths
    assert cache / "yarn" not in paths
    assert preview.total_bytes == 5


def test_user_cache_items_sorted_by_size(context: Context) -> None:
    make_file(context.paths.cache_dir / "small/x", "1")
    make_file(context.paths.cache_dir / "big/x", "1" * 100)
    preview = UserCacheCleaner().scan(context)
    assert preview.items[0].path.name == "big"


def test_user_cache_missing_directory(context: Context) -> None:
    context.paths.cache_dir.rmdir()
    preview = UserCacheCleaner().scan(context)
    assert preview.items == []
    assert preview.notes


def test_clean_dry_run_keeps_files(context: Context) -> None:
    source = make_file(context.paths.cache_dir / "app/data", "1234")
    assistant = CleanAssistant(context)
    previews = assistant.scan(["user-cache"])
    report = assistant.clean(previews, dry_run=True)
    assert source.exists()
    assert report.dry_run is True
    assert report.freed_bytes == 4
    assert report.outcomes[0].quarantine_id is None


def test_clean_quarantines_and_logs(context: Context) -> None:
    source = make_file(context.paths.cache_dir / "app/data", "1234")
    assistant = CleanAssistant(context)
    previews = assistant.scan(["user-cache"])
    report = assistant.clean(previews)
    assert not source.exists()
    assert report.freed_bytes == 4
    outcome = report.outcomes[0]
    assert outcome.quarantined == 1
    assert outcome.quarantined_bytes == 4
    assert outcome.immediate_bytes == 0
    assert outcome.quarantine_id is not None
    entries = context.history.entries()
    assert len(entries) == 1
    assert entries[0].quarantine_id == outcome.quarantine_id
    assert entries[0].bytes == 4


def test_clean_requires_matching_preview(context: Context) -> None:
    assistant = CleanAssistant(context)
    fake = Preview(category_id="user-cache", items=[])
    with pytest.raises(PreviewRequired):
        assistant.clean([fake])


def test_unsupported_category(context: Context) -> None:
    assistant = CleanAssistant(context, cleaner_factory=lambda category_id: None)
    previews = assistant.scan(["user-cache"])
    assert previews["user-cache"].supported is False
    report = assistant.clean(previews)
    assert report.outcomes[0].failed == 1


def test_scan_failure_isolated(context: Context) -> None:
    class Boom:
        category = get_category("user-cache")

        def scan(self, ctx: Context) -> Preview:
            raise RuntimeError("boom")

        def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
            raise AssertionError("tidak dipanggil")

    assistant = CleanAssistant(context, cleaner_factory=lambda cid: Boom() if cid == "user-cache" else None)
    previews = assistant.scan(["user-cache", "kernels"])
    assert previews["user-cache"].supported is False
    assert "boom" in previews["user-cache"].errors[0]
    assert previews["kernels"].supported is False


def test_browser_cleaner_only_known_caches(context: Context) -> None:
    cache = context.paths.cache_dir
    make_file(cache / "google-chrome/Cache/a", "123")
    make_file(cache / "mozilla/firefox/prof/cache2/b", "12")
    make_file(cache / "tidak-dikenal/x", "1")
    preview = BrowserCacheCleaner().scan(context)
    labels = {item.label for item in preview.items}
    assert labels == {"Google Chrome", "Firefox"}
    assert len(preview.items) == 2


def test_browser_clean_quarantines(context: Context) -> None:
    source = make_file(context.paths.cache_dir / "google-chrome/Cache/a", "123")
    cleaner = BrowserCacheCleaner()
    preview = cleaner.scan(context)
    outcome = cleaner.clean(context, preview)
    assert outcome.quarantined == 1
    assert not source.exists()


def test_trash_scan_and_clean(context: Context) -> None:
    trash_root = context.paths.home / ".local/share/Trash"
    source = make_file(trash_root / "files/foto.jpg", "abc")
    original = context.paths.home / "Downloads/foto.jpg"
    info = make_file(
        trash_root / "info/foto.jpg.trashinfo",
        f"[Trash Info]\nPath={original}\nDeletionDate=2026-10-27T10:00:00\n",
    )
    cleaner = TrashCleaner()
    preview = cleaner.scan(context)
    assert preview.total_bytes == 3
    outcome = cleaner.clean(context, preview)
    assert outcome.quarantined == 1
    assert not source.exists()
    assert not info.exists()
    entry = context.quarantine.list_actions()[0]
    assert entry.items[0].original == str(original)
    restored = context.quarantine.restore(entry.action_id)
    assert restored.restored == 1
    assert original.read_text(encoding="utf-8") == "abc"


def test_trash_empty(context: Context) -> None:
    preview = TrashCleaner().scan(context)
    assert preview.items == []
    assert preview.notes


def test_tmp_scan_filters_old_and_in_use(context: Context, fake_runner) -> None:
    tmp = context.paths.home / "fake-tmp"
    tmp.mkdir()
    old = make_file(tmp / "old.log", "12345")
    recent = make_file(tmp / "recent.log", "1")
    skip = tmp / ".X11-unix"
    skip.mkdir()
    now = time.time()
    os.utime(old, (now - 10 * 86400, now - 10 * 86400))
    os.utime(skip, (now - 10 * 86400, now - 10 * 86400))
    fake_runner.paths["fuser"] = "/usr/bin/fuser"
    fake_runner.responses["fuser"] = CommandResult(argv=("fuser",), returncode=0)
    preview = TmpCleaner(tmp_dir=tmp).scan(context)
    assert preview.items == []
    assert any("dipakai" in note for note in preview.notes)

    fake_runner.responses["fuser"] = CommandResult(argv=("fuser",), returncode=1)
    preview = TmpCleaner(tmp_dir=tmp).scan(context)
    assert [item.path for item in preview.items] == [old]
    assert recent.exists()


def test_tmp_clean_calls_helper(context: Context, fake_runner) -> None:
    payload = {"ok": True, "freed_bytes": 999, "removed": 3, "errors": []}
    fake_runner.responses["pkexec"] = CommandResult(
        argv=("pkexec",),
        returncode=0,
        stdout=json.dumps(payload),
    )
    preview = Preview(category_id="tmp", token="abc")
    # daftarkan token lewat scan agar lolos validasi
    assistant = CleanAssistant(context)
    assistant._tokens["tmp"] = "abc"  # type: ignore[attr-defined]
    outcome = assistant._clean_one(context, preview, dry_run=False)  # type: ignore[attr-defined]
    assert outcome.freed_bytes == 999
    assert outcome.removed == 3
    call = fake_runner.calls[0]
    assert call == ("pkexec", "/usr/lib/ubuclean/bin/ubuclean-helper", "clean-tmp", "7")
    assert fake_runner.timeouts[0] == 300.0


def test_tmp_clean_auth_cancelled(context: Context, fake_runner) -> None:
    fake_runner.responses["pkexec"] = CommandResult(argv=("pkexec",), returncode=126)
    cleaner = TmpCleaner()
    preview = Preview(category_id="tmp", token="x")
    outcome = cleaner.clean(context, preview)
    assert outcome.failed == 1
    assert "dibatalkan" in outcome.errors[0].lower()


def test_clean_report_aggregates(context: Context) -> None:
    make_file(context.paths.cache_dir / "a/x", "123")
    make_file(context.paths.home / ".local/share/Trash/files/y", "12")
    assistant = CleanAssistant(context)
    previews = assistant.scan(["user-cache", "trash"])
    report = assistant.clean(previews, dry_run=True)
    assert report.freed_bytes == 5
    assert report.failures == 0
    assert len(report.outcomes) == 2
    assert all(entry.dry_run for entry in context.history.entries())
