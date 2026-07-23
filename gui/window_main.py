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

import datetime
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

GUI_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(GUI_DIR)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from backend import color_detector, color_replacer, conflicts, detect_diff, mapping_store, palette_store  # noqa: E402
from backend import palette_generator, restart_actions  # noqa: E402
from backend.config import load_config  # noqa: E402

from . import dialogs  # noqa: E402
from .color_chip import ColorChip, ensure_base_styles  # noqa: E402
from .template_loader import compiled_ui_path  # noqa: E402

_TYPE_DISPLAY = {"hex": "hex", "hex_from_rgb": "rgb"}


@Gtk.Template(filename=compiled_ui_path("window_main.blp"))
class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "MainWindow"

    toast_overlay = Gtk.Template.Child()
    rescan_button = Gtk.Template.Child()
    available_detected_listbox = Gtk.Template.Child()
    mapping_old_listbox = Gtk.Template.Child()
    palette_left_area = Gtk.Template.Child()
    palette_empty_label = Gtk.Template.Child()
    available_palette_listbox = Gtk.Template.Child()
    mapping_new_listbox = Gtk.Template.Child()
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
        self.active_group_ids = frozenset()
        self.auto_link_siblings = True

        self._setup_actions()
        self.rescan_button.connect("clicked", lambda _b: self._start_detection())
        self.restore_button.connect("clicked", self._on_restore_clicked)
        self.test_button.connect("clicked", lambda _b: self._on_apply_clicked(dry_run=True))
        self.apply_button.connect("clicked", lambda _b: self._on_apply_clicked(dry_run=False))
        self.available_detected_listbox.connect("row-activated", self._on_available_detected_activated)
        self.mapping_old_listbox.connect("row-activated", self._on_mapping_old_activated)
        self.available_palette_listbox.connect("row-activated", self._on_available_palette_activated)
        self.mapping_new_listbox.connect("row-activated", self._on_mapping_new_activated)

        GLib.idle_add(self._start_detection)

    # ------------------------------------------------------------------ actions

    def _setup_actions(self):
        for name, handler in (
            ("new-palette", self._action_new_palette),
            ("import-palette", self._action_import_palette),
            ("add-color", self._action_add_color),
            ("generate-palette", self._action_generate_palette),
            ("palette-generation-settings", self._action_palette_generation_settings),
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
                "Desactivar vínculo automático hex/rgb",
                "Un mismo color real puede aparecer en tus archivos tanto en formato hex como en rgb. "
                "Con el vínculo desactivado vas a tener que agregar y asignar cada formato por separado, "
                "y si igual les asignás el mismo color a mano no vas a recibir ningún aviso especial "
                "(se van a tratar como dos colores independientes que casualmente coinciden).\n\n"
                "¿Desactivar de todas formas?",
                "Desactivar",
                do_disable,
                destructive=True,
            )
        else:
            action.set_state(GLib.Variant.new_boolean(True))
            self.auto_link_siblings = True
            self._refresh_all()

    def _action_new_palette(self, _action, _param):
        dialogs.prompt_text(
            self, "Nueva paleta", "Nombre del archivo (sin .csv):", "mi-paleta", "Crear",
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
        dialogs.toast(self.toast_overlay, f"Paleta creada: {os.path.basename(path)}")

    def _action_import_palette(self, _action, _param):
        dialogs.pick_import_palette_file(self, self._on_import_palette_selected)

    def _on_import_palette_selected(self, path):
        entries = palette_store.read_palette_csv(path)
        self._set_new_palette(path, entries)
        dialogs.toast(self.toast_overlay, f"Paleta importada: {len(entries)} color(es)")

    def _action_add_color(self, _action, _param):
        if not self.new_palette_path:
            dialogs.toast(self.toast_overlay, "Primero creá o importá una paleta.")
            return
        dialogs.prompt_add_color(self, self._on_add_color)

    def _on_add_color(self, hex_value, label):
        entry = palette_store.add_color(self.new_palette_path, hex_value, label)
        self.new_palette.append(entry)
        self._refresh_all()

    def _action_generate_palette(self, _action, _param):
        dialogs.pick_image_file(self, self._on_generate_palette_image_picked)

    def _on_generate_palette_image_picked(self, image_path):
        dialogs.prompt_text(
            self, "Generar paleta", "¿Cuántos colores generar?", "6", "Generar",
            lambda text: self._on_generate_palette_count(image_path, text),
        )

    def _on_generate_palette_count(self, image_path, count_text):
        try:
            n_colors = max(1, int(count_text.strip()))
        except ValueError:
            n_colors = 6

        settings = palette_generator.read_generation_settings(self.config)
        try:
            colors = palette_generator.generate_palette(
                image_path, n_colors=n_colors, mode=settings["mode"], saturate=settings["saturate"],
            )
        except Exception as e:
            dialogs.toast(self.toast_overlay, f"No se pudo generar la paleta: {e}")
            return

        base = os.path.splitext(os.path.basename(image_path))[0]
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.config.palettes_created_dir, f"generated-{base}-{ts}.csv")
        entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"]} for i, c in enumerate(colors)]
        palette_store.write_palette_csv(path, entries)

        self._set_new_palette(path, entries)
        dialogs.toast(self.toast_overlay, f"Paleta generada: {len(entries)} color(es) desde {base}")

    def _action_snapshot_detect(self, _action, _param):
        dialogs.prompt_text(
            self, "Guardar snapshot", "Nombre del snapshot (sin .csv):", "detected-manual", "Guardar",
            self._on_snapshot_name,
        )

    def _on_snapshot_name(self, name):
        if not name:
            return
        dest = detect_diff.save_snapshot(self.config, self.detected_colors, name)
        dialogs.toast(self.toast_overlay, f"Guardado: {os.path.basename(dest)}")

    def _action_restart_actions(self, _action, _param):
        dialogs.show_restart_actions_settings(self, self.config)

    def _action_palette_generation_settings(self, _action, _param):
        dialogs.show_palette_generation_settings(self, self.config)

    def _action_scanned_files_settings(self, _action, _param):
        dialogs.show_scanned_files_settings(self, self.config, on_change=self._on_scanned_files_changed)

    def _on_scanned_files_changed(self):
        self.config = load_config()
        self._start_detection()

    def _set_new_palette(self, path, entries):
        if self.mapping is not None and self.mapping.new_palette and self.mapping.new_palette != path:
            # Switching to a genuinely different target palette -- old
            # new_id assignments pointed at the PREVIOUS palette's ids, so
            # they're meaningless here. Keep the old_id selections (what to
            # replace), just clear who they're assigned to.
            for e in self.mapping.entries:
                e["new_id"] = None

        self.new_palette_path = path
        self.new_palette = entries
        if self.mapping is not None:
            self.mapping.new_palette = path
            self.mapping.save()
        self._refresh_all()

    # --------------------------------------------------------------- detection

    def _start_detection(self):
        raw = detect_diff.detect_with_route(self.config, save=False)
        route = raw["route"]
        if route == "a":
            def after_welcome():
                dialogs.show_restart_actions_onboarding(
                    self, self.config, lambda: self._finish_detection(raw["colors"], persist=True)
                )

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

        # Continue the canonical mapping.csv instead of discarding it --
        # otherwise the first save from THIS session (e.g. picking a
        # palette) would wipe out everything built in a previous session,
        # breaking `automatic`'s whole "build once, reuse later" workflow.
        self.mapping = mapping_store.MappingStore(
            self.config.mapping_csv, old_palette=self.config.detected_palette_csv,
            new_palette=self.new_palette_path or "", project_dir=self.config.project_dir,
        ).load()

        if self.mapping.new_palette and not self.new_palette_path:
            self.new_palette_path = self.mapping.new_palette
            self.new_palette = palette_store.read_palette_csv(self.new_palette_path)

        self._refresh_all()
        self.status_label.set_label(f"{len(colors)} color(es) detectado(s).")

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

    def _group_containing(self, old_id):
        for group in self._detected_groups_ordered():
            if any(c["id"] == old_id for c in group):
                return group
        return None

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

    def _quick_link(self, sibling_id, source_old_id):
        self.mapping.link(sibling_id, link_to_old_id=source_old_id)
        self._refresh_all()

    # ------------------------------------------------------------ row builders

    def _build_group_chip(self, group, number=None, warning=None, removable_cb=None):
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
            parts.append(f"{_TYPE_DISPLAY[type_key]}: x{c['count']}")
            file_groups[_TYPE_DISPLAY[type_key]] = c["files"]
        chip.set_meta_text(" · ".join(parts))
        chip.set_file_groups(file_groups)

        chip.set_number(number)
        chip.set_warning(warning)
        chip.set_removable(removable_cb)
        return chip

    def _build_palette_chip(self, entry, number=None, warning=None, usage_count=0):
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

    def _build_empty_target_chip(self, number, warning=None):
        chip = ColorChip()
        chip.swatch.add_css_class("swatch-empty")
        chip.set_hex_text("Sin asignar")
        chip.set_meta_text("Elegí un color de la paleta →")
        chip.set_number(number)
        chip.set_warning(warning)
        return chip

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
        self._refresh_warnings()
        self.apply_button.set_sensitive(bool(self.mapping and self.mapping.resolved_entries()))

    def _refresh_detected_list(self):
        self._clear_listbox(self.available_detected_listbox)
        present_ids = {e["old_id"] for e in self.mapping.entries} if self.mapping else set()
        for group in self._detected_groups_ordered():
            member_ids = {c["id"] for c in group}
            if member_ids & present_ids:
                continue  # already (at least partially) in the mapping
            chip = self._build_group_chip(group)
            chip.set_addable(lambda g=group: self._add_group_to_mapping(g))
            row = Gtk.ListBoxRow(child=chip)
            row.group = group
            self.available_detected_listbox.append(row)

    def _refresh_palette_list(self):
        has_palette = self.new_palette_path is not None
        self.palette_empty_label.set_visible(not has_palette)
        self.available_palette_listbox.set_visible(has_palette)

        self._clear_listbox(self.available_palette_listbox)
        if not has_palette:
            return

        usage_counts = {}
        if self.mapping:
            for e in self.mapping.resolved_entries():
                usage_counts[e["new_id"]] = usage_counts.get(e["new_id"], 0) + 1

        for entry in self.new_palette:
            chip = self._build_palette_chip(entry, usage_count=usage_counts.get(entry["id"], 0))
            row = Gtk.ListBoxRow(child=chip)
            row.item_id = entry["id"]
            self.available_palette_listbox.append(row)

    def _refresh_mapping_lists(self):
        self._clear_listbox(self.mapping_old_listbox)
        self._clear_listbox(self.mapping_new_listbox)
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

        visited = set()
        idx = 0
        for entry in self.mapping.entries:
            old_id = entry["old_id"]
            if old_id in visited:
                continue

            full_group = self._group_containing(old_id) or [detected_by_id[old_id]]
            group_members = [c for c in full_group if c["id"] in present_ids]
            for m in group_members:
                visited.add(m["id"])
            idx += 1

            group_old_ids = {m["id"] for m in group_members}
            if group_old_ids & collision_old_ids:
                warning = "Colisiona con otro color detectado"
            elif group_old_ids & convergence_old_ids:
                warning = "Converge con otro color del mapping (se pierde la distinción)"
            else:
                warning = None

            old_chip = self._build_group_chip(
                group_members, number=idx, warning=warning,
                removable_cb=(lambda ids=tuple(group_old_ids): self._remove_group(ids)),
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
                new_chip = self._build_palette_chip(palette_by_id[new_id], number=idx, warning=warning)
            else:
                new_chip = self._build_empty_target_chip(idx, warning=warning)
            new_row = Gtk.ListBoxRow(child=new_chip)
            new_row.group_ids = tuple(group_old_ids)
            self.mapping_new_listbox.append(new_row)

    def _refresh_warnings(self):
        self._clear_listbox_box(self.warnings_box)
        added_any = False

        if self.mapping is not None:
            resolved = self.mapping.resolved_entries()

            collisions = conflicts.find_case1_collisions(self.detected_colors, self.new_palette, resolved)
            for c in collisions:
                text = (
                    f"El color #{c['new_hex']} elegido para el id {c['old_id']} ya existe en la paleta "
                    f"detectada (id {c['conflict_with_ids']}). Puede romper el reemplazo de ese otro color."
                )
                self.warnings_box.append(self._build_warning_row(text))
                added_any = True

            convergence = conflicts.find_target_convergence(
                self.detected_colors, self.new_palette, resolved, sibling_groups=self.siblings
            )
            for c in convergence:
                text = (
                    f"Los colores con id {c['old_ids']} van a terminar todos en #{c['target_hex']}. "
                    "Si no era intencional, vas a perder la distinción entre ellos en un futuro re-escaneo."
                )
                self.warnings_box.append(self._build_warning_row(text))
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
                                f"El id {old_id} (#{color['color']}) también aparece como id {sib['id']} "
                                f"({_TYPE_DISPLAY.get(sib['type'], sib['type'])}) y no está en el mapping."
                            )
                            self.warnings_box.append(
                                self._build_warning_row(
                                    text, "Agregar también",
                                    lambda o=old_id, s=sib["id"]: self._quick_link(s, o),
                                )
                            )
                            added_any = True

        self.warnings_revealer.set_reveal_child(added_any)

    def _build_warning_row(self, text, action_label=None, action_cb=None):
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

    @staticmethod
    def _clear_listbox_box(box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    # ------------------------------------------------------------- row clicks

    def _on_available_detected_activated(self, _listbox, row):
        self._add_group_to_mapping(row.group)

    def _on_mapping_old_activated(self, _listbox, row):
        ids = frozenset(row.group_ids)
        self.active_group_ids = frozenset() if self.active_group_ids == ids else ids
        self._refresh_mapping_lists()

    def _on_available_palette_activated(self, _listbox, row):
        if not self.active_group_ids:
            dialogs.toast(self.toast_overlay, "Elegí primero qué color reemplazar (columna izquierda).")
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

    def _on_apply_clicked(self, dry_run):
        if not self.mapping or not self.mapping.resolved_entries():
            dialogs.toast(self.toast_overlay, "No hay ningún color mapeado todavía.")
            return

        entries = self.mapping.resolved_entries()
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
            verb = "Simulado" if dry_run else "Aplicado"
            msg = f"{verb}: {total} reemplazo(s) en {len(results)} operación(es) de archivo."
            if errors:
                msg += f" ({len(errors)} error(es))"
            dialogs.toast(self.toast_overlay, msg)

            if dry_run:
                self.status_label.set_label(msg)
                return

            dialogs.toast(self.toast_overlay, f"Backup en: {self.config.backup_dir}")
            # Files on disk just changed -- the old mapping's ids point at
            # colors that no longer exist, so re-scan and start fresh.
            self._finish_detection(detect_diff.run_detect(self.config), persist=True)
            actions = restart_actions.read_restart_actions(self.config)
            started = restart_actions.run_enabled(actions)
            if started:
                names = ", ".join(a["label"] for a in started)
                dialogs.toast(self.toast_overlay, f"Reiniciando: {names}")

        if (collisions or convergence) and not dry_run:
            parts = []
            if collisions:
                names = ", ".join(f"id {c['old_id']}" for c in collisions)
                parts.append(f"{len(collisions)} color(es) ({names}) ya existen en la paleta detectada.")
            if convergence:
                summary = "; ".join(f"ids {c['old_ids']} → #{c['target_hex']}" for c in convergence)
                parts.append(f"{len(convergence)} grupo(s) de colores van a converger al mismo resultado: {summary}.")
            body = (
                " ".join(parts)
                + "\n\nEsto puede hacer que un futuro re-escaneo no pueda distinguir esos colores "
                  "(puede ser intencional si es justo lo que buscabas). ¿Aplicar de todas formas?"
            )
            dialogs.ask_confirm(
                self, "Conflictos detectados", body, "Aplicar de todas formas", do_apply, destructive=True,
            )
        else:
            do_apply()

    def _on_restore_clicked(self, _button):
        def do_restore():
            results = color_replacer.restore_files(self.config.files_to_replace, self.config.backup_dir)
            restored = sum(1 for r in results if r["restored"])
            msg = f"Restaurados {restored}/{len(results)} archivo(s) desde el backup."
            dialogs.toast(self.toast_overlay, msg)
            # Files on disk just changed back -- re-scan and start fresh, same as after an apply.
            self._finish_detection(detect_diff.run_detect(self.config), persist=True)

            def do_restart_services():
                started = restart_actions.run_enabled(restart_actions.read_restart_actions(self.config))
                if started:
                    names = ", ".join(a["label"] for a in started)
                    dialogs.toast(self.toast_overlay, f"Reiniciando: {names}")

            dialogs.ask_confirm(
                self,
                "Reiniciar servicios",
                "¿Querés reiniciar los servicios configurados para que tomen el tema restaurado?",
                "Reiniciar",
                do_restart_services,
            )

        dialogs.ask_confirm(
            self,
            "Restaurar desde backup",
            "Esto va a sobrescribir tus archivos actuales con la última copia de respaldo. ¿Continuar?",
            "Restaurar",
            do_restore,
            destructive=True,
        )
