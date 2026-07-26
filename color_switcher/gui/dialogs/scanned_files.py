#!/usr/bin/env python3
"""
dialogs/scanned_files.py — The "Archivos a escanear" manager: a file picker
with no format filter (files_to_replace can be any config format), the
Adw.PreferencesGroup builder, and its standalone settings dialog.
"""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...backend import color_detector as cd
from ...backend import config as cfg

from .common import _center_group_title


def pick_scan_file(parent: Gtk.Widget, on_selected):
    """No filter -- files_to_replace can be any config format (css, lua,
    toml, jsonc, ...)."""
    file_dialog = Gtk.FileDialog(title="Elegir archivo a escanear")

    def _on_selected(dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except Exception:
            return  # user cancelled
        on_selected(gfile.get_path())

    file_dialog.open(parent, None, _on_selected)


def build_scanned_files_group(config, on_change=None) -> Adw.PreferencesGroup:
    """An Adw.PreferencesGroup listing every file in files_to_replace (the
    config.json list of dotfiles scanned for colors), with an add/remove
    row per file. Persisted immediately (same real-time-save philosophy as
    the restart-actions group). on_change(), if given, fires after every
    add/remove, since the caller's already-loaded Config object doesn't
    auto-reflect config.json edits -- it should reload and re-detect."""
    files = cfg.read_files_to_replace(config)

    group = Adw.PreferencesGroup(
        title="Archivos a escanear",
        description="Los archivos de configuración donde se detectan y reemplazan colores.",
    )
    _center_group_title(group)

    def persist():
        cfg.write_files_to_replace(config, files)
        if on_change:
            on_change()

    def add_row(file_str):
        exists = os.path.isfile(cd.expand_path(file_str))
        row = Adw.ActionRow(title=file_str, subtitle="" if exists else "No encontrado")

        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
            tooltip_text="Quitar",
        )

        def on_remove(_b):
            files.remove(file_str)
            group.remove(row)
            persist()

        remove_button.connect("clicked", on_remove)
        row.add_suffix(remove_button)
        group.add(row)

    for f in files:
        add_row(f)

    def on_add_path(path):
        if not path:
            return
        entry = cfg.to_home_relative(path)
        if entry in files:
            return
        files.append(entry)
        add_row(entry)
        persist()

    add_button = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], tooltip_text="Agregar archivo")
    add_button.connect("clicked", lambda _b: pick_scan_file(group.get_root(), on_add_path))
    group.set_header_suffix(add_button)

    return group


def show_scanned_files_settings(parent: Gtk.Widget, config, on_change=None):
    """Reachable any time from the header menu."""
    group = build_scanned_files_group(config, on_change=on_change)
    page = Adw.PreferencesPage()
    page.add(group)
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Archivos a escanear")
    prefs.add(page)
    prefs.present(parent)
