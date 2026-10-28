"""Halaman pengelola startup aplikasi dan user service."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk

from ubuclean.core.startup import StartupEntry
from ubuclean.core.util import format_count
from ubuclean.ui.common import (
    action_row,
    clear_group,
    expander_row,
    icon,
    icon_button,
    label,
    run_async,
)
from ubuclean.ui.pages.base import BasePage


class StartupPage(BasePage):
    """Autostart XDG dan user service systemd dengan toggle aman."""

    def __init__(self, window: Any) -> None:
        super().__init__("Startup", "Aplikasi yang berjalan saat login", maximum_width=900)
        self.window = window
        self.services = window.services
        self._entries: list[StartupEntry] = []
        self._busy = False
        self._toggling = False

        refresh = icon_button("view-refresh-symbolic", "Muat ulang daftar startup")
        refresh.connect("clicked", lambda *_: self.refresh())
        self.add_action(refresh)

        self._info = label("", css="uc-subtle", wrap=True)
        info_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        info_card.add_css_class("uc-info")
        info_card.append(icon("dialog-information-symbolic", 20, css="uc-nav-icon"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label("Nonaktifkan tanpa menghapus", css="uc-section"))
        text.append(
            label(
                "Entri sistem dinonaktifkan lewat override di ~/.config/autostart, "
                "sedangkan berkas asli di /etc/xdg/autostart tidak disentuh.",
                css="uc-subtle",
                wrap=True,
            )
        )
        info_card.append(text)
        self.body.append(info_card)
        self._count_label = label("Memuat daftar startup…", css="uc-subtle")
        self.body.append(self._count_label)
        self._desktop_group = Adw.PreferencesGroup(
            title="Aplikasi autostart",
            description="Entri dari ~/.config/autostart dan /etc/xdg/autostart.",
        )
        self._systemd_group = Adw.PreferencesGroup(
            title="User service systemd",
            description="Service pengguna yang aktif saat login.",
        )
        self.body.append(self._desktop_group)
        self.body.append(self._systemd_group)

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._count_label.set_text("Memuat daftar startup…")

        def work() -> list[StartupEntry]:
            return self.services.startup.list_entries()

        def done(entries: list[StartupEntry]) -> None:
            self._busy = False
            self._entries = entries
            self._populate(entries)

        def failed(exc: Exception) -> None:
            self._busy = False
            self._count_label.set_text("Gagal memuat daftar startup.")
            self.window.toast(f"Gagal memuat startup: {exc}")

        run_async(work, done, failed)

    def _clear_group(self, group: Adw.PreferencesGroup) -> None:
        clear_group(group)

    def _populate(self, entries: list[StartupEntry]) -> None:
        self._clear_group(self._desktop_group)
        self._clear_group(self._systemd_group)
        desktop = [entry for entry in entries if not entry.is_systemd]
        systemd = [entry for entry in entries if entry.is_systemd]
        if desktop:
            for entry in desktop:
                self._desktop_group.add(self._row(entry))
        else:
            self._desktop_group.add(self._empty_row("Tidak ada aplikasi autostart."))
        if systemd:
            for entry in systemd:
                self._systemd_group.add(self._row(entry))
        else:
            self._systemd_group.add(self._empty_row("Tidak ada user service yang aktif."))
        enabled = sum(1 for entry in entries if entry.enabled)
        self._count_label.set_text(
            f"{format_count(len(entries))} entri · {format_count(enabled)} aktif"
        )

    @staticmethod
    def _empty_row(text: str) -> Adw.ActionRow:
        row = action_row(text)
        row.set_activatable(False)
        return row

    def _row(self, entry: StartupEntry) -> Adw.ExpanderRow:
        row = expander_row(
            entry.name,
            entry.description or entry.command or entry.source,
        )
        row.add_prefix(
            icon(
                "application-x-executable-symbolic",
                16,
                css="uc-nav-icon",
            )
        )
        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=entry.enabled)
        switch.set_tooltip_text(f"Aktifkan/nonaktifkan {entry.name}")
        switch.connect("notify::active", lambda sw, _pspec, item=entry: self._on_toggle(sw, item))
        row.add_suffix(switch)
        source_row = action_row("Sumber", self._source_text(entry))
        source_row.set_activatable(False)
        row.add_row(source_row)
        command_row = action_row(
            "Perintah",
            entry.command or "Tidak ada perintah Exec",
        )
        command_row.set_activatable(False)
        row.add_row(command_row)
        if entry.path is not None:
            path_row = action_row("Berkas", str(entry.path))
            path_row.set_activatable(False)
            row.add_row(path_row)
        if entry.unit:
            unit_row = action_row("Unit systemd", entry.unit)
            unit_row.set_activatable(False)
            row.add_row(unit_row)
        return row

    @staticmethod
    def _source_text(entry: StartupEntry) -> str:
        if entry.is_systemd:
            return "systemd --user"
        if entry.is_system:
            return "sistem" + (" · override pengguna" if entry.overridden else "")
        return "pengguna"

    def _on_toggle(self, switch: Gtk.Switch, entry: StartupEntry) -> None:
        if self._toggling:
            return
        desired = bool(switch.get_active())
        if desired == entry.enabled:
            return
        self._toggling = True
        switch.set_sensitive(False)
        original = entry.enabled

        def work() -> tuple[bool, str]:
            return self.services.startup.set_enabled(entry, desired)

        def done(result: tuple[bool, str]) -> None:
            self._toggling = False
            ok, message = result
            switch.set_sensitive(True)
            if not ok:
                switch.set_active(original)
                self.window.toast(message)
                return
            self.window.toast(message)
            self.refresh()

        def failed(exc: Exception) -> None:
            self._toggling = False
            switch.set_sensitive(True)
            switch.set_active(original)
            self.window.toast(f"Gagal mengubah startup: {exc}")

        run_async(work, done, failed)
