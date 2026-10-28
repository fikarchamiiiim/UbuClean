"""Base class and utilities for all cleaners."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from ubuclean.core.context import Context
from ubuclean.core.models import Category, CleanOutcome, Preview, PreviewItem
from ubuclean.core.paths import path_size, safe_remove_many
from ubuclean.core.runner import CommandResult, PrivilegedError


class CommandFailed(RuntimeError):
    """A system command returned a failure code during scan."""


class BaseCleaner(ABC):
    """Cleaner contract: scan must be read-only, clean only acts on a preview."""

    category: Category

    @abstractmethod
    def scan(self, ctx: Context) -> Preview:
        """Scan the category and return a preview without changing the system."""

    @abstractmethod
    def clean(self, ctx: Context, preview: Preview) -> CleanOutcome:
        """Clean the items from a preview that has been confirmed."""

    # -- common utilities ----------------------------------------------
    def new_preview(
        self,
        *,
        items: Iterable[PreviewItem] = (),
        notes: Iterable[str] = (),
        errors: Iterable[str] = (),
        commands: Iterable[Iterable[str]] = (),
        supported: bool = True,
        estimated_bytes: int | None = None,
    ) -> Preview:
        """Build a preview with this category's metadata."""
        return Preview(
            category_id=self.category.id,
            items=list(items),
            notes=list(notes),
            errors=list(errors),
            commands=[tuple(command) for command in commands],
            supported=supported,
            estimated_bytes=estimated_bytes,
        )

    def item(self, path: Path, *, label: str | None = None, detail: str = "") -> PreviewItem:
        """Create a preview item from a path."""
        return PreviewItem(
            id=str(path),
            label=label or str(path),
            size=path_size(path),
            detail=detail,
            path=path,
        )

    def remove_paths(
        self,
        ctx: Context,
        paths: Iterable[Path],
        *,
        direct: bool = False,
    ) -> CleanOutcome:
        """Delete/quarantine paths according to the category settings."""
        outcome = CleanOutcome(category_id=self.category.id)
        candidates = [Path(path) for path in paths]
        if not candidates:
            return outcome
        if ctx.dry_run:
            outcome.freed_bytes = sum(path_size(path) for path in candidates)
            outcome.removed = len(candidates)
            outcome.detail = "Preview: nothing was deleted."
            return outcome
        if self.category.quarantine and not direct:
            entry = ctx.quarantine.quarantine(self.category.id, candidates)
            if entry is None:
                outcome.failed = len(candidates)
                outcome.errors.append("No items were quarantined successfully.")
                return outcome
            outcome.quarantined = len(entry.items)
            outcome.quarantined_bytes = entry.total_bytes
            outcome.freed_bytes = entry.total_bytes
            outcome.removed = len(entry.items)
            outcome.quarantine_id = entry.action_id
            outcome.failed = len(entry.errors)
            outcome.errors.extend(entry.errors)
            return outcome
        results = safe_remove_many(candidates, ctx.policy)
        outcome.removed = sum(1 for result in results if result.removed)
        outcome.freed_bytes = sum(result.size for result in results if result.removed)
        failures = [result for result in results if result.error]
        outcome.failed = len(failures)
        outcome.errors.extend(result.error for result in failures if result.error)
        return outcome

    def run_helper(
        self,
        ctx: Context,
        args: Iterable[str],
        *,
        timeout: float = 300.0,
    ) -> CommandResult:
        """Run a helper action and raise ``PrivilegedError`` on failure."""
        return ctx.privileged.run(list(args), timeout=timeout)

    def helper_json(
        self,
        ctx: Context,
        args: Iterable[str],
        *,
        timeout: float = 300.0,
    ) -> dict[str, object]:
        """Run the helper and parse its JSON output (tolerant of empty output)."""
        result = self.run_helper(ctx, args, timeout=timeout)
        for line in reversed(result.stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def helper_clean(
        self,
        ctx: Context,
        args: Iterable[str],
        *,
        timeout: float = 300.0,
    ) -> CleanOutcome:
        """Run a helper action and turn its result into a category outcome."""
        try:
            payload = self.helper_json(ctx, args, timeout=timeout)
        except PrivilegedError as exc:
            return CleanOutcome(category_id=self.category.id, failed=1, errors=[str(exc)])
        if not payload:
            return CleanOutcome(
                category_id=self.category.id,
                failed=1,
                errors=["The helper did not return readable output."],
            )
        return helper_outcome(self.category, payload)

    @staticmethod
    def failure(error: Exception | str) -> CleanOutcome:
        """Build a failed outcome from an exception/message."""
        return CleanOutcome(
            category_id="",
            failed=1,
            errors=[str(error)],
        )


def command_output(ctx: Context, argv: list[str], *, timeout: float = 30.0) -> str:
    """Run a read-only command and return stdout; on error → empty string."""
    result = ctx.runner.run(argv, timeout=timeout)
    if result.returncode != 0:
        return ""
    return result.stdout


def command_or_error(ctx: Context, argv: list[str], *, timeout: float = 30.0) -> str:
    """Run a read-only command; raise ``CommandFailed`` with stderr."""
    result = ctx.runner.run(argv, timeout=timeout)
    if result.returncode != 0:
        message = result.stderr.strip() or f"{argv[0]} failed ({result.returncode})"
        raise CommandFailed(message)
    return result.stdout


def helper_outcome(
    category: Category,
    payload: dict[str, object],
    *,
    errors: Iterable[str] = (),
) -> CleanOutcome:
    """Convert helper JSON into :class:`CleanOutcome`."""
    error_list = [str(err) for err in errors]
    helper_errors = payload.get("errors", [])
    if isinstance(helper_errors, list):
        error_list.extend(str(err) for err in helper_errors)
    freed = payload.get("freed_bytes", 0)
    try:
        freed_bytes = int(freed) if freed is not None else 0
    except (TypeError, ValueError):
        freed_bytes = 0
    removed = payload.get("removed", 0)
    try:
        removed_count = int(removed) if removed is not None else 0
    except (TypeError, ValueError):
        removed_count = 0
    detail = str(payload.get("detail", ""))
    return CleanOutcome(
        category_id=category.id,
        freed_bytes=max(0, freed_bytes),
        removed=max(0, removed_count),
        failed=len(error_list) or (0 if payload.get("ok", True) else 1),
        errors=error_list,
        detail=detail,
    )
