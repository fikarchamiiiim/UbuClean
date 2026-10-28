"""Treemap sederhana untuk komposisi folder."""

from __future__ import annotations

from typing import Any, Sequence

import cairo
import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from ubuclean.core.util import format_bytes
from ubuclean.ui.common import color_rgba, set_accessible

try:
    gi.require_foreign("cairo")
    _CAIRO_OK = True
except ImportError:
    _CAIRO_OK = False


class Treemap(Gtk.DrawingArea):
    """Kotak proporsional untuk item terbesar dalam satu folder."""

    def __init__(self, *, height: int = 240) -> None:
        super().__init__()
        self.set_content_height(height)
        self.set_hexpand(True)
        if _CAIRO_OK:
            self.set_draw_func(self._draw)
        self._entries: list[tuple[str, int]] = []
        self.add_css_class("uc-chart")
        set_accessible(self, "Treemap komposisi folder")

    def set_entries(self, entries: Sequence[tuple[str, int]]) -> None:
        """Perbarui daftar (label, ukuran) dan gambar ulang."""
        self._entries = [
            (str(name), max(0, int(size))) for name, size in entries if size > 0
        ][:20]
        self.queue_draw()
        if not self._entries:
            return
        total = sum(size for _name, size in self._entries)
        top = ", ".join(
            f"{name} {format_bytes(size)}" for name, size in self._entries[:5]
        )
        try:
            self.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [f"Treemap folder, total {format_bytes(total)}. Terbesar: {top}"],
            )
        except (AttributeError, TypeError):
            pass

    def _draw(
        self,
        _area: Gtk.DrawingArea,
        cr: cairo.Context,
        width: int,
        height: int,
        _data: Any = None,
    ) -> None:
        if not self._entries or width <= 4 or height <= 4:
            return
        accent = color_rgba(self, "accent_color")
        foreground = color_rgba(self, "view_fg_color")
        total = sum(size for _name, size in self._entries) or 1
        self._draw_tiles(
            cr,
            self._entries,
            total,
            0.0,
            0.0,
            float(width),
            float(height),
            accent,
            foreground,
            0,
        )

    def _draw_tiles(
        self,
        cr: cairo.Context,
        items: list[tuple[str, int]],
        total: int,
        x: float,
        y: float,
        width: float,
        height: float,
        accent: Any,
        foreground: Any,
        depth: int,
    ) -> None:
        if not items or total <= 0 or width <= 1 or height <= 1:
            return
        vertical = width >= height
        cursor = x if vertical else y
        available = width if vertical else height
        for index, (name, size) in enumerate(items):
            extent = available * (size / total)
            if index == len(items) - 1:
                extent = (x + width - cursor) if vertical else (y + height - cursor)
            gap = 2.0 if extent > 6 else 0.5
            if vertical:
                tile_x, tile_y = cursor, y
                tile_w, tile_h = max(0.0, extent - gap), height
            else:
                tile_x, tile_y = x, cursor
                tile_w, tile_h = width, max(0.0, extent - gap)
            shade = 0.20 + min(0.28, depth * 0.05 + (index % 4) * 0.045)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, shade)
            cr.rectangle(tile_x, tile_y, tile_w, tile_h)
            cr.fill()

            if tile_w > 56 and tile_h > 26:
                label_text = name if len(name) <= 22 else name[:21] + "…"
                cr.set_source_rgba(foreground.red, foreground.green, foreground.blue, 0.92)
                cr.select_font_face(
                    "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD
                )
                cr.set_font_size(10.5)
                cr.move_to(tile_x + 7, tile_y + 15)
                cr.show_text(label_text)
                cr.set_font_size(9.0)
                cr.set_source_rgba(
                    foreground.red, foreground.green, foreground.blue, 0.65
                )
                cr.move_to(tile_x + 7, tile_y + 28)
                cr.show_text(format_bytes(size))
            cursor += extent
