#!/usr/bin/env python3
"""
palette_shift.py — Re-tweak an existing palette (its modifiers, or individual
colors) and hand back the new colors, WITHOUT the caller re-passing the image
or the whole flag set that first produced it. Powers `ucs automatic shift` and
`ucs palette edit|remove`, plus the GUI's Modificadores / edit / delete.

Model (see [[palette-shift-design]]): a palette's #ucs-meta stores an ordered
`base` list — every color, each tagged origin gen|custom, plus an optional
foreground/background role (user metadata, like label — never touched by a
post-mod, carried through verbatim) — plus the active `post` modifiers. The
CSV rows are the EFFECTIVE colors, always `apply_post_modifiers(base, post)`
(so the applied file is unchanged in shape).

Two kinds of modifier, handled very differently:

  * Post modifiers (my-eyes, ying-yang) are image-independent and apply to
    EVERY base color, gen and custom alike. Recomputed from base (never
    mutated), so they're instant and reversible, and hand-added colors survive
    (transformed, not dropped).

  * Selection modifiers (mode, scoring, weighted-contrast, shuffle, overfetch,
    colors) need the image's cluster pool, so they REGENERATE the gen colors
    from the stored image + params. A regeneration discards hand-added/edited
    colors (v1: warn, don't preserve). Only valid on a generated palette.

Per-color ops (add/edit/delete) keep the base coherent: an added or edited
color becomes origin custom (a literal, user-chosen color — still subject to
post-mods, immune only to regeneration); delete renumbers, and the caller
adjusts the mapping (see mapping_store.drop_and_shift_new_id).
"""

import os

from . import color_detector
from . import mapping_store
from . import palette_generator
from . import palette_store
from .color_detector import expand_path
from .config import to_home_relative


def generate_and_save_palette(config, image, n_colors, sample_size, mode, saturate,
                                out_path=None, scoring="default", custom_scoring_values=None,
                                weighted_contrast=True, shuffle=None, overfetch=0, ying_yang=False,
                                my_eyes_factor=palette_generator._MY_EYES_CHROMA_FACTOR,
                                my_eyes_max_chroma=palette_generator._MY_EYES_CHROMA_MAX,
                                shading_direction="dark", shading_min_luminance=8.0,
                                shading_max_luminance=92.0, keep_custom=None, consider_plane=None,
                                mapping_path=None):
    """Shared by the CLI (`palette generate` / `automatic --from-image`) and
    the GUI ("Generar paleta desde imagen…") -- the one place that turns an
    image + generation params into a saved palette CSV with full #ucs-meta.
    Returns (entries, saved_path, warnings) — entries is the id/hex/label
    list ready to hand to guiless.apply_palette directly (no need to re-read
    the file back).

    shuffle=None means "no shuffle override was requested" -- distinct from
    an explicit 0, since resolving it (and persisting it as the new
    "last_shuffle" anchor for a future --shuffle next) only happens when
    shuffling was actually requested.

    my_eyes_factor/my_eyes_max_chroma: only used when saturate=True -- see
    palette_generator._boost_saturation.

    shading_direction/shading_min_luminance/shading_max_luminance: only used
    when mode="shading" -- see palette_generator.generate_shading_series.

    keep_custom (on|off|toggle|None): if out_path ALREADY exists as a palette
    (e.g. a repeated `automatic --from-image` wallpaper-switch hook always
    targeting the same default generated.csv), this decides whether hand-
    added/edited colors already there survive this fresh generation instead
    of being silently overwritten -- same on/off/toggle/keep-current
    semantics and same positional-overlay mechanism as palette_shift's own
    regeneration (see _plan_regen_merge/_merge_regen_base). The "current"
    value resolved against is out_path's OWN stored keep_custom_on_regen if
    it already exists, else the project's remembered palette_generation
    default (config.json) -- so a brand new out_path still has a sensible
    fallback instead of always hardcoding True. Whatever gets resolved is
    persisted both into the new file's #ucs-meta and back into the project's
    palette_generation settings (same "last used becomes the new remembered
    default" pattern resolve_shuffle_index already uses for last_shuffle).

    consider_plane (on|off|toggle|None): whether generation aims for a fg/bg
    role demand at all -- same resolution shape as keep_custom (out_path's
    own stored consider_roles_on_regen if it exists, else the project's
    remembered default). The demand itself (see
    color_detector.compute_role_demand) is resolved separately, in priority
    order: (1) out_path's own current role tags if it already exists --
    [[color-roles-design]] Phase 5's tier 1; (2) detected colors tagged in
    color_roles.json AND present in `mapping_path`'s mapping (default:
    config.mapping_csv); (3) if there's no mapping at all, every tagged entry
    in color_roles.json, unfiltered."""
    if not out_path:
        out_path = config.generated_palette_csv
    if not out_path.endswith(".csv"):
        out_path += ".csv"
    if not os.path.isabs(out_path):
        out_path = os.path.join(config.palettes_created_dir, out_path)

    existing_entries, existing_meta = palette_store.read_palette(out_path)
    gen_settings = palette_generator.read_generation_settings(config)
    stored_keep_custom = (
        existing_meta.get("keep_custom_on_regen", True) if existing_entries
        else gen_settings.get("keep_custom_on_regen", True)
    )
    resolved_keep_custom = _resolve_bool(stored_keep_custom, keep_custom)
    stored_consider_plane = (
        existing_meta.get("consider_roles_on_regen", True) if existing_entries
        else gen_settings.get("consider_roles_on_regen", True)
    )
    resolved_consider_plane = _resolve_bool(stored_consider_plane, consider_plane)
    gen_settings["keep_custom_on_regen"] = resolved_keep_custom
    gen_settings["consider_roles_on_regen"] = resolved_consider_plane
    palette_generator.write_generation_settings(config, gen_settings)

    old_base = reconstruct_base(existing_entries, existing_meta) if existing_entries else []
    warnings = []
    n_gen_needed, custom_in_range, custom_trailing = _plan_regen_merge(
        old_base, n_colors, resolved_keep_custom, warnings, consider_plane=resolved_consider_plane,
    )

    if resolved_consider_plane:
        if existing_entries:
            # Tier 1: out_path already exists -- its own current tags are
            # the most accurate source, no need to touch color_roles.json.
            n_bg_needed, n_fg_needed = _role_demand_from_entries(existing_entries)
        else:
            # Tiers 2/3: a brand new out_path has nothing of its own to read.
            detected_colors = color_detector.read_detected_csv(config.detected_palette_csv)
            roles = color_detector.read_color_roles(config.color_roles_json)
            _old_p, _new_p, mapping_entries = mapping_store.read_mapping_csv(
                mapping_path or config.mapping_csv, project_dir=config.project_dir,
            )
            n_bg_needed, n_fg_needed = color_detector.compute_role_demand(
                detected_colors, roles, mapping_entries,
            )
        _check_role_demand_fits(n_colors, n_bg_needed, n_fg_needed)
    else:
        n_bg_needed, n_fg_needed = 0, 0

    weights = palette_generator.resolve_scoring_weights(
        scoring, custom_percentages=custom_scoring_values, config=config,
    )
    resolved_shuffle = (
        palette_generator.resolve_shuffle_index(shuffle, n_colors, overfetch=overfetch, config=config)
        if shuffle is not None else 0
    )
    _unused_effective, base_colors = palette_generator.generate_palette(
        image, n_colors=n_gen_needed, sample_size=sample_size, mode=mode,
        saturate=False, weights=weights, weighted_contrast=weighted_contrast,
        shuffle=resolved_shuffle, overfetch=overfetch, ying_yang=False,
        shading_direction=shading_direction, shading_min_l=shading_min_luminance,
        shading_max_l=shading_max_luminance,
        n_background_needed=n_bg_needed, n_foreground_needed=n_fg_needed, with_base=True,
    )
    fresh_gen = [{"hex": c["hex"], "label": c["label"], "origin": "gen", "role": c.get("role")}
                for c in base_colors]
    merged_base = _merge_regen_base(fresh_gen, n_colors, custom_in_range, custom_trailing, resolved_keep_custom)

    post = {"my_eyes": bool(saturate), "ying_yang": bool(ying_yang),
            "my_eyes_factor": my_eyes_factor, "my_eyes_max_chroma": my_eyes_max_chroma}
    # Rows are the EFFECTIVE (applied) colors; the pre-post-mod base + the
    # generation params ride along in #ucs-meta so `shift` can re-tweak & re-apply
    # this palette without re-passing the image or the whole flag set.
    entries = derive_effective(merged_base, post)
    meta = palette_store.default_meta()
    meta.update({
        "generated": True,
        "image": to_home_relative(image),
        "gen": {
            "colors": n_colors,
            "sample_size": sample_size,
            "mode": mode,
            "scoring": scoring,
            "custom_percentages": custom_scoring_values,
            "weighted_contrast": weighted_contrast,
            "shuffle": resolved_shuffle,
            "overfetch": overfetch,
            "shading_direction": shading_direction,
            "shading_min_luminance": shading_min_luminance,
            "shading_max_luminance": shading_max_luminance,
        },
        "post": post,
        "keep_custom_on_regen": resolved_keep_custom,
        "consider_roles_on_regen": resolved_consider_plane,
        "base": merged_base,
    })
    palette_store.write_palette_csv(out_path, entries, meta=meta)
    return entries, out_path, warnings


class ShiftError(Exception):
    """A clean, user-facing reason a shift can't proceed (created palette asked
    for a selection modifier, missing provenance, ...). main() prints it and
    exits 1, same as ImageLoadError -- never a traceback."""


class PaletteEditError(Exception):
    """User-facing reason a per-color edit can't proceed (duplicate color,
    target not found, empty palette). Printed cleanly, no traceback."""


def _resolve_bool(current, override) -> bool:
    """Apply an on|off|toggle|None override to a stored boolean. None keeps it."""
    if override is None:
        return bool(current)
    if override == "on":
        return True
    if override == "off":
        return False
    if override == "toggle":
        return not bool(current)
    raise ShiftError(f"valor inválido para un modificador booleano: {override!r} (esperado on|off|toggle)")


def _resolve_shading_direction(current, override) -> str:
    """Apply a dark|light|toggle|None override to the stored shading
    direction (defaults to "dark" if nothing was stored yet -- e.g. a
    palette generated before this option existed). None keeps it."""
    current = current or "dark"
    if override is None:
        return current
    if override in ("dark", "light"):
        return override
    if override == "toggle":
        return "light" if current == "dark" else "dark"
    raise ShiftError(f"valor inválido para la dirección de shading: {override!r} (esperado dark|light|toggle)")


# --------------------------------------------------------------------------- #
# base <-> effective
# --------------------------------------------------------------------------- #

def _norm_hex(h):
    return h.lstrip("#").lower()


def reconstruct_base(entries, meta):
    """The full ordered base (one entry per color, with origin and role),
    robust to the formats a palette file can be in:
      - new: meta['base'] covers every row (len matches) -> used directly.
      - older: meta['base'] held only gen colors, customs were separate literal
        rows -> gen bases come from meta['base'], custom bases from the row hex.
      - legacy: no meta['base'] at all -> every row is its own base.
    Always aligned 1:1 with `entries` by position. Role, like label, is pure
    user metadata a post-mod transform never touches -- carried through
    verbatim rather than defaulted (unlike origin, which always has a value)."""
    stored = list(meta.get("base") or [])
    if stored and len(stored) == len(entries):
        return [
            {"hex": _norm_hex(b["hex"]), "label": b.get("label", ""),
             "origin": b.get("origin") or (entries[i].get("origin") or "gen"),
             "role": b.get("role") or entries[i].get("role")}
            for i, b in enumerate(stored)
        ]
    base = []
    gi = 0
    for e in entries:
        if e.get("origin") == "custom":
            base.append({"hex": _norm_hex(e["hex"]), "label": e.get("label", ""),
                        "origin": "custom", "role": e.get("role")})
        elif gi < len(stored):
            base.append({"hex": _norm_hex(stored[gi]["hex"]),
                         "label": stored[gi].get("label", e.get("label", "")), "origin": "gen",
                         "role": stored[gi].get("role") or e.get("role")})
            gi += 1
        else:
            base.append({"hex": _norm_hex(e["hex"]), "label": e.get("label", ""),
                        "origin": "gen", "role": e.get("role")})
    return base


def derive_effective(base, post):
    """The effective (to-apply) rows for a base under the active post-mods:
    apply_post_modifiers to EVERY color (gen and custom), order preserved, ids
    renumbered 1..N, origin and role carried through."""
    modded = palette_generator.apply_post_modifiers(
        [{"hex": b["hex"], "label": b.get("label", "")} for b in base],
        my_eyes=bool(post.get("my_eyes")), ying_yang=bool(post.get("ying_yang")),
        my_eyes_factor=post.get("my_eyes_factor", palette_generator._MY_EYES_CHROMA_FACTOR),
        my_eyes_max_chroma=post.get("my_eyes_max_chroma", palette_generator._MY_EYES_CHROMA_MAX),
    )
    rows = []
    for i, (b, m) in enumerate(zip(base, modded)):
        row = {"id": i + 1, "hex": m["hex"], "label": b.get("label", "")}
        if b.get("origin") in ("gen", "custom"):
            row["origin"] = b["origin"]
        if b.get("role"):
            row["role"] = b["role"]
        rows.append(row)
    return rows


def _post_of(meta):
    post = dict({"my_eyes": False, "ying_yang": False,
                 "my_eyes_factor": palette_generator._MY_EYES_CHROMA_FACTOR,
                 "my_eyes_max_chroma": palette_generator._MY_EYES_CHROMA_MAX})
    post.update(meta.get("post") or {})
    return post


def _write_derived(path, base, meta):
    """Persist a mutated base: rows = derive_effective(base, post), meta.base
    updated to the full base. Returns the new rows."""
    post = _post_of(meta)
    rows = derive_effective(base, post)
    new_meta = dict(meta)
    new_meta["base"] = base
    new_meta["post"] = post
    palette_store.write_palette_csv(path, rows, meta=new_meta)
    return rows


# --------------------------------------------------------------------------- #
# per-color ops (add / edit / delete)
# --------------------------------------------------------------------------- #

def _load(path):
    entries, meta = palette_store.read_palette(path)
    return entries, meta, reconstruct_base(entries, meta)


def _has_dup(base, new_hex, exclude_index=None):
    h = _norm_hex(new_hex)
    return any(i != exclude_index and b["hex"] == h for i, b in enumerate(base))


def _find_index(base, entries, target):
    """Locate a color by id (a digit) or by hex (effective row hex or base
    hex). Returns its position, or None."""
    t = _norm_hex(str(target).strip())
    if t.isdigit():
        tid = int(t)
        for i, e in enumerate(entries):
            if e["id"] == tid:
                return i
        return None
    for i, e in enumerate(entries):
        if _norm_hex(e["hex"]) == t:
            return i
    for i, b in enumerate(base):
        if b["hex"] == t:
            return i
    return None


# Sentinel for edit_color's role param: "not passed, leave whatever role this
# slot already had untouched" -- distinct from None, which means "explicitly
# clear it back to unmarked" (role=None is itself a meaningful value here).
_UNSET = object()


def add_color(path, hex_value, label="", role=None):
    """Append a user-chosen color (origin custom) to a palette, rejecting a
    duplicate. Returns the new effective row entry."""
    entries, meta, base = _load(path)
    if _has_dup(base, hex_value):
        raise PaletteEditError(f"El color #{_norm_hex(hex_value)} ya existe en la paleta.")
    base.append({"hex": _norm_hex(hex_value), "label": label, "origin": "custom", "role": role})
    rows = _write_derived(path, base, meta)
    return rows[-1]


def edit_color(path, target, new_hex, role=_UNSET):
    """Change a color (by id or hex) to new_hex, marking it custom. Rejects a
    duplicate. `role` left at its default keeps the slot's existing role
    (None explicitly clears it back to unmarked). Returns the 1-based id of
    the edited slot (unchanged: an edit keeps its position, so any mapping to
    it stays valid)."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    if _has_dup(base, new_hex, exclude_index=idx):
        raise PaletteEditError(f"El color #{_norm_hex(new_hex)} ya existe en la paleta.")
    new_role = base[idx].get("role") if role is _UNSET else role
    base[idx] = {"hex": _norm_hex(new_hex), "label": base[idx].get("label", ""),
                "origin": "custom", "role": new_role}
    _write_derived(path, base, meta)
    return idx + 1


def set_role(path, target, role):
    """Set (or clear, role=None) a color's role WITHOUT touching its
    hex/label/origin -- unlike edit_color, this never marks a gen color
    custom (used by the GUI's role toggle button, which only ever changes
    the role, never the color value itself). Returns the 1-based id of the
    affected slot."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    base[idx] = dict(base[idx], role=role)
    _write_derived(path, base, meta)
    return idx + 1


def delete_color(path, target):
    """Remove a color (by id or hex). Returns the 1-based id it had, so the
    caller can adjust a mapping (unassign that new_id, shift higher ones down --
    see mapping_store.drop_and_shift_new_id). Renumbers the palette to stay
    contiguous, which guiless's positional matching relies on."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    del base[idx]
    _write_derived(path, base, meta)
    return idx + 1


# --------------------------------------------------------------------------- #
# shift (modifiers)
# --------------------------------------------------------------------------- #

_SELECTION_KEYS = ("mode", "scoring", "custom_scoring_values", "weighted_contrast",
                   "shuffle", "overfetch", "colors", "shading_direction",
                   "shading_min_luminance", "shading_max_luminance")


def _plan_regen_merge(old_base, n_colors, keep_custom, warnings, consider_plane=False):
    """Shared by _regenerate (palette shift) and generate_and_save_palette
    (palette generate/automatic --from-image, when out_path already has
    content): given an existing base (possibly empty -- a brand new out_path
    has nothing to preserve) and a freshly requested n_colors, decide the
    regen plan and append the right discard/role-loss warnings (a list,
    mutated in place). Raises ShiftError if keep_custom is on and there's no
    room. Returns (n_gen_needed, custom_in_range, custom_trailing) -- the
    last two keyed by ORIGINAL position, for _merge_regen_base.

    consider_plane: when True, a lost role tag isn't actually a loss -- the
    DEMAND it represented gets read from old_base and re-satisfied by a new
    color elsewhere in the regenerated palette (see _regenerate/
    generate_and_save_palette), so the usual "you lost this role" warning
    would be a false alarm and is skipped."""
    custom_by_pos = {i: b for i, b in enumerate(old_base) if b.get("origin") == "custom"}
    n_custom = len(custom_by_pos)
    # Only customs that landed WITHIN the requested n_colors (edited in
    # place, same slot they always had) compete for the gen budget -- ones
    # added past the original block never occupied a numbered gen slot.
    custom_in_range = {i: b for i, b in custom_by_pos.items() if i < n_colors}
    custom_trailing = {i: b for i, b in custom_by_pos.items() if i >= n_colors}
    n_custom_in_range = len(custom_in_range)

    if keep_custom and n_custom_in_range >= n_colors:
        raise ShiftError(
            f"Esta paleta tiene {n_custom_in_range} color(es) editado(s) a mano dentro de los "
            f"primeros {n_colors} que pediste -- no queda espacio para generar ninguno nuevo. "
            f"Pedí al menos {n_custom_in_range + 1} colores, desactivá --keep-custom para esta "
            "regeneración, o creá una paleta nueva desde cero."
        )

    if not keep_custom and n_custom:
        warnings.append(
            f"Se descartan {n_custom} color(es) agregados/editados a mano: una regeneración "
            "reemplaza los colores. Los modificadores simples (--my-eyes/--ying-yang) no los borran."
        )
    # A role survives regeneration LITERALLY only when its color does
    # (keep_custom AND it's one of the preserved custom slots) -- everything
    # else that had a role is about to get a brand new hex. Only actually a
    # LOSS when consider_plane is off, though: when it's on, the demand those
    # tags represented gets re-satisfied by a new color elsewhere (see
    # _regenerate/generate_and_save_palette), so warning here would be a
    # false alarm.
    if not consider_plane:
        n_roled_lost = sum(
            1 for i, b in enumerate(old_base)
            if b.get("role") and not (keep_custom and i in custom_by_pos)
        )
        if n_roled_lost:
            warnings.append(
                f"Se pierde el rol foreground/background asignado a {n_roled_lost} color(es) de esta "
                "paleta: una regeneración reemplaza los colores, así que quedan sin marcar de nuevo."
            )

    n_gen_needed = (n_colors - n_custom_in_range) if keep_custom else n_colors
    return n_gen_needed, custom_in_range, custom_trailing


def _merge_regen_base(fresh_gen, n_colors, custom_in_range, custom_trailing, keep_custom):
    """Overlay fresh_gen (n_gen_needed colors, freshly generated) with the
    preserved custom slots BY POSITION -- an edited-in-place custom must stay
    at the same id it always had, or an existing mapping entry pointing at it
    would silently start resolving to a different, freshly-regenerated color."""
    if not keep_custom:
        return fresh_gen
    merged = []
    gi = 0
    for i in range(n_colors):
        if i in custom_in_range:
            merged.append(custom_in_range[i])
        else:
            merged.append(fresh_gen[gi])
            gi += 1
    merged.extend(b for i, b in sorted(custom_trailing.items()))
    return merged


def _role_demand_from_entries(entries: list) -> tuple:
    """Tier 1 of the demand resolution (see [[color-roles-design]] Phase 5):
    an EXISTING palette's own already-tagged colors are the most accurate
    source -- no need to touch color_roles.json/the mapping at all when
    regenerating something that already carries its own role tags."""
    n_background = sum(1 for e in entries if e.get("role") == "background")
    n_foreground = sum(1 for e in entries if e.get("role") == "foreground")
    return n_background, n_foreground


def _check_role_demand_fits(n_colors: int, n_background_needed: int, n_foreground_needed: int) -> None:
    """A color can only carry ONE role, so satisfying the demand needs at
    least that many DISTINCT chosen colors -- same shape of hard constraint
    as keep_custom's budget check, same treatment: refuse outright rather
    than silently under-cover or guess which role wins."""
    total_needed = n_background_needed + n_foreground_needed
    if total_needed > n_colors:
        raise ShiftError(
            f"Considerar roles fg/bg está activado y hace falta al menos {total_needed} color(es) "
            f"({n_background_needed} background + {n_foreground_needed} foreground), pero pediste "
            f"{n_colors} en total. Pedí al menos {total_needed} colores (--colors), desactivá "
            "--consider-plane para esta generación, o creá una paleta nueva desde cero."
        )


def shift_palette(palette_path, config, *, my_eyes=None, ying_yang=None,
                  my_eyes_factor=None, my_eyes_max_chroma=None,
                  mode=None, scoring=None, custom_scoring_values=None,
                  weighted_contrast=None, shuffle=None, overfetch=None, colors=None,
                  shading_direction=None, shading_min_luminance=None, shading_max_luminance=None,
                  keep_custom=None, consider_plane=None, write=True) -> dict:
    """Compute (and, unless write=False, persist) the shifted palette.

    Boolean modifiers (my_eyes, ying_yang) take on|off|toggle|None.
    shading_direction takes dark|light|toggle|None (like a boolean modifier,
    but a 2-way choice instead -- see _resolve_shading_direction).
    my_eyes_factor/my_eyes_max_chroma are POST modifiers too (no
    regeneration needed, just a different multiplier/cap applied to the same
    stored base) -- a concrete value or None ("keep stored"). Everything else
    (selection modifiers, including the luminance bounds) also takes a
    concrete value or None ("keep stored"), but DOES trigger a regenerate.
    keep_custom/consider_plane (on|off|toggle|None) are stored per-palette
    REGEN POLICIES, not post-modifiers -- neither triggers a regenerate by
    itself, but they control what a regenerate does with hand-added/edited
    colors and fg/bg role demand respectively (see _regenerate).

    Returns {"entries", "meta", "regenerated": bool, "warnings": [str]}.
    Raises ShiftError for the clean user-facing failures."""
    entries, meta = palette_store.read_palette(palette_path)
    if not entries:
        raise ShiftError(f"Paleta vacía o no encontrada: {palette_path}")

    post = _post_of(meta)
    new_post = {
        "my_eyes": _resolve_bool(post.get("my_eyes"), my_eyes),
        "ying_yang": _resolve_bool(post.get("ying_yang"), ying_yang),
        "my_eyes_factor": my_eyes_factor if my_eyes_factor is not None else post.get("my_eyes_factor"),
        "my_eyes_max_chroma": (my_eyes_max_chroma if my_eyes_max_chroma is not None
                               else post.get("my_eyes_max_chroma")),
    }
    resolved_keep_custom = _resolve_bool(meta.get("keep_custom_on_regen", True), keep_custom)
    resolved_consider_plane = _resolve_bool(meta.get("consider_roles_on_regen", True), consider_plane)
    selection = {"mode": mode, "scoring": scoring, "custom_scoring_values": custom_scoring_values,
                 "weighted_contrast": weighted_contrast, "shuffle": shuffle,
                 "overfetch": overfetch, "colors": colors,
                 "shading_direction": shading_direction,
                 "shading_min_luminance": shading_min_luminance,
                 "shading_max_luminance": shading_max_luminance}
    wants_regen = any(selection[k] is not None for k in _SELECTION_KEYS)
    warnings = []

    if wants_regen:
        new_entries, new_meta = _regenerate(meta, entries, new_post, selection, config, warnings,
                                            resolved_keep_custom, resolved_consider_plane)
    else:
        base = reconstruct_base(entries, meta)
        new_entries = derive_effective(base, new_post)
        new_meta = dict(meta)
        new_meta["post"] = new_post
        new_meta["keep_custom_on_regen"] = resolved_keep_custom
        new_meta["consider_roles_on_regen"] = resolved_consider_plane
        new_meta["base"] = base

    if write:
        palette_store.write_palette_csv(palette_path, new_entries, meta=new_meta)
    return {"entries": new_entries, "meta": new_meta, "regenerated": wants_regen, "warnings": warnings}


def _regenerate(meta, entries, new_post, selection, config, warnings, keep_custom, consider_plane):
    if not meta.get("generated"):
        raise ShiftError(
            "Esta paleta es creada (no tiene imagen): no admite modificadores de selección "
            "(--mode/--scoring/--shuffle/--overfetch/--colors). Solo --my-eyes/--ying-yang."
        )
    image = meta.get("image")
    if not image:
        raise ShiftError(
            "Esta paleta no tiene información de generación guardada. Regenerá una vez con "
            "'automatic --from-image <img>' para grabarla, y después usá shift."
        )
    gen = meta.get("gen") or {}
    n_colors = selection["colors"] if selection["colors"] is not None else gen.get("colors", 6)
    resolved = {
        "sample_size": gen.get("sample_size", 40000),
        "mode": selection["mode"] if selection["mode"] is not None else gen.get("mode", "contrast"),
        "scoring": selection["scoring"] if selection["scoring"] is not None else gen.get("scoring", "default"),
        "custom_percentages": (selection["custom_scoring_values"] if selection["custom_scoring_values"] is not None
                               else gen.get("custom_percentages")),
        "weighted_contrast": (selection["weighted_contrast"] if selection["weighted_contrast"] is not None
                              else gen.get("weighted_contrast", True)),
        "overfetch": selection["overfetch"] if selection["overfetch"] is not None else gen.get("overfetch", 0),
        "shading_min_luminance": (selection["shading_min_luminance"]
                                  if selection["shading_min_luminance"] is not None
                                  else gen.get("shading_min_luminance", 8.0)),
        "shading_max_luminance": (selection["shading_max_luminance"]
                                  if selection["shading_max_luminance"] is not None
                                  else gen.get("shading_max_luminance", 92.0)),
    }
    resolved["shading_direction"] = _resolve_shading_direction(
        gen.get("shading_direction"), selection["shading_direction"],
    )
    weights = palette_generator.resolve_scoring_weights(
        resolved["scoring"], custom_percentages=resolved["custom_percentages"], config=config,
    )
    # Only resolve/persist a new shuffle anchor when the user actually passed
    # --shuffle; otherwise reuse the stored resolved index verbatim (no "next"
    # bookkeeping side effects on an unrelated shift).
    if selection["shuffle"] is not None:
        resolved_shuffle = palette_generator.resolve_shuffle_index(
            selection["shuffle"], n_colors, overfetch=resolved["overfetch"], config=config,
        )
    else:
        resolved_shuffle = int(gen.get("shuffle", 0))

    old_base = reconstruct_base(entries, meta)
    n_gen_needed, custom_in_range, custom_trailing = _plan_regen_merge(
        old_base, n_colors, keep_custom, warnings, consider_plane=consider_plane,
    )

    # Tier 1 of the demand resolution (see [[color-roles-design]] Phase 5):
    # a shift always regenerates an EXISTING palette, so its own current
    # role tags are always the most accurate source -- no need to fall back
    # to color_roles.json/the mapping here at all.
    n_bg_needed, n_fg_needed = _role_demand_from_entries(entries) if consider_plane else (0, 0)
    if consider_plane:
        _check_role_demand_fits(n_colors, n_bg_needed, n_fg_needed)

    _unused_effective, gen_base = palette_generator.generate_palette(
        expand_path(image), n_colors=n_gen_needed, sample_size=resolved["sample_size"],
        mode=resolved["mode"], saturate=False, weights=weights,
        weighted_contrast=resolved["weighted_contrast"], shuffle=resolved_shuffle,
        overfetch=resolved["overfetch"], ying_yang=False,
        shading_direction=resolved["shading_direction"], shading_min_l=resolved["shading_min_luminance"],
        shading_max_l=resolved["shading_max_luminance"],
        n_background_needed=n_bg_needed, n_foreground_needed=n_fg_needed, with_base=True,
    )
    fresh_gen = [{"hex": c["hex"], "label": c["label"], "origin": "gen", "role": c.get("role")} for c in gen_base]
    merged_base = _merge_regen_base(fresh_gen, n_colors, custom_in_range, custom_trailing, keep_custom)

    new_entries = derive_effective(merged_base, new_post)
    new_meta = dict(meta)
    new_meta["generated"] = True
    new_meta["gen"] = {
        "colors": n_colors, "sample_size": resolved["sample_size"], "mode": resolved["mode"],
        "scoring": resolved["scoring"], "custom_percentages": resolved["custom_percentages"],
        "weighted_contrast": resolved["weighted_contrast"], "shuffle": resolved_shuffle,
        "overfetch": resolved["overfetch"], "shading_direction": resolved["shading_direction"],
        "shading_min_luminance": resolved["shading_min_luminance"],
        "shading_max_luminance": resolved["shading_max_luminance"],
    }
    new_meta["post"] = new_post
    new_meta["keep_custom_on_regen"] = keep_custom
    new_meta["consider_roles_on_regen"] = consider_plane
    new_meta["base"] = merged_base
    return new_entries, new_meta
