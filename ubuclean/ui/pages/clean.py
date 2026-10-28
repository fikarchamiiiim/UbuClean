"""Halaman Bersihkan dan dialog hasil pembersihan."""

from __future__ import annotations

import time
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk

from ubuclean.core.journal import HistoryEntry
from ubuclean.core.models import CleanReport, Preview
from ubuclean.core.registry import CATEGORIES, CATEGORY_MAP, Group
from ubuclean.core.util import format_bytes, format_count, format_date
from ubuclean.ui.common import (
    AnimatedNumber,
    action_row,
    ask_confirm,
    button,
    card,
    icon,
    icon_button,
    label,
    plain_row,
    progress,
    run_async,
)
from ubuclean.ui.pages.base import BasePage

GROUP_TITLES = {
    Group.SYSTEM: ("Sistem", "Operasi ini memakai helper root melalui pkexec."),
    Group.USER: ("Pengguna", "Item aman dipindahkan ke karantina sebelum dihapus permanen."),
    Group.DEVELOPER: ("Developer", "Semua kategori developer tidak dicentang secara default."),
}
MAX_DETAIL_ROWS = 120


class CleanPage(BasePage):
    """Daftar kategori dengan switch, ukuran, pratinjau, dan aksi massal."""

    def __init__(self, window: Any) -> None:
        super().__init__("Bersihkan", "Tinjau setiap kategori sebelum menghapus", maximum_width=960)
        self.window = window
        self.services = window.services
        self._previews: dict[str, Preview] = dict(self.services.last_previews)
        self._switches: dict[str, Gtk.Switch] = {}
        self._rows: dict[str, Adw.ExpanderRow] = {}
        self._size_labels: dict[str, Gtk.Label] = {}
        self._detail_rows: dict[str, list[Gtk.Widget]] = {}
        self._busy = False
        self._did_scan = bool(self._previews)

        self._scan_content = Adw.ButtonContent(
            icon_name="view-refresh-symbolic", label="Pindai ulang"
        )
        self._scan_button = button()
        self._scan_button.set_child(self._scan_content)
        self._scan_button.connect("clicked", lambda *_: self.scan())
        self.add_action(self._scan_button)
        uninstall_button = icon_button(
            "application-x-executable-symbolic", "Uninstaller aplikasi"
        )
        uninstall_button.connect("clicked", lambda *_: self._open_uninstaller())
        self.add_action(uninstall_button)

        self._progress_card = self._build_progress_card()
        self.body.append(self._progress_card)
        self._build_groups()
        self._build_bottom_bar()
        if self._previews:
            self._apply_previews(self._previews, animate=False)

    # ------------------------------------------------------------------
    def _build_progress_card(self) -> Gtk.Widget:
        box = card(spacing=10, css=("uc-card-quiet",))
        box.set_visible(False)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.append(icon("view-refresh-symbolic", 16, css="uc-nav-icon"))
        self._progress_label = label("Memindai kategori…", css="uc-subtle", hexpand=True)
        top.append(self._progress_label)
        self._progress_bar = progress()
        box.append(top)
        box.append(self._progress_bar)
        return box

    def _build_groups(self) -> None:
        self._groups: dict[Group, Adw.PreferencesGroup] = {}
        for group in (Group.SYSTEM, Group.USER, Group.DEVELOPER):
            title, description = GROUP_TITLES[group]
            group_widget = Adw.PreferencesGroup(title=title, description=description)
            self.body.append(group_widget)
            self._groups[group] = group_widget
            for category in CATEGORIES:
                if category.group is not group:
                    continue
                self._add_category_row(category)

    def _add_category_row(self, category: Any) -> None:
        row = plain_row(Adw.ExpanderRow(title=category.label))
        row.set_subtitle(category.description)
        if category.risky:
            row.add_prefix(icon("dialog-warning-symbolic", 16, css="uc-warning-text"))
        elif category.privileged:
            row.add_prefix(icon("system-lock-screen-symbolic", 16, css="uc-subtle"))
        size_label = label("—", css="uc-label")
        row.add_suffix(size_label)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=category.default_enabled)
        switch.set_tooltip_text(f"Sertakan {category.label} dalam pembersihan")
        switch.connect("notify::active", lambda *_: self._update_total())
        row.add_suffix(switch)
        self._groups[category.group].add(row)
        self._rows[category.id] = row
        self._switches[category.id] = switch
        self._size_labels[category.id] = size_label

    def _build_bottom_bar(self) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        bar.add_css_class("uc-bottom-bar")
        bar.set_margin_top(10)
        bar.set_margin_bottom(10)
        bar.set_margin_start(18)
        bar.set_margin_end(18)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        self._total_label = label("Belum ada hasil pindai", css="uc-metric-compact")
        self._total_anim = AnimatedNumber(
            self,
            self._total_label,
            formatter=lambda value: f"{format_bytes(value)} bisa dibebaskan",
            duration=300,
        )
        self._detail_label = label("Tekan Pindai ulang untuk menghitung ukuran.", css="uc-subtle")
        text.append(self._total_label)
        text.append(self._detail_label)
        bar.append(text)

        dry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dry_box.append(label("Pratinjau", css="uc-subtle"))
        self._dry_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._dry_switch.set_tooltip_text("Tampilkan hasil tanpa mengubah apa pun")
        self._dry_switch.connect("notify::active", lambda *_: self._update_total())
        dry_box.append(self._dry_switch)
        bar.append(dry_box)

        self._clean_content = Adw.ButtonContent(
            icon_name="edit-delete-symbolic", label="Bersihkan"
        )
        self._clean_button = button(suggested=True)
        self._clean_button.set_child(self._clean_content)
        self._clean_button.set_sensitive(False)
        self._clean_button.connect("clicked", lambda *_: self._confirm_clean())
        bar.append(self._clean_button)
        self.add_bottom_bar(bar)

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        latest = self.services.last_previews
        if latest and latest != self._previews:
            self._previews = dict(latest)
            self._did_scan = True
            self._apply_previews(self._previews, animate=False)
        if not self._did_scan and not self._busy:
            self.scan()

    def scan(self) -> None:
        """Pindai semua kategori di thread pekerja."""
        if self._busy:
            return
        self._busy = True
        self._scan_button.set_sensitive(False)
        self._progress_card.set_visible(True)
        self._progress_label.set_text("Memindai semua kategori…")
        self._progress_bar.set_fraction(0.0)
        self._clean_button.set_sensitive(False)
        category_ids = [category.id for category in CATEGORIES]

        def progress_callback(category_id: str, done: int, total: int) -> None:
            GLib.idle_add(self._on_progress, category_id, done, total)

        def work() -> dict[str, Preview]:
            return self.services.assistant.scan(category_ids, progress=progress_callback)

        def done(previews: dict[str, Preview]) -> None:
            self._busy = False
            self._did_scan = True
            self._scan_button.set_sensitive(True)
            self._progress_card.set_visible(False)
            self._previews = previews
            self.services.last_previews = previews
            self._apply_previews(previews, animate=True)
            total = sum(preview.total_bytes for preview in previews.values())
            self.window.toast(f"Pemindaian selesai · {format_bytes(total)} bisa dibebaskan")

        def failed(exc: Exception) -> None:
            self._busy = False
            self._scan_button.set_sensitive(True)
            self._progress_card.set_visible(False)
            self.window.toast(f"Pemindaian gagal: {exc}")

        run_async(work, done, failed)

    def _on_progress(self, category_id: str, done: int, total: int) -> None:
        category = CATEGORY_MAP.get(category_id)
        label_text = category.label if category else category_id
        self._progress_label.set_text(f"Memindai {done}/{total} · {label_text}")
        self._progress_bar.set_fraction(done / total if total else 0.0)
        return False

    def _clear_expander(self, category_id: str) -> None:
        row = self._rows.get(category_id)
        if row is None:
            return
        for widget in self._detail_rows.pop(category_id, []):
            try:
                row.remove(widget)
            except (TypeError, AttributeError):
                pass

    def _apply_previews(self, previews: dict[str, Preview], *, animate: bool) -> None:
        for category_id, preview in previews.items():
            row = self._rows.get(category_id)
            switch = self._switches.get(category_id)
            if row is None or switch is None:
                continue
            self._clear_expander(category_id)
            added: list[Gtk.Widget] = []
            size_label = self._size_labels[category_id]
            supported = preview.supported and preview.item_count > 0
            switch.set_sensitive(supported)
            if not supported and switch.get_active():
                switch.set_active(False)
            size_label.set_text(format_bytes(preview.total_bytes) if preview.supported else "Tidak tersedia")
            notes = list(preview.notes)
            for error in preview.errors:
                notes.append(f"Gagal: {error}")
            for note in notes:
                note_row = action_row(note)
                note_row.add_prefix(icon("dialog-information-symbolic", 16, css="uc-nav-icon"))
                note_row.set_activatable(False)
                row.add_row(note_row)
                added.append(note_row)
            for item in preview.items[:MAX_DETAIL_ROWS]:
                item_row = action_row(item.label, item.detail)
                if item.protected:
                    item_row.add_prefix(icon("system-lock-screen-symbolic", 16, css="uc-subtle"))
                item_row.add_suffix(label(format_bytes(item.size), css="uc-subtle"))
                item_row.set_activatable(False)
                row.add_row(item_row)
                added.append(item_row)
            if len(preview.items) > MAX_DETAIL_ROWS:
                more = action_row(
                    f"… dan {len(preview.items) - MAX_DETAIL_ROWS} item lainnya"
                )
                more.set_activatable(False)
                row.add_row(more)
                added.append(more)
            self._detail_rows[category_id] = added
            if preview.supported and preview.notes:
                row.set_subtitle(f"{preview.item_count} item · {preview.notes[0]}")
            elif preview.supported:
                row.set_subtitle(f"{preview.item_count} item")
            else:
                row.set_subtitle(preview.errors[0] if preview.errors else "Tidak tersedia")
        for category_id, switch in self._switches.items():
            if category_id not in previews:
                if switch.get_active():
                    switch.set_active(False)
                switch.set_sensitive(False)
        self._update_total(animate=animate)

    def _selected_previews(self) -> dict[str, Preview]:
        selected: dict[str, Preview] = {}
        for category_id, preview in self._previews.items():
            switch = self._switches.get(category_id)
            if switch is not None and switch.get_active() and preview.supported:
                selected[category_id] = preview
        return selected

    def _update_total(self, *, animate: bool = False) -> None:
        selected = self._selected_previews()
        total = sum(preview.total_bytes for preview in selected.values())
        items = sum(preview.item_count for preview in selected.values())
        if not selected:
            self._total_label.set_text("Belum ada item dipilih")
            self._detail_label.set_text("Aktifkan kategori untuk mulai membersihkan.")
        else:
            if animate:
                self._total_anim.set_value(total)
            else:
                self._total_label.set_text(f"{format_bytes(total)} bisa dibebaskan")
            self._detail_label.set_text(
                f"{len(selected)} kategori · {format_count(items)} item · "
                f"{'pratinjau tanpa perubahan' if self._dry_switch.get_active() else 'item aman masuk karantina'}"
            )
        self._clean_button.set_sensitive(bool(selected) and not self._busy)
        label_text = f"Bersihkan {format_bytes(total)}" if selected and total else "Bersihkan"
        if self._dry_switch.get_active():
            label_text = "Pratinjau hasil"
        self._clean_content.set_label(label_text)

    def _confirm_clean(self) -> None:
        selected = self._selected_previews()
        if not selected or self._busy:
            return
        total = sum(preview.total_bytes for preview in selected.values())
        dry_run = self._dry_switch.get_active()
        body = (
            f"{format_bytes(total)} dari {len(selected)} kategori akan diproses. "
            "Item dari kategori pengguna dipindahkan ke karantina dan bisa dipulihkan."
        )
        if dry_run:
            body = (
                "Mode pratinjau aktif: tidak ada file yang dihapus. "
                f"UbuClean akan menampilkan hasil untuk {format_bytes(total)}."
            )
        ask_confirm(
            self,
            heading="Jalankan pratinjau?" if dry_run else f"Bersihkan {format_bytes(total)}?",
            body=body,
            confirm_label="Jalankan pratinjau" if dry_run else "Bersihkan sekarang",
            destructive=not dry_run,
            on_confirm=lambda: self._run_clean(selected, dry_run),
        )

    def _run_clean(self, selected: dict[str, Preview], dry_run: bool) -> None:
        self._busy = True
        self._clean_button.set_sensitive(False)
        self._scan_button.set_sensitive(False)
        self._progress_card.set_visible(True)
        self._progress_label.set_text("Membersihkan…")
        self._progress_bar.set_pulse_step(0.15)
        self._progress_bar.pulse()

        pulse_id = GLib.timeout_add(120, self._pulse)

        def work() -> CleanReport:
            return self.services.assistant.clean(selected, dry_run=dry_run)

        def done(report: CleanReport) -> None:
            GLib.source_remove(pulse_id)
            self._busy = False
            self._scan_button.set_sensitive(True)
            self._progress_card.set_visible(False)
            self.services.last_report = report
            show_clean_result(
                self,
                report,
                dry_run=dry_run,
                on_undo=lambda: self._undo(report),
                on_history=lambda: self.window.show_page("history"),
                on_rescan=self.scan,
            )
            if not dry_run:
                self._mark_cleaned(selected)
            quarantined = any(outcome.quarantine_id for outcome in report.outcomes)
            self.window.toast(
                f"{'Pratinjau' if dry_run else 'Selesai'} · {format_bytes(report.freed_bytes)}",
                action_label="Urungkan" if quarantined and not dry_run else None,
                action=(lambda: self._undo(report)) if quarantined and not dry_run else None,
                timeout=6,
            )

        def failed(exc: Exception) -> None:
            GLib.source_remove(pulse_id)
            self._busy = False
            self._scan_button.set_sensitive(True)
            self._progress_card.set_visible(False)
            self._update_total()
            self.window.toast(f"Pembersihan gagal: {exc}")

        run_async(work, done, failed)

    def _pulse(self) -> bool:
        if not self._busy:
            return False
        self._progress_bar.pulse()
        return True

    def _mark_cleaned(self, selected: dict[str, Preview]) -> None:
        for category_id in selected:
            row = self._rows.get(category_id)
            switch = self._switches.get(category_id)
            size_label = self._size_labels.get(category_id)
            if row is None or switch is None:
                continue
            self._clear_expander(category_id)
            done_row = Adw.ActionRow(title="Sudah dibersihkan")
            done_row.add_prefix(icon("object-select-symbolic", 16, css="uc-success-text"))
            done_row.set_activatable(False)
            row.add_row(done_row)
            self._detail_rows[category_id] = [done_row]
            switch.set_active(False)
            switch.set_sensitive(False)
            if size_label is not None:
                size_label.set_text("0 B")
        self._previews = {
            category_id: preview
            for category_id, preview in self._previews.items()
            if category_id not in selected
        }
        self.services.last_previews = {
            category_id: preview
            for category_id, preview in self.services.last_previews.items()
            if category_id not in selected
        }
        self._update_total()

    def _undo(self, report: CleanReport) -> None:
        action_ids = [outcome.quarantine_id for outcome in report.outcomes if outcome.quarantine_id]

        def work() -> list[Any]:
            return [
                self.services.context.quarantine.restore(action_id)
                for action_id in action_ids
            ]

        def done(results: list[Any]) -> None:
            restored = sum(result.restored for result in results)
            failed = sum(result.failed for result in results)
            restored_bytes = sum(result.bytes for result in results)
            errors = [error for result in results for error in result.errors]
            try:
                self.services.context.history.record(
                    HistoryEntry(
                        action="restore",
                        category="quarantine",
                        label="Urungkan pembersihan",
                        items=restored,
                        bytes=restored_bytes,
                        ok=failed == 0,
                        detail=f"{restored} item dipulihkan, {failed} gagal.",
                        errors=errors,
                    )
                )
            except OSError:
                pass
            self.window.toast(
                f"{restored} item dipulihkan dari karantina"
                if not errors
                else f"Sebagian gagal dipulihkan: {errors[0]}"
            )
            if restored:
                self.scan()

        run_async(work, done, lambda exc: self.window.toast(f"Pemulihan gagal: {exc}"))

    def _open_uninstaller(self) -> None:
        from ubuclean.ui.pages.uninstaller import show_uninstaller

        show_uninstaller(self, self.window)


# ---------------------------------------------------------------------------
def show_clean_result(
    parent: Gtk.Widget,
    report: CleanReport,
    *,
    dry_run: bool,
    on_undo: Any,
    on_history: Any,
    on_rescan: Any,
) -> None:
    """Dialog hasil yang menampilkan rincian dan aksi lanjutan."""
    dialog = Adw.Dialog()
    dialog.set_title("Hasil pembersihan" if not dry_run else "Pratinjau pembersihan")
    dialog.set_content_width(560)
    dialog.set_content_height(660)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header.set_title_widget(
        Adw.WindowTitle(
            title="Hasil pembersihan" if not dry_run else "Pratinjau pembersihan",
            subtitle=format_date(time.time(), with_time=True),
        )
    )
    close_button = icon_button("window-close-symbolic", "Tutup")
    close_button.connect("clicked", lambda *_: dialog.close())
    header.pack_end(close_button)
    toolbar.add_top_bar(header)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    clamp = Adw.Clamp()
    clamp.set_maximum_size(520)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    content.set_margin_top(20)
    content.set_margin_bottom(24)
    content.set_margin_start(20)
    content.set_margin_end(20)

    hero = card(spacing=6, css=("uc-result",))
    hero.set_halign(Gtk.Align.FILL)
    big = label("", css="uc-total")
    animator = AnimatedNumber(parent, big, formatter=format_bytes, duration=300)
    animator.set_value(report.freed_bytes)
    hero.append(big)
    hero.append(
        label(
            "Berhasil dibebaskan"
            if not dry_run
            else "Pratinjau: tidak ada perubahan pada sistem",
            css="uc-subtle",
        )
    )
    quarantined = sum(outcome.quarantined_bytes for outcome in report.outcomes)
    if quarantined and not dry_run:
        hero.append(
            label(
                f"{format_bytes(quarantined)} dipindahkan ke karantina dan bisa dipulihkan.",
                css="uc-subtle",
                wrap=True,
            )
        )
    content.append(hero)

    details = Adw.PreferencesGroup(title="Rincian per kategori")
    for outcome in report.outcomes:
        category = CATEGORY_MAP.get(outcome.category_id)
        title = category.label if category else outcome.category_id
        row = Adw.ActionRow(
            title=title,
            subtitle=(
                f"{outcome.removed} item"
                + (f" · {outcome.failed} gagal" if outcome.failed else "")
                + (f" · {format_bytes(outcome.quarantined_bytes)} di karantina" if outcome.quarantined else "")
            ),
        )
        if outcome.ok:
            row.add_prefix(icon("object-select-symbolic", 16, css="uc-success-text"))
        else:
            row.add_prefix(icon("dialog-warning-symbolic", 16, css="uc-warning-text"))
            for error in outcome.errors[:2]:
                row.set_tooltip_text(error)
        row.add_suffix(label(format_bytes(outcome.freed_bytes), css="uc-metric-compact"))
        details.add(row)
    content.append(details)

    if report.errors:
        error_group = Adw.PreferencesGroup(title="Catatan")
        for error in report.errors[:8]:
            row = Adw.ActionRow(title=error)
            row.set_activatable(False)
            error_group.add(row)
        content.append(error_group)

    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    actions.set_halign(Gtk.Align.END)
    history_button = button("Lihat riwayat")
    history_button.connect("clicked", lambda *_: (dialog.close(), on_history()))
    actions.append(history_button)
    if quarantined and not dry_run:
        undo_button = button("Urungkan", icon_name="edit-undo-symbolic")
        undo_button.connect("clicked", lambda *_: (dialog.close(), on_undo()))
        actions.append(undo_button)
    rescan_button = button("Pindai ulang", icon_name="view-refresh-symbolic")
    rescan_button.connect("clicked", lambda *_: (dialog.close(), on_rescan()))
    actions.append(rescan_button)
    close = button("Selesai", suggested=True)
    close.connect("clicked", lambda *_: dialog.close())
    actions.append(close)
    content.append(actions)

    clamp.set_child(content)
    scrolled.set_child(clamp)
    toolbar.set_content(scrolled)
    dialog.set_child(toolbar)
    dialog.present(parent)
