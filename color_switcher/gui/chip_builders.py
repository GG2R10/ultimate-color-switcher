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
from gi.repository import Gtk

from ..backend import color_detector

from .color_chip import ColorChip

TYPE_DISPLAY = {"hex": "hex", "hex_from_rgb": "rgb"}


def build_group_chip(group, number=None, warning=None, removable_cb=None) -> ColorChip:
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
    return chip


def build_palette_chip(entry, number=None, warning=None, usage_count=0) -> ColorChip:
    chip = ColorChip()
    chip.set_color(entry["hex"])
    chip.set_hex_text(f"#{entry['hex']}")
    label = entry.get("label", "")
    if usage_count:
        label = f"{label + ' · ' if label else ''}usado {usage_count}x"
    chip.set_meta_text(label)
    chip.set_number(number)
    chip.set_warning(warning)
    return chip


def build_empty_target_chip(number, warning=None) -> ColorChip:
    chip = ColorChip()
    chip.swatch.add_css_class("swatch-empty")
    chip.set_hex_text("Sin asignar")
    chip.set_meta_text("Elegí un color de la paleta →")
    chip.set_number(number)
    chip.set_warning(warning)
    return chip


def build_warning_row(text, action_label=None, action_cb=None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, css_classes=["warning-row"])
    icon = Gtk.Image(icon_name="dialog-warning-symbolic")
    label = Gtk.Label(label=text, xalign=0, hexpand=True, wrap=True)
    box.append(icon)
    box.append(label)
    if action_label and action_cb:
        btn = Gtk.Button(label=action_label, css_classes=["flat"])
        btn.connect("clicked", lambda _b: action_cb())
        box.append(btn)
    return box
