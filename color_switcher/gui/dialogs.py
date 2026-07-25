#!/usr/bin/env python3
"""
dialogs.py — Small dialog helpers used by window_main.py: the two startup
popups (rutas a/c from the spec), a generic confirm dialog, palette-name /
color-picker prompts, the restart-actions manager, and a toast shortcut.
"""

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..backend import color_detector as cd
from ..backend import config as cfg
from ..backend import palette_generator as pg
from ..backend import restart_actions as ra


def toast(toast_overlay: Adw.ToastOverlay, message: str, timeout: int = 3):
    toast_overlay.add_toast(Adw.Toast.new(message))


def show_welcome(parent: Gtk.Widget, on_continue):
    """Ruta a: no previous detection — explain the app before scanning."""
    dialog = Adw.AlertDialog.new(
        "¡Bienvenido a Ultimate Color Switcher!",
        "Esta es la primera vez que corrés la app en este proyecto.\n\n"
        "Vamos a escanear los archivos configurados en config.json en busca de "
        "colores (hex y rgb), y después vas a poder armar una paleta nueva y "
        "mapear cada color detectado a un color de esa paleta, para aplicarlo "
        "a tus dotfiles.",
    )
    dialog.add_response("start", "Empezar")
    dialog.set_default_response("start")
    dialog.set_close_response("start")

    def _on_response(_d, _response):
        on_continue()

    dialog.connect("response", _on_response)
    dialog.present(parent)


def show_configure_files_onboarding(parent: Gtk.Widget, config, on_done):
    """Fresh-config step (files_to_replace empty): offer to auto-scan ~/.config
    for color-bearing files, add them by hand, or edit config.json. Shown
    AFTER the welcome dialog, so the user meets the app before being asked to
    configure it. on_done() continues the startup flow (re-detect with the
    now-populated list); it fires however the step ends."""
    dialog = Adw.AlertDialog.new(
        "¿Qué archivos querés recolorear?",
        "Ultimate Color Switcher busca y reemplaza colores en los archivos de una lista. "
        "Todavía está vacía.\n\n"
        "Puedo buscar automáticamente en ~/.config los archivos que tengan colores y armarte "
        "una base para empezar.\n\n"
        "⚠ Es solo una base: puede incluir hex que no son colores (como direcciones 0xADDR) o "
        "archivos que no querés modificar. Vas a poder revisarlos y quitarlos antes de confirmar.",
    )
    dialog.add_response("external", "Editar config.json")
    dialog.add_response("manual", "Agregar a mano")
    dialog.add_response("scan", "Buscar en ~/.config")
    dialog.set_response_appearance("scan", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("scan")
    dialog.set_close_response("manual")

    def _on_response(_d, response):
        if response == "scan":
            run_config_scan_flow(parent, config, on_done)
        elif response == "manual":
            show_scanned_files_settings(parent, config, on_change=on_done)
        else:
            path = os.path.join(config.project_dir, "config.json")
            Gio.AppInfo.launch_default_for_uri(f"file://{path}", None)
            on_done()

    dialog.connect("response", _on_response)
    dialog.present(parent)


def run_config_scan_flow(parent: Gtk.Widget, config, on_done):
    """Scan ~/.config on a worker thread (a full walk can take a couple of
    seconds and would otherwise freeze the UI) behind a spinner, then hand the
    results to the review tree. on_done() fires once the user finishes (or if
    nothing turns up)."""
    spinner = Gtk.Spinner(width_request=32, height_request=32)
    spinner.start()
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=16, halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER, margin_top=32, margin_bottom=32, margin_start=32, margin_end=32,
    )
    box.append(spinner)
    box.append(Gtk.Label(label="Buscando colores en ~/.config…"))

    dialog = Adw.Dialog.new()
    dialog.set_title("Buscando…")
    dialog.set_content_width(340)
    dialog.set_content_height(200)
    dialog.set_can_close(False)  # don't let the user cancel mid-scan
    dialog.set_child(box)
    dialog.present(parent)

    def on_scan_done(found):
        dialog.force_close()
        if not found:
            info = Adw.AlertDialog.new(
                "Sin resultados",
                "No encontré archivos con colores en ~/.config. Podés agregarlos a mano cuando quieras.",
            )
            info.add_response("ok", "Entendido")
            info.connect("response", lambda _d, _r: on_done())
            info.present(parent)
        else:
            show_config_scan_results(parent, config, found, on_done)
        return GLib.SOURCE_REMOVE

    def worker():
        found = cd.scan_config_dir_for_color_files()
        GLib.idle_add(on_scan_done, found)

    threading.Thread(target=worker, daemon=True).start()


def show_config_scan_results(parent: Gtk.Widget, config, found: list, on_done):
    """Review tree for the auto-scan: one collapsible Adw.ExpanderRow per
    folder (collapsed by default, with an enable-switch that drops the whole
    folder), each holding a checkbox row per file. "Agregar seleccionados"
    merges the ticked files into files_to_replace. on_done() fires however the
    dialog closes."""
    groups = cd.group_paths_by_top_level(found)
    checks = {}            # abs path -> Gtk.CheckButton
    folder_rows = []       # (expander, [abs paths])

    pref_group = Adw.PreferencesGroup(
        title="Archivos con colores encontrados",
        description="Desmarcá lo que no quieras. Podés apagar una carpeta entera o archivos "
                    "sueltos. Puede incluir hex que no son colores o archivos que no querés tocar.",
    )
    config_root = os.path.join(os.path.expanduser("~"), ".config")
    for folder, files in groups:
        # Files sitting directly in ~/.config get grouped under the root
        # itself -- label it so it doesn't read as "the whole config".
        if folder == config_root:
            title = f"{cfg.to_home_relative(folder)}  ·  sueltos"
        else:
            title = cfg.to_home_relative(folder)
        expander = Adw.ExpanderRow(title=title, subtitle=f"{len(files)} archivo(s)")
        expander.set_expanded(False)
        expander.set_show_enable_switch(True)
        expander.set_enable_expansion(True)
        paths = []
        for path, display in files:
            row = Adw.ActionRow(title=display)
            check = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            expander.add_row(row)
            checks[path] = check
            paths.append(path)
        pref_group.add(expander)
        folder_rows.append((expander, paths))

    page = Adw.PreferencesPage()
    page.add(pref_group)

    toolbar_view = Adw.ToolbarView()
    header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    header.set_title_widget(Adw.WindowTitle(title="Revisar archivos encontrados"))
    toolbar_view.add_top_bar(header)
    toolbar_view.set_content(Gtk.ScrolledWindow(child=page, vexpand=True))

    footer = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, spacing=8,
        margin_top=8, margin_bottom=12, margin_end=12,
    )
    add_button = Gtk.Button(label="Agregar seleccionados", css_classes=["suggested-action"])
    footer.append(add_button)
    toolbar_view.add_bottom_bar(footer)

    dialog = Adw.Dialog.new()
    dialog.set_title("Revisar archivos encontrados")
    dialog.set_content_width(560)
    dialog.set_content_height(640)
    dialog.set_child(toolbar_view)

    def on_add(_b):
        existing = cfg.read_files_to_replace(config)
        for expander, files in folder_rows:
            if not expander.get_enable_expansion():
                continue  # whole folder switched off
            for path in files:
                if checks[path].get_active():
                    entry = cfg.to_home_relative(path)
                    if entry not in existing:
                        existing.append(entry)
        cfg.write_files_to_replace(config, existing)
        dialog.close()

    add_button.connect("clicked", on_add)
    dialog.connect("closed", lambda _d: on_done())  # add OR Escape -> continue the flow once
    dialog.present(parent)


def show_stale_detect(parent: Gtk.Widget, diff: dict, on_choice):
    """Ruta c: a fresh detect differs from the saved one. on_choice receives
    "rescan" or "keep"."""
    lines = []
    if diff["added"]:
        lines.append(f"Nuevos: {len(diff['added'])} color(es)")
    if diff["removed"]:
        lines.append(f"Ya no aparecen: {len(diff['removed'])} color(es)")
    body = (
        "Los colores detectados cambiaron desde la última vez que abriste la app.\n\n"
        + "\n".join(lines)
        + "\n\nSe recomienda re-escanear y armar un mapping nuevo. Si seguís con la "
        "detección anterior, el mapping podría no corresponder con tus archivos actuales."
    )
    dialog = Adw.AlertDialog.new("Los colores cambiaron", body)
    dialog.add_response("keep", "Seguir con la anterior")
    dialog.add_response("rescan", "Re-escanear (recomendado)")
    dialog.set_response_appearance("rescan", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("rescan")
    dialog.set_close_response("keep")

    def _on_response(_d, response):
        on_choice(response)

    dialog.connect("response", _on_response)
    dialog.present(parent)


def ask_confirm(parent: Gtk.Widget, heading: str, body: str, ok_label: str, on_confirm,
                 destructive: bool = False):
    dialog = Adw.AlertDialog.new(heading, body)
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("ok", ok_label)
    dialog.set_response_appearance(
        "ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response == "ok":
            on_confirm()

    dialog.connect("response", _on_response)
    dialog.present(parent)


def prompt_text(parent: Gtk.Widget, heading: str, body: str, placeholder: str, ok_label: str, on_submit):
    """Generic single-line text prompt (used for palette filenames / labels)."""
    dialog = Adw.AlertDialog.new(heading, body)
    entry = Gtk.Entry(placeholder_text=placeholder)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("ok", ok_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response == "ok":
            on_submit(entry.get_text().strip())

    dialog.connect("response", _on_response)
    dialog.present(parent)


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    r = round(rgba.red * 255)
    g = round(rgba.green * 255)
    b = round(rgba.blue * 255)
    return f"{r:02x}{g:02x}{b:02x}"


def prompt_add_color(parent: Gtk.Widget, on_add):
    """Native color picker, then an inline prompt for the label. Calls
    on_add(hex_no_hash, label)."""
    color_dialog = Gtk.ColorDialog(with_alpha=False)

    def _on_color_chosen(dialog, result):
        try:
            rgba = dialog.choose_rgba_finish(result)
        except Exception:
            return  # user cancelled
        hex_value = _rgba_to_hex(rgba)

        def _on_label(label):
            on_add(hex_value, label)

        prompt_text(
            parent,
            f"Color #{hex_value}",
            "Etiqueta para este color en la paleta (opcional):",
            "ej: primary",
            "Agregar",
            _on_label,
        )

    color_dialog.choose_rgba(parent, None, None, _on_color_chosen)


def pick_import_palette_file(parent: Gtk.Widget, on_selected):
    file_dialog = Gtk.FileDialog(title="Importar paleta CSV")
    csv_filter = Gtk.FileFilter(name="Paleta CSV")
    csv_filter.add_pattern("*.csv")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(csv_filter)
    file_dialog.set_filters(filters)

    def _on_selected(dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except Exception:
            return  # user cancelled
        on_selected(gfile.get_path())

    file_dialog.open(parent, None, _on_selected)


def pick_image_file(parent: Gtk.Widget, on_selected):
    file_dialog = Gtk.FileDialog(title="Elegir imagen (wallpaper)")
    image_filter = Gtk.FileFilter(name="Imágenes")
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
        image_filter.add_pattern(pattern)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(image_filter)
    file_dialog.set_filters(filters)

    def _on_selected(dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except Exception:
            return  # user cancelled
        on_selected(gfile.get_path())

    file_dialog.open(parent, None, _on_selected)


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


_GENERATION_MODES = ["contrast", "balanced", "shading"]
_GENERATION_MODE_LABELS = {
    "contrast": "Contraste",
    "balanced": "Balanceado",
    "shading": "Shading",
}
_GENERATION_MODE_DESCRIPTIONS = {
    "contrast": "Secondary maximiza contraste con primary (default).",
    "balanced": "Secondary elegido por score, sin sesgo de contraste con primary.",
    "shading": "El resto de la paleta son variantes monocromáticas de primary.",
}


def _mode_list_factory() -> Gtk.SignalListItemFactory:
    """Popup rows show just the short mode name; the full explanation only
    appears as a tooltip on hover, so long descriptions don't overflow the
    dropdown."""
    factory = Gtk.SignalListItemFactory()

    def on_setup(_factory, list_item):
        list_item.set_child(Gtk.Label(xalign=0))

    def on_bind(_factory, list_item):
        mode = _GENERATION_MODES[list_item.get_position()]
        label = list_item.get_child()
        label.set_label(_GENERATION_MODE_LABELS[mode])
        label.set_tooltip_text(_GENERATION_MODE_DESCRIPTIONS[mode])

    factory.connect("setup", on_setup)
    factory.connect("bind", on_bind)
    return factory


_SCORING_MODES = ["default", "alternative", "custom"]
_SCORING_MODE_LABELS = {
    "default": "Default",
    "alternative": "Alternativo",
    "custom": "Custom",
}
_SCORING_MODE_DESCRIPTIONS = {
    "default": "Coverage 20% / Saturación 30% / Midtone 25% / Contraste 25%.",
    "alternative": "Coverage 30% / Saturación 30% / Midtone 30% / Contraste 10%.",
    "custom": "Definí tus propios porcentajes abajo (deben sumar 100).",
}
_SCORING_WEIGHT_LABELS = {
    "coverage": "Coverage",
    "saturation": "Saturación",
    "midtone": "Midtone",
    "contrast": "Contraste",
}


def _scoring_list_factory() -> Gtk.SignalListItemFactory:
    """Same short-name-in-list/full-description-as-tooltip pattern as
    _mode_list_factory."""
    factory = Gtk.SignalListItemFactory()

    def on_setup(_factory, list_item):
        list_item.set_child(Gtk.Label(xalign=0))

    def on_bind(_factory, list_item):
        scoring = _SCORING_MODES[list_item.get_position()]
        label = list_item.get_child()
        label.set_label(_SCORING_MODE_LABELS[scoring])
        label.set_tooltip_text(_SCORING_MODE_DESCRIPTIONS[scoring])

    factory.connect("setup", on_setup)
    factory.connect("bind", on_bind)
    return factory


def build_palette_generation_group(config) -> Adw.PreferencesGroup:
    """An Adw.PreferencesGroup configuring "Generar paleta desde imagen…"
    (mode + saturation boost / --my-eyes), persisted immediately to
    config.json via palette_generator.read/write_generation_settings —
    same real-time-save philosophy as the restart-actions group."""
    settings = pg.read_generation_settings(config)

    group = Adw.PreferencesGroup(
        title="Generación de paleta desde imagen",
        description="Afecta a 'Generar paleta desde imagen…' en el menú principal.",
    )

    string_list = Gtk.StringList.new([_GENERATION_MODE_LABELS[m] for m in _GENERATION_MODES])
    mode_row = Adw.ComboRow(title="Modo de selección", model=string_list, list_factory=_mode_list_factory())
    mode_row.set_selected(_GENERATION_MODES.index(settings["mode"]))
    mode_row.set_tooltip_text(_GENERATION_MODE_DESCRIPTIONS[settings["mode"]])

    def on_mode_changed(row, _pspec):
        mode = _GENERATION_MODES[row.get_selected()]
        settings["mode"] = mode
        row.set_tooltip_text(_GENERATION_MODE_DESCRIPTIONS[mode])
        pg.write_generation_settings(config, settings)

    mode_row.connect("notify::selected", on_mode_changed)
    group.add(mode_row)

    saturate_row = Adw.SwitchRow(
        title="Saturar colores (--my-eyes)",
        subtitle="Sube la saturación de los colores elegidos justo antes de usarlos.",
    )
    saturate_row.set_active(bool(settings.get("saturate", False)))

    def on_saturate_toggled(row, _pspec):
        settings["saturate"] = row.get_active()
        pg.write_generation_settings(config, settings)

    saturate_row.connect("notify::active", on_saturate_toggled)
    group.add(saturate_row)

    ying_yang_row = Adw.SwitchRow(
        title="Ying Yang (Complementarios)",
        subtitle="Usa la paleta complementaria: rota todos los colores 180° en el tono.",
    )
    ying_yang_row.set_active(bool(settings.get("ying_yang", False)))

    def on_ying_yang_toggled(row, _pspec):
        settings["ying_yang"] = row.get_active()
        pg.write_generation_settings(config, settings)

    ying_yang_row.connect("notify::active", on_ying_yang_toggled)
    group.add(ying_yang_row)

    scoring_string_list = Gtk.StringList.new([_SCORING_MODE_LABELS[s] for s in _SCORING_MODES])
    scoring_row = Adw.ComboRow(
        title="Ponderación de scoring", model=scoring_string_list, list_factory=_scoring_list_factory(),
    )
    scoring_row.set_selected(_SCORING_MODES.index(settings["scoring"]))
    scoring_row.set_tooltip_text(_SCORING_MODE_DESCRIPTIONS[settings["scoring"]])
    group.add(scoring_row)

    # Pre-filled with whatever's stored, or the "default" preset's own
    # percentages as a sensible starting point to tweak from -- not left
    # empty, per the spec ("nos muestra un campo con los porcentajes que
    # estaban guardados que podemos editar").
    custom_percentages = {k: pg._SCORING_PRESETS["default"][k] * 100 for k in pg._SCORING_WEIGHT_KEYS}
    custom_percentages.update(settings.get("custom_percentages") or {})

    total_row = Adw.ActionRow(title="Suma de porcentajes")

    def _update_total_row():
        total = sum(custom_percentages[k] for k in pg._SCORING_WEIGHT_KEYS)
        ok = abs(total - 100.0) <= 0.5
        total_row.set_subtitle(f"{total:g}%" + ("" if ok else "  ✗ debe sumar 100"))

    def _persist_custom_percentages():
        settings["custom_percentages"] = dict(custom_percentages)
        pg.write_generation_settings(config, settings)
        _update_total_row()

    custom_rows = []
    for key in pg._SCORING_WEIGHT_KEYS:
        adjustment = Gtk.Adjustment(
            value=custom_percentages[key], lower=0, upper=100, step_increment=1, page_increment=5,
        )
        row = Adw.SpinRow(title=_SCORING_WEIGHT_LABELS[key], adjustment=adjustment, digits=0)
        row.set_visible(settings["scoring"] == "custom")

        def on_percentage_changed(row, _pspec=None, key=key):
            custom_percentages[key] = row.get_value()
            _persist_custom_percentages()

        row.connect("notify::value", on_percentage_changed)
        group.add(row)
        custom_rows.append(row)

    total_row.set_visible(settings["scoring"] == "custom")
    _update_total_row()
    group.add(total_row)

    def on_scoring_changed(row, _pspec):
        scoring = _SCORING_MODES[row.get_selected()]
        settings["scoring"] = scoring
        row.set_tooltip_text(_SCORING_MODE_DESCRIPTIONS[scoring])
        is_custom = scoring == "custom"
        for r in custom_rows:
            r.set_visible(is_custom)
        total_row.set_visible(is_custom)
        pg.write_generation_settings(config, settings)

    scoring_row.connect("notify::selected", on_scoring_changed)

    return group


def build_contrast_comparison_group(config) -> Adw.PreferencesGroup:
    """Whether score_cluster's contrast_term (see palette_generator) is
    computed against every cluster (weighted by coverage) or just the
    single highest-coverage one -- palette_generation.weighted_contrast,
    same real-time-save group as build_palette_generation_group.

    Two mutually exclusive, fully-clickable Adw.ActionRows with a small
    checkmark suffix on whichever is active, rather than Gtk.CheckButton
    radios (which render at a fixed ~24px indicator size GTK4 doesn't let
    CSS shrink -- min-width/-gtk-icon-size/font-size overrides all measured
    as no-ops) or a Adw.ComboRow like the other two settings here, matching
    the always-visible two-option layout it was speced with."""
    settings = pg.read_generation_settings(config)

    group = Adw.PreferencesGroup(
        title="Comparación de contraste",
        description="Cómo se mide qué tan distinto es un color candidato al elegir primary/secondary/auxN.",
    )

    weighted_row = Adw.ActionRow(title="Ponderado (Recomendado)", activatable=True)
    weighted_row.set_tooltip_text(
        "Se compara de forma ponderada con todos los colores. Funciona bien para la gran mayoría de imágenes."
    )
    weighted_icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
    weighted_row.add_suffix(weighted_icon)

    single_row = Adw.ActionRow(title="Solo background", activatable=True)
    single_row.set_tooltip_text(
        "Se compara solo con el color más dominante en la imagen. Funciona bien para imágenes con "
        "backgrounds de un solo color."
    )
    single_icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
    single_row.add_suffix(single_icon)

    def _sync_icons():
        is_weighted = bool(settings.get("weighted_contrast", True))
        weighted_icon.set_visible(is_weighted)
        single_icon.set_visible(not is_weighted)

    _sync_icons()

    def on_weighted_activated(_row):
        settings["weighted_contrast"] = True
        pg.write_generation_settings(config, settings)
        _sync_icons()

    def on_single_activated(_row):
        settings["weighted_contrast"] = False
        pg.write_generation_settings(config, settings)
        _sync_icons()

    weighted_row.connect("activated", on_weighted_activated)
    single_row.connect("activated", on_single_activated)

    group.add(weighted_row)
    group.add(single_row)
    return group


def build_advanced_generation_group(config) -> Adw.PreferencesGroup:
    """Overfetch + shuffle -- palette_generation.{overfetch, shuffle_enabled,
    shuffle_mode, shuffle_value}, same real-time-save group as the others
    here. Tucked into a collapsed Adw.ExpanderRow ("Opciones avanzadas")
    since these are power-user/scripting knobs most people never touch --
    see resolve_shuffle_index/select_primary in palette_generator for what
    they actually do."""
    settings = pg.read_generation_settings(config)

    group = Adw.PreferencesGroup()
    expander = Adw.ExpanderRow(
        title="Opciones avanzadas",
        subtitle="Overfetch y shuffle -- para explorar variantes de una misma imagen, pensado para scripts.",
    )
    group.add(expander)

    overfetch_adjustment = Gtk.Adjustment(
        value=settings.get("overfetch", 0), lower=0, upper=30, step_increment=1, page_increment=5,
    )
    overfetch_row = Adw.SpinRow(
        title="Overfetch",
        subtitle="Candidatos extra a considerar más allá de la cantidad pedida (0 = desactivado). "
                  "Le da más margen a shuffle.",
        adjustment=overfetch_adjustment, digits=0,
    )

    def on_overfetch_changed(row, _pspec=None):
        settings["overfetch"] = int(row.get_value())
        pg.write_generation_settings(config, settings)

    overfetch_row.connect("notify::value", on_overfetch_changed)
    expander.add_row(overfetch_row)

    shuffle_enabled_row = Adw.SwitchRow(
        title="Shuffle",
        subtitle="Saltear los primeros N candidatos al elegir primary (el resto de la paleta se recalcula a partir de eso).",
    )
    shuffle_enabled_row.set_active(bool(settings.get("shuffle_enabled", False)))
    expander.add_row(shuffle_enabled_row)

    shuffle_next_row = Adw.SwitchRow(
        title="Modo siguiente (next)",
        subtitle="Cada generación avanza automáticamente al próximo valor, cíclico.",
    )
    shuffle_next_row.set_active(settings.get("shuffle_mode", "manual") == "next")
    expander.add_row(shuffle_next_row)

    shuffle_value_adjustment = Gtk.Adjustment(
        value=settings.get("shuffle_value", 0), lower=0, upper=30, step_increment=1, page_increment=5,
    )
    shuffle_value_row = Adw.SpinRow(title="Valor de shuffle", adjustment=shuffle_value_adjustment, digits=0)
    expander.add_row(shuffle_value_row)

    def _sync_shuffle_visibility():
        enabled = shuffle_enabled_row.get_active()
        shuffle_next_row.set_visible(enabled)
        shuffle_value_row.set_visible(enabled and not shuffle_next_row.get_active())

    def on_shuffle_enabled_toggled(row, _pspec=None):
        settings["shuffle_enabled"] = row.get_active()
        pg.write_generation_settings(config, settings)
        _sync_shuffle_visibility()

    def on_shuffle_next_toggled(row, _pspec=None):
        settings["shuffle_mode"] = "next" if row.get_active() else "manual"
        pg.write_generation_settings(config, settings)
        _sync_shuffle_visibility()

    def on_shuffle_value_changed(row, _pspec=None):
        settings["shuffle_value"] = int(row.get_value())
        pg.write_generation_settings(config, settings)

    shuffle_enabled_row.connect("notify::active", on_shuffle_enabled_toggled)
    shuffle_next_row.connect("notify::active", on_shuffle_next_toggled)
    shuffle_value_row.connect("notify::value", on_shuffle_value_changed)

    _sync_shuffle_visibility()

    return group


def show_palette_generation_settings(parent: Gtk.Widget, config):
    """Reachable any time from the header menu."""
    page = Adw.PreferencesPage()
    page.add(build_palette_generation_group(config))
    page.add(build_contrast_comparison_group(config))
    page.add(build_advanced_generation_group(config))
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Generación de paleta")
    prefs.add(page)
    prefs.present(parent)


def show_restart_actions_onboarding(parent: Gtk.Widget, config, on_done):
    """First-run onboarding: same content as show_restart_actions_settings,
    but with an explicit "Continuar" action so it can't go unnoticed before
    the mapping screen appears. on_done() fires once, however the dialog
    gets closed (button or Escape/X)."""
    group = build_restart_actions_group(config)
    intro = Gtk.Label(
        label="Estos son los que probablemente necesites reiniciar para que tomen los colores nuevos. "
              "Podés ajustarlos ahora o después desde el menú (⋮).",
        wrap=True, xalign=0, margin_start=12, margin_end=12, margin_top=12,
        css_classes=["dim-label"],
    )
    page = Adw.PreferencesPage()
    page.add(group)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    content_box.append(intro)
    content_box.append(page)

    toolbar_view = Adw.ToolbarView()
    header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    header.set_title_widget(Adw.WindowTitle(title="Servicios a reiniciar"))
    toolbar_view.add_top_bar(header)
    toolbar_view.set_content(Gtk.ScrolledWindow(child=content_box, vexpand=True))

    footer = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, spacing=8,
        margin_top=8, margin_bottom=12, margin_end=12,
    )
    continue_button = Gtk.Button(label="Continuar", css_classes=["suggested-action"])
    footer.append(continue_button)
    toolbar_view.add_bottom_bar(footer)

    dialog = Adw.Dialog.new()
    dialog.set_title("Servicios a reiniciar")
    dialog.set_content_width(480)
    dialog.set_content_height(520)
    dialog.set_child(toolbar_view)

    continue_button.connect("clicked", lambda _b: dialog.close())
    dialog.connect("closed", lambda _d: on_done())

    dialog.present(parent)
