#!/usr/bin/env python3
"""
window_main.py — Main mapping screen: two columns (detected colors / new
palette), each with an "available" list on top and a "mapping" list below.

Detected colors that are the same real color in two literal formats (hex
and rgb) are, by default, grouped into a single row and kept in sync
("auto-link"): adding/removing/assigning one adds/removes/assigns both. This
can be turned off from the header menu for full manual control over each
format independently.
"""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ..backend import color_detector, color_replacer, conflicts, detect_diff, mapping_store, palette_store
from ..backend import palette_generator, palette_shift, restart_actions
from ..backend.config import load_config, to_home_relative

from . import dialogs
from .chip_builders import TYPE_DISPLAY as _TYPE_DISPLAY
from .chip_builders import (
    build_empty_target_chip as _build_empty_target_chip,
    build_group_chip as _build_group_chip,
    build_mapping_preview_row as _build_mapping_preview_row,
    build_palette_chip as _build_palette_chip,
    build_swatch_circle as _build_swatch_circle,
    build_warning_row as _build_warning_row,
)
from .color_chip import ensure_base_styles
from .template_loader import compiled_ui_path  # noqa: E402


@Gtk.Template(filename=compiled_ui_path("window_main.blp"))
class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "MainWindow"

    toast_overlay = Gtk.Template.Child()
    rescan_button = Gtk.Template.Child()
    modifiers_button = Gtk.Template.Child()
    available_detected_listbox = Gtk.Template.Child()
    detected_view_toggle = Gtk.Template.Child()
    detected_search_entry = Gtk.Template.Child()
    mapping_old_listbox = Gtk.Template.Child()
    palette_left_area = Gtk.Template.Child()
    palette_empty_box = Gtk.Template.Child()
    available_palette_listbox = Gtk.Template.Child()
    palette_role_filter = Gtk.Template.Child()
    add_color_button = Gtk.Template.Child()
    mapping_new_listbox = Gtk.Template.Child()
    mapping_palette_header = Gtk.Template.Child()
    palette_image_button = Gtk.Template.Child()
    palette_image_picture = Gtk.Template.Child()
    palette_image_placeholder = Gtk.Template.Child()
    mapping_preview_listbox = Gtk.Template.Child()
    warnings_revealer = Gtk.Template.Child()
    warnings_box = Gtk.Template.Child()
    status_label = Gtk.Template.Child()
    restore_button = Gtk.Template.Child()
    test_button = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        ensure_base_styles()

        self.config = load_config()
        self.detected_colors = []
        self.siblings = {}
        self.new_palette_path = None
        self.new_palette = []
        self.mapping = None
        self.registry = None
        self._mapping_drift = {"ok": [], "driftable": [], "orphaned": []}
        self.active_group_ids = frozenset()
        self._welcomed = False  # welcome dialog shows once per session, before anything else
        self.auto_link_siblings = True
        self.detected_by_folder = False

        self._setup_actions()
        self.rescan_button.connect("clicked", lambda _b: self._start_detection())
        self.modifiers_button.connect("clicked", lambda _b: self._action_modifiers(None, None))
        # Refresh from disk when the window regains focus, so external CLI
        # changes (automatic/shift/generate) don't leave the GUI stale.
        self.connect("notify::is-active", self._on_window_active_changed)
        self.restore_button.connect("clicked", self._on_restore_clicked)
        self.test_button.connect("clicked", lambda _b: self._on_apply_clicked(dry_run=True))
        self.apply_button.connect("clicked", lambda _b: self._on_apply_clicked(dry_run=False))
        self.available_detected_listbox.connect("row-activated", self._on_available_detected_activated)
        self.mapping_old_listbox.connect("row-activated", self._on_mapping_old_activated)
        self.available_palette_listbox.connect("row-activated", self._on_available_palette_activated)
        self.mapping_new_listbox.connect("row-activated", self._on_mapping_new_activated)
        self.palette_image_button.connect("clicked", self._on_palette_image_clicked)
        self.detected_view_toggle.connect("toggled", self._on_detected_view_toggled)
        self.detected_search_entry.connect("search-changed", self._on_detected_search_changed)
        self.palette_role_filter.connect("notify::selected", self._on_palette_role_filter_changed)

        GLib.idle_add(self._start_detection)

    # ------------------------------------------------------------------ actions

    def _setup_actions(self):
        for name, handler in (
            ("new-palette", self._action_new_palette),
            ("import-palette", self._action_import_palette),
            ("add-color", self._action_add_color),
            ("generate-palette", self._action_generate_palette),
            ("save-palette-as", self._action_save_palette_as),
            ("modifiers", self._action_modifiers),
            ("palette-generation-settings", self._action_palette_generation_settings),
            ("other-settings", self._action_other_settings),
            ("manage-palettes", self._action_manage_palettes),
            ("snapshot-detect", self._action_snapshot_detect),
            ("restart-actions", self._action_restart_actions),
            ("scanned-files-settings", self._action_scanned_files_settings),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        self.auto_link_action = Gio.SimpleAction.new_stateful(
            "auto-link-siblings", None, GLib.Variant.new_boolean(True)
        )
        self.auto_link_action.connect("activate", self._on_toggle_auto_link)
        self.add_action(self.auto_link_action)

    def _on_toggle_auto_link(self, action, _param):
        currently_on = action.get_state().get_boolean()
        if currently_on:
            def do_disable():
                action.set_state(GLib.Variant.new_boolean(False))
                self.auto_link_siblings = False
                self._refresh_all()

            dialogs.ask_confirm(
                self,
                "Disable automatic hex/rgb linking",
                "The same real color can appear in your files in both hex and rgb format. "
                "With linking disabled you'll have to add and assign each format separately, "
                "and if you assign them the same color by hand anyway you won't get any special "
                "warning (they'll be treated as two independent colors that just happen to match).\n\n"
                "Disable anyway?",
                "Disable",
                do_disable,
                destructive=True,
            )
        else:
            action.set_state(GLib.Variant.new_boolean(True))
            self.auto_link_siblings = True
            self._refresh_all()

    def _on_detected_view_toggled(self, toggle_button):
        self.detected_by_folder = toggle_button.get_active()
        self._refresh_detected_list()

    def _on_detected_search_changed(self, _entry):
        self._refresh_detected_list()

    def _action_new_palette(self, _action, _param):
        dialogs.prompt_text(
            self, "New palette", "File name (without .csv):", "my-palette", "Create",
            self._on_new_palette_name,
        )

    def _on_new_palette_name(self, name):
        if not name:
            return
        if not name.endswith(".csv"):
            name += ".csv"
        path = os.path.join(self.config.palettes_created_dir, name)
        palette_store.write_palette_csv(path, [])
        self._set_new_palette(path, [])
        dialogs.toast(self.toast_overlay, f"Palette created: {os.path.basename(path)}")

    def _action_import_palette(self, _action, _param):
        dialogs.pick_import_palette_file(self, self._on_import_palette_selected)

    def _on_import_palette_selected(self, path):
        entries = palette_store.read_palette_csv(path)
        self._set_new_palette(path, entries)
        dialogs.toast(self.toast_overlay, f"Palette imported: {len(entries)} color(s)")

    def _action_save_palette_as(self, _action, _param):
        if not self.new_palette_path:
            dialogs.toast(self.toast_overlay, "First create, import, or generate a palette.")
            return
        dialogs.prompt_text(
            self, "Save palette as…", "File name (without .csv):", "my-palette-copy", "Save",
            self._on_save_palette_as_name,
        )

    def _on_save_palette_as_name(self, name):
        if not name:
            return
        if not name.endswith(".csv"):
            name += ".csv"
        new_path = os.path.join(self.config.palettes_created_dir, name)
        if os.path.abspath(new_path) == os.path.abspath(self.new_palette_path):
            dialogs.toast(self.toast_overlay, "Pick a different name than the current palette's.")
            return
        entries = palette_store.read_palette_csv(self.new_palette_path)
        meta = palette_store.read_palette_meta(self.new_palette_path)
        # A "save as" copy is a fork, not another instance of the same
        # generated palette -- sever its provenance link to the source image
        # (see palette_store.find_palettes_for_image) so it's never ambiguous
        # which palette is "the" one auto-detected/reused for that wallpaper.
        # Keep showing the same wallpaper picture cosmetically via
        # preview_image, which is independent of that provenance.
        if meta.get("generated") and meta.get("image") and not meta.get("preview_image"):
            meta["preview_image"] = meta["image"]
        meta["generated"] = False
        meta["image"] = None
        palette_store.write_palette_csv(new_path, entries, meta=meta)
        self._set_new_palette(new_path, entries)
        dialogs.toast(self.toast_overlay, f"Saved as: {os.path.basename(new_path)}")

    def _action_add_color(self, _action, _param):
        if not self.new_palette_path:
            dialogs.toast(self.toast_overlay, "First create or import a palette.")
            return
        dialogs.prompt_add_color(self, self._on_add_color)

    def _on_add_color(self, hex_value, label):
        try:
            palette_shift.add_color(self.new_palette_path, hex_value, label)
        except palette_shift.PaletteEditError as e:
            dialogs.toast(self.toast_overlay, str(e))
            return
        self.new_palette = palette_store.read_palette_csv(self.new_palette_path)
        self._refresh_all()

    def _on_palette_edit(self, entry):
        dialogs.prompt_pick_color(
            self, lambda hexv: self._do_palette_edit(entry, hexv), initial_hex=entry["hex"],
        )

    def _do_palette_edit(self, entry, new_hex):
        try:
            palette_shift.edit_color(self.new_palette_path, entry["id"], new_hex)
        except palette_shift.PaletteEditError as e:
            dialogs.toast(self.toast_overlay, str(e))
            return
        self.new_palette = palette_store.read_palette_csv(self.new_palette_path)
        self._refresh_all()
        dialogs.toast(self.toast_overlay, f"Color changed to #{new_hex}.")

    def _on_palette_delete(self, entry):
        dialogs.ask_confirm(
            self, "Delete color", f"Delete color #{entry['hex']} from the palette?", "Delete",
            lambda: self._do_palette_delete(entry), destructive=True,
        )

    def _do_palette_delete(self, entry):
        try:
            deleted_id = palette_shift.delete_color(self.new_palette_path, entry["id"])
        except palette_shift.PaletteEditError as e:
            dialogs.toast(self.toast_overlay, str(e))
            return
        # This palette is the mapping's target -> keep it aligned: unassign any
        # detected colors that pointed at the deleted color, shift the rest.
        dropped = 0
        if self.mapping is not None:
            dropped = self.mapping.drop_and_shift_new_id(deleted_id)
        self.new_palette = palette_store.read_palette_csv(self.new_palette_path)
        self._refresh_all()
        msg = "Color deleted."
        if dropped:
            msg += f" {dropped} assignment(s) were left unassigned."
        dialogs.toast(self.toast_overlay, msg)

    def _action_generate_palette(self, _action, _param):
        dialogs.pick_image_file(self, self._on_generate_palette_image_picked)

    def _on_generate_palette_image_picked(self, image_path):
        # "One persistent palette per wallpaper": before generating anything,
        # check if this exact image (by provenance, not filename) already has
        # a saved palette -- if so, offer to reuse it (recommended) instead of
        # silently regenerating/overwriting it.
        existing = palette_store.find_palettes_for_image(self.config.palettes_created_dir, image_path)
        if existing:
            self._ask_reuse_or_regenerate(image_path, existing[0])
            return
        dialogs.prompt_text(
            self, "Generate palette", "How many colors to generate?", "6", "Generate",
            lambda text: self._on_generate_palette_count(image_path, text),
        )

    def _ask_reuse_or_regenerate(self, image_path, existing_path):
        n = len(palette_store.read_palette_csv(existing_path))
        body = (
            f"A palette generated from this image already exists ({n} color(s)), at:\n{existing_path}\n\n"
            "Use the existing one (recommended) or regenerate it from scratch?"
        )
        dialogs.ask_choice(
            self, "Palette already exists", body,
            [("use", "Use existing", Adw.ResponseAppearance.SUGGESTED),
             ("regenerate", "Regenerate", Adw.ResponseAppearance.DESTRUCTIVE)],
            lambda choice: self._on_reuse_or_regenerate_choice(choice, image_path, existing_path),
        )

    def _on_reuse_or_regenerate_choice(self, choice, image_path, existing_path):
        if choice == "use":
            entries = palette_store.read_palette_csv(existing_path)
            self._set_new_palette(existing_path, entries)
            dialogs.toast(self.toast_overlay, f"Using the existing palette: {len(entries)} color(s).")
            return
        n_colors = len(palette_store.read_palette_csv(existing_path)) or 6
        dialogs.prompt_text(
            self, "Regenerate palette", "How many colors to generate?", str(n_colors), "Regenerate",
            lambda text: self._on_generate_palette_count(image_path, text, out_path=existing_path),
        )

    def _on_generate_palette_count(self, image_path, count_text, out_path=None):
        try:
            n_colors = max(1, int(count_text.strip()))
        except ValueError:
            n_colors = 6

        settings = palette_generator.read_generation_settings(self.config)
        shuffle_arg = None
        if settings.get("shuffle_enabled", False):
            shuffle_arg = "next" if settings.get("shuffle_mode") == "next" else int(settings.get("shuffle_value", 0))

        try:
            # Same shared save-a-generated-palette-to-disk path the CLI's
            # `palette generate` / `automatic --from-image` use, so the two
            # never drift out of sync on what a "generated palette" looks like.
            entries, path, warnings = palette_shift.generate_and_save_palette(
                self.config, image_path, n_colors, 40000, settings["mode"], settings["saturate"],
                out_path, scoring=settings["scoring"], custom_scoring_values=settings.get("custom_percentages"),
                weighted_contrast=settings.get("weighted_contrast", True),
                shuffle=shuffle_arg, overfetch=int(settings.get("overfetch", 0)),
                ying_yang=settings.get("ying_yang", False),
                my_eyes_factor=settings.get("my_eyes_factor", 1.5),
                my_eyes_max_chroma=settings.get("my_eyes_max_chroma", 132.0),
                shading_direction=settings.get("shading_direction", "dark"),
                shading_min_luminance=settings.get("shading_min_luminance", 8.0),
                shading_max_luminance=settings.get("shading_max_luminance", 92.0),
                # Explicit overrides, not left as None: otherwise a target
                # out_path that already exists would have its OWN stored
                # keep_custom_on_regen/hallucinate_on_monochrome/eco_contrast
                # win over whatever's configured in "Configure palette
                # generation…", silently ignoring it.
                keep_custom="on" if settings.get("keep_custom_on_regen", True) else "off",
                hallucinate="on" if settings.get("hallucinate_on_monochrome", True) else "off",
                eco="on" if settings.get("eco_contrast", False) else "off",
            )
        except Exception as e:
            dialogs.toast(self.toast_overlay, f"Couldn't generate the palette: {e}")
            return

        self._set_new_palette(path, entries)
        base = os.path.splitext(os.path.basename(image_path))[0]
        dialogs.toast(self.toast_overlay, f"Palette generated: {len(entries)} color(s) from {base}")
        for w in warnings:
            dialogs.toast(self.toast_overlay, f"⚠ {w}")

    def _action_modifiers(self, _action, _param):
        if not self.new_palette_path or not self.new_palette:
            dialogs.toast(self.toast_overlay, "First generate or import a palette with colors.")
            return
        dialogs.show_palette_modifiers(
            self, self.config, self.new_palette_path, on_applied=self._on_modifiers_applied,
        )

    def _on_modifiers_applied(self, path):
        # The shifted palette was written to disk (same path) -- reload it so
        # the main window shows the new colors and re-runs the conflict checks.
        entries = palette_store.read_palette_csv(path)
        self._set_new_palette(path, entries)
        dialogs.toast(self.toast_overlay, "Modifiers applied to the palette.")

    def _on_palette_image_clicked(self, _button):
        if not self.new_palette_path:
            dialogs.toast(self.toast_overlay, "First create or import a palette.")
            return
        dialogs.pick_image_file(self, self._on_palette_preview_image_picked)

    def _on_palette_preview_image_picked(self, image_path):
        # Always a cosmetic override (preview_image), never `image` -- doesn't
        # touch `generated` or anything shift/regeneration reads.
        palette_store.set_preview_image(self.new_palette_path, image_path)
        self._refresh_wallpaper_panel()

    def _action_snapshot_detect(self, _action, _param):
        dialogs.prompt_text(
            self, "Save snapshot", "Snapshot name (without .csv):", "detected-manual", "Save",
            self._on_snapshot_name,
        )

    def _on_snapshot_name(self, name):
        if not name:
            return
        dest = detect_diff.save_snapshot(self.config, self.detected_colors, name)
        dialogs.toast(self.toast_overlay, f"Saved: {os.path.basename(dest)}")

    def _action_restart_actions(self, _action, _param):
        dialogs.show_restart_actions_settings(self, self.config)

    def _action_palette_generation_settings(self, _action, _param):
        dialogs.show_palette_generation_settings(self, self.config)

    def _action_other_settings(self, _action, _param):
        dialogs.show_other_settings(self, self.config)

    def _action_manage_palettes(self, _action, _param):
        dialogs.show_manage_palettes(
            self, self.config, on_change=self._on_manage_palettes_changed, on_load=self._on_load_palette_from_manager,
        )

    def _on_load_palette_from_manager(self, path):
        entries = palette_store.read_palette_csv(path)
        self._set_new_palette(path, entries)
        dialogs.toast(self.toast_overlay, f"Loaded: {os.path.basename(path)}")

    def _on_manage_palettes_changed(self):
        """A palette/mapping was deleted from the manager -- re-sync this
        window's in-memory state with disk: the palette currently open here
        (self.new_palette_path) or its mapping section may be exactly what
        just got wiped."""
        if self.new_palette_path and not os.path.isfile(color_detector.expand_path(self.new_palette_path)):
            self.new_palette_path = None
            self.new_palette = []
        elif self.new_palette_path:
            self.new_palette = palette_store.read_palette_csv(self.new_palette_path)
        if self.registry is not None:
            self.mapping = self.registry.for_active()
        self._refresh_all()

    def _action_scanned_files_settings(self, _action, _param):
        dialogs.show_scanned_files_settings(self, self.config, on_change=self._on_scanned_files_changed)

    def _on_scanned_files_changed(self):
        self.config = load_config()
        self._start_detection()

    def _set_new_palette(self, path, entries):
        """Switch which palette the window is working on. Each palette has
        its OWN persistent mapping section (see mapping_store.MappingRegistry)
        -- so unlike before this rework, switching away and back never wipes
        or clobbers a different palette's already-tuned mapping; for_palette
        loads-or-creates exactly the right section and marks it active."""
        self.new_palette_path = path
        self.new_palette = entries
        if self.registry is not None:
            self.mapping = self.registry.for_palette(path, old_palette=self.config.detected_palette_csv)
        self._refresh_all()

    # --------------------------------------------------------------- detection

    def _start_detection(self):
        if not self.config.files_to_replace:
            # Fresh config: welcome FIRST (once), THEN the "configure files"
            # step (auto-scan ~/.config / manual). on_done reloads config and
            # loops back here -- files present now, and _welcomed already set,
            # so no second welcome.
            configure = lambda: dialogs.show_configure_files_onboarding(
                self, self.config, on_done=self._on_scanned_files_changed
            )
            if self._welcomed:
                configure()
            else:
                self._welcomed = True
                dialogs.show_welcome(self, configure)
            return GLib.SOURCE_REMOVE

        raw = detect_diff.detect_with_route(self.config, save=False)
        route = raw["route"]
        if route == "a":
            def after_welcome():
                dialogs.show_restart_actions_onboarding(
                    self, self.config, lambda: self._finish_detection(raw["colors"], persist=True)
                )

            if self._welcomed:
                after_welcome()
            else:
                self._welcomed = True
                dialogs.show_welcome(self, after_welcome)
        elif route == "b":
            self._finish_detection(raw["colors"], persist=True)
        else:
            def on_choice(choice):
                if choice == "rescan":
                    self._finish_detection(raw["colors"], persist=True)
                else:
                    self._finish_detection(raw["previous"], persist=False)

            dialogs.show_stale_detect(self, raw["diff"], on_choice)
        return GLib.SOURCE_REMOVE

    def _finish_detection(self, colors, persist):
        if persist:
            color_detector.write_detected_csv(colors, self.config.detected_palette_csv)
        self.detected_colors = colors
        self.siblings = conflicts.find_case2_siblings(colors)
        self.active_group_ids = frozenset()

        # Continue whatever's currently active in the mapping registry (one
        # section PER palette, see mapping_store.MappingRegistry) instead of
        # discarding it -- otherwise the first save from THIS session (e.g.
        # picking a palette) would wipe out everything built in a previous
        # session, breaking `automatic`'s whole "build once, reuse later"
        # workflow.
        self.registry = mapping_store.MappingRegistry(
            self.config.mapping_registry_json, project_dir=self.config.project_dir,
        )
        self.mapping = self.registry.for_active()
        # Refresh/add the ACTIVE palette's detected-color identity stamps
        # against this fresh scan ONLY -- every OTHER (inactive) palette's
        # mapping is, by definition, "orphaned" against whatever's currently
        # applied (only one wallpaper's colors can physically be in the files
        # at a time), which isn't real, actionable drift; checking/warning
        # about all of them here was pure noise (real bug report: switching
        # wallpapers falsely warned about every OTHER previously-used
        # palette). Inactive mappings get correctly re-stamped automatically
        # the next time THEY become active (see stamp_applied_entries), not
        # checked continuously in the background. driftable entries here are
        # re-linkable with one click, orphaned ones are flagged only (see
        # mapping_store.refresh_identity_stamps).
        new_entries, self._mapping_drift = mapping_store.refresh_identity_stamps(
            self.mapping.entries, colors
        )
        self.mapping.entries = new_entries
        self.mapping.save()

        if self.mapping.new_palette and not self.new_palette_path:
            self.new_palette_path = self.mapping.new_palette
            self.new_palette = palette_store.read_palette_csv(self.new_palette_path)

        self._refresh_all()
        self.status_label.set_label(f"{len(colors)} color(s) detected.")

    # ------------------------------------------------------------------ groups

    def _detected_groups_ordered(self):
        """Ordered list of groups of detected colors (each a list of 1+ color
        dicts). When auto_link_siblings is on, hex/rgb siblings of the same
        real color are grouped together; otherwise every detected id is its
        own singleton group."""
        if not self.auto_link_siblings:
            return [[c] for c in self.detected_colors]
        groups_by_hex = color_detector.grouped_by_hex(self.detected_colors)
        ordered = []
        seen = set()
        for c in self.detected_colors:
            if c["id"] in seen:
                continue
            group = groups_by_hex[c["color"].lower()]
            for m in group:
                seen.add(m["id"])
            ordered.append(group)
        return ordered

    def _detected_group_lookup(self) -> dict:
        """{old_id: group} for every detected color -- computes
        _detected_groups_ordered() exactly ONCE and reuses it. Callers that
        need a group per entry in a loop MUST build this once and look up in
        it directly (see _refresh_mapping_lists/_mapping_groups_in_order)
        instead of redoing the full O(detected colors) grouping from scratch
        per entry -- fine for a single lookup, but O(entries x detected)
        done per mapping entry. With a few hundred/thousand detected colors
        (a realistic scan of many dotfiles) that quadratic-ish blowup is
        exactly what made the mapping screen -- and the move-up/down
        buttons, which refresh it -- visibly hang."""
        lookup = {}
        for group in self._detected_groups_ordered():
            for c in group:
                lookup[c["id"]] = group
        return lookup

    def _group_role(self, group, roles=None):
        """The role currently on file for this group's representative color
        -- role_key is by VALUE (type+hex), so this works regardless of
        whether the group is currently in the mapping or not. roles: pass an
        already-loaded read_color_roles() when the caller is looping over
        many groups in one refresh, so the file isn't re-read/re-parsed per
        group -- omit only for a genuine one-off lookup."""
        if roles is None:
            roles = color_detector.read_color_roles(self.config.color_roles_json)
        return color_detector.role_of(roles, color_detector.role_key(group[0]["type"], group[0]["color"]))

    def _on_group_role_clicked(self, group):
        """Cycles the WHOLE group's role atomically (every member, e.g. both
        the hex and hex_from_rgb sibling when auto-link is on) -- a group is
        one identity everywhere else in this app (adding it, removing it),
        roles follow the same rule. Any pairing that no longer makes sense
        after the change (this group stops being a foreground/background, or
        was the pair target of some other foreground) gets cleaned up too --
        see clear_dangling_pairs_after_role_change."""
        roles = color_detector.read_color_roles(self.config.color_roles_json)
        key = color_detector.role_key(group[0]["type"], group[0]["color"])
        current = color_detector.role_of(roles, key)
        new_role = color_detector.cycle_role(current)
        for c in group:
            k = color_detector.role_key(c["type"], c["color"])
            roles = color_detector.clear_dangling_pairs_after_role_change(roles, k, new_role)
            if new_role is None:
                roles.pop(k, None)
            else:
                roles[k] = {"role": new_role, "pair": None}
        color_detector.write_color_roles(self.config.color_roles_json, roles)
        self._refresh_all()

    def _group_for_hex(self, hex_value):
        """The currently-visible detected group whose representative color
        is `hex_value` -- used to resolve a bare hex (e.g. from the
        background side's "linked foregrounds" list) back into a full
        group, so an unlink/relink action can route through
        _on_group_pair_selected's "a group is one identity" treatment."""
        for group in self._detected_groups_ordered():
            if group[0]["color"] == hex_value:
                return group
        return None

    def _build_pair_widget(self, group, roles=None, role=None):
        """The expandable "Background -> color"/"Foreground -> color"
        section under a detected-color chip (see
        chip_builders.build_group_chip's pair_widget param) -- None if
        there's nothing to show for this group. A FOREGROUND-tagged group
        gets an interactive selector (pairing lives on the foreground side
        of color_roles.json, see color_detector.set_pair) with a small
        swatch showing the current link at a glance. A BACKGROUND-tagged
        group is ALSO interactive -- each currently-linked foreground shows
        its own swatch + an unlink button, plus a "link another
        foreground" selector to add more (many-to-one: unlike the palette
        side, one background can have several linked foregrounds
        simultaneously, so this can't be a single-select dropdown the way
        the foreground side's is).

        roles/role: pass an already-loaded read_color_roles() (and, if the
        caller already computed it, the group's role via _group_role) when
        looping over many groups in one refresh -- avoids re-reading
        color_roles.json and re-deriving the role a 2nd/3rd time per row.
        Both are computed locally if omitted, for one-off callers.

        Deduped/matched by hex VALUE throughout, never by exact role_key: a
        real color detected in both a "hex" and "hex_from_rgb"
        representation carries the SAME role/pairing on each independently
        (see color_detector.compute_role_pairs' docstring) -- without this,
        a background would show up twice in the dropdown, and a linked
        foreground could appear duplicated (or get MISSED entirely, if its
        stored `pair` happens to reference a different sibling
        representation than this group's own)."""
        if roles is None:
            roles = color_detector.read_color_roles(self.config.color_roles_json)
        if role is None:
            role = self._group_role(group, roles)
        own_hex = group[0]["color"]

        if role == "foreground":
            seen_hex = set()
            backgrounds = []
            for k, e in roles.items():
                if e["role"] != "background":
                    continue
                hex_val = k.split(":", 1)[1]
                if hex_val in seen_hex:
                    continue
                seen_hex.add(hex_val)
                backgrounds.append((k, hex_val))

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
            box.append(Gtk.Label(label="Background →", css_classes=["dim-label", "caption"]))

            key = color_detector.role_key(group[0]["type"], own_hex)
            current_pair = color_detector.pair_of(roles, key)
            current_pair_hex = current_pair.split(":", 1)[1] if current_pair else None
            box.append(_build_swatch_circle(current_pair_hex, size=16))

            options = ["(not linked)"] + [f"#{hex_val}" for _k, hex_val in backgrounds]
            dropdown = Gtk.DropDown(model=Gtk.StringList.new(options))
            selected = 0
            for i, (_k, hex_val) in enumerate(backgrounds, start=1):
                if hex_val == current_pair_hex:
                    selected = i
                    break
            dropdown.set_selected(selected)

            def on_selected(dd, _pspec, group=group, backgrounds=backgrounds):
                idx = dd.get_selected()
                bg_key = None if idx == 0 else backgrounds[idx - 1][0]
                self._on_group_pair_selected(group, bg_key)

            dropdown.connect("notify::selected", on_selected)
            box.append(dropdown)
            return box

        if role == "background":
            seen_hex = set()
            linked_hexes = []
            for k, e in roles.items():
                if e["role"] != "foreground" or not e.get("pair"):
                    continue
                if e["pair"].split(":", 1)[1] != own_hex:
                    continue
                fg_hex = k.split(":", 1)[1]
                if fg_hex in seen_hex:
                    continue
                seen_hex.add(fg_hex)
                linked_hexes.append(fg_hex)

            candidates = []
            seen_candidate = set(linked_hexes)
            for k, e in roles.items():
                if e["role"] != "foreground":
                    continue
                hex_val = k.split(":", 1)[1]
                if hex_val in seen_candidate:
                    continue
                seen_candidate.add(hex_val)
                candidates.append(hex_val)

            if not linked_hexes and not candidates:
                return None

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for fg_hex in linked_hexes:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
                row.append(_build_swatch_circle(fg_hex, size=16))
                row.append(Gtk.Label(label=f"Foreground → #{fg_hex}", xalign=0,
                                     css_classes=["dim-label", "caption"], hexpand=True))
                unlink_button = Gtk.Button(icon_name="edit-clear-symbolic", css_classes=["flat", "circular"],
                                           tooltip_text="Unlink", valign=Gtk.Align.CENTER)

                def on_unlink(_b, fg_hex=fg_hex):
                    fg_group = self._group_for_hex(fg_hex)
                    if fg_group is not None:
                        self._on_group_pair_selected(fg_group, None)

                unlink_button.connect("clicked", on_unlink)
                row.append(unlink_button)
                box.append(row)

            if candidates:
                add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
                add_row.append(Gtk.Label(label="+ Foreground →", css_classes=["dim-label", "caption"]))
                options = ["(link another foreground)"] + [f"#{h}" for h in candidates]
                add_dropdown = Gtk.DropDown(model=Gtk.StringList.new(options))

                bg_key = color_detector.role_key(group[0]["type"], own_hex)

                def on_add_selected(dd, _pspec, candidates=candidates, bg_key=bg_key):
                    idx = dd.get_selected()
                    if idx == 0:
                        return
                    fg_group = self._group_for_hex(candidates[idx - 1])
                    if fg_group is not None:
                        self._on_group_pair_selected(fg_group, bg_key)

                add_dropdown.connect("notify::selected", on_add_selected)
                add_row.append(add_dropdown)
                box.append(add_row)

            return box

        return None

    def _on_group_pair_selected(self, fg_group, bg_key):
        """Link (or, bg_key=None, unlink) every member of a foreground-
        tagged detected group to a background detected color chosen from
        the pairing dropdown -- same "a group is one identity" treatment
        _on_group_role_clicked already gives roles. If the chosen
        background isn't currently in the mapping, add its WHOLE group
        (every detected color sharing its hex value, hex AND hex_from_rgb
        alike -- same "a group is one identity" precedent
        _add_group_to_mapping already follows) first, so the link actually
        affects generation instead of being inert metadata (see
        [[color-roles-design]]'s pairing rework)."""
        roles = color_detector.read_color_roles(self.config.color_roles_json)
        if bg_key is not None and self.mapping is not None:
            bg_hex = bg_key.split(":", 1)[1]
            present_ids = {e["old_id"] for e in self.mapping.entries}
            for c in self.detected_colors:
                if c["color"] == bg_hex and c["id"] not in present_ids:
                    self.mapping.add_or_update(c["id"], None)
        for c in fg_group:
            fg_key = color_detector.role_key(c["type"], c["color"])
            roles = color_detector.set_pair(roles, fg_key, bg_key)
        color_detector.write_color_roles(self.config.color_roles_json, roles)
        self._refresh_all()

    def _on_palette_role_clicked(self, entry):
        """Cycles a palette color's role. Uses palette_shift.set_role (not
        edit_color) so this never marks a generated color as hand-edited --
        only the role changes, never the hex/origin."""
        new_role = color_detector.cycle_role(entry.get("role"))
        palette_shift.set_role(self.new_palette_path, entry["id"], new_role)
        self.new_palette = palette_store.read_palette_csv(self.new_palette_path)
        self._refresh_all()

    def _build_palette_pair_widget(self, entry):
        """The "Pair -> color" selector under a palette-color chip (see
        chip_builders.build_palette_chip's pair_widget param) -- None if
        this color has no role (pairing only makes sense between two
        role-tagged colors). Unlike the detected side (many-to-one, `pair`
        lives only on the foreground entry), palette-side pairing
        (palette_shift.set_pair) is strictly 1-to-1 and symmetric --
        linking either side to a new partner drops whatever it was
        previously linked to (see palette_shift._clear_pairing). So both
        foreground AND background palette chips get the SAME interactive
        selector here, listing every OTHER currently role-tagged color of
        the OPPOSITE role."""
        role = entry.get("role")
        if role is None:
            return None
        opposite = "background" if role == "foreground" else "foreground"
        candidates = [e for e in self.new_palette if e.get("role") == opposite]

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        box.append(Gtk.Label(label="Pair →", css_classes=["dim-label", "caption"]))

        current_pair_id = entry.get("paired_id")
        current_pair_hex = next((c["hex"] for c in candidates if c["id"] == current_pair_id), None)
        box.append(_build_swatch_circle(current_pair_hex, size=16))

        options = ["(not linked)"] + [f"#{c['hex']} (id {c['id']})" for c in candidates]
        dropdown = Gtk.DropDown(model=Gtk.StringList.new(options))
        selected = 0
        for i, c in enumerate(candidates, start=1):
            if c["id"] == current_pair_id:
                selected = i
                break
        dropdown.set_selected(selected)

        def on_selected(dd, _pspec, entry=entry, candidates=candidates):
            idx = dd.get_selected()
            other_id = None if idx == 0 else candidates[idx - 1]["id"]
            self._on_palette_pair_selected(entry, other_id)

        dropdown.connect("notify::selected", on_selected)
        box.append(dropdown)
        return box

    def _on_palette_pair_selected(self, entry, other_id):
        """Link (or, other_id=None, unlink) a palette color to another
        role-tagged palette color of the opposite role, chosen from the
        pairing dropdown -- see palette_shift.set_pair."""
        palette_shift.set_pair(self.new_palette_path, entry["id"], other_id)
        self.new_palette = palette_store.read_palette_csv(self.new_palette_path)
        self._refresh_all()

    def _palette_entry_matches_filter(self, entry):
        idx = self.palette_role_filter.get_selected()
        role = entry.get("role")
        if idx == 1:
            return role == "foreground"
        if idx == 2:
            return role == "background"
        if idx == 3:
            return role is None
        return True  # idx == 0 ("All"), or nothing selected yet

    def _on_palette_role_filter_changed(self, _dropdown, _pspec):
        self._refresh_palette_list()

    def _add_group_to_mapping(self, group):
        for c in group:
            self.mapping.add_or_update(c["id"], None)
        self.active_group_ids = frozenset(c["id"] for c in group)
        self._refresh_all()

    def _remove_group(self, old_ids):
        for oid in old_ids:
            self.mapping.remove(oid)
        if self.active_group_ids and set(self.active_group_ids) & set(old_ids):
            self.active_group_ids = frozenset()
        self._refresh_all()

    def _mapping_groups_in_order(self, group_lookup=None):
        """The mapping's entries grouped exactly like _refresh_mapping_lists
        numbers them (hex/rgb siblings collapsed into one row), but keeping
        each group's actual entry dicts -- the shared basis for both
        reordering and swapping the source of an existing mapping row.
        group_lookup: pass an already-built _detected_group_lookup() when the
        caller (_refresh_mapping_lists) already has one, so it isn't rebuilt
        a second time in the same refresh. Looks up each group's entries via
        entries_by_old_id (O(1) each) rather than rescanning self.mapping.entries
        per group, which used to make this O(mapping entries²)."""
        groups = []
        visited = set()
        entries_by_old_id = {e["old_id"]: e for e in self.mapping.entries}
        if group_lookup is None:
            group_lookup = self._detected_group_lookup()
        for entry in self.mapping.entries:
            old_id = entry["old_id"]
            if old_id in visited:
                continue
            full_group = group_lookup.get(old_id) or [{"id": old_id}]
            member_ids = {m["id"] for m in full_group} & entries_by_old_id.keys()
            member_entries = [entries_by_old_id[oid] for oid in member_ids]
            visited |= member_ids
            groups.append((member_ids, member_entries))
        return groups

    def _reorder_mapping_group(self, group_old_ids, direction: int):
        """Move the mapping row for this detected-color group one position
        earlier (direction=-1) or later (direction=+1) among the numbered
        mapping rows -- the "editable position" reordering the mapping
        screen didn't have before. Siblings (hex/rgb of the same real
        color) always move together, since they're numbered as one row."""
        groups = self._mapping_groups_in_order()
        idx = next((i for i, (ids, _e) in enumerate(groups) if ids == set(group_old_ids)), None)
        if idx is None:
            return
        new_idx = idx + direction
        if not (0 <= new_idx < len(groups)):
            return
        groups[idx], groups[new_idx] = groups[new_idx], groups[idx]
        self.mapping.entries = [e for _ids, es in groups for e in es]
        self.mapping.save()
        self._refresh_all()

    def _swap_mapping_source(self, group_old_ids, new_group):
        """Replace which detected color(s) feed an ALREADY-mapped entry with
        a different (currently unmapped) detected color group, keeping that
        entry's position and whatever it's currently assigned to -- the
        source-side equivalent of picking a new target palette color for an
        existing entry (see _on_available_palette_activated), without first
        having to remove anything."""
        old_ids = set(group_old_ids)
        entries = self.mapping.entries
        positions = [i for i, e in enumerate(entries) if e["old_id"] in old_ids]
        if not positions:
            return
        insert_at = positions[0]
        new_id = next((entries[i]["new_id"] for i in positions if entries[i]["new_id"] is not None), None)
        kept = [e for i, e in enumerate(entries) if i not in positions]
        new_entries = [{"old_id": m["id"], "new_id": new_id} for m in new_group]
        kept[insert_at:insert_at] = new_entries
        self.mapping.entries = kept
        self.mapping.save()

    def _quick_link(self, sibling_id, source_old_id):
        self.mapping.link(sibling_id, link_to_old_id=source_old_id)
        self._refresh_all()

    def _on_relink_drift(self):
        """One or more detected colors drifted to a different id on the last
        rescan -- explicit, user-triggered re-link (never automatic, see
        mapping_store.refresh_identity_stamps' docstring). Resolves every
        pending drift finding as one atomic batch (a rank-swap between two
        colors, the common case, can only be fixed correctly all at once)."""
        self.mapping.apply_drift_relinks(self._mapping_drift["driftable"])
        self._mapping_drift["driftable"] = []
        self._refresh_all()

    # ------------------------------------------------------------ row builders

    @staticmethod
    def _clear_listbox(listbox):
        child = listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            listbox.remove(child)
            child = nxt

    # ----------------------------------------------------------------- refresh

    def _refresh_all(self):
        self._refresh_detected_list()
        self._refresh_palette_list()
        self._refresh_mapping_lists()
        self._refresh_wallpaper_panel()
        self._refresh_warnings()
        self.apply_button.set_sensitive(bool(self.mapping and self.mapping.resolved_entries()))

    def _refresh_wallpaper_panel(self):
        self.mapping_palette_header.set_label(
            f"Palette mapping: {self.new_palette_path or '(none)'}"
        )
        image_path = None
        if self.new_palette_path:
            meta = palette_store.read_palette_meta(self.new_palette_path)
            # preview_image (an explicit user override) always wins when
            # present; the generation source image is only a fallback --
            # both are existence-checked so a moved/deleted file never
            # crashes this, it just falls through to the placeholder.
            candidates = []
            if meta.get("preview_image"):
                candidates.append(meta["preview_image"])
            if meta.get("generated") and meta.get("image"):
                candidates.append(meta["image"])
            for candidate in candidates:
                expanded = color_detector.expand_path(candidate)
                if os.path.isfile(expanded):
                    image_path = expanded
                    break

        has_image = image_path is not None
        self.palette_image_picture.set_visible(has_image)
        self.palette_image_placeholder.set_visible(not has_image)
        if has_image:
            self.palette_image_picture.set_filename(dialogs.resolve_gif_safe_image_source(image_path))

    @staticmethod
    def _group_matches_search(group, query: str) -> bool:
        """query already lowercased/stripped. Matches on hex, the hex/rgb
        type label, or any occurrence's file path -- covers "I remember it
        was in some css file" as well as "I remember the color"."""
        if not query:
            return True
        for c in group:
            if query in c["color"].lower():
                return True
            if query in _TYPE_DISPLAY.get(c["type"], c["type"]).lower():
                return True
            if any(query in f.lower() for f in c.get("files", [])):
                return True
        return False

    def _refresh_detected_list(self):
        self._clear_listbox(self.available_detected_listbox)
        present_ids = {e["old_id"] for e in self.mapping.entries} if self.mapping else set()
        query = self.detected_search_entry.get_text().strip().lower()
        groups = [
            g for g in self._detected_groups_ordered()
            if not ({c["id"] for c in g} & present_ids)  # already (at least partially) in the mapping
            and self._group_matches_search(g, query)
        ]
        if self.detected_by_folder:
            self._render_detected_by_folder(groups)
        else:
            self._render_detected_flat(groups)

    def _render_detected_flat(self, groups):
        roles = color_detector.read_color_roles(self.config.color_roles_json)
        for group in groups:
            role = self._group_role(group, roles)
            chip = _build_group_chip(
                group, role=role, role_cb=self._on_group_role_clicked,
                pair_widget=self._build_pair_widget(group, roles, role),
            )
            chip.set_addable(lambda g=group: self._add_group_to_mapping(g))
            row = Gtk.ListBoxRow(child=chip)
            row.group = group
            self.available_detected_listbox.append(row)

    def _render_detected_by_folder(self, groups):
        """Same groups (and same "add" semantics) as the flat view -- just
        presented grouped by top-level folder, with a mini-separator per file
        and that file's color chips right below it. A group with occurrences
        in several files gets a fresh chip instance under EACH of them (a
        widget can only have one parent), but "add" on any of them adds the
        exact same underlying group, same as clicking it in the flat view."""
        all_files = set()
        for g in groups:
            for c in g:
                all_files.update(c["files"])
        if not all_files:
            return

        roles = color_detector.read_color_roles(self.config.color_roles_json)
        config_root = os.path.join(os.path.expanduser("~"), ".config")
        for folder, files in color_detector.group_paths_by_top_level(sorted(all_files)):
            if folder == config_root:
                title = f"{to_home_relative(folder)}  ·  loose"
            else:
                title = to_home_relative(folder)
            expander = Adw.ExpanderRow(title=title, subtitle=f"{len(files)} file(s)")
            expander.set_expanded(True)

            for abs_path, display in files:
                groups_here = [g for g in groups if any(abs_path in c["files"] for c in g)]
                if not groups_here:
                    continue
                sep_label = Gtk.Label(
                    label=display, xalign=0, wrap=True,
                    css_classes=["dim-label", "caption-heading"],
                    margin_start=6, margin_top=6, margin_bottom=2,
                )
                expander.add_row(Gtk.ListBoxRow(child=sep_label, activatable=False, selectable=False))
                for group in groups_here:
                    role = self._group_role(group, roles)
                    chip = _build_group_chip(
                        group, role=role, role_cb=self._on_group_role_clicked,
                        pair_widget=self._build_pair_widget(group, roles, role),
                    )
                    chip.set_addable(lambda g=group: self._add_group_to_mapping(g))
                    row = Gtk.ListBoxRow(child=chip)
                    row.group = group
                    expander.add_row(row)

            self.available_detected_listbox.append(expander)

    def _refresh_palette_list(self):
        has_palette = self.new_palette_path is not None
        self.palette_empty_box.set_visible(not has_palette)
        self.available_palette_listbox.set_visible(has_palette)
        self.add_color_button.set_visible(has_palette)
        self.palette_role_filter.set_visible(has_palette)
        self.modifiers_button.set_sensitive(has_palette and bool(self.new_palette))

        self._clear_listbox(self.available_palette_listbox)
        if not has_palette:
            return

        usage_counts = {}
        if self.mapping:
            for e in self.mapping.resolved_entries():
                usage_counts[e["new_id"]] = usage_counts.get(e["new_id"], 0) + 1

        for entry in self.new_palette:
            if not self._palette_entry_matches_filter(entry):
                continue
            chip = _build_palette_chip(entry, usage_count=usage_counts.get(entry["id"], 0),
                                       role_cb=self._on_palette_role_clicked,
                                       pair_widget=self._build_palette_pair_widget(entry))
            chip.set_editable(lambda e=entry: self._on_palette_edit(e))
            chip.set_deletable(lambda e=entry: self._on_palette_delete(e))
            row = Gtk.ListBoxRow(child=chip)
            row.item_id = entry["id"]
            self.available_palette_listbox.append(row)

    def _refresh_mapping_lists(self):
        self._clear_listbox(self.mapping_old_listbox)
        self._clear_listbox(self.mapping_new_listbox)
        self._clear_listbox(self.mapping_preview_listbox)
        if self.mapping is None:
            return

        resolved = self.mapping.resolved_entries()
        collisions = conflicts.find_case1_collisions(self.detected_colors, self.new_palette, resolved)
        collision_old_ids = {c["old_id"] for c in collisions}
        convergence = conflicts.find_target_convergence(
            self.detected_colors, self.new_palette, resolved, sibling_groups=self.siblings
        )
        convergence_old_ids = {oid for c in convergence for oid in c["old_ids"]}

        detected_by_id = {c["id"]: c for c in self.detected_colors}
        palette_by_id = {p["id"]: p for p in self.new_palette}
        entry_by_old_id = {e["old_id"]: e for e in self.mapping.entries}
        present_ids = set(entry_by_old_id.keys())
        group_lookup = self._detected_group_lookup()
        total_groups = len(self._mapping_groups_in_order(group_lookup))
        roles = color_detector.read_color_roles(self.config.color_roles_json)

        visited = set()
        idx = 0
        for entry in self.mapping.entries:
            old_id = entry["old_id"]
            if old_id in visited:
                continue

            full_group = group_lookup.get(old_id) or [detected_by_id[old_id]]
            group_members = [c for c in full_group if c["id"] in present_ids]
            for m in group_members:
                visited.add(m["id"])
            idx += 1

            group_old_ids = {m["id"] for m in group_members}
            if group_old_ids & collision_old_ids:
                warning = "Collides with another detected color"
            elif group_old_ids & convergence_old_ids:
                warning = "Converges with another color in the mapping (loses the distinction)"
            else:
                warning = None

            group_role = self._group_role(group_members, roles)
            old_chip = _build_group_chip(
                group_members, number=idx, warning=warning,
                removable_cb=(lambda ids=tuple(group_old_ids): self._remove_group(ids)),
                role=group_role, role_cb=self._on_group_role_clicked,
                pair_widget=self._build_pair_widget(group_members, roles, group_role),
                move_up_cb=(
                    (lambda ids=tuple(group_old_ids): self._reorder_mapping_group(ids, -1))
                    if idx > 1 else None
                ),
                move_down_cb=(
                    (lambda ids=tuple(group_old_ids): self._reorder_mapping_group(ids, 1))
                    if idx < total_groups else None
                ),
            )
            old_row = Gtk.ListBoxRow(child=old_chip)
            old_row.group_ids = tuple(group_old_ids)
            if self.active_group_ids and group_old_ids == set(self.active_group_ids):
                old_chip.add_css_class("active-row")
            self.mapping_old_listbox.append(old_row)

            new_id = None
            for m in group_members:
                e = entry_by_old_id.get(m["id"])
                if e and e["new_id"] is not None:
                    new_id = e["new_id"]
                    break

            if new_id is not None and new_id in palette_by_id:
                new_chip = _build_palette_chip(palette_by_id[new_id], number=idx, warning=warning,
                                               role_cb=self._on_palette_role_clicked,
                                               pair_widget=self._build_palette_pair_widget(palette_by_id[new_id]))
                new_hex = palette_by_id[new_id]["hex"]
            else:
                new_chip = _build_empty_target_chip(idx, warning=warning)
                new_hex = None
            new_row = Gtk.ListBoxRow(child=new_chip)
            new_row.group_ids = tuple(group_old_ids)
            self.mapping_new_listbox.append(new_row)

            preview_row = _build_mapping_preview_row(group_members[0]["color"], new_hex, warning=warning)
            self.mapping_preview_listbox.append(Gtk.ListBoxRow(child=preview_row, activatable=False))

    def _refresh_warnings(self):
        self._clear_listbox_box(self.warnings_box)
        added_any = False

        if self.mapping is not None:
            resolved = self.mapping.resolved_entries()

            collisions = conflicts.find_case1_collisions(self.detected_colors, self.new_palette, resolved)
            for c in collisions:
                text = (
                    f"Color #{c['new_hex']} chosen for id {c['old_id']} already exists in the detected "
                    f"palette (id {c['conflict_with_ids']}). This may break the replacement of that other color."
                )
                self.warnings_box.append(_build_warning_row(text))
                added_any = True

            convergence = conflicts.find_target_convergence(
                self.detected_colors, self.new_palette, resolved, sibling_groups=self.siblings
            )
            for c in convergence:
                text = (
                    f"Colors with id {c['old_ids']} will all end up as #{c['target_hex']}. "
                    "If that wasn't intentional, you'll lose the distinction between them on a future re-scan."
                )
                self.warnings_box.append(_build_warning_row(text))
                added_any = True

            detected_roles = color_detector.read_color_roles(self.config.color_roles_json)
            role_mismatches = conflicts.find_role_mismatches(
                self.detected_colors, self.new_palette, resolved, detected_roles
            )
            for m in role_mismatches:
                text = (
                    f"Detected color id {m['old_id']} is marked as {m['detected_role']}, but it's being "
                    f"mapped to a palette color (id {m['new_id']}) marked as {m['palette_role']}. "
                    "This may cause poor contrast."
                )
                self.warnings_box.append(_build_warning_row(text))
                added_any = True

            pair_mismatches = conflicts.find_pair_mismatches(
                self.detected_colors, self.new_palette, resolved, detected_roles
            )
            for m in pair_mismatches:
                text = (
                    f"Detected colors id {m['fg_old_id']} (foreground) and id {m['bg_old_id']} "
                    f"(background) are linked as a pair, but they map to palette colors "
                    f"(id {m['fg_new_id']} / id {m['bg_new_id']}) that aren't linked to each other. "
                    "This may cause poor contrast."
                )
                self.warnings_box.append(_build_warning_row(text))
                added_any = True

            for d in self._mapping_drift.get("driftable", []):
                text = (
                    f"Detected color id {d['old_id']} (#{d['hex']}) seems to have moved to "
                    f"id {d['correct_old_id']} in the last scan."
                )
                # The button resolves EVERY pending drift finding at once (not
                # just this row) -- they were all computed from the same scan,
                # and the most common case (two colors trading rank) can only
                # be fixed correctly as one atomic batch, never one at a time
                # (see mapping_store.apply_drift_relinks).
                self.warnings_box.append(_build_warning_row(text, "Re-link", self._on_relink_drift))
                added_any = True

            for d in self._mapping_drift.get("orphaned", []):
                text = (
                    f"Mapped color id {d['old_id']} (#{d['hex']}) no longer appears in your "
                    "scanned files."
                )
                self.warnings_box.append(_build_warning_row(text))
                added_any = True

            unpaired = color_detector.tagged_without_pair(detected_roles)
            if unpaired:
                text = (
                    f"{len(unpaired)} detected color(s) are marked foreground/background but have no "
                    "linked pair: they aren't taken into account for generation."
                )
                self.warnings_box.append(_build_warning_row(text))
                added_any = True

            if not self.auto_link_siblings:
                present_ids = {e["old_id"] for e in self.mapping.entries}
                detected_by_id = {c["id"]: c for c in self.detected_colors}
                seen_pairs = set()
                for old_id in present_ids:
                    color = detected_by_id.get(old_id)
                    if not color:
                        continue
                    group = self.siblings.get(color["color"].lower())
                    if not group:
                        continue
                    for sib in group:
                        if sib["id"] != old_id and sib["id"] not in present_ids:
                            pair = tuple(sorted((old_id, sib["id"])))
                            if pair in seen_pairs:
                                continue
                            seen_pairs.add(pair)
                            text = (
                                f"Id {old_id} (#{color['color']}) also appears as id {sib['id']} "
                                f"({_TYPE_DISPLAY.get(sib['type'], sib['type'])}) and isn't in the mapping."
                            )
                            self.warnings_box.append(
                                _build_warning_row(
                                    text, "Add too",
                                    lambda o=old_id, s=sib["id"]: self._quick_link(s, o),
                                )
                            )
                            added_any = True

        self.warnings_revealer.set_reveal_child(added_any)

    @staticmethod
    def _clear_listbox_box(box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    # ------------------------------------------------------------- row clicks

    def _on_available_detected_activated(self, _listbox, row):
        # A currently-selected mapping row (picked via _on_mapping_old_activated)
        # means "swap which detected color feeds this entry" instead of "add
        # a brand new entry" -- the source-side equivalent of picking a new
        # target palette color for an existing entry, without deleting first.
        if self.active_group_ids and any(
            self.mapping._find(oid) is not None for oid in self.active_group_ids
        ):
            self._swap_mapping_source(self.active_group_ids, row.group)
            self.active_group_ids = frozenset()
            self._refresh_all()
            return
        self._add_group_to_mapping(row.group)

    def _on_mapping_old_activated(self, _listbox, row):
        ids = frozenset(row.group_ids)
        self.active_group_ids = frozenset() if self.active_group_ids == ids else ids
        self._refresh_mapping_lists()

    def _on_available_palette_activated(self, _listbox, row):
        if not self.active_group_ids:
            dialogs.toast(self.toast_overlay, "First pick which color to replace (left column).")
            return
        for oid in self.active_group_ids:
            self.mapping.add_or_update(oid, row.item_id)
        self.active_group_ids = frozenset()
        self._refresh_all()

    def _on_mapping_new_activated(self, _listbox, row):
        for oid in row.group_ids:
            self.mapping.add_or_update(oid, None)
        self.active_group_ids = frozenset(row.group_ids)
        self._refresh_all()

    # --------------------------------------------------------- apply/restore

    def _on_window_active_changed(self, _window, _pspec):
        # Only when GAINING focus, and only once real detection has populated
        # the window (avoids firing mid-startup / before the first scan).
        if self.is_active() and self.detected_colors:
            self._reload_from_disk_if_changed()

    @staticmethod
    def _detected_signature(colors):
        return [(c["id"], c.get("type"), c.get("color")) for c in colors]

    @staticmethod
    def _palette_signature(entries):
        return [e["hex"] for e in entries]

    def _reload_from_disk_if_changed(self):
        """Reload detected/palette state if a CLI run (automatic, shift,
        generate…) changed the files under us. Returns True if anything was
        reloaded, so a caller about to apply can abort and let the user review
        the refreshed state first (comparing content, not mtime, so the GUI's
        own writes never trip it)."""
        disk_palette = (palette_store.read_palette_csv(self.new_palette_path)
                        if self.new_palette_path else [])
        palette_changed = (self._palette_signature(disk_palette)
                           != self._palette_signature(self.new_palette))

        disk_detected = color_detector.read_detected_csv(self.config.detected_palette_csv)
        detected_changed = (bool(disk_detected)
                            and self._detected_signature(disk_detected)
                            != self._detected_signature(self.detected_colors))

        if not palette_changed and not detected_changed:
            return False

        changed = []
        if palette_changed:
            self.new_palette = disk_palette
            changed.append("the palette")
        if detected_changed:
            # Files changed under us -> re-detect so the mapping lines up with
            # the colors actually in the files now (rebuilds it from mapping.csv).
            self._finish_detection(detect_diff.run_detect(self.config), persist=True)
            changed.append("the detected colors")
        else:
            self._refresh_all()

        dialogs.toast(
            self.toast_overlay,
            f"External changes detected ({' and '.join(changed)}). Reloaded — review and re-apply.",
        )
        return True

    def _on_apply_clicked(self, dry_run):
        if self._reload_from_disk_if_changed():
            return  # state was stale; let the user review the refreshed palette/mapping first
        if not self.mapping or not self.mapping.resolved_entries():
            dialogs.toast(self.toast_overlay, "No color has been mapped yet.")
            return

        entries = self.mapping.resolved_entries()
        # Resolve entries against the current palette via the one shared
        # resolver every apply path uses (mapping_store.resolve_apply_targets).
        # Unlike CLI, the GUI is interactive -- a tier="blocked" refuses
        # outright (this used to be a silent data-loss bug: apply_mapping just
        # dropped any replacement whose new_id wasn't in the palette, with no
        # warning at all) and a tier="compacted" is folded into the same
        # confirm dialog used for collisions/convergence below, requiring an
        # explicit click before applying with the (possibly reordered) entries.
        resolution = mapping_store.resolve_apply_targets(entries, self.new_palette)
        if resolution["tier"] == "blocked":
            dialogs.toast(
                self.toast_overlay,
                f"Can't apply: the palette has {resolution['available']} color(s), but "
                f"the mapping needs at least {resolution['needed']} (distinct ids used).",
            )
            return
        entries = resolution["final_entries"]
        reorder_warning = resolution["warning"] if resolution["tier"] == "compacted" else None

        collisions = conflicts.find_case1_collisions(self.detected_colors, self.new_palette, entries)
        convergence = conflicts.find_target_convergence(
            self.detected_colors, self.new_palette, entries, sibling_groups=self.siblings
        )

        def do_apply():
            results = color_replacer.apply_mapping(
                self.detected_colors, self.new_palette, entries, self.config.backup_dir, dry_run=dry_run
            )
            total = sum(r.get("count", 0) for r in results)
            errors = [r for r in results if "error" in r]
            verb = "Simulated" if dry_run else "Applied"
            msg = f"{verb}: {total} replacement(s) across {len(results)} file operation(s)."
            if errors:
                msg += f" ({len(errors)} error(s))"
            dialogs.toast(self.toast_overlay, msg)

            if dry_run:
                self.status_label.set_label(msg)
                return

            dialogs.toast(self.toast_overlay, f"Backup at: {self.config.backup_dir}")
            role_collisions, pair_collisions = color_detector.rekey_roles_after_apply(
                self.config.color_roles_json, self.detected_colors, self.new_palette, entries
            )
            if role_collisions:
                new_keys = ", ".join(k for k, _old in role_collisions)
                dialogs.toast(
                    self.toast_overlay,
                    f"{len(role_collisions)} new color(s) were left without a role assigned due to convergence: {new_keys}.",
                )
            if pair_collisions:
                fg_keys = ", ".join(k for k, _old in pair_collisions)
                dialogs.toast(
                    self.toast_overlay,
                    f"{len(pair_collisions)} color(s) lost their bg/fg link after the apply: {fg_keys}.",
                )
            # The colors just replaced no longer exist in the files BY
            # DESIGN -- re-stamp this mapping's identity to the NEW colors
            # now actually there, so the drift refresh inside
            # _finish_detection below doesn't mistake "I just replaced this"
            # for real drift and flag it orphaned.
            self.mapping.entries = mapping_store.stamp_applied_entries(
                self.mapping.entries, entries, self.new_palette, self.detected_colors,
            )
            self.mapping.save()
            # Files on disk just changed -- the old mapping's ids point at
            # colors that no longer exist, so re-scan and start fresh.
            self._finish_detection(detect_diff.run_detect(self.config), persist=True)
            restart_actions.write_wallpaper_state(self.config, self.new_palette_path)
            actions = restart_actions.read_restart_actions(self.config)
            started = restart_actions.run_enabled(
                actions, extra_env=restart_actions.wallpaper_env(self.new_palette_path)
            )
            if started:
                names = ", ".join(a["label"] for a in started)
                dialogs.toast(self.toast_overlay, f"Restarting: {names}")

        if (collisions or convergence or reorder_warning) and not dry_run:
            parts = []
            if reorder_warning:
                parts.append(reorder_warning)
            if collisions:
                names = ", ".join(f"id {c['old_id']}" for c in collisions)
                parts.append(f"{len(collisions)} color(s) ({names}) already exist in the detected palette.")
            if convergence:
                summary = "; ".join(f"ids {c['old_ids']} → #{c['target_hex']}" for c in convergence)
                parts.append(f"{len(convergence)} group(s) of colors will converge to the same result: {summary}.")
            body = (
                " ".join(parts)
                + "\n\nThis may make it impossible for a future re-scan to tell those colors apart "
                  "(this may be intentional if it's exactly what you wanted). Apply anyway?"
            )
            dialogs.ask_confirm(
                self, "Conflicts detected", body, "Apply anyway", do_apply, destructive=True,
            )
        else:
            do_apply()

    def _on_restore_clicked(self, _button):
        if not dialogs.backup_exists(self.config):
            dialogs.toast(self.toast_overlay, "No backup found. Do a real Apply first.")
            return

        def do_restore():
            results = color_replacer.restore_files(self.config.files_to_replace, self.config.backup_dir)
            restored = sum(1 for r in results if r["restored"])
            msg = f"Restored {restored}/{len(results)} file(s) from the backup."
            dialogs.toast(self.toast_overlay, msg)
            # Files on disk just changed back -- re-scan and start fresh, same as after an apply.
            self._finish_detection(detect_diff.run_detect(self.config), persist=True)
            restart_actions.write_wallpaper_state(self.config, self.new_palette_path)

            def do_restart_services():
                started = restart_actions.run_enabled(
                    restart_actions.read_restart_actions(self.config),
                    extra_env=restart_actions.wallpaper_env(self.new_palette_path),
                )
                if started:
                    names = ", ".join(a["label"] for a in started)
                    dialogs.toast(self.toast_overlay, f"Restarting: {names}")

            dialogs.ask_confirm(
                self,
                "Restart services",
                "Do you want to restart the configured services so they pick up the restored theme?",
                "Restart",
                do_restart_services,
            )

        dialogs.ask_confirm(
            self,
            "Restore from backup",
            "This overwrites your files COMPLETELY with the backup copy -- it's not just undoing "
            "the color replacement. Any changes or new code you've added to those files since the "
            "last apply are lost.\n\nRestore anyway?",
            "Restore",
            do_restore,
            destructive=True,
        )
