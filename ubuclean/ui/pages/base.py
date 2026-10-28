"""Page skeleton with header, clamp, and scroll area."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk


class BasePage(Gtk.Box):
    """Standard page: HeaderBar + scrolling content inside Adw.Clamp.

    Adw.ToolbarView is final in libadwaita 1.5, so pages use composition
    and expose ``header``/``body`` to subclasses.
    """

    def __init__(
        self,
        title: str,
        subtitle: str | None = None,
        *,
        maximum_width: int = 900,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar = Adw.ToolbarView()
        self.append(self.toolbar)
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Adw.WindowTitle(title=title, subtitle=subtitle))
        self.toolbar.add_top_bar(self.header)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_vexpand(True)
        self.clamp = Adw.Clamp()
        self.clamp.set_maximum_size(maximum_width)
        self.clamp.set_tightening_threshold(640)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.body.set_margin_top(24)
        self.body.set_margin_bottom(28)
        self.body.set_margin_start(24)
        self.body.set_margin_end(24)
        self.clamp.set_child(self.body)
        self.scrolled.set_child(self.clamp)
        self.toolbar.set_content(self.scrolled)

    def add_action(self, widget: Gtk.Widget) -> None:
        """Add an action button to the HeaderBar."""
        self.header.pack_end(widget)

    def add_bottom_bar(self, widget: Gtk.Widget) -> None:
        """Add an action bar pinned below the page."""
        self.toolbar.add_bottom_bar(widget)

    def on_shown(self) -> None:
        """Called when the page becomes visible."""

    def on_hidden(self) -> None:
        """Called when the page is left."""

    def scan(self) -> None:
        """Ctrl+R shortcut; pages that support it may override this."""
