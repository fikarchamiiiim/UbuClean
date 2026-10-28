"""Disk analysis page and duplicate file finder."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk

from ubuclean.core.disk import (
    DiskEntry,
    DuplicateProgress,
    DuplicateScanResult,
    FolderScanResult,
    InvalidScanRoot,
    ScanProgress,
    find_duplicates,
    scan_folder,
)
from ubuclean.core.journal import HistoryEntry
from ubuclean.core.util import format_bytes, format_count
from ubuclean.ui.common import (
    AnimatedNumber,
    action_row,
    ask_confirm,
    button,
    card,
    clear_group,
    expander_row,
    icon,
    label,
    progress,
    run_async,
    show_info,
)
from ubuclean.ui.pages.base import BasePage
from ubuclean.ui.widgets.treemap import Treemap

STAGE_LABELS = {
    "size": "Grouping by size",
    "partial": "Computing partial hashes",
    "full": "Computing full hashes",
}


class DiskPage(BasePage):
    """Folder scanner, treemap, and duplicate finder with no automatic deletion."""

    def __init__(self, window: Any) -> None:
        super().__init__("Disk", "Folder and duplicate file analysis", maximum_width=980)
        self.window = window
        self.services = window.services
        self.current_folder = Path.home()
        self._scan_busy = False
        self._dup_busy = False
        self._scan_cancel: threading.Event | None = None
        self._dup_cancel: threading.Event | None = None
        self._dup_checks: dict[Path, Gtk.CheckButton] = {}
        self._dup_rows: list[Adw.ActionRow] = []
        self._dup_footer: Gtk.Widget | None = None

        self.body.append(self._build_folder_card())
        self._scan_progress_card = self._build_scan_progress()
        self.body.append(self._scan_progress_card)
        self._empty_status = self._build_empty_state()
        self.body.append(self._empty_status)
        self._summary_card = self._build_summary()
        self.body.append(self._summary_card)
        self._treemap_card = self._build_treemap_card()
        self.body.append(self._treemap_card)
        self._folders_group = Adw.PreferencesGroup(
            title="Largest folders",
            description="Sorted by the total size of the folder contents.",
        )
        self._files_group = Adw.PreferencesGroup(
            title="Largest files",
            description="Largest individual files inside the scanned folder.",
        )
        self.body.append(self._folders_group)
        self.body.append(self._files_group)
        self.body.append(self._build_duplicates())
        self._dup_progress_card = self._build_dup_progress()
        self.body.append(self._dup_progress_card)
        self._dup_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.body.append(self._dup_results)
        self._set_results_visible(False)

    # ------------------------------------------------------------------
    def _build_folder_card(self) -> Gtk.Widget:
        box = card(spacing=14)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.append(icon("folder-symbolic", 28, css="uc-nav-icon"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label("Analyzed folder", css="uc-label"))
        self._path_label = label(str(self.current_folder), css="uc-metric-compact", wrap=True)
        text.append(self._path_label)
        text.append(
            label(
                "Scanning runs in a separate thread and can be cancelled.",
                css="uc-subtle",
            )
        )
        row.append(text)
        self._choose_button = button("Select folder", icon_name="folder-open-symbolic")
        self._choose_button.connect("clicked", lambda *_: self._choose_folder())
        self._scan_button = button("Scan", icon_name="system-search-symbolic", suggested=True)
        self._scan_button.connect("clicked", lambda *_: self._start_scan())
        row.append(self._choose_button)
        row.append(self._scan_button)
        box.append(row)
        return box

    def _build_scan_progress(self) -> Gtk.Widget:
        box = card(spacing=10, css=("uc-card-quiet",))
        box.set_visible(False)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._scan_spinner = Gtk.Spinner()
        top.append(self._scan_spinner)
        self._scan_label = label("Scanning…", css="uc-subtle", hexpand=True)
        top.append(self._scan_label)
        self._scan_cancel_button = button("Cancel", css=("flat",))
        self._scan_cancel_button.connect("clicked", lambda *_: self._cancel_scan())
        top.append(self._scan_cancel_button)
        self._scan_bar = progress()
        box.append(top)
        box.append(self._scan_bar)
        return box

    def _build_empty_state(self) -> Gtk.Widget:
        return Adw.StatusPage(
            title="No results yet",
            description="Select a folder and press Scan to see the largest folders and files.",
            icon_name="drive-harddisk-symbolic",
        )

    def _build_summary(self) -> Gtk.Widget:
        box = card(spacing=8, css=("uc-result",))
        box.set_visible(False)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.append(icon("drive-harddisk-symbolic", 24))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self._summary_value = label("0 B", css="uc-total")
        self._summary_anim = AnimatedNumber(self, self._summary_value, duration=300)
        self._summary_detail = label("", css="uc-subtle", wrap=True)
        text.append(self._summary_value)
        text.append(self._summary_detail)
        top.append(text)
        box.append(top)
        return box

    def _build_treemap_card(self) -> Gtk.Widget:
        box = card(spacing=10)
        box.set_visible(False)
        box.append(label("Folder composition", css="uc-section"))
        box.append(
            label(
                "Each box represents a direct item inside the folder; area is proportional to size.",
                css="uc-subtle",
                wrap=True,
            )
        )
        self.treemap = Treemap()
        box.append(self.treemap)
        return box

    def _build_duplicates(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="Duplicate files",
            description=(
                "Group by size, then partial hash, then full hash. "
                "No files are deleted automatically."
            ),
        )
        row = Adw.ActionRow(
            title="Find duplicates",
            subtitle="Choose yourself which files to move to Trash.",
        )
        row.add_prefix(icon("document-open-recent-symbolic", 16, css="uc-nav-icon"))
        self._dup_button = button("Find duplicates", icon_name="system-search-symbolic")
        self._dup_button.set_valign(Gtk.Align.CENTER)
        self._dup_button.connect("clicked", lambda *_: self._start_duplicates())
        row.add_suffix(self._dup_button)
        group.add(row)
        return group

    def _build_dup_progress(self) -> Gtk.Widget:
        box = card(spacing=10, css=("uc-card-quiet",))
        box.set_visible(False)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._dup_spinner = Gtk.Spinner()
        top.append(self._dup_spinner)
        self._dup_label = label("Searching for duplicates…", css="uc-subtle", hexpand=True)
        top.append(self._dup_label)
        self._dup_cancel_button = button("Cancel", css=("flat",))
        self._dup_cancel_button.connect("clicked", lambda *_: self._cancel_duplicates())
        top.append(self._dup_cancel_button)
        self._dup_bar = progress()
        box.append(top)
        box.append(self._dup_bar)
        return box

    def _set_results_visible(self, visible: bool) -> None:
        self._summary_card.set_visible(visible)
        self._treemap_card.set_visible(visible)
        self._folders_group.set_visible(visible)
        self._files_group.set_visible(visible)
        self._empty_status.set_visible(not visible)

    # ------------------------------------------------------------------
    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Select folder to analyze", modal=True)
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(str(self.current_folder)))
        except (AttributeError, GLib.Error):
            pass

        def callback(source: Gtk.FileDialog, result: Any) -> None:
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is None:
                return
            path = folder.get_path()
            if not path:
                return
            self.current_folder = Path(path)
            self._path_label.set_text(str(self.current_folder))
            self._set_results_visible(False)
            self._summary_card.set_visible(False)
            self._scan_progress_card.set_visible(False)
            self._clear_box(self._dup_results)
            self._dup_checks.clear()

        dialog.select_folder(self, None, callback)

    @staticmethod
    def _clear_box(box: Gtk.Box) -> None:
        while (child := box.get_first_child()) is not None:
            box.remove(child)

    def _cancel_scan(self) -> None:
        if self._scan_cancel is not None:
            self._scan_cancel.set()
            self._scan_cancel_button.set_sensitive(False)
            self._scan_label.set_text("Cancelling scan…")

    def _start_scan(self) -> None:
        if self._scan_busy:
            return
        self._scan_busy = True
        self._scan_cancel = threading.Event()
        self._scan_progress_card.set_visible(True)
        self._scan_spinner.start()
        self._scan_bar.set_fraction(0.0)
        self._scan_label.set_text("Scanning…")
        self._scan_cancel_button.set_sensitive(True)
        self._scan_button.set_sensitive(False)
        self._choose_button.set_sensitive(False)
        self._set_results_visible(False)
        folder = self.current_folder

        def progress_callback(item: ScanProgress) -> None:
            GLib.idle_add(self._update_scan_progress, item)

        def work() -> FolderScanResult:
            return scan_folder(
                folder,
                cancel=self._scan_cancel,
                progress=progress_callback,
                top_n=25,
            )

        def done(result: FolderScanResult) -> None:
            self._scan_busy = False
            self._scan_spinner.stop()
            self._scan_progress_card.set_visible(False)
            self._scan_button.set_sensitive(True)
            self._choose_button.set_sensitive(True)
            self._populate_scan(result)
            if result.cancelled:
                self.window.toast("Scan cancelled; partial results shown.")
            else:
                self.window.toast(
                    f"Done · {format_bytes(result.total_bytes)} in {result.elapsed:.1f} seconds"
                )

        def failed(exc: Exception) -> None:
            self._scan_busy = False
            self._scan_spinner.stop()
            self._scan_progress_card.set_visible(False)
            self._scan_button.set_sensitive(True)
            self._choose_button.set_sensitive(True)
            self._set_results_visible(False)
            if isinstance(exc, InvalidScanRoot):
                show_info(self, heading="Invalid folder", body=str(exc))
            else:
                self.window.toast(f"Scan failed: {exc}")

        run_async(work, done, failed)

    def _update_scan_progress(self, item: ScanProgress) -> None:
        self._scan_bar.pulse()
        current = item.current
        if len(current) > 90:
            current = "…" + current[-89:]
        self._scan_label.set_text(
            f"{format_count(item.files)} file · {format_bytes(item.size)} · {current}"
        )

    def _populate_scan(self, result: FolderScanResult) -> None:
        self._summary_card.set_visible(True)
        self._summary_anim.set_value(result.total_bytes)
        detail = (
            f"{format_count(result.scanned_files)} file · "
            f"{format_count(result.scanned_dirs)} folders · {result.elapsed:.1f} seconds"
        )
        if result.cancelled:
            detail += " · cancelled"
        if result.errors:
            detail += f" · {result.errors} items could not be read"
        self._summary_detail.set_text(detail)
        self.treemap.set_entries(
            [(entry.path.name, entry.size) for entry in result.children]
        )
        self._treemap_card.set_visible(True)
        self._empty_status.set_visible(False)
        self._folders_group.set_visible(bool(result.folders))
        self._files_group.set_visible(bool(result.files))
        self._fill_group(self._folders_group, result.folders[:25], is_dir=True)
        self._fill_group(self._files_group, result.files[:25], is_dir=False)

    def _fill_group(self, group: Adw.PreferencesGroup, entries: Sequence[DiskEntry], *, is_dir: bool) -> None:
        clear_group(group)
        for entry in entries:
            try:
                display = str(entry.path.relative_to(self.current_folder))
            except ValueError:
                display = str(entry.path)
            row = action_row(entry.path.name, display)
            row.add_prefix(
                icon("folder-symbolic" if is_dir else "text-x-generic-symbolic", 16, css="uc-nav-icon")
            )
            row.add_suffix(label(format_bytes(entry.size), css="uc-label"))
            row.set_activatable(False)
            group.add(row)

    # -- duplicates ----------------------------------------------------
    def _cancel_duplicates(self) -> None:
        if self._dup_cancel is not None:
            self._dup_cancel.set()
            self._dup_cancel_button.set_sensitive(False)
            self._dup_label.set_text("Cancelling duplicate search…")

    def _start_duplicates(self) -> None:
        if self._dup_busy:
            return
        self._dup_busy = True
        self._dup_cancel = threading.Event()
        self._dup_progress_card.set_visible(True)
        self._dup_spinner.start()
        self._dup_bar.set_fraction(0.0)
        self._dup_label.set_text("Grouping by size…")
        self._dup_cancel_button.set_sensitive(True)
        self._dup_button.set_sensitive(False)
        self._choose_button.set_sensitive(False)
        self._clear_box(self._dup_results)
        self._dup_checks.clear()
        folder = self.current_folder

        def progress_callback(item: DuplicateProgress) -> None:
            GLib.idle_add(self._update_dup_progress, item)

        def work() -> DuplicateScanResult:
            return find_duplicates(
                folder,
                cancel=self._dup_cancel,
                progress=progress_callback,
                min_size=4096,
            )

        def done(result: DuplicateScanResult) -> None:
            self._dup_busy = False
            self._dup_spinner.stop()
            self._dup_progress_card.set_visible(False)
            self._dup_button.set_sensitive(True)
            self._choose_button.set_sensitive(True)
            self._populate_duplicates(result)
            if result.cancelled:
                self.window.toast("Duplicate search cancelled.")
            else:
                self.window.toast(
                    f"{len(result.groups)} duplicate groups · {format_bytes(result.total_wasted)} can be saved"
                )

        def failed(exc: Exception) -> None:
            self._dup_busy = False
            self._dup_spinner.stop()
            self._dup_progress_card.set_visible(False)
            self._dup_button.set_sensitive(True)
            self._choose_button.set_sensitive(True)
            self.window.toast(f"Duplicate search failed: {exc}")

        run_async(work, done, failed)

    def _update_dup_progress(self, item: DuplicateProgress) -> None:
        self._dup_bar.pulse()
        stage = STAGE_LABELS.get(item.stage, item.stage)
        if item.total:
            self._dup_label.set_text(f"{stage} · {item.processed}/{item.total}")
        else:
            self._dup_label.set_text(stage)

    def _populate_duplicates(self, result: DuplicateScanResult) -> None:
        if not result.groups:
            box = card(spacing=8)
            box.append(label("No duplicates found", css="uc-section"))
            box.append(
                label(
                    f"{format_count(result.scanned_files)} files checked. "
                    "Files smaller than 4 kB are skipped.",
                    css="uc-subtle",
                    wrap=True,
                )
            )
            self._dup_results.append(box)
            return
        group = Adw.PreferencesGroup(
            title=f"{len(result.groups)} duplicate groups",
            description=(
                f"{format_bytes(result.total_wasted)} can be saved by keeping one copy."
                + (
                    f" Showing the top {min(100, len(result.groups))} groups."
                    if len(result.groups) > 100
                    else ""
                )
            ),
        )
        for duplicate_group in result.groups[:100]:
            expander = expander_row(
                f"{len(duplicate_group.files)} file · {format_bytes(duplicate_group.size)}",
                f"{format_bytes(duplicate_group.wasted_bytes)} can be saved",
            )
            select_button = button("Select all but one", css=("flat",))
            checks_for_group: list[Gtk.CheckButton] = []
            for index, path in enumerate(duplicate_group.files):
                row = action_row(path.name, str(path.parent))
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                check.set_active(False)
                check.connect("toggled", lambda *_: self._update_dup_selection())
                row.add_suffix(check)
                expander.add_row(row)
                self._dup_checks[path] = check
                self._dup_rows.append(row)
                checks_for_group.append(check)
            select_button.connect(
                "clicked",
                lambda _button, checks=checks_for_group: self._select_duplicates(checks),
            )
            expander.add_suffix(select_button)
            group.add(expander)
        self._dup_results.append(group)

        footer = card(spacing=8)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._dup_selection_label = label("No files selected yet", css="uc-subtle", hexpand=True)
        bottom.append(self._dup_selection_label)
        self._trash_button = button(
            "Move to Trash",
            icon_name="user-trash-symbolic",
            destructive=True,
        )
        self._trash_button.set_sensitive(False)
        self._trash_button.connect("clicked", lambda *_: self._confirm_trash())
        bottom.append(self._trash_button)
        footer.append(bottom)
        footer.append(
            label(
                "Files are moved to Trash via Gio, not permanently deleted.",
                css="uc-subtle",
            )
        )
        self._dup_results.append(footer)
        self._dup_footer = footer

    def _select_duplicates(self, checks: list[Gtk.CheckButton]) -> None:
        for index, check in enumerate(checks):
            check.set_active(index > 0)
        self._update_dup_selection()

    def _selected_duplicates(self) -> list[Path]:
        return [path for path, check in self._dup_checks.items() if check.get_active()]

    def _update_dup_selection(self) -> None:
        selected = self._selected_duplicates()
        total = sum(path.stat().st_size for path in selected if path.exists())
        if selected:
            self._dup_selection_label.set_text(
                f"{len(selected)} files · {format_bytes(total)} selected"
            )
        else:
            self._dup_selection_label.set_text("No files selected yet")
        self._trash_button.set_sensitive(bool(selected))

    def _confirm_trash(self) -> None:
        selected = self._selected_duplicates()
        if not selected:
            return
        total = sum(path.stat().st_size for path in selected if path.exists())
        ask_confirm(
            self,
            heading=f"Move {len(selected)} files to Trash?",
            body=(
                f"{format_bytes(total)} will be moved to Trash. "
                "The files can still be restored from Trash."
            ),
            confirm_label="Move",
            destructive=True,
            on_confirm=lambda: self._trash_paths(selected),
        )

    def _trash_paths(self, paths: list[Path]) -> None:
        total = sum(path.stat().st_size for path in paths if path.exists())

        def work() -> list[str]:
            errors: list[str] = []
            for path in paths:
                try:
                    Gio.File.new_for_path(str(path)).trash(None)
                except (GLib.Error, OSError) as exc:
                    errors.append(f"{path.name}: {exc}")
            return errors

        def done(errors: list[str]) -> None:
            try:
                self.services.context.history.record(
                    HistoryEntry(
                        action="trash-duplicates",
                        category="disk",
                        label="Duplicates moved to Trash",
                        items=len(paths) - len(errors),
                        bytes=total,
                        ok=not errors,
                        detail="Duplicate files moved to Trash via Gio.",
                        errors=list(errors),
                    )
                )
            except OSError:
                pass
            if errors:
                show_info(
                    self,
                    heading="Some files failed to move",
                    body="\n".join(errors[:8]),
                )
            self.window.toast(
                f"{len(paths) - len(errors)} files moved to Trash"
            )
            self._clear_box(self._dup_results)
            self._dup_checks.clear()
            self._dup_rows.clear()
            self._dup_button.set_sensitive(True)

        run_async(work, done, lambda exc: self.window.toast(f"Failed to move: {exc}"))
