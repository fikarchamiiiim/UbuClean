"""History page and quarantine restore."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk

from ubuclean.core.journal import HistoryEntry
from ubuclean.core.quarantine import QuarantineEntry
from ubuclean.core.registry import CATEGORY_MAP
from ubuclean.core.util import format_bytes, format_count, format_date
from ubuclean.ui.common import (
    action_row,
    ask_confirm,
    button,
    card,
    clear_group,
    icon,
    icon_button,
    label,
    run_async,
)
from ubuclean.ui.pages.base import BasePage


class HistoryPage(BasePage):
    """Action log, quarantine list, and a Restore button per action."""

    def __init__(self, window: Any) -> None:
        super().__init__("History", "Cleanup log and restorable quarantine", maximum_width=900)
        self.window = window
        self.services = window.services
        self._entries: list[HistoryEntry] = []
        self._quarantine: list[QuarantineEntry] = []
        self._quarantine_bytes = 0
        self._busy = False

        refresh = icon_button("view-refresh-symbolic", "Reload history")
        refresh.connect("clicked", lambda *_: self.refresh())
        self.add_action(refresh)

        self._summary = self._build_summary()
        self.body.append(self._summary)
        self._quarantine_group = Adw.PreferencesGroup(
            title="Quarantine",
            description="Items from safe categories are held temporarily before permanent deletion.",
        )
        self._history_group = Adw.PreferencesGroup(
            title="Recent actions",
            description="Every action is recorded with its time, category, size, and result.",
        )
        self.body.append(self._quarantine_group)
        self.body.append(self._history_group)

    def _build_summary(self) -> Gtk.Widget:
        box = card(spacing=10)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.append(icon("document-open-recent-symbolic", 22, css="uc-nav-icon"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self._summary_value = label("0 B", css="uc-metric-compact")
        self._summary_detail = label("", css="uc-subtle", wrap=True)
        text.append(self._summary_value)
        text.append(self._summary_detail)
        top.append(text)
        self._purge_button = button(
            "Empty quarantine", icon_name="user-trash-symbolic", destructive=True
        )
        self._purge_button.set_valign(Gtk.Align.CENTER)
        self._purge_button.connect("clicked", lambda *_: self._confirm_purge())
        top.append(self._purge_button)
        box.append(top)
        return box

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True

        def work() -> tuple[list[HistoryEntry], list[QuarantineEntry], int]:
            entries = self.services.context.history.entries(limit=250)
            actions = self.services.context.quarantine.list_actions()
            total = sum(entry.total_bytes for entry in actions)
            return entries, actions, total

        def done(result: Any) -> None:
            self._busy = False
            self._entries, self._quarantine, self._quarantine_bytes = result
            self._populate()

        def failed(exc: Exception) -> None:
            self._busy = False
            self.window.toast(f"Failed to load history: {exc}")

        run_async(work, done, failed)

    @staticmethod
    def _clear_group(group: Adw.PreferencesGroup) -> None:
        clear_group(group)

    def _populate(self) -> None:
        retention = int(self.services.config.get("quarantine_days", 7))
        self._summary_value.set_text(format_bytes(self._quarantine_bytes))
        self._summary_detail.set_text(
            f"{len(self._quarantine)} quarantine actions · {retention} day retention · "
            "expired items are deleted automatically when the app or a schedule runs."
        )
        self._purge_button.set_sensitive(bool(self._quarantine))

        self._clear_group(self._quarantine_group)
        if not self._quarantine:
            self._quarantine_group.add(self._message_row("Quarantine is empty."))
        else:
            for action in self._quarantine:
                self._quarantine_group.add(self._quarantine_row(action))

        self._clear_group(self._history_group)
        if not self._entries:
            self._history_group.add(self._message_row("No action history yet."))
        else:
            for entry in self._entries:
                self._history_group.add(self._history_row(entry))

    @staticmethod
    def _message_row(text: str) -> Adw.ActionRow:
        row = action_row(text)
        row.set_activatable(False)
        return row

    @staticmethod
    def _category_label(category_id: str) -> str:
        category = CATEGORY_MAP.get(category_id)
        return category.label if category else category_id

    def _quarantine_row(self, action: QuarantineEntry) -> Adw.ActionRow:
        date = format_date(action.created_at, with_time=True)
        row = action_row(
            self._category_label(action.category_id),
            f"{date} · {format_count(len(action.items))} item · {format_bytes(action.total_bytes)}",
        )
        row.add_prefix(icon("user-trash-symbolic", 16, css="uc-nav-icon"))
        restore = button("Restore", icon_name="edit-undo-symbolic")
        restore.set_valign(Gtk.Align.CENTER)
        restore.connect("clicked", lambda *_: self._restore(action))
        row.add_suffix(restore)
        if action.errors:
            row.set_tooltip_text("\n".join(action.errors[:3]))
        return row

    def _history_row(self, entry: HistoryEntry) -> Adw.ActionRow:
        timestamp = format_date(entry.ts, with_time=True)
        status = "succeeded" if entry.ok else "failed"
        if entry.dry_run:
            status = "preview"
        detail = entry.detail or entry.action
        row = action_row(
            entry.label or self._category_label(entry.category),
            f"{timestamp} · {detail} · {status}",
        )
        if entry.ok and not entry.dry_run:
            row.add_prefix(icon("object-select-symbolic", 16, css="uc-success-text"))
        elif entry.dry_run:
            row.add_prefix(icon("view-reveal-symbolic", 16, css="uc-nav-icon"))
        else:
            row.add_prefix(icon("dialog-warning-symbolic", 16, css="uc-warning-text"))
        row.add_suffix(
            label(
                f"{format_bytes(entry.bytes)} · {format_count(entry.items)} item",
                css="uc-label",
            )
        )
        if entry.errors:
            row.set_tooltip_text("\n".join(entry.errors[:3]))
        return row

    # ------------------------------------------------------------------
    def _restore(self, action: QuarantineEntry) -> None:
        label_text = self._category_label(action.category_id)
        ask_confirm(
            self,
            heading="Restore items from quarantine?",
            body=(
                f"{len(action.items)} items ({format_bytes(action.total_bytes)}) will be returned "
                "to their original paths. Files that already exist will be renamed."
            ),
            confirm_label="Restore",
            on_confirm=lambda: self._run_restore(action, label_text),
        )

    def _run_restore(self, action: QuarantineEntry, label_text: str) -> None:
        action_id = action.action_id

        def work() -> Any:
            return self.services.context.quarantine.restore(action_id)

        def done(result: Any) -> None:
            try:
                self.services.context.history.record(
                    HistoryEntry(
                        action="restore",
                        category=action.category_id,
                        label=f"Restore {label_text}",
                        items=result.restored,
                        bytes=result.bytes,
                        ok=result.failed == 0,
                        detail=f"{result.restored} items restored, {result.failed} failed.",
                        errors=list(result.errors),
                    )
                )
            except OSError:
                pass
            self.window.toast(
                f"{result.restored} items restored"
                if not result.errors
                else f"Restore finished with {result.failed} failures"
            )
            self.refresh()

        run_async(work, done, lambda exc: self.window.toast(f"Restore failed: {exc}"))

    def _confirm_purge(self) -> None:
        ask_confirm(
            self,
            heading="Empty quarantine?",
            body=(
                "All quarantined items will be permanently deleted and cannot be restored. "
                "The action history is kept."
            ),
            confirm_label="Delete permanently",
            destructive=True,
            on_confirm=self._run_purge,
        )

    def _run_purge(self) -> None:
        total = self._quarantine_bytes

        def work() -> list[str]:
            return self.services.context.quarantine.purge_all()

        def done(removed: list[str]) -> None:
            try:
                self.services.context.history.record(
                    HistoryEntry(
                        action="purge",
                        category="quarantine",
                        label="Quarantine emptied",
                        items=len(removed),
                        bytes=total,
                        ok=True,
                        detail=f"{len(removed)} quarantine actions permanently deleted.",
                    )
                )
            except OSError:
                pass
            self.window.toast(f"{len(removed)} quarantine actions permanently deleted")
            self.refresh()

        run_async(work, done, lambda exc: self.window.toast(f"Failed to empty quarantine: {exc}"))
