"""Tes mode CLI (tanpa GUI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ubuclean import cli


def make_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "UbuClean" in capsys.readouterr().out


def test_scan_json_isolated(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / ".cache/app/data", "12345")
    code = cli.main(["scan", "--category", "user-cache", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_bytes"] == 5
    assert payload["previews"][0]["category"] == "user-cache"


def test_clean_dry_run_keeps_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = make_file(tmp_path / ".cache/app/data", "1234")
    code = cli.main(["clean", "--category", "user-cache", "--dry-run", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["freed_bytes"] == 4
    assert source.exists()


def test_global_dry_run_flag_before_subcommand(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = make_file(tmp_path / ".cache/app/data", "1234")
    code = cli.main(["--dry-run", "clean", "--category", "user-cache", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert source.exists()


def test_clean_requires_confirmation_without_tty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = make_file(tmp_path / ".cache/app/data", "1234")
    code = cli.main(["clean", "--category", "user-cache"])
    assert code == 2
    assert source.exists()


def test_clean_with_yes_quarantines(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = make_file(tmp_path / ".cache/app/data", "1234")
    code = cli.main(["clean", "--category", "user-cache", "--yes", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["freed_bytes"] == 4
    assert not source.exists()
    assert (tmp_path / ".local/share/ubuclean/quarantine").is_dir()


def test_history_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / ".cache/app/data", "1234")
    cli.main(["clean", "--category", "user-cache", "--yes"])
    capsys.readouterr()
    code = cli.main(["history", "--json"])
    assert code == 0
    entries = json.loads(capsys.readouterr().out)
    assert entries[0]["category"] == "user-cache"
