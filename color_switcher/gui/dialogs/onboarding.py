#!/usr/bin/env python3
"""
dialogs/onboarding.py — First-run flow: the welcome popup, the "which files"
question, the threaded ~/.config auto-scan with its review tree, the stale-
detect prompt, and the restart-actions onboarding step.
"""

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ...backend import color_detector as cd
from ...backend import config as cfg

from .common import _build_dialog_shell, _center_group_title
from .restart_actions import build_restart_actions_group
from .scanned_files import show_scanned_files_settings


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
    _center_group_title(pref_group)
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

    add_button = Gtk.Button(label="Agregar seleccionados", css_classes=["suggested-action"])
    dialog = _build_dialog_shell(
        "Revisar archivos encontrados", page, [add_button],
        width=560, height=640, hide_title_buttons=True,
    )

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

    continue_button = Gtk.Button(label="Continuar", css_classes=["suggested-action"])
    dialog = _build_dialog_shell(
        "Servicios a reiniciar", content_box, [continue_button],
        width=480, height=520, hide_title_buttons=True,
    )

    continue_button.connect("clicked", lambda _b: dialog.close())
    dialog.connect("closed", lambda _d: on_done())

    dialog.present(parent)
