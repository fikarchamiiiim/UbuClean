"""Home page with Health Score and Smart Clean."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, GObject, Gtk

from ubuclean.core.health import HealthReport, Recommendation, scan_health
from ubuclean.core.registry import CATEGORIES, CATEGORY_MAP
from ubuclean.core.util import format_bytes, format_count, format_percent
from ubuclean.ui.common import (
    AnimatedNumber,
    action_row,
    button,
    card,
    clear_group,
    icon,
    label,
    progress,
    run_async,
    set_accessible,
    set_progress,
    severity_for,
)
from ubuclean.ui.pages.base import BasePage


class HomePage(BasePage):
    """Health summary, metric cards, recommendations, and quick scan."""

    def __init__(self, window: Any) -> None:
        super().__init__("Home", "System health overview")
        self.window = window
        self.services = window.services
        self._busy = False
        self._timer_id = 0
        self._previews = self.services.last_previews
        self._health: HealthReport | None = None
        self._startup_count = 0

        self._scan_content = Adw.ButtonContent(
            icon_name="system-search-symbolic", label="Scan"
        )
        self._scan_button = button(suggested=True)
        self._scan_button.set_child(self._scan_content)
        self._scan_button.connect("clicked", lambda *_: self.scan())
        self.add_action(self._scan_button)

        self._ring = self._build_ring()
        self._cards: dict[str, dict[str, Any]] = {}
        self._flow = self._build_cards()
        self._result_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._recommend_group = Adw.PreferencesGroup(
            title="Recommendations",
            description="Actions most relevant to the current condition.",
        )
        self._recommend_group.add(
            Adw.ActionRow(
                title="No data yet",
                subtitle="Scan the system to get recommendations.",
            )
        )
        self.body.append(self._recommend_group)
        self.body.append(self._result_slot)
        self._sync_cards(self.services.monitor.latest)

    # ------------------------------------------------------------------
    def _build_ring(self) -> Any:
        from ubuclean.ui.widgets.health_ring import HealthRing

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero.add_css_class("uc-hero")
        hero.set_halign(Gtk.Align.CENTER)
        ring = HealthRing()
        hero.append(ring)
        self._summary = label("Preparing system data…", xalign=0.5, css="uc-subtle")
        self._summary.set_halign(Gtk.Align.CENTER)
        hero.append(self._summary)
        self._deduction_label = label("", xalign=0.5, css="uc-subtle", wrap=True)
        self._deduction_label.set_halign(Gtk.Align.CENTER)
        self._deduction_label.set_max_width_chars(64)
        hero.append(self._deduction_label)
        self.body.append(hero)
        return ring

    def _metric_card(self, key: str, title: str, icon_name: str, initial: str) -> Gtk.Box:
        box = card(spacing=8)
        box.set_size_request(200, -1)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.append(icon(icon_name, 16, css="uc-nav-icon"))
        header.append(label(title, css="uc-label"))
        value = label(initial, css="uc-metric")
        subtitle = label("", css="uc-subtle", wrap=True)
        bar = progress(0.0)
        box.append(header)
        box.append(value)
        box.append(subtitle)
        box.append(bar)
        self._cards[key] = {"value": value, "subtitle": subtitle, "bar": bar}
        return box

    def _build_cards(self) -> Gtk.FlowBox:
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(1)
        flow.set_max_children_per_line(4)
        flow.set_column_spacing(16)
        flow.set_row_spacing(16)
        flow.set_valign(Gtk.Align.START)
        flow.append(self._metric_card("disk", "Disk", "drive-harddisk-symbolic", "—"))
        flow.append(self._metric_card("ram", "RAM", "drive-harddisk-solidstate-symbolic", "—"))
        flow.append(self._metric_card("cpu", "CPU", "preferences-system-symbolic", "—"))
        flow.append(self._metric_card("trash", "Trash", "user-trash-symbolic", "—"))
        self.body.append(flow)
        try:
            condition = Adw.BreakpointCondition.parse("max-width: 620sp")
            breakpoint = Adw.Breakpoint.new(condition)
            setter = GObject.Value()
            setter.init(GObject.TYPE_INT)
            setter.set_int(1)
            breakpoint.add_setter(flow, "max-children-per-line", setter)
            self.window.add_breakpoint(breakpoint)
        except (AttributeError, TypeError):
            pass
        return flow

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        """Load metrics and start the light refresh."""
        self._refresh_live()
        if not self._timer_id:
            self._timer_id = GLib.timeout_add_seconds(15, self._auto_refresh)

    def on_hidden(self) -> None:
        """Stop refreshing while the page is not visible."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _auto_refresh(self) -> bool:
        if not self._busy:
            self._refresh_live()
        return True

    def _refresh_live(self) -> None:
        if self._busy:
            return
        self._busy = True

        def collect() -> Any:
            snapshot = self.services.monitor.sample()
            startup_count = self._count_startup()
            return snapshot, startup_count

        def done(result: Any) -> None:
            self._busy = False
            snapshot, startup_count = result
            self._startup_count = startup_count
            previews = self.services.last_previews
            self._previews = previews
            self._sync_cards(snapshot, previews)
            if previews:
                self._apply_health(self._compute_health(snapshot, previews, startup_count))
            else:
                health = scan_health(
                    {},
                    disk_percent=snapshot.disk_percent,
                    ram_percent=snapshot.ram_percent,
                    swap_percent=snapshot.swap_percent,
                    startup_count=startup_count,
                )
                self._apply_health(health)

        def failed(exc: Exception) -> None:
            self._busy = False
            self.window.toast(f"Failed to read metrics: {exc}")

        run_async(collect, done, failed)

    def scan(self) -> None:
        """Scan safe categories and update the score and recommendations."""
        if self._busy:
            return
        self._busy = True
        self._scan_button.set_sensitive(False)
        self._scan_content.set_label("Scanning…")
        self._scan_content.set_icon_name("view-refresh-symbolic")
        self._summary.set_text("Scanning cleanup categories…")

        def collect() -> Any:
            snapshot = self.services.monitor.sample()
            startup_count = self._count_startup()
            previews = self.services.assistant.scan(category.id for category in CATEGORIES)
            return previews, snapshot, startup_count

        def done(result: Any) -> None:
            previews, snapshot, startup_count = result
            self._startup_count = startup_count
            self._busy = False
            self._scan_button.set_sensitive(True)
            self._scan_content.set_label("Scan")
            self._scan_content.set_icon_name("system-search-symbolic")
            self.services.last_previews = previews
            self._previews = previews
            self._sync_cards(snapshot, previews)
            health = self._compute_health(snapshot, previews, startup_count)
            self._apply_health(health)
            self._show_result(previews)
            total = self._cleanable_total(previews)
            self.window.toast(f"Scan complete · {format_bytes(total)} can be freed")

        def failed(exc: Exception) -> None:
            self._busy = False
            self._scan_button.set_sensitive(True)
            self._scan_content.set_label("Scan")
            self._scan_content.set_icon_name("system-search-symbolic")
            self._summary.set_text("Scan failed.")
            self.window.toast(f"Scan failed: {exc}")

        run_async(collect, done, failed)

    def _count_startup(self) -> int:
        try:
            return len(self.services.startup.list_desktop_entries())
        except Exception:
            return 0

    def _compute_health(
        self, snapshot: Any, previews: dict[str, Any], startup_count: int
    ) -> HealthReport:
        trash = previews.get("trash")
        kernels = previews.get("kernels")
        old_kernel_count = (
            sum(1 for item in kernels.items if not item.protected) if kernels else 0
        )
        return scan_health(
            previews,
            disk_percent=snapshot.disk_percent,
            ram_percent=snapshot.ram_percent,
            swap_percent=snapshot.swap_percent,
            trash_bytes=trash.total_bytes if trash else 0,
            startup_count=startup_count,
            old_kernels=old_kernel_count,
        )

    def _sync_cards(self, snapshot: Any, previews: dict[str, Any] | None = None) -> None:
        if snapshot is None:
            return
        previews = previews if previews is not None else self.services.last_previews
        disk = self._cards["disk"]
        disk["value"].set_text(format_percent(snapshot.disk_percent))
        disk["subtitle"].set_text(
            f"{format_bytes(snapshot.disk_used)} / {format_bytes(snapshot.disk_total)}"
        )
        set_progress(disk["bar"], snapshot.disk_percent / 100, severity=severity_for(snapshot.disk_percent))
        ram = self._cards["ram"]
        ram["value"].set_text(format_percent(snapshot.ram_percent))
        ram["subtitle"].set_text(
            f"{format_bytes(snapshot.ram_used)} / {format_bytes(snapshot.ram_total)} · swap "
            f"{format_percent(snapshot.swap_percent)}"
        )
        set_progress(ram["bar"], snapshot.ram_percent / 100, severity=severity_for(snapshot.ram_percent))
        cpu = self._cards["cpu"]
        cpu["value"].set_text(format_percent(snapshot.cpu_percent))
        cpu["subtitle"].set_text(
            f"{len(snapshot.cpu_per_core)} core · load {snapshot.load_1:.2f}"
        )
        set_progress(cpu["bar"], snapshot.cpu_percent / 100)
        total = sum(preview.total_bytes for preview in previews.values())
        items = sum(preview.item_count for preview in previews.values())
        trash = self._cards["trash"]
        trash["value"].set_text(format_bytes(total))
        trash["subtitle"].set_text(
            f"{format_count(items)} item from {len(previews)} categories" if previews else "Not scanned yet"
        )
        set_progress(trash["bar"], min(1.0, total / (5 * 1024**3)))
        self._summary.set_text(
            f"Disk {format_percent(snapshot.disk_percent)} · RAM {format_percent(snapshot.ram_percent)} · "
            f"CPU {format_percent(snapshot.cpu_percent)}"
        )

    def _apply_health(self, health: HealthReport) -> None:
        self._health = health
        self._ring.set_score(health.score, health.verdict, health.severity)
        deductions = health.deductions()[:3]
        if deductions:
            self._deduction_label.set_text(
                "Score deductions: "
                + " · ".join(
                    f"{factor.label} −{factor.lost:.0f}" for factor in deductions
                )
            )
        else:
            self._deduction_label.set_text("All metrics within healthy range.")
        self._render_recommendations(health)

    def _render_recommendations(self, health: HealthReport) -> None:
        clear_group(self._recommend_group)
        if not health.recommendations:
            row = action_row(
                "System looks healthy",
                "No urgent action needed. Regular scans are still recommended.",
            )
            row.add_prefix(icon("object-select-symbolic", 16, css="uc-success-text"))
            self._recommend_group.add(row)
            return
        for recommendation in health.recommendations:
            self._recommend_group.add(self._recommendation_row(recommendation))

    def _recommendation_row(self, recommendation: Recommendation) -> Adw.ActionRow:
        row = action_row(recommendation.title, recommendation.detail)
        css = {
            "critical": "uc-critical-text",
            "warning": "uc-warning-text",
            "info": "uc-nav-icon",
        }.get(recommendation.severity, "uc-nav-icon")
        icon_name = {
            "critical": "dialog-warning-symbolic",
            "warning": "dialog-warning-symbolic",
            "info": "dialog-information-symbolic",
        }.get(recommendation.severity, "dialog-information-symbolic")
        row.add_prefix(icon(icon_name, 16, css=css))
        button_widget = button("Open", css=("flat",))
        button_widget.set_valign(Gtk.Align.CENTER)
        button_widget.connect("clicked", lambda *_: self.window.show_page("clean"))
        row.add_suffix(button_widget)
        return row

    @staticmethod
    def _cleanable_previews(previews: dict[str, Any]) -> dict[str, Any]:
        return {
            category_id: preview
            for category_id, preview in previews.items()
            if CATEGORY_MAP.get(category_id) and CATEGORY_MAP[category_id].default_enabled
        }

    def _cleanable_total(self, previews: dict[str, Any]) -> int:
        return sum(
            preview.total_bytes for preview in self._cleanable_previews(previews).values()
        )

    def _show_result(self, previews: dict[str, Any]) -> None:
        while (child := self._result_slot.get_first_child()) is not None:
            self._result_slot.remove(child)
        cleanable = self._cleanable_previews(previews)
        total = sum(preview.total_bytes for preview in cleanable.values())
        items = sum(preview.item_count for preview in cleanable.values())
        all_total = sum(preview.total_bytes for preview in previews.values())
        result = card(spacing=10, css=("uc-result",))
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.append(icon("edit-clear-all-symbolic", 24))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        heading = Gtk.Label(xalign=0.0)
        heading.add_css_class("uc-total")
        self._result_number = AnimatedNumber(heading, heading, formatter=lambda value: format_bytes(value))
        self._result_number.set_value(total)
        text.append(heading)
        subtitle = f"{format_count(items)} item · {len(cleanable)} categories ready to clean"
        if all_total > total:
            subtitle += f" · {format_bytes(all_total - total)} other options (risky/developer)"
        text.append(label(subtitle, css="uc-subtle", wrap=True))
        top.append(text)
        result.append(top)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        details = button("View details")
        details.connect("clicked", lambda *_: self.window.show_page("clean"))
        clean = button("Clean", icon_name="edit-delete-symbolic", suggested=True)
        clean.connect("clicked", lambda *_: self.window.show_page("clean"))
        actions.append(details)
        actions.append(clean)
        result.append(actions)
        self._result_slot.append(result)
        set_accessible(result, f"{format_bytes(total)} ready to clean")
