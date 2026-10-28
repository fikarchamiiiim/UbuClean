"""Halaman Monitor real-time dengan grafik, core, dan daftar proses."""

from __future__ import annotations

from typing import Any, Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk

from ubuclean.core.monitor import ProcessInfo, Snapshot, list_processes, terminate_process
from ubuclean.core.util import format_bytes, format_percent
from ubuclean.ui.common import (
    action_row,
    ask_confirm,
    card,
    format_rate,
    format_temp,
    icon,
    icon_button,
    label,
    progress,
    run_async,
    set_progress,
    severity_for,
)
from ubuclean.ui.pages.base import BasePage
from ubuclean.ui.widgets.chart import Sparkline


class MonitorPage(BasePage):
    """Dashboard metrik live, grafik 60 detik, core, dan proses teratas."""

    def __init__(self, window: Any) -> None:
        super().__init__("Monitor", "Aktivitas sistem real-time")
        self.window = window
        self.services = window.services
        self._busy = False
        self._timer_id = 0
        self._tick_count = 0
        self._sort_by = "cpu"
        self._process_rows: list[Adw.ActionRow] = []
        self._core_widgets: list[dict[str, Gtk.Widget]] = []
        self._error_shown = False
        self._paused = False

        self._pause_button = Gtk.ToggleButton()
        self._pause_button.set_icon_name("media-playback-pause-symbolic")
        self._pause_button.set_tooltip_text("Jeda pembaruan otomatis")
        self._pause_button.connect("toggled", self._on_pause_toggled)
        self.add_action(self._pause_button)

        self._meta = label("Menunggu data…", css="uc-subtle")
        self._cards: dict[str, dict[str, Gtk.Widget | None]] = {}
        self.body.append(self._live_cards())
        self.body.append(self._meta)
        self.body.append(self._charts_card())
        self.body.append(self._cores_card())
        self.body.append(self._process_group())

    # ------------------------------------------------------------------
    def _live_cards(self) -> Gtk.Widget:
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(1)
        flow.set_max_children_per_line(3)
        flow.set_column_spacing(16)
        flow.set_row_spacing(16)
        for key, title, icon_name, has_bar in (
            ("cpu", "CPU", "preferences-system-symbolic", True),
            ("ram", "RAM", "drive-harddisk-solidstate-symbolic", True),
            ("swap", "Swap", "drive-harddisk-symbolic", True),
            ("disk", "Disk", "drive-harddisk-symbolic", True),
            ("temp", "Suhu", "dialog-warning-symbolic", False),
            ("net", "Jaringan", "network-wired-symbolic", False),
        ):
            box = card(spacing=6)
            box.set_size_request(190, -1)
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header.append(icon(icon_name, 16, css="uc-nav-icon"))
            header.append(label(title, css="uc-label"))
            value = label("—", css="uc-metric")
            subtitle = label("", css="uc-subtle", wrap=True)
            bar = progress() if has_bar else None
            box.append(header)
            box.append(value)
            box.append(subtitle)
            if bar is not None:
                box.append(bar)
            self._cards[key] = {"value": value, "subtitle": subtitle, "bar": bar}
            flow.append(box)
        return flow

    def _chart_row(self, title: str, colors: Sequence[str], accessible: str) -> dict[str, Any]:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.append(label(title, css="uc-label", hexpand=True))
        value = label("—", css="uc-metric-compact")
        top.append(value)
        chart = Sparkline(height=66, colors=colors, accessible_name=accessible)
        row.append(top)
        row.append(chart)
        return {"row": row, "value": value, "chart": chart}

    def _charts_card(self) -> Gtk.Widget:
        box = card(spacing=14)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.append(label("60 detik terakhir", css="uc-section", hexpand=True))
        header.append(label("langsung", css="uc-pill"))
        box.append(header)
        self._cpu_chart = self._chart_row("CPU", ("accent_color",), "Grafik CPU")
        self._ram_chart = self._chart_row("RAM", ("accent_color",), "Grafik RAM")
        self._net_chart = self._chart_row(
            "Jaringan", ("accent_color", "success_color"), "Grafik jaringan"
        )
        box.append(self._cpu_chart["row"])
        box.append(self._divider())
        box.append(self._ram_chart["row"])
        box.append(self._divider())
        box.append(self._net_chart["row"])
        return box

    @staticmethod
    def _divider() -> Gtk.Widget:
        divider = Gtk.Box()
        divider.add_css_class("uc-divider")
        return divider

    def _cores_card(self) -> Gtk.Widget:
        box = card(spacing=10)
        box.append(label("CPU per core", css="uc-section"))
        self._cores_flow = Gtk.FlowBox()
        self._cores_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._cores_flow.set_homogeneous(True)
        self._cores_flow.set_min_children_per_line(2)
        self._cores_flow.set_max_children_per_line(8)
        self._cores_flow.set_column_spacing(14)
        self._cores_flow.set_row_spacing(10)
        box.append(self._cores_flow)
        return box

    def _process_group(self) -> Gtk.Widget:
        self._process_group_widget = Adw.PreferencesGroup(
            title="Proses teratas",
            description="Hanya proses milik Anda yang dapat diakhiri; proses sistem dikunci.",
        )
        self._sort_row = Adw.ComboRow(title="Urutkan berdasarkan")
        self._sort_row.set_model(Gtk.StringList.new(["CPU", "RAM"]))
        self._sort_row.set_selected(0)
        self._sort_row.connect("notify::selected", self._on_sort_changed)
        self._process_group_widget.add(self._sort_row)
        return self._process_group_widget

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self.services.monitor.reset()
        if not self._timer_id:
            self._timer_id = GLib.timeout_add(1000, self._tick)
        self._sample_once()

    def on_hidden(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._busy = False

    def _tick(self) -> bool:
        if not self._paused and not self._busy:
            self._sample_once()
        return True

    def _on_pause_toggled(self, toggle: Gtk.ToggleButton) -> None:
        self._paused = bool(toggle.get_active())
        toggle.set_icon_name(
            "media-playback-start-symbolic" if self._paused else "media-playback-pause-symbolic"
        )
        toggle.set_tooltip_text("Lanjutkan pembaruan" if self._paused else "Jeda pembaruan otomatis")
        if not self._paused:
            self._sample_once()

    def _on_sort_changed(self, row: Adw.ComboRow, _pspec: Any) -> None:
        self._sort_by = "memory" if int(row.get_selected()) == 1 else "cpu"
        self._sample_once()

    def _sample_once(self) -> None:
        if self._busy:
            return
        self._busy = True
        tick = self._tick_count
        self._tick_count += 1
        sort_by = self._sort_by

        def collect() -> Any:
            snapshot = self.services.monitor.sample()
            processes = list_processes(limit=12, sort_by=sort_by) if tick % 2 == 0 else None
            return snapshot, processes

        def done(result: Any) -> None:
            self._busy = False
            self._error_shown = False
            snapshot, processes = result
            self._apply_snapshot(snapshot)
            if processes is not None:
                self._apply_processes(processes)

        def failed(exc: Exception) -> None:
            self._busy = False
            if not self._error_shown:
                self._error_shown = True
                self.window.toast(f"Monitor gagal memperbarui: {exc}")

        run_async(collect, done, failed)

    def _apply_snapshot(self, snapshot: Snapshot) -> None:
        cpu = self._cards["cpu"]
        cpu["value"].set_text(format_percent(snapshot.cpu_percent))
        cpu["subtitle"].set_text(f"{len(snapshot.cpu_per_core)} core · load {snapshot.load_1:.2f}")
        set_progress(cpu["bar"], snapshot.cpu_percent / 100, severity=severity_for(snapshot.cpu_percent))

        ram = self._cards["ram"]
        ram["value"].set_text(format_percent(snapshot.ram_percent))
        ram["subtitle"].set_text(
            f"{format_bytes(snapshot.ram_used)} / {format_bytes(snapshot.ram_total)}"
        )
        set_progress(ram["bar"], snapshot.ram_percent / 100, severity=severity_for(snapshot.ram_percent))

        swap = self._cards["swap"]
        swap["value"].set_text(
            format_percent(snapshot.swap_percent) if snapshot.swap_total else "Tidak ada"
        )
        swap["subtitle"].set_text(
            f"{format_bytes(snapshot.swap_used)} / {format_bytes(snapshot.swap_total)}"
        )
        set_progress(
            swap["bar"],
            snapshot.swap_percent / 100 if snapshot.swap_total else 0.0,
            severity=severity_for(snapshot.swap_percent),
        )

        disk = self._cards["disk"]
        disk["value"].set_text(format_percent(snapshot.disk_percent))
        disk["subtitle"].set_text(
            f"{format_bytes(snapshot.disk_used)} / {format_bytes(snapshot.disk_total)}"
        )
        set_progress(disk["bar"], snapshot.disk_percent / 100, severity=severity_for(snapshot.disk_percent))

        temp = self._cards["temp"]
        temp["value"].set_text(format_temp(snapshot.temperature))
        temp["subtitle"].set_text("Sensor perangkat" if snapshot.temperature else "Sensor tidak tersedia")

        net = self._cards["net"]
        net["value"].set_text(format_rate(snapshot.net_recv_rate))
        net["subtitle"].set_text(f"unggah {format_rate(snapshot.net_sent_rate)}")

        self._cpu_chart["value"].set_text(format_percent(snapshot.cpu_percent))
        self._ram_chart["value"].set_text(format_percent(snapshot.ram_percent))
        self._net_chart["value"].set_text(
            f"unduh {format_rate(snapshot.net_recv_rate)} · unggah {format_rate(snapshot.net_sent_rate)}"
        )

        history = self.services.monitor.history
        self._cpu_chart["chart"].set_data([[item.cpu_percent for item in history]], maximum=100)
        self._ram_chart["chart"].set_data([[item.ram_percent for item in history]], maximum=100)
        self._net_chart["chart"].set_data(
            [
                [item.net_recv_rate for item in history],
                [item.net_sent_rate for item in history],
            ]
        )

        self._update_cores(snapshot.cpu_per_core)
        self._meta.set_text(
            f"Load {snapshot.load_1:.2f} · {snapshot.process_count} proses · "
            f"{snapshot.disk_total and format_bytes(snapshot.disk_total)} disk"
        )

    def _update_cores(self, cores: Sequence[float]) -> None:
        while len(self._core_widgets) < len(cores):
            index = len(self._core_widgets)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.set_size_request(92, -1)
            name = label(f"Core {index}", css="uc-core-name")
            bar = progress()
            bar.set_size_request(80, -1)
            box.append(name)
            box.append(bar)
            self._cores_flow.append(box)
            self._core_widgets.append({"box": box, "bar": bar})
        for widget, value in zip(self._core_widgets, cores):
            set_progress(widget["bar"], value / 100, severity=severity_for(value))

    def _apply_processes(self, processes: Sequence[ProcessInfo]) -> None:
        for row in self._process_rows:
            self._process_group_widget.remove(row)
        self._process_rows.clear()
        for process in processes:
            row = action_row(
                process.name,
                f"PID {process.pid} · {process.username} · CPU {process.cpu_percent:.0f}% · "
                f"RAM {process.memory_percent:.1f}% · {format_bytes(process.rss)}",
            )
            row.add_prefix(icon("application-x-executable-symbolic", 16, css="uc-nav-icon"))
            if process.protected_reason:
                lock = icon("system-lock-screen-symbolic", 16, css="uc-subtle")
                lock.set_tooltip_text(process.protected_reason)
                row.add_suffix(lock)
                row.set_tooltip_text(f"Dilindungi: {process.protected_reason}")
            else:
                kill = icon_button(
                    "edit-delete-symbolic", f"Akhiri {process.name} (PID {process.pid})"
                )
                kill.add_css_class("uc-danger-text")
                kill.set_valign(Gtk.Align.CENTER)
                kill.connect("clicked", lambda _button, item=process: self._confirm_kill(item))
                row.add_suffix(kill)
            self._process_group_widget.add(row)
            self._process_rows.append(row)

    # ------------------------------------------------------------------
    def _confirm_kill(self, process: ProcessInfo) -> None:
        ask_confirm(
            self,
            heading=f"Akhiri {process.name}?",
            body=(
                f"Proses PID {process.pid} akan dihentikan. Pekerjaan yang belum disimpan "
                "pada aplikasi tersebut bisa hilang."
            ),
            confirm_label="Akhiri proses",
            destructive=True,
            on_confirm=lambda: self._kill(process),
        )

    def _kill(self, process: ProcessInfo) -> None:
        def work() -> tuple[bool, str]:
            return terminate_process(process.pid)

        def done(result: tuple[bool, str]) -> None:
            ok, message = result
            self.window.toast(message)
            if ok:
                self._sample_once()

        def failed(exc: Exception) -> None:
            self.window.toast(f"Gagal mengakhiri proses: {exc}")

        run_async(work, done, failed)
