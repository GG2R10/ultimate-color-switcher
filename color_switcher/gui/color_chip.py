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
.color-swatch-circle { border-radius: 999px; box-shadow: inset 0 0 0 1px alpha(currentColor, 0.15); }
.swatch-empty-circle { background: transparent; box-shadow: inset 0 0 0 2px alpha(currentColor, 0.25); border-radius: 999px; }
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
.ucs-section-title { font-size: 1.3em; font-weight: 700; }
.ucs-section-description { font-size: 0.85em; }
.role-badge { min-width: 22px; min-height: 22px; padding: 0; border-radius: 999px; font-weight: 700; }
.role-badge-unmarked { background: alpha(currentColor, 0.08); box-shadow: inset 0 0 0 2px alpha(currentColor, 0.55); }
"""

_ROLE_LABELS = {"foreground": "F", "background": "B", None: ""}
_ROLE_NAMES_ES = {"foreground": "Foreground", "background": "Background", None: "Sin rol"}

_base_css_provider = None
_known_swatch_colors = set()


def _ensure_display_provider():
    global _base_css_provider
    if _base_css_provider is None:
        _base_css_provider = Gtk.CssProvider()
        # USER (not APPLICATION) priority: some GTK4 themes (e.g. Sweet-Dark)
        # ship a user gtk.css that resets button.flat's background/box-shadow
        # unconditionally, loaded at USER priority -- the highest GTK level,
        # which otherwise beats an APPLICATION-priority provider regardless
        # of selector specificity. Our own classes (role-badge-unmarked,
        # swatch-<hex>, ...) are always at least as specific as the theme's
        # `button`/`button.flat` reset, so tying its priority is enough for
        # our rules to win the tiebreak.
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _base_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        _base_css_provider.load_from_string(_BASE_CSS)


def ensure_base_styles():
    """Make sure .swatch-empty / .number-badge / .active-row / .color-pill
    exist even before any real color has been registered."""
    _ensure_display_provider()


def register_swatch_color(hex_value: str) -> str:
    """Ensure a `.swatch-<hex>` CSS class exists with that background color.
    Returns the class name to apply to a widget.

    Every registered hex gets both a solid-fill (.swatch-<hex>, used for the
    chip's own swatch and the role badge's "background" fill) and a text-
    only variant (.role-fg-<hex>, the role badge's "foreground" state:
    transparent fill, letter colored like the detected color itself) -- via
    its OWN tiny, dedicated Gtk.CssProvider, added once and never touched
    again. A single ever-growing provider re-`load_from_string`'d on every
    new color (the previous approach) reparses every PREVIOUSLY known
    color's rules again on each call -- O(n^2) total, ~90s for 1500 distinct
    colors (a realistic scan of many dotfiles) measured before this fix, vs.
    ~0.05s with one provider per color."""
    hex_value = hex_value.lstrip("#").lower()
    css_class = f"swatch-{hex_value}"
    _ensure_display_provider()
    if hex_value not in _known_swatch_colors:
        _known_swatch_colors.add(hex_value)
        provider = Gtk.CssProvider()
        provider.load_from_string(
            f".swatch-{hex_value} {{ background-color: #{hex_value}; }}\n"
            f".role-fg-{hex_value} {{ color: #{hex_value}; }}"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
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
    edit_button = Gtk.Template.Child()
    delete_button = Gtk.Template.Child()
    remove_button = Gtk.Template.Child()
    move_up_button = Gtk.Template.Child()
    move_down_button = Gtk.Template.Child()
    role_button = Gtk.Template.Child()
    files_revealer = Gtk.Template.Child()
    files_box = Gtk.Template.Child()
    pair_revealer = Gtk.Template.Child()
    pair_box = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_swatch_class = None
        self._current_text_class = None
        self._current_role_fg_class = None
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
        self._current_role_fg_class = f"role-fg-{hex_value.lstrip('#').lower()}"

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

    def set_editable(self, callback=None):
        self.edit_button.set_visible(callback is not None)
        if callback is not None:
            self.edit_button.connect("clicked", lambda _b: callback())

    def set_deletable(self, callback=None):
        self.delete_button.set_visible(callback is not None)
        if callback is not None:
            self.delete_button.connect("clicked", lambda _b: callback())

    def set_reorderable(self, move_up_cb=None, move_down_cb=None):
        """Two tiny up/down buttons that move this chip's mapping entry one
        position earlier/later in the list -- the "editable position"
        fallback for reordering the mapping (real drag-and-drop would be
        nicer but is much more failure-prone to get right reliably in
        GTK4; up/down buttons are simple, precise, and keyboard/screen-
        reader friendly for free). None hides that specific button (e.g.
        the first row has nothing to move further up)."""
        self.move_up_button.set_visible(move_up_cb is not None)
        if move_up_cb is not None:
            self.move_up_button.connect("clicked", lambda _b: move_up_cb())
        self.move_down_button.set_visible(move_down_cb is not None)
        if move_down_cb is not None:
            self.move_down_button.connect("clicked", lambda _b: move_down_cb())

    def set_role(self, role):
        """role: None (unmarked) | "foreground" | "background" -- purely
        display, doesn't decide the NEXT state itself (see
        set_role_toggleable, which just reports a click; the caller owns
        cycling + persisting, then re-renders with the new role).

        unmarked: transparent, just a thin ring outline (otherwise invisible
        against the chip). background: filled with the chip's OWN color
        (same .swatch-<hex> class the swatch itself uses), letter colored by
        _readable_text_class for guaranteed contrast regardless of how
        light/dark that color is. foreground: transparent fill, letter
        colored like the color itself (.role-fg-<hex>) -- an outline look,
        mirroring how a foreground/text color reads AS a color, not as a
        block. (A true "cut-out" letter showing the backdrop through it
        would need masking GTK4 CSS doesn't support -- this is the closest
        practical equivalent.)"""
        self.role_button.set_label(_ROLE_LABELS[role])
        self.role_button.set_tooltip_text(
            f"{_ROLE_NAMES_ES[role]} -- clic para ciclar: sin marcar → background → foreground"
        )
        for css_class in ("role-badge-unmarked", self._current_swatch_class,
                         self._current_text_class, self._current_role_fg_class):
            if css_class:
                self.role_button.remove_css_class(css_class)

        if role == "background":
            if self._current_swatch_class:
                self.role_button.add_css_class(self._current_swatch_class)
            if self._current_text_class:
                self.role_button.add_css_class(self._current_text_class)
        elif role == "foreground":
            if self._current_role_fg_class:
                self.role_button.add_css_class(self._current_role_fg_class)
        else:
            self.role_button.add_css_class("role-badge-unmarked")

    def set_role_toggleable(self, callback=None):
        self.role_button.set_visible(callback is not None)
        if callback is not None:
            self.role_button.connect("clicked", lambda _b: callback())

    def set_pair_section(self, widget=None):
        """Show (and populate) or hide the expandable "Background -> color"
        / "Foreground -> color" section below the chip -- `widget` is an
        arbitrary caller-built widget (typically a label + Gtk.DropDown, or
        several such rows for a background with multiple linked
        foregrounds); None hides the section entirely. ColorChip stays dumb
        about WHAT the section shows or does -- window_main.py owns the
        actual linking UI/logic, same separation as set_role/
        set_role_toggleable already have for the role badge itself."""
        child = self.pair_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.pair_box.remove(child)
            child = nxt
        if widget is not None:
            self.pair_box.append(widget)
        self.pair_revealer.set_reveal_child(widget is not None)
