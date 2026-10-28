"""Halaman Memori: statistik RAM/swap dan aksi drop caches."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk

from ubuclean.core.ram import MemoryStats, RamOutcome, SwapEligibility
from ubuclean.core.util import format_bytes, format_percent
from ubuclean.ui.common import (
    ask_confirm,
    button,
    card,
    icon,
    icon_button,
    label,
    progress,
    run_async,
    set_progress,
    severity_for,
    show_info,
)
from ubuclean.ui.pages.base import BasePage


class MemoryPage(BasePage):
    """Panel RAM, swap, dan pembersihan cache kernel."""

    def __init__(self, window: Any) -> None:
        super().__init__("Memori", "RAM, swap, dan cache sistem")
        self.window = window
        self.services = window.services
        self._busy = False
        self._eligibility: SwapEligibility | None = None
        self._loading_config = False

        refresh = icon_button("view-refresh-symbolic", "Muat ulang statistik memori")
        refresh.connect("clicked", lambda *_: self._refresh())
        self.add_action(refresh)

        self.body.append(self._honest_note())
        self.body.append(self._stats_card())

        self._result_box = card(spacing=10, css=("uc-result",))
        self._result_box.set_visible(False)
        self.body.append(self._result_box)

        self.body.append(self._drop_group())
        self.body.append(self._swap_group())

    # ------------------------------------------------------------------
    def _honest_note(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("uc-info")
        box.append(icon("dialog-information-symbolic", 20, css="uc-nav-icon"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label("Drop caches bukan jalan pintas performa", css="uc-section"))
        text.append(
            label(
                "Membuang cache hanya mengosongkan memori yang akan terbentuk lagi saat file dibaca. "
                "Ini tidak selalu membuat sistem lebih cepat, dan bisa terasa lebih lambat sesaat.",
                css="uc-subtle",
                wrap=True,
            )
        )
        box.append(text)
        return box

    def _stat_block(self, title: str, icon_name: str) -> dict[str, Gtk.Widget]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.append(icon(icon_name, 16, css="uc-nav-icon"))
        top.append(label(title, css="uc-label", hexpand=True))
        value = label("—", css="uc-metric-compact")
        top.append(value)
        bar = progress()
        subtitle = label("", css="uc-subtle", wrap=True)
        box.append(top)
        box.append(bar)
        box.append(subtitle)
        return {"box": box, "value": value, "bar": bar, "subtitle": subtitle}

    def _stats_card(self) -> Gtk.Widget:
        box = card(spacing=18)
        box.append(label("Penggunaan saat ini", css="uc-section"))
        self._ram_block = self._stat_block("RAM", "drive-harddisk-solidstate-symbolic")
        self._cache_block = self._stat_block("Cache + buffer", "folder-symbolic")
        self._swap_block = self._stat_block("Swap", "drive-harddisk-symbolic")
        box.append(self._ram_block["box"])
        box.append(self._cache_block["box"])
        box.append(self._swap_block["box"])
        return box

    def _drop_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="Buang cache RAM",
            description="sync dijalankan sebelum menulis level ke /proc/sys/vm/drop_caches.",
        )
        self._level_row = Adw.ComboRow(
            title="Level",
            subtitle="Pilih seberapa agresif cache kernel dibuang.",
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
        self._level_row.connect("notify::selected", self._on_level_changed)
        group.add(self._level_row)
        row = Adw.ActionRow(
            title="Bersihkan cache sekarang",
            subtitle="Membutuhkan autentikasi administrator.",
        )
        self._drop_button = button("Buang cache", icon_name="edit-clear-all-symbolic")
        self._drop_button.set_valign(Gtk.Align.CENTER)
        self._drop_button.connect("clicked", lambda *_: self._confirm_drop())
        row.add_suffix(self._drop_button)
        group.add(row)
        return group

    def _swap_group(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="Segarkan swap",
            description="Menjalankan swapoff -a lalu swapon -a hanya jika RAM bebas cukup.",
        )
        self._swap_row = Adw.ActionRow(
            title="Segarkan swap",
            subtitle="Memeriksa ketersediaan RAM sebelum bertindak.",
        )
        self._swap_row.add_prefix(icon("drive-harddisk-symbolic", 16, css="uc-nav-icon"))
        self._swap_button = button("Segarkan", icon_name="view-refresh-symbolic")
        self._swap_button.set_valign(Gtk.Align.CENTER)
        self._swap_button.connect("clicked", lambda *_: self._confirm_swap())
        self._swap_row.add_suffix(self._swap_button)
        group.add(self._swap_row)
        return group

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._drop_button.set_sensitive(False)

        def collect() -> Any:
            return self.services.ram.stats(), self.services.ram.swap_eligibility()

        def done(result: Any) -> None:
            self._busy = False
            self._drop_button.set_sensitive(True)
            stats, eligibility = result
            self._apply_stats(stats)
            self._apply_eligibility(eligibility)

        def failed(exc: Exception) -> None:
            self._busy = False
            self._drop_button.set_sensitive(True)
            self.window.toast(f"Gagal membaca memori: {exc}")

        run_async(collect, done, failed)

    def _apply_stats(self, stats: MemoryStats) -> None:
        ram = self._ram_block
        ram["value"].set_text(format_percent(stats.percent, precision=1))
        ram["subtitle"].set_text(
            f"{format_bytes(stats.used)} / {format_bytes(stats.total)} · tersedia {format_bytes(stats.available)}"
        )
        set_progress(ram["bar"], stats.percent / 100, severity=severity_for(stats.percent))

        cache = self._cache_block
        cache["value"].set_text(format_bytes(stats.reclaimable))
        cache["subtitle"].set_text(
            f"cached {format_bytes(stats.cached)} · buffer {format_bytes(stats.buffers)}"
        )
        cache["bar"].set_fraction(1.0 if stats.reclaimable else 0.0)
        cache["bar"].add_css_class("success")

        swap = self._swap_block
        if stats.swap_total <= 0:
            swap["value"].set_text("Tidak ada")
            swap["subtitle"].set_text("Sistem tidak memiliki partisi/file swap.")
            set_progress(swap["bar"], 0.0)
        else:
            swap["value"].set_text(format_percent(stats.swap_percent, precision=1))
            swap["subtitle"].set_text(
                f"{format_bytes(stats.swap_used)} / {format_bytes(stats.swap_total)}"
            )
            set_progress(swap["bar"], stats.swap_percent / 100, severity=severity_for(stats.swap_percent))

        level = int(self.services.config.get("drop_caches_level", 2))
        self._loading_config = True
        self._level_row.set_selected(max(0, min(2, level - 1)))
        self._loading_config = False

    def _apply_eligibility(self, eligibility: SwapEligibility) -> None:
        self._eligibility = eligibility
        self._swap_row.set_subtitle(eligibility.message)
        self._swap_button.set_sensitive(True)
        if eligibility.can_refresh:
            self._swap_button.remove_css_class("destructive-action")
        else:
            self._swap_button.add_css_class("dim-label")

    def _on_level_changed(self, row: Adw.ComboRow, _pspec: Any) -> None:
        if self._loading_config:
            return
        level = int(row.get_selected()) + 1
        self.services.config.update({"drop_caches_level": level})
        self.services.save_config()

    # ------------------------------------------------------------------
    def _confirm_drop(self) -> None:
        level = int(self._level_row.get_selected()) + 1
        descriptions = {
            1: "page cache saja",
            2: "page cache, dentries, dan inodes",
            3: "semua cache (paling agresif)",
        }
        ask_confirm(
            self,
            heading=f"Buang cache level {level}?",
            body=(
                f"sync akan dijalankan lalu {descriptions[level]} dibuang. "
                "Cache akan terbentuk kembali; sistem bisa sesaat terasa lebih lambat."
            ),
            confirm_label="Buang cache",
            on_confirm=lambda: self._run_drop(level),
        )

    def _run_drop(self, level: int) -> None:
        if self._busy:
            return
        self._busy = True
        self._drop_button.set_sensitive(False)
        self._swap_button.set_sensitive(False)

        def work() -> RamOutcome:
            return self.services.ram.drop_caches(level)

        def done(outcome: RamOutcome) -> None:
            self._busy = False
            self._drop_button.set_sensitive(True)
            self._swap_button.set_sensitive(True)
            self._show_outcome(outcome, title=f"Cache level {level}")
            self.window.toast(
                f"Berhasil membebaskan {format_bytes(outcome.freed_bytes)}"
                if outcome.ok
                else "Drop caches gagal"
            )
            self._refresh()

        def failed(exc: Exception) -> None:
            self._busy = False
            self._drop_button.set_sensitive(True)
            self._swap_button.set_sensitive(True)
            self.window.toast(f"Drop caches gagal: {exc}")

        run_async(work, done, failed)

    def _confirm_swap(self) -> None:
        if self._eligibility is not None and not self._eligibility.can_refresh:
            show_info(
                self,
                heading="Swap tidak dapat disegarkan",
                body=self._eligibility.message,
            )
            return
        ask_confirm(
            self,
            heading="Segarkan swap?",
            body=(
                "Isi swap akan dipindahkan kembali ke RAM, lalu swap diaktifkan ulang. "
                "Aplikasi bisa terasa berhenti sesaat."
            ),
            confirm_label="Segarkan swap",
            on_confirm=self._run_swap,
        )

    def _run_swap(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._swap_button.set_sensitive(False)
        self._drop_button.set_sensitive(False)

        def work() -> RamOutcome:
            return self.services.ram.swap_refresh()

        def done(outcome: RamOutcome) -> None:
            self._busy = False
            self._swap_button.set_sensitive(True)
            self._drop_button.set_sensitive(True)
            if outcome.ok:
                self._show_outcome(outcome, title="Swap disegarkan")
                self.window.toast("Swap berhasil disegarkan")
            else:
                show_info(self, heading="Swap tidak disegarkan", body="\n".join(outcome.errors))
            self._refresh()

        def failed(exc: Exception) -> None:
            self._busy = False
            self._swap_button.set_sensitive(True)
            self._drop_button.set_sensitive(True)
            self.window.toast(f"Segarkan swap gagal: {exc}")

        run_async(work, done, failed)

    def _show_outcome(self, outcome: RamOutcome, *, title: str) -> None:
        while (child := self._result_box.get_first_child()) is not None:
            self._result_box.remove(child)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        heading.append(icon("object-select-symbolic" if outcome.ok else "dialog-warning-symbolic", 20))
        heading.append(label(title, css="uc-section", hexpand=True))
        if outcome.freed_bytes:
            heading.append(label(f"+{format_bytes(outcome.freed_bytes)}", css="uc-pill"))
        self._result_box.append(heading)
        before = outcome.before
        after = outcome.after
        rows = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        rows.set_homogeneous(True)
        for caption, stats in (("Sebelum", before), ("Sesudah", after)):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            column.append(label(caption, css="uc-label"))
            column.append(
                label(
                    f"RAM {format_percent(stats.percent, precision=1)} · "
                    f"cache {format_bytes(stats.reclaimable)}",
                    css="uc-metric-compact",
                )
            )
            column.append(
                label(
                    f"Swap {format_percent(stats.swap_percent, precision=1)} · "
                    f"{format_bytes(stats.swap_used)}",
                    css="uc-subtle",
                )
            )
            rows.append(column)
        self._result_box.append(rows)
        if outcome.detail:
            self._result_box.append(label(outcome.detail, css="uc-subtle", wrap=True))
        for error in outcome.errors:
            self._result_box.append(label(error, css="uc-critical-text", wrap=True))
        self._result_box.set_visible(True)
