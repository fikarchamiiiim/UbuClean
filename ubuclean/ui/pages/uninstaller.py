"""Uninstaller dialog for apt, snap, and flatpak applications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, Gtk

from ubuclean.core.uninstaller import (
    InstalledApp,
    Leftover,
    UninstallerService,
    installed_at_text,
)
from ubuclean.core.util import format_bytes
from ubuclean.ui.common import (
    action_row,
    ask_confirm,
    button,
    clear_group,
    icon,
    icon_button,
    label,
    run_async,
    show_info,
)

SOURCES = ["All", "apt", "snap", "flatpak"]
PAGE_SIZE = 80


class UninstallerDialog:
    """Dialog for searching, removing, and cleaning up application leftovers."""

    def __init__(self, parent: Gtk.Widget, window: Any) -> None:
        self.parent = parent
        self.window = window
        self.services = window.services
        self.service = UninstallerService(self.services.context)
        self.apps: list[InstalledApp] = []
        self.filtered: list[InstalledApp] = []
        self.shown = PAGE_SIZE
        self.source = "All"
        self.query = ""
        self.busy = False

        self.dialog = Adw.Dialog()
        self.dialog.set_title("Application uninstaller")
        self.dialog.set_content_width(820)
        self.dialog.set_content_height(700)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(
                title="Installed applications",
                subtitle="apt · snap · flatpak",
            )
        )
        close = icon_button("window-close-symbolic", "Close")
        close.connect("clicked", lambda *_: self.dialog.close())
        header.pack_end(close)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_margin_top(14)
        controls.set_margin_bottom(8)
        controls.set_margin_start(16)
        controls.set_margin_end(16)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search application name or id…")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda *_: self._on_filter_changed())
        self.source_dropdown = Gtk.DropDown.new_from_strings(SOURCES)
        self.source_dropdown.connect("notify::selected", lambda *_: self._on_filter_changed())
        controls.append(self.search)
        controls.append(self.source_dropdown)
        content.append(controls)
        self.count_label = label("", css="uc-subtle")
        self.count_label.set_margin_start(18)
        self.count_label.set_margin_end(18)
        self.count_label.set_margin_bottom(6)
        content.append(self.count_label)
        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        self.list_group = Adw.PreferencesGroup()
        self.page.add(self.list_group)
        content.append(self.page)
        toolbar.set_content(content)
        self.dialog.set_child(toolbar)

    def present(self) -> None:
        """Show the dialog, then load the application list."""
        self.dialog.present(self.parent)
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.count_label.set_text("Loading installed applications…")
        self._clear_list()
        loading = Adw.ActionRow(title="Loading applications…")
        spinner = Gtk.Spinner()
        spinner.start()
        loading.add_prefix(spinner)
        self.list_group.add(loading)

        def work() -> list[InstalledApp]:
            return self.service.list_packages()

        def done(apps: list[InstalledApp]) -> None:
            self.busy = False
            self.apps = apps
            self.shown = PAGE_SIZE
            self._render()

        def failed(exc: Exception) -> None:
            self.busy = False
            self.count_label.set_text("Failed to load applications.")
            self._clear_list()
            row = action_row(f"Failed to load: {exc}")
            row.set_activatable(False)
            self.list_group.add(row)

        run_async(work, done, failed)

    def _clear_list(self) -> None:
        clear_group(self.list_group)

    def _on_filter_changed(self) -> None:
        index = int(self.source_dropdown.get_selected())
        self.source = SOURCES[index] if 0 <= index < len(SOURCES) else "All"
        self.query = self.search.get_text().strip().lower()
        self.shown = PAGE_SIZE
        self._render()

    def _matches(self, app: InstalledApp) -> bool:
        if self.source != "All" and app.source != self.source:
            return False
        if not self.query:
            return True
        haystack = " ".join(
            [app.id, app.name, app.description, app.version]
        ).lower()
        return self.query in haystack

    def _render(self) -> None:
        self._clear_list()
        self.filtered = [app for app in self.apps if self._matches(app)]
        self.count_label.set_text(
            f"{len(self.filtered)} matching applications · {len(self.apps)} total installed"
        )
        if not self.filtered:
            row = Adw.ActionRow(title="No matching applications")
            row.set_activatable(False)
            self.list_group.add(row)
            return
        for app in self.filtered[: self.shown]:
            self.list_group.add(self._app_row(app))
        remaining = len(self.filtered) - self.shown
        if remaining > 0:
            row = Adw.ActionRow(
                title=f"Load {min(remaining, PAGE_SIZE)} more applications",
                subtitle=f"{remaining} applications not shown yet",
            )
            load_button = button("Load more", css=("flat",))
            load_button.set_valign(Gtk.Align.CENTER)
            load_button.connect("clicked", lambda *_: self._show_more())
            row.add_suffix(load_button)
            self.list_group.add(row)

    def _show_more(self) -> None:
        self.shown += PAGE_SIZE
        self._render()

    def _app_row(self, app: InstalledApp) -> Adw.ActionRow:
        parts = [app.source]
        if app.version:
            parts.append(app.version)
        if app.size:
            parts.append(format_bytes(app.size))
        parts.append(installed_at_text(app.installed_at))
        subtitle = " · ".join(parts)
        if app.description:
            subtitle = f"{app.description} — {subtitle}"
        row = action_row(app.name, subtitle)
        row.add_prefix(self._app_icon(app))
        if app.protected:
            lock = icon("system-lock-screen-symbolic", 16, css="uc-subtle")
            lock.set_tooltip_text(app.protected_reason or "Protected")
            row.add_suffix(lock)
            row.set_tooltip_text(f"Protected: {app.protected_reason}")
        else:
            remove = button("Delete", destructive=True, css=("flat",))
            remove.set_valign(Gtk.Align.CENTER)
            remove.connect("clicked", lambda _button, item=app: self._confirm_remove(item))
            row.add_suffix(remove)
        return row

    @staticmethod
    def _app_icon(app: InstalledApp) -> Gtk.Widget:
        name = app.icon_name or "application-x-executable-symbolic"
        try:
            display = Gdk.Display.get_default()
            if display is not None and Gtk.IconTheme.get_for_display(display).has_icon(name):
                return icon(name, 16, css="uc-nav-icon")
        except (AttributeError, TypeError):
            pass
        return icon("application-x-executable-symbolic", 16, css="uc-nav-icon")

    # ------------------------------------------------------------------
    def _confirm_remove(self, app: InstalledApp) -> None:
        if app.protected:
            show_info(
                self.dialog,
                heading="Application protected",
                body=(
                    f"{app.name} is {app.protected_reason or 'a core system component'} "
                    "and cannot be removed from UbuClean."
                ),
            )
            return

        def work() -> int:
            return self.service.dependent_count(app.id) if app.source == "apt" else 0

        def done(dependents: int) -> None:
            body_parts = [
                f"{app.name} ({app.source} {app.version}) will be removed.",
            ]
            if dependents:
                body_parts.append(
                    f"Warning: {dependents} other packages depend on this package. "
                    "Removal may affect the system."
                )
            body_parts.append(
                "After that UbuClean will offer to clean up leftover configuration and cache."
            )
            ask_confirm(
                self.dialog,
                heading=f"Delete {app.name}?",
                body=" ".join(body_parts),
                confirm_label="Delete application",
                destructive=True,
                on_confirm=lambda: self._remove(app),
            )

        run_async(work, done, lambda exc: self.window.toast(f"Failed to check dependencies: {exc}"))

    def _remove(self, app: InstalledApp) -> None:
        self.count_label.set_text(f"Removing {app.name}…")

        def work() -> tuple[bool, str]:
            return self.service.remove(app)

        def done(result: tuple[bool, str]) -> None:
            ok, message = result
            self.window.toast(message)
            if not ok:
                self.count_label.set_text("")
                return
            self.apps = [
                item
                for item in self.apps
                if not (item.id == app.id and item.source == app.source)
            ]
            self._render()
            leftovers = self.service.find_leftovers(app)
            if leftovers:
                show_leftovers(self.dialog, self, app, leftovers)

        run_async(work, done, lambda exc: self.window.toast(f"Failed to delete: {exc}"))


class LeftoverDialog:
    """Dialog for choosing leftover configuration to quarantine."""

    def __init__(
        self,
        parent: Gtk.Widget,
        owner: UninstallerDialog,
        app: InstalledApp,
        leftovers: list[Leftover],
    ) -> None:
        self.parent = parent
        self.owner = owner
        self.app = app
        self.leftovers = leftovers
        self.checks: dict[Path, Gtk.CheckButton] = {}
        self.dialog = Adw.Dialog()
        self.dialog.set_title("Leftover configuration")
        self.dialog.set_content_width(600)
        self.dialog.set_content_height(600)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Leftover configuration", subtitle=app.name)
        )
        close = icon_button("window-close-symbolic", "Close")
        close.connect("clicked", lambda *_: self.dialog.close())
        header.pack_end(close)
        toolbar.add_top_bar(header)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Select what to clean",
            description=(
                "All items are moved to UbuClean's quarantine and can be restored. "
                "Nothing is deleted automatically."
            ),
        )
        for leftover in leftovers:
            row = action_row(
                leftover.path.name,
                f"{leftover.origin} · {format_bytes(leftover.size)} · {leftover.path}",
            )
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.connect("toggled", lambda *_: self._update_button())
            row.add_suffix(check)
            group.add(row)
            self.checks[leftover.path] = check
        page.add(group)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_top(12)
        footer.set_margin_bottom(16)
        footer.set_margin_start(18)
        footer.set_margin_end(18)
        self.selection_label = label("No items selected yet", css="uc-subtle", hexpand=True)
        footer.append(self.selection_label)
        self.clean_button = button(
            "Move to quarantine", icon_name="user-trash-symbolic", destructive=True
        )
        self.clean_button.set_sensitive(False)
        self.clean_button.connect("clicked", lambda *_: self._quarantine())
        footer.append(self.clean_button)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.append(page)
        container.append(footer)
        toolbar.set_content(container)
        self.dialog.set_child(toolbar)

    def present(self) -> None:
        """Show the dialog."""
        self.dialog.present(self.parent)

    def _selected(self) -> list[Path]:
        return [path for path, check in self.checks.items() if check.get_active()]

    def _update_button(self) -> None:
        selected = self._selected()
        self.clean_button.set_sensitive(bool(selected))
        total = sum(
            leftover.size
            for leftover in self.leftovers
            if leftover.path in selected
        )
        self.selection_label.set_text(
            f"{len(selected)} items · {format_bytes(total)} selected"
            if selected
            else "No items selected yet"
        )

    def _quarantine(self) -> None:
        selected = self._selected()
        if not selected:
            return
        self.clean_button.set_sensitive(False)

        def work() -> tuple[Any, str]:
            return self.owner.service.quarantine_leftovers(self.app, selected)

        def done(result: tuple[Any, str]) -> None:
            entry, message = result
            self.owner.window.toast(message)
            self.dialog.close()
            if entry is not None:
                self.owner._render()

        run_async(work, done, lambda exc: self.owner.window.toast(f"Failed: {exc}"))


def show_uninstaller(parent: Gtk.Widget, window: Any) -> None:
    """Open the uninstaller dialog."""
    UninstallerDialog(parent, window).present()


def show_leftovers(
    parent: Gtk.Widget,
    owner: UninstallerDialog,
    app: InstalledApp,
    leftovers: list[Leftover],
) -> None:
    """Open the leftover configuration selection dialog."""
    LeftoverDialog(parent, owner, app, leftovers).present()
