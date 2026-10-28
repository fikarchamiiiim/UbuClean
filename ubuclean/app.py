"""Aplikasi GTK UbuClean."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio

from ubuclean import constants
from ubuclean.ui.common import run_async
from ubuclean.ui.services import Services
from ubuclean.ui.window import MainWindow


class UbuCleanApplication(Adw.Application):
    """Adw.Application dengan satu jendela utama."""

    def __init__(self, initial_page: str = "home") -> None:
        super().__init__(application_id=constants.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.services = Services()
        self._initial_page = initial_page
        self._window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        from ubuclean.ui.common import load_stylesheet

        load_stylesheet()
        self.set_accels_for_action("app.quit", ["<primary>q"])
        run_async(
            self.services.purge_expired_quarantine,
            lambda _removed: None,
            lambda _error: None,
        )

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(
                application=self,
                services=self.services,
                initial_page=self._initial_page,
            )
        self._window.present()


def run_gui(argv: list[str] | None = None, *, page: str = "home") -> int:
    """Jalankan GUI; menolak eksekusi sebagai root."""
    del argv
    if os.geteuid() == 0:
        print(
            "UbuClean GUI tidak boleh dijalankan sebagai root. "
            "Jalankan sebagai pengguna biasa; operasi sistem akan memakai pkexec.",
            file=sys.stderr,
        )
        return 1
    application = UbuCleanApplication(initial_page=page)
    return int(application.run(["ubuclean"]))
