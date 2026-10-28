"""Main window with an Adw.NavigationSplitView sidebar."""

from __future__ import annotations

from typing import Any, Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GObject, Gtk

from ubuclean import constants
from ubuclean.ui.common import (
    icon,
    icon_button,
    label,
    reduce_motion,
    set_accessible,
    show_shortcuts,
)
from ubuclean.ui.pages import (
    CleanPage,
    DiskPage,
    HistoryPage,
    HomePage,
    MemoryPage,
    MonitorPage,
    SchedulePage,
    SettingsPage,
    StartupPage,
)

NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("home", "Home", "go-home-symbolic"),
    ("clean", "Clean", "edit-clear-all-symbolic"),
    ("memory", "Memory", "drive-harddisk-solidstate-symbolic"),
    ("monitor", "Monitor", "preferences-system-symbolic"),
    ("disk", "Disk", "drive-harddisk-symbolic"),
    ("startup", "Startup", "applications-system-symbolic"),
    ("schedule", "Schedule", "alarm-symbolic"),
    ("history", "History", "document-open-recent-symbolic"),
    ("settings", "Settings", "emblem-system-symbolic"),
)


class MainWindow(Adw.ApplicationWindow):
    """Application shell: sidebar, NavigationView, and ToastOverlay."""

    def __init__(
        self,
        *,
        application: Adw.Application,
        services: Any,
        initial_page: str = "home",
    ) -> None:
        super().__init__(application=application, title=constants.APP_NAME)
        self.services = services
        self.set_default_size(1180, 780)
        self.set_size_request(420, 480)
        self.set_icon_name(constants.APP_ID)

        self._pages: dict[str, Any] = {
            "home": HomePage(self),
            "clean": CleanPage(self),
            "memory": MemoryPage(self),
            "monitor": MonitorPage(self),
            "disk": DiskPage(self),
            "startup": StartupPage(self),
            "schedule": SchedulePage(self),
            "history": HistoryPage(self),
            "settings": SettingsPage(self),
        }
        self._active_tag = ""
        self._syncing_selection = False

        self.nav_view = Adw.NavigationView()
        try:
            self.nav_view.set_animate_transitions(not reduce_motion())
        except AttributeError:
            pass
        for tag, title, _icon_name in NAV_ITEMS:
            page = self._pages[tag]
            self.nav_view.add(Adw.NavigationPage(child=page, title=title, tag=tag))
        self.nav_view.connect("notify::visible-page", self._on_visible_page_changed)

        split = Adw.NavigationSplitView()
        sidebar_page = Adw.NavigationPage(child=self._build_sidebar(), title=constants.APP_NAME)
        content_page = Adw.NavigationPage(child=self.nav_view, title="Content")
        split.set_sidebar(sidebar_page)
        split.set_content(content_page)
        split.set_min_sidebar_width(210)
        split.set_max_sidebar_width(260)
        try:
            split.set_sidebar_width_fraction(0.22)
        except AttributeError:
            pass
        self.split_view = split

        try:
            condition = Adw.BreakpointCondition.parse("max-width: 820sp")
            if condition is not None:
                breakpoint = Adw.Breakpoint.new(condition)
                value = GObject.Value()
                value.init(GObject.TYPE_BOOLEAN)
                value.set_boolean(True)
                breakpoint.add_setter(split, "collapsed", value)
                self.add_breakpoint(breakpoint)
        except (AttributeError, TypeError):
            pass

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(split)
        self.set_content(self.toast_overlay)

        self._attach_sidebar_toggles()
        self._install_actions()
        target = initial_page if initial_page in self._pages else "home"
        self._navigate(target)
        for index, (item_tag, _title, _icon_name) in enumerate(NAV_ITEMS):
            if item_tag == target:
                self._select_row(index)
                break
        if self._active_tag != target:
            previous = self._pages.get(self._active_tag)
            if previous is not None:
                previous.on_hidden()
            self._active_tag = target
            self._pages[target].on_shown()

    # ------------------------------------------------------------------
    def _attach_sidebar_toggles(self) -> None:
        self._sidebar_toggles: list[Gtk.Button] = []
        for page in self._pages.values():
            toggle = icon_button("sidebar-show-symbolic", "Show sidebar")
            toggle.set_visible(False)
            toggle.connect("clicked", lambda *_: self.split_view.set_show_content(False))
            page.header.pack_start(toggle)
            self._sidebar_toggles.append(toggle)
        self.split_view.connect("notify::collapsed", lambda *_: self._sync_sidebar_toggles())
        self.split_view.connect("notify::show-content", lambda *_: self._sync_sidebar_toggles())
        self._sync_sidebar_toggles()

    def _sync_sidebar_toggles(self) -> None:
        try:
            collapsed = bool(self.split_view.get_collapsed())
            show_content = bool(self.split_view.get_show_content())
        except AttributeError:
            collapsed = False
            show_content = True
        for toggle in self._sidebar_toggles:
            toggle.set_visible(collapsed and show_content)

    def _build_sidebar(self) -> Gtk.Widget:
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("uc-sidebar")
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Adw.WindowTitle(title=constants.APP_NAME, subtitle="System maintenance"))
        menu = Gio.Menu()
        menu.append("Help", "win.help")
        menu.append("About UbuClean", "win.about")
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_popover(Gtk.PopoverMenu.new_from_model(menu))
        menu_button.set_tooltip_text("Main menu")
        set_accessible(menu_button, "Main menu")
        menu_button.add_css_class("flat")
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        self.nav_list = Gtk.ListBox()
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.set_margin_top(8)
        self.nav_list.set_margin_bottom(12)
        self.nav_list.set_margin_start(6)
        self.nav_list.set_margin_end(6)
        for tag, title, icon_name in NAV_ITEMS:
            row = Gtk.ListBoxRow()
            row.add_css_class("uc-nav-row")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            box.append(icon(icon_name, 16, css="uc-nav-icon"))
            box.append(label(title, xalign=0.0))
            row.set_child(box)
            row.set_tooltip_text(title)
            set_accessible(row, title)
            row.set_activatable(True)
            self.nav_list.append(row)
        self.nav_list.connect("row-selected", self._on_row_selected)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.nav_list)
        toolbar.set_content(scrolled)
        return toolbar

    # ------------------------------------------------------------------
    def _install_actions(self) -> None:
        actions: tuple[tuple[str, Callable[..., None], list[str]], ...] = (
            ("refresh", lambda *_: self._refresh_current(), ["<Control>r"]),
            ("settings", lambda *_: self.show_page("settings"), ["<Control>comma"]),
            ("help", lambda *_: self._show_help(), ["F1"]),
            ("about", lambda *_: self._show_about(), []),
        )
        application = self.get_application()
        for name, callback, accels in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels and application is not None:
                application.set_accels_for_action(f"win.{name}", accels)

    def _show_help(self) -> None:
        show_shortcuts(
            self,
            [
                ("Ctrl+R", "Scan again / refresh data"),
                ("Ctrl+,", "Open Settings"),
                ("F1", "Show shortcut help"),
                ("Ctrl+Q", "Quit UbuClean"),
                ("Tab", "Move between controls"),
                ("Space", "Activate a button or switch"),
            ],
        )

    def show_help(self) -> None:
        """Open the keyboard shortcut help."""
        self._show_help()

    def show_about(self) -> None:
        """Open the About dialog."""
        self._show_about()

    def _show_about(self) -> None:
        dialog = Adw.AboutDialog(
            application_name=constants.APP_NAME,
            application_icon=constants.APP_ID,
            developer_name="UbuClean",
            version=constants.VERSION,
            comments="System cleaner, monitor, and maintenance for Ubuntu Desktop.",
            website="https://github.com/ubuclean/ubuclean",
            issue_url="https://github.com/ubuclean/ubuclean/issues",
            license_type=Gtk.License.GPL_3_0,
        )
        dialog.present(self)

    # ------------------------------------------------------------------
    def toast(
        self,
        message: str,
        *,
        action_label: str | None = None,
        action: Callable[[], None] | None = None,
        timeout: int = 4,
    ) -> None:
        """Show an Adw.Toast, optionally with an action button."""
        toast = Adw.Toast(title=message, timeout=timeout)
        toast.set_use_markup(False)
        if action_label and action is not None:
            toast.set_button_label(action_label)
            toast.connect("button-clicked", lambda _toast: action())
        self.toast_overlay.add_toast(toast)

    def show_page(self, tag: str) -> None:
        """Navigate to a sidebar page."""
        if tag not in self._pages:
            return
        self._navigate(tag)
        for index, (item_tag, _title, _icon_name) in enumerate(NAV_ITEMS):
            if item_tag == tag:
                self._select_row(index)
                break

    def _select_row(self, index: int) -> None:
        row = self.nav_list.get_row_at_index(index)
        if row is None:
            return
        self._syncing_selection = True
        self.nav_list.select_row(row)
        self._syncing_selection = False

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._syncing_selection or row is None:
            return
        index = row.get_index()
        if 0 <= index < len(NAV_ITEMS):
            self._navigate(NAV_ITEMS[index][0])

    def _navigate(self, tag: str) -> None:
        visible = self.nav_view.get_visible_page()
        if visible is not None and visible.get_tag() == tag:
            self._maybe_show_content()
            return
        try:
            self.nav_view.replace_with_tags([tag])
        except AttributeError:
            self.nav_view.push_by_tag(tag)
        self._maybe_show_content()

    def _maybe_show_content(self) -> None:
        try:
            if self.split_view.get_collapsed():
                self.split_view.set_show_content(True)
        except AttributeError:
            pass

    def _on_visible_page_changed(self, nav_view: Adw.NavigationView, _pspec: Any) -> None:
        page = nav_view.get_visible_page()
        if page is None:
            return
        tag = page.get_tag() or ""
        if tag == self._active_tag:
            return
        old = self._pages.get(self._active_tag)
        if old is not None:
            old.on_hidden()
        self._active_tag = tag
        new = self._pages.get(tag)
        if new is not None:
            new.on_shown()
        for index, (item_tag, _title, _icon_name) in enumerate(NAV_ITEMS):
            if item_tag == tag:
                self._select_row(index)
                break

    def _refresh_current(self) -> None:
        if self._active_tag != "home":
            self.show_page("home")
        page = self._pages.get("home")
        if page is not None:
            page.scan()
