#!/usr/bin/env python3
"""
chip_builders.py — Pure view-construction functions for the three ColorChip
variants window_main.py's detected/palette/mapping lists use, plus the small
warning-row widget shown under the mapping section. Free functions (no window
state) split out of MainWindow to keep it focused on state management and
event wiring rather than widget construction.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from ..backend import color_detector

from .color_chip import ColorChip, register_swatch_color

TYPE_DISPLAY = {"hex": "hex", "hex_from_rgb": "rgb"}


def build_group_chip(group, number=None, warning=None, removable_cb=None,
                     role=None, role_cb=None, pair_widget=None) -> ColorChip:
    chip = ColorChip()
    representative = group[0]
    chip.set_color(representative["color"])
    r, g, b = color_detector.hex_to_rgb(representative["color"])
    chip.set_hex_text(f"#{representative['color']}  ·  rgb({r}, {g}, {b})")

    by_type = {c["type"]: c for c in group}
    parts = []
    file_groups = {}
    for type_key in ("hex", "hex_from_rgb"):
        c = by_type.get(type_key)
        if c is None:
            continue
        parts.append(f"{TYPE_DISPLAY[type_key]}: x{c['count']}")
        file_groups[TYPE_DISPLAY[type_key]] = c["files"]
    chip.set_meta_text(" · ".join(parts))
    chip.set_file_groups(file_groups)

    chip.set_number(number)
    chip.set_warning(warning)
    chip.set_removable(removable_cb)
    chip.set_role(role)
    chip.set_role_toggleable((lambda g=group: role_cb(g)) if role_cb is not None else None)
    chip.set_pair_section(pair_widget)
    return chip


def build_palette_chip(entry, number=None, warning=None, usage_count=0, role_cb=None,
                       pair_widget=None) -> ColorChip:
    chip = ColorChip()
    chip.set_color(entry["hex"])
    chip.set_hex_text(f"#{entry['hex']}")
    label = entry.get("label", "")
    if usage_count:
        label = f"{label + ' · ' if label else ''}usado {usage_count}x"
    chip.set_meta_text(label)
    chip.set_number(number)
    chip.set_warning(warning)
    chip.set_role(entry.get("role"))
    chip.set_role_toggleable((lambda e=entry: role_cb(e)) if role_cb is not None else None)
    chip.set_pair_section(pair_widget)
    return chip


def build_empty_target_chip(number, warning=None) -> ColorChip:
    chip = ColorChip()
    chip.swatch.add_css_class("swatch-empty")
    chip.set_hex_text("Sin asignar")
    chip.set_meta_text("Elegí un color de la paleta →")
    chip.set_number(number)
    chip.set_warning(warning)
    return chip


def build_swatch_circle(hex_value, size=18, tooltip=None) -> Gtk.Box:
    """A small circular swatch -- hex_value=None renders a hollow "empty"
    circle (nothing linked/assigned yet). Shared by the pairing widgets
    (window_main._build_pair_widget/_build_palette_pair_widget) so a
    dropdown's current selection is also visible at a glance, not just as
    hex text inside the dropdown itself."""
    if hex_value is None:
        return Gtk.Box(width_request=size, height_request=size,
                       css_classes=["swatch-empty-circle"], valign=Gtk.Align.CENTER,
                       tooltip_text=tooltip or "Sin vincular")
    css_class = register_swatch_color(hex_value)
    return Gtk.Box(width_request=size, height_request=size,
                   css_classes=["color-swatch-circle", css_class], valign=Gtk.Align.CENTER,
                   tooltip_text=tooltip or f"#{hex_value}")


def build_mapping_preview_row(old_hex, new_hex, warning=None) -> Gtk.Box:
    """Compact 'detected color -> new color' visual summary row: two small
    circular swatches joined by an arrow, for the wallpaper panel's read-only
    mapping preview (the boxed-list rows next to it remain the actual editable
    mapping UI -- this is purely a nicer-looking summary). new_hex=None means
    the group isn't assigned to anything yet -- shown as a hollow circle."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER,
                  margin_top=4, margin_bottom=4)

    old_class = register_swatch_color(old_hex)
    box.append(Gtk.Box(width_request=22, height_request=22,
                       css_classes=["color-swatch-circle", old_class],
                       tooltip_text=f"#{old_hex}"))

    box.append(Gtk.Image(icon_name="go-next-symbolic", pixel_size=14, css_classes=["dim-label"]))

    if new_hex is not None:
        new_class = register_swatch_color(new_hex)
        box.append(Gtk.Box(width_request=22, height_request=22,
                           css_classes=["color-swatch-circle", new_class],
                           tooltip_text=f"#{new_hex}"))
    else:
        box.append(Gtk.Box(width_request=22, height_request=22,
                           css_classes=["swatch-empty-circle"],
                           tooltip_text="Sin asignar"))

    if warning:
        box.append(Gtk.Image(icon_name="dialog-warning-symbolic", pixel_size=14, tooltip_text=warning))

    return box


def build_warning_row(text, action_label=None, action_cb=None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, css_classes=["warning-row"])
    icon = Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.START)
    # wrap=True alone isn't enough -- without max_width_chars, GtkLabel's
    # NATURAL width is still the full unwrapped text, so a long enough
    # warning overflows the row/window instead of actually wrapping (the
    # bug the user hit: long conflict messages got cut off). max_width_chars
    # forces the label to size from wrap-ability instead, and hexpand=True
    # still lets it fill whatever width the row actually has.
    label = Gtk.Label(label=text, xalign=0, yalign=0, hexpand=True, wrap=True,
                      wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=1)
    box.append(icon)
    box.append(label)
    if action_label and action_cb:
        btn = Gtk.Button(label=action_label, css_classes=["flat"])
        btn.connect("clicked", lambda _b: action_cb())
        box.append(btn)
    return box
