"""Tes karantina dan pemulihan."""

from __future__ import annotations

import json
from pathlib import Path

from ubuclean.core.quarantine import QuarantineStore


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_quarantine_moves_and_restores(home: Path, quarantine: QuarantineStore) -> None:
    source = make_file(home / ".cache/app/state.log", "hello")
    entry = quarantine.quarantine("user-cache", [source], now=1000.0)
    assert entry is not None
    assert not source.exists()
    assert len(entry.items) == 1
    assert entry.items[0].original == str(source)
    assert entry.items[0].size == 5
    stored = quarantine.root / entry.action_id / entry.items[0].stored
    assert stored.read_text(encoding="utf-8") == "hello"

    result = quarantine.restore(entry.action_id)
    assert result.restored == 1
    assert result.failed == 0
    assert source.read_text(encoding="utf-8") == "hello"
    assert quarantine.get(entry.action_id) is None


def test_quarantine_directory(home: Path, quarantine: QuarantineStore) -> None:
    directory = home / ".cache/bundle"
    make_file(directory / "a", "123")
    make_file(directory / "b", "4567")
    entry = quarantine.quarantine("user-cache", [directory])
    assert entry is not None
    assert entry.total_bytes == 7
    assert not directory.exists()
    quarantine.restore(entry.action_id)
    assert (directory / "a").read_text(encoding="utf-8") == "123"


def test_restore_collision_uses_suffix(home: Path, quarantine: QuarantineStore) -> None:
    source = make_file(home / ".cache/name.txt", "old")
    entry = quarantine.quarantine("user-cache", [source])
    assert entry is not None
    make_file(source, "new")
    result = quarantine.restore(entry.action_id)
    assert result.restored == 1
    assert source.read_text(encoding="utf-8") == "new"
    restored = list((home / ".cache").glob("name.txt (dipulihkan)*"))
    assert len(restored) == 1
    assert restored[0].read_text(encoding="utf-8") == "old"


def test_restore_refuses_protected_original(home: Path, quarantine: QuarantineStore) -> None:
    source = make_file(home / ".cache/x", "x")
    entry = quarantine.quarantine("user-cache", [source])
    assert entry is not None
    meta_path = quarantine.root / entry.action_id / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["items"][0]["original"] = str(home / ".ssh/id_rsa")
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = quarantine.restore(entry.action_id)
    assert result.restored == 0
    assert result.failed == 1
    assert "dilindungi" in result.errors[0].lower()
    assert quarantine.get(entry.action_id) is not None


def test_restore_refuses_outside_home(quarantine: QuarantineStore, tmp_path: Path) -> None:
    source = make_file(quarantine.home / ".cache/x", "x")
    entry = quarantine.quarantine("user-cache", [source])
    assert entry is not None
    meta_path = quarantine.root / entry.action_id / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["items"][0]["original"] = str(tmp_path / "outside")
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = quarantine.restore(entry.action_id)
    assert result.failed == 1
    assert result.restored == 0


def test_quarantine_skips_missing_and_protected(home: Path, quarantine: QuarantineStore) -> None:
    protected = make_file(home / "Documents/secret.txt", "secret")
    assert quarantine.quarantine("user-cache", [home / ".cache/kosong", protected]) is None
    assert protected.exists()


def test_purge_expired(home: Path, quarantine: QuarantineStore) -> None:
    source = make_file(home / ".cache/old", "x")
    entry = quarantine.quarantine("user-cache", [source], now=1_000_000.0)
    assert entry is not None
    assert quarantine.purge_expired(now=1_000_000.0 + 8 * 86400) == [entry.action_id]
    assert quarantine.get(entry.action_id) is None


def test_purge_keeps_recent(home: Path, quarantine: QuarantineStore) -> None:
    source = make_file(home / ".cache/new", "x")
    entry = quarantine.quarantine("user-cache", [source], now=2_000_000.0)
    assert entry is not None
    assert quarantine.purge_expired(now=2_000_000.0 + 86400) == []
    assert quarantine.get(entry.action_id) is not None


def test_restore_dry_run_keeps_files(home: Path, quarantine: QuarantineStore) -> None:
    source = make_file(home / ".cache/keep", "x")
    entry = quarantine.quarantine("user-cache", [source])
    assert entry is not None
    result = quarantine.restore(entry.action_id, dry_run=True)
    assert result.restored == 1
    assert not source.exists()
    assert quarantine.get(entry.action_id) is not None


def test_rejects_path_traversal_action_id(quarantine: QuarantineStore) -> None:
    assert quarantine.get("../../etc") is None
    assert quarantine.restore("../../etc").failed == 1


def test_partial_restore_keeps_metadata(home: Path, quarantine: QuarantineStore) -> None:
    one = make_file(home / ".cache/one", "1")
    two = make_file(home / ".cache/two", "2")
    entry = quarantine.quarantine("user-cache", [one, two])
    assert entry is not None
    action_dir = quarantine.root / entry.action_id
    (action_dir / entry.items[1].stored).unlink()
    result = quarantine.restore(entry.action_id)
    assert result.restored == 1
    assert result.failed == 1
    remaining = quarantine.get(entry.action_id)
    assert remaining is not None
    assert len(remaining.items) == 1


def test_total_bytes(home: Path, quarantine: QuarantineStore) -> None:
    first = make_file(home / ".cache/a", "1234")
    second = make_file(home / ".cache/b", "12")
    quarantine.quarantine("user-cache", [first])
    quarantine.quarantine("user-cache", [second])
    assert quarantine.total_bytes() == 6
    assert quarantine.size_bytes() >= 6
