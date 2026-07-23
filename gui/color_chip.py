#!/usr/bin/env python3
"""
color_chip.py — Reusable row widget: color swatch + hex + meta text, with an
optional numbered overlay badge (used in the mapping lists), an optional
expandable "affected files" section (grouped by format, each occurrence
shown as a rounded pill colored like the chip's own swatch), a remove
button, an add button, and a warning icon.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from .template_loader import compiled_ui_path

_BASE_CSS = """
.color-swatch { border-radius: 6px; box-shadow: inset 0 0 0 1px alpha(currentColor, 0.15); }
.swatch-empty { background: transparent; box-shadow: inset 0 0 0 2px alpha(currentColor, 0.25); border-radius: 6px; }
.number-badge {
  background-color: alpha(black, 0.65);
  color: white;
  border-radius: 999px;
  min-width: 15px;
  min-height: 15px;
  padding: 0 3px;
  font-size: 10px;
  margin: 2px;
}
.active-row { background-color: alpha(@accent_color, 0.15); border-radius: 8px; }
.color-pill { border-radius: 999px; padding: 2px 10px; font-size: 11px; }
.pill-text-dark { color: rgba(0, 0, 0, 0.85); }
.pill-text-light { color: rgba(255, 255, 255, 0.95); }
"""

_css_provider = None
_known_swatch_colors = set()


def _rebuild_css():
    rules = "\n".join(
        f".swatch-{h} {{ background-color: #{h}; }}" for h in sorted(_known_swatch_colors)
    )
    _css_provider.load_from_string(_BASE_CSS + rules)


def _ensure_display_provider():
    global _css_provider
    if _css_provider is None:
        _css_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        _rebuild_css()


def ensure_base_styles():
    """Make sure .swatch-empty / .number-badge / .active-row / .color-pill
    exist even before any real color has been registered."""
    _ensure_display_provider()


def register_swatch_color(hex_value: str) -> str:
    """Ensure a `.swatch-<hex>` CSS class exists with that background color.
    Returns the class name to apply to a widget."""
    hex_value = hex_value.lstrip("#").lower()
    css_class = f"swatch-{hex_value}"
    _ensure_display_provider()
    if hex_value not in _known_swatch_colors:
        _known_swatch_colors.add(hex_value)
        _rebuild_css()
    return css_class


def _readable_text_class(hex_value: str) -> str:
    """Pick black or white pill text for readability, via relative luminance
    (WCAG formula) — dark text on light backgrounds, light text on dark ones."""
    hex_value = hex_value.lstrip("#").lower()
    r, g, b = (int(hex_value[i:i + 2], 16) for i in (0, 2, 4))

    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "pill-text-dark" if luminance > 0.5 else "pill-text-light"


@Gtk.Template(filename=compiled_ui_path("color_chip.blp"))
class ColorChip(Gtk.Box):
    __gtype_name__ = "ColorChip"

    swatch = Gtk.Template.Child()
    number_label = Gtk.Template.Child()
    hex_label = Gtk.Template.Child()
    warning_icon = Gtk.Template.Child()
    meta_label = Gtk.Template.Child()
    expand_button = Gtk.Template.Child()
    add_button = Gtk.Template.Child()
    remove_button = Gtk.Template.Child()
    files_revealer = Gtk.Template.Child()
    files_box = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_swatch_class = None
        self._current_text_class = None
        self.expand_button.connect("toggled", self._on_expand_toggled)

    def _on_expand_toggled(self, button):
        self.files_revealer.set_reveal_child(button.get_active())

    def set_color(self, hex_value: str):
        if self._current_swatch_class:
            self.swatch.remove_css_class(self._current_swatch_class)
        css_class = register_swatch_color(hex_value)
        self.swatch.add_css_class(css_class)
        self._current_swatch_class = css_class
        self._current_text_class = _readable_text_class(hex_value)

    def set_hex_text(self, text: str):
        self.hex_label.set_label(text)

    def set_meta_text(self, text: str):
        self.meta_label.set_label(text or "")
        self.meta_label.set_visible(bool(text))

    def set_number(self, n):
        if n is None:
            self.number_label.set_visible(False)
        else:
            self.number_label.set_label(str(n))
            self.number_label.set_visible(True)

    def set_warning(self, tooltip: str = None):
        self.warning_icon.set_visible(bool(tooltip))
        if tooltip:
            self.warning_icon.set_tooltip_text(tooltip)

    def set_file_groups(self, groups: dict):
        """
        groups: {"hex": [file_path, ...], "rgb": [file_path, ...]} (either
        key may be omitted/empty). Each format gets a small section title
        followed by a wrapping row of rounded pills — one per occurrence —
        colored like this chip's own swatch, labeled with the file's
        basename (full path as tooltip).
        """
        child = self.files_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.files_box.remove(child)
            child = nxt

        any_files = any(files for files in groups.values())
        self.expand_button.set_visible(any_files)
        if not any_files:
            self.expand_button.set_active(False)
            return

        for section_label, files in groups.items():
            if not files:
                continue
            title = Gtk.Label(
                label=section_label, xalign=0, css_classes=["caption-heading", "dim-label"],
            )
            self.files_box.append(title)

            flow = Gtk.FlowBox(
                selection_mode=Gtk.SelectionMode.NONE,
                row_spacing=4, column_spacing=4, homogeneous=False,
            )
            for f in files:
                basename = f.rsplit("/", 1)[-1]
                pill = Gtk.Label(label=basename, tooltip_text=f, css_classes=["color-pill"])
                if self._current_swatch_class:
                    pill.add_css_class(self._current_swatch_class)
                if self._current_text_class:
                    pill.add_css_class(self._current_text_class)
                flow.append(pill)
            self.files_box.append(flow)

    def set_removable(self, callback=None):
        self.remove_button.set_visible(callback is not None)
        if callback is not None:
            self.remove_button.connect("clicked", lambda _b: callback())

    def set_addable(self, callback=None):
        self.add_button.set_visible(callback is not None)
        if callback is not None:
            self.add_button.connect("clicked", lambda _b: callback())
