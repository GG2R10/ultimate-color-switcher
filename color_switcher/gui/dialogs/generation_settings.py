#!/usr/bin/env python3
"""
dialogs/generation_settings.py — Everything behind "Configurar generación de
paleta…" and "Otros…": the mode/scoring ComboRow builders (also reused by
dialogs/modifiers.py's live shift preview), the three settings groups
(generation / contrast comparison / advanced+shading), the my-eyes fine-tuning
group, and their two Adw.PreferencesDialog wrappers.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...backend import palette_generator as pg

from .common import _center_group_title

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


def _build_mode_row(initial_mode: str) -> Adw.ComboRow:
    """Adw.ComboRow over _GENERATION_MODES with the short label shown and the
    full description kept as this row's own tooltip, self-updating on
    selection change. Shared by build_palette_generation_group (settings
    dialog, persists to config) and dialogs.modifiers.show_palette_modifiers
    (shift preview, doesn't persist) -- callers just connect their own
    notify::selected handler on top for whichever side effect they need."""
    string_list = Gtk.StringList.new([_GENERATION_MODE_LABELS[m] for m in _GENERATION_MODES])
    row = Adw.ComboRow(title="Modo de selección", model=string_list, list_factory=_mode_list_factory())
    row.set_selected(_GENERATION_MODES.index(initial_mode))
    row.set_tooltip_text(_GENERATION_MODE_DESCRIPTIONS[initial_mode])
    row.connect("notify::selected", lambda r, _p:
                r.set_tooltip_text(_GENERATION_MODE_DESCRIPTIONS[_GENERATION_MODES[r.get_selected()]]))
    return row


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


def _build_scoring_row(initial_scoring: str) -> Adw.ComboRow:
    """Same self-updating-tooltip pattern as _build_mode_row, over
    _SCORING_MODES."""
    string_list = Gtk.StringList.new([_SCORING_MODE_LABELS[s] for s in _SCORING_MODES])
    row = Adw.ComboRow(title="Ponderación de scoring", model=string_list, list_factory=_scoring_list_factory())
    row.set_selected(_SCORING_MODES.index(initial_scoring))
    row.set_tooltip_text(_SCORING_MODE_DESCRIPTIONS[initial_scoring])
    row.connect("notify::selected", lambda r, _p:
                r.set_tooltip_text(_SCORING_MODE_DESCRIPTIONS[_SCORING_MODES[r.get_selected()]]))
    return row


def build_palette_generation_group(config, settings=None) -> tuple:
    """An Adw.PreferencesGroup configuring "Generar paleta desde imagen…"
    (mode + saturation boost / --my-eyes), persisted immediately to
    config.json via palette_generator.read/write_generation_settings —
    same real-time-save philosophy as the restart-actions group.

    `settings`: pass the SAME dict this dialog's other groups use (see
    show_palette_generation_settings) rather than each group reading its own
    independent copy -- otherwise, editing a setting in one group and then a
    DIFFERENT setting in another group in the same dialog session would have
    the second write silently revert the first (each group's own snapshot,
    taken when the dialog opened, doesn't know about the other's edit).
    Defaults to a fresh independent read when not given (a standalone call).

    Returns (group, mode_row): mode_row is exposed so
    build_advanced_generation_group's "Shading" mini-section (a separate
    Adw.PreferencesGroup) can show/hide itself in sync, without either group
    needing to own the other's state."""
    settings = settings if settings is not None else pg.read_generation_settings(config)

    group = Adw.PreferencesGroup(
        title="Generación de paleta desde imagen",
        description="Afecta a 'Generar paleta desde imagen…' en el menú principal.",
    )
    _center_group_title(group)

    mode_row = _build_mode_row(settings["mode"])

    def on_mode_changed(row, _pspec):
        settings["mode"] = _GENERATION_MODES[row.get_selected()]
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

    scoring_row = _build_scoring_row(settings["scoring"])
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
        is_custom = scoring == "custom"
        for r in custom_rows:
            r.set_visible(is_custom)
        total_row.set_visible(is_custom)
        pg.write_generation_settings(config, settings)

    scoring_row.connect("notify::selected", on_scoring_changed)

    return group, mode_row


def build_contrast_comparison_group(config, settings=None) -> Adw.PreferencesGroup:
    """Whether score_cluster's contrast_term (see palette_generator) is
    computed against every cluster (weighted by coverage) or just the
    single highest-coverage one -- palette_generation.weighted_contrast,
    same real-time-save group as build_palette_generation_group.

    `settings`: see build_palette_generation_group's docstring -- pass the
    SAME dict the dialog's other groups use, to avoid one group's write
    reverting another's.

    Two mutually exclusive, fully-clickable Adw.ActionRows with a small
    checkmark suffix on whichever is active, rather than Gtk.CheckButton
    radios (which render at a fixed ~24px indicator size GTK4 doesn't let
    CSS shrink -- min-width/-gtk-icon-size/font-size overrides all measured
    as no-ops) or a Adw.ComboRow like the other two settings here, matching
    the always-visible two-option layout it was speced with."""
    settings = settings if settings is not None else pg.read_generation_settings(config)

    group = Adw.PreferencesGroup(
        title="Comparación de contraste",
        description="Cómo se mide qué tan distinto es un color candidato al elegir primary/secondary/auxN.",
    )
    _center_group_title(group)

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


_SHADING_DIRECTIONS_GUI = ["dark", "light"]
_SHADING_DIRECTION_LABELS = {"dark": "Oscuro (dark)", "light": "Claro (light)"}


def build_advanced_generation_group(config, mode_row=None, settings=None) -> Adw.PreferencesGroup:
    """Overfetch + shuffle + shading direction/luminance -- palette_generation.
    {overfetch, shuffle_enabled, shuffle_mode, shuffle_value, shading_direction,
    shading_min_luminance, shading_max_luminance}, same real-time-save group as
    the others here. Tucked into collapsed Adw.ExpanderRows ("Opciones
    avanzadas" / "Shading") since these are power-user/scripting knobs most
    people never touch -- see resolve_shuffle_index/select_primary/
    generate_shading_series in palette_generator for what they actually do.

    mode_row (from build_palette_generation_group): the Shading section only
    makes sense when mode="shading", so it shows/hides itself in sync with
    that combo row rather than needing its own copy of `mode`.

    `settings`: see build_palette_generation_group's docstring -- pass the
    SAME dict the dialog's other groups use, to avoid one group's write
    reverting another's."""
    settings = settings if settings is not None else pg.read_generation_settings(config)

    group = Adw.PreferencesGroup()

    keep_custom_row = Adw.SwitchRow(
        title="Mantener colores editados/agregados",
        subtitle="Al regenerar una paleta ya existente en la ruta de salida, preservar sus colores "
                 "editados/agregados a mano en vez de descartarlos.",
    )
    keep_custom_row.set_active(bool(settings.get("keep_custom_on_regen", True)))

    def on_keep_custom_changed(row, _pspec=None):
        settings["keep_custom_on_regen"] = row.get_active()
        pg.write_generation_settings(config, settings)

    keep_custom_row.connect("notify::active", on_keep_custom_changed)
    group.add(keep_custom_row)

    consider_plane_row = Adw.SwitchRow(
        title="Considerar roles foreground/background",
        subtitle="Apuntar a la demanda de colores fg/bg tageados (de la paleta existente, o de "
                 "colores detectados) en vez de ignorar los roles por completo.",
    )
    consider_plane_row.set_active(bool(settings.get("consider_roles_on_regen", True)))

    def on_consider_plane_changed(row, _pspec=None):
        settings["consider_roles_on_regen"] = row.get_active()
        pg.write_generation_settings(config, settings)

    consider_plane_row.connect("notify::active", on_consider_plane_changed)
    group.add(consider_plane_row)

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
                  "Los auxiliares se eligen entre más candidatos por score; el ramp de shading sale "
                  "más denso. También le da más margen a shuffle.",
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

    shading_expander = Adw.ExpanderRow(
        title="Shading",
        subtitle="Dirección y límites de luminancia del ramp -- solo aplica con modo 'shading'.",
    )
    group.add(shading_expander)

    direction_string_list = Gtk.StringList.new(
        [_SHADING_DIRECTION_LABELS[d] for d in _SHADING_DIRECTIONS_GUI]
    )
    direction_row = Adw.ComboRow(title="Dirección", model=direction_string_list)
    direction_row.set_selected(_SHADING_DIRECTIONS_GUI.index(settings.get("shading_direction", "dark")))
    shading_expander.add_row(direction_row)

    min_lum_adjustment = Gtk.Adjustment(
        value=settings.get("shading_min_luminance", 8.0), lower=0, upper=100, step_increment=1, page_increment=5,
    )
    min_lum_row = Adw.SpinRow(
        title="Luminancia mínima", subtitle="Solo con dirección 'dark'.",
        adjustment=min_lum_adjustment, digits=0,
    )
    shading_expander.add_row(min_lum_row)

    max_lum_adjustment = Gtk.Adjustment(
        value=settings.get("shading_max_luminance", 92.0), lower=0, upper=100, step_increment=1, page_increment=5,
    )
    max_lum_row = Adw.SpinRow(
        title="Luminancia máxima", subtitle="Solo con dirección 'light'.",
        adjustment=max_lum_adjustment, digits=0,
    )
    shading_expander.add_row(max_lum_row)

    def on_direction_changed(row, _pspec):
        settings["shading_direction"] = _SHADING_DIRECTIONS_GUI[row.get_selected()]
        pg.write_generation_settings(config, settings)

    def on_min_lum_changed(row, _pspec=None):
        settings["shading_min_luminance"] = row.get_value()
        pg.write_generation_settings(config, settings)

    def on_max_lum_changed(row, _pspec=None):
        settings["shading_max_luminance"] = row.get_value()
        pg.write_generation_settings(config, settings)

    direction_row.connect("notify::selected", on_direction_changed)
    min_lum_row.connect("notify::value", on_min_lum_changed)
    max_lum_row.connect("notify::value", on_max_lum_changed)

    def _sync_shading_visibility(*_args):
        is_shading = mode_row is not None and _GENERATION_MODES[mode_row.get_selected()] == "shading"
        shading_expander.set_visible(is_shading)

    if mode_row is not None:
        mode_row.connect("notify::selected", _sync_shading_visibility)
    _sync_shading_visibility()

    return group


def build_other_settings_group(config) -> Adw.PreferencesGroup:
    """Rarely-touched fine-tuning knobs that don't fit neatly into the main
    generation settings -- currently just --my-eyes' chroma-boost factor/cap
    (palette_generation.{my_eyes_factor, my_eyes_max_chroma}), same
    real-time-save group as the others. See
    palette_generator._boost_saturation for what these mean: --my-eyes
    multiplies a color's CIELAB chroma by `factor`, capped at `max_chroma`."""
    settings = pg.read_generation_settings(config)

    group = Adw.PreferencesGroup(
        title="Saturar colores (--my-eyes)",
        description="Ajuste fino del boost de croma CIELAB (C*) usado por 'Saturar colores'.",
    )
    _center_group_title(group)

    factor_adjustment = Gtk.Adjustment(
        value=settings.get("my_eyes_factor", 1.5), lower=1.0, upper=10.0,
        step_increment=0.1, page_increment=0.5,
    )
    factor_row = Adw.SpinRow(
        title="Multiplicador de Saturación",
        subtitle="Cuánto se multiplica el croma CIELAB (C*) de cada color elegido.",
        adjustment=factor_adjustment, digits=2,
    )

    def on_factor_changed(row, _pspec=None):
        settings["my_eyes_factor"] = row.get_value()
        pg.write_generation_settings(config, settings)

    factor_row.connect("notify::value", on_factor_changed)
    group.add(factor_row)

    max_chroma_adjustment = Gtk.Adjustment(
        value=settings.get("my_eyes_max_chroma", 132.0), lower=10.0, upper=200.0,
        step_increment=1, page_increment=10,
    )
    max_chroma_row = Adw.SpinRow(
        title="Saturación máxima",
        subtitle="Tope del croma CIELAB resultante -- evita distorsión de tono en colores extremos. Recomendado 132-150 para sRGB.",
        adjustment=max_chroma_adjustment, digits=0,
    )

    def on_max_chroma_changed(row, _pspec=None):
        settings["my_eyes_max_chroma"] = row.get_value()
        pg.write_generation_settings(config, settings)

    max_chroma_row.connect("notify::value", on_max_chroma_changed)
    group.add(max_chroma_row)

    return group


def show_other_settings(parent: Gtk.Widget, config):
    """"Otros…": rarely-touched fine-tuning knobs, reachable from the header
    menu. Currently just the --my-eyes chroma-boost group; a placeholder
    name/section for whatever else ends up not fitting elsewhere."""
    page = Adw.PreferencesPage()
    page.add(build_other_settings_group(config))
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Otros")
    prefs.add(page)
    prefs.present(parent)


def show_palette_generation_settings(parent: Gtk.Widget, config):
    """Reachable any time from the header menu.

    All three groups here share ONE settings dict (read once), so editing a
    setting in one group and then a different one in another group during
    the same dialog session doesn't have the second write silently revert
    the first -- see build_palette_generation_group's docstring."""
    settings = pg.read_generation_settings(config)
    page = Adw.PreferencesPage()
    generation_group, mode_row = build_palette_generation_group(config, settings=settings)
    page.add(generation_group)
    page.add(build_contrast_comparison_group(config, settings=settings))
    page.add(build_advanced_generation_group(config, mode_row=mode_row, settings=settings))
    prefs = Adw.PreferencesDialog()
    prefs.set_title("Generación de paleta")
    prefs.add(page)
    prefs.present(parent)
