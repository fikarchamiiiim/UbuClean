"""Scan orchestration and cleaning backed by preview tokens."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Mapping, Sequence

from ubuclean.core.cleaners import create_cleaner
from ubuclean.core.cleaners.base import BaseCleaner
from ubuclean.core.context import Context
from ubuclean.core.journal import HistoryEntry
from ubuclean.core.models import CleanOutcome, CleanReport, Preview
from ubuclean.core.registry import CATEGORY_MAP, get_category

ProgressCallback = Callable[[str, int, int], None]


class PreviewRequired(RuntimeError):
    """Execution rejected because this session never scanned a preview."""


class CleanAssistant:
    """One scan + clean session; the token prevents deletion without a preview."""

    def __init__(
        self,
        ctx: Context,
        cleaner_factory: Callable[[str], BaseCleaner | None] = create_cleaner,
    ) -> None:
        self.ctx = ctx
        self._factory = cleaner_factory
        self._tokens: dict[str, str] = {}

    def _scan_category(self, category_id: str) -> Preview:
        cleaner = self._factory(category_id)
        if cleaner is None:
            return Preview(
                category_id=category_id,
                supported=False,
                errors=["This category is not available in this version."],
            )
        try:
            return cleaner.scan(self.ctx)
        except Exception as exc:  # one failing category does not bring down the others
            return Preview(
                category_id=category_id,
                supported=False,
                errors=[f"Scan failed: {exc}"],
            )

    def scan(
        self,
        category_ids: Iterable[str],
        *,
        progress: ProgressCallback | None = None,
        workers: int = 4,
    ) -> dict[str, Preview]:
        """Scan categories in parallel and register their preview tokens."""
        ordered = [cid for cid in dict.fromkeys(category_ids) if cid in CATEGORY_MAP]
        if not ordered:
            return {}
        total = len(ordered)
        results: dict[str, Preview] = {}
        worker_count = max(1, min(workers, total))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ubuclean-scan") as pool:
            futures = {
                pool.submit(self._scan_category, category_id): category_id
                for category_id in ordered
            }
            done = 0
            for future in as_completed(futures):
                category_id = futures[future]
                preview = future.result()
                results[category_id] = preview
                self._tokens[category_id] = preview.token
                done += 1
                if progress is not None:
                    try:
                        progress(category_id, done, total)
                    except Exception:
                        pass
        return {category_id: results[category_id] for category_id in ordered}

    def _validate_preview(self, preview: Preview) -> None:
        registered = self._tokens.get(preview.category_id)
        if registered is None or registered != preview.token:
            raise PreviewRequired(
                f"Preview for category {preview.category_id} is not valid or has expired."
            )

    def clean(
        self,
        previews: Mapping[str, Preview] | Sequence[Preview],
        *,
        dry_run: bool = False,
        progress: ProgressCallback | None = None,
    ) -> CleanReport:
        """Run the cleaning only for the registered previews."""
        items = list(previews.values()) if isinstance(previews, Mapping) else list(previews)
        for preview in items:
            self._validate_preview(preview)
        context = self.ctx.with_dry_run(dry_run)
        report = CleanReport(dry_run=dry_run)
        total = len(items)
        done = 0
        for preview in items:
            outcome = self._clean_one(context, preview, dry_run=dry_run)
            report.outcomes.append(outcome)
            self._log_outcome(context, preview, outcome, dry_run=dry_run)
            done += 1
            if progress is not None:
                try:
                    progress(preview.category_id, done, total)
                except Exception:
                    pass
        report.finished_at = time.time()
        return report

    def _clean_one(self, context: Context, preview: Preview, *, dry_run: bool) -> CleanOutcome:
        if not preview.supported:
            return CleanOutcome(
                category_id=preview.category_id,
                failed=1,
                errors=list(preview.errors) or ["Category is not supported."],
            )
        cleaner = self._factory(preview.category_id)
        if cleaner is None:
            return CleanOutcome(
                category_id=preview.category_id,
                failed=1,
                errors=["Cleaner is not available."],
            )
        try:
            return cleaner.clean(context, preview)
        except Exception as exc:  # the UI/CLI must never crash
            return CleanOutcome(
                category_id=preview.category_id,
                failed=1,
                errors=[f"Cleaning failed: {exc}"],
            )

    @staticmethod
    def _log_outcome(
        context: Context,
        preview: Preview,
        outcome: CleanOutcome,
        *,
        dry_run: bool,
    ) -> None:
        category = CATEGORY_MAP.get(preview.category_id)
        label = category.label if category else preview.category_id
        entry = HistoryEntry(
            action="clean",
            category=preview.category_id,
            label=label,
            items=outcome.removed,
            bytes=outcome.freed_bytes,
            ok=outcome.ok,
            dry_run=dry_run,
            detail=outcome.detail,
            quarantine_id=outcome.quarantine_id,
            errors=list(outcome.errors),
        )
        try:
            context.history.record(entry)
        except OSError:
            pass

    def invalidate(self) -> None:
        """Invalidate every preview token (used after cleaning)."""
        self._tokens.clear()


def category_label(category_id: str) -> str:
    """The category label, or the id when it is unknown."""
    try:
        return get_category(category_id).label
    except KeyError:
        return category_id
