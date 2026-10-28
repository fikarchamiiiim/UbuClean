"""Smooth line chart with a thin gradient area."""

from __future__ import annotations

from typing import Any, Sequence

import cairo
import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from ubuclean.ui.common import color_rgba, set_accessible


def _cairo_available() -> bool:
    try:
        gi.require_foreign("cairo")
        return True
    except ImportError:
        return False


CAIRO_AVAILABLE = _cairo_available()


class Sparkline(Gtk.DrawingArea):
    """Gridless chart that supports multiple data series."""

    def __init__(
        self,
        *,
        height: int = 80,
        maximum: float | None = None,
        colors: Sequence[str] = ("accent_color",),
        fill: bool = True,
        accessible_name: str = "Chart",
    ) -> None:
        super().__init__()
        self.set_content_height(height)
        self.set_hexpand(True)
        if CAIRO_AVAILABLE:
            self.set_draw_func(self._draw)
        else:
            self.set_tooltip_text(
                "Chart needs python3-gi-cairo (sudo apt install python3-gi-cairo)."
            )
        self._series: list[list[float]] = []
        self._maximum = maximum
        self._colors = tuple(colors)
        self._fill = fill
        self._accessible_name = accessible_name
        self.add_css_class("uc-chart")
        set_accessible(self, accessible_name)

    def set_data(self, series: Sequence[Sequence[float]], *, maximum: float | None = None) -> None:
        """Update the chart data."""
        self._series = [[max(0.0, float(value)) for value in values] for values in series]
        if maximum is not None:
            self._maximum = max(1.0, float(maximum))
        self.queue_draw()
        if self._series:
            latest = self._series[0][-1] if self._series[0] else 0
            try:
                self.update_property(
                    [Gtk.AccessibleProperty.LABEL],
                    [f"{self._accessible_name}, last value {latest:.0f}"],
                )
            except (AttributeError, TypeError):
                pass

    def _resolve_max(self) -> float:
        if self._maximum is not None:
            return self._maximum
        values = [value for series in self._series for value in series]
        peak = max(values) if values else 1.0
        return max(1.0, peak * 1.15)

    def _points(self, values: list[float], width: float, height: float, maximum: float) -> list[tuple[float, float]]:
        if not values:
            return []
        count = len(values)
        pad_x = 2.0
        pad_y = 6.0
        step = (width - pad_x * 2) / max(1, count - 1)
        return [
            (
                pad_x + index * step,
                height - pad_y - (value / maximum) * (height - pad_y * 2),
            )
            for index, value in enumerate(values)
        ]

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int, _data: Any = None) -> None:
        if width <= 2 or height <= 2 or not self._series:
            return
        maximum = self._resolve_max()
        for index, values in enumerate(self._series):
            points = self._points(values, width, height, maximum)
            if not points:
                continue
            color = color_rgba(self, self._colors[index % len(self._colors)])
            if index == 0 and self._fill:
                gradient = cairo.LinearGradient(0, 0, 0, height)
                gradient.add_color_stop_rgba(0, color.red, color.green, color.blue, 0.20)
                gradient.add_color_stop_rgba(1, color.red, color.green, color.blue, 0.0)
                cr.set_source(gradient)
                cr.move_to(points[0][0], height)
                cr.line_to(points[0][0], points[0][1])
                for point_index in range(1, len(points)):
                    prev = points[point_index - 1]
                    point = points[point_index]
                    mid = (prev[0] + point[0]) / 2
                    cr.curve_to(mid, prev[1], mid, point[1], point[0], point[1])
                cr.line_to(points[-1][0], height)
                cr.close_path()
                cr.fill()

            cr.set_line_width(2.0)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_source_rgba(color.red, color.green, color.blue, 1.0)
            cr.move_to(points[0][0], points[0][1])
            for point_index in range(1, len(points)):
                prev = points[point_index - 1]
                point = points[point_index]
                mid = (prev[0] + point[0]) / 2
                cr.curve_to(mid, prev[1], mid, point[1], point[0], point[1])
            cr.stroke()

            cr.set_source_rgba(color.red, color.green, color.blue, 1.0)
            cr.arc(points[-1][0], points[-1][1], 2.5, 0, 6.28318)
            cr.fill()
