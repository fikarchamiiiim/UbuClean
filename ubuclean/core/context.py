"""Shared context injected into the cleaners and core services."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ubuclean.config import Config
from ubuclean.core.journal import HistoryLog
from ubuclean.core.paths import AppPaths, SafetyPolicy
from ubuclean.core.quarantine import QuarantineStore
from ubuclean.core.runner import PrivilegedRunner, Runner, SubprocessRunner


@dataclass(frozen=True)
class Context:
    """All core dependencies in one object that is easy to replace in tests."""

    paths: AppPaths
    config: Config
    runner: Runner
    privileged: PrivilegedRunner
    policy: SafetyPolicy
    quarantine: QuarantineStore
    history: HistoryLog
    dry_run: bool = False

    @classmethod
    def build(
        cls,
        *,
        paths: AppPaths | None = None,
        config: Config | None = None,
        runner: Runner | None = None,
        dry_run: bool = False,
        helper_path: str | None = None,
    ) -> "Context":
        """Build the context for the current user or the given paths."""
        paths = paths or AppPaths.default()
        config = config or Config.load(paths.config_file)
        runner = runner or SubprocessRunner()
        policy = SafetyPolicy.for_home(paths.home)
        quarantine = QuarantineStore(
            paths.quarantine_dir,
            paths.home,
            retention_days=int(config.get("quarantine_days", 7)),
        )
        privileged = PrivilegedRunner(runner, helper_path, dry_run=dry_run)
        history = HistoryLog(paths.log_file)
        return cls(
            paths=paths,
            config=config,
            runner=runner,
            privileged=privileged,
            policy=policy,
            quarantine=quarantine,
            history=history,
            dry_run=dry_run,
        )

    def with_dry_run(self, dry_run: bool) -> "Context":
        """A copy of the context with a consistent dry-run mode."""
        if dry_run == self.dry_run:
            return self
        privileged = PrivilegedRunner(
            self.runner,
            self.privileged.helper_path,
            dry_run=dry_run,
        )
        return replace(self, dry_run=dry_run, privileged=privileged)
