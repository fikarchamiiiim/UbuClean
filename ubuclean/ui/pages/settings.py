"""Halaman Pengaturan UbuClean."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk

from ubuclean.core.notifier_service import NotifierService
from ubuclean.ui.common import action_row, button, clear_group, icon, icon_button, run_async
from ubuclean.ui.pages.base import BasePage


class SettingsPage(BasePage):
    """Preferensi karantina, pembersihan, developer, dan notifikasi."""

    def __init__(self, window: Any) -> None:
        super().__init__("Pengaturan", "Preferensi pembersihan dan layanan latar", maximum_width=860)
        self.window = window
        self.services = window.services
        self.notifier_service = NotifierService(
            self.services.paths.home, self.services.context.runner
        )
        self._loading = False
        self._watcher_busy = False

        self.body.append(self._cleaning_group())
        self._developer_group = self._developer_group_widget()
        self.body.append(self._developer_group)
        self.body.append(self._applications_group())
        self.body.append(self._notification_group())
        self.body.append(self._about_group())

    # ------------------------------------------------------------------
    def _spin(
        self,
        title: str,
        subtitle: str,
        value: int,
        minimum: int,
        maximum: int,
        callback: Callable[[int], None],
    ) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(minimum, maximum, 1)
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_digits(0)
        row.set_value(float(value))
        row.connect("notify::value", lambda widget, _pspec: self._on_spin(widget, callback))
        return row

    def _on_spin(self, row: Adw.SpinRow, callback: Callable[[int], None]) -> None:
        if self._loading:
            return
        callback(int(row.get_value()))

    def _update(self, patch: dict[str, Any]) -> None:
        self.services.config.update(patch)
        self.services.save_config()

    def _cleaning_group(self) -> Adw.PreferencesGroup:
        config = self.services.config
        group = Adw.PreferencesGroup(
            title="Pembersihan",
            description="Nilai ini dipakai saat memindai dan membersihkan.",
        )
        group.add(
            self._spin(
                "Umur file /tmp",
                "Hanya file yang tidak diubah lebih lama dari ini yang dihapus.",
                int(config.get("tmp_days", 7)),
                1,
                365,
                lambda value: self._update({"tmp_days": value}),
            )
        )
        group.add(
            self._spin(
                "Umur log journald",
                "Batas journalctl --vacuum-time.",
                int(config.get("journal_days", 14)),
                1,
                365,
                lambda value: self._update({"journal_days": value}),
            )
        )
        group.add(
            self._spin(
                "Retensi karantina",
                "Item dihapus permanen setelah melewati umur ini.",
                int(config.get("quarantine_days", 7)),
                1,
                365,
                lambda value: self._update({"quarantine_days": value}),
            )
        )
        group.add(
            self._spin(
                "Umur proyek developer",
                "Folder build dipindahkan hanya jika proyek lama tidak disentuh.",
                int(config.get("developer_days", 90)),
                1,
                3650,
                lambda value: self._update({"developer_days": value}),
            )
        )
        self._level_row = Adw.ComboRow(
            title="Level drop caches",
            subtitle="Nilai awal untuk halaman Memori.",
        )
        self._level_row.set_model(
            Gtk.StringList.new(
                [
                    "Level 1 — page cache",
                    "Level 2 — page cache + dentries/inodes",
                    "Level 3 — semua cache",
                ]
            )
        )
        self._level_row.set_selected(
            max(0, min(2, int(config.get("drop_caches_level", 2)) - 1))
        )
        self._level_row.connect("notify::selected", self._on_level_changed)
        group.add(self._level_row)
        return group

    def _on_level_changed(self, row: Adw.ComboRow, _pspec: Any) -> None:
        if self._loading:
            return
        self._update({"drop_caches_level": int(row.get_selected()) + 1})

    def _developer_group_widget(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Folder proyek developer",
            description=(
                "Folder induk yang dipindai untuk node_modules, __pycache__, target/, dan .venv. "
                "Hanya folder di dalam home yang bisa dikarantina."
            ),
        )
        return group

    def _refresh_developer_roots(self) -> None:
        clear_group(self._developer_group)
        roots = list(self.services.config.get("developer_roots", []))
        for root in roots:
            row = action_row(str(root), "Folder induk proyek")
            row.add_prefix(icon("folder-symbolic", 16, css="uc-nav-icon"))
            remove = icon_button("edit-delete-symbolic", f"Hapus {root} dari daftar")
            remove.set_valign(Gtk.Align.CENTER)
            remove.connect("clicked", lambda _button, value=root: self._remove_root(value))
            row.add_suffix(remove)
            self._developer_group.add(row)
        add_row = Adw.ActionRow(
            title="Tambah folder",
            subtitle="Pilih folder induk tempat proyek kamu berada.",
        )
        add_button = button("Pilih folder", icon_name="folder-open-symbolic", css=("flat",))
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.connect("clicked", lambda *_: self._choose_root())
        add_row.add_suffix(add_button)
        self._developer_group.add(add_row)

    def _choose_root(self) -> None:
        dialog = Gtk.FileDialog(title="Pilih folder induk proyek", modal=True)
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(str(self.services.paths.home)))
        except (AttributeError, GLib.Error):
            pass

        def callback(source: Gtk.FileDialog, result: Any) -> None:
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is None or not folder.get_path():
                return
            path = Path(folder.get_path())
            home = self.services.paths.home.resolve()
            try:
                resolved = path.resolve(strict=False)
            except OSError:
                return
            if home not in resolved.parents and resolved != home:
                self.window.toast("Pilih folder di dalam direktori home agar bisa dikarantina.")
                return
            roots = [str(item) for item in self.services.config.get("developer_roots", [])]
            text = str(resolved)
            if text not in roots:
                roots.append(text)
                self._update({"developer_roots": roots})
            self._refresh_developer_roots()

        dialog.select_folder(self, None, callback)

    def _remove_root(self, value: str) -> None:
        roots = [
            str(item)
            for item in self.services.config.get("developer_roots", [])
            if str(item) != str(value)
        ]
        self._update({"developer_roots": roots})
        self._refresh_developer_roots()

    def _applications_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Aplikasi terpasang",
            description="Uninstaller apt, snap, dan flatpak dengan pembersihan sisa konfigurasi.",
        )
        row = Adw.ActionRow(
            title="Kelola aplikasi terpasang",
            subtitle="Cari aplikasi, lihat ukuran, dan hapus dengan pratinjau sisa data.",
        )
        row.add_prefix(icon("application-x-executable-symbolic", 16, css="uc-nav-icon"))
        open_button = button("Buka", icon_name="system-search-symbolic", css=("flat",))
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.connect("clicked", lambda *_: self._open_uninstaller())
        row.add_suffix(open_button)
        group.add(row)
        return group

    def _open_uninstaller(self) -> None:
        from ubuclean.ui.pages.uninstaller import show_uninstaller

        show_uninstaller(self, self.window)

    def _notification_group(self) -> Adw.PreferencesGroup:
        config = self.services.config
        notifications = config.notifications
        group = Adw.PreferencesGroup(
            title="Notifikasi pintar",
            description="Ambang dan cooldown untuk pemantau latar.",
        )
        self._notif_enabled = Adw.SwitchRow(
            title="Notifikasi desktop",
            subtitle="Kirim peringatan saat disk atau RAM melewati ambang.",
        )
        self._notif_enabled.set_active(bool(notifications.get("enabled", True)))
        self._notif_enabled.connect("notify::active", self._on_notif_enabled)
        group.add(self._notif_enabled)

        self._notif_muted = Adw.SwitchRow(
            title="Bisukan sementara",
            subtitle="Tidak mengirim notifikasi sampai dimatikan lagi.",
        )
        self._notif_muted.set_active(bool(notifications.get("muted", False)))
        self._notif_muted.connect("notify::active", self._on_notif_muted)
        group.add(self._notif_muted)

        group.add(
            self._spin(
                "Ambang disk",
                "Persentase pemakaian disk untuk memicu notifikasi.",
                int(notifications.get("disk_threshold", 90)),
                50,
                99,
                lambda value: self._update({"notifications": {"disk_threshold": value}}),
            )
        )
        group.add(
            self._spin(
                "Ambang RAM",
                "Persentase RAM untuk memicu notifikasi.",
                int(notifications.get("ram_threshold", 90)),
                50,
                99,
                lambda value: self._update({"notifications": {"ram_threshold": value}}),
            )
        )
        group.add(
            self._spin(
                "Durasi RAM tinggi",
                "RAM harus melewati ambang selama ini sebelum notifikasi dikirim.",
                int(notifications.get("ram_sustained_minutes", 5)),
                1,
                1440,
                lambda value: self._update({"notifications": {"ram_sustained_minutes": value}}),
            )
        )
        group.add(
            self._spin(
                "Jeda antar notifikasi",
                "Jeda minimum antar notifikasi dengan jenis yang sama.",
                int(notifications.get("cooldown_hours", 6)),
                1,
                168,
                lambda value: self._update({"notifications": {"cooldown_hours": value}}),
            )
        )
        self._watcher_row = Adw.SwitchRow(
            title="Pemantau latar",
            subtitle="Memeriksa disk dan RAM setiap menit lewat systemd user service.",
        )
        self._watcher_row.set_active(bool(notifications.get("watcher_enabled", False)))
        self._watcher_row.connect("notify::active", self._on_watcher_toggled)
        group.add(self._watcher_row)
        return group

    def _on_notif_enabled(self, row: Adw.SwitchRow, _pspec: Any) -> None:
        if self._loading:
            return
        self._update({"notifications": {"enabled": bool(row.get_active())}})

    def _on_notif_muted(self, row: Adw.SwitchRow, _pspec: Any) -> None:
        if self._loading:
            return
        self._update({"notifications": {"muted": bool(row.get_active())}})

    def _on_watcher_toggled(self, row: Adw.SwitchRow, _pspec: Any) -> None:
        if self._loading or self._watcher_busy:
            return
        desired = bool(row.get_active())
        self._watcher_busy = True
        row.set_sensitive(False)

        def work() -> tuple[bool, str]:
            if desired:
                return self.notifier_service.install()
            return self.notifier_service.uninstall()

        def done(result: tuple[bool, str]) -> None:
            self._watcher_busy = False
            row.set_sensitive(True)
            ok, message = result
            if not ok:
                self._loading = True
                row.set_active(not desired)
                self._loading = False
                self.window.toast(message)
                return
            self._update({"notifications": {"watcher_enabled": desired}})
            self.window.toast(message)

        def failed(exc: Exception) -> None:
            self._watcher_busy = False
            row.set_sensitive(True)
            self._loading = True
            row.set_active(not desired)
            self._loading = False
            self.window.toast(f"Gagal mengatur pemantau: {exc}")

        run_async(work, done, failed)

    def _about_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Bantuan dan data")
        shortcuts_row = Adw.ActionRow(
            title="Pintasan keyboard",
            subtitle="Ctrl+R pindai · Ctrl+, pengaturan · F1 bantuan",
        )
        shortcuts_button = button("Buka", css=("flat",))
        shortcuts_button.set_valign(Gtk.Align.CENTER)
        shortcuts_button.connect("clicked", lambda *_: self.window.show_help())
        shortcuts_row.add_suffix(shortcuts_button)
        group.add(shortcuts_row)

        log_row = Adw.ActionRow(
            title="Lokasi log",
            subtitle=str(self.services.paths.log_file),
        )
        log_row.set_activatable(False)
        group.add(log_row)

        about_row = Adw.ActionRow(
            title="Tentang UbuClean",
            subtitle="Versi, lisensi, dan tautan proyek.",
        )
        about_button = button("Tentang", css=("flat",))
        about_button.set_valign(Gtk.Align.CENTER)
        about_button.connect("clicked", lambda *_: self.window.show_about())
        about_row.add_suffix(about_button)
        group.add(about_row)
        return group

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self._loading = True
        config = self.services.config
        notifications = config.notifications
        self._notif_enabled.set_active(bool(notifications.get("enabled", True)))
        self._notif_muted.set_active(bool(notifications.get("muted", False)))
        self._watcher_row.set_active(bool(notifications.get("watcher_enabled", False)))
        self._level_row.set_selected(
            max(0, min(2, int(config.get("drop_caches_level", 2)) - 1))
        )
        self._loading = False
        self._refresh_developer_roots()
