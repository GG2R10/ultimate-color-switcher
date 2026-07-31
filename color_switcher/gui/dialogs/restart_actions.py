#!/usr/bin/env python3
"""
dialogs/restart_actions.py — The "Restart services" manager: the add
prompt, the Adw.PreferencesGroup builder (also reused by the first-run
onboarding in dialogs/onboarding.py), and its standalone settings dialog.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...backend import restart_actions as ra

from .common import _center_group_title


def prompt_restart_action(parent: Gtk.Widget, on_submit, action: dict = None):
    """Two-field-plus-switch prompt (label + shell command + "run on CLI")
    used to add or edit a restart action. Pass the existing action dict to
    pre-fill the fields and switch the dialog into edit mode (title/button
    change accordingly); on_submit is called the same way either way, with
    the (possibly unchanged) label, command, and run_on_cli."""
    editing = action is not None
    dialog = Adw.AlertDialog.new(
        "Edit action" if editing else "New action",
        "Runs in the background after every real Apply (not Simulate).",
    )
    label_entry = Adw.EntryRow(title="Label")
    command_entry = Adw.EntryRow(title="Command (shell)")
    cli_row = Adw.SwitchRow(
        title="Run when triggered from the CLI",
        subtitle="Turn off for e.g. a wallpaper-setter action already covered by another tool's "
                 "own postcommand hook (see $UCS_WALLPAPER) -- avoids running it twice.",
    )
    cli_row.set_active(action.get("run_on_cli", True) if editing else True)
    if editing:
        label_entry.set_text(action["label"])
        command_entry.set_text(action["command"])
    group = Adw.PreferencesGroup()
    group.add(label_entry)
    group.add(command_entry)
    group.add(cli_row)
    dialog.set_extra_child(group)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", "Save" if editing else "Add")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response == "ok":
            on_submit(label_entry.get_text().strip(), command_entry.get_text().strip(), cli_row.get_active())

    dialog.connect("response", _on_response)
    dialog.present(parent)


def build_restart_actions_group(config) -> Adw.PreferencesGroup:
    """
    An Adw.PreferencesGroup listing every restart action as an
    Adw.SwitchRow (enabled toggle + edit + delete buttons), plus an "add"
    button in the group header. Every change is persisted to config.json
    immediately (same real-time-save philosophy as the mapping screen).
    """
    actions = ra.read_restart_actions(config)

    group = Adw.PreferencesGroup(
        title="Restart services",
        description="Run in the background after every real Apply (not Simulate).",
    )
    _center_group_title(group)

    def persist():
        ra.write_restart_actions(config, actions)

    def row_subtitle(action):
        cli_note = "" if action.get("run_on_cli", True) else "  ·  GUI only"
        return action["command"] + cli_note

    def add_row(action):
        row = Adw.SwitchRow(title=action["label"], subtitle=row_subtitle(action))
        row.set_active(bool(action.get("enabled", True)))

        def on_toggle(r, _pspec):
            action["enabled"] = r.get_active()
            persist()

        row.connect("notify::active", on_toggle)

        edit_button = Gtk.Button(
            icon_name="document-edit-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
            tooltip_text="Edit",
        )

        def on_edit_submit(label, command, run_on_cli):
            if not label or not command:
                return
            action["label"] = label
            action["command"] = command
            action["run_on_cli"] = run_on_cli
            row.set_title(label)
            row.set_subtitle(row_subtitle(action))
            persist()

        def on_edit(_b):
            prompt_restart_action(group.get_root(), on_edit_submit, action=action)

        edit_button.connect("clicked", on_edit)
        row.add_suffix(edit_button)

        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
            tooltip_text="Remove",
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

    def on_add(label, command, run_on_cli):
        if not label or not command:
            return
        new_action = {"label": label, "command": command, "enabled": True, "run_on_cli": run_on_cli}
        actions.append(new_action)
        add_row(new_action)
        persist()

    add_button = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], tooltip_text="Add action")
    add_button.connect("clicked", lambda _b: prompt_restart_action(group.get_root(), on_add))
    group.set_header_suffix(add_button)

    return group


def show_restart_actions_settings(parent: Gtk.Widget, config):
    """Reachable any time from the header menu."""
    group = build_restart_actions_group(config)
    page = Adw.PreferencesPage()
    page.add(group)
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Restart services")
    prefs.add(page)
    prefs.present(parent)
