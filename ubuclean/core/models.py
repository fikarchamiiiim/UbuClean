"""Data models shared by the core, CLI, and UI."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Group(str, Enum):
    """Display group for cleaning categories."""

    SYSTEM = "system"
    USER = "user"
    DEVELOPER = "developer"


@dataclass(frozen=True, slots=True)
class Category:
    """Metadata for a single cleaning category.

    ``privileged`` marks operations that require the root helper, ``risky``
    marks categories that must not be part of the automatic schedule and are
    not checked by default, while ``quarantine`` marks items that are moved to
    quarantine before being permanently deleted.
    """

    id: str
    label: str
    description: str
    group: Group
    icon: str = "user-trash-symbolic"
    privileged: bool = False
    risky: bool = False
    default_enabled: bool = True
    quarantine: bool = False
    schedule_allowed: bool = True
    schedule_default: bool = False


@dataclass(frozen=True, slots=True)
class PreviewItem:
    """A single scan result row (file, package, or revision)."""

    id: str
    label: str
    size: int = 0
    detail: str = ""
    path: Path | None = None
    protected: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON-able representation for CLI mode."""
        return {
            "id": self.id,
            "label": self.label,
            "size": self.size,
            "detail": self.detail,
            "path": str(self.path) if self.path else None,
            "protected": self.protected,
        }


@dataclass(slots=True)
class Preview:
    """Scan result for one category, and at the same time the execution permission token."""

    category_id: str
    items: list[PreviewItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands: list[tuple[str, ...]] = field(default_factory=list)
    supported: bool = True
    estimated_bytes: int | None = None
    scanned_at: float = field(default_factory=time.time)
    token: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def total_bytes(self) -> int:
        """Total size of the items, or the aggregate estimate when provided."""
        if self.estimated_bytes is not None:
            return max(0, self.estimated_bytes)
        return sum(item.size for item in self.items if not item.protected)

    @property
    def item_count(self) -> int:
        """Number of items that will be cleaned."""
        return sum(1 for item in self.items if not item.protected)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able representation for CLI mode and dry-run."""
        return {
            "category": self.category_id,
            "token": self.token,
            "supported": self.supported,
            "total_bytes": self.total_bytes,
            "estimated_bytes": self.estimated_bytes,
            "item_count": self.item_count,
            "notes": list(self.notes),
            "errors": list(self.errors),
            "commands": [list(cmd) for cmd in self.commands],
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(slots=True)
class CleanOutcome:
    """Execution result for one category."""

    category_id: str
    freed_bytes: int = 0
    removed: int = 0
    failed: int = 0
    quarantined: int = 0
    quarantined_bytes: int = 0
    quarantine_id: str | None = None
    errors: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True when no item failed."""
        return self.failed == 0

    @property
    def immediate_bytes(self) -> int:
        """Bytes actually gone from disk right now."""
        return max(0, self.freed_bytes - self.quarantined_bytes)

    def merge(self, other: "CleanOutcome") -> None:
        """Merge another result into this one."""
        self.freed_bytes += other.freed_bytes
        self.removed += other.removed
        self.failed += other.failed
        self.quarantined += other.quarantined
        self.quarantined_bytes += other.quarantined_bytes
        self.errors.extend(other.errors)
        if other.quarantine_id:
            self.quarantine_id = other.quarantine_id

    def to_dict(self) -> dict[str, Any]:
        """JSON-able representation for logs and the CLI."""
        return {
            "category": self.category_id,
            "freed_bytes": self.freed_bytes,
            "removed": self.removed,
            "failed": self.failed,
            "quarantined": self.quarantined,
            "quarantined_bytes": self.quarantined_bytes,
            "immediate_bytes": self.immediate_bytes,
            "quarantine_id": self.quarantine_id,
            "errors": list(self.errors),
            "detail": self.detail,
        }


@dataclass(slots=True)
class CleanReport:
    """Summary of the whole cleaning run."""

    outcomes: list[CleanOutcome] = field(default_factory=list)
    dry_run: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def freed_bytes(self) -> int:
        """Total bytes freed."""
        return sum(outcome.freed_bytes for outcome in self.outcomes)

    @property
    def failures(self) -> int:
        """Total items that failed to process."""
        return sum(outcome.failed for outcome in self.outcomes)

    @property
    def errors(self) -> list[str]:
        """Collection of error messages from all categories."""
        return [error for outcome in self.outcomes for error in outcome.errors]

    def to_dict(self) -> dict[str, Any]:
        """JSON-able representation for the CLI and logs."""
        return {
            "dry_run": self.dry_run,
            "freed_bytes": self.freed_bytes,
            "failures": self.failures,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }
