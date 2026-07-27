#!/usr/bin/env python3
"""
dialogs/modifiers.py — The "Modificadores…" dialog: tweak an existing
palette's modifiers with a live preview and re-apply, mirroring
`automatic shift` / `palette shift` on the backend.
"""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...backend import color_detector as cd
from ...backend import palette_generator as pg
from ...backend import palette_shift
from ...backend import palette_store as ps

from ..color_chip import ensure_base_styles, register_swatch_color

from .common import _build_dialog_shell, _center_group_title
from .generation_settings import (
    _GENERATION_MODES,
    _SCORING_MODES,
    _build_mode_row,
    _build_scoring_row,
)


def _preview_swatch(hex_value: str) -> Gtk.Widget:
    css_class = register_swatch_color(hex_value)
    return Gtk.Box(width_request=34, height_request=34, tooltip_text=f"#{hex_value}",
                   css_classes=["color-swatch", css_class])


def show_palette_modifiers(parent: Gtk.Widget, config, palette_path, on_applied):
    """"Modificadores…": tweak a palette's modifiers with a live preview and
    re-apply, mirroring `automatic shift`. Post modifiers (my-eyes, ying-yang)
    are always available; the regeneration controls (mode/scoring/colors/…)
    only show for a generated palette, since a hand-created one has no image.

    On "Aplicar cambios" the shifted palette is written to disk (same path) and
    on_applied(path) fires, so the main window reloads it and its usual conflict
    checks / Aplicar flow take over -- this dialog never touches files itself."""
    ensure_base_styles()
    entries, meta = ps.read_palette(palette_path)
    generated = bool(meta.get("generated"))
    orig_gen = meta.get("gen") or {}
    post = meta.get("post") or {}

    state = {
        "my_eyes": bool(post.get("my_eyes")),
        "ying_yang": bool(post.get("ying_yang")),
        "mode": orig_gen.get("mode", "contrast"),
        "scoring": orig_gen.get("scoring", "default"),
        "colors": int(orig_gen.get("colors", len(entries))),
        "overfetch": int(orig_gen.get("overfetch", 0)),
        "shuffle": int(orig_gen.get("shuffle", 0)),
    }

    def overrides():
        ov = {"my_eyes": "on" if state["my_eyes"] else "off",
              "ying_yang": "on" if state["ying_yang"] else "off"}
        # Pass a selection override ONLY when it differs from what's stored, so
        # merely toggling my-eyes stays on the fast post-only path (no regen).
        if generated:
            if state["mode"] != orig_gen.get("mode", "contrast"):
                ov["mode"] = state["mode"]
            if state["scoring"] != orig_gen.get("scoring", "default"):
                ov["scoring"] = state["scoring"]
            if state["colors"] != int(orig_gen.get("colors", len(entries))):
                ov["colors"] = state["colors"]
            if state["overfetch"] != int(orig_gen.get("overfetch", 0)):
                ov["overfetch"] = state["overfetch"]
            if state["shuffle"] != int(orig_gen.get("shuffle", 0)):
                ov["shuffle"] = state["shuffle"]
        return ov

    preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    warn_label = Gtk.Label(wrap=True, xalign=0, visible=False,
                           css_classes=["dim-label"], margin_start=12, margin_end=12)

    def refresh_preview():
        child = preview_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            preview_box.remove(child)
            child = nxt
        try:
            result = palette_shift.shift_palette(palette_path, config, write=False, **overrides())
        except (palette_shift.ShiftError, pg.ImageLoadError) as e:
            warn_label.set_text(str(e))
            warn_label.set_visible(True)
            apply_button.set_sensitive(False)
            return
        apply_button.set_sensitive(True)
        for entry in result["entries"]:
            preview_box.append(_preview_swatch(entry["hex"]))
        warn_label.set_text("  ⚠ ".join(result["warnings"]))
        warn_label.set_visible(bool(result["warnings"]))

    page = Adw.PreferencesPage()

    post_group = Adw.PreferencesGroup(
        title="Modificadores simples",
        description="Se aplican al instante, sin regenerar. Andan también en paletas creadas a mano.",
    )
    _center_group_title(post_group)
    my_eyes_row = Adw.SwitchRow(title="Saturar colores (--my-eyes)")
    my_eyes_row.set_active(state["my_eyes"])
    ying_row = Adw.SwitchRow(title="Ying Yang (complementarios)",
                             subtitle="Rota todos los tonos 180°.")
    ying_row.set_active(state["ying_yang"])

    def on_my_eyes(row, _p):
        state["my_eyes"] = row.get_active()
        refresh_preview()

    def on_ying(row, _p):
        state["ying_yang"] = row.get_active()
        refresh_preview()

    my_eyes_row.connect("notify::active", on_my_eyes)
    ying_row.connect("notify::active", on_ying)
    post_group.add(my_eyes_row)
    post_group.add(ying_row)
    page.add(post_group)

    if generated:
        sel_group = Adw.PreferencesGroup(
            title="Regenerar desde la imagen",
            description="Cambiar esto regenera la paleta (descarta colores agregados a mano).",
        )
        _center_group_title(sel_group)

        mode_row = _build_mode_row(state["mode"])

        def on_mode(row, _p):
            state["mode"] = _GENERATION_MODES[row.get_selected()]
            refresh_preview()

        mode_row.connect("notify::selected", on_mode)
        sel_group.add(mode_row)

        scoring_row = _build_scoring_row(state["scoring"])

        def on_scoring(row, _p):
            state["scoring"] = _SCORING_MODES[row.get_selected()]
            refresh_preview()

        scoring_row.connect("notify::selected", on_scoring)
        sel_group.add(scoring_row)

        colors_row = Adw.SpinRow(
            title="Cantidad de colores",
            adjustment=Gtk.Adjustment(value=state["colors"], lower=1, upper=32, step_increment=1),
            digits=0,
        )

        def on_colors(row, _p):
            state["colors"] = int(row.get_value())
            refresh_preview()

        colors_row.connect("notify::value", on_colors)
        sel_group.add(colors_row)

        shuffle_row = Adw.SpinRow(
            title="Shuffle",
            subtitle="Saltear N candidatos a primary (explora variantes).",
            adjustment=Gtk.Adjustment(value=state["shuffle"], lower=0, upper=999, step_increment=1),
            digits=0,
        )

        def on_shuffle(row, _p):
            state["shuffle"] = int(row.get_value())
            refresh_preview()

        shuffle_row.connect("notify::value", on_shuffle)
        sel_group.add(shuffle_row)

        overfetch_row = Adw.SpinRow(
            title="Overfetch",
            subtitle="Más candidatos por score para auxiliares (o ramp más denso en shading). "
                     "También le da margen a Shuffle.",
            adjustment=Gtk.Adjustment(value=state["overfetch"], lower=0, upper=999, step_increment=1),
            digits=0,
        )

        def on_overfetch(row, _p):
            state["overfetch"] = int(row.get_value())
            refresh_preview()

        overfetch_row.connect("notify::value", on_overfetch)
        sel_group.add(overfetch_row)
        page.add(sel_group)
    else:
        note = Adw.PreferencesGroup()
        note.add(Adw.ActionRow(
            title="Paleta creada a mano",
            subtitle="Los modificadores de selección (modo, scoring, shuffle…) solo aplican a "
                     "paletas generadas desde una imagen.",
        ))
        page.add(note)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    image_path = cd.expand_path(meta.get("image") or "")
    if generated and image_path and os.path.isfile(image_path):
        image_caption = Gtk.Label(
            label=f"Wallpaper ligado a paleta\nRuta: {image_path}",
            xalign=0, wrap=True, justify=Gtk.Justification.LEFT,
            css_classes=["dim-label", "caption"],
            margin_start=12, margin_end=12, margin_top=8,
        )
        content_box.append(image_caption)
        picture = Gtk.Picture.new_for_filename(image_path)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_size_request(-1, 150)
        frame = Gtk.Frame(css_classes=["card"], margin_start=12, margin_end=12,
                          margin_top=12, overflow=Gtk.Overflow.HIDDEN)
        frame.set_child(picture)
        content_box.append(frame)

    content_box.append(page)

    preview_title = Gtk.Label(label="Vista previa", xalign=0, css_classes=["heading"],
                              margin_start=12, margin_top=6)
    content_box.append(preview_title)
    preview_scroller = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER,
        margin_start=12, margin_end=12, margin_bottom=6,
    )
    preview_scroller.set_child(preview_box)
    content_box.append(preview_scroller)
    content_box.append(warn_label)

    cancel_button = Gtk.Button(label="Cancelar")
    apply_button = Gtk.Button(label="Aplicar cambios", css_classes=["suggested-action"])
    dialog = _build_dialog_shell(
        "Modificadores", content_box, [cancel_button, apply_button],
        width=500, height=640,
    )

    def on_apply(_b):
        try:
            palette_shift.shift_palette(palette_path, config, write=True, **overrides())
        except (palette_shift.ShiftError, pg.ImageLoadError) as e:
            warn_label.set_text(str(e))
            warn_label.set_visible(True)
            return
        dialog.close()
        on_applied(palette_path)

    cancel_button.connect("clicked", lambda _b: dialog.close())
    apply_button.connect("clicked", on_apply)

    refresh_preview()
    dialog.present(parent)
