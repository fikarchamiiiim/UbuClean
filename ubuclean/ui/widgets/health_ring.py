"""Ring skor kesehatan yang digambar dengan Cairo."""

from __future__ import annotations

import math
from typing import Any

import cairo
import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GObject, Gtk

from ubuclean.ui.common import color_rgba, label, reduce_motion, set_accessible


def _cairo_available() -> bool:
    try:
        gi.require_foreign("cairo")
        return True
    except ImportError:
        return False


CAIRO_AVAILABLE = _cairo_available()


class HealthRing(Gtk.Overlay):
    """Gauge melingkar 270 derajat dengan animasi pengisian."""

    __gtype_name__ = "UbuHealthRing"

    value = GObject.Property(type=float, default=0.0, minimum=0.0, maximum=100.0)

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(190, 190)
        self._severity = "success"
        self._verdict_text = "Memuat…"
        self._area = Gtk.DrawingArea()
        if CAIRO_AVAILABLE:
            self._area.set_draw_func(self._draw)
        else:
            self._area.set_tooltip_text(
                "Grafik membutuhkan paket python3-gi-cairo (sudo apt install python3-gi-cairo)."
            )
        self.set_child(self._area)
        overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        overlay.set_halign(Gtk.Align.CENTER)
        overlay.set_valign(Gtk.Align.CENTER)
        self._value_label = label("0", xalign=0.5, css="uc-ring-value")
        self._value_label.set_halign(Gtk.Align.CENTER)
        self._label = label("Skor sistem", xalign=0.5, css="uc-ring-label")
        self._label.set_halign(Gtk.Align.CENTER)
        self._verdict = label("Memuat…", xalign=0.5, css="uc-ring-verdict")
        self._verdict.set_halign(Gtk.Align.CENTER)
        overlay.append(self._value_label)
        overlay.append(self._label)
        overlay.append(self._verdict)
        self.add_overlay(overlay)
        set_accessible(self, "Skor kesehatan sistem", "Nilai 0 sampai 100")
        self.connect("notify::value", self._on_value)
        self._animation: Adw.TimedAnimation | None = None

    def set_score(self, score: float, verdict: str, severity: str, *, animate: bool = True) -> None:
        """Perbarui nilai, label, dan warna ring."""
        self._verdict_text = verdict
        self._severity = severity
        self._verdict.set_text(verdict)
        for name in ("uc-success-text", "uc-warning-text", "uc-critical-text"):
            self._verdict.remove_css_class(name)
        self._verdict.add_css_class(
            {"success": "uc-success-text", "warning": "uc-warning-text"}.get(severity, "uc-critical-text")
        )
        target = max(0.0, min(100.0, float(score)))
        if not animate or reduce_motion():
            self.value = target
            return
        animation = Adw.TimedAnimation.new(
            self,
            float(self.value),
            target,
            300,
            Adw.PropertyAnimationTarget.new(self, "value"),
        )
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        animation.play()
        self._animation = animation

    def _on_value(self, *_args: Any) -> None:
        score = int(round(float(self.value)))
        self._value_label.set_text(str(score))
        self._area.queue_draw()
        try:
            self.update_property(
                [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.VALUE_TEXT],
                [f"Skor kesehatan sistem {score} dari 100", self._verdict_text],
            )
        except (AttributeError, TypeError):
            pass

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int, _data: Any = None) -> None:
        size = min(width, height)
        thickness = max(10.0, size * 0.075)
        radius = size / 2 - thickness - 4
        center_x, center_y = width / 2, height / 2
        start = math.radians(135)
        sweep = math.radians(270)
        fraction = max(0.0, min(1.0, float(self.value) / 100.0))

        cr.set_line_width(thickness)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        track = color_rgba(self, "view_fg_color", (0.5, 0.5, 0.5, 1.0))
        cr.set_source_rgba(track.red, track.green, track.blue, 0.10)
        cr.arc(center_x, center_y, radius, start, start + sweep)
        cr.stroke()

        color_name = {
            "success": "success_color",
            "warning": "warning_color",
            "error": "error_color",
        }.get(self._severity, "accent_color")
        accent = color_rgba(self, color_name)
        gradient = cairo.LinearGradient(
            center_x - radius, center_y + radius, center_x + radius, center_y - radius
        )
        gradient.add_color_stop_rgba(0.0, accent.red, accent.green, accent.blue, 0.75)
        gradient.add_color_stop_rgba(1.0, accent.red, accent.green, accent.blue, 1.0)
        cr.set_source(gradient)
        if fraction > 0:
            cr.arc(center_x, center_y, radius, start, start + sweep * fraction)
            cr.stroke()
