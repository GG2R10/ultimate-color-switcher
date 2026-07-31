#!/usr/bin/env python3
"""
dialogs/manage_palettes.py — "Manage palettes": one row per saved palette
(name, wallpaper thumbnail if any, color-swatch preview, load / delete-mapping
/ delete-palette buttons), plus two bulk actions below the list (wipe every
mapping / wipe every palette). The GUI counterpart of the CLI's
`ucs manage mappings/palette show|delete`.
"""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...backend import color_detector as cd
from ...backend import mapping_store, palette_store

from ..chip_builders import build_swatch_circle
from .common import _center_group_title, ask_confirm, resolve_gif_safe_image_source

_THUMB_SIZE = 36
_MAX_SWATCHES = 8


def _find_wallpaper_thumbnail(meta: dict) -> str:
    """Same preview_image-then-image fallback the main window's wallpaper
    panel uses (see window_main._refresh_wallpaper_panel) -- both existence-
    checked so a moved/deleted source image just means "no thumbnail",
    never a crash."""
    for candidate in (meta.get("preview_image"), meta.get("image") if meta.get("generated") else None):
        if candidate:
            expanded = cd.expand_path(candidate)
            if os.path.isfile(expanded):
                return expanded
    return None


def _build_palette_row(config, registry, palette_path: str, on_change, on_load) -> Adw.ActionRow:
    entries = palette_store.read_palette_csv(palette_path)
    meta = palette_store.read_palette_meta(palette_path)
    is_active = palette_path == registry.active_palette_path()

    row = Adw.ActionRow(
        title=os.path.basename(palette_path),
        subtitle=("(active)  " if is_active else "") + f"{len(entries)} color(s)",
    )

    thumb_path = _find_wallpaper_thumbnail(meta)
    if thumb_path:
        picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER, width_request=_THUMB_SIZE, height_request=_THUMB_SIZE,
            css_classes=["ucs-manage-thumb"], valign=Gtk.Align.CENTER,
            tooltip_text=os.path.basename(thumb_path),
        )
        picture.set_filename(resolve_gif_safe_image_source(thumb_path))
        row.add_prefix(picture)

    swatches = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, valign=Gtk.Align.CENTER)
    for e in entries[:_MAX_SWATCHES]:
        swatches.append(build_swatch_circle(e["hex"], size=14))
    if len(entries) > _MAX_SWATCHES:
        swatches.append(Gtk.Label(label=f"+{len(entries) - _MAX_SWATCHES}", css_classes=["dim-label", "caption"]))
    row.add_suffix(swatches)

    load_btn = Gtk.Button(
        icon_name="document-open-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
        tooltip_text="Already loaded" if is_active else "Load this palette and its mapping",
        sensitive=not is_active,
    )
    load_btn.connect("clicked", lambda _b: on_load(palette_path))
    row.add_suffix(load_btn)

    has_mapping = registry.peek_section(palette_path) is not None
    delete_mapping_btn = Gtk.Button(
        icon_name="edit-clear-all-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
        tooltip_text="Delete this palette's mapping" if has_mapping else "This palette has no mapping",
        sensitive=has_mapping,
    )

    def on_delete_mapping(_b):
        def do_delete():
            registry.remove_section(palette_path)
            if on_change:
                on_change()
        ask_confirm(
            row.get_root(), "Delete mapping",
            f"Delete the mapping for \"{os.path.basename(palette_path)}\"? "
            "This action can't be undone.",
            "Delete", do_delete, destructive=True,
        )
    delete_mapping_btn.connect("clicked", on_delete_mapping)
    row.add_suffix(delete_mapping_btn)

    delete_palette_btn = Gtk.Button(
        icon_name="user-trash-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
        tooltip_text="Delete this palette",
    )

    def on_delete_palette(_b):
        def do_delete():
            palette_store.delete_palette(palette_path)
            if on_change:
                on_change()
        ask_confirm(
            row.get_root(), "Delete palette",
            f"Delete the palette \"{os.path.basename(palette_path)}\"? This action can't be undone "
            "(its mapping, if it has one, is NOT deleted by this -- use the button next to it for that).",
            "Delete", do_delete, destructive=True,
        )
    delete_palette_btn.connect("clicked", on_delete_palette)
    row.add_suffix(delete_palette_btn)

    return row


def build_saved_palettes_group(config, registry, on_change=None, on_load=None) -> Adw.PreferencesGroup:
    palettes = palette_store.list_palettes(config.palettes_created_dir)

    group = Adw.PreferencesGroup(
        title="Saved palettes",
        description="Wallpaper, colors, and mapping associated with each palette you created or generated.",
    )
    _center_group_title(group)

    if not palettes:
        group.add(Adw.ActionRow(title="No saved palettes yet.", sensitive=False))
        return group

    for palette_path in palettes:
        group.add(_build_palette_row(config, registry, palette_path, on_change, on_load))

    return group


def build_bulk_actions_group(config, registry, on_change=None) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="Bulk actions",
        description="Neither of these can be undone -- you'll be asked to confirm before continuing.",
    )
    _center_group_title(group)

    mapping_count = len(registry.all_sections())
    palette_count = len(palette_store.list_palettes(config.palettes_created_dir))

    mappings_row = Adw.ActionRow(
        title="Delete all mappings",
        subtitle=f"{mapping_count} saved",
        sensitive=mapping_count > 0,
    )
    mappings_btn = Gtk.Button(label="Delete all", css_classes=["destructive-action"], valign=Gtk.Align.CENTER)

    def on_delete_all_mappings(_b):
        def do_delete():
            registry.remove_all_sections()
            if on_change:
                on_change()
        ask_confirm(
            group.get_root(), "Delete ALL mappings",
            f"This will delete all {mapping_count} saved mapping(s). This action can't be undone.",
            "Delete all", do_delete, destructive=True,
        )
    mappings_btn.connect("clicked", on_delete_all_mappings)
    mappings_row.add_suffix(mappings_btn)
    group.add(mappings_row)

    palettes_row = Adw.ActionRow(
        title="Delete all palettes",
        subtitle=f"{palette_count} saved -- their mappings, if any, aren't deleted by this",
        sensitive=palette_count > 0,
    )
    palettes_btn = Gtk.Button(label="Delete all", css_classes=["destructive-action"], valign=Gtk.Align.CENTER)

    def on_delete_all_palettes(_b):
        def do_delete():
            for palette_path in palette_store.list_palettes(config.palettes_created_dir):
                palette_store.delete_palette(palette_path)
            if on_change:
                on_change()
        ask_confirm(
            group.get_root(), "Delete ALL palettes",
            f"This will delete all {palette_count} saved palette(s). This action can't be undone.",
            "Delete all", do_delete, destructive=True,
        )
    palettes_btn.connect("clicked", on_delete_all_palettes)
    palettes_row.add_suffix(palettes_btn)
    group.add(palettes_row)

    return group


def show_manage_palettes(parent: Gtk.Widget, config, on_change=None, on_load=None):
    """Reachable any time from the header menu. Rebuilds itself in place
    (close + reopen) after any row/bulk action -- simpler and more robust
    than patching individual rows, since a single delete can change several
    rows at once (the "activa" marker moving, counts in the bulk-actions
    group, the whole list going empty). Loading a palette (on_load), unlike
    a delete, closes the dialog outright instead of rebuilding it -- the
    point is to take the user back to the main window with it loaded, not
    to keep managing palettes."""
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Manage palettes")

    def _on_change():
        prefs.close()
        show_manage_palettes(parent, config, on_change=on_change, on_load=on_load)
        if on_change:
            on_change()

    def _on_load(palette_path):
        prefs.close()
        if on_load:
            on_load(palette_path)

    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    page = Adw.PreferencesPage()
    page.add(build_saved_palettes_group(config, registry, on_change=_on_change, on_load=_on_load))
    page.add(build_bulk_actions_group(config, registry, on_change=_on_change))
    prefs.add(page)
    prefs.present(parent)
