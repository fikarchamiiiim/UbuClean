"""Subprocess wrapper with argv lists and timeouts.

``shell=True`` is never used anywhere in the project. Every command runs as an
argument list through :class:`SubprocessRunner` and can be replaced with
:class:`FakeRunner` during testing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    """Result of executing a single command."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """True when the command finished with exit code 0."""
        return self.returncode == 0 and not self.timed_out


class Runner(Protocol):
    """Runner interface that can be swapped out in tests."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run argv and return the result."""

    def which(self, name: str) -> str | None:
        """Look up an executable on PATH."""


class SubprocessRunner:
    """The real runner, based on :func:`subprocess.run`."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run ``argv`` without a shell and capture stdout/stderr."""
        args = [str(part) for part in argv]
        if not args:
            raise ValueError("argv must not be empty")
        started = time.monotonic()
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
                env=run_env,
            )
            return CommandResult(
                argv=tuple(args),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration=time.monotonic() - started,
            )
        except FileNotFoundError:
            return CommandResult(
                argv=tuple(args),
                returncode=127,
                stderr=f"Command not found: {args[0]}",
                duration=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CommandResult(
                argv=tuple(args),
                returncode=-1,
                stdout=stdout,
                stderr=stderr or f"Timed out after {timeout:.0f} seconds",
                duration=time.monotonic() - started,
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(
                argv=tuple(args),
                returncode=126,
                stderr=str(exc),
                duration=time.monotonic() - started,
            )

    def which(self, name: str) -> str | None:
        """Look up an executable on the system PATH."""
        return shutil.which(name)


@dataclass
class FakeRunner:
    """Fake runner for tests: records argv and returns fixed responses."""

    responses: dict[str, CommandResult] = field(default_factory=dict)
    argv_responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    default_returncode: int = 0
    default_stdout: str = ""
    calls: list[tuple[str, ...]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Record the call and return the prepared response."""
        args = tuple(str(part) for part in argv)
        self.calls.append(args)
        self.timeouts.append(timeout)
        if args in self.argv_responses:
            return self.argv_responses[args]
        if args and args[0] in self.responses:
            return self.responses[args[0]]
        return CommandResult(
            argv=args,
            returncode=self.default_returncode,
            stdout=self.default_stdout,
        )

    def which(self, name: str) -> str | None:
        """Return the fake path when one has been registered."""
        return self.paths.get(name)

    @property
    def last_argv(self) -> tuple[str, ...]:
        """The last arguments that were run."""
        return self.calls[-1] if self.calls else ()


class PrivilegedError(RuntimeError):
    """A general failure while calling the helper through pkexec."""

    def __init__(self, message: str, result: CommandResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class AuthCancelled(PrivilegedError):
    """The user closed the polkit authentication dialog."""


class AuthDenied(PrivilegedError):
    """Polkit denied the authentication request."""


class PrivilegedFailure(PrivilegedError):
    """The helper ran but returned a failure code."""


def default_helper_path() -> str:
    """Helper location: env override, dev sibling, then the installed location."""
    override = os.environ.get("UBUCLEAN_HELPER")
    if override:
        return override
    sibling = Path(__file__).resolve().parents[2] / "bin" / "ubuclean-helper"
    if sibling.is_file():
        return str(sibling)
    return "/usr/lib/ubuclean/bin/ubuclean-helper"


class PrivilegedRunner:
    """Root helper caller through ``pkexec`` with an argument whitelist."""

    def __init__(
        self,
        runner: Runner,
        helper_path: str | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.runner = runner
        self.helper_path = helper_path or default_helper_path()
        self.dry_run = dry_run

    def describe(self, args: Sequence[str]) -> tuple[str, ...]:
        """The full arguments that will be run (for preview/dry-run)."""
        return ("pkexec", self.helper_path, *(str(arg) for arg in args))

    def run(self, args: Sequence[str], *, timeout: float = 300.0) -> CommandResult:
        """Run one helper action, mapping the polkit exit codes."""
        argv = self.describe(args)
        if self.dry_run:
            return CommandResult(argv=argv, returncode=0, stdout="dry-run")
        result = self.runner.run(argv, timeout=timeout)
        if result.returncode == 126:
            raise AuthCancelled("Authentication cancelled.", result)
        if result.returncode == 127:
            raise AuthDenied("Authorisation denied by polkit.", result)
        if result.timed_out:
            raise PrivilegedFailure("The system command timed out.", result)
        if result.returncode != 0:
            message = result.stderr.strip() or f"Helper failed (code {result.returncode})."
            raise PrivilegedFailure(message, result)
        return result


def prune_environment() -> dict[str, str]:
    """Minimal environment for the helper (avoids a foreign PYTHONPATH)."""
    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
