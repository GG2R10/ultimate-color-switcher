#!/usr/bin/env python3
"""
dialogs.py — Small dialog helpers used by window_main.py: the two startup
popups (rutas a/c from the spec), a generic confirm dialog, palette-name /
color-picker prompts, the restart-actions manager, and a toast shortcut.
"""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from backend import color_detector as cd  # noqa: E402
from backend import config as cfg  # noqa: E402
from backend import palette_generator as pg  # noqa: E402
from backend import restart_actions as ra  # noqa: E402


def toast(toast_overlay: Adw.ToastOverlay, message: str, timeout: int = 3):
    toast_overlay.add_toast(Adw.Toast.new(message))


def show_welcome(parent: Gtk.Widget, on_continue):
    """Ruta a: no previous detection — explain the app before scanning."""
    dialog = Adw.AlertDialog.new(
        "¡Bienvenido a Color Switcher!",
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

    return group


def show_palette_generation_settings(parent: Gtk.Widget, config):
    """Reachable any time from the header menu."""
    group = build_palette_generation_group(config)
    page = Adw.PreferencesPage()
    page.add(group)
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
