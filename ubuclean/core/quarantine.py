"""Quarantine cleaned items so they can be restored."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ubuclean.core.paths import HOME_PERSONAL_DIRS, path_size


class QuarantineError(RuntimeError):
    """Error while moving or restoring quarantine items."""


@dataclass(slots=True)
class QuarantineItem:
    """A single item kept in quarantine."""

    original: str
    stored: str
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-able representation."""
        return {"original": self.original, "stored": self.stored, "size": self.size}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantineItem":
        """Build an item from JSON metadata."""
        return cls(
            original=str(data["original"]),
            stored=str(data["stored"]),
            size=int(data.get("size", 0) or 0),
        )


@dataclass(slots=True)
class QuarantineEntry:
    """Metadata for one quarantined cleaning action."""

    action_id: str
    category_id: str
    created_at: float
    items: list[QuarantineItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Total size of the stored items."""
        return sum(item.size for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able representation."""
        return {
            "version": 1,
            "action_id": self.action_id,
            "category": self.category_id,
            "created_at": self.created_at,
            "items": [item.to_dict() for item in self.items],
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantineEntry":
        """Build an entry from JSON metadata, validating the required fields."""
        return cls(
            action_id=str(data["action_id"]),
            category_id=str(data.get("category", "")),
            created_at=float(data.get("created_at", 0) or 0),
            items=[QuarantineItem.from_dict(item) for item in data.get("items", [])],
            errors=[str(err) for err in data.get("errors", []) or []],
        )


@dataclass(slots=True)
class RestoreResult:
    """Result of restoring one quarantine action."""

    action_id: str
    restored: int = 0
    failed: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


class QuarantineStore:
    """Stores items in ``~/.local/share/ubuclean/quarantine``."""

    def __init__(self, root: Path, home: Path, *, retention_days: int = 7) -> None:
        self.root = Path(root)
        self.home = Path(home).resolve()
        self.retention_days = max(1, int(retention_days))

    def ensure(self) -> None:
        """Create the quarantine directory with 0700 permissions."""
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _action_dir(self, action_id: str) -> Path:
        """Directory of one action; rejects ids that try to escape the root."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", action_id):
            raise QuarantineError("Invalid quarantine ID.")
        return self.root / action_id

    def _meta_path(self, action_dir: Path) -> Path:
        return action_dir / "metadata.json"

    def _write_meta(self, action_dir: Path, entry: QuarantineEntry) -> None:
        payload = json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=action_dir,
            prefix=".metadata-",
            delete=False,
        )
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(handle.name, 0o600)
            os.replace(handle.name, self._meta_path(action_dir))
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    def _read_meta(self, action_dir: Path) -> QuarantineEntry | None:
        path = self._meta_path(action_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return QuarantineEntry.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _valid_restore(self, original: Path) -> bool:
        """Restore destination: inside home, and not an SSH/GPG key."""
        try:
            resolved = original.resolve(strict=False)
        except OSError:
            return False
        if self.home not in resolved.parents:
            return False
        for name in (".ssh", ".gnupg"):
            protected = (self.home / name).resolve(strict=False)
            if resolved == protected or protected in resolved.parents:
                return False
        return True

    def _valid_source(self, source: Path) -> bool:
        """Quarantine source: must not be personal data or a secret key."""
        if not self._valid_restore(source):
            return False
        for name in HOME_PERSONAL_DIRS:
            protected = (self.home / name).resolve(strict=False)
            if source.resolve(strict=False) == protected or protected in source.resolve(strict=False).parents:
                return False
        return True

    @staticmethod
    def _safe_name(path: Path) -> str:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name)[:80].strip("._")
        return name or "item"

    def quarantine(
        self,
        category_id: str,
        paths: Iterable[Path],
        *,
        now: float | None = None,
        action_id: str | None = None,
        original_overrides: Mapping[str, Path] | None = None,
    ) -> QuarantineEntry | None:
        """Move ``paths`` into quarantine and write the metadata.

        ``original_overrides`` maps a source path to its real original location
        (used by the Trash so that a restore goes back to the original folder).
        Returns ``None`` when no item was moved successfully.
        """
        self.ensure()
        action_id = action_id or (
            time.strftime("%Y%m%dT%H%M%S", time.localtime(now or time.time()))
            + "-"
            + uuid.uuid4().hex[:6]
        )
        action_dir = self._action_dir(action_id)
        files_dir = action_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(action_dir, 0o700)
        entry = QuarantineEntry(
            action_id=action_id,
            category_id=category_id,
            created_at=now if now is not None else time.time(),
        )
        for index, source in enumerate(paths):
            source = Path(source)
            try:
                if not self._valid_source(source):
                    entry.errors.append(f"Path is not eligible for quarantine: {source}")
                    continue
                if not os.path.lexists(source):
                    entry.errors.append(f"Not found: {source}")
                    continue
                size = path_size(source)
                stored = files_dir / f"{index:04d}-{self._safe_name(source)}"
                self._move(source, stored)
                original = source
                if original_overrides:
                    override = original_overrides.get(str(source))
                    if override is not None and self._valid_restore(override):
                        original = override
                entry.items.append(
                    QuarantineItem(
                        original=str(original),
                        stored=str(stored.relative_to(action_dir)),
                        size=size,
                    )
                )
            except OSError as exc:
                entry.errors.append(f"{source}: {exc}")
        if not entry.items:
            shutil.rmtree(action_dir, ignore_errors=True)
            return None
        self._write_meta(action_dir, entry)
        return entry

    @staticmethod
    def _move(source: Path, stored: Path) -> None:
        """Move an item; falls back to a copy across filesystems."""
        stored.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, stored)
        except OSError:
            shutil.move(str(source), str(stored))

    @staticmethod
    def _unique_target(target: Path) -> Path:
        """Find an alternative name when the target already exists."""
        if not os.path.lexists(target):
            return target
        if target.is_dir():
            suffix = " (restored)"
        else:
            suffix = " (restored)"
        candidate = target.with_name(target.name + suffix)
        counter = 2
        while os.path.lexists(candidate):
            candidate = target.with_name(f"{target.name}{suffix} {counter}")
            counter += 1
        return candidate

    def list_actions(self) -> list[QuarantineEntry]:
        """All quarantine actions, newest first."""
        if not self.root.is_dir():
            return []
        result: list[QuarantineEntry] = []
        try:
            children = sorted(self.root.iterdir(), key=lambda p: p.name, reverse=True)
        except OSError:
            return []
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            entry = self._read_meta(child)
            if entry is not None:
                result.append(entry)
        result.sort(key=lambda entry: entry.created_at, reverse=True)
        return result

    def get(self, action_id: str) -> QuarantineEntry | None:
        """Get a single action by id, or ``None``."""
        try:
            action_dir = self._action_dir(action_id)
        except QuarantineError:
            return None
        return self._read_meta(action_dir)

    def restore(self, action_id: str, *, dry_run: bool = False) -> RestoreResult:
        """Restore all items to their original paths."""
        entry = self.get(action_id)
        if entry is None:
            return RestoreResult(action_id=action_id, failed=1, errors=["Action not found."])
        action_dir = self._action_dir(action_id)
        result = RestoreResult(action_id=action_id, dry_run=dry_run)
        remaining: list[QuarantineItem] = []
        if not dry_run:
            os.chmod(action_dir, 0o700)
        for item in entry.items:
            stored = action_dir / item.stored
            original = Path(item.original)
            if not self._valid_restore(original):
                result.failed += 1
                result.errors.append(f"Original path is protected: {original}")
                remaining.append(item)
                continue
            if not os.path.lexists(stored):
                result.failed += 1
                result.errors.append(f"Quarantine file is missing: {stored.name}")
                remaining.append(item)
                continue
            target = self._unique_target(original)
            if dry_run:
                result.restored += 1
                result.bytes += item.size
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                self._move(stored, target)
                result.restored += 1
                result.bytes += item.size
            except OSError as exc:
                result.failed += 1
                result.errors.append(f"{original}: {exc}")
                remaining.append(item)
        if not dry_run:
            if remaining:
                entry.items = remaining
                entry.errors = result.errors
                self._write_meta(action_dir, entry)
            else:
                shutil.rmtree(action_dir, ignore_errors=True)
        return result

    def delete_action(self, action_id: str) -> bool:
        """Permanently delete one quarantine action."""
        try:
            action_dir = self._action_dir(action_id)
        except QuarantineError:
            return False
        if not action_dir.is_dir():
            return False
        shutil.rmtree(action_dir, ignore_errors=True)
        return True

    def purge_expired(self, *, now: float | None = None) -> list[str]:
        """Delete actions older than the retention period; return their ids."""
        now = time.time() if now is None else now
        cutoff = now - self.retention_days * 86400
        removed: list[str] = []
        for entry in self.list_actions():
            if entry.created_at <= cutoff:
                if self.delete_action(entry.action_id):
                    removed.append(entry.action_id)
        return removed

    def purge_all(self) -> list[str]:
        """Delete the entire contents of the quarantine."""
        removed: list[str] = []
        for entry in self.list_actions():
            if self.delete_action(entry.action_id):
                removed.append(entry.action_id)
        return removed

    def total_bytes(self) -> int:
        """Total size of all quarantine items."""
        return sum(entry.total_bytes for entry in self.list_actions())

    def size_bytes(self) -> int:
        """Actual size of the quarantine directory (including metadata)."""
        return path_size(self.root)
