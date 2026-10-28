"""Fixture pytest bersama untuk tes UbuClean."""

from __future__ import annotations

from pathlib import Path

import pytest

from ubuclean.config import Config
from ubuclean.core.context import Context
from ubuclean.core.paths import AppPaths, SafetyPolicy
from ubuclean.core.quarantine import QuarantineStore
from ubuclean.core.runner import FakeRunner
from ubuclean.core.journal import HistoryLog


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Home pengguna tiruan yang terisolasi."""
    directory = tmp_path / "home"
    directory.mkdir()
    for name in (".cache", ".config", ".local/share/Trash"):
        (directory / name).mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture
def policy(home: Path) -> SafetyPolicy:
    """Kebijakan keamanan untuk home tiruan."""
    return SafetyPolicy.for_home(home)


@pytest.fixture
def app_paths(home: Path) -> AppPaths:
    """Himpunan path aplikasi untuk home tiruan."""
    return AppPaths.for_home(home)


@pytest.fixture
def quarantine(app_paths: AppPaths) -> QuarantineStore:
    """Karantina UbuClean di home tiruan."""
    return QuarantineStore(app_paths.quarantine_dir, app_paths.home, retention_days=7)


@pytest.fixture
def history(app_paths: AppPaths) -> HistoryLog:
    """Log riwayat di home tiruan."""
    return HistoryLog(app_paths.log_file)


@pytest.fixture
def fake_runner() -> FakeRunner:
    """Runner palsu tanpa menyentuh sistem."""
    return FakeRunner()


@pytest.fixture
def context(app_paths: AppPaths, fake_runner: FakeRunner) -> Context:
    """Konteks core terisolasi dengan runner palsu."""
    config = Config({}, path=app_paths.config_file)
    return Context.build(
        paths=app_paths,
        config=config,
        runner=fake_runner,
        helper_path="/usr/lib/ubuclean/bin/ubuclean-helper",
    )
