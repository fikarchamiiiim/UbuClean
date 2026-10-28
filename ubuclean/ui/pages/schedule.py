"""Halaman Jadwal pembersihan otomatis."""

from __future__ import annotations

import re
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk

from ubuclean.core.registry import CATEGORIES
from ubuclean.ui.common import (
    ask_confirm,
    button,
    card,
    icon,
    icon_button,
    label,
    run_async,
    show_info,
)
from ubuclean.ui.pages.base import BasePage

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FREQUENCY_LABELS = ["Harian", "Mingguan", "Bulanan"]
FREQUENCY_VALUES = ["daily", "weekly", "monthly"]


class SchedulePage(BasePage):
    """Membuat dan menghapus systemd user timer dari GUI."""

    def __init__(self, window: Any) -> None:
        super().__init__("Jadwal", "Pembersihan otomatis via systemd user timer", maximum_width=860)
        self.window = window
        self.services = window.services
        self._busy = False
        self._loading = False
        self._category_switches: dict[str, Adw.SwitchRow] = {}

        refresh = icon_button("view-refresh-symbolic", "Muat ulang status jadwal")
        refresh.connect("clicked", lambda *_: self._refresh_status())
        self.add_action(refresh)

        self._status_card = card(spacing=8, css=("uc-card-quiet",))
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        status_row.append(icon("alarm-symbolic", 22, css="uc-nav-icon"))
        status_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self._status_label = label("Memeriksa jadwal…", css="uc-section")
        self._status_detail = label("", css="uc-subtle", wrap=True)
        status_text.append(self._status_label)
        status_text.append(self._status_detail)
        status_row.append(status_text)
        self._status_card.append(status_row)
        self.body.append(self._status_card)

        self.body.append(self._form_group())
        self.body.append(self._category_group())
        self.body.append(self._actions())
        self.body.append(self._info_card())

    # ------------------------------------------------------------------
    def _form_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Frekuensi",
            description="Timer berjalan sebagai systemd user service, tanpa hak root.",
        )
        self._enabled_row = Adw.SwitchRow(
            title="Aktifkan pembersihan terjadwal",
            subtitle="Timer akan dibuat dan diaktifkan saat disimpan.",
        )
        group.add(self._enabled_row)
        self._frequency_row = Adw.ComboRow(title="Frekuensi")
        self._frequency_row.set_model(Gtk.StringList.new(FREQUENCY_LABELS))
        group.add(self._frequency_row)
        self._time_row = Adw.EntryRow(title="Jam (HH:MM)")
        group.add(self._time_row)
        return group

    def _category_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Kategori yang dibersihkan",
            description=(
                "Kernel lama dan cache browser tidak pernah ikut jadwal otomatis. "
                "Kategori yang butuh root akan dilewati saat headless."
            ),
        )
        for category in CATEGORIES:
            if not category.schedule_allowed or category.risky:
                continue
            row = Adw.SwitchRow(title=category.label, subtitle=category.description)
            self._category_switches[category.id] = row
            group.add(row)
        return group

    def _actions(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_halign(Gtk.Align.END)
        remove = button("Hapus jadwal", icon_name="edit-delete-symbolic", destructive=True)
        remove.connect("clicked", lambda *_: self._remove_schedule())
        save = button("Simpan & aktifkan", icon_name="object-select-symbolic", suggested=True)
        save.connect("clicked", lambda *_: self._save_schedule())
        box.append(remove)
        box.append(save)
        return box

    def _info_card(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("uc-info")
        box.append(icon("dialog-information-symbolic", 20, css="uc-nav-icon"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label("Mode headless", css="uc-section"))
        text.append(
            label(
                "Timer menjalankan `ubuclean --run-scheduled` tanpa membuka GUI. "
                "Hasil dicatat ke riwayat dan notifikasi desktop.",
                css="uc-subtle",
                wrap=True,
            )
        )
        box.append(text)
        return box

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self._load_config()
        self._refresh_status()

    def _load_config(self) -> None:
        schedule = self.services.config.schedule
        self._loading = True
        for category_id, row in self._category_switches.items():
            row.set_active(category_id in schedule.get("categories", []))
        self._enabled_row.set_active(bool(schedule.get("enabled", False)))
        try:
            index = FREQUENCY_VALUES.index(str(schedule.get("frequency", "weekly")))
        except ValueError:
            index = 1
        self._frequency_row.set_selected(index)
        self._time_row.set_text(str(schedule.get("time", "09:00")))
        self._loading = False

    def _refresh_status(self) -> None:
        if self._busy:
            return

        def work() -> Any:
            return self.services.scheduler.status()

        def done(status: Any) -> None:
            self._status_label.set_text("Timer aktif" if status.active else "Jadwal belum aktif")
            self._status_detail.set_text(status.message)

        def failed(exc: Exception) -> None:
            self._status_detail.set_text(f"Tidak bisa membaca status: {exc}")

        run_async(work, done, failed)

    def _collect(self) -> tuple[bool, str, str, list[str]]:
        enabled = self._enabled_row.get_active()
        index = int(self._frequency_row.get_selected())
        frequency = FREQUENCY_VALUES[index] if 0 <= index < len(FREQUENCY_VALUES) else "weekly"
        time_text = self._time_row.get_text().strip()
        categories = [
            category_id
            for category_id, row in self._category_switches.items()
            if row.get_active()
        ]
        return enabled, frequency, time_text, categories

    def _save_schedule(self) -> None:
        if self._busy:
            return
        enabled, frequency, time_text, categories = self._collect()
        if not TIME_RE.fullmatch(time_text):
            show_info(self, heading="Format jam tidak valid", body="Gunakan format 24 jam HH:MM, contoh 09:30.")
            return
        if enabled and not categories:
            show_info(self, heading="Pilih kategori", body="Aktifkan minimal satu kategori untuk dijadwalkan.")
            return
        self.services.config.update(
            {
                "schedule": {
                    "enabled": enabled,
                    "frequency": frequency,
                    "time": time_text,
                    "categories": categories,
                }
            }
        )
        self.services.save_config()
        if not enabled:
            self._remove_schedule()
            return
        self._busy = True

        def work() -> tuple[bool, str]:
            return self.services.scheduler.install(frequency, time_text)

        def done(result: tuple[bool, str]) -> None:
            self._busy = False
            ok, message = result
            self.window.toast(message)
            if ok:
                self._refresh_status()
            else:
                show_info(self, heading="Jadwal gagal dibuat", body=message)

        def failed(exc: Exception) -> None:
            self._busy = False
            show_info(self, heading="Jadwal gagal dibuat", body=str(exc))

        run_async(work, done, failed)

    def _remove_schedule(self) -> None:
        if self._busy:
            return

        def do_remove() -> None:
            self._busy = True
            self.services.config.update({"schedule": {"enabled": False}})
            self.services.save_config()

            def work() -> tuple[bool, str]:
                return self.services.scheduler.uninstall()

            def done(result: tuple[bool, str]) -> None:
                self._busy = False
                self.window.toast(result[1])
                self._refresh_status()

            def failed(exc: Exception) -> None:
                self._busy = False
                self.window.toast(f"Gagal menghapus jadwal: {exc}")

            run_async(work, done, failed)

        ask_confirm(
            self,
            heading="Hapus jadwal pembersihan?",
            body="Timer systemd milik UbuClean akan dinonaktifkan dan dihapus. Konfigurasi kategori tetap disimpan.",
            confirm_label="Hapus jadwal",
            destructive=True,
            on_confirm=do_remove,
        )
