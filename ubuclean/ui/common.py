"""Shared UI utilities: theme, labels, cards, animations, and worker threads."""

from __future__ import annotations

import functools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TypeVar

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ubuclean.core.util import format_bytes, format_count, format_percent

T = TypeVar("T")

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ubuclean-ui")

ICON_FALLBACK = "dialog-information-symbolic"


def load_stylesheet() -> None:
    """Load ``style.css`` for the active display."""
    provider = Gtk.CssProvider()
    css_path = Path(__file__).with_name("style.css")
    provider.load_from_path(str(css_path))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


def reduce_motion() -> bool:
    """True if the user disabled animations in GNOME."""
    settings = Gtk.Settings.get_default()
    if settings is None:
        return False
    try:
        return not bool(settings.props.gtk_enable_animations)
    except AttributeError:
        return False


def run_async(
    func: Callable[[], T],
    on_success: Callable[[T], None],
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """Run a heavy function on a worker thread and return to the main loop."""

    def worker() -> None:
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 - the UI must not crash
            if on_error is not None:
                GLib.idle_add(functools.partial(on_error, exc))
            return
        GLib.idle_add(functools.partial(on_success, result))

    _EXECUTOR.submit(worker)


def set_accessible(widget: Gtk.Widget, label: str, description: str | None = None) -> None:
    """Attach a label/description for screen readers."""
    properties = [Gtk.AccessibleProperty.LABEL]
    values: list[Any] = [label]
    if description:
        properties.append(Gtk.AccessibleProperty.DESCRIPTION)
        values.append(description)
    try:
        widget.update_property(properties, values)
    except (AttributeError, TypeError):
        widget.set_tooltip_text(label)


def color_rgba(widget: Gtk.Widget, name: str, fallback: tuple[float, float, float, float] = (0.3, 0.5, 0.9, 1.0)) -> Gdk.RGBA:
    """Look up a themed named color, with a safe fallback."""
    context = widget.get_style_context()
    try:
        found, color = context.lookup_color(name)
    except (AttributeError, TypeError):
        found = False
    if found:
        return color
    rgba = Gdk.RGBA()
    rgba.red, rgba.green, rgba.blue, rgba.alpha = fallback
    return rgba


def icon(name: str, size: int = 16, *, css: str | None = None) -> Gtk.Image:
    """Consistent symbolic icon."""
    image = Gtk.Image.new_from_icon_name(name or ICON_FALLBACK)
    image.set_pixel_size(size)
    if css:
        image.add_css_class(css)
    return image


def label(
    text: str = "",
    *,
    xalign: float = 0.0,
    css: str | None = None,
    wrap: bool = False,
    selectable: bool = False,
    hexpand: bool = False,
) -> Gtk.Label:
    """Label with consistent styling."""
    widget = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    if wrap:
        widget.set_wrap_mode(2)
    if hexpand:
        widget.set_hexpand(True)
    if css:
        widget.add_css_class(css)
    return widget


def button(
    label_text: str | None = None,
    *,
    icon_name: str | None = None,
    tooltip: str | None = None,
    css: Iterable[str] = (),
    suggested: bool = False,
    destructive: bool = False,
) -> Gtk.Button:
    """Button with optional icon+label and accessibility support."""
    widget = Gtk.Button()
    if icon_name and label_text:
        widget.set_child(Adw.ButtonContent(icon_name=icon_name, label=label_text))
    elif icon_name:
        widget.set_icon_name(icon_name)
    else:
        widget.set_label(label_text or "")
    if suggested:
        widget.add_css_class("suggested-action")
    if destructive:
        widget.add_css_class("destructive-action")
    for name in css:
        widget.add_css_class(name)
    if tooltip:
        widget.set_tooltip_text(tooltip)
    elif label_text:
        widget.set_tooltip_text(label_text)
    set_accessible(widget, tooltip or label_text or icon_name or "Button")
    return widget


def icon_button(icon_name: str, tooltip: str, *, css: Iterable[str] = ()) -> Gtk.Button:
    """Round button containing a symbolic icon."""
    widget = button(icon_name=icon_name, tooltip=tooltip, css=("circular", "flat", *css))
    return widget


def card(*, spacing: int = 8, css: Iterable[str] = ()) -> Gtk.Box:
    """UbuClean-styled card."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.add_css_class("uc-card")
    for name in css:
        box.add_css_class(name)
    return box


def _find_listbox(widget: Gtk.Widget) -> Gtk.ListBox | None:
    """Find the internal Gtk.ListBox owned by an Adw.PreferencesGroup."""
    if isinstance(widget, Gtk.ListBox):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find_listbox(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def clear_group(group: Adw.PreferencesGroup) -> None:
    """Remove every row from an Adw.PreferencesGroup properly."""
    listbox = _find_listbox(group)
    if listbox is None:
        return
    while (row := listbox.get_first_child()) is not None:
        group.remove(row)


def plain_row(row: Any) -> Any:
    """Disable markup on the row so user text (e.g. ``&``) shows up verbatim."""
    try:
        row.set_use_markup(False)
    except (AttributeError, TypeError):
        pass
    return row


def action_row(title: str, subtitle: str = "") -> Adw.ActionRow:
    """ActionRow with plain text (no markup)."""
    row = Adw.ActionRow()
    plain_row(row)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def expander_row(title: str, subtitle: str = "") -> Adw.ExpanderRow:
    """ExpanderRow with plain text (no markup)."""
    row = Adw.ExpanderRow()
    plain_row(row)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def progress(fraction: float = 0.0, *, css: str | None = None) -> Gtk.ProgressBar:
    """Thin progress bar with an optional class (success/warning/error)."""
    widget = Gtk.ProgressBar()
    widget.set_fraction(max(0.0, min(1.0, fraction)))
    widget.add_css_class("uc-progress")
    if css:
        widget.add_css_class(css)
    return widget


def set_progress(bar: Gtk.ProgressBar, fraction: float, *, severity: str | None = None) -> None:
    """Update the progress and its semantic color."""
    bar.set_fraction(max(0.0, min(1.0, fraction)))
    for name in ("success", "warning", "error"):
        bar.remove_css_class(name)
    if severity:
        bar.add_css_class(severity)


def severity_for(percent: float) -> str:
    """Status color for a usage percentage."""
    if percent >= 90:
        return "error"
    if percent >= 75:
        return "warning"
    return "success"


class _ValueHolder(GObject.Object):
    value = GObject.Property(type=float, default=0.0)
    __gtype_name__ = "UbuValueHolder"


class AnimatedNumber:
    """A number that counts up with Adw.TimedAnimation."""

    def __init__(
        self,
        target: Gtk.Widget,
        widget: Gtk.Label,
        *,
        formatter: Callable[[float], str] = format_bytes,
        duration: int = 300,
    ) -> None:
        self._target_widget = target
        self._label = widget
        self._formatter = formatter
        self._duration = duration
        self._holder = _ValueHolder()
        self._holder.connect("notify::value", self._on_value)
        self._animation: Adw.TimedAnimation | None = None
        self._value = 0.0

    @property
    def value(self) -> float:
        """Current value."""
        return self._value

    def set_value(self, value: float, *, animate: bool = True) -> None:
        """Set the value, animating it when allowed."""
        value = float(value)
        if not animate or reduce_motion() or abs(value - self._holder.props.value) < 1:
            self._holder.props.value = value
            self._on_value()
            return
        animation = Adw.TimedAnimation.new(
            self._target_widget,
            float(self._holder.props.value),
            value,
            self._duration,
            Adw.PropertyAnimationTarget.new(self._holder, "value"),
        )
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        animation.play()
        self._animation = animation

    def _on_value(self, *_args: Any) -> None:
        self._value = float(self._holder.props.value)
        self._label.set_text(self._formatter(self._value))


def metric_row(
    title: str,
    value_label: Gtk.Label,
    bar: Gtk.ProgressBar,
    *,
    subtitle: Gtk.Label | None = None,
) -> Gtk.Box:
    """Metric row with a label, a value, and a progress bar."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    top.append(label(title, css="uc-label", hexpand=True))
    top.append(value_label)
    box.append(top)
    box.append(bar)
    if subtitle is not None:
        box.append(subtitle)
    return box


def ask_confirm(
    parent: Gtk.Widget,
    *,
    heading: str,
    body: str,
    confirm_label: str,
    destructive: bool = False,
    on_confirm: Callable[[], None],
) -> None:
    """Important confirmation dialog built on Adw.AlertDialog."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", confirm_label)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    if destructive:
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    else:
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)


def show_info(parent: Gtk.Widget, *, heading: str, body: str) -> None:
    """Simple information dialog."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("close", "Close")
    dialog.set_default_response("close")
    dialog.set_close_response("close")
    dialog.present(parent)


def show_shortcuts(parent: Gtk.Widget, entries: Sequence[tuple[str, str]]) -> None:
    """Keyboard shortcut help dialog (replacement for Adw.ShortcutsDialog 1.5)."""
    dialog = Adw.Dialog()
    dialog.set_title("Keyboard shortcuts")
    dialog.set_content_width(420)
    dialog.set_content_height(420)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header.set_title_widget(Adw.WindowTitle(title="Keyboard shortcuts", subtitle="UbuClean"))
    toolbar.add_top_bar(header)
    page = Adw.PreferencesPage()
    group = Adw.PreferencesGroup()
    for keys, description in entries:
        row = Adw.ActionRow(title=description)
        key_label = label(keys, css="uc-key")
        key_label.set_valign(Gtk.Align.CENTER)
        row.add_suffix(key_label)
        group.add(row)
    page.add(group)
    toolbar.set_content(page)
    dialog.set_child(toolbar)
    dialog.present(parent)


def format_rate(value: float) -> str:
    """Format a byte/second rate."""
    return f"{format_bytes(value)}/s"


def format_temp(value: float | None) -> str:
    """Format a temperature."""
    if value is None:
        return "—"
    return f"{value:.0f} °C"


def format_uptime(seconds: float) -> str:
    """Format a short uptime."""
    seconds = int(max(0, seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} days {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


__all__ = [
    "AnimatedNumber",
    "ask_confirm",
    "button",
    "card",
    "color_rgba",
    "format_bytes",
    "format_count",
    "format_percent",
    "format_rate",
    "format_temp",
    "format_uptime",
    "icon",
    "icon_button",
    "label",
    "load_stylesheet",
    "metric_row",
    "progress",
    "reduce_motion",
    "run_async",
    "set_accessible",
    "set_progress",
    "severity_for",
    "show_info",
    "show_shortcuts",
]
