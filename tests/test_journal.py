"""Tes log riwayat JSONL."""

from __future__ import annotations

import os
from pathlib import Path

from ubuclean.core.journal import HistoryEntry, HistoryLog


def test_record_and_read(history: HistoryLog) -> None:
    history.record(HistoryEntry(action="clean", category="apt-cache", label="Cache apt", items=2, bytes=100))
    history.record(HistoryEntry(action="clean", category="trash", label="Tong sampah", items=1, bytes=50))
    entries = history.entries()
    assert len(entries) == 2
    assert entries[0].category == "trash"
    assert entries[1].category == "apt-cache"
    assert entries[0].to_dict()["bytes"] == 50


def test_log_permissions(history: HistoryLog) -> None:
    history.record(HistoryEntry(action="clean", category="tmp", label="Tmp"))
    assert oct(history.path.stat().st_mode & 0o777) == "0o600"
    assert oct(history.path.parent.stat().st_mode & 0o777) == "0o700"


def test_ignores_malformed_lines(history: HistoryLog) -> None:
    history.record(HistoryEntry(action="clean", category="tmp", label="Tmp"))
    with history.path.open("a", encoding="utf-8") as handle:
        handle.write("bukan json\n")
        handle.write("[1,2,3]\n")
        handle.write('{"action": "x"}\n')
    entries = history.entries()
    assert len(entries) == 2
    assert entries[0].action == "x"


def test_limit_and_clear(history: HistoryLog) -> None:
    for index in range(5):
        history.record(HistoryEntry(action="clean", category=f"c{index}", label="x"))
    assert len(history.entries(limit=2)) == 2
    history.clear()
    assert history.entries() == []


def test_missing_log_returns_empty(history: HistoryLog) -> None:
    assert not history.path.exists()
    assert history.entries() == []


def test_unicode_preserved(history: HistoryLog) -> None:
    history.record(
        HistoryEntry(
            action="clean",
            category="user-cache",
            label="Cache aplikasi — 2,4 GB",
            detail="Berhasil dibersihkan",
        )
    )
    entry = history.entries()[0]
    assert "—" in entry.label
    assert entry.detail == "Berhasil dibersihkan"
