"""Mode headless ``ubuclean --run-scheduled``."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ubuclean.core.context import Context
from ubuclean.core.journal import HistoryEntry
from ubuclean.core.models import CleanReport
from ubuclean.core.registry import CATEGORY_MAP, is_schedule_safe
from ubuclean.core.scan import CleanAssistant


@dataclass(slots=True)
class ScheduledOutcome:
    """Ringkasan satu eksekusi pembersihan terjadwal."""

    started_at: float
    finished_at: float = 0.0
    cleaned: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    report: CleanReport | None = None
    notified: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def freed_bytes(self) -> int:
        """Total byte yang dibersihkan."""
        return self.report.freed_bytes if self.report else 0

    def to_dict(self) -> dict[str, Any]:
        """Representasi JSON-able."""
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cleaned": list(self.cleaned),
            "skipped": list(self.skipped),
            "freed_bytes": self.freed_bytes,
            "notified": self.notified,
            "errors": list(self.errors),
            "report": self.report.to_dict() if self.report else None,
        }


def _notify_summary(ctx: Context, outcome: ScheduledOutcome) -> bool:
    if ctx.runner.which("notify-send") is None:
        return False
    from ubuclean.core.util import format_bytes

    parts = [f"Pembersihan terjadwal: {format_bytes(outcome.freed_bytes)} dibersihkan."]
    if outcome.skipped:
        parts.append(f"{len(outcome.skipped)} kategori dilewati (butuh autentikasi/berisiko).")
    result = ctx.runner.run(
        [
            "notify-send",
            "--app-name=UbuClean",
            "--icon=com.ubuclean.App",
            "UbuClean",
            " ".join(parts),
        ],
        timeout=20,
    )
    return result.returncode == 0


def run_scheduled(ctx: Context | None = None, *, notify: bool = True) -> ScheduledOutcome:
    """Jalankan kategori aman tanpa root; kategori root dicatat dilewati."""
    ctx = ctx or Context.build()
    config = ctx.config
    requested = [str(item) for item in config.schedule.get("categories", [])]
    outcome = ScheduledOutcome(started_at=time.time())
    ctx.quarantine.purge_expired()

    safe_ids: list[str] = []
    for category_id in dict.fromkeys(requested):
        category = CATEGORY_MAP.get(category_id)
        if category is None:
            outcome.errors.append(f"Kategori tidak dikenal: {category_id}")
            continue
        if not is_schedule_safe(category_id):
            outcome.skipped.append(category_id)
            continue
        safe_ids.append(category_id)
    if not safe_ids:
        outcome.finished_at = time.time()
        return outcome

    assistant = CleanAssistant(ctx)
    previews = assistant.scan(safe_ids)
    for category_id in safe_ids:
        preview = previews.get(category_id)
        category = CATEGORY_MAP[category_id]
        if preview is None or not preview.supported:
            outcome.skipped.append(category_id)
            _log_skipped(ctx, category_id, category.label, "Pemindaian tidak tersedia.")
            continue
        if category.privileged:
            outcome.skipped.append(category_id)
            _log_skipped(
                ctx,
                category_id,
                category.label,
                "Dilewati: operasi root membutuhkan autentikasi.",
            )
            continue
        report = assistant.clean([preview])
        outcome.cleaned.append(category_id)
        if outcome.report is None:
            outcome.report = report
        else:
            outcome.report.outcomes.extend(report.outcomes)
        outcome.errors.extend(report.errors)
    outcome.finished_at = time.time()
    if outcome.report is not None:
        outcome.report.finished_at = outcome.finished_at
    try:
        ctx.history.record(
            HistoryEntry(
                action="scheduled",
                category="schedule",
                label="Pembersihan terjadwal",
                items=sum(item.removed for item in (outcome.report.outcomes if outcome.report else [])),
                bytes=outcome.freed_bytes,
                ok=not outcome.errors,
                detail=f"{len(outcome.cleaned)} kategori dibersihkan, {len(outcome.skipped)} dilewati.",
                errors=list(outcome.errors),
            )
        )
    except OSError:
        pass
    if notify:
        outcome.notified = _notify_summary(ctx, outcome)
    return outcome


def _log_skipped(ctx: Context, category_id: str, label: str, reason: str) -> None:
    try:
        ctx.history.record(
            HistoryEntry(
                action="scheduled",
                category=category_id,
                label=label,
                ok=True,
                detail=reason,
            )
        )
    except OSError:
        pass
