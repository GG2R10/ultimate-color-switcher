#!/usr/bin/env python3
"""
dialogs/backup_settings.py — The backup status/delete row shown in "Other…":
where the backup lives, whether one currently exists, and a way to delete
it (e.g. to force starting fresh, or to free the space).
"""

import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .common import _center_group_title, ask_confirm


def backup_exists(config) -> bool:
    """True if config.backup_dir has anything in it -- same "is there a
    backup to restore" question `ucs restore`/the GUI's restore button ask
    before doing anything."""
    return os.path.isdir(config.backup_dir) and bool(os.listdir(config.backup_dir))


def build_backup_group(config, on_change=None) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="Backup",
        description="The copy of your files as they were before the last real apply -- "
                    "it's what \"Restore\" uses to undo.",
    )
    _center_group_title(group)

    exists = backup_exists(config)
    row = Adw.ActionRow(
        title=config.backup_dir,
        subtitle="Backup available" if exists else "No backup yet",
    )

    delete_button = Gtk.Button(
        label="Delete backup", css_classes=["destructive-action"], valign=Gtk.Align.CENTER,
        sensitive=exists, tooltip_text="Delete the current backup" if exists else "No backup to delete",
    )

    def on_delete(_b):
        def do_delete():
            shutil.rmtree(config.backup_dir, ignore_errors=True)
            os.makedirs(config.backup_dir, exist_ok=True)
            if on_change:
                on_change()
        ask_confirm(
            group.get_root(), "Delete backup",
            "Delete the current backup? Without it, \"Restore\" won't have anything to restore "
            "until the next real apply. This action can't be undone.",
            "Delete", do_delete, destructive=True,
        )

    delete_button.connect("clicked", on_delete)
    row.add_suffix(delete_button)
    group.add(row)

    return group
