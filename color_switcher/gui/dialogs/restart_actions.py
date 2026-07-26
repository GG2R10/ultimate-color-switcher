#!/usr/bin/env python3
"""
dialogs/restart_actions.py — The "Servicios a reiniciar" manager: the add
prompt, the Adw.PreferencesGroup builder (also reused by the first-run
onboarding in dialogs/onboarding.py), and its standalone settings dialog.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...backend import restart_actions as ra

from .common import _center_group_title


def prompt_restart_action(parent: Gtk.Widget, on_submit):
    """Two-field prompt (label + shell command) used to add a restart action."""
    dialog = Adw.AlertDialog.new(
        "Nueva acción", "Se ejecuta en segundo plano después de cada Aplicar real (no en Simular)."
    )
    label_entry = Adw.EntryRow(title="Etiqueta")
    command_entry = Adw.EntryRow(title="Comando (shell)")
    group = Adw.PreferencesGroup()
    group.add(label_entry)
    group.add(command_entry)
    dialog.set_extra_child(group)
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("ok", "Agregar")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response == "ok":
            on_submit(label_entry.get_text().strip(), command_entry.get_text().strip())

    dialog.connect("response", _on_response)
    dialog.present(parent)


def build_restart_actions_group(config) -> Adw.PreferencesGroup:
    """
    An Adw.PreferencesGroup listing every restart action as an
    Adw.SwitchRow (enabled toggle + a delete button), plus an "add" button
    in the group header. Every change is persisted to config.json
    immediately (same real-time-save philosophy as the mapping screen).
    """
    actions = ra.read_restart_actions(config)

    group = Adw.PreferencesGroup(
        title="Servicios a reiniciar",
        description="Se ejecutan en segundo plano después de cada Aplicar real (no en Simular).",
    )
    _center_group_title(group)

    def persist():
        ra.write_restart_actions(config, actions)

    def add_row(action):
        row = Adw.SwitchRow(title=action["label"], subtitle=action["command"])
        row.set_active(bool(action.get("enabled", True)))

        def on_toggle(r, _pspec):
            action["enabled"] = r.get_active()
            persist()

        row.connect("notify::active", on_toggle)

        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
            tooltip_text="Eliminar",
        )

        def on_remove(_b):
            actions.remove(action)
            group.remove(row)
            persist()

        remove_button.connect("clicked", on_remove)
        row.add_suffix(remove_button)
        group.add(row)

    for action in actions:
        add_row(action)

    def on_add(label, command):
        if not label or not command:
            return
        new_action = {"label": label, "command": command, "enabled": True}
        actions.append(new_action)
        add_row(new_action)
        persist()

    add_button = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], tooltip_text="Agregar acción")
    add_button.connect("clicked", lambda _b: prompt_restart_action(group.get_root(), on_add))
    group.set_header_suffix(add_button)

    return group


def show_restart_actions_settings(parent: Gtk.Widget, config):
    """Reachable any time from the header menu."""
    group = build_restart_actions_group(config)
    page = Adw.PreferencesPage()
    page.add(group)
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Servicios a reiniciar")
    prefs.add(page)
    prefs.present(parent)
